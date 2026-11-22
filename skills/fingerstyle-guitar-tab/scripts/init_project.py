#!/usr/bin/env python3
"""Scaffold a clean single-source-of-truth fingerstyle TAB project."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tab_common import (
    OPEN_TUNING_NO_CAPO,
    chord_pitch_classes,
    chord_root_concert_pc,
    diatonic_chords_for_major_pc,
    get_tier_profile,
    key_name_from_major_pc,
    parse_key_spec,
)


def _pick_safe_note(
    pcs: set,
    capo: int,
    max_fret: int,
    strings: Tuple[int, ...],
    min_m: int,
    max_m: int,
    anchor_fret: Optional[int] = None,
    max_span: int = 15,
) -> Tuple[int, int, int]:
    cands: List[Tuple[int, int, int, int]] = []
    upper_f = min(max_fret, 5)
    for s in strings:
        for f in range(0, upper_f + 1):
            if anchor_fret and f > 0 and abs(f - anchor_fret) > max_span:
                continue
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if min_m <= cm <= max_m and (cm % 12) in pcs:
                cands.append((0 if f == 0 else 1, f, s, cm))
    if not cands:
        for s in strings:
            for f in range(0, max_fret + 1):
                if anchor_fret and f > 0 and abs(f - anchor_fret) > max_span:
                    continue
                cm = OPEN_TUNING_NO_CAPO[s] + capo + f
                if min_m <= cm <= max_m and (cm % 12) in pcs:
                    cands.append((1, f, s, cm))
    if not cands:
        for s in strings:
            for f in range(0, max_fret + 1):
                cm = OPEN_TUNING_NO_CAPO[s] + capo + f
                if min_m <= cm <= max_m and (cm % 12) in pcs:
                    cands.append((1, f, s, cm))
    cands.sort()
    if not cands:
        return strings[-1], 0, OPEN_TUNING_NO_CAPO[strings[-1]] + capo
    _op, f, s, cm = cands[0]
    return s, f, cm


def _build_starter_bars_code(
    key_str: str, capo: int, tier_key: str, n_bars: int, pickup: bool
) -> Tuple[str, str, Dict[int, List[int]]]:
    ks = parse_key_spec(key_str)
    tier = get_tier_profile(tier_key)
    fing_pc = (ks.major_tonic_pc - capo) % 12
    fing_name = key_name_from_major_pc(fing_pc)
    scale_chords = diatonic_chords_for_major_pc(fing_pc)
    # Classic gentle fingerstyle progression I - V - vi - I (using diatonic chords)
    prog = [scale_chords[0], scale_chords[4], scale_chords[5], scale_chords[0]]
    chord_legend = f"和弦对照 (Capo {capo})：指法={fing_name}调  常用={'  '.join(scale_chords)}"

    lines: List[str] = []
    landmarks: Dict[int, List[int]] = {}

    if pickup:
        s0, f0, cm0 = _pick_safe_note(ks.diatonic_pcs, capo, tier.max_fret, (2, 1, 3), 58 + capo, 72 + capo)
        lines.extend([
            "    m.append(Measure(",
            "        bar=0,",
            "        is_pickup=True,",
            '        section="Intro",',
            "        dynamic=0.88,",
            "        events=[",
            f'            ev(12, 4, n({s0}, {f0}, "melody")),',
            "        ],",
            "    ))",
            "",
        ])
        landmarks[0] = [cm0]

    for idx in range(1, n_bars + 1):
        ch = prog[(idx - 1) % len(prog)]
        ch_pcs = chord_pitch_classes(ch, capo)
        root_pc = chord_root_concert_pc(ch, capo)

        mel_s, mel_f, mel_m = _pick_safe_note(ch_pcs, capo, tier.max_fret, (1, 2, 3), 62 + capo, 74 + capo)
        mel_pcs = ch_pcs or ks.diatonic_pcs
        bass_s, bass_f, _ = _pick_safe_note(
            {root_pc} if root_pc is not None else mel_pcs,
            capo,
            tier.max_fret,
            (6, 5, 4),
            40,
            min(mel_m, 57),
            anchor_fret=mel_f,
            max_span=tier.max_span,
        )
        inner_anchor = bass_f or mel_f or None
        inner_s, inner_f, _ = _pick_safe_note(
            mel_pcs,
            capo,
            tier.max_fret,
            (3, 4, 2),
            45,
            mel_m,
            anchor_fret=inner_anchor,
            max_span=tier.max_span,
        )
        if inner_s in (mel_s, bass_s):
            other = [s for s in (3, 4, 2, 5) if s not in (mel_s, bass_s)]
            if other:
                inner_s, inner_f, _ = _pick_safe_note(
                    ks.diatonic_pcs,
                    capo,
                    tier.max_fret,
                    tuple(other),
                    45,
                    mel_m,
                    anchor_fret=inner_anchor,
                    max_span=tier.max_span,
                )

        mel2_s, mel2_f, mel2_m = _pick_safe_note(ks.diatonic_pcs, capo, tier.max_fret, (2, 1, 3), 57 + capo, mel_m + 5)

        section_line = '        section="Intro",\n' if (idx == 1 and not pickup) else ""
        is_last = idx == n_bars

        if tier.key == "advanced" and idx == 1:
            # Ensure even starter template includes H/P/S/arpeggio/harmonic cleanly in advanced tier
            lines.append(f"    # M{idx:02d}: {ch}")
            lines.append("    m.append(Measure(")
            lines.append(f"        bar={idx},")
            if section_line:
                lines.append(section_line.rstrip())
            lines.append("        dynamic=0.90,")
            lines.append(f'        chords=[(0, "{ch}")],')
            lines.append("        events=[")
            lines.append(f'            ev(0, 4, n({bass_s}, {bass_f}, "bass"), n(1, 0, "melody"), arpeggio=True),')
            lines.append(f'            ev(4, 2, n(1, 2, "melody"), link_tech="P"),')
            lines.append('            ev(6, 2, n(1, 0, "melody")),')
            lines.append('            ev(8, 2, n(2, 0, "melody"), link_tech="H"),')
            lines.append('            ev(10, 2, n(2, 2, "melody")),')
            lines.append('            ev(12, 2, n(1, 3, "melody", grace_from=2)),')
            lines.append('            ev(14, 2, n(1, 12, "melody", harmonic=True)),')
            lines.append("        ],")
            lines.append("    ))")
            # Validate melodic pitches carefully in case any aren't diatonic in selected key:
            # Fall back to safe diatonic notes below if outside key.
        elif is_last:
            lines.append(f"    # M{idx:02d}: {ch} (收束琶音)")
            lines.append("    m.append(Measure(")
            lines.append(f"        bar={idx},")
            if section_line:
                lines.append(section_line.rstrip())
            lines.append("        dynamic=0.88,")
            lines.append(f'        chords=[(0, "{ch}")],')
            lines.append("        events=[")
            notes_parts = [f'n({bass_s}, {bass_f}, "bass")']
            if inner_s not in (bass_s, mel_s):
                notes_parts.append(f'n({inner_s}, {inner_f}, "inner")')
            notes_parts.append(f'n({mel_s}, {mel_f}, "melody")')
            lines.append(f"            ev(0, 16, {', '.join(notes_parts)}, fermata=True, arpeggio=True),")
            lines.append("        ],")
            lines.append("    ))")
            landmarks[idx] = [mel_m]
        else:
            lines.append(f"    # M{idx:02d}: {ch}")
            lines.append("    m.append(Measure(")
            lines.append(f"        bar={idx},")
            if section_line:
                lines.append(section_line.rstrip())
            lines.append("        dynamic=0.92,")
            lines.append(f'        chords=[(0, "{ch}")],')
            lines.append("        events=[")
            lines.append(f'            ev(0, 4, n({bass_s}, {bass_f}, "bass"), n({mel_s}, {mel_f}, "melody")),')
            lines.append(f'            ev(4, 4, n({inner_s}, {inner_f}, "inner")),')
            lines.append(f'            ev(8, 4, n({mel2_s}, {mel2_f}, "melody")),')
            lines.append(f'            ev(12, 4, n({inner_s}, {inner_f}, "inner")),')
            lines.append("        ],")
            lines.append("    ))")
            if idx == 1:
                landmarks[1] = [mel_m, mel2_m]

    return "\n".join(lines), chord_legend, landmarks


def render_score_template(
    title: str,
    composer: str,
    artist: str,
    key_str: str,
    capo: int,
    bpm: int,
    tier_key: str,
    n_bars: int,
    pickup: bool,
) -> str:
    ks = parse_key_spec(key_str)
    tier = get_tier_profile(tier_key)
    fing_name = key_name_from_major_pc((ks.major_tonic_pc - capo) % 12)
    file_prefix = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title.strip()).strip("_") or "Fingerstyle_Score"
    bars_code, chord_legend, landmarks = _build_starter_bars_code(key_str, capo, tier_key, n_bars, pickup)
    if tier.key == "advanced":
        # Avoid hardcoded non-diatonic starter frets on Bar 1 when key != G/D; rely on clean template + comment
        bars_code, chord_legend, landmarks = _build_starter_bars_code(key_str, capo, "standard", n_bars, pickup)
        required_techs_line = "REQUIRED_TECHS = ()  # 进阶编配完成后可改为 ('H', 'P', 'S', 'arpeggio', 'harmonic')\n"
    else:
        required_techs_line = ""

    diatonic_sorted = sorted(ks.diatonic_pcs)
    tech_1 = (
        "技巧图例：基本指弹分解 / 琶音"
        if tier.key == "beginner"
        else "技巧图例：H = 击弦   P = 勾弦   S = 滑音   波浪线 = 琶音"
    )
    tech_2 = (
        f"编配档位：{tier.label_zh} | 跨度 <= {tier.max_span} 品 | 最高品位 <= {tier.max_fret} 品"
        if tier.key == "beginner"
        else "<12> = 自然泛音 (Harm.)   P.M. = 右手闷音   X = 拍弦"
    )

    return f'''"""Single source of truth for 《{title}》 fingerstyle guitar TAB & audio.

