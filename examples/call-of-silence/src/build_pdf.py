"""Generate GNU LilyPond source and compile Call_of_Silence_指弹吉他六线谱.pdf."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from score_data import (
    ARRANGER_INFO,
    CHORD_LEGEND,
    COMPOSER,
    OPEN_TUNING_NO_CAPO,
    SUBTITLE,
    TECHNIQUE_LEGEND_1,
    TECHNIQUE_LEGEND_2,
    TEMPO_BPM,
    TITLE,
    Measure,
    build_measures,
    validate_score,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_DIR / "build"
OUTPUT_PDF = PROJECT_DIR / "Call_of_Silence_指弹吉他六线谱.pdf"


def _ver_key(p: Path) -> Tuple[object, ...]:
    return tuple(int(tok) if tok.isdigit() else tok for tok in p.name.replace("-", ".").split("."))


def resolve_lilypond() -> Tuple[Path, Dict[str, str]]:
    """Resolve LilyPond binary and env from TAB_TOOLS_ROOT, .tools/root, repo root, or PATH."""
    env = os.environ.copy()
    candidates: List[Path] = []
    if env.get("TAB_TOOLS_ROOT"):
        candidates.append(Path(env["TAB_TOOLS_ROOT"]).expanduser())
    candidates.extend([
        PROJECT_DIR / ".tools" / "root",
        PROJECT_DIR.parent.parent / ".tools" / "root",
    ])

    for tools_root in candidates:
        lily_bin = tools_root / "usr" / "bin" / "lilypond"
        if not lily_bin.exists():
            continue

        lib_dirs: List[str] = []
        ccache_dirs: List[str] = []
        usr_lib = tools_root / "usr" / "lib"
        if usr_lib.is_dir():
            for arch_dir in sorted(usr_lib.glob("*-linux-gnu")):
                if not arch_dir.is_dir():
                    continue
                lib_dirs.append(str(arch_dir))
                guile_dir = arch_dir / "guile"
                if guile_dir.is_dir():
                    vers = sorted((d for d in guile_dir.iterdir() if d.is_dir()), key=_ver_key)
                    if vers:
                        latest_guile = vers[-1]
                        lib_dirs.append(str(latest_guile))
                        ccache = latest_guile / "ccache"
                        if ccache.is_dir():
                            ccache_dirs.append(str(ccache))
        if env.get("LD_LIBRARY_PATH"):
            lib_dirs.append(env["LD_LIBRARY_PATH"])
        if lib_dirs:
            env["LD_LIBRARY_PATH"] = ":".join(lib_dirs)

        lp_share = tools_root / "usr" / "share" / "lilypond"
        if lp_share.is_dir():
            lp_vers = sorted((d for d in lp_share.iterdir() if d.is_dir()), key=_ver_key)
            if lp_vers:
                env["LILYPOND_DATADIR"] = str(lp_vers[-1])

        guile_share = tools_root / "usr" / "share" / "guile"
        if guile_share.is_dir():
            guile_vers = sorted((d for d in guile_share.iterdir() if d.is_dir()), key=_ver_key)
            if guile_vers:
                env["GUILE_LOAD_PATH"] = str(guile_vers[-1])

        if ccache_dirs:
            env["GUILE_LOAD_COMPILED_PATH"] = ":".join(ccache_dirs)
        return lily_bin, env

    sys_lily = shutil.which("lilypond")
    if sys_lily:
        return Path(sys_lily), env

    raise FileNotFoundError(
        "LilyPond not found in TAB_TOOLS_ROOT, .tools/root, repo root .tools/root, or system PATH. "
        "Run `bash skills/fingerstyle-guitar-tab/scripts/setup_tools.sh` to install rootless tools."
    )

LY_PITCH_NAMES = ["c", "cis", "d", "dis", "e", "f", "fis", "g", "gis", "a", "ais", "b"]

DUR_MAP: Dict[int, str] = {
    1: "16",
    2: "8",
    3: "8.",
    4: "4",
    6: "4.",
    8: "2",
    12: "2.",
    16: "1",
}

CHORD_TO_LY: Dict[str, str] = {
    "Cmaj7": "c{dur}:maj7",
    "D": "d{dur}",
    "D/F#": "d{dur}/fis",
    "Em": "e{dur}:m",
    "Em9": "e{dur}:m9",
    "G": "g{dur}",
    "C": "c{dur}",
    "Am7": "a{dur}:m7",
    "Bm7": "b{dur}:m7",
}

PHRASE_CUES: Dict[Tuple[int, int], str] = {
    (9, 12): "Don't you think...",
    (11, 12): "I've been burning...",
    (13, 12): "I've got to face...",
    (15, 14): "I won't run...",
    (27, 12): "You will know...",
    (29, 12): "Must be ragged...",
    (31, 12): "Even if my body's...",
    (33, 12): "I don't want to go...",
    (35, 10): "So cry no more...",
    (37, 10): "Go ahead, be proud...",
    (39, 10): "You are the one...",
    (41, 14): "You guide us far...",
}

HARMONIC_LABELS: Set[Tuple[int, int]] = {(9, 2), (17, 10), (43, 8)}
SLAP_LABEL_LOC: Tuple[int, int] = (28, 4)


def midi_to_ly_pitch(midi: int) -> str:
    pc = midi % 12
    octave = midi // 12 - 1  # C4 = 60 -> octave 4
    name = LY_PITCH_NAMES[pc]
    if octave >= 4:
        marks = "'" * (octave - 3)
    elif octave <= 2:
        marks = "," * (3 - octave)
    else:
        marks = ""
    return f"{name}{marks}"


def decompose_duration(sixteenths: int) -> List[int]:
    """Break an integer number of 16th-note units into valid LilyPond durations."""
    out: List[int] = []
    rem = sixteenths
    for unit in (16, 12, 8, 6, 4, 3, 2, 1):
        while rem >= unit:
            out.append(unit)
            rem -= unit
    return out


def format_chords(measures: List[Measure]) -> str:
    """Emit synchronized ChordNames stream including grace spacers matching TabStaff."""
    lines: List[str] = []
    for meas in measures:
        grace_slots = {
            e.slot for e in meas.events if any(nt.grace_from is not None for nt in e.notes)
        }
        if meas.is_pickup:
            lines.append("  \\partial 4 s4 |")
            continue

        # Map each 16th-note span boundaries (chords + any internal grace_slots)
        chord_map = dict(meas.chords)
        boundaries = sorted({0, 16} | set(chord_map.keys()) | grace_slots)

        tokens: List[str] = []
        for idx in range(len(boundaries) - 1):
            start = boundaries[idx]
            end = boundaries[idx + 1]
            span = end - start

            if start in grace_slots:
                tokens.append("\\grace { s16 }")

            label = chord_map.get(start)
            parts = decompose_duration(span)
            for p_idx, u in enumerate(parts):
                dstr = DUR_MAP[u]
                if p_idx == 0 and label is not None:
                    tokens.append(CHORD_TO_LY[label].format(dur=dstr))
                else:
                    tokens.append(f"s{dstr}")

        lines.append(f"  {' '.join(tokens)} | % M{meas.bar:02d}")
    return "\n".join(lines)


def format_tab_music(measures: List[Measure]) -> str:
    lines: List[str] = []
    for meas in measures:
        bar_tokens: List[str] = []

        if meas.section:
            bar_tokens.append(
                f'\\mark \\markup {{ \\box \\bold \\fontsize #-0.5 "{meas.section}" }}'
            )

        if meas.is_pickup:
            bar_tokens.append("\\partial 4")
            bar_tokens.append("\\once \\omit Score.BarNumber")

        n_events = len(meas.events)
        for ev_idx, ev in enumerate(meas.events):
            dstr = DUR_MAP[ev.dur]
            prefix = ""
            suffix_marks: List[str] = []

            # Grace-note slide-in (S)
            grace_notes = [nt for nt in ev.notes if nt.grace_from is not None]
            if grace_notes:
                gnt = grace_notes[0]
                grace_written = OPEN_TUNING_NO_CAPO[gnt.string] + gnt.grace_from
                prefix = f"\\slashedGrace {{ <{midi_to_ly_pitch(grace_written)}\\{gnt.string}>16\\glissando }} "
                suffix_marks.append('^\\markup { \\bold \\fontsize #-2 "S" }')

            if ev.rest:
                tok = f"r{dstr}"
            else:
                # Sort string ascending (1 first) so \glissando always maps string-1 melody -> string-1!
                sorted_notes = sorted(ev.notes, key=lambda x: x.string)
                parts: List[str] = []
                for nt in sorted_notes:
                    pitch_str = midi_to_ly_pitch(nt.written_midi)
                    if nt.role == "slap":
                        parts.append(
                            f'\\tweak TabNoteHead.stencil #(tab-note-head::print-custom-fret-label "X") {pitch_str}\\{nt.string}'
                        )
                    elif nt.harmonic:
                        parts.append(
                            f'\\tweak TabNoteHead.stencil #(tab-note-head::print-custom-fret-label "<12>") {pitch_str}\\{nt.string}'
                        )
                    else:
                        parts.append(f"{pitch_str}\\{nt.string}")
                tok = f"<{' '.join(parts)}>{dstr}"

            # Close previous Hammer-on / Pull-off slur BEFORE starting a new one (handles 0h3p0!)
            if ev_idx > 0 and meas.events[ev_idx - 1].link_tech in ("H", "P"):
                tok += ")"

            if ev.link_tech in ("H", "P"):
                tok += "("
                suffix_marks.append(f'^\\markup {{ \\bold \\fontsize #-2 "{ev.link_tech}" }}')
            elif ev.link_tech == "S":
                tok += "\\glissando"
                suffix_marks.append('^\\markup { \\bold \\fontsize #-2 "S" }')

            if ev.arpeggio:
                tok += "\\arpeggio"

            if ev.fermata:
                tok += "\\fermata"

            # Palm Mute TextSpanner M18..M22
            if meas.bar == 18 and ev_idx == 0:
                tok += "\\startTextSpan"
            elif meas.bar == 22 and ev_idx == n_events - 1:
                tok += "\\stopTextSpan"
            elif meas.bar == 23 and ev_idx == 0:
                suffix_marks.append('^\\markup { \\italic \\fontsize #-2 "open (cresc.)" }')

            if (meas.bar, ev.slot) in HARMONIC_LABELS:
                suffix_marks.append('^\\markup { \\italic \\fontsize #-2.5 "Harm." }')

            if (meas.bar, ev.slot) == SLAP_LABEL_LOC:
                suffix_marks.append('^\\markup { \\fontsize #-2.5 "拍弦" }')

            cue = PHRASE_CUES.get((meas.bar, ev.slot))
            if cue:
                safe_cue = cue.replace('"', '\\"')
                suffix_marks.append(f'_\\markup {{ \\italic \\fontsize #-2.5 "{safe_cue}" }}')

            bar_tokens.append(prefix + tok + "".join(suffix_marks))

        bar_str = " ".join(bar_tokens)
        line_breaks = {4, 8, 13, 17, 20, 23, 26, 29, 32, 35, 38, 41}
        page_breaks = {17, 32}

        if meas.bar == 44:
            suffix = ' \\bar "|."'
        elif meas.bar in line_breaks:
            suffix = " | \\break"
        else:
            suffix = " |"

        if meas.bar in page_breaks:
            suffix += "\n  \\pageBreak"

        lines.append(f"  {bar_str}{suffix} % M{meas.bar:02d}")
    return "\n".join(lines)


def build_lilypond_source(measures: List[Measure]) -> str:
    chords_block = format_chords(measures)
    tab_block = format_tab_music(measures)

    return f'''\\version "2.24.0"
\\pointAndClickOff

\\paper {{
  #(set-paper-size "a4")
  top-margin = 13\\mm
  bottom-margin = 13\\mm
  left-margin = 13\\mm
  right-margin = 13\\mm
  ragged-last-bottom = ##f
  ragged-bottom = ##f
  system-system-spacing = #'((basic-distance . 13.5) (minimum-distance . 10.5) (padding . 2.5) (stretchability . 10))
  markup-system-spacing = #'((basic-distance . 8) (padding . 2))
  #(define fonts
    (set-global-fonts
      #:roman "Noto Serif CJK SC"
      #:sans "Noto Sans CJK SC"
      #:typewriter "DejaVu Sans Mono"
      #:factor (/ staff-height pt 20)))
}}

\\header {{
  title = \\markup {{ \\bold \\fontsize #3.5 "{TITLE}" }}
  subtitle = \\markup {{ \\fontsize #0.5 "{SUBTITLE}" }}
  composer = \\markup {{ \\fontsize #-0.5 "作曲：{COMPOSER}" }}
  arranger = \\markup {{ \\fontsize #-1 "{ARRANGER_INFO}" }}
  tagline = \\markup {{ \\fontsize #-2 "Call of Silence · 指弹吉他六线谱（技巧编配版 · 4/4 拍 · ♩ = {TEMPO_BPM} · Capo = 2 · F#m / A大调）" }}
}}

\\markup {{
  \\fill-line {{
    \\center-column {{
      \\box \\pad-around #0.45 \\center-column {{
        \\fontsize #-1.6 "{CHORD_LEGEND}"
        \\vspace #0.15
        \\fontsize #-1.8 "{TECHNIQUE_LEGEND_1}"
        \\vspace #0.1
        \\fontsize #-1.8 "{TECHNIQUE_LEGEND_2}"
      }}
      \\vspace #0.3
    }}
  }}
}}

chordTrack = \\chordmode {{
  \\set noChordSymbol = ""
{chords_block}
}}

tabMusic = {{
  \\clef tab
  \\time 4/4
  \\tempo 4 = {TEMPO_BPM}
  \\arpeggioArrowUp
{tab_block}
}}

\\score {{
  <<
    \\new ChordNames \\with {{
      majorSevenSymbol = \\markup {{ \\fontsize #-2 "maj7" }}
      \\override ChordName.font-size = #0.2
      \\override ChordName.font-series = #'bold
    }} \\chordTrack
    \\new TabStaff \\with {{
      stringTunings = #guitar-tuning
      \\override StaffSymbol.staff-space = #1.55
      \\override TabNoteHead.whiteout = ##t
      \\override TabNoteHead.font-size = #-0.5
      \\override TabNoteHead.font-series = #'bold
      \\override Stem.length = #7.0
      \\override Beam.damping = #2.0
      \\override BarNumber.font-size = #-1.5
      \\override BarNumber.padding = #2.2
      \\override RehearsalMark.padding = #2.4
      \\override Glissando.thickness = #2.0
      \\override Glissando.minimum-length = #3.5
      \\override Glissando.springs-and-rods = #ly:spanner::set-spacing-rods
      \\override TextSpanner.bound-details.left.text = \\markup {{ \\bold \\upright \\fontsize #-2 "P.M. " }}
      \\override TextSpanner.style = #'dashed-line
      \\override TextSpanner.bound-details.right.text = \\markup {{ \\draw-line #'(0 . -0.8) }}
      \\override TextSpanner.padding = #2.0
    }} {{
      \\tabFullNotation
      \\tabMusic
    }}
  >>
  \\layout {{
    indent = 0\\mm
    \\context {{
      \\Score
      \\override BarNumber.break-visibility = #end-of-line-invisible
      barNumberVisibility = #(every-nth-bar-number-visible 1)
    }}
  }}
}}
'''


def compile_pdf() -> Path:
    measures = build_measures()
    validate_score(measures)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    ly_path = BUILD_DIR / "Call_of_Silence.ly"
    ly_path.write_text(build_lilypond_source(measures), encoding="utf-8")

    lilypond_bin, env = resolve_lilypond()

    out_prefix = BUILD_DIR / "Call_of_Silence"
    cmd = [str(lilypond_bin), "-dno-point-and-click", "-o", str(out_prefix), str(ly_path)]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    print(res.stdout)
    if res.returncode != 0:
        print(res.stderr)
        raise RuntimeError(f"LilyPond failed with exit code {res.returncode}")
    if res.stderr.strip():
        print("LilyPond stderr:\n", res.stderr)

    built_pdf = BUILD_DIR / "Call_of_Silence.pdf"
    shutil.copy2(built_pdf, OUTPUT_PDF)
    print(f"Generated PDF: {OUTPUT_PDF} ({OUTPUT_PDF.stat().st_size} bytes)")
    return OUTPUT_PDF


if __name__ == "__main__":
    compile_pdf()
