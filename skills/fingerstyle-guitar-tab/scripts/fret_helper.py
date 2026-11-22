#!/usr/bin/env python3
"""Zero-mental-math fretboard helper for fingerstyle guitar arrangements.

Subcommands:
  recommend-capo  --key Bb [--tier beginner]
  jianpu          --key Bb --capo 3 [--tier beginner] 3 5 2 3 1 7, 1
  chord           --chord G --capo 3 --max-midi 62 [--tier beginner]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tab_common import (
    MAJOR_SCALE_INTERVALS,
    OPEN_TUNING_NO_CAPO,
    chord_pitch_classes,
    chord_root_concert_pc,
    diatonic_chords_for_major_pc,
    get_tier_profile,
    key_name_from_major_pc,
    midi_to_name,
    parse_key_spec,
)

# Open-chord playability ranking for fingered major key centers
FINGERED_KEY_SCORE = {
    7: (100, "G调指法 (最易：G Em C D Am 均为经典开放和弦)"),
    0: (96, "C调指法 (很易：C Am Dm Em G 均为开放和弦，F可用Fmaj7)"),
    2: (82, "D调指法 (中等：D G A Em 顺手，Bm可用Bm7替代)"),
    9: (76, "A调指法 (中等：A D E 顺手，F#m/Bm 需小横按或简化)"),
    4: (68, "E调指法 (偏难：开放E/A不错，F#m/C#m/B 多横按)"),
    5: (45, "F调指法 (含F/Bb大横按，新手建议加变调夹转C/G指法)"),
}


def cmd_recommend_capo(args: argparse.Namespace) -> int:
    ks = parse_key_spec(args.key)
    tier = get_tier_profile(args.tier)

    ranked: List[Tuple[float, int, str, List[str], str]] = []
    max_capo = 7 if tier.key != "beginner" else 6
    for capo in range(0, max_capo + 1):
        fing_pc = (ks.major_tonic_pc - capo) % 12
        fing_name = key_name_from_major_pc(fing_pc)
        base_score, note = FINGERED_KEY_SCORE.get(
            fing_pc, (20, f"{fing_name}调指法 (偏冷门/大量封闭和弦，不推荐)")
        )
        # Beginner strongly prefers open G/C shapes cleanly in capo 0..5
        capo_penalty = capo * (2.0 if tier.key == "beginner" else 1.2)
        if capo > 5:
            capo_penalty += 8.0
        score = base_score - capo_penalty
        chords = diatonic_chords_for_major_pc(fing_pc)
        ranked.append((score, capo, fing_name, chords, note))

    ranked.sort(reverse=True)
    print(f"原调 {ks.name} (1 = {ks.relative_major_name}) — 档位 [{tier.key}: {tier.label_zh}] 变调夹推荐：")
    print("-" * 78)
    for idx, (_score, capo, fing_name, chords, note) in enumerate(ranked[:5]):
        badge = "  <<< 推荐 (RECOMMENDED)" if idx == 0 else ""
        ch_str = " ".join(chords)
        print(f"Capo = {capo:d} | {fing_name:2s}调指法 | 顺阶和弦: {ch_str:28s} | {note}{badge}")
    return 0


JIANPU_RE = re.compile(r"^([#b]?)([1-7])([',’+-]*)$")


def parse_jianpu_token(token: str, base_tonic_midi: int) -> Tuple[int, str]:
    tok = token.strip()
    m = JIANPU_RE.match(tok)
    if not m:
        raise ValueError(
            f"无法解析简谱记号 '{token}'。示例: 1 3 5 7, 1' 2' #4 b7"
        )
    acc, deg_s, oct_marks = m.group(1), m.group(2), m.group(3)
    deg = int(deg_s)
    semis = MAJOR_SCALE_INTERVALS[deg - 1]
    if acc == "#":
        semis += 1
    elif acc == "b":
        semis -= 1

    oct_shift = 0
    for ch in oct_marks:
        if ch in ("'", "’", "+"):
            oct_shift += 1
        elif ch in (",", "-"):
            oct_shift -= 1

    midi = base_tonic_midi + semis + 12 * oct_shift
    return midi, midi_to_name(midi)


def cmd_jianpu(args: argparse.Namespace) -> int:
    ks = parse_key_spec(args.key)
    tier = get_tier_profile(args.tier)
    capo = int(args.capo)

    if args.octave_c is not None:
        base_tonic = 12 * (args.octave_c + 1) + ks.major_tonic_pc
    else:
        # Place 1=X cleanly inside comfortable solo-guitar melody register [G3..E5]
        c_oct = 48 if ks.major_tonic_pc >= 7 else 60
        base_tonic = c_oct + ks.major_tonic_pc
    base_tonic += int(args.octave_shift) * 12

    fing_name = key_name_from_major_pc((ks.major_tonic_pc - capo) % 12)
    print(
        f"简谱查弦: 1={ks.relative_major_name} (1基准={midi_to_name(base_tonic)} midi={base_tonic}) "
        f"| Capo={capo} ({fing_name}调指法) | 档位={tier.key} (上限品={tier.max_fret})"
    )
    print("-" * 78)

    for tok in args.tokens:
        midi, pname = parse_jianpu_token(tok, base_tonic)
        cands: List[Tuple[int, int, int]] = []
        # Rank candidates: open string bonus -> low fret -> treble strings (1..3) preferred for melody
        for s in (1, 2, 3, 4, 5, 6):
            fret = midi - OPEN_TUNING_NO_CAPO[s] - capo
            if 0 <= fret <= tier.max_fret:
                open_pen = 0 if fret == 0 else 1
                string_pen = s * 0.15
                fret_pen = fret * (1.2 if tier.key == "beginner" else 0.7)
                cands.append((open_pen, fret_pen + string_pen, s, fret))
        cands.sort()
        if not cands:
            print(f"  {tok:5s} -> {pname:4s} ({midi}) : [无合法指位! 建议 --octave-shift 1 或调大 Capo]")
        else:
            best_strs = [f'n({s}, {f}, "melody")' for _, _, s, f in cands[:3]]
            print(f"  {tok:5s} -> {pname:4s} ({midi}) : {'   |   '.join(best_strs)}")
    return 0


def cmd_chord(args: argparse.Namespace) -> int:
    tier = get_tier_profile(args.tier)
    capo = int(args.capo)
    max_midi = int(args.max_midi)
    chord_pcs = chord_pitch_classes(args.chord, capo)
    root_pc = chord_root_concert_pc(args.chord, capo)
    if not chord_pcs:
        raise ValueError(f"无法识别和弦 '{args.chord}'，示例: G, Cmaj7, Am7, D/F#, Bb, Fmaj7")

    bass_cands: List[Tuple[int, int, int, int, int]] = []
    inner_cands: List[Tuple[int, int, int, int]] = []

    max_f_hint = min(tier.max_fret, 5 if tier.key == "beginner" else tier.max_fret)
    for s in (6, 5, 4):
        for f in range(0, max_f_hint + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            pc = cm % 12
            if pc in chord_pcs:
                is_not_root = 0 if (root_pc is not None and pc == root_pc) else 1
                bass_cands.append((is_not_root, f, -s, cm, s))

    for s in (4, 3, 2, 5):
        for f in range(0, max_f_hint + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            pc = cm % 12
            if pc in chord_pcs:
                inner_cands.append((0 if f == 0 else 1, f, s, cm))

    bass_cands.sort()
    inner_cands.sort()

    print(
        f"和弦安全伴奏候选: 和弦={args.chord} | Capo={capo} | 严格不高于旋律 max_midi={max_midi} ({midi_to_name(max_midi)}) | 档位={tier.key}"
    )
    print("  [Bass 低音弦位]")
    if bass_cands:
        for is_not_root, f, _ns, cm, s in bass_cands[:4]:
            tag = " [ROOT 根音]" if is_not_root == 0 else ""
            print(f'    n({s}, {f}, "bass")   -> {midi_to_name(cm):4s} ({cm}){tag}')
    else:
        print("    (无符合 max_midi 的低音弦位，请提高旋律八度或降低 Capo)")

    print("  [Inner 内声部弦位]")
    if inner_cands:
        for _op, f, s, cm in inner_cands[:6]:
            print(f'    n({s}, {f}, "inner")  -> {midi_to_name(cm):4s} ({cm})')
    else:
        print("    (当前旋律音高已贴近低音区，建议仅保留 bass，不加 inner 防声部交叉)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fingerstyle guitar fretboard & jianpu helper.")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_capo = sub.add_parser("recommend-capo", help="Recommend best Capo & fingered key for a target key and tier.")
    p_capo.add_argument("--key", required=True, help="Concert key, e.g. C, G, Bb, F#m, Dm")
    p_capo.add_argument("--tier", default="standard", choices=("beginner", "standard", "advanced"))

    p_jp = sub.add_parser("jianpu", help="Convert Jianpu tokens to playable melody n(string, fret, 'melody') calls.")
    p_jp.add_argument("--key", required=True, help="Concert key (or relative minor), e.g. Bb, C, A, F#m")
    p_jp.add_argument("--capo", type=int, default=0, help="Capo fret (default 0)")
    p_jp.add_argument("--tier", default="standard", choices=("beginner", "standard", "advanced"))
    p_jp.add_argument("--octave-c", type=int, default=None, help="Explicit C-octave base for tonic (3 or 4)")
    p_jp.add_argument("--octave-shift", type=int, default=0, help="Shift all notes by N octaves (+1/-1)")
    p_jp.add_argument("tokens", nargs="+", help="Jianpu notes like: 3 5 2 3 1 7, 1'")

    p_ch = sub.add_parser("chord", help="List safe bass & inner voicings strictly <= max_midi (zero voice crossing).")
    p_ch.add_argument("--chord", required=True, help="Fingered chord label (e.g. G, Cmaj7, Am7, D/F#)")
    p_ch.add_argument("--capo", type=int, default=0, help="Capo fret (default 0)")
    p_ch.add_argument("--max-midi", type=int, required=True, help="Current active melody concert MIDI pitch ceiling")
    p_ch.add_argument("--tier", default="standard", choices=("beginner", "standard", "advanced"))
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "recommend-capo":
        return cmd_recommend_capo(args)
    if args.cmd == "jianpu":
        return cmd_jianpu(args)
    if args.cmd == "chord":
        return cmd_chord(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
