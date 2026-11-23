#!/usr/bin/env python3
"""Tier S Importer: convert MIDI (.mid), MusicXML (.musicxml/.xml/.mxl), or AlphaTab/Jitashe JSON (.json) into src/score_data.py."""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from tab_common import (
    KEY_FROM_FIFTHS,
    MAJOR_KEYS,
    OPEN_TUNING_NO_CAPO,
    PC_BY_TOKEN,
    VALID_DURS,
    chord_pitch_classes,
    chord_root_concert_pc,
    diatonic_chords_for_major_pc,
    get_tier_profile,
    key_name_from_major_pc,
    load_score_module,
    midi_to_name,
    parse_key_spec,
    validate_project_score,
)


def _read_vlq(data: bytes, p: int) -> Tuple[int, int]:
    val = 0
    while True:
        b = data[p]
        p += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, p


def parse_midi_file(path: Path) -> Dict[str, object]:
    raw = path.read_bytes()
    if raw[:4] != b"MThd":
        raise ValueError(f"Not a valid MIDI file: {path}")
    hdr_len = struct.unpack(">I", raw[4:8])[0]
    _fmt, ntracks, ppq = struct.unpack(">HHH", raw[8:14])
    if ppq <= 0 or ppq & 0x8000:
        raise ValueError(f"Unsupported MIDI time division in {path}")
    pos = 8 + hdr_len

    bpms: List[Tuple[int, float]] = []
    time_sig: Tuple[int, int] = (4, 4)
    key_hint: Optional[str] = None
    notes: List[Tuple[int, int, int, float, float, int]] = []  # (track, ch, pitch, start_beat, dur_beat, vel)

    for t_idx in range(ntracks):
        if raw[pos:pos + 4] != b"MTrk":
            break
        tlen = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
        p = pos + 8
        end = p + tlen
        pos = end

        tick = 0
        running = 0
        active: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

        while p < end:
            delta, p = _read_vlq(raw, p)
            tick += delta
            if p >= end:
                break
            status = raw[p]
            if status == 0xFF:
                mtype = raw[p + 1]
                mlen, p = _read_vlq(raw, p + 2)
                payload = raw[p:p + mlen]
                p += mlen
                if mtype == 0x51 and len(payload) == 3:
                    us = int.from_bytes(payload, "big")
                    if us > 0:
                        bpms.append((tick, round(60_000_000 / us)))
                elif mtype == 0x58 and len(payload) >= 2:
                    time_sig = (payload[0], 2 ** payload[1])
                elif mtype == 0x59 and len(payload) == 2:
                    sf = struct.unpack("b", bytes([payload[0]]))[0]
                    mi = payload[1]
                    key_hint = KEY_FROM_FIFTHS.get((sf, mi), key_hint)
                continue
            if status in (0xF0, 0xF7):
                slen, p = _read_vlq(raw, p + 1)
                p += slen
                continue
            if status & 0x80:
                running = status
                p += 1
            else:
                status = running

            kind = status & 0xF0
            ch = status & 0x0F
            if kind in (0xC0, 0xD0):
                p += 1
                continue
            if p + 1 >= end:
                break
            d1, d2 = raw[p], raw[p + 1]
            p += 2
            if ch == 9:
                continue
            if kind == 0x90 and d2 > 0:
                active.setdefault((ch, d1), []).append((tick, d2))
            elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                lst = active.get((ch, d1))
                if lst:
                    on_t, vel = lst.pop(0)
                    dur_t = max(ppq // 4, tick - on_t)
                    notes.append((t_idx, ch, d1, on_t / ppq, dur_t / ppq, vel))

        for (ch, d1), rem in active.items():
            for on_t, vel in rem:
                notes.append((t_idx, ch, d1, on_t / ppq, 1.0, vel))

    bpm = int(round(bpms[0][1])) if bpms else 80
    return {
        "notes": notes,
        "bpm": max(40, min(200, bpm)),
        "key_hint": key_hint,
        "time_sig": time_sig,
    }


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _find_child(elem: ET.Element, name: str) -> Optional[ET.Element]:
    for ch in elem:
        if _strip_ns(ch.tag) == name:
            return ch
    return None


def _find_children(elem: ET.Element, name: str) -> List[ET.Element]:
    return [ch for ch in elem if _strip_ns(ch.tag) == name]


def _load_musicxml_root(path: Path) -> ET.Element:
    if path.suffix.lower() == ".mxl":
        with zipfile.ZipFile(path, "r") as zf:
            xml_target: Optional[str] = None
            if "META-INF/container.xml" in zf.namelist():
                c_root = ET.fromstring(zf.read("META-INF/container.xml"))
                for rf in c_root.iter():
                    if _strip_ns(rf.tag) == "rootfile" and rf.attrib.get("full-path"):
                        xml_target = rf.attrib["full-path"]
                        break
            if not xml_target:
                for name in zf.namelist():
                    if not name.startswith("META-INF/") and name.lower().endswith((".xml", ".musicxml")):
                        xml_target = name
                        break
            if not xml_target:
                raise ValueError(f"No MusicXML payload found inside {path}")
            xml_bytes = zf.read(xml_target)
    else:
        xml_bytes = path.read_bytes()
    return ET.fromstring(xml_bytes)


STEP_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_musicxml_file(path: Path) -> Dict[str, object]:
    root = _load_musicxml_root(path)
    title_hint: Optional[str] = None
    composer_hint: Optional[str] = None
    work = _find_child(root, "work")
    if work is not None:
        wt = _find_child(work, "work-title")
        if wt is not None and wt.text:
            title_hint = wt.text.strip()
    ident = _find_child(root, "identification")
    if ident is not None:
        for cr in _find_children(ident, "creator"):
            if cr.attrib.get("type") == "composer" and cr.text:
                composer_hint = cr.text.strip()

    bpm = 80
    bpm_found = False
    key_hint: Optional[str] = None
    time_sig: Tuple[int, int] = (4, 4)
    raw_notes: List[Tuple[int, int, int, float, float, int, bool, bool]] = []

    parts = _find_children(root, "part")
    for p_idx, part in enumerate(parts):
        divisions = 1
        bar_beats = 4.0
        beat_cursor = 0.0
        for meas in _find_children(part, "measure"):
            cur_div = 0
            last_start_div = 0
            max_div_in_bar = 0
            for child in meas:
                tag = _strip_ns(child.tag)
                if tag == "attributes":
                    div_el = _find_child(child, "divisions")
                    if div_el is not None and div_el.text:
                        divisions = max(1, int(float(div_el.text)))
                    key_el = _find_child(child, "key")
                    if key_el is not None and key_hint is None:
                        f_el = _find_child(key_el, "fifths")
                        m_el = _find_child(key_el, "mode")
                        if f_el is not None and f_el.text is not None:
                            sf = int(f_el.text)
                            mi = 1 if (m_el is not None and (m_el.text or "").strip().lower() == "minor") else 0
                            key_hint = KEY_FROM_FIFTHS.get((sf, mi), key_hint)
                    t_el = _find_child(child, "time")
                    if t_el is not None:
                        b_el = _find_child(t_el, "beats")
                        bt_el = _find_child(t_el, "beat-type")
                        if b_el is not None and bt_el is not None and b_el.text and bt_el.text:
                            num = int(re.split(r"[^0-9]+", b_el.text.strip())[0] or "4")
                            den = int(bt_el.text.strip() or "4")
                            time_sig = (num, den)
                            bar_beats = num * (4.0 / den)
                elif tag == "direction":
                    snd = _find_child(child, "sound")
                    if snd is not None and "tempo" in snd.attrib and not bpm_found:
                        bpm = int(round(float(snd.attrib["tempo"])))
                        bpm_found = True
                    dt = _find_child(child, "direction-type")
                    if dt is not None and not bpm_found:
                        met = _find_child(dt, "metronome")
                        if met is not None:
                            pm = _find_child(met, "per-minute")
                            if pm is not None and pm.text:
                                bpm = int(round(float(pm.text)))
                                bpm_found = True
                elif tag == "sound" and "tempo" in child.attrib and not bpm_found:
                    bpm = int(round(float(child.attrib["tempo"])))
                    bpm_found = True
                elif tag == "backup":
                    d_el = _find_child(child, "duration")
                    if d_el is not None and d_el.text:
                        cur_div = max(0, cur_div - int(float(d_el.text)))
                elif tag == "forward":
                    d_el = _find_child(child, "duration")
                    if d_el is not None and d_el.text:
                        cur_div += int(float(d_el.text))
                        max_div_in_bar = max(max_div_in_bar, cur_div)
                elif tag == "note":
                    if _find_child(child, "grace") is not None:
                        continue
                    is_chord = _find_child(child, "chord") is not None
                    start_div = last_start_div if is_chord else cur_div
                    d_el = _find_child(child, "duration")
                    dur_div = int(float(d_el.text)) if (d_el is not None and d_el.text) else divisions
                    if not is_chord:
                        last_start_div = cur_div
                        cur_div += dur_div
                    max_div_in_bar = max(max_div_in_bar, start_div + dur_div)

                    pitch_el = _find_child(child, "pitch")
                    if pitch_el is not None:
                        st = (_find_child(pitch_el, "step").text or "C").strip().upper()
                        oct_v = int((_find_child(pitch_el, "octave").text or "4").strip())
                        alt_el = _find_child(pitch_el, "alter")
                        alt_v = int(round(float(alt_el.text))) if (alt_el is not None and alt_el.text) else 0
                        midi_p = 12 * (oct_v + 1) + STEP_PC[st] + alt_v
                        tie_types = {t.attrib.get("type", "") for t in _find_children(child, "tie")}
                        raw_notes.append((
                            p_idx,
                            0,
                            midi_p,
                            beat_cursor + start_div / divisions,
                            max(0.25, dur_div / divisions),
                            84,
                            "start" in tie_types,
                            "stop" in tie_types,
                        ))
            measured = max_div_in_bar / divisions if max_div_in_bar else bar_beats
            beat_cursor += max(bar_beats, min(bar_beats * 1.5, measured)) if measured > 0 else bar_beats

    # Merge tied notes across beats/bars
    merged: List[Tuple[int, int, int, float, float, int]] = []
    open_ties: Dict[Tuple[int, int], int] = {}
    for p_idx, ch, midi_p, st_b, dur_b, vel, t_start, t_stop in sorted(raw_notes, key=lambda x: (x[3], x[0], x[2])):
        key = (p_idx, midi_p)
        if t_stop and key in open_ties:
            idx = open_ties.pop(key)
            prev = merged[idx]
            merged[idx] = (prev[0], prev[1], prev[2], prev[3], (st_b + dur_b) - prev[3], prev[5])
            if t_start:
                open_ties[key] = idx
        else:
            merged.append((p_idx, ch, midi_p, st_b, dur_b, vel))
            if t_start:
                open_ties[key] = len(merged) - 1

    return {
        "notes": merged,
        "bpm": max(40, min(200, bpm)),
        "key_hint": key_hint,
        "time_sig": time_sig,
        "title_hint": title_hint,
        "composer_hint": composer_hint,
    }


def estimate_major_key(notes: List[Tuple[int, int, int, float, float, int]], key_hint: Optional[str]) -> str:
    if key_hint:
        ks = parse_key_spec(key_hint)
        return ks.name
    pc_weights = [0.0] * 12
    for _tr, _ch, pitch, _st, dur, _vel in notes:
        pc_weights[pitch % 12] += dur
    last_bass_pc = min((x for x in notes[-6:]), key=lambda x: x[2])[2] % 12 if notes else 0
    best: Tuple[float, str] = (-1.0, "C")
    for k_name, (t_pc, _fifths) in MAJOR_KEYS.items():
        if k_name in ("C#", "Gb"):
            continue
        scale = {(t_pc + iv) % 12 for iv in (0, 2, 4, 5, 7, 9, 11)}
        score = sum(pc_weights[pc] for pc in scale)
        if last_bass_pc == t_pc:
            score += 2.0
        elif last_bass_pc == (t_pc + 9) % 12:
            score += 1.5
        if score > best[0]:
            best = (score, k_name)
    return best[1]


def choose_best_capo(major_tonic_pc: int, tier_key: str) -> int:
    pref = {7: 100, 0: 96, 2: 82, 9: 76, 4: 68, 5: 45}
    best: Tuple[float, int] = (-999.0, 0)
    for capo in range(0, 7):
        fing = (major_tonic_pc - capo) % 12
        score = pref.get(fing, 20) - capo * (2.0 if tier_key == "beginner" else 1.2)
        if capo > 5:
            score -= 8.0
        if score > best[0]:
            best = (score, capo)
    return best[1]


def fold_into_range(midi: int, lo: int, hi: int) -> int:
    while midi < lo:
        midi += 12
    while midi > hi:
        midi -= 12
    return midi


def decompose_span(span: int, allow_16th: bool) -> List[int]:
    units = (16, 12, 8, 6, 4, 3, 2, 1) if allow_16th else (16, 12, 8, 6, 4, 2)
    out: List[int] = []
    rem = span
    for u in units:
        while rem >= u:
            out.append(u)
            rem -= u
    return out or [16]


def infer_chord_for_notes(pcs_hist: Dict[int, float], capo: int, chord_pool: List[str]) -> str:
    if not pcs_hist:
        return chord_pool[0]
    best: Tuple[float, str] = (-1.0, chord_pool[0])
    for ch in chord_pool:
        ch_pcs = chord_pitch_classes(ch, capo)
        r_pc = chord_root_concert_pc(ch, capo)
        score = sum(w for pc, w in pcs_hist.items() if pc in ch_pcs)
        if r_pc is not None:
            score += 0.35 * pcs_hist.get(r_pc, 0.0)
        if score > best[0]:
            best = (score, ch)
    return best[1]


def _pick_melody_sf(
    midi_p: int, capo: int, max_fret: int, prev_fret: int
) -> Tuple[int, int]:
    cands: List[Tuple[float, int, int]] = []
    for s in (1, 2, 3, 4):
        f = midi_p - OPEN_TUNING_NO_CAPO[s] - capo
        if 0 <= f <= max_fret:
            open_bonus = -1.2 if f == 0 else 0.0
            dist = abs(f - prev_fret) * 0.55 + f * 0.35 + (s - 1) * 0.4 + open_bonus
            cands.append((dist, s, f))
    if not cands:
        for s in (5, 6):
            f = midi_p - OPEN_TUNING_NO_CAPO[s] - capo
            if 0 <= f <= max_fret:
                cands.append((abs(f - prev_fret) + 4.0, s, f))
    if not cands:
        # Clamp octave into max guitar melody fret range
        lo = OPEN_TUNING_NO_CAPO[4] + capo
        hi = OPEN_TUNING_NO_CAPO[1] + capo + max_fret
        folded = fold_into_range(midi_p, lo, hi)
        return _pick_melody_sf(folded, capo, max_fret, prev_fret)
    cands.sort()
    return cands[0][1], cands[0][2]


def _pick_accomp_sf(
    target_midi: Optional[int],
    capo: int,
    max_fret: int,
    max_span: int,
    mel_midi: int,
    used_strings: Set[int],
    used_frets: List[int],
    chord_pcs: Set[int],
    root_pc: Optional[int],
    role: str,
) -> Optional[Tuple[int, int]]:
    strings = (6, 5, 4) if role == "bass" else (4, 3, 2, 5)
    cands: List[Tuple[int, int, int, int, int]] = []
    for s in strings:
        if s in used_strings:
            continue
        for f in range(0, min(max_fret, 5 if role == "bass" else max_fret) + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > mel_midi:
                continue
            if role == "bass" and target_midi is None and root_pc is not None and (cm % 12) != root_pc and (cm % 12) not in chord_pcs:
                continue
            if chord_pcs and (cm % 12) not in chord_pcs:
                continue
            test_f = used_frets + ([f] if f > 0 else [])
            if test_f and (max(test_f) - min(test_f)) > max_span:
                continue
            pitch_match = 0 if (target_midi is not None and (cm % 12) == (target_midi % 12)) else 1
            root_bonus = 0 if (root_pc is not None and (cm % 12) == root_pc) else 1
            cands.append((pitch_match, root_bonus, 0 if f == 0 else 1, f, s))
    if not cands:
        return None
    cands.sort()
    _pm, _rb, _ob, f, s = cands[0]
    return s, f


def build_score_source_from_import(
    parsed: Dict[str, object],
    *,
    title: str,
    composer: str,
    artist: str,
    tier_key: str,
    key_arg: str,
    capo_arg: str,
    bpm_arg: str,
    melody_track: Optional[int],
    bass_track: Optional[int],
    max_bars: Optional[int],
) -> Tuple[str, Dict[str, object]]:
    raw_notes: List[Tuple[int, int, int, float, float, int]] = list(parsed["notes"])
    if not raw_notes:
        raise ValueError("输入文件未检测到任何可用音符 (notes is empty)。")

    tier = get_tier_profile(tier_key)
    key_name = estimate_major_key(raw_notes, parsed["key_hint"]) if key_arg == "auto" else key_arg
    ks = parse_key_spec(key_name)
    capo = choose_best_capo(ks.major_tonic_pc, tier.key) if capo_arg == "auto" else int(capo_arg)
    bpm = int(parsed["bpm"]) if bpm_arg == "auto" else int(bpm_arg)

    fing_pc = (ks.major_tonic_pc - capo) % 12
    fing_name = key_name_from_major_pc(fing_pc)
    scale_chords = diatonic_chords_for_major_pc(fing_pc)
    chord_pool = scale_chords + [f"{scale_chords[0]}maj7", f"{scale_chords[1]}7", f"{scale_chords[5]}7"]

    # Quantize note starts onto 16th-note grid (8th-note step=2 for beginner!)
    grid_step = 2 if not tier.allow_16th else 1
    first_beat = min(st for _, _, _, st, _, _ in raw_notes)
    lead_shift = 4.0 * int(first_beat // 4.0)

    buckets: Dict[int, List[Tuple[int, int, float]]] = {}
    has_chromatic = False
    max_slot_seen = 0
    for tr, _ch, pitch, st_b, dur_b, _vel in raw_notes:
        rel_b = max(0.0, st_b - lead_shift)
        slot_g = int(round((rel_b * 4.0) / grid_step)) * grid_step
        max_slot_seen = max(max_slot_seen, slot_g)
        buckets.setdefault(slot_g, []).append((tr, pitch, dur_b))
        if (pitch % 12) not in ks.diatonic_pcs:
            has_chromatic = True

    n_bars = max(1, (max_slot_seen // 16) + 1)
    if max_bars is not None:
        n_bars = min(n_bars, max(1, max_bars))

    bars_code: List[str] = []
    landmarks: Dict[int, List[int]] = {}
    prev_mel_fret = 0
    mel_lo = OPEN_TUNING_NO_CAPO[3] + capo
    mel_hi = OPEN_TUNING_NO_CAPO[1] + capo + min(tier.max_fret, 10)
    active_mel_midi: Optional[int] = None

    for bar_idx in range(1, n_bars + 1):
        b0 = (bar_idx - 1) * 16
        b1 = bar_idx * 16

        # Infer primary chord label for this bar
        pc_hist: Dict[int, float] = {}
        bar_slots: Dict[int, List[Tuple[int, int, float]]] = {}
        for g in range(b0, b1):
            if g in buckets:
                local_s = g - b0
                bar_slots[local_s] = buckets[g]
                for _tr, p, d in buckets[g]:
                    pc_hist[p % 12] = pc_hist.get(p % 12, 0.0) + max(0.25, min(2.0, d))

        ch_lbl = infer_chord_for_notes(pc_hist, capo, chord_pool)
        ch_pcs = chord_pitch_classes(ch_lbl, capo)
        root_pc = chord_root_concert_pc(ch_lbl, capo)

        ev_lines: List[str] = []
        bar_mel_midis: List[int] = []
        is_last = bar_idx == n_bars

        if not bar_slots:
            if active_mel_midi is None:
                ev_lines.append("            ev(0, 16, rest=True),")
            else:
                bass_sf = _pick_accomp_sf(None, capo, tier.max_fret, tier.max_span, active_mel_midi, set(), [], ch_pcs, root_pc, "bass")
                if bass_sf is not None:
                    bs, bf = bass_sf
                    ev_lines.append(f'            ev(0, 16, n({bs}, {bf}, "bass")),')
                else:
                    ev_lines.append("            ev(0, 16, rest=True),")
                    active_mel_midi = None
        else:
            sorted_starts = sorted(bar_slots.keys())
            if sorted_starts[0] > 0:
                cur_rest_s = 0
                for sub_d in decompose_span(sorted_starts[0], tier.allow_16th):
                    ev_lines.append(f"            ev({cur_rest_s}, {sub_d}, rest=True),")
                    cur_rest_s += sub_d
                active_mel_midi = None

            for k_idx, s_pos in enumerate(sorted_starts):
                next_s = sorted_starts[k_idx + 1] if (k_idx + 1 < len(sorted_starts)) else 16
                span = next_s - s_pos
                parts_d = decompose_span(span, tier.allow_16th)

                notes_here = bar_slots[s_pos]
                # Select melody candidate (honor --melody-track if given)
                mel_cands = [item for item in notes_here if melody_track is None or item[0] == melody_track]
                mel_raw = max(mel_cands, key=lambda x: x[1])[1] if mel_cands else max(notes_here, key=lambda x: x[1])[1]
                mel_m = fold_into_range(mel_raw, mel_lo, mel_hi)
                mel_s, mel_f = _pick_melody_sf(mel_m, capo, tier.max_fret, prev_mel_fret)
                prev_mel_fret = mel_f
                real_mel_m = OPEN_TUNING_NO_CAPO[mel_s] + capo + mel_f
                active_mel_midi = real_mel_m
                bar_mel_midis.append(real_mel_m)

                note_calls = [f'n({mel_s}, {mel_f}, "melody")']
                used_s: Set[int] = {mel_s}
                used_f: List[int] = [mel_f] if mel_f > 0 else []

                # Pick bass if distinct lower note exists or on strong beats (0, 8)
                bass_cands = [
                    item[1] for item in notes_here
                    if (bass_track is None or item[0] == bass_track) and item[1] <= real_mel_m
                ]
                bass_target: Optional[int] = min(bass_cands) if bass_cands else None
                want_bass = (bass_target is not None and (bass_target % 12 != real_mel_m % 12 or bass_target < real_mel_m - 4)) or (s_pos in (0, 8))
                if want_bass:
                    b_sf = _pick_accomp_sf(
                        bass_target, capo, tier.max_fret, tier.max_span, real_mel_m,
                        used_s, used_f, ch_pcs, root_pc, "bass",
                    )
                    if b_sf is not None:
                        bs, bf = b_sf
                        note_calls.insert(0, f'n({bs}, {bf}, "bass")')
                        used_s.add(bs)
                        if bf > 0:
                            used_f.append(bf)

                # Optional inner voice on downbeats if tier allows >= 3 voices
                if tier.max_simultaneous >= 3 and s_pos in (0, 8):
                    i_sf = _pick_accomp_sf(
                        None, capo, tier.max_fret, tier.max_span, real_mel_m,
                        used_s, used_f, ch_pcs, root_pc, "inner",
                    )
                    if i_sf is not None:
                        ins, inf = i_sf
                        note_calls.insert(-1, f'n({ins}, {inf}, "inner")')
                        used_s.add(ins)
                        if inf > 0:
                            used_f.append(inf)

                extra = ", arpeggio=True" if (is_last and k_idx == len(sorted_starts) - 1 and len(note_calls) >= 2 and "arpeggio" in tier.allowed_techs) else ""
                if is_last and k_idx == len(sorted_starts) - 1:
                    extra += ", fermata=True"

                cur_sub_s = s_pos
                for p_i, u in enumerate(parts_d):
                    if p_i == 0:
                        ev_lines.append(f"            ev({cur_sub_s}, {u}, {', '.join(note_calls)}{extra}),")
                    else:
                        # Continuation sub-slot: support sustained melody with soft chord tone or rest cleanly
                        fill_sf = _pick_accomp_sf(
                            None, capo, tier.max_fret, tier.max_span, real_mel_m,
                            set(), [], ch_pcs, root_pc, "inner",
                        )
                        if fill_sf is not None and not is_last:
                            fs, ff = fill_sf
                            ev_lines.append(f'            ev({cur_sub_s}, {u}, n({fs}, {ff}, "inner")),')
                        elif is_last:
                            # Hold closing chord shape softly instead of hard rest before final note? Better rest if needed or merge!
                            ev_lines.append(f"            ev({cur_sub_s}, {u}, rest=True),")
                            active_mel_midi = None
                        else:
                            ev_lines.append(f"            ev({cur_sub_s}, {u}, rest=True),")
                            active_mel_midi = None
                    cur_sub_s += u

        section_line = '        section="Main",\n' if bar_idx == 1 else ""
        bars_code.append(f"    # M{bar_idx:02d}: {ch_lbl}")
        bars_code.append("    m.append(Measure(")
        bars_code.append(f"        bar={bar_idx},")
        if section_line:
            bars_code.append(section_line.rstrip())
        bars_code.append(f"        dynamic={'0.88' if is_last else '0.94'},")
        bars_code.append(f'        chords=[(0, "{ch_lbl}")],')
        bars_code.append("        events=[")
        bars_code.extend(ev_lines)
        bars_code.append("        ],")
        bars_code.append("    ))")

        if bar_mel_midis and len(landmarks) < 4 and bar_idx in (1, 2, max(2, n_bars // 2), n_bars):
            landmarks[bar_idx] = bar_mel_midis

    # Guarantee standard tier arpeggio requirement even if last bar ended with 1 note
    has_arp = any("arpeggio=True" in ln for ln in bars_code)
    req_line = "REQUIRED_TECHS = ()\n" if (tier.key == "advanced" or (not has_arp and tier.key == "standard")) else ""

    file_prefix = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title.strip()).strip("_") or "Imported_Score"
    diatonic_sorted = sorted(ks.diatonic_pcs)

    src = f'''"""Auto-imported fingerstyle guitar TAB score_data.py for 《{title}》.

Concert Key: {ks.name} (1 = {ks.relative_major_name})
Capo: {capo} ({fing_name}调指法)
Time Signature: 4/4 (16 sixteenth-note slots per measure)
Tempo: {bpm} BPM
Difficulty Tier: {tier.key} ({tier.label_zh})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TIER = "{tier.key}"
ALLOW_CHROMATIC = {has_chromatic}
{req_line}
TITLE = "{title}"
FILE_PREFIX = "{file_prefix}"
SUBTITLE = "指弹吉他独奏六线谱"
ARTIST = "{artist}"
COMPOSER = "{composer}"
ARRANGER_INFO = "原调：{ks.name} (1={ks.relative_major_name})  |  选调：{fing_name}调指法 (Capo = {capo}品)  |  标准调弦：6E 5A 4D 3G 2B 1E"
CHORD_LEGEND = "和弦对照 (Capo {capo})：指法={fing_name}调  常用={'  '.join(scale_chords)}"
TECHNIQUE_LEGEND_1 = "技巧图例：{'基本指弹（新手档）' if tier.key == 'beginner' else 'H = 击弦   P = 勾弦   S = 滑音   波浪线 = 琶音'}"
TECHNIQUE_LEGEND_2 = "编配档位：{tier.label_zh} | 跨度 <= {tier.max_span} 品 | 最高品位 <= {tier.max_fret} 品"

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

LANDMARKS: Dict[int, List[int]] = {landmarks}
PHRASE_CUES: Dict[Tuple[int, int], str] = {{}}


@dataclass(frozen=True)
class NoteEvent:
    string: int
    fret: int
    role: str = "melody"
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
    """Return auto-imported fingerstyle measures (M01..M{n_bars:02d})."""
    m: List[Measure] = []
{chr(10).join(bars_code)}
    return m
'''
    meta = {
        "key": ks.name,
        "capo": capo,
        "fing_key": fing_name,
        "bpm": bpm,
        "bars": n_bars,
        "has_chromatic": has_chromatic,
    }
    return src, meta


def build_score_source_from_alphatab_json(
    json_path: Path,
    *,
    title_override: Optional[str],
    composer_override: Optional[str],
    artist_override: Optional[str],
    tier_key: str,
    key_arg: str,
    capo_arg: str,
    bpm_arg: str,
    track_idx: Optional[int] = None,
    max_bars: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Direct guitar-tab importer for AlphaTab / Jitashe JSON preserving real string/fret & techniques.

    CRITICAL: AlphaTab indexes `note.string` from LOWEST pitch string (1 = 6th string E2) to HIGHEST
    (6 = 1st string E4), which is INVERTED relative to standard TAB (1 = high E4, 6 = low E2).
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    tracks = data.get("tracks") or []
    if not tracks:
        raise ValueError(f"AlphaTab JSON has no tracks: {json_path}")

    t_idx = 0 if track_idx is None else max(0, min(len(tracks) - 1, track_idx))
    track = tracks[t_idx]
    staves = track.get("staves") or []
    if not staves:
        raise ValueError(f"Track {t_idx} has no staves: {json_path}")
    staff = staves[0]
    raw_bars = staff.get("bars") or []
    if max_bars is not None and max_bars > 0:
        raw_bars = raw_bars[:max_bars]
    if not raw_bars:
        raise ValueError(f"AlphaTab JSON has no bars: {json_path}")

    st_tuning = (staff.get("stringtuning") or {}).get("tunings") or [64, 59, 55, 50, 45, 40]
    n_strings = len(st_tuning)
    if n_strings < 6:
        st_tuning = [64, 59, 55, 50, 45, 40]
        n_strings = 6
    # Map real_string (1..6, high E -> low E) to open MIDI pitch in AlphaTab's tuning
    # AlphaTab tunings array is ordered high->low (index 0 = string 1 high E = 64, index 5 = string 6 low E = 40)
    # while AlphaTab note.string is 1..n_strings from LOW to HIGH (1 = 6th string, 6 = 1st string)!
    tuning_by_real_s: Dict[int, int] = {s: int(st_tuning[s - 1]) for s in range(1, 7)}
    is_std_tuning = all(tuning_by_real_s[s] == OPEN_TUNING_NO_CAPO[s] for s in range(1, 7))

    capo_raw = staff.get("capo") or track.get("capo") or 0
    capo = int(capo_arg) if capo_arg != "auto" else int(capo_raw)

    masterbars = data.get("masterbars") or []
    detected_bpm = 72
    for mb in masterbars:
        t_autos = mb.get("tempoautomations") or []
        if t_autos and t_autos[0].get("value"):
            detected_bpm = int(round(float(t_autos[0]["value"])))
            break
    if data.get("tempo") and detected_bpm == 72:
        detected_bpm = int(round(float(data["tempo"])))
    bpm = int(bpm_arg) if bpm_arg != "auto" else max(40, min(200, detected_bpm))

    tier = get_tier_profile(tier_key)

    # First pass: collect all sounded concert MIDI pitches (excluding ties) for key detection
    all_midis: List[int] = []
    for b in raw_bars:
        for v in b.get("voices") or []:
            for bt in v.get("beats") or []:
                if bt.get("isempty"):
                    continue
                for nt in bt.get("notes") or []:
                    if nt.get("istiedestination"):
                        continue
                    at_s = int(nt.get("string") or 1)
                    real_s = n_strings + 1 - at_s
                    if 1 <= real_s <= 6:
                        all_midis.append(tuning_by_real_s[real_s] + capo + int(nt.get("fret") or 0))

    if key_arg == "auto":
        maj_name = estimate_major_key([(0, 0, m, 0.0, 1.0, 90) for m in all_midis], None)
        ks = parse_key_spec(maj_name)
    else:
        ks = parse_key_spec(key_arg)

    fing_pc = (ks.major_tonic_pc - capo) % 12
    fing_name = key_name_from_major_pc(fing_pc)
    scale_chords = diatonic_chords_for_major_pc(fing_pc)

    title = (
        title_override
        or str(data.get("title") or "").strip()
        or json_path.stem.replace("_", " ").strip()
        or "Untitled"
    )
    raw_composer = (
        composer_override
        or str(data.get("music") or data.get("artist") or "").strip()
        or "Unknown"
    )
    artist = artist_override or str(data.get("artist") or "").strip() or raw_composer
    file_prefix = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_") or "Fingerstyle_Score"

    # Second pass: build exact 16-slot measures with real fingerings, ties, grace, H/P/S, harmonics
    n_bars = len(raw_bars)
    bars_code: List[str] = []
    landmarks: Dict[int, List[int]] = {}
    has_chromatic = False
    active_melody: Optional[int] = None
    arpeggio_emitted = False

    def _section_for_bar(b_num: int, total: int) -> Tuple[Optional[str], float]:
        mb = masterbars[b_num - 1] if b_num - 1 < len(masterbars) else {}
        sec_obj = mb.get("section") if isinstance(mb, dict) else None
        if isinstance(sec_obj, dict) and (sec_obj.get("text") or sec_obj.get("marker")):
            lbl = str(sec_obj.get("text") or sec_obj.get("marker")).strip()
            return lbl, 0.94
        if total < 12:
            return ("Intro" if b_num == 1 else ("Chorus" if b_num == max(2, total // 2 + 1) else None)), 0.92
        b_verse = max(5, round(total * 0.14) + 1)
        b_chorus = max(b_verse + 4, round(total * 0.48) + 1)
        b_outro = max(b_chorus + 4, round(total * 0.80) + 1)
        if b_num == 1:
            return "Intro", 0.84
        if b_num < b_verse:
            return None, 0.84
        if b_num == b_verse:
            return "Verse", 0.90
        if b_num < b_chorus:
            return None, 0.90
        if b_num == b_chorus:
            return "Chorus", 1.04
        if b_num < b_outro:
            return None, 1.04
        if b_num == b_outro:
            return "Outro", 0.82
        return None, (0.78 if b_num == total else 0.82)

    for b_idx, b in enumerate(raw_bars, start=1):
        # Determine bar tick length (default 3840 = 4/4 -> 240 ticks/slot)
        bar_ticks = 3840
        if b_idx < len(masterbars):
            s_cur = int(masterbars[b_idx - 1].get("start") or 0)
            s_nxt = int(masterbars[b_idx].get("start") or (s_cur + 3840))
            if s_nxt > s_cur:
                bar_ticks = s_nxt - s_cur
        tick_per_slot = max(60.0, bar_ticks / 16.0)

        # Collect non-empty beats from primary voice (or merge secondary voice by slot)
        raw_beats: List[Dict[str, Any]] = []
        voices = b.get("voices") or []
        primary_beats = [bt for bt in (voices[0].get("beats") or []) if not bt.get("isempty")] if voices else []
        pending_grace: Dict[int, int] = {}

        for bt in primary_beats:
            if bt.get("gracetype"):
                for nt in bt.get("notes") or []:
                    at_s = int(nt.get("string") or 1)
                    real_s = n_strings + 1 - at_s
                    if 1 <= real_s <= 6:
                        gf = int(nt.get("fret") or 0)
                        if 1 <= gf <= 15:
                            pending_grace[real_s] = gf
                continue

            st_ticks = float(bt.get("displaystart") or 0)
            du_ticks = float(bt.get("displayduration") or bt.get("playbackduration") or 0)
            if du_ticks <= 0:
                continue
            slot = max(0, min(15, int(round(st_ticks / tick_per_slot))))
            dur = max(1, min(16 - slot, int(round(du_ticks / tick_per_slot))))

            ev_notes: List[Dict[str, Any]] = []
            seen_strings: Set[int] = set()
            for nt in bt.get("notes") or []:
                if nt.get("istiedestination"):
                    continue
                at_s = int(nt.get("string") or 1)
                real_s = n_strings + 1 - at_s
                if not (1 <= real_s <= 6) or real_s in seen_strings:
                    continue
                fret = int(nt.get("fret") or 0)
                if not is_std_tuning:
                    cm = tuning_by_real_s[real_s] + capo + fret
                    fret = cm - OPEN_TUNING_NO_CAPO[real_s] - capo
                if fret < 0:
                    fret = 0
                is_harm = bool(nt.get("harmonictype")) and fret == 12 and ("harmonic" in tier.allowed_techs)
                if not is_harm and fret > tier.max_fret:
                    # Relocate to another free string within tier.max_fret preserving concert pitch
                    cm_orig = OPEN_TUNING_NO_CAPO[real_s] + capo + fret
                    placed = False
                    for cand_s in range(1, 7):
                        if cand_s in seen_strings:
                            continue
                        cf = cm_orig - OPEN_TUNING_NO_CAPO[cand_s] - capo
                        if 0 <= cf <= tier.max_fret:
                            real_s, fret = cand_s, cf
                            placed = True
                            break
                    if not placed:
                        fret = min(fret, tier.max_fret)
                seen_strings.add(real_s)
                cm = OPEN_TUNING_NO_CAPO[real_s] + capo + fret
                if (cm % 12) not in ks.diatonic_pcs:
                    has_chromatic = True
                gf = pending_grace.pop(real_s, None)
                if gf is not None and (
                    "grace" not in tier.allowed_techs
                    or not (1 <= gf <= 15)
                    or not (1 <= abs(fret - gf) <= 3)
                ):
                    gf = None
                ev_notes.append({
                    "string": real_s,
                    "fret": fret,
                    "midi": cm,
                    "harmonic": is_harm,
                    "grace_from": gf,
                    "hp_origin": bool(nt.get("ishammerpullorigin")),
                    "slide_out": bool(nt.get("slideouttype")),
                })
            pending_grace.clear()

            # If beat only had tied destination notes, extend previous beat seamlessly
            if not ev_notes and bt.get("notes"):
                if raw_beats and not raw_beats[-1]["rest"] and (raw_beats[-1]["dur"] + dur) in VALID_DURS:
                    raw_beats[-1]["dur"] += dur
                    continue
                raw_beats.append({"slot": slot, "dur": dur, "rest": True, "notes": [], "brush": False})
                continue

            raw_beats.append({
                "slot": slot,
                "dur": dur,
                "rest": len(ev_notes) == 0,
                "notes": ev_notes,
                "brush": bool(bt.get("brushtype")),
            })

        # Normalize timeline to exact contiguous [0..16) with VALID_DURS
        raw_beats.sort(key=lambda x: x["slot"])
        norm_events: List[Dict[str, Any]] = []
        cur = 0
        for e in raw_beats:
            sl = e["slot"]
            du = e["dur"]
            if sl > cur:
                gap = sl - cur
                for chunk in decompose_span(gap, tier.allow_16th):
                    norm_events.append({"slot": cur, "dur": chunk, "rest": True, "notes": [], "brush": False})
                    cur += chunk
            elif sl < cur:
                trim = cur - sl
                du -= trim
                sl = cur
                if du <= 0:
                    continue
            if sl + du > 16:
                du = 16 - sl
            if du <= 0:
                continue
            chunks = decompose_span(du, tier.allow_16th)
            for ci, chunk in enumerate(chunks):
                if ci == 0:
                    norm_events.append({
                        "slot": cur,
                        "dur": chunk,
                        "rest": e["rest"],
                        "notes": e["notes"],
                        "brush": e["brush"],
                    })
                else:
                    norm_events.append({"slot": cur, "dur": chunk, "rest": True, "notes": [], "brush": False})
                cur += chunk
        if cur < 16:
            for chunk in decompose_span(16 - cur, tier.allow_16th):
                norm_events.append({"slot": cur, "dur": chunk, "rest": True, "notes": [], "brush": False})
                cur += chunk

        # Enforce tier span & max_simultaneous constraints cleanly
        for e in norm_events:
            if e["rest"] or not e["notes"]:
                continue
            e["notes"].sort(key=lambda x: x["midi"])
            if len(e["notes"]) > tier.max_simultaneous:
                # Keep lowest bass + highest melody + closest inner
                keep = [e["notes"][0], e["notes"][-1]] + e["notes"][1:-1][: max(0, tier.max_simultaneous - 2)]
                e["notes"] = sorted(keep, key=lambda x: x["midi"])
            # Trim extreme outlier inner notes if fretted span exceeds tier.max_span
            while True:
                fr = [x for x in e["notes"] if x["fret"] > 0 and not x["harmonic"]]
                if len(fr) < 2:
                    break
                fvals = [x["fret"] for x in fr]
                if max(fvals) - min(fvals) <= tier.max_span:
                    break
                # Drop middle/inner outlier furthest from melody fret
                mel_f = e["notes"][-1]["fret"]
                inner_cands = [x for x in fr if x is not e["notes"][-1]]
                if not inner_cands:
                    break
                worst = max(inner_cands, key=lambda x: abs(x["fret"] - mel_f))
                e["notes"].remove(worst)

        # Assign voice roles (melody / bass / inner) with guaranteed 0 voice crossings
        for idx_e, e in enumerate(norm_events):
            if e["rest"] or not e["notes"]:
                active_melody = None
                continue
            ns = sorted(e["notes"], key=lambda x: x["midi"])
            e["notes"] = ns
            if len(ns) == 1:
                nt = ns[0]
                is_link_src = nt["hp_origin"] or nt["slide_out"]
                is_low_bass = (
                    (not is_link_src)
                    and (
                        nt["string"] in (5, 6)
                        or (nt["string"] == 4 and active_melody is not None and nt["midi"] <= active_melody - 4)
                    )
                    and (active_melody is None or nt["midi"] <= active_melody)
                )
                if is_low_bass:
                    nt["role"] = "bass" if (nt["string"] in (5, 6) or e["slot"] in (0, 8)) else "inner"
                else:
                    nt["role"] = "melody"
                    active_melody = None if nt["harmonic"] else nt["midi"]
            else:
                top = ns[-1]
                top["role"] = "melody"
                active_melody = None if top["harmonic"] else top["midi"]
                low = ns[0]
                if low["string"] in (4, 5, 6):
                    low["role"] = "bass"
                else:
                    low["role"] = "inner"
                for mid in ns[1:-1]:
                    mid["role"] = "inner"

            # Attach link_tech (H/P/S) only when next event in same bar has valid same-string target
            e["link_tech"] = None
            if idx_e + 1 < len(norm_events):
                nxt = norm_events[idx_e + 1]
                if not nxt["rest"] and nxt["notes"]:
                    mel_cur = [x for x in e["notes"] if x.get("role") == "melody"]
                    if mel_cur:
                        mc = mel_cur[0]
                        nxt_same = [x for x in nxt["notes"] if x["string"] == mc["string"]]
                        if nxt_same:
                            df = nxt_same[0]["fret"] - mc["fret"]
                            if mc["hp_origin"] and 1 <= df <= 3 and "H" in tier.allowed_techs:
                                e["link_tech"] = "H"
                            elif mc["hp_origin"] and -3 <= df <= -1 and "P" in tier.allowed_techs:
                                e["link_tech"] = "P"
                            elif mc["slide_out"] and df != 0 and "S" in tier.allowed_techs:
                                e["link_tech"] = "S"

            # Set arpeggio on brushes or rich downbeat chords
            e["arpeggio"] = False
            if "arpeggio" in tier.allowed_techs and len(e["notes"]) >= 2:
                if e["brush"] or (len(e["notes"]) >= 3 and e["slot"] in (0, 8)):
                    e["arpeggio"] = True
                    arpeggio_emitted = True

        # Infer real diatonic chords for first half (0..7) and second half (8..15)
        def _half_chord(ev_slice: List[Dict[str, Any]]) -> str:
            pcs_hist: Dict[int, float] = {}
            for e in ev_slice:
                if e["rest"]:
                    continue
                for nt in e["notes"]:
                    pc = nt["midi"] % 12
                    w = 2.5 if nt.get("role") == "bass" else 1.0
                    pcs_hist[pc] = pcs_hist.get(pc, 0.0) + w * e["dur"]
            if not pcs_hist:
                return scale_chords[0]
            return infer_chord_for_notes(pcs_hist, capo, scale_chords)

        h1 = [e for e in norm_events if e["slot"] < 8]
        h2 = [e for e in norm_events if e["slot"] >= 8]
        c1 = _half_chord(h1)
        c2 = _half_chord(h2)
        bar_chords = [(0, c1)] if (not h2 or c2 == c1) else [(0, c1), (8, c2)]

        sec_lbl, dyn = _section_for_bar(b_idx, n_bars)
        mel_mids_bar = [
            nt["midi"] for e in norm_events if not e["rest"] for nt in e["notes"]
            if nt.get("role") == "melody" and not nt.get("harmonic")
        ]
        if mel_mids_bar and (b_idx in (1, n_bars) or b_idx % max(4, n_bars // 6) == 1):
            landmarks[b_idx] = mel_mids_bar

        chords_repr = ", ".join(f'({sl}, "{lbl}")' for sl, lbl in bar_chords)
        bars_code.append(f"    # M{b_idx:02d}: " + " -> ".join(lbl for _, lbl in bar_chords))
        bars_code.append("    m.append(Measure(")
        bars_code.append(f"        bar={b_idx},")
        if sec_lbl:
            bars_code.append(f'        section="{sec_lbl}",')
        bars_code.append(f"        dynamic={dyn:.2f},")
        bars_code.append(f"        chords=[{chords_repr}],")
        bars_code.append("        events=[")
        for idx_e, e in enumerate(norm_events):
            if e["rest"] or not e["notes"]:
                bars_code.append(f'            ev({e["slot"]}, {e["dur"]}, rest=True),')
                continue
            ncalls = []
            for nt in e["notes"]:
                kw_parts = []
                if nt["harmonic"]:
                    kw_parts.append("harmonic=True")
                if nt["grace_from"] is not None:
                    kw_parts.append(f"grace_from={nt['grace_from']}")
                kw = (", " + ", ".join(kw_parts)) if kw_parts else ""
                ncalls.append(f'n({nt["string"]}, {nt["fret"]}, "{nt["role"]}"{kw})')
            extras = []
            if e["arpeggio"]:
                extras.append("arpeggio=True")
            if e["link_tech"]:
                extras.append(f'link_tech="{e["link_tech"]}"')
            if b_idx == n_bars and idx_e == len(norm_events) - 1:
                extras.append("fermata=True")
            ex_str = (", " + ", ".join(extras)) if extras else ""
            bars_code.append(f'            ev({e["slot"]}, {e["dur"]}, {", ".join(ncalls)}{ex_str}),')
        bars_code.append("        ],")
        bars_code.append("    ))")
        bars_code.append("")

    diatonic_sorted = sorted(ks.diatonic_pcs)
    req_line = "REQUIRED_TECHS = ()" if (tier.key == "advanced" or not arpeggio_emitted) else ""
    src = f'''"""Single source of truth for 《{title}》 fingerstyle guitar TAB & audio.

Imported directly from AlphaTab/Guitar-Pro JSON (real string/fret & techniques preserved).
Concert Key: {ks.name} (1 = {ks.relative_major_name})
Capo: {capo} ({fing_name}调指法)
Time Signature: 4/4 (16 sixteenth-note slots per measure)
Tempo: {bpm} BPM
Difficulty Tier: {tier.key} ({tier.label_zh})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TIER = "{tier.key}"
ALLOW_CHROMATIC = {has_chromatic}
{req_line}
TITLE = "{title}"
FILE_PREFIX = "{file_prefix}"
SUBTITLE = "指弹吉他独奏六线谱"
ARTIST = "{artist}"
COMPOSER = "{raw_composer}"
ARRANGER_INFO = "原调：{ks.name} (1={ks.relative_major_name})  |  选调：{fing_name}调指法 (Capo = {capo}品)  |  标准调弦：6E 5A 4D 3G 2B 1E"
CHORD_LEGEND = "和弦对照 (Capo {capo})：指法={fing_name}调  常用={'  '.join(scale_chords)}"
TECHNIQUE_LEGEND_1 = "技巧图例：H = 击弦   P = 勾弦   S = 滑音   波浪线 = 琶音"
TECHNIQUE_LEGEND_2 = "<12> = 自然泛音   P.M. = 右手闷音   X = 拍弦"

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

LANDMARKS: Dict[int, List[int]] = {landmarks}
PHRASE_CUES: Dict[Tuple[int, int], str] = {{}}


@dataclass(frozen=True)
class NoteEvent:
    string: int
    fret: int
    role: str = "melody"
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
    """Return AlphaTab-imported fingerstyle measures (M01..M{n_bars:02d})."""
    m: List[Measure] = []
{chr(10).join(bars_code)}
    return m
'''
    meta = {
        "key": ks.name,
        "capo": capo,
        "fing_key": fing_name,
        "bpm": bpm,
        "bars": n_bars,
        "has_chromatic": has_chromatic,
    }
    return src, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Import MIDI (.mid), MusicXML (.xml/.musicxml/.mxl), or AlphaTab JSON (.json) into src/score_data.py.")
    parser.add_argument("input_file", help="Path to .mid, .musicxml, .xml, .mxl, or AlphaTab .json file")
    parser.add_argument("--out", required=True, help="Output path for src/score_data.py (or project dir)")
    parser.add_argument("--tier", default="standard", choices=("beginner", "standard", "advanced"))
    parser.add_argument("--title", default=None, help="Override song title")
    parser.add_argument("--composer", default=None, help="Override composer")
    parser.add_argument("--artist", default=None, help="Performer/Artist folder name for library (defaults to composer)")
    parser.add_argument("--key", default="auto", help="Target key or 'auto'")
    parser.add_argument("--capo", default="auto", help="Capo fret (int) or 'auto'")
    parser.add_argument("--bpm", default="auto", help="BPM (int) or 'auto'")
    parser.add_argument("--melody-track", type=int, default=None, help="Explicit track/part index for melody")
    parser.add_argument("--bass-track", type=int, default=None, help="Explicit track/part index for bass")
    parser.add_argument("--max-bars", type=int, default=None, help="Trim imported bars to at most N bars")
    args = parser.parse_args()

    in_path = Path(args.input_file).expanduser().resolve()
    if not in_path.is_file():
        raise FileNotFoundError(f"Input score not found: {in_path}")

    out_arg = Path(args.out).expanduser().resolve()
    if out_arg.suffix == ".py":
        out_py = out_arg
        proj_dir = out_py.parent.parent if out_py.parent.name == "src" else out_py.parent
    else:
        proj_dir = out_arg
        out_py = proj_dir / "src" / "score_data.py"
    out_py.parent.mkdir(parents=True, exist_ok=True)

    ext = in_path.suffix.lower()
    if ext == ".json":
        src, meta = build_score_source_from_alphatab_json(
            in_path,
            title_override=args.title,
            composer_override=args.composer,
            artist_override=args.artist,
            tier_key=args.tier,
            key_arg=args.key,
            capo_arg=args.capo,
            bpm_arg=args.bpm,
            track_idx=args.melody_track,
            max_bars=args.max_bars,
        )
    elif ext in (".mid", ".midi", ".xml", ".musicxml", ".mxl"):
        parsed = parse_midi_file(in_path) if ext in (".mid", ".midi") else parse_musicxml_file(in_path)
        title = args.title or parsed.get("title_hint") or in_path.stem.replace("_", " ").strip()
        composer = args.composer or parsed.get("composer_hint") or "Unknown"
        artist = args.artist or composer
        src, meta = build_score_source_from_import(
            parsed,
            title=str(title),
            composer=str(composer),
            artist=str(artist),
            tier_key=args.tier,
            key_arg=args.key,
            capo_arg=args.capo,
            bpm_arg=args.bpm,
            melody_track=args.melody_track,
            bass_track=args.bass_track,
            max_bars=args.max_bars,
        )
    else:
        raise ValueError(f"Unsupported file type '{ext}'. Expected .mid, .midi, .musicxml, .xml, .mxl, or AlphaTab .json.")

    out_py.write_text(src, encoding="utf-8")

    # Immediately verify with validate_project_score
    mod = load_score_module(proj_dir)
    errors, tech_counts, summary = validate_project_score(mod)
    if errors:
        print(f"[WARN] Imported draft has {len(errors)} validation issue(s):")
        for e in errors[:10]:
            print("  -", e)
        return 1

    print(
        f"[IMPORT OK] {out_py.name} <- {in_path.name} | tier={args.tier}, key={meta['key']}, "
        f"capo={meta['capo']} ({meta['fing_key']}调指法), bpm={meta['bpm']}, bars={meta['bars']}, "
        f"score={summary.get('quality_score', '-')}分, Lv.{summary.get('difficulty', '-')}, "
        f"chromatic={meta['has_chromatic']}, techniques={tech_counts}"
    )
    print(f"Next: run `python3 {SCRIPT_DIR / 'tab_engine.py'} build {proj_dir}`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