Concert Key: {ks.name} (1 = {ks.relative_major_name})
Capo: {capo} ({fing_name}调指法)
Time Signature: 4/4 (16 sixteenth-note slots per measure)
Tempo: {bpm} BPM
Difficulty Tier: {tier.key} ({tier.label_zh})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TIER = "{tier.key}"  # "beginner" | "standard" | "advanced"
ALLOW_CHROMATIC = False
{required_techs_line}
TITLE = "{title}"
FILE_PREFIX = "{file_prefix}"
SUBTITLE = "指弹吉他独奏六线谱"
ARTIST = "{artist}"
COMPOSER = "{composer}"
ARRANGER_INFO = "原调：{ks.name} (1={ks.relative_major_name})  |  选调：{fing_name}调指法 (Capo = {capo}品)  |  标准调弦：6E 5A 4D 3G 2B 1E"
CHORD_LEGEND = "{chord_legend}"
TECHNIQUE_LEGEND_1 = "{tech_1}"
TECHNIQUE_LEGEND_2 = "{tech_2}"

TEMPO_BPM = {bpm}
CAPO = {capo}
KEY_SIGNATURE_FIFTHS = {ks.fifths}
IS_MINOR_KEY = {ks.is_minor}

OPEN_TUNING_NO_CAPO: Dict[int, int] = {{
    6: 40,
    5: 45,
    4: 50,
    3: 55,
    2: 59,
    1: 64,
}}

