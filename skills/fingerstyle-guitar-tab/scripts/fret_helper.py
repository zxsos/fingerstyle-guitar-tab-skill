#!/usr/bin/env python3
"""Zero-mental-math fretboard helper for fingerstyle guitar arrangements.

Subcommands:
  recommend-capo  --key Bb [--tier beginner]
  jianpu          --key Bb --capo 3 [--tier beginner] 3 5 2 3 1 7, 1
  chord           --chord G D/F# Cmaj7 --capo 3 --max-midi 62 [--tier standard] [--closing]
  harmonic        --key Bb --capo 3
  verify-melody   --score <proj>/src/score_data.py --key Bb --spec <jianpu_spec.txt>
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
    load_score_module,
    midi_to_name,
    note_concert_midi,
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


def _default_base_tonic_midi(ks, octave_c=None, octave_shift=0) -> int:
    if octave_c is not None:
        base_tonic = 12 * (octave_c + 1) + ks.major_tonic_pc
    else:
        c_oct = 48 if ks.major_tonic_pc >= 7 else 60
        base_tonic = c_oct + ks.major_tonic_pc
    return base_tonic + int(octave_shift) * 12


def _midi_to_jianpu(midi: int, base_tonic: int) -> str:
    deg_map = {
        0: "1", 1: "#1", 2: "2", 3: "b3", 4: "3", 5: "4",
        6: "#4", 7: "5", 8: "#5", 9: "6", 10: "b7", 11: "7",
    }
    rel = midi - base_tonic
    octv = rel // 12
    semi = rel % 12
    tok = deg_map[semi]
    if octv > 0:
        tok += "'" * octv
    elif octv < 0:
        tok += "," * (-octv)
    return tok


def _print_single_chord(chord_lbl: str, capo: int, max_midi: int, tier, show_closing: bool) -> None:
    chord_pcs = chord_pitch_classes(chord_lbl, capo)
    root_pc = chord_root_concert_pc(chord_lbl, capo)
    if not chord_pcs:
        raise ValueError(f"无法识别和弦 '{chord_lbl}'，示例: G, Cmaj7, Am7, D/F#, Bb, Fmaj7")

    root_bass: List[Tuple[int, int, int, int, int]] = []
    high_root_bass: List[Tuple[int, int, int, int, int]] = []
    alt_bass: List[Tuple[int, int, int, int, int]] = []
    max_f_hint = min(tier.max_fret, 4 if tier.key == "beginner" else 5)
    for s in (6, 5, 4):
        for f in range(0, tier.max_fret + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            pc = cm % 12
            if pc in chord_pcs:
                if root_pc is not None and pc == root_pc:
                    if f <= max_f_hint:
                        root_bass.append((0 if f == 0 else 1, f, -s, cm, s))
                    else:
                        high_root_bass.append((f, -s, 0, cm, s))
                elif f <= max_f_hint:
                    alt_bass.append((0 if f == 0 else 1, f, -s, cm, s))
    root_bass.sort()
    high_root_bass.sort()
    alt_bass.sort()

    primary_root_s = root_bass[0][4] if root_bass else 6
    primary_root_m = root_bass[0][3] if root_bass else (OPEN_TUNING_NO_CAPO[6] + capo)

    inner_cands: List[Tuple[int, int, int, int, int]] = []
    # Strictly strings 2..5 (NEVER string 6 as inner!), prefer strings above primary root & low frets 0..4
    for s in (4, 3, 2, 5):
        for f in range(0, max_f_hint + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            pc = cm % 12
            if pc in chord_pcs:
                below_root = 1 if (s > primary_root_s or cm <= primary_root_m) else 0
                is_open = 0 if f == 0 else 1
                inner_cands.append((below_root, is_open, f, s, cm))
    inner_cands.sort()

    print(
        f"和弦 [{chord_lbl}] (Capo={capo} 指法和弦名 | 旋律上限 max_midi={max_midi} {midi_to_name(max_midi)} | 档位={tier.key})"
    )
    print("  ★ [ROOT BASS 推荐根音低音 — 和弦换拍首选]")
    if root_bass:
        for _op, f, _ns, cm, s in root_bass[:2]:
            print(f'    n({s}, {f}, "bass")   -> {midi_to_name(cm):4s} ({cm}) [ROOT 根音]')
    elif alt_bass:
        print("    (当前 max_midi 低于根音，备选低音如下)")
        for _op, f, _ns, cm, s in alt_bass[:2]:
            print(f'    n({s}, {f}, "bass")   -> {midi_to_name(cm):4s} ({cm}) [和弦内音]')
    else:
        print("    (无符合 max_midi 的低音弦位，请提高旋律八度或降低 Capo)")
    if high_root_bass:
        f_hi, _ns, _, cm_hi, s_hi = high_root_bass[0]
        print(f'    n({s_hi}, {f_hi}, "bass")   -> {midi_to_name(cm_hi):4s} ({cm_hi}) [高把位根音，仅配合高把位旋律缩跨度]')

    print("  ★ [SAFE INNER 安全内声部 — 仅用于长音/休止空隙或强拍琶音，禁放 6 弦]")
    if inner_cands:
        shown = 0
        for below_root, _op, f, s, cm in inner_cands:
            if shown >= 5:
                break
            tag = " [低于根音-慎用]" if below_root else ""
            print(f'    n({s}, {f}, "inner")  -> {midi_to_name(cm):4s} ({cm}){tag}')
            shown += 1
    else:
        print("    (当前旋律音高已贴近低音区，建议仅保留 bass，不加 inner 防声部交叉)")

    if show_closing and root_bass:
        # Build a clean idiomatic full-voicing closing chord (strings primary_root_s down to 1)
        rs, rf = root_bass[0][4], root_bass[0][1]
        closing_parts = [f'n({rs}, {rf}, "bass")']
        used_frets = [rf] if rf > 0 else []
        for s in range(rs - 1, 0, -1):
            c_opts: List[Tuple[int, int, int]] = []
            for f in range(0, 5):
                cm = OPEN_TUNING_NO_CAPO[s] + capo + f
                if (cm % 12) not in chord_pcs:
                    continue
                tf = used_frets + ([f] if f > 0 else [])
                if tf and (max(tf) - min(tf)) > tier.max_span:
                    continue
                c_opts.append((0 if f == 0 else 1, f, cm))
            c_opts.sort()
            if c_opts:
                _, cf, _cm = c_opts[0]
                role = "melody" if s == 1 else "inner"
                closing_parts.append(f'n({s}, {cf}, "{role}")')
                if cf > 0:
                    used_frets.append(cf)
        print(f"  ★ [CLOSING CHORD 末小节收尾全音符和弦]")
        print(f"    ev(0, 16, {', '.join(closing_parts)}, fermata=True, arpeggio=True)")


def cmd_chord(args: argparse.Namespace) -> int:
    tier = get_tier_profile(args.tier)
    capo = int(args.capo)
    max_midi = int(args.max_midi)
    for idx, ch in enumerate(args.chord):
        if idx > 0:
            print("-" * 78)
        _print_single_chord(ch, capo, max_midi, tier, show_closing=args.closing or len(args.chord) == 1)
    return 0


def cmd_harmonic(args: argparse.Namespace) -> int:
    ks = parse_key_spec(args.key)
    capo = int(args.capo)
    base_tonic = _default_base_tonic_midi(ks, args.octave_c, args.octave_shift)
    fing_name = key_name_from_major_pc((ks.major_tonic_pc - capo) % 12)
    print(
        f"自然 12 品泛音表: 1={ks.relative_major_name} (1基准={midi_to_name(base_tonic)} midi={base_tonic}) "
        f"| Capo={capo} ({fing_name}调指法)"
    )
    print("-" * 78)
    for s in (1, 2, 3, 4, 5, 6):
        cm = OPEN_TUNING_NO_CAPO[s] + capo + 12
        jp = _midi_to_jianpu(cm, base_tonic)
        in_key = "调内 OK" if (cm % 12) in ks.diatonic_pcs else "调外(慎用)"
        print(
            f'  {s}弦12品 -> {midi_to_name(cm):4s} (midi={cm}) | 简谱={jp:4s} | {in_key:8s} | '
            f'n({s}, 12, "melody", harmonic=True)'
        )
    return 0


BAR_HEADER_RE = re.compile(r"^M(\d+)\b(.*)$")
SLOT_TOKEN_RE = re.compile(r"s(\d+):([#b]?[1-7][',’+-]*)(?:\[d(\d+)\])?")


def cmd_verify_melody(args: argparse.Namespace) -> int:
    ks = parse_key_spec(args.key)
    base_tonic = _default_base_tonic_midi(ks, args.octave_c, args.octave_shift)

    score_path = Path(args.score).expanduser().resolve()
    proj_dir = score_path.parent.parent if score_path.name == "score_data.py" else score_path
    mod = load_score_module(proj_dir)
    capo = int(getattr(mod, "CAPO", 0))
    measures = mod.build_measures()
    by_bar = {m.bar: m for m in measures}

    spec_lines = Path(args.spec).expanduser().read_text(encoding="utf-8").splitlines()
    checked_bars = 0
    mismatches: List[str] = []

    for raw_line in spec_lines:
        line = raw_line.strip()
        m = BAR_HEADER_RE.match(line)
        if not m:
            continue
        bar_num = int(m.group(1))
        rest = m.group(2)
        if "|" in rest:
            mel_part = rest.rsplit("|", 1)[-1].strip()
        elif ":" in rest:
            mel_part = rest.split(":", 1)[-1].strip()
        else:
            mel_part = rest.strip()

        slot_matches = SLOT_TOKEN_RE.findall(mel_part)
        if bar_num not in by_bar:
            mismatches.append(f"M{bar_num:02d}: score_data.py 中缺少该小节")
            continue
        checked_bars += 1
        meas = by_bar[bar_num]

        if slot_matches:
            expected_sm = [(int(sl), parse_jianpu_token(tok, base_tonic)[0]) for sl, tok, _d in slot_matches]
            actual_sm = [
                (e.slot, note_concert_midi(nt, capo))
                for e in meas.events
                for nt in e.notes
                if nt.role == "melody"
            ]
            if actual_sm != expected_sm:
                mismatches.append(
                    f"M{bar_num:02d}: 期望(slot,midi)={expected_sm}，实际={actual_sm}"
                )
            else:
                # Also verify explicit [dN] durations when specified in the jianpu spec
                actual_dur_by_slot = {
                    e.slot: e.dur
                    for e in meas.events
                    if any(nt.role == "melody" for nt in e.notes)
                }
                for sl, _tok, d_str in slot_matches:
                    if d_str and actual_dur_by_slot.get(int(sl)) != int(d_str):
                        mismatches.append(
                            f"M{bar_num:02d} s{sl}: 期望显式时值 dur={d_str}，实际 dur={actual_dur_by_slot.get(int(sl))}"
                        )
        else:
            # Bare jianpu tokens without slot prefix (or empty/comment bar)
            bare_tokens = [tok for tok in mel_part.split() if JIANPU_RE.match(tok)]
            if bare_tokens:
                expected_p = [parse_jianpu_token(tok, base_tonic)[0] for tok in bare_tokens]
                actual_p = [
                    note_concert_midi(nt, capo)
                    for e in meas.events
                    for nt in e.notes
                    if nt.role == "melody"
                ]
                if actual_p != expected_p:
                    mismatches.append(
                        f"M{bar_num:02d}: 期望 midi={expected_p}，实际={actual_p}"
                    )

    if checked_bars == 0:
        print("[FAIL] 未在 --spec 文件中解析到任何 M01..MN 简谱行。")
        return 1
    if mismatches:
        print(f"[FAIL] 主旋律逐小节核对发现 {len(mismatches)}/{checked_bars} 处不一致：")
        for item in mismatches[:20]:
            print(f"  - {item}")
        return 1
    print(f"[PASS] 主旋律逐小节核对 100% 匹配：{checked_bars} 个小节 0 漏音、0 错音、0 拍位漂移。")
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

    p_ch = sub.add_parser("chord", help="List ROOT bass & safe inner voicings strictly <= max_midi (supports batch chords).")
    p_ch.add_argument("--chord", nargs="+", required=True, help="One or more fingered chord labels (e.g. G D/F# Em7 Bm7 Cmaj7 D)")
    p_ch.add_argument("--capo", type=int, default=0, help="Capo fret (default 0)")
    p_ch.add_argument("--max-midi", type=int, required=True, help="Current active melody concert MIDI pitch ceiling")
    p_ch.add_argument("--tier", default="standard", choices=("beginner", "standard", "advanced"))
    p_ch.add_argument("--closing", action="store_true", help="Also print a full-voicing closing chord (ev(0, 16, ...))")

    p_hm = sub.add_parser("harmonic", help="List all natural 12th-fret harmonics with concert MIDI and jianpu degrees.")
    p_hm.add_argument("--key", required=True, help="Concert key, e.g. Bb, C, A, F#m")
    p_hm.add_argument("--capo", type=int, default=0, help="Capo fret (default 0)")
    p_hm.add_argument("--octave-c", type=int, default=None, help="Explicit C-octave base for tonic")
    p_hm.add_argument("--octave-shift", type=int, default=0, help="Shift jianpu reference by N octaves")

    p_vm = sub.add_parser("verify-melody", help="Diff every bar's melody (slot & concert MIDI) against a jianpu spec file.")
    p_vm.add_argument("--score", required=True, help="Path to <proj> or <proj>/src/score_data.py")
    p_vm.add_argument("--key", required=True, help="Concert key, e.g. Bb, C, A, F#m")
    p_vm.add_argument("--spec", required=True, help="Path to jianpu spec file with M01..MN lines")
    p_vm.add_argument("--octave-c", type=int, default=None, help="Explicit C-octave base for tonic")
    p_vm.add_argument("--octave-shift", type=int, default=0, help="Shift jianpu reference by N octaves")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "recommend-capo":
        return cmd_recommend_capo(args)
    if args.cmd == "jianpu":
        return cmd_jianpu(args)
    if args.cmd == "chord":
        return cmd_chord(args)
    if args.cmd == "harmonic":
        return cmd_harmonic(args)
    if args.cmd == "verify-melody":
        return cmd_verify_melody(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
