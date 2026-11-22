#!/usr/bin/env python3
"""Universal fingerstyle guitar TAB engine: check, pdf, audio, verify, manifest, bundle, build."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent.parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tab_common import (
    OPEN_TUNING_NO_CAPO,
    chord_to_lilypond,
    get_tier_profile,
    load_score_module,
    note_concert_midi,
    note_written_midi,
    safe_file_prefix,
    validate_project_score,
)

PPQ = 480
TICKS_PER_16TH = PPQ // 4
LEAD_IN_TICKS = 120
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


def _ver_key(p: Path) -> Tuple[object, ...]:
    return tuple(int(tok) if tok.isdigit() else tok for tok in p.name.replace("-", ".").split("."))


def _tool_candidates(project_dir: Path) -> List[Path]:
    env = os.environ
    cands: List[Path] = []
    if env.get("TAB_TOOLS_ROOT"):
        cands.append(Path(env["TAB_TOOLS_ROOT"]).expanduser())
    cands.extend([
        project_dir / ".tools" / "root",
        project_dir.parent.parent / ".tools" / "root",
        SKILL_ROOT / ".tools" / "root",
    ])
    return cands


def resolve_lilypond(project_dir: Path) -> Tuple[Path, Dict[str, str]]:
    env = os.environ.copy()
    for tools_root in _tool_candidates(project_dir):
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
                        latest_g = vers[-1]
                        lib_dirs.append(str(latest_g))
                        ccache = latest_g / "ccache"
                        if ccache.is_dir():
                            ccache_dirs.append(str(ccache))
        if env.get("LD_LIBRARY_PATH"):
            lib_dirs.append(env["LD_LIBRARY_PATH"])
        if lib_dirs:
            env["LD_LIBRARY_PATH"] = ":".join(lib_dirs)
        lp_share = tools_root / "usr" / "share" / "lilypond"
        if lp_share.is_dir():
            vers = sorted((d for d in lp_share.iterdir() if d.is_dir()), key=_ver_key)
            if vers:
                env["LILYPOND_DATADIR"] = str(vers[-1])
        guile_share = tools_root / "usr" / "share" / "guile"
        if guile_share.is_dir():
            vers = sorted((d for d in guile_share.iterdir() if d.is_dir()), key=_ver_key)
            if vers:
                env["GUILE_LOAD_PATH"] = str(vers[-1])
        if ccache_dirs:
            env["GUILE_LOAD_COMPILED_PATH"] = ":".join(ccache_dirs)
        return lily_bin, env

    sys_lily = shutil.which("lilypond")
    if sys_lily:
        return Path(sys_lily), env
    raise FileNotFoundError(
        "LilyPond not found in TAB_TOOLS_ROOT, .tools/root, or PATH. "
        "Run `bash skills/fingerstyle-guitar-tab/scripts/setup_tools.sh`."
    )


def resolve_fluidsynth_and_sf2(project_dir: Path) -> Tuple[Path, Path, Dict[str, str]]:
    env = os.environ.copy()
    cands = _tool_candidates(project_dir)
    fs_bin: Optional[Path] = None
    for tools_root in cands:
        cand_fs = tools_root / "usr" / "bin" / "fluidsynth"
        if cand_fs.exists():
            fs_bin = cand_fs
            usr_lib = tools_root / "usr" / "lib"
            lib_dirs: List[str] = []
            if usr_lib.is_dir():
                lib_dirs.extend(str(d) for d in sorted(usr_lib.glob("*-linux-gnu")) if d.is_dir())
            if env.get("LD_LIBRARY_PATH"):
                lib_dirs.append(env["LD_LIBRARY_PATH"])
            if lib_dirs:
                env["LD_LIBRARY_PATH"] = ":".join(lib_dirs)
            break

    if fs_bin is None:
        sys_fs = shutil.which("fluidsynth")
        if sys_fs:
            fs_bin = Path(sys_fs)
    if fs_bin is None:
        raise FileNotFoundError("FluidSynth not found. Run `bash skills/fingerstyle-guitar-tab/scripts/setup_tools.sh`.")

    sf2_cands: List[Path] = []
    if env.get("TAB_SF2_PATH"):
        sf2_cands.append(Path(env["TAB_SF2_PATH"]).expanduser())
    for tools_root in cands:
        sf2_cands.extend([
            tools_root / "usr" / "share" / "sounds" / "sf2" / "FluidR3_GM.sf2",
            tools_root / "usr" / "share" / "soundfonts" / "FluidR3_GM.sf2",
        ])
    sf2_cands.extend([
        Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
        Path("/usr/share/soundfonts/FluidR3_GM.sf2"),
        Path("/opt/homebrew/share/soundfonts/FluidR3_GM.sf2"),
        Path("/usr/local/share/soundfonts/FluidR3_GM.sf2"),
    ])
    sf2_path = next((p for p in sf2_cands if p.is_file()), None)
    if sf2_path is None:
        raise FileNotFoundError("FluidR3_GM.sf2 SoundFont not found.")
    return fs_bin, sf2_path, env


def project_artifact_paths(project_dir: Path, mod: Any) -> Dict[str, Path]:
    prefix = safe_file_prefix(mod)
    return {
        "build": project_dir / "build",
        "previews": project_dir / "previews",
        "pdf": project_dir / f"{prefix}_指弹吉他六线谱.pdf",
        "mp3": project_dir / f"{prefix}_指弹吉他试听.mp3",
        "manifest": project_dir / "sync_manifest.json",
        "bundle": project_dir / f"{prefix}_bundle.zip",
    }


def _midi_to_ly_pitch(midi: int) -> str:
    pc = midi % 12
    octave = midi // 12 - 1
    name = LY_PITCH_NAMES[pc]
    if octave >= 4:
        return name + ("'" * (octave - 3))
    if octave <= 2:
        return name + ("," * (3 - octave))
    return name


def _decompose_duration(sixteenths: int) -> List[int]:
    out: List[int] = []
    rem = sixteenths
    for unit in (16, 12, 8, 6, 4, 3, 2, 1):
        while rem >= unit:
            out.append(unit)
            rem -= unit
    return out


def _partial_str(sixteenths: int) -> str:
    return {4: "4", 8: "2", 12: "2."}.get(sixteenths, "4")


def _format_chords(measures: List[Any]) -> str:
    lines: List[str] = []
    for meas in measures:
        grace_slots = {
            e.slot for e in meas.events if any(nt.grace_from is not None for nt in e.notes)
        }
        if getattr(meas, "is_pickup", False):
            pickup_len = 16 - meas.events[0].slot
            parts = _decompose_duration(pickup_len)
            s_toks = " ".join(f"s{DUR_MAP[u]}" for u in parts)
            lines.append(f"  \\partial {_partial_str(pickup_len)} {s_toks} |")
            continue

        chord_map = dict(getattr(meas, "chords", []))
        boundaries = sorted({0, 16} | set(chord_map.keys()) | grace_slots)
        tokens: List[str] = []
        for idx in range(len(boundaries) - 1):
            start, end = boundaries[idx], boundaries[idx + 1]
            span = end - start
            if start in grace_slots:
                tokens.append("\\grace { s16 }")
            label = chord_map.get(start)
            parts = _decompose_duration(span)
            for p_idx, u in enumerate(parts):
                dstr = DUR_MAP[u]
                if p_idx == 0 and label is not None:
                    tokens.append(chord_to_lilypond(label, dstr))
                else:
                    tokens.append(f"s{dstr}")
        lines.append(f"  {' '.join(tokens)} | % M{meas.bar:02d}")
    return "\n".join(lines)


def _format_tab_music(mod: Any, measures: List[Any]) -> str:
    capo = int(getattr(mod, "CAPO", 0))
    phrase_cues: Dict[Tuple[int, int], str] = dict(getattr(mod, "PHRASE_CUES", {}) or {})
    explicit_breaks = getattr(mod, "LINE_BREAKS", None)
    explicit_pages = getattr(mod, "PAGE_BREAKS", None)
    last_bar = measures[-1].bar
    full_bars = sum(1 for m in measures if not getattr(m, "is_pickup", False))

    if explicit_breaks is not None:
        line_breaks = set(explicit_breaks)
    elif full_bars <= 4:
        line_breaks = set()
    else:
        line_breaks = {m.bar for m in measures if not getattr(m, "is_pickup", False) and m.bar % 4 == 0 and m.bar < last_bar}

    if explicit_pages is not None:
        page_breaks = set(explicit_pages)
    else:
        page_breaks = {
            m.bar for m in measures
            if not getattr(m, "is_pickup", False) and m.bar >= 16 and m.bar % 16 == 0 and (last_bar - m.bar) >= 2
        }

    # Flatten event sequence to compute clean palm-mute TextSpanner boundaries
    flat: List[Tuple[int, int, Any, bool]] = []
    for m_i, meas in enumerate(measures):
        for e_i, ev in enumerate(meas.events):
            has_pm = (not ev.rest) and any(nt.palm_mute for nt in ev.notes)
            flat.append((m_i, e_i, ev, has_pm))

    pm_starts: Set[Tuple[int, int]] = set()
    pm_stops: Set[Tuple[int, int]] = set()
    in_pm = False
    for idx, (m_i, e_i, _ev, has_pm) in enumerate(flat):
        if has_pm and not in_pm:
            pm_starts.add((m_i, e_i))
            in_pm = True
        if in_pm:
            nxt_pm = flat[idx + 1][3] if idx + 1 < len(flat) else False
            if has_pm and not nxt_pm:
                pm_stops.add((m_i, e_i))
                in_pm = False

    harm_labeled = False
    slap_labeled = False
    lines: List[str] = []

    for m_i, meas in enumerate(measures):
        bar_tokens: List[str] = []
        if getattr(meas, "section", None):
            safe_sec = str(meas.section).replace('"', '\\"')
            bar_tokens.append(f'\\mark \\markup {{ \\box \\bold \\fontsize #-0.5 "{safe_sec}" }}')
        if getattr(meas, "is_pickup", False):
            pickup_len = 16 - meas.events[0].slot
            bar_tokens.append(f"\\partial {_partial_str(pickup_len)}")
            bar_tokens.append("\\once \\omit Score.BarNumber")

        for ev_idx, ev in enumerate(meas.events):
            dstr = DUR_MAP[ev.dur]
            prefix = ""
            suffix_marks: List[str] = []

            grace_notes = [nt for nt in ev.notes if nt.grace_from is not None]
            if grace_notes:
                gnt = grace_notes[0]
                g_written = OPEN_TUNING_NO_CAPO[gnt.string] + gnt.grace_from
                prefix = f"\\slashedGrace {{ <{_midi_to_ly_pitch(g_written)}\\{gnt.string}>16\\glissando }} "
                suffix_marks.append('^\\markup { \\bold \\fontsize #-2 "S" }')

            if ev.rest:
                tok = f"r{dstr}"
            else:
                sorted_notes = sorted(ev.notes, key=lambda x: x.string)
                parts: List[str] = []
                for nt in sorted_notes:
                    p_str = _midi_to_ly_pitch(note_written_midi(nt))
                    if nt.role == "slap":
                        parts.append(f'\\tweak TabNoteHead.stencil #(tab-note-head::print-custom-fret-label "X") {p_str}\\{nt.string}')
                    elif nt.harmonic:
                        parts.append(f'\\tweak TabNoteHead.stencil #(tab-note-head::print-custom-fret-label "<12>") {p_str}\\{nt.string}')
                    else:
                        parts.append(f"{p_str}\\{nt.string}")
                tok = f"<{' '.join(parts)}>{dstr}"

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
            if (m_i, ev_idx) in pm_starts:
                tok += "\\startTextSpan"
            if (m_i, ev_idx) in pm_stops:
                tok += "\\stopTextSpan"

            if not harm_labeled and any(nt.harmonic for nt in ev.notes):
                suffix_marks.append('^\\markup { \\italic \\fontsize #-2.5 "Harm." }')
                harm_labeled = True
            if not slap_labeled and any(nt.role == "slap" for nt in ev.notes):
                suffix_marks.append('^\\markup { \\fontsize #-2.5 "拍弦" }')
                slap_labeled = True

            cue = phrase_cues.get((meas.bar, ev.slot))
            if cue:
                safe_cue = cue.replace('"', '\\"')
                suffix_marks.append(f'_\\markup {{ \\italic \\fontsize #-2.5 "{safe_cue}" }}')

            bar_tokens.append(prefix + tok + "".join(suffix_marks))

        bar_str = " ".join(bar_tokens)
        if meas.bar == last_bar:
            suffix = ' \\bar "|."'
        elif meas.bar in line_breaks:
            suffix = " | \\break"
        else:
            suffix = " |"
        if meas.bar in page_breaks:
            suffix += "\n  \\pageBreak"
        lines.append(f"  {bar_str}{suffix} % M{meas.bar:02d}")
    return "\n".join(lines)


def build_lilypond_source(mod: Any, measures: List[Any]) -> str:
    title = str(getattr(mod, "TITLE", "Fingerstyle Score")).replace('"', '\\"')
    tier = get_tier_profile(getattr(mod, "TIER", "standard"))
    subtitle = str(getattr(mod, "SUBTITLE", "指弹吉他独奏六线谱")).replace('"', '\\"')
    composer = str(getattr(mod, "COMPOSER", "Unknown")).replace('"', '\\"')
    arranger = str(getattr(mod, "ARRANGER_INFO", "")).replace('"', '\\"')
    chord_legend = str(getattr(mod, "CHORD_LEGEND", "")).replace('"', '\\"')
    tech1 = str(getattr(mod, "TECHNIQUE_LEGEND_1", "")).replace('"', '\\"')
    tech2 = str(getattr(mod, "TECHNIQUE_LEGEND_2", "")).replace('"', '\\"')
    bpm = int(getattr(mod, "TEMPO_BPM", 80))
    capo = int(getattr(mod, "CAPO", 0))
    full_bars = sum(1 for m in measures if not getattr(m, "is_pickup", False))
    ragged_bottom = "##t" if full_bars <= 12 else "##f"

    chords_block = _format_chords(measures)
    tab_block = _format_tab_music(mod, measures)

    legend_lines: List[str] = []
    for txt in (chord_legend, tech1, tech2):
        if txt.strip():
            legend_lines.append(f'        \\fontsize #-1.7 "{txt}"')
    legend_markup = ""
    if legend_lines:
        joined_leg = "\n        \\vspace #0.12\n".join(legend_lines)
        legend_markup = f'''\\markup {{
  \\fill-line {{
    \\center-column {{
      \\box \\pad-around #0.45 \\center-column {{
{joined_leg}
      }}
      \\vspace #0.3
    }}
  }}
}}'''

    return f'''\\version "2.24.0"
\\pointAndClickOff

\\paper {{
  #(set-paper-size "a4")
  top-margin = 13\\mm
  bottom-margin = 13\\mm
  left-margin = 13\\mm
  right-margin = 13\\mm
  ragged-last-bottom = {ragged_bottom}
  ragged-bottom = {ragged_bottom}
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
  title = \\markup {{ \\bold \\fontsize #3.5 "{title}" }}
  subtitle = \\markup {{ \\fontsize #0.5 "{subtitle}" }}
  composer = \\markup {{ \\fontsize #-0.5 "作曲：{composer}" }}
  arranger = \\markup {{ \\fontsize #-1 "{arranger}" }}
  tagline = \\markup {{ \\fontsize #-2 "{title} · 指弹吉他六线谱（4/4 拍 · ♩ = {bpm} · Capo = {capo}）" }}
}}

{legend_markup}

chordTrack = \\chordmode {{
  \\set noChordSymbol = ""
{chords_block}
}}

tabMusic = {{
  \\clef tab
  \\time 4/4
  \\tempo 4 = {bpm}
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


def _vlq(value: int) -> bytes:
    buf = [value & 0x7F]
    value >>= 7
    while value:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.reverse()
    return bytes(buf)


def _build_midi_track(events: List[Tuple[int, int, bytes]]) -> bytes:
    body = bytearray()
    prev_tick = 0
    for tick, _order, msg in sorted(events, key=lambda x: (x[0], x[1])):
        delta = max(0, tick - prev_tick)
        prev_tick = tick
        body.extend(_vlq(delta))
        body.extend(msg)
    body.extend(_vlq(0))
    body.extend(b"\xff\x2f\x00")
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def _compute_ritardando(mod: Any, measures: List[Any]) -> Tuple[Optional[int], float]:
    bpm = float(getattr(mod, "TEMPO_BPM", 80))
    explicit = getattr(mod, "RITARDANDO_BAR", None)
    if explicit is not None:
        return int(explicit), max(45.0, round(bpm * 0.8333, 1))
    last_meas = measures[-1]
    if last_meas.bar >= 4 and last_meas.events and last_meas.events[-1].dur >= 8:
        return last_meas.bar, max(45.0, round(bpm * 0.8333, 1))
    return None, bpm


def write_midi_file(mod: Any, measures: List[Any], path: Path) -> Tuple[List[float], int]:
    bpm = float(getattr(mod, "TEMPO_BPM", 80))
    capo = int(getattr(mod, "CAPO", 0))
    fifths = int(getattr(mod, "KEY_SIGNATURE_FIFTHS", 0))
    is_minor = 1 if bool(getattr(mod, "IS_MINOR_KEY", False)) else 0
    rit_bar, rit_bpm = _compute_ritardando(mod, measures)

    meta_events: List[Tuple[int, int, bytes]] = []
    ch_events: Dict[int, List[Tuple[int, int, bytes]]] = {0: [], 1: [], 2: [], 3: []}
    slap_ticks: List[int] = []

    meta_events.append((0, 0, b"\xff\x58\x04\x04\x02\x18\x08"))
    meta_events.append((0, 1, b"\xff\x59\x02" + struct.pack("bB", fifths, is_minor)))
    us_main = round(60_000_000 / bpm)
    meta_events.append((0, 2, b"\xff\x51\x03" + us_main.to_bytes(3, "big")))

    chan_cfg = [(0, 25, 112, 60, 22), (1, 24, 90, 68, 24), (2, 46, 92, 64, 32), (3, 28, 104, 62, 14)]
    for ch, prog, vol, pan, rev in chan_cfg:
        ch_events[ch].extend([
            (0, 0, bytes([0xC0 | ch, prog])),
            (0, 1, bytes([0xB0 | ch, 7, vol])),
            (0, 2, bytes([0xB0 | ch, 10, pan])),
            (0, 3, bytes([0xB0 | ch, 91, rev])),
            (0, 4, bytes([0xB0 | ch, 93, 0])),
        ])

    active: Dict[int, Tuple[int, int, str, Tuple[int, ...], int]] = {}

    def stop_string(s: int, off_tick: int) -> None:
        if s not in active:
            return
        midi_p, on_t, _role, chs, _ma = active.pop(s)
        safe_off = max(on_t + 20, off_tick)
        for ch in chs:
            ch_events[ch].append((safe_off, 10, bytes([0x80 | ch, midi_p, 0])))

    def damp_aged(cur_t: int) -> None:
        for s in list(active.keys()):
            _m, on_t, _r, _chs, max_age = active[s]
            if cur_t - on_t >= max_age:
                stop_string(s, cur_t - 8)

    cursor_tick = LEAD_IN_TICKS
    tempo_map_ticks: List[Tuple[int, int]] = [(0, us_main)]
    first_full_bar = next((m.bar for m in measures if not getattr(m, "is_pickup", False)), 1)

    for meas in measures:
        chord_slots = {sl for sl, _lbl in getattr(meas, "chords", [])}
        bar_start_slot = meas.events[0].slot if getattr(meas, "is_pickup", False) else 0

        if rit_bar is not None and meas.bar == rit_bar:
            us_end = round(60_000_000 / rit_bpm)
            meta_events.append((cursor_tick, 2, b"\xff\x51\x03" + us_end.to_bytes(3, "big")))
            tempo_map_ticks.append((cursor_tick, us_end))

        for ev_idx, ev in enumerate(meas.events):
            ev_tick = cursor_tick + (ev.slot - bar_start_slot) * TICKS_PER_16TH
            damp_aged(ev_tick)

            if ev.rest:
                for s in list(active.keys()):
                    stop_string(s, ev_tick)
                continue

            incoming_link: Optional[str] = None
            incoming_string: Optional[int] = None
            if ev_idx > 0 and meas.events[ev_idx - 1].link_tech in ("H", "P", "S"):
                prev_ev = meas.events[ev_idx - 1]
                prev_mel = [x for x in prev_ev.notes if x.role == "melody"]
                if prev_mel:
                    incoming_link = prev_ev.link_tech
                    incoming_string = prev_mel[0].string

            for nt in ev.notes:
                if nt.role == "slap":
                    slap_ticks.append(ev_tick)

            pitched_notes = [nt for nt in ev.notes if nt.role != "slap"]
            if not pitched_notes:
                continue

            is_chord_change = (ev.slot in chord_slots) and not (meas.bar == first_full_bar and ev.slot == 0)
            if is_chord_change:
                for s in list(active.keys()):
                    if active[s][2] in ("bass", "inner"):
                        stop_string(s, ev_tick - 10)

            new_melodies = [nt for nt in pitched_notes if nt.role == "melody"]
            if new_melodies:
                new_m = note_concert_midi(new_melodies[0], capo)
                for s in list(active.keys()):
                    old_m, _on, old_r, _chs, _ma = active[s]
                    if old_r == "melody" and abs(new_m - old_m) <= 2:
                        if not (incoming_link is not None and s == incoming_string):
                            stop_string(s, ev_tick - 10)

            for nt in pitched_notes:
                if nt.role in ("bass", "inner"):
                    nt_m = note_concert_midi(nt, capo)
                    for s in list(active.keys()):
                        old_m, _on, old_r, _chs, _ma = active[s]
                        if old_r == "melody" and abs(nt_m - old_m) <= 1:
                            stop_string(s, ev_tick - 10)

            ordered = sorted(pitched_notes, key=lambda x: -x.string)
            roll_step = 16 if ev.arpeggio else (6 if len(ordered) >= 2 else 0)
            if ev.slot == 0:
                beat_acc = 4
            elif ev.slot == 8:
                beat_acc = 2
            elif ev.slot % 4 == 0:
                beat_acc = 0
            elif ev.slot % 2 == 0:
                beat_acc = -2
            else:
                beat_acc = -5

            for idx, nt in enumerate(ordered):
                on_tick = ev_tick + idx * roll_step
                is_legato = (incoming_link is not None and nt.string == incoming_string)
                if nt.string in active:
                    stop_string(nt.string, on_tick + 8 if is_legato else on_tick - 8)

                midi_p = note_concert_midi(nt, capo)
                dyn = float(getattr(meas, "dynamic", 1.0))

                if nt.grace_from is not None:
                    g_p = OPEN_TUNING_NO_CAPO[nt.string] + capo + nt.grace_from
                    g_on, g_off = max(0, on_tick - 26), on_tick + 6
                    g_v0 = max(32, min(105, round(92 * dyn * 0.70)))
                    g_v1 = max(20, min(80, round(g_v0 * 0.50)))
                    ch_events[0].append((g_on, 18, bytes([0x90, g_p, g_v0])))
                    ch_events[0].append((g_off, 19, bytes([0x80, g_p, 0])))
                    ch_events[1].append((g_on, 18, bytes([0x91, g_p, g_v1])))
                    ch_events[1].append((g_off, 19, bytes([0x81, g_p, 0])))

                if nt.harmonic:
                    ch_events[2].append((on_tick, 20, bytes([0x92, midi_p, max(40, min(112, round(86 * dyn)))])))
                    ch_events[1].append((on_tick, 20, bytes([0x91, midi_p, max(24, min(80, round(48 * dyn)))])))
                    active[nt.string] = (midi_p, on_tick, nt.role, (2, 1), 3000)
                elif nt.palm_mute:
                    ch_events[3].append((on_tick, 20, bytes([0x93, midi_p, max(36, min(108, round(80 * dyn)))])))
                    ch_events[0].append((on_tick, 20, bytes([0x90, midi_p, max(24, min(80, round(48 * dyn)))])))
                    ch_events[0].append((on_tick + 95, 25, bytes([0x80, midi_p, 0])))
                    active[nt.string] = (midi_p, on_tick, nt.role, (3,), 280)
                else:
                    base_vel = {"melody": 98, "bass": 74, "inner": 48}.get(nt.role, 74)
                    raw_vel = (base_vel + beat_acc) * dyn
                    if nt.grace_from is not None:
                        raw_vel *= 0.94
                    if is_legato:
                        if incoming_link == "H":
                            vel_ch0, vel_ch1 = max(32, min(110, round(raw_vel * 0.74))), max(24, min(90, round(raw_vel * 0.52)))
                        elif incoming_link == "P":
                            vel_ch0, vel_ch1 = max(32, min(110, round(raw_vel * 0.70))), max(24, min(90, round(raw_vel * 0.52)))
                        else:
                            vel_ch0, vel_ch1 = max(32, min(110, round(raw_vel * 0.84))), max(24, min(90, round(raw_vel * 0.45)))
                    else:
                        vel_ch0 = max(36, min(114, round(raw_vel)))
                        vel_ch1 = max(24, min(90, round(vel_ch0 * 0.38)))
                    ch_events[0].append((on_tick, 20, bytes([0x90, midi_p, vel_ch0])))
                    ch_events[1].append((on_tick, 20, bytes([0x91, midi_p, vel_ch1])))
                    max_age = 2880 if nt.role == "melody" else 1920
                    active[nt.string] = (midi_p, on_tick, nt.role, (0, 1), max_age)

        bar_sixteenths = (16 - meas.events[0].slot) if getattr(meas, "is_pickup", False) else 16
        cursor_tick += bar_sixteenths * TICKS_PER_16TH

    end_tick = cursor_tick + 720
    for s in list(active.keys()):
        stop_string(s, end_tick)
    tail_tick = end_tick + 960
    for ch in (0, 1, 2, 3):
        ch_events[ch].append((tail_tick, 30, bytes([0xB0 | ch, 123, 0])))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, 5, PPQ)
    midi_bytes = (
        header
        + _build_midi_track(meta_events)
        + _build_midi_track(ch_events[0])
        + _build_midi_track(ch_events[1])
        + _build_midi_track(ch_events[2])
        + _build_midi_track(ch_events[3])
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(midi_bytes)

    tick_to_sec = make_tick_to_sec(PPQ, sorted(tempo_map_ticks))
    slap_times = [tick_to_sec(tk) for tk in slap_ticks]
    return slap_times, end_tick


def _inject_thumb_slaps(wav_path: Path, slap_times: List[float]) -> None:
    if not slap_times:
        return
    with wave.open(str(wav_path), "rb") as wf:
        n_ch, sw, sr, n_frames = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        raw = wf.readframes(n_frames)
    if (n_ch, sw, sr) != (2, 2, 44100):
        raise RuntimeError(f"Unexpected WAV format: ch={n_ch}, sw={sw}, sr={sr}")

    pcm = list(struct.unpack(f"<{n_frames * 2}h", raw))
    slap_len = int(0.030 * sr)
    for idx, t_sec in enumerate(slap_times):
        start_f = int(round(t_sec * sr))
        if not (0 <= start_f < n_frames):
            continue
        rng = random.Random(42 + idx)
        lp = 0.0
        for j in range(slap_len):
            fi = start_f + j
            if fi >= n_frames:
                break
            t = j / sr
            ramp = 0.5 - 0.5 * math.cos(math.pi * t / 0.002) if t < 0.002 else 1.0
            thump = 0.038 * math.sin(2.0 * math.pi * (112.0 * t - 320.0 * t * t)) * math.exp(-t / 0.0085)
            lp = 0.68 * lp + 0.32 * rng.uniform(-1.0, 1.0)
            click = 0.020 * (0.65 * math.cos(2.0 * math.pi * 2100.0 * t) + 0.35 * lp) * math.exp(-t / 0.0026)
            val = ramp * (thump + click) * 32767.0
            li, ri = 2 * fi, 2 * fi + 1
            pcm[li] = max(-32767, min(32767, int(round(pcm[li] + 1.02 * val))))
            pcm[ri] = max(-32767, min(32767, int(round(pcm[ri] + 0.98 * val))))

    out = struct.pack(f"<{len(pcm)}h", *pcm)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(out)


def _probe_duration(media_path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return round(float(res.stdout.strip()), 4)


def _compute_timeline(mod: Any, measures: List[Any], audio_dur: float) -> Tuple[List[Tuple[int, float, float, Any]], List[Dict[str, object]], List[Dict[str, object]]]:
    """Compute per-bar timeline (bar, start_s, end_s, measure), records, and section starts."""
    bpm_main = float(getattr(mod, "TEMPO_BPM", 80))
    rit_bar, rit_bpm = _compute_ritardando(mod, measures)
    cursor_t = LEAD_IN_TICKS / float(PPQ) * (60.0 / bpm_main)
    last_bar = measures[-1].bar

    bar_records: List[Dict[str, object]] = []
    section_starts: List[Dict[str, object]] = []
    cur_sec = "Intro"
    phrase_cues: Dict[Tuple[int, int], str] = dict(getattr(mod, "PHRASE_CUES", {}) or {})
    span_list: List[Tuple[int, float, float, Any]] = []

    for meas in measures:
        if getattr(meas, "section", None):
            cur_sec = str(meas.section)
            section_starts.append({"name": cur_sec, "bar": meas.bar, "start": round(cursor_t, 4)})
        bpm = rit_bpm if (rit_bar is not None and meas.bar == rit_bar) else bpm_main
        beats = ((16 - meas.events[0].slot) / 4.0) if getattr(meas, "is_pickup", False) else 4.0
        dur = beats * (60.0 / bpm)
        end_t = max(cursor_t + 0.1, audio_dur) if meas.bar == last_bar else (cursor_t + dur)
        chords = " → ".join(lbl for _, lbl in getattr(meas, "chords", []))
        if not chords:
            chords = "弱起 Pick-up" if getattr(meas, "is_pickup", False) else ""
        cue = ""
        for (b, _s), txt in phrase_cues.items():
            if b == meas.bar:
                cue = txt
        bar_records.append({
            "bar": meas.bar,
            "start": round(cursor_t, 4),
            "end": round(end_t, 4),
            "sec": cur_sec,
            "chords": chords,
            "cue": cue,
        })
        span_list.append((meas.bar, cursor_t, end_t, meas))
        cursor_t = cursor_t + dur
    return span_list, bar_records, section_starts


def _read_vlq_bytes(data: bytes, p: int) -> Tuple[int, int]:
    val = 0
    while True:
        b = data[p]
        p += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, p


def parse_midi_note_spans(path: Path) -> Tuple[int, List[Tuple[int, int]], Dict[Tuple[int, int], List[Tuple[int, int]]]]:
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"Not a MIDI file: {path}")
    _fmt, ntracks, ppq = struct.unpack(">HHH", data[8:14])
    pos = 8 + struct.unpack(">I", data[4:8])[0]
    tempo_map: List[Tuple[int, int]] = []
    ons: Dict[Tuple[int, int], List[int]] = {}
    offs: Dict[Tuple[int, int], List[int]] = {}

    for _ in range(ntracks):
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        p, end = pos + 8, pos + 8 + length
        tick, running = 0, 0
        while p < end:
            delta, p = _read_vlq_bytes(data, p)
            tick += delta
            status = data[p]
            if status == 0xFF:
                mtype = data[p + 1]
                mlen, p = _read_vlq_bytes(data, p + 2)
                if mtype == 0x51:
                    tempo_map.append((tick, int.from_bytes(data[p:p + 3], "big")))
                p += mlen
                continue
            if status in (0xF0, 0xF7):
                slen, p = _read_vlq_bytes(data, p + 1)
                p += slen
                continue
            if status & 0x80:
                running = status
                p += 1
            else:
                status = running
            kind, ch = status & 0xF0, status & 0x0F
            if kind in (0xC0, 0xD0):
                p += 1
                continue
            d1, d2 = data[p], data[p + 1]
            p += 2
            if kind == 0x90 and d2 > 0:
                ons.setdefault((ch, d1), []).append(tick)
            elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                offs.setdefault((ch, d1), []).append(tick)
        pos = end

    spans: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for key, on_list in ons.items():
        off_list = sorted(offs.get(key, []))
        j = 0
        pairs: List[Tuple[int, int]] = []
        for on_t in sorted(on_list):
            while j < len(off_list) and off_list[j] <= on_t:
                j += 1
            off_t = off_list[j] if j < len(off_list) else on_t + ppq
            j += 1
            pairs.append((on_t, off_t))
        spans[key] = pairs

    tempo_map.sort()
    if not tempo_map or tempo_map[0][0] != 0:
        tempo_map.insert(0, (0, 500000))
    return ppq, tempo_map, spans


def make_tick_to_sec(ppq: int, tempo_map: List[Tuple[int, int]]):
    segs: List[Tuple[int, float, int]] = []
    acc = 0.0
    for i, (tk, us) in enumerate(tempo_map):
        segs.append((tk, acc, us))
        if i + 1 < len(tempo_map):
            acc += (tempo_map[i + 1][0] - tk) / ppq * us / 1e6

    def tick_to_sec(tick: int) -> float:
        seg = segs[0]
        for s in segs:
            if s[0] <= tick:
                seg = s
            else:
                break
        return seg[1] + (tick - seg[0]) / ppq * seg[2] / 1e6

    return tick_to_sec


def extract_note_events(mod: Any, measures: List[Any], duration: float) -> List[Dict[str, object]]:
    capo = int(getattr(mod, "CAPO", 0))
    with tempfile.TemporaryDirectory() as td:
        midi_path = Path(td) / "timeline.mid"
        write_midi_file(mod, measures, midi_path)
        ppq, tempo_map, spans = parse_midi_note_spans(midi_path)

    tick_to_sec = make_tick_to_sec(ppq, tempo_map)
    ticks_per_16th = ppq // 4
    span_index: Dict[Tuple[int, int, int], int] = {
        (ch, pitch, on_t): off_t
        for (ch, pitch), pairs in spans.items()
        for on_t, off_t in pairs
    }

    notes: List[Dict[str, object]] = []
    missing: List[str] = []
    cursor_tick = LEAD_IN_TICKS

    for m_idx, meas in enumerate(measures):
        bar_start_slot = meas.events[0].slot if getattr(meas, "is_pickup", False) else 0
        for ev_idx, ev in enumerate(meas.events):
            if ev.rest:
                continue
            ev_tick = cursor_tick + (ev.slot - bar_start_slot) * ticks_per_16th
            ev_sec = tick_to_sec(ev_tick)

            next_ev = None
            if ev_idx + 1 < len(meas.events):
                next_ev = meas.events[ev_idx + 1]
            elif m_idx + 1 < len(measures) and measures[m_idx + 1].events:
                next_ev = measures[m_idx + 1].events[0]

            for nt in ev.notes:
                if nt.role == "slap":
                    notes.append({"t": round(ev_sec, 4), "d": 0.12, "s": nt.string, "f": 0, "r": "x", "b": meas.bar})

            pitched = sorted((nt for nt in ev.notes if nt.role != "slap"), key=lambda x: -x.string)
            roll_step = 16 if ev.arpeggio else (6 if len(pitched) >= 2 else 0)

            for idx, nt in enumerate(pitched):
                on_tick = ev_tick + idx * roll_step
                channel = 2 if nt.harmonic else (3 if nt.palm_mute else 0)
                cm = note_concert_midi(nt, capo)
                off_tick = span_index.get((channel, cm, on_tick))
                if off_tick is None:
                    missing.append(f"M{meas.bar} slot {ev.slot} string {nt.string} fret {nt.fret}")
                    continue
                t0 = tick_to_sec(on_tick)
                t1 = min(duration, tick_to_sec(off_tick))
                item: Dict[str, object] = {
                    "t": round(t0, 4),
                    "d": round(max(0.05, t1 - t0), 4),
                    "s": nt.string,
                    "f": nt.fret,
                    "r": {"melody": "m", "bass": "b", "inner": "i"}.get(nt.role, "m"),
                    "b": meas.bar,
                }
                if nt.grace_from is not None:
                    item["gf"] = nt.grace_from
                if ev.link_tech in ("H", "P", "S") and nt.role == "melody":
                    item["lt"] = ev.link_tech
                    if next_ev and not next_ev.rest:
                        dest = next((n2 for n2 in next_ev.notes if n2.role == "melody" and n2.string == nt.string), None)
                        if dest is not None:
                            item["to"] = dest.fret
                if nt.harmonic:
                    item["h"] = 1
                if nt.palm_mute:
                    item["pm"] = 1
                if ev.arpeggio:
                    item["arp"] = 1
                notes.append(item)

        cursor_tick += ((16 - meas.events[0].slot) if getattr(meas, "is_pickup", False) else 16) * ticks_per_16th

    if missing:
        raise AssertionError(f"{len(missing)} notes missing MIDI spans: {missing[:5]}")
    notes.sort(key=lambda x: (float(x["t"]), -int(x["s"])))
    return notes


def _page_sort_key(path: Path) -> Tuple[int, str]:
    m = re.search(r"(\d+)", path.stem)
    return (int(m.group(1)) if m else 0, path.name)


def _read_png_gray(png_path: Path) -> Tuple[int, int, bytes]:
    raw = png_path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Not a PNG file: {png_path}")
    w, h = struct.unpack(">II", raw[16:24])
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(png_path), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        capture_output=True,
        check=True,
    )
    if len(res.stdout) != w * h:
        raise RuntimeError(f"Unexpected decoded gray size for {png_path}")
    return w, h, res.stdout


def _white_gap_mid(row_has_ink: List[bool], y_lo: int, y_hi: int, h: int) -> int:
    lo, hi = max(0, y_lo), min(h - 1, y_hi)
    if lo > hi:
        return max(0, min(h - 1, (y_lo + y_hi) // 2))
    best: Optional[Tuple[int, int]] = None
    cur: Optional[List[int]] = None
    for y in range(lo, hi + 1):
        if not row_has_ink[y]:
            cur = [y, y] if cur is None else [cur[0], y]
        elif cur is not None:
            if best is None or (cur[1] - cur[0]) > (best[1] - best[0]):
                best = (cur[0], cur[1])
            cur = None
    if cur is not None and (best is None or (cur[1] - cur[0]) > (best[1] - best[0])):
        best = (cur[0], cur[1])
    return ((best[0] + best[1]) // 2) if best is not None else ((lo + hi) // 2)


def _detect_page_layout(png_path: Path, page_num: int) -> Tuple[int, int, Optional[List[int]], List[Dict[str, object]]]:
    w, h, buf = _read_png_gray(png_path)
    x0, x1 = int(w * 0.05), int(w * 0.95)

    def _extract_staves_at(thresh: float) -> List[List[float]]:
        dark_rows = [
            y for y in range(h)
            if sum(1 for v in buf[y * w + x0 : y * w + x1] if v < 190) > (x1 - x0) * thresh
        ]
        lines: List[List[int]] = []
        for y in dark_rows:
            if not lines or y > lines[-1][-1] + 2:
                lines.append([y])
            else:
                lines[-1].append(y)
        line_ys = [round(sum(g) / len(g), 1) for g in lines]
        grouped: List[List[float]] = []
        for ly in line_ys:
            if not grouped or ly - grouped[-1][-1] > 35:
                grouped.append([ly])
            else:
                grouped[-1].append(ly)

        found: List[List[float]] = []
        for g in grouped:
            k = 0
            while k + 6 <= len(g):
                win = g[k : k + 6]
                gaps = [win[i + 1] - win[i] for i in range(5)]
                if 10.0 <= min(gaps) and max(gaps) <= 32.0 and (max(gaps) - min(gaps)) <= 2.5:
                    found.append(win)
                    k += 6
                else:
                    k += 1
        return found

    staves = _extract_staves_at(0.55)
    for cand in _extract_staves_at(0.25):
        overlaps = any(not (cand[-1] < s[0] - 10 or cand[0] > s[-1] + 10) for s in staves)
        if not overlaps:
            staves.append(cand)
    staves.sort(key=lambda s: s[0])
    if not staves:
        raise RuntimeError(f"No 6-line TAB systems detected on page {page_num} ({png_path.name})")

    row_has_ink = [any(v < 235 for v in buf[y * w : (y + 1) * w]) for y in range(h)]
    first_staff = int(staves[0][0])
    if page_num == 1:
        top_cut = _white_gap_mid(row_has_ink, max(0, first_staff - 170), max(0, first_staff - 55), h)
        header_ink = [y for y in range(0, top_cut + 1) if row_has_ink[y]]
        header_slice = [max(0, min(header_ink) - 16), min(top_cut, max(header_ink) + 16)] if header_ink else None
    else:
        top_cut = _white_gap_mid(row_has_ink, 90, 125, h)
        header_slice = None

    cuts: List[int] = [top_cut]
    for i in range(len(staves) - 1):
        prev_bot, next_top = int(staves[i][-1]), int(staves[i + 1][0])
        safe_lo, safe_hi = prev_bot + 40, next_top - 78
        if safe_lo <= safe_hi and any(not row_has_ink[y] for y in range(safe_lo, safe_hi + 1)):
            cut = _white_gap_mid(row_has_ink, safe_lo, safe_hi, h)
        else:
            cut = _white_gap_mid(row_has_ink, prev_bot + 20, next_top - 20, h)
        cuts.append(cut)

    last_bot = int(staves[-1][-1])
    cuts.append(_white_gap_mid(row_has_ink, last_bot + 40, min(h - 1, last_bot + 160), h))

    sys_entries: List[Dict[str, object]] = []
    scale_x = w / 1241.0
    for si, sys_lines in enumerate(staves):
        y_top, y_bot = int(sys_lines[0]), int(sys_lines[-1])
        c0, c1 = cuts[si], cuts[si + 1]
        ink_ys = [y for y in range(c0, c1 + 1) if row_has_ink[y]]
        ink0, ink1 = (min(ink_ys), max(ink_ys)) if ink_ys else (y_top - 40, y_bot + 40)
        slice_y = [max(c0, ink0 - 14), min(c1, ink1 + 16)]

        gap_ys: List[int] = []
        for k in range(5):
            gap_ys.extend(range(int(sys_lines[k]) + 3, int(sys_lines[k + 1]) - 2))

        xs_dark = [x for x in range(w) if buf[y_top * w + x] < 190]
        sx0, sx1 = min(xs_dark), max(xs_dark)
        span = max(1, y_bot - y_top + 1)

        bar_cols: List[int] = []
        for x in range(sx0, sx1 + 1):
            inside = sum(1 for y in range(y_top, y_bot + 1) if buf[y * w + x] < 140)
            below = sum(1 for y in range(y_bot + 3, min(h, y_bot + 15)) if buf[y * w + x] < 140)
            if inside >= span * 0.95 and below == 0:
                if x > sx1 - 20 or x < sx0 + 10:
                    bar_cols.append(x)
                else:
                    side_dark = sum(1 for y in gap_ys if (buf[y * w + x - 3] < 160 or buf[y * w + x + 3] < 160))
                    if side_dark <= len(gap_ys) * 0.15:
                        bar_cols.append(x)

        clusters: List[List[int]] = []
        for x in bar_cols:
            if not clusters or x > clusters[-1][-1] + 15:
                clusters.append([x])
            else:
                clusters[-1].append(x)
        bar_xs = [round(sum(g) / len(g), 1) for g in clusters]
        if len(bar_xs) < 2:
            raise RuntimeError(f"Page {page_num} Sys {si}: expected >= 2 barlines, got {bar_xs}")

        start_x = (152.0 if (page_num == 1 and si == 0) else 115.0) * scale_x
        if start_x >= bar_xs[1] - 10:
            start_x = round(bar_xs[0] + 20 * scale_x, 1)
        boundaries = [round(start_x, 1)] + bar_xs[1:]
        bar_boxes = [[boundaries[i], boundaries[i + 1]] for i in range(len(boundaries) - 1)]

        sys_entries.append({
            "page": page_num,
            "width": w,
            "height": h,
            "sliceY": slice_y,
            "inkY": [ink0, ink1],
            "staffY": [y_top, y_bot],
            "barBoxes": bar_boxes,
        })
    return w, h, header_slice, sys_entries


LEAK_PAT = re.compile(b"(textedit://|" + b"/" + b"home/|" + b"/" + b"Users/|C:\\\\" + b"Users\\\\)")


def _scan_pdf_leaks(pdf_path: Path) -> List[bytes]:
    pdf_bytes = pdf_path.read_bytes()
    leaks = LEAK_PAT.findall(pdf_bytes)
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
        try:
            leaks.extend(LEAK_PAT.findall(zlib.decompress(m.group(1))))
        except zlib.error:
            pass
    return leaks


# =============================================================================
# CLI Subcommands
# =============================================================================
def cmd_check(proj: Path) -> int:
    mod = load_score_module(proj)
    measures = mod.build_measures()
    errors, tech_counts, summary = validate_project_score(mod, measures)
    if errors:
        print(f"[FAIL] 校验未通过，共 {len(errors)} 处问题 (TIER='{summary.get('tier', '?')}')：")
        for idx, err in enumerate(errors, 1):
            print(f"  {idx}. {err}")
        return 1
    print(
        f"[PASS] Score check OK | TIER={summary['tier']} ({summary['tier_label']}) | "
        f"{summary['measures']} bars (M{summary['first_bar']:02d}..M{summary['last_bar']:02d}) | "
        f"{summary['total_notes']} notes/slaps | max_fret={summary['max_fret']} | "
        f"0 voice crossings | landmarks={summary['landmarks']} | techniques={tech_counts}"
    )
    return 0


def cmd_pdf(proj: Path) -> Path:
    mod = load_score_module(proj)
    measures = mod.build_measures()
    errors, _tc, _sum = validate_project_score(mod, measures)
    if errors:
        raise ValueError("Refusing PDF build because score has errors:\n  " + "\n  ".join(errors[:10]))

    paths = project_artifact_paths(proj, mod)
    build_dir = paths["build"]
    build_dir.mkdir(parents=True, exist_ok=True)
    prefix = safe_file_prefix(mod)
    ly_path = build_dir / f"{prefix}.ly"
    ly_path.write_text(build_lilypond_source(mod, measures), encoding="utf-8")

    lilypond_bin, env = resolve_lilypond(proj)
    out_prefix = build_dir / prefix
    res = subprocess.run(
        [str(lilypond_bin), "-dno-point-and-click", "-o", str(out_prefix), str(ly_path)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        print(res.stdout, res.stderr)
        raise RuntimeError(f"LilyPond failed with exit code {res.returncode}")

    built_pdf = build_dir / f"{prefix}.pdf"
    shutil.copy2(built_pdf, paths["pdf"])
    print(f"[PASS] Generated PDF: {paths['pdf'].name} ({paths['pdf'].stat().st_size:,} bytes)")
    return paths["pdf"]


def _render_previews(proj: Path, mod: Any, clean: bool = True) -> List[Path]:
    paths = project_artifact_paths(proj, mod)
    if not paths["pdf"].is_file():
        raise FileNotFoundError(f"Missing PDF before preview rendering: {paths['pdf']}")
    previews_dir = paths["previews"]
    previews_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for old in previews_dir.glob("page-*.png"):
            old.unlink()
    subprocess.run(
        ["pdftoppm", "-png", "-r", "150", str(paths["pdf"]), str(previews_dir / "page")],
        check=True,
        capture_output=True,
    )
    pngs = sorted(previews_dir.glob("page-*.png"), key=_page_sort_key)
    print(f"[PASS] Rendered {len(pngs)} preview PNG(s) at 150 DPI in previews/")
    return pngs


def cmd_audio(proj: Path) -> Path:
    mod = load_score_module(proj)
    measures = mod.build_measures()
    errors, _tc, _sum = validate_project_score(mod, measures)
    if errors:
        raise ValueError("Refusing audio synthesis because score has errors:\n  " + "\n  ".join(errors[:10]))

    paths = project_artifact_paths(proj, mod)
    build_dir = paths["build"]
    build_dir.mkdir(parents=True, exist_ok=True)
    prefix = safe_file_prefix(mod)
    midi_path = build_dir / f"{prefix}.mid"
    raw_wav = build_dir / f"{prefix}_raw.wav"

    slap_times, _end_tick = write_midi_file(mod, measures, midi_path)
    fs_bin, sf2_path, env = resolve_fluidsynth_and_sf2(proj)
    res = subprocess.run(
        [str(fs_bin), "-ni", "-C", "0", "-R", "1", "-g", "0.70", "-r", "44100", "-F", str(raw_wav), str(sf2_path), str(midi_path)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        print(res.stdout, res.stderr)
        raise RuntimeError(f"FluidSynth failed with exit code {res.returncode}")

    _inject_thumb_slaps(raw_wav, slap_times)
    wav_dur = _probe_duration(raw_wav)
    # Compute nominal end time cleanly from tempo map
    ppq, tmap, _sp = parse_midi_note_spans(midi_path)
    t2s = make_tick_to_sec(ppq, tmap)
    nominal_end = t2s(_end_tick) + 0.6
    target_dur = min(wav_dur, max(nominal_end, wav_dur - 1.5))
    fade_dur = min(3.5, max(0.8, target_dur * 0.12))
    fade_start = max(0.0, target_dur - fade_dur)

    af = (
        "highpass=f=55,"
        "equalizer=f=210:t=q:w=1.2:g=1.5,"
        "equalizer=f=3400:t=q:w=1.4:g=-1.8,"
        "aecho=0.85:0.88:35|62:0.12|0.07,"
        "loudnorm=I=-16:TP=-1.5:LRA=9,"
        f"afade=t=out:st={fade_start:.2f}:d={fade_dur:.2f}"
    )
    ff_res = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw_wav), "-t", f"{target_dur:.2f}",
            "-af", af, "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "320k",
            "-map_metadata", "-1", str(paths["mp3"]),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if ff_res.returncode != 0:
        print(ff_res.stderr)
        raise RuntimeError(f"FFmpeg mastering failed with exit code {ff_res.returncode}")

    print(f"[PASS] Generated MP3: {paths['mp3'].name} ({target_dur:.2f}s, {paths['mp3'].stat().st_size:,} bytes, {len(slap_times)} slap cues)")
    return paths["mp3"]


def _decode_mono_pcm(source: Any, sr: int = 11025) -> Tuple[int, ...]:
    if isinstance(source, Path):
        res = subprocess.run(["ffmpeg", "-i", str(source), "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"], capture_output=True, check=True)
    else:
        res = subprocess.run(["ffmpeg", "-i", "pipe:0", "-ac", "1", "-ar", str(sr), "-f", "s16le", "pipe:1"], input=source, capture_output=True, check=True)
    return struct.unpack(f"<{len(res.stdout) // 2}h", res.stdout)


def _chroma_in_segment(samples: Tuple[int, ...], t0: float, dur: float, diatonic_pcs: Set[int], sr: int = 11025) -> List[float]:
    i0, i1 = max(0, int(t0 * sr)), min(len(samples), int((t0 + dur) * sr))
    seg = samples[i0:i1]
    n_len = len(seg)
    if n_len < 64:
        return [0.0] * 12
    wseg = [seg[i] * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n_len - 1))) for i in range(n_len)]
    spec: Dict[int, float] = {}
    for m in range(40, 82):
        if (m % 12) not in diatonic_pcs:
            continue
        f = 440.0 * (2.0 ** ((m - 69) / 12.0))
        c = 2.0 * math.cos(2.0 * math.pi * f / sr)
        q1 = q2 = 0.0
        for x in wseg:
            q0 = c * q1 - q2 + x
            q2, q1 = q1, q0
        spec[m] = math.sqrt(max(0.0, q1 * q1 + q2 * q2 - q1 * q2 * c)) / n_len
    adj = dict(spec)
    for m in sorted(spec.keys()):
        if spec[m] > 40.0:
            for dh, wt in ((12, 0.45), (19, 0.25), (24, 0.18)):
                if m + dh in adj:
                    adj[m + dh] = max(0.0, adj[m + dh] - wt * spec[m])
    chroma = [0.0] * 12
    for m, v in adj.items():
        chroma[m % 12] += v
    norm = math.sqrt(sum(v * v for v in chroma)) or 1.0
    return [v / norm for v in chroma]


def cmd_verify(proj: Path, ref: Optional[str] = None) -> int:
    mod = load_score_module(proj)
    measures = mod.build_measures()
    paths = project_artifact_paths(proj, mod)

    # Gate 1: Score integrity & tier compliance
    errors, tech_counts, summary = validate_project_score(mod, measures)
    assert not errors, "Gate 1 Score integrity failed:\n  " + "\n  ".join(errors)
    print(
        f"[PASS] Gate 1 Score integrity: TIER={summary['tier']}, {summary['measures']} measures, "
        f"{summary['total_notes']} notes/slaps, max_fret={summary['max_fret']}, "
        f"0 voice crossings, landmarks={summary['landmarks']}, techniques={tech_counts}."
    )

    # Gate 2: PDF integrity & sanitization
    pdf_path = paths["pdf"]
    assert pdf_path.is_file(), f"Missing PDF: {pdf_path}"
    pdf_size = pdf_path.stat().st_size
    assert pdf_size > 15_000, f"PDF unusually small: {pdf_size} bytes"
    gs_res = subprocess.run(
        ["gs", "-q", "-dNODISPLAY", "-dNOSAFER", "-c", f"({pdf_path}) (r) file runpdfbegin pdfpagecount = quit"],
        capture_output=True, text=True, check=True,
    )
    pages = int(gs_res.stdout.strip())
    assert pages >= 1, f"Expected >= 1 page, got {pages}"
    leaks = _scan_pdf_leaks(pdf_path)
    assert not leaks, f"PDF contains unsanitized local path leaks ({len(leaks)} hits): {leaks[:3]}"
    print(f"[PASS] Gate 2 PDF verified: {pdf_path.name} ({pdf_size:,} bytes, {pages} page(s), 0 path leaks).")

    # Gate 3: Audio DSP & diatonic scale purity
    mp3_path = paths["mp3"]
    assert mp3_path.is_file(), f"Missing MP3: {mp3_path}"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(mp3_path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(probe.stdout)
    dur, sr, ch, br = float(info["format"]["duration"]), int(info["streams"][0]["sample_rate"]), int(info["streams"][0]["channels"]), int(info["format"]["bit_rate"])
    assert sr == 44100 and ch == 2 and br >= 310_000, f"Unexpected audio format: sr={sr}, ch={ch}, br={br}"

    stat_res = subprocess.run(
        ["ffmpeg", "-i", str(mp3_path), "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    peak_db = None
    for line in stat_res.stderr.splitlines():
        if "Peak level dB:" in line:
            val = float(line.split(":")[-1].strip())
            if peak_db is None or val > peak_db:
                peak_db = val
    assert peak_db is not None and peak_db < -1.0, f"Peak clipping risk: {peak_db} dBFS"

    st_res = subprocess.run(["ffmpeg", "-i", str(mp3_path), "-ac", "2", "-ar", "22050", "-f", "s16le", "pipe:1"], capture_output=True, check=True)
    lr = struct.unpack(f"<{len(st_res.stdout) // 2}h", st_res.stdout)
    left, right = lr[0::2], lr[1::2]
    l2, r2 = sum(x * x for x in left), sum(x * x for x in right)
    lr_diff_db = abs(10.0 * math.log10((l2 + 1e-9) / (r2 + 1e-9)))
    phase_corr = sum(x * y for x, y in zip(left, right)) / (math.sqrt(l2 * r2) + 1e-9)
    assert lr_diff_db < 1.5, f"Stereo L/R imbalance: {lr_diff_db:.2f} dB"
    assert phase_corr > 0.50, f"Stereo phase corr too low: {phase_corr:.3f}"

    sr_mono = 11025
    samples = _decode_mono_pcm(mp3_path, sr_mono)
    timeline, _recs, _secs = _compute_timeline(mod, measures, dur)
    pc_e = [0.0] * 12
    diatonic_pcs = set(getattr(mod, "DIATONIC_PCS", {0, 2, 4, 5, 7, 9, 11}))
    allow_chromatic = bool(getattr(mod, "ALLOW_CHROMATIC", False))

    for bar_num, t0, t1, meas in timeline:
        is_all_rest = all(e.rest for e in meas.events)
        i0 = max(0, int(t0 * sr_mono))
        i1 = min(len(samples), int(min(dur - 0.3, t1) * sr_mono))
        if i1 - i0 < 512:
            continue
        seg = samples[i0:i1]
        if not is_all_rest:
            rms_db = 20.0 * math.log10(max(1.0, math.sqrt(sum(x * x for x in seg) / len(seg))) / 32768.0)
            assert rms_db > -35.0, f"Bar M{bar_num:02d} unexpectedly silent: {rms_db:.1f} dBFS"
        if bar_num < measures[-1].bar or len(measures) <= 4:
            nb = len(seg)
            for m in range(42, 81):
                f = 440.0 * (2.0 ** ((m - 69) / 12.0))
                c = 2.0 * math.cos(2.0 * math.pi * f / sr_mono)
                q1 = q2 = 0.0
                for x in seg:
                    q0 = c * q1 - q2 + x
                    q2, q1 = q1, q0
                pc_e[m % 12] += math.sqrt(max(0.0, q1 * q1 + q2 * q2 - q1 * q2 * c)) / nb

    tot_e = sum(pc_e) or 1.0
    diatonic_share = sum(pc_e[pc] for pc in diatonic_pcs) / tot_e
    min_share = 0.70 if allow_chromatic else 0.85
    assert diatonic_share > min_share, f"Diatonic energy share {diatonic_share:.3f} <= {min_share}"
    print(
        f"[PASS] Gate 3 MP3 audio verified: {dur:.2f}s, {sr} Hz, {ch}ch, {br // 1000} kbps, "
        f"peak {peak_db:.2f} dBFS, L/R diff {lr_diff_db:.2f} dB, phase corr {phase_corr:.3f}, "
        f"diatonic share {diatonic_share * 100:.1f}%."
    )

    # Gate 4: Optional reference chroma alignment
    if ref:
        if ref.startswith(("http://", "https://")):
            req = urllib.request.Request(ref, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                ref_samples = _decode_mono_pcm(resp.read(), sr_mono)
        else:
            ref_path = Path(ref).expanduser()
            assert ref_path.is_file(), f"Reference audio not found: {ref_path}"
            ref_samples = _decode_mono_pcm(ref_path, sr_mono)

        bar_dur = 4.0 * 60.0 / float(getattr(mod, "TEMPO_BPM", 80))
        s8 = bar_dur / 8.0
        full_bars = [item for item in timeline if item[0] >= 1 and item[0] < measures[-1].bar]
        t_our_b1 = full_bars[0][1] if full_bars else timeline[0][1]
        # Detect first substantial onset in ref as bar 1 anchor if needed
        ref_rms_win = int(0.05 * sr_mono)
        t_ref_b1 = 0.0
        for off in range(0, min(len(ref_samples), 5 * sr_mono), ref_rms_win):
            seg = ref_samples[off:off + ref_rms_win]
            if seg and math.sqrt(sum(x * x for x in seg) / len(seg)) > 500:
                t_ref_b1 = off / sr_mono
                break

        sims: List[float] = []
        for idx_b, (b_num, t0, t1, _m) in enumerate(full_bars):
            for k in range(8):
                c_our = _chroma_in_segment(samples, t0 + k * s8 + 0.015, 0.18, diatonic_pcs, sr_mono)
                c_ref = _chroma_in_segment(ref_samples, t_ref_b1 + idx_b * bar_dur + k * s8 + 0.015, 0.18, diatonic_pcs, sr_mono)
                sims.append(sum(x * y for x, y in zip(c_our, c_ref)))
        overall = sum(sims) / len(sims) if sims else 0.0
        assert overall >= 0.80, f"Gate 4 chroma similarity {overall:.3f} < 0.80"
        print(f"[PASS] Gate 4 Reference chroma alignment: mean 8th-slot cosine = {overall:.3f}.")
    else:
        print("[SKIP] Gate 4 (reference alignment): pass --ref <audio_path_or_url> or set TAB_REF_AUDIO to enable.")

    print("\nALL VERIFICATION CHECKS PASSED.")
    return 0


def cmd_manifest(proj: Path) -> Path:
    mod = load_score_module(proj)
    measures = mod.build_measures()
    paths = project_artifact_paths(proj, mod)

    if not paths["pdf"].is_file():
        cmd_pdf(proj)
    png_files = sorted(paths["previews"].glob("page-*.png"), key=_page_sort_key)
    if not png_files:
        png_files = _render_previews(proj, mod, clean=True)
    if not paths["mp3"].is_file():
        cmd_audio(proj)

    duration = _probe_duration(paths["mp3"])
    all_raw_systems: List[Dict[str, object]] = []
    header_info: Optional[Dict[str, object]] = None
    page_rels: List[str] = []

    for page_idx, png_path in enumerate(png_files, start=1):
        page_rels.append(f"previews/{png_path.name}")
        w, h, header_slice, sys_entries = _detect_page_layout(png_path, page_idx)
        if page_idx == 1 and header_slice is not None:
            header_info = {"page": 1, "width": w, "height": h, "sliceY": header_slice}
        all_raw_systems.extend(sys_entries)

    total_bars_detected = sum(len(s["barBoxes"]) for s in all_raw_systems)
    if total_bars_detected != len(measures):
        raise AssertionError(
            f"Bar count mismatch: detected {total_bars_detected} bar boxes across PNGs, expected {len(measures)} measures"
        )

    _span, bar_records, section_starts = _compute_timeline(mod, measures, duration)
    systems_out: List[Dict[str, object]] = []
    bars_out: List[Dict[str, object]] = []
    bar_cursor = 0

    for sys_idx, raw_sys in enumerate(all_raw_systems):
        sys_bars: List[Dict[str, object]] = []
        for x0, x1 in raw_sys["barBoxes"]:
            rec = dict(bar_records[bar_cursor])
            rec["sysIdx"] = sys_idx
            rec["page"] = raw_sys["page"]
            rec["x0"] = x0
            rec["x1"] = x1
            sys_bars.append(rec)
            bars_out.append(rec)
            bar_cursor += 1
        systems_out.append({
            "sysIdx": sys_idx,
            "page": raw_sys["page"],
            "width": raw_sys["width"],
            "height": raw_sys["height"],
            "sliceY": raw_sys["sliceY"],
            "inkY": raw_sys["inkY"],
            "staffY": raw_sys["staffY"],
            "x0": sys_bars[0]["x0"],
            "x1": sys_bars[-1]["x1"],
            "bars": sys_bars,
        })

    note_events = extract_note_events(mod, measures, duration)
    tier = get_tier_profile(getattr(mod, "TIER", "standard"))
    manifest = {
        "version": 1,
        "tier": tier.key,
        "title": str(getattr(mod, "TITLE", "Fingerstyle Score")),
        "subtitle": str(getattr(mod, "SUBTITLE", "指弹吉他独奏六线谱")),
        "composer": str(getattr(mod, "COMPOSER", "Unknown")),
        "arrangerInfo": str(getattr(mod, "ARRANGER_INFO", "")),
        "bpm": int(getattr(mod, "TEMPO_BPM", 80)),
        "capo": int(getattr(mod, "CAPO", 0)),
        "duration": duration,
        "audio": paths["mp3"].name,
        "pdf": paths["pdf"].name,
        "pages": page_rels,
        "header": header_info,
        "sections": section_starts,
        "systems": systems_out,
        "bars": bars_out,
        "notes": note_events,
    }
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[PASS] Generated sync_manifest.json: {len(systems_out)} systems, {len(bars_out)} bars, {len(note_events)} notes, duration={duration:.2f}s.")
    return paths["manifest"]


def cmd_bundle(proj: Path) -> Path:
    mod = load_score_module(proj)
    paths = project_artifact_paths(proj, mod)
    if not paths["manifest"].is_file():
        cmd_manifest(proj)
    for req in (paths["manifest"], paths["mp3"], paths["pdf"]):
        if not req.is_file():
            raise FileNotFoundError(f"Missing required bundle input: {req}")

    pngs = sorted(paths["previews"].glob("page-*.png"), key=_page_sort_key)
    if not pngs:
        raise FileNotFoundError(f"No preview PNGs in {paths['previews']}")

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest.get("systems") and manifest.get("bars"), "Invalid sync_manifest.json structure"
    leaks = _scan_pdf_leaks(paths["pdf"])
    assert not leaks, f"Refusing to bundle unsanitized PDF ({len(leaks)} leaks)"

    score_src = proj / "src" / "score_data.py"
    if score_src.is_file():
        src_leaks = LEAK_PAT.findall(score_src.read_bytes())
        assert not src_leaks, f"Refusing to bundle unsanitized {score_src.name} ({len(src_leaks)} leaks)"

    with zipfile.ZipFile(paths["bundle"], "w") as zf:
        zf.write(paths["manifest"], arcname="sync_manifest.json", compress_type=zipfile.ZIP_DEFLATED)
        zf.write(paths["mp3"], arcname=paths["mp3"].name, compress_type=zipfile.ZIP_STORED)
        zf.write(paths["pdf"], arcname=paths["pdf"].name, compress_type=zipfile.ZIP_STORED)
        for png in pngs:
            zf.write(png, arcname=f"previews/{png.name}", compress_type=zipfile.ZIP_STORED)
        if score_src.is_file():
            zf.write(score_src, arcname="src/score_data.py", compress_type=zipfile.ZIP_DEFLATED)

    with zipfile.ZipFile(paths["bundle"], "r") as zf:
        bad = zf.testzip()
        assert bad is None, f"ZIP integrity check failed on {bad}"
        names = zf.namelist()
    print(f"[PASS] Generated bundle: {paths['bundle'].name} ({len(names)} entries, {paths['bundle'].stat().st_size:,} bytes)")
    return paths["bundle"]


def cmd_build(proj: Path, ref: Optional[str] = None, keep_build: bool = False) -> int:
    rc = cmd_check(proj)
    if rc != 0:
        return rc
    mod = load_score_module(proj)
    cmd_pdf(proj)
    _render_previews(proj, mod, clean=True)
    cmd_audio(proj)
    cmd_verify(proj, ref=ref)
    cmd_manifest(proj)
    cmd_bundle(proj)
    if not keep_build:
        for junk in (proj / "build", proj / "src" / "__pycache__"):
            if junk.exists():
                shutil.rmtree(junk)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Universal fingerstyle guitar TAB engine.")
    p.add_argument("action", choices=("check", "pdf", "audio", "verify", "manifest", "bundle", "build"))
    p.add_argument("project_dir", help="Project root containing src/score_data.py")
    p.add_argument("--ref", default=os.environ.get("TAB_REF_AUDIO"), help="Optional reference audio path or URL for Gate 4")
    p.add_argument("--keep-build", action="store_true", help="Keep intermediate build/ folder after `build`")
    return p


def main() -> int:
    args = build_parser().parse_args()
    proj = Path(args.project_dir).expanduser().resolve()
    if args.action == "check":
        return cmd_check(proj)
    if args.action == "pdf":
        cmd_pdf(proj)
        return 0
    if args.action == "audio":
        cmd_audio(proj)
        return 0
    if args.action == "verify":
        return cmd_verify(proj, ref=args.ref)
    if args.action == "manifest":
        cmd_manifest(proj)
        return 0
    if args.action == "bundle":
        cmd_bundle(proj)
        return 0
    if args.action == "build":
        return cmd_build(proj, ref=args.ref, keep_build=args.keep_build)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