DIATONIC_PCS = {set(diatonic_sorted)}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# 关键小节主旋律 concert_midi 断言（请替换为权威简谱/乐谱对应小节音高，防止编配跑偏）
LANDMARKS: Dict[int, List[int]] = {landmarks}
PHRASE_CUES: Dict[Tuple[int, int], str] = {{}}


@dataclass(frozen=True)
class NoteEvent:
    string: int
    fret: int
    role: str = "melody"  # "melody" | "bass" | "inner" | "slap"
    harmonic: bool = False
    grace_from: Optional[int] = None
    palm_mute: bool = False

    @property
    def concert_midi(self) -> int:
        return OPEN_TUNING_NO_CAPO[self.string] + CAPO + self.fret

    @property
    def written_midi(self) -> int:
        return OPEN_TUNING_NO_CAPO[self.string] + self.fret

    @property
    def pitch_name(self) -> str:
        m = self.concert_midi
        return f"{{NOTE_NAMES[m % 12]}}{{m // 12 - 1}}"


@dataclass
class BeatEvent:
    slot: int
    dur: int
    notes: Tuple[NoteEvent, ...] = ()
    rest: bool = False
    lyric: Optional[str] = None
    fermata: bool = False
    arpeggio: bool = False
    link_tech: Optional[str] = None


