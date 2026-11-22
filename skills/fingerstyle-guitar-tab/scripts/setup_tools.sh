#!/usr/bin/env bash
set -euo pipefail

# Rootless toolchain bootstrap for fingerstyle-guitar-tab.
# Usage: bash setup_tools.sh [TARGET_DIR] [--force]

TARGET_DIR=""
FORCE=0

for arg in "$@"; do
  case "$arg" in
    --force)
      FORCE=1
      ;;
    -h|--help)
      echo "Usage: bash setup_tools.sh [TARGET_DIR] [--force]"
      exit 0
      ;;
    *)
      if [[ -z "$TARGET_DIR" ]]; then
        TARGET_DIR="$arg"
      else
        echo "Unexpected argument: $arg" >&2
        exit 1
      fi
      ;;
  esac
done

TARGET_DIR="${TARGET_DIR:-$PWD}"
mkdir -p "$TARGET_DIR"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

TOOLS_DIR="$TARGET_DIR/.tools"
DEBS_DIR="$TOOLS_DIR/debs"
TOOLS_ROOT="$TOOLS_DIR/root"

find_sf2() {
  local root_dir="${1:-}"
  local candidates=()
  if [[ -n "$root_dir" ]]; then
    candidates+=(
      "$root_dir/usr/share/sounds/sf2/FluidR3_GM.sf2"
      "$root_dir/usr/share/soundfonts/FluidR3_GM.sf2"
    )
  fi
  candidates+=(
    "/usr/share/sounds/sf2/FluidR3_GM.sf2"
    "/usr/share/soundfonts/FluidR3_GM.sf2"
    "/opt/homebrew/share/soundfonts/FluidR3_GM.sf2"
    "/usr/local/share/soundfonts/FluidR3_GM.sf2"
  )
  for p in "${candidates[@]}"; do
    if [[ -f "$p" ]]; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

check_system_reqs() {
  local missing=()
  for cmd in python3 ffmpeg ffprobe gs pdftoppm; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    echo "[ERROR] Missing required system tools: ${missing[*]}" >&2
    echo "Install them with your package manager (e.g., sudo apt install python3 ffmpeg ghostscript poppler-utils)." >&2
    return 1
  fi
  return 0
}

OS_NAME="$(uname -s)"
if [[ "$OS_NAME" == "Darwin" ]]; then
  echo "Detected macOS (Darwin)."
  echo "Recommended Homebrew installation:"
  echo "  brew install lilypond fluid-synth ffmpeg ghostscript poppler"
  echo "SoundFont (FluidR3_GM.sf2): place FluidR3_GM.sf2 in /opt/homebrew/share/soundfonts/ or set TAB_SF2_PATH=/path/to/FluidR3_GM.sf2"

  missing_mac=0
  for cmd in lilypond fluidsynth python3 ffmpeg ffprobe gs pdftoppm; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "[MISSING] $cmd" >&2
      missing_mac=1
    fi
  done
  if ! find_sf2 "" >/dev/null 2>&1; then
    echo "[MISSING] FluidR3_GM.sf2" >&2
    missing_mac=1
  fi
  if (( missing_mac == 0 )); then
    echo "[OK] All required macOS tools and FluidR3_GM.sf2 are available."
    exit 0
  fi
  exit 1
fi

if [[ "$OS_NAME" != "Linux" ]]; then
  echo "[ERROR] Unsupported OS: $OS_NAME. Please install LilyPond, FluidSynth, FluidR3_GM.sf2, FFmpeg, Ghostscript, and poppler manually." >&2
  exit 1
fi

check_system_reqs

USE_SYSTEM=0
if (( FORCE == 0 )); then
  if [[ -x "$TOOLS_ROOT/usr/bin/lilypond" && -x "$TOOLS_ROOT/usr/bin/fluidsynth" ]] && find_sf2 "$TOOLS_ROOT" >/dev/null 2>&1; then
    echo "[OK] Rootless toolchain already present at $TOOLS_ROOT (pass --force to re-download)."
  elif command -v lilypond >/dev/null 2>&1 && command -v fluidsynth >/dev/null 2>&1 && find_sf2 "" >/dev/null 2>&1; then
    echo "[OK] System lilypond, fluidsynth, and FluidR3_GM.sf2 already available (pass --force to populate local .tools/root)."
    USE_SYSTEM=1
  else
    FORCE=1
  fi
fi

if (( FORCE == 1 )); then
  if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "[ERROR] dpkg-deb not found. Rootless .deb extraction requires a Debian/Ubuntu host." >&2
    exit 1
  fi

  DL_TOOL=""
  if command -v apt-get >/dev/null 2>&1; then
    DL_TOOL="apt-get"
  elif command -v apt >/dev/null 2>&1; then
    DL_TOOL="apt"
  else
    echo "[ERROR] Neither apt-get nor apt found. Cannot download .deb packages without sudo." >&2
    exit 1
  fi

  mkdir -p "$DEBS_DIR" "$TOOLS_ROOT"

  REQUIRED_PKGS=(
    lilypond
    lilypond-data
    lilypond-fonts
    guile-3.0-libs
    libgc1
    fonts-urw-base35
    fluidsynth
    libfluidsynth3
    fluid-soundfont-gm
  )
  OPTIONAL_PKGS=(
    libinstpatch-1.0-2
  )

  echo "Downloading required packages into $DEBS_DIR using $DL_TOOL..."
  (
    cd "$DEBS_DIR"
    "$DL_TOOL" download "${REQUIRED_PKGS[@]}"
    for opt_pkg in "${OPTIONAL_PKGS[@]}"; do
      "$DL_TOOL" download "$opt_pkg" >/dev/null 2>&1 || true
    done
  )

  echo "Extracting packages into $TOOLS_ROOT..."
  shopt -s nullglob
  debs=("$DEBS_DIR"/*.deb)
  if (( ${#debs[@]} == 0 )); then
    echo "[ERROR] No .deb packages found in $DEBS_DIR after download." >&2
    exit 1
  fi
  for deb in "${debs[@]}"; do
    dpkg-deb -x "$deb" "$TOOLS_ROOT"
  done
  shopt -u nullglob
fi

if (( USE_SYSTEM == 1 )); then
  LILYPOND_BIN="$(command -v lilypond)"
  FLUIDSYNTH_BIN="$(command -v fluidsynth)"
  SF2_FOUND="$(find_sf2 "")"
  echo "--- Smoke Test (System Toolchain) ---"
  "$LILYPOND_BIN" --version | head -n 1
  "$FLUIDSYNTH_BIN" --version | head -n 2
  echo "SoundFont: $SF2_FOUND"
  exit 0
fi

LILYPOND_BIN="$TOOLS_ROOT/usr/bin/lilypond"
FLUIDSYNTH_BIN="$TOOLS_ROOT/usr/bin/fluidsynth"
SF2_FOUND="$(find_sf2 "$TOOLS_ROOT" || true)"

if [[ ! -x "$LILYPOND_BIN" || ! -x "$FLUIDSYNTH_BIN" || -z "$SF2_FOUND" ]]; then
  echo "[ERROR] Local toolchain incomplete inside $TOOLS_ROOT." >&2
  exit 1
fi

LIB_PATHS=()
CCACHE_PATHS=()
if [[ -d "$TOOLS_ROOT/usr/lib" ]]; then
  for arch_dir in "$TOOLS_ROOT"/usr/lib/*-linux-gnu; do
    [[ -d "$arch_dir" ]] || continue
    LIB_PATHS+=("$arch_dir")
    if [[ -d "$arch_dir/guile" ]]; then
      latest_guile="$(find "$arch_dir/guile" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)"
      if [[ -n "$latest_guile" ]]; then
        LIB_PATHS+=("$latest_guile")
        [[ -d "$latest_guile/ccache" ]] && CCACHE_PATHS+=("$latest_guile/ccache")
      fi
    fi
  done
fi

LD_STR="$(IFS=:; echo "${LIB_PATHS[*]}")"
if [[ -n "${LD_LIBRARY_PATH:-}" ]]; then
  LD_STR="${LD_STR}:${LD_LIBRARY_PATH}"
fi

LP_DATADIR="$(find "$TOOLS_ROOT/usr/share/lilypond" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -n 1 || true)"
GUILE_LOAD="$(find "$TOOLS_ROOT/usr/share/guile" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -V | tail -n 1 || true)"
GUILE_CCACHE="$(IFS=:; echo "${CCACHE_PATHS[*]}")"

echo "--- Smoke Test (Rootless Toolchain) ---"
LD_LIBRARY_PATH="$LD_STR" \
  LILYPOND_DATADIR="$LP_DATADIR" \
  GUILE_LOAD_PATH="$GUILE_LOAD" \
  GUILE_LOAD_COMPILED_PATH="$GUILE_CCACHE" \
  "$LILYPOND_BIN" --version | head -n 1

LD_LIBRARY_PATH="$LD_STR" \
  "$FLUIDSYNTH_BIN" --version | head -n 2

echo "SoundFont: $SF2_FOUND"
echo ""
echo "[READY] Export this when building projects outside $TARGET_DIR:"
echo "  export TAB_TOOLS_ROOT=\"$TOOLS_ROOT\""