@dataclass
class Measure:
    bar: int
    events: List[BeatEvent]
    chords: List[Tuple[int, str]] = field(default_factory=list)
    section: Optional[str] = None
    is_pickup: bool = False
    dynamic: float = 1.0


def n(
    string: int,
    fret: int,
    role: str = "melody",
    *,
    harmonic: bool = False,
    grace_from: Optional[int] = None,
    palm_mute: bool = False,
) -> NoteEvent:
    return NoteEvent(
        string=string,
        fret=fret,
        role=role,
        harmonic=harmonic,
        grace_from=grace_from,
        palm_mute=palm_mute,
    )


def ev(
    slot: int,
    dur: int,
    *notes: NoteEvent,
    rest: bool = False,
    lyric: Optional[str] = None,
    fermata: bool = False,
    arpeggio: bool = False,
    link_tech: Optional[str] = None,
) -> BeatEvent:
    return BeatEvent(
        slot=slot,
        dur=dur,
        notes=notes,
        rest=rest,
        lyric=lyric,
        fermata=fermata,
        arpeggio=arpeggio,
        link_tech=link_tech,
    )


def build_measures() -> List[Measure]:
    """Return arranged measures starting at {'M00 pickup' if pickup else 'M01'}."""
    m: List[Measure] = []
{bars_code}
    return m
'''


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize a starter fingerstyle guitar TAB project.")
    parser.add_argument("--dir", required=True, help="Target project directory")
    parser.add_argument("--title", required=True, help="Song title")
    parser.add_argument("--key", default="C", help="Concert key, e.g. C, G, Bb, F#m")
    parser.add_argument("--capo", type=int, default=0, help="Capo fret (default 0)")
    parser.add_argument("--bpm", type=int, default=80, help="Tempo BPM (default 80)")
    parser.add_argument("--tier", default="standard", choices=("beginner", "standard", "advanced"))
    parser.add_argument("--composer", default="Unknown", help="Composer / Original artist")
    parser.add_argument("--artist", default=None, help="Performer/Artist folder name for library (defaults to composer)")
    parser.add_argument("--bars", type=int, default=4, help="Number of starter full bars (default 4)")
    parser.add_argument("--pickup", action="store_true", help="Include M00 pickup bar")
    parser.add_argument("--force", action="store_true", help="Overwrite existing src/score_data.py")
    args = parser.parse_args()

    proj = Path(args.dir).expanduser().resolve()
    score_path = proj / "src" / "score_data.py"
    if score_path.exists() and not args.force:
        raise FileExistsError(f"{score_path} already exists (pass --force to overwrite).")

    score_path.parent.mkdir(parents=True, exist_ok=True)
    content = render_score_template(
        title=args.title,
        composer=args.composer,
        artist=args.artist or args.composer,
        key_str=args.key,
        capo=args.capo,
        bpm=args.bpm,
        tier_key=args.tier,
        n_bars=max(1, args.bars),
        pickup=args.pickup,
    )
    score_path.write_text(content, encoding="utf-8")
    print(f"[INIT] Created {score_path.name} in {proj} (tier={args.tier}, key={args.key}, capo={args.capo}, bpm={args.bpm})")
    print(f"Next: run `python3 {SCRIPT_DIR / 'tab_engine.py'} check {proj}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
