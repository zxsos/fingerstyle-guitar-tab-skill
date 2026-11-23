#!/usr/bin/env python3
"""Deterministic Fingerstyle Guitar Arranger Engine (Viterbi Voicer + Style Arpeggiator).

Turns a compact declarative `lead_sheet.json` (chords + jianpu/MIDI melody + section styles)
into a physically optimal, musical fingerstyle guitar arrangement, and exports to:
  1. `<proj>/src/score_data.py`  (compatible with `tab_engine.py check/build`)
  2. `<proj>/<prefix>.atex`      (native AlphaTex for CoderLine/alphaTab player)
  3. `<proj>/<prefix>.musicxml`  (standard MusicXML 4.0 TAB for alphaTab/GP/MuseScore)

Subcommands:
  arrange      <lead_sheet.json> --out-dir <proj> [--tier standard] [--formats score,atex,musicxml]
  parse-jianpu <jianpu.txt>      --out <lead_sheet.json>
  export       <proj>            [--formats atex,musicxml]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from fret_helper import FINGERED_KEY_SCORE, _default_base_tonic_midi, parse_jianpu_token
from tab_common import (
    CHORD_QUALITIES,
    CHORD_RE,
    NOTE_NAMES,
    NOTE_NAMES_FLAT,
    OPEN_TUNING_NO_CAPO,
    PC_BY_TOKEN,
    VALID_DURS,
    BeatEvent,
    Measure,
    NoteEvent,
    TierProfile,
    chord_pitch_classes,
    chord_root_concert_pc,
    diatonic_chords_for_major_pc,
    finger_pos_cm,
    get_tier_profile,
    key_name_from_major_pc,
    load_score_module,
    midi_to_name,
    note_concert_midi,
    parse_key_spec,
    safe_file_prefix,
    validate_project_score,
)


# =============================================================================
# 1. Key / Capo / Chord Normalization (Supports Both Concert & Fingered Chords)
# =============================================================================

def choose_optimal_capo(major_tonic_pc: int, tier_key: str) -> int:
    best_score = -1e9
    best_capo = 0
    max_capo = 6 if tier_key == "beginner" else 7
    for capo in range(0, max_capo + 1):
        fing_pc = (major_tonic_pc - capo) % 12
        base_score, _note = FINGERED_KEY_SCORE.get(fing_pc, (20, ""))
        pen = capo * (2.0 if tier_key == "beginner" else 1.2) + (8.0 if capo > 5 else 0.0)
        sc = base_score - pen
        if sc > best_score:
            best_score = sc
            best_capo = capo
    return best_capo


def transpose_chord_label(label: str, semitones: int, prefer_flat: bool = False) -> str:
    """Transpose a chord label (including slash bass) by `semitones` (e.g. -capo)."""
    clean = label.strip()
    m = CHORD_RE.match(clean)
    if not m or semitones % 12 == 0:
        return clean
    root, qual, bass = m.group(1), m.group(2), m.group(3)
    names = NOTE_NAMES_FLAT if prefer_flat else NOTE_NAMES
    r_pc = PC_BY_TOKEN.get(root.upper())
    if r_pc is None:
        return clean
    new_root = names[(r_pc + semitones) % 12]
    if bass:
        b_pc = PC_BY_TOKEN.get(bass.upper())
        new_bass = names[(b_pc + semitones) % 12] if b_pc is not None else bass
        return f"{new_root}{qual}/{new_bass}"
    return f"{new_root}{qual}"


# =============================================================================
# 2. Lead Sheet Representation & Parser
# =============================================================================

@dataclass
class LeadMelodyNote:
    bar: int
    slot: int
    midi: int
    dur: Optional[int] = None  # Explicit duration in 16th slots (optional)
    lyric: Optional[str] = None
    tag: str = ""  # "" | "grace" | "harm" | "fermata"


@dataclass
class LeadBar:
    bar: int
    section: Optional[str]
    style: str
    dynamic: float
    chords: List[Tuple[int, str]]
    melody: List[LeadMelodyNote]
    is_pickup: bool = False


STYLE_DEFAULT_DYNAMICS: Dict[str, float] = {
    "intro_arp": 0.85,
    "verse_breathing": 0.90,
    "chorus_open": 0.95,
    "interlude_pm": 0.89,
    "interlude_open": 0.92,
    "chorus_slap": 0.98,
    "outro_calm": 0.86,
}


def load_lead_sheet(data: Dict[str, Any], tier_override: Optional[str] = None) -> Dict[str, Any]:
    """Normalize a declarative `lead_sheet.json` dict into internal structures."""
    tier_key = (tier_override or data.get("tier") or "standard").strip().lower()
    tier = get_tier_profile(tier_key)
    key_str = str(data.get("key") or "C").strip()
    ks = parse_key_spec(key_str)
    bpm = int(data.get("bpm") or 80)

    capo_raw = data.get("capo", "auto")
    if capo_raw is None or str(capo_raw).strip().lower() == "auto":
        capo = choose_optimal_capo(ks.major_tonic_pc, tier.key)
    else:
        capo = int(capo_raw)

    fing_pc = (ks.major_tonic_pc - capo) % 12
    prefer_flat = fing_pc in {1, 3, 5, 8, 10}
    chords_mode = str(data.get("chords_mode") or "fingered").strip().lower()

    base_tonic = _default_base_tonic_midi(
        ks,
        octave_c=data.get("octave_c"),
        octave_shift=int(data.get("octave_shift") or 0),
    )

    bars: List[LeadBar] = []
    auto_bar_idx = 1

    sections_raw = data.get("sections") or []
    if not sections_raw and data.get("bars"):
        sections_raw = [{"name": "Main", "style": "verse_breathing", "bars": data["bars"]}]

    for sec in sections_raw:
        sec_name = str(sec.get("name") or "").strip() or None
        sec_style = str(sec.get("style") or "verse_breathing").strip().lower()
        sec_dyn = float(sec.get("dynamic") or STYLE_DEFAULT_DYNAMICS.get(sec_style, 0.92))
        for b_i, b_obj in enumerate(sec.get("bars") or []):
            is_pickup = bool(b_obj.get("is_pickup", False))
            bar_num = int(b_obj["bar"]) if "bar" in b_obj else (0 if is_pickup else auto_bar_idx)
            if not is_pickup:
                auto_bar_idx = bar_num + 1
            b_style = str(b_obj.get("style") or sec_style).strip().lower()
            b_dyn = float(b_obj.get("dynamic") or sec_dyn)
            b_sec = sec_name if b_i == 0 else None

            chords: List[Tuple[int, str]] = []
            for item in b_obj.get("chords") or []:
                sl, lbl = int(item[0]), str(item[1]).strip()
                if chords_mode == "concert" and capo != 0:
                    lbl = transpose_chord_label(lbl, -capo, prefer_flat=prefer_flat)
                chords.append((sl, lbl))
            chords.sort(key=lambda x: x[0])

            mel_notes: List[LeadMelodyNote] = []
            for m_item in b_obj.get("melody") or []:
                if isinstance(m_item, dict):
                    sl = int(m_item["slot"])
                    raw_p = m_item.get("midi") if "midi" in m_item else m_item.get("note")
                    du = int(m_item["dur"]) if m_item.get("dur") is not None else None
                    lyr = m_item.get("lyric") or None
                    tag = str(m_item.get("tag") or "").strip().lower()
                else:
                    sl = int(m_item[0])
                    raw_p = m_item[1]
                    du = int(m_item[2]) if len(m_item) > 2 and m_item[2] is not None else None
                    lyr = (str(m_item[3]).strip() or None) if len(m_item) > 3 and m_item[3] else None
                    tag = str(m_item[4]).strip().lower() if len(m_item) > 4 and m_item[4] else ""

                if isinstance(raw_p, int):
                    midi_val = raw_p
                else:
                    tok_str = str(raw_p).strip()
                    if tok_str.isdigit() and int(tok_str) >= 36:
                        midi_val = int(tok_str)
                    else:
                        midi_val, _ = parse_jianpu_token(tok_str, base_tonic)

                # Beginner tier folds 16th-note subdivisions cleanly onto 8th-note grid
                if not tier.allow_16th:
                    if sl % 2 == 1:
                        sl = max(0, sl - 1)
                    if du == 1:
                        du = 2

                mel_notes.append(LeadMelodyNote(bar=bar_num, slot=sl, midi=midi_val, dur=du, lyric=lyr, tag=tag))

            # Deduplicate identical slots if beginner quantization collided
            dedup_mel: List[LeadMelodyNote] = []
            seen_sl: Set[int] = set()
            for mn in sorted(mel_notes, key=lambda x: x.slot):
                if mn.slot not in seen_sl:
                    dedup_mel.append(mn)
                    seen_sl.add(mn.slot)

            bars.append(
                LeadBar(
                    bar=bar_num,
                    section=b_sec,
                    style=b_style,
                    dynamic=b_dyn,
                    chords=chords,
                    melody=dedup_mel,
                    is_pickup=is_pickup,
                )
            )

    return {
        "title": str(data.get("title") or "Untitled").strip(),
        "artist": str(data.get("artist") or data.get("composer") or "Unknown").strip(),
        "composer": str(data.get("composer") or data.get("artist") or "Unknown").strip(),
        "subtitle": str(data.get("subtitle") or "指弹吉他独奏六线谱").strip(),
        "file_prefix": str(data.get("file_prefix") or "").strip(),
        "key_spec": ks,
        "capo": capo,
        "fing_name": key_name_from_major_pc(fing_pc),
        "fing_pc": fing_pc,
        "bpm": bpm,
        "tier": tier,
        "base_tonic": base_tonic,
        "bars": bars,
    }


# =============================================================================
# 3. Fretboard Physics & Global Viterbi Melody Voicer
# =============================================================================

def _chord_at_slot(chords: Sequence[Tuple[int, str]], slot: int) -> str:
    cur = chords[0][1] if chords else ""
    for cs, cl in chords:
        if cs <= slot:
            cur = cl
    return cur


def _find_root_bass_candidates(
    chord_lbl: str,
    capo: int,
    max_midi: int,
    tier: TierProfile,
    forbidden_strings: Set[int],
    anchor_frets: List[int],
) -> List[Tuple[float, int, int, int]]:
    """Return sorted (cost, string, fret, concert_midi) for playable chord bass notes."""
    if not chord_lbl:
        return []
    ch_pcs = chord_pitch_classes(chord_lbl, capo)
    root_pc = chord_root_concert_pc(chord_lbl, capo)
    if not ch_pcs:
        return []

    out: List[Tuple[float, int, int, int]] = []
    for s in (6, 5, 4):
        if s in forbidden_strings:
            continue
        for f in range(0, tier.max_fret + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            pc = cm % 12
            if pc not in ch_pcs:
                continue
            is_root = (root_pc is not None and pc == root_pc)
            test_f = anchor_frets + ([f] if f > 0 else [])
            if len(test_f) >= 2:
                if (max(test_f) - min(test_f)) > tier.max_span:
                    continue
                span_cm = finger_pos_cm(capo + max(test_f)) - finger_pos_cm(capo + min(test_f))
                if span_cm > 13.5:
                    continue
            # Strongly favor true root & open/low frets (high-pos root smoothly available when melody is high!)
            root_cost = 0.0 if is_root else 25.0
            open_bonus = -1.2 if f == 0 else 0.0
            fret_cost = f * 0.45 + (3.5 if f > 5 else 0.0)
            # Slight preference for deeper bass strings when same fret tier
            str_cost = (6 - s) * 0.15
            out.append((root_cost + fret_cost + open_bonus + str_cost, s, f, cm))
    out.sort()
    return out


def _melody_candidates(
    mn: LeadMelodyNote,
    capo: int,
    tier: TierProfile,
    chord_lbl: str,
    is_chord_change: bool,
) -> List[Tuple[float, int, int, bool]]:
    """Enumerate valid (emission_cost, string, fret, is_harmonic) for a melody note."""
    if mn.tag == "harm" and "harmonic" in tier.allowed_techs:
        # Natural 12th-fret harmonic matching exact concert pitch
        cands: List[Tuple[float, int, int, bool]] = []
        for s in (1, 2, 3, 4, 5, 6):
            if OPEN_TUNING_NO_CAPO[s] + capo + 12 == mn.midi:
                cands.append((0.0, s, 12, True))
        if cands:
            return cands

    cands = []
    for s in (1, 2, 3, 4, 5, 6):
        f = mn.midi - OPEN_TUNING_NO_CAPO[s] - capo
        if not (0 <= f <= tier.max_fret):
            continue
        # Open string & treble string (1..3) preference
        open_bonus = -2.4 if f == 0 else 0.0
        if s <= 3:
            str_pen = (s - 1) * 0.25
        elif s == 4:
            str_pen = 1.6
        else:
            str_pen = 14.0  # Strongly avoid melody on 5/6 bass strings
        fret_pen = f * (0.95 if tier.key == "beginner" else 0.55)
        if f >= 5 and mn.midi < 71:
            fret_pen += 2.2  # Don't climb up the neck for mid-register notes

        # Check chord-root bass compatibility on chord-change beats!
        bass_pen = 0.0
        if is_chord_change and chord_lbl:
            b_cands = _find_root_bass_candidates(
                chord_lbl, capo, mn.midi, tier, {s}, [f] if f > 0 else []
            )
            if not b_cands:
                bass_pen = 30.0
            elif b_cands[0][0] >= 20.0:
                bass_pen = 15.0  # Non-root fallback only
            else:
                # Slight cost if forced into high-position bass when a low-position option exists
                bass_pen = min(4.0, b_cands[0][0] * 0.25)

        cands.append((open_bonus + str_pen + fret_pen + bass_pen, s, f, False))

    if not cands:
        # Octave-fold into playable treble range if input melody exceeds guitar range
        lo = OPEN_TUNING_NO_CAPO[4] + capo
        hi = OPEN_TUNING_NO_CAPO[1] + capo + tier.max_fret
        folded = mn.midi
        while folded < lo:
            folded += 12
        while folded > hi:
            folded -= 12
        for s in (1, 2, 3, 4):
            f = folded - OPEN_TUNING_NO_CAPO[s] - capo
            if 0 <= f <= tier.max_fret:
                cands.append((f * 0.6 + (s - 1) * 0.3, s, f, False))
    cands.sort()
    return cands[:5]


def run_viterbi_melody_voicer(
    bars: List[LeadBar], capo: int, tier: TierProfile
) -> Dict[Tuple[int, int], Tuple[int, int, bool]]:
    """Globally optimal left-hand fretboard path for every melody note via Viterbi DP."""
    flat: List[Tuple[LeadBar, LeadMelodyNote, int]] = []
    for b in bars:
        for mn in b.melody:
            g_slot = b.bar * 16 + mn.slot
            flat.append((b, mn, g_slot))

    if not flat:
        return {}

    states_per_step: List[List[Tuple[float, int, int, bool]]] = []
    for b, mn, _gs in flat:
        ch_slots = {cs for cs, _ in b.chords}
        ch_lbl = _chord_at_slot(b.chords, mn.slot)
        states_per_step.append(_melody_candidates(mn, capo, tier, ch_lbl, mn.slot in ch_slots))

    n_steps = len(flat)
    dp: List[List[float]] = [[1e18] * len(st) for st in states_per_step]
    parent: List[List[int]] = [[-1] * len(st) for st in states_per_step]

    for j, (emit_c, _s, _f, _h) in enumerate(states_per_step[0]):
        dp[0][j] = emit_c

    for i in range(1, n_steps):
        _b_prev, mn_prev, gs_prev = flat[i - 1]
        _b_cur, mn_cur, gs_cur = flat[i]
        dt_slots = max(1, gs_cur - gs_prev)
        same_pitch = (mn_prev.midi == mn_cur.midi and mn_prev.tag != "harm" and mn_cur.tag != "harm")
        speed_weight = 1.6 if dt_slots <= 2 else (1.0 if dt_slots <= 4 else 0.45)

        for j, (emit_c, s2, f2, h2) in enumerate(states_per_step[i]):
            best_val = 1e18
            best_k = 0
            for k, (_ec1, s1, f1, h1) in enumerate(states_per_step[i - 1]):
                trans = 0.0
                if same_pitch and dt_slots <= 8:
                    # Repeated pitch strongly prefers staying on the exact same string/fret!
                    if (s1, f1) != (s2, f2):
                        trans += 6.5
                if not h1 and not h2:
                    if f1 > 0 and f2 > 0:
                        fjump = abs(f2 - f1)
                        dist_cm = abs(finger_pos_cm(capo + f2) - finger_pos_cm(capo + f1))
                        trans += (fjump * 0.42 + max(0.0, dist_cm - 7.0) * 0.35) * speed_weight
                        if fjump >= 5 and dt_slots <= 2:
                            trans += 8.0
                    # Small bonus for smooth same-string stepwise H/P legato opportunities
                    if s1 == s2 and 1 <= abs(f2 - f1) <= 3 and dt_slots <= 2 and f1 <= 5 and f2 <= 5:
                        trans -= 0.55
                cand_val = dp[i - 1][k] + trans + emit_c
                if cand_val < best_val:
                    best_val = cand_val
                    best_k = k
            dp[i][j] = best_val
            parent[i][j] = best_k

    # Backtrace optimal path
    best_last = min(range(len(dp[-1])), key=lambda j: dp[-1][j])
    chosen: List[Tuple[int, int, bool]] = [(0, 0, False)] * n_steps
    idx = best_last
    for i in range(n_steps - 1, -1, -1):
        _ec, s, f, h = states_per_step[i][idx]
        chosen[i] = (s, f, h)
        idx = parent[i][idx]

    return {(flat[i][1].bar, flat[i][1].slot): chosen[i] for i in range(n_steps)}


# =============================================================================
# 4. Safe Inner Voicing, Closing Chord & Rhythm Decomposer
# =============================================================================

def _find_safe_inners(
    chord_lbl: str,
    capo: int,
    max_midi: int,
    tier: TierProfile,
    forbidden_strings: Set[int],
    anchor_frets: List[int],
    root_bass_midi: Optional[int] = None,
) -> List[Tuple[int, int, int]]:
    """Return ordered list of safe (string, fret, concert_midi) inner notes on strings 2..5 (NEVER string 6)."""
    if not chord_lbl:
        return []
    ch_pcs = chord_pitch_classes(chord_lbl, capo)
    if not ch_pcs:
        return []
    max_f = min(tier.max_fret, 4 if tier.key == "beginner" else 5)
    cands: List[Tuple[int, int, int, int, int]] = []
    for s in (3, 4, 2, 5):
        if s in forbidden_strings:
            continue
        for f in range(0, max_f + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > max_midi:
                continue
            if (cm % 12) not in ch_pcs:
                continue
            test_f = anchor_frets + ([f] if f > 0 else [])
            if len(test_f) >= 2:
                if (max(test_f) - min(test_f)) > tier.max_span:
                    continue
                if finger_pos_cm(capo + max(test_f)) - finger_pos_cm(capo + min(test_f)) > 13.0:
                    continue
            below_bass = 1 if (root_bass_midi is not None and cm <= root_bass_midi) else 0
            # Prefer mid strings (3, 4), open strings, low frets, and above bass
            str_pref = {3: 0, 4: 1, 5: 2, 2: 3}[s]
            cands.append((below_bass, 0 if f == 0 else 1, f, str_pref, cm))
    cands.sort()
    # Deduplicate by (s, f)
    out: List[Tuple[int, int, int]] = []
    seen: Set[Tuple[int, int]] = set()
    for _bb, _op, f, sp, cm in cands:
        s = {0: 3, 1: 4, 2: 5, 3: 2}[sp]
        if (s, f) not in seen:
            seen.add((s, f))
            out.append((s, f, cm))
    return out


def _decompose_legal_gap(span: int, allow_16th: bool) -> List[int]:
    """Split a gap in 16th-note slots into an 8th-note groove with legal VALID_DURS."""
    if span <= 0:
        return []
    if span in (1, 2, 3):
        return [span] if (allow_16th or span != 1) else [2]
    out: List[int] = []
    rem = span
    while rem > 0:
        if rem == 3:
            out.append(3)
            rem = 0
        elif rem == 1:
            if out and out[-1] == 2:
                out[-1] = 3
            else:
                out.append(1 if allow_16th else 2)
            rem = 0
        else:
            out.append(2)
            rem -= 2
    return out


def _build_closing_chord_notes(
    chord_lbl: str,
    capo: int,
    mel_s: int,
    mel_f: int,
    tier: TierProfile,
) -> Tuple[NoteEvent, ...]:
    """Build a full idiomatic open/low-position closing chord ending with melody on top."""
    ch_pcs = chord_pitch_classes(chord_lbl, capo)
    mel_m = OPEN_TUNING_NO_CAPO[mel_s] + capo + mel_f
    b_cands = _find_root_bass_candidates(chord_lbl, capo, mel_m, tier, {mel_s}, [mel_f] if mel_f > 0 else [])
    if not b_cands:
        return (NoteEvent(mel_s, mel_f, "melody"),)
    _, bs, bf, _bm = b_cands[0]
    notes: List[NoteEvent] = [NoteEvent(bs, bf, "bass")]
    used_s: Set[int] = {bs, mel_s}
    used_f: List[int] = [f for f in (bf, mel_f) if f > 0]
    max_inner = max(0, tier.max_simultaneous - 2)

    # Fill strings between bass and melody cleanly
    inner_added = 0
    for s in range(bs - 1, mel_s, -1):
        if inner_added >= max_inner:
            break
        if s in used_s:
            continue
        opts: List[Tuple[int, int, int]] = []
        for f in range(0, min(tier.max_fret, 4) + 1):
            cm = OPEN_TUNING_NO_CAPO[s] + capo + f
            if cm > mel_m or (ch_pcs and (cm % 12) not in ch_pcs):
                continue
            tf = used_f + ([f] if f > 0 else [])
            if tf and (max(tf) - min(tf)) > tier.max_span:
                continue
            # Check finger count <= 4 (with barre support)
            uniq_f = sorted({(nt.string, nt.fret) for nt in notes if nt.fret > 0} | ({(mel_s, mel_f)} if mel_f > 0 else set()) | ({(s, f)} if f > 0 else set()))
            if uniq_f:
                min_fu = min(fv for _, fv in uniq_f)
                at_min = [st for st, fv in uniq_f if fv == min_fu]
                fingers = (len(uniq_f) - len(at_min) + 1) if len(at_min) >= 2 else len(uniq_f)
                if fingers > 4:
                    continue
            opts.append((0 if f == 0 else 1, f, cm))
        opts.sort()
        if opts:
            _, cf, _ = opts[0]
            notes.append(NoteEvent(s, cf, "inner"))
            used_s.add(s)
            if cf > 0:
                used_f.append(cf)
            inner_added += 1

    notes.append(NoteEvent(mel_s, mel_f, "melody"))
    return tuple(notes)


# =============================================================================
# 5. Complete Arrangement Pipeline (Lead Sheet -> Verified List[Measure])
# =============================================================================

def arrange_lead_sheet(parsed: Dict[str, Any]) -> Tuple[List[Measure], Dict[int, List[int]]]:
    """Deterministically arrange a normalized lead sheet into fingerstyle Measures."""
    bars: List[LeadBar] = parsed["bars"]
    capo: int = parsed["capo"]
    tier: TierProfile = parsed["tier"]
    diatonic_pcs: Set[int] = parsed["key_spec"].diatonic_pcs

    voicing = run_viterbi_melody_voicer(bars, capo, tier)
    measures: List[Measure] = []
    landmarks: Dict[int, List[int]] = {}

    active_melody_midi: Optional[int] = None
    last_bar_num = bars[-1].bar if bars else 1

    # Count total full bars to pace arpeggio restraint cleanly (target 25%..42%)
    total_full_bars = sum(1 for b in bars if not b.is_pickup)
    arp_budget = max(4, int(total_full_bars * 0.45))
    arp_used = 0

    for b_idx, lb in enumerate(bars):
        is_last_bar = (lb.bar == last_bar_num)
        is_pm_style = (lb.style == "interlude_pm") and ("palm_mute" in tier.allowed_techs)
        is_slap_style = (lb.style == "chorus_slap") and ("slap" in tier.allowed_techs)
        ch_slots = {cs for cs, _ in lb.chords}

        # Special handling: Pickup bar (M00)
        if lb.is_pickup:
            start_s = lb.melody[0].slot if lb.melody else 12
            if start_s not in (4, 8, 12):
                start_s = 12
            evs: List[BeatEvent] = []
            for m_i, mn in enumerate(lb.melody):
                ms, mf, mh = voicing[(lb.bar, mn.slot)]
                nxt_s = lb.melody[m_i + 1].slot if m_i + 1 < len(lb.melody) else 16
                du = mn.dur if (mn.dur in VALID_DURS and mn.slot + mn.dur <= nxt_s) else (nxt_s - mn.slot)
                if du not in VALID_DURS:
                    du = _decompose_legal_gap(nxt_s - mn.slot, tier.allow_16th)[0]
                evs.append(BeatEvent(slot=mn.slot, dur=du, notes=(NoteEvent(ms, mf, "melody", harmonic=mh),), lyric=mn.lyric))
                active_melody_midi = None if mh else (OPEN_TUNING_NO_CAPO[ms] + capo + mf)
            measures.append(Measure(bar=lb.bar, events=evs, chords=lb.chords, section=lb.section, is_pickup=True, dynamic=lb.dynamic))
            continue

        # Special handling: Full-bar closing chord (fermata whole-note ending)
        if is_last_bar and len(lb.melody) == 1 and lb.melody[0].slot == 0 and (
            lb.melody[0].dur == 16 or lb.melody[0].tag == "fermata"
        ):
            mn = lb.melody[0]
            ms, mf, _mh = voicing[(lb.bar, mn.slot)]
            ch0 = _chord_at_slot(lb.chords, 0)
            closing_notes = _build_closing_chord_notes(ch0, capo, ms, mf, tier)
            ev0 = BeatEvent(
                slot=0,
                dur=16,
                notes=closing_notes,
                lyric=mn.lyric,
                fermata=True,
                arpeggio=(len(closing_notes) >= 2 and "arpeggio" in tier.allowed_techs),
            )
            measures.append(Measure(bar=lb.bar, events=[ev0], chords=lb.chords, section=lb.section, dynamic=lb.dynamic))
            continue

        # Build event timeline covering slots 0..15 with steady 8th-note flow
        mel_by_slot: Dict[int, LeadMelodyNote] = {mn.slot: mn for mn in lb.melody}

        # Determine exact slot boundaries where attacks occur (melody attacks + chord changes + 8th-note inner fills)
        # First lock exact spans of explicit-dur melody notes (like dur=3 dotted-8th followed by dur=1 16th)
        fixed_slots: Set[int] = set(mel_by_slot.keys()) | ch_slots | {0}
        # Reserve explicit melody durations so inner fills don't chop a dotted-8th [d3] note!
        protected_interior: Set[int] = set()
        for mn in lb.melody:
            if mn.dur is not None and mn.dur in VALID_DURS:
                # Only protect short explicit syncopated durations (1, 2, 3) from being split
                if mn.dur <= 3:
                    for k in range(mn.slot + 1, min(16, mn.slot + mn.dur)):
                        protected_interior.add(k)

        # Add regular 8th-note grid slots (0, 2, 4, 6, 8, 10, 12, 14) unless inside a protected syncopated melody note
        grid_slots: Set[int] = set(fixed_slots)
        for s8 in (0, 2, 4, 6, 8, 10, 12, 14):
            if s8 not in protected_interior:
                grid_slots.add(s8)

        # Ensure every consecutive span is a legal VALID_DURS (e.g. if slot 7->12 has span 5, slot 8 is already in grid_slots!)
        ordered_starts = sorted(s for s in grid_slots if 0 <= s < 16)
        final_starts: List[int] = []
        for idx_s, s_cur in enumerate(ordered_starts):
            s_nxt = ordered_starts[idx_s + 1] if idx_s + 1 < len(ordered_starts) else 16
            span = s_nxt - s_cur
            if span in VALID_DURS and (tier.allow_16th or span != 1):
                final_starts.append(s_cur)
            else:
                cur_sub = s_cur
                for chunk in _decompose_legal_gap(span, tier.allow_16th):
                    final_starts.append(cur_sub)
                    cur_sub += chunk

        bar_events: List[BeatEvent] = []
        inner_cycle = 0
        prev_inner_sf: Optional[Tuple[int, int]] = None
        cur_root_bass_m: Optional[int] = None

        # Decide whether slot 0 of this bar gets a phrase-opening arpeggio
        has_mel_at_0 = 0 in mel_by_slot
        want_bar_arp = (
            "arpeggio" in tier.allowed_techs
            and has_mel_at_0
            and arp_used < arp_budget
            and (not is_pm_style)
            and (lb.style in ("intro_arp", "verse_breathing", "chorus_open", "chorus_slap", "interlude_open"))
        )
        # Keep arpeggio restrained (~30-42%): always arpeggiate section openings or every phrase-start melody bar
        if want_bar_arp and lb.section is None and lb.style == "chorus_slap" and (lb.bar % 2 == 0):
            want_bar_arp = False

        for ev_i, s_cur in enumerate(final_starts):
            s_nxt = final_starts[ev_i + 1] if ev_i + 1 < len(final_starts) else 16
            dur = s_nxt - s_cur
            ch_lbl = _chord_at_slot(lb.chords, s_cur)
            is_ch_slot = (s_cur in ch_slots) or (s_cur == 0 and bool(ch_lbl))
            # Also add secondary root bass pulse on beat 3 (slot 8) for single-chord bars (like M16, M20, M35..M41)
            is_beat3_bass = (s_cur == 8 and bool(ch_lbl) and (8 not in ch_slots) and (8 not in mel_by_slot or lb.style in ("interlude_pm", "interlude_open", "verse_breathing")))
            need_bass = is_ch_slot or (is_beat3_bass and (s_cur not in mel_by_slot or lb.style in ("interlude_pm", "interlude_open")))

            mn = mel_by_slot.get(s_cur)
            notes: List[NoteEvent] = []
            used_s: Set[int] = set()
            used_f: List[int] = []
            ev_arp = False

            # 1) Lock melody note if present at this slot
            mel_note_obj: Optional[NoteEvent] = None
            if mn is not None:
                ms, mf, mh = voicing[(lb.bar, mn.slot)]
                mel_m = OPEN_TUNING_NO_CAPO[ms] + capo + mf
                active_melody_midi = None if mh else mel_m
                mel_note_obj = NoteEvent(ms, mf, "melody", harmonic=mh)
                used_s.add(ms)
                if mf > 0 and not mh:
                    used_f.append(mf)

            ceiling = active_melody_midi if active_melody_midi is not None else 84

            # 2) Add chord ROOT bass on chord changes / beat 1 / beat 3 pulse
            if need_bass and ch_lbl:
                b_cands = _find_root_bass_candidates(ch_lbl, capo, ceiling, tier, used_s, used_f)
                if b_cands:
                    _, bs, bf, bm = b_cands[0]
                    notes.append(NoteEvent(bs, bf, "bass", palm_mute=is_pm_style))
                    used_s.add(bs)
                    if bf > 0:
                        used_f.append(bf)
                    cur_root_bass_m = bm

            # 3) Add backbeat thumb slap on Beat 2 (slot 4) & Beat 4 (slot 12) in chorus_slap style
            # Skip slap on calm cadence bars (Dsus4/D or outro harmonic bars)
            want_slap = (
                is_slap_style
                and s_cur in (4, 12)
                and 6 not in used_s
                and (not is_last_bar)
                and (lb.bar < last_bar_num - 2)
                and not (ch_lbl.startswith("Dsus"))
            )
            if want_slap:
                notes.append(NoteEvent(6, 0, "slap"))
                used_s.add(6)

            # 4) Phrase-downbeat 3-voice arpeggio inner note (ONLY on slot 0 with melody!)
            if s_cur == 0 and want_bar_arp and mel_note_obj is not None and not mel_note_obj.harmonic and len(notes) >= 1 and tier.max_simultaneous >= 3:
                inners = _find_safe_inners(ch_lbl, capo, ceiling, tier, used_s, used_f, cur_root_bass_m)
                # For high-fret climax chords (e.g. s6f8 + s1f7), also allow open strings 3/2 in chord!
                if inners:
                    ins, inf, _im = inners[0]
                    notes.append(NoteEvent(ins, inf, "inner", palm_mute=is_pm_style))
                    used_s.add(ins)
                    if inf > 0:
                        used_f.append(inf)
                    # Add 2nd open inner on high climax (e.g. M63 Cmaj7 open 3+2 strings)
                    if len(inners) >= 2 and inners[1][1] == 0 and (used_f and max(used_f) >= 6) and tier.max_simultaneous >= 4:
                        ins2, inf2, _ = inners[1]
                        if ins2 not in used_s:
                            notes.append(NoteEvent(ins2, inf2, "inner", palm_mute=is_pm_style))
                            used_s.add(ins2)
                if len(notes) + 1 >= 3:
                    ev_arp = True
                    arp_used += 1

            # 5) Append melody note cleanly (NO weak-beat double-stop inner clutter!)
            if mel_note_obj is not None:
                notes.append(mel_note_obj)
            elif not any(nt.role == "bass" for nt in notes):
                # Sustain/rest gap (or backbeat slap without bass/melody) -> fill 1 flowing [SAFE INNER] note!
                inners = _find_safe_inners(ch_lbl, capo, ceiling, tier, used_s, used_f, cur_root_bass_m)
                if inners:
                    # Prefer pitches strictly below sustained melody ceiling so gap inner doesn't echo active melody
                    strictly_below = [cand for cand in inners if cand[2] < ceiling]
                    pool = strictly_below if strictly_below else inners
                    # Avoid repeating exact same inner string/fret back-to-back if an alternative exists
                    pick = pool[inner_cycle % len(pool)]
                    if prev_inner_sf is not None and (pick[0], pick[1]) == prev_inner_sf and len(pool) > 1:
                        inner_cycle += 1
                        pick = pool[inner_cycle % len(pool)]
                    inner_cycle += 1
                    prev_inner_sf = (pick[0], pick[1])
                    notes.append(NoteEvent(pick[0], pick[1], "inner", palm_mute=is_pm_style))
                elif not notes:
                    # Melody is very low (e.g. 5, or 7,); fall back cleanly to chord root bass (never voice-cross!)
                    b_cands = _find_root_bass_candidates(ch_lbl, capo, ceiling, tier, used_s, used_f)
                    if b_cands:
                        _, bs, bf, _ = b_cands[0]
                        notes.append(NoteEvent(bs, bf, "bass", palm_mute=is_pm_style))
                        prev_inner_sf = None
                    else:
                        bar_events.append(BeatEvent(slot=s_cur, dur=dur, notes=(), rest=True))
                        active_melody_midi = None
                        continue

            bar_events.append(
                BeatEvent(
                    slot=s_cur,
                    dur=dur,
                    notes=tuple(notes),
                    lyric=(mn.lyric if mn is not None else None),
                    arpeggio=ev_arp,
                )
            )

        # 6) Inject idiomatic H / P / S legato & strictly direction-matched upward grace_from!
        prev_bar_mel_midi: Optional[int] = None
        if measures:
            for pe in reversed(measures[-1].events):
                pmels = [nt for nt in pe.notes if nt.role == "melody" and not nt.harmonic]
                if pmels:
                    prev_bar_mel_midi = note_concert_midi(pmels[0], capo)
                    break

        prev_ev_mel_midi: Optional[int] = prev_bar_mel_midi
        prev_ev_same_bar = False

        for ev_i, e in enumerate(bar_events):
            mels = [nt for nt in e.notes if nt.role == "melody" and not nt.harmonic]
            if not mels:
                continue
            mc = mels[0]
            cm = note_concert_midi(mc, capo)
            mn_src = mel_by_slot.get(e.slot)

            # A) Expressive upward grace slide-in ONLY when explicitly tagged OR strong climax peak going UP
            want_grace = (
                "grace" in tier.allowed_techs
                and mc.fret >= 2
                and (not e.rest)
                and (mn_src is not None and mn_src.tag == "grace")
            )
            # Also allow automatic tasteful grace on very high climax downbeat (fret >= 5, ascending into bar)
            if (
                not want_grace
                and "grace" in tier.allowed_techs
                and lb.style in ("chorus_open", "chorus_slap")
                and e.slot == 0
                and mc.fret >= 5
                and (prev_ev_mel_midi is None or cm > prev_ev_mel_midi)
            ):
                want_grace = True

            if want_grace:
                # Strictly direction-matched: NEVER put upward grace_from on a descending same-bar melody!
                is_desc_same_bar = (prev_ev_same_bar and prev_ev_mel_midi is not None and cm < prev_ev_mel_midi)
                if not is_desc_same_bar:
                    for step in (2, 1):
                        gf = mc.fret - step
                        if gf >= 1 and ((OPEN_TUNING_NO_CAPO[mc.string] + capo + gf) % 12) in diatonic_pcs:
                            new_notes = tuple(
                                NoteEvent(nt.string, nt.fret, nt.role, harmonic=nt.harmonic, grace_from=gf, palm_mute=nt.palm_mute)
                                if nt is mc else nt
                                for nt in e.notes
                            )
                            bar_events[ev_i] = BeatEvent(
                                slot=e.slot, dur=e.dur, notes=new_notes, rest=e.rest,
                                lyric=e.lyric, fermata=e.fermata, arpeggio=e.arpeggio, link_tech=e.link_tech
                            )
                            e = bar_events[ev_i]
                            break

            # B) Same-string stepwise H / P legato to the immediate next event in the same bar
            if ev_i + 1 < len(bar_events) and ("H" in tier.allowed_techs or "P" in tier.allowed_techs):
                nxt = bar_events[ev_i + 1]
                has_slap_cur = any(nt.role == "slap" for nt in e.notes)
                if not nxt.rest and not has_slap_cur and e.dur <= 2:
                    nxt_same = [nt for nt in nxt.notes if nt.string == mc.string and nt.role == "melody" and not nt.harmonic]
                    if nxt_same:
                        df = nxt_same[0].fret - mc.fret
                        # Prioritize super idiomatic guitar H/P: fast 16th ornaments, open-to-fretted H, fretted-to-open P
                        if 1 <= df <= 3 and "H" in tier.allowed_techs and (e.dur == 1 or mc.fret == 0):
                            e.link_tech = "H"
                        elif -3 <= df <= -1 and "P" in tier.allowed_techs and (nxt_same[0].fret == 0 or e.dur == 1):
                            e.link_tech = "P"

            prev_ev_mel_midi = cm
            prev_ev_same_bar = True

        # Record landmarks on key bars (first bar of each section + climax)
        bar_mel_midis = [
            note_concert_midi(nt, capo)
            for e in bar_events
            for nt in e.notes
            if nt.role == "melody" and not nt.harmonic
        ]
        if bar_mel_midis and (lb.section is not None or lb.bar == last_bar_num - 5):
            landmarks[lb.bar] = bar_mel_midis

        measures.append(
            Measure(
                bar=lb.bar,
                events=bar_events,
                chords=lb.chords,
                section=lb.section,
                dynamic=lb.dynamic,
            )
        )

    # Guarantee at least 2 H/P/S links in standard/advanced full arrangements if natural opportunities exist
    if tier.key in ("standard", "advanced") and total_full_bars >= 16:
        legato_cnt = sum(1 for meas in measures for e in meas.events if e.link_tech in ("H", "P", "S"))
        if legato_cnt < 2:
            for meas in measures:
                for ev_i in range(len(meas.events) - 1):
                    e, nxt = meas.events[ev_i], meas.events[ev_i + 1]
                    if e.link_tech is not None or e.rest or nxt.rest:
                        continue
                    mels = [nt for nt in e.notes if nt.role == "melody" and not nt.harmonic]
                    if not mels:
                        continue
                    mc = mels[0]
                    nxt_same = [nt for nt in nxt.notes if nt.string == mc.string and nt.role != "slap" and not nt.harmonic]
                    if nxt_same:
                        df = nxt_same[0].fret - mc.fret
                        if 1 <= df <= 3:
                            e.link_tech = "H"
                            legato_cnt += 1
                        elif -3 <= df <= -1:
                            e.link_tech = "P"
                            legato_cnt += 1
                        elif df != 0:
                            e.link_tech = "S"
                            legato_cnt += 1
                    if legato_cnt >= 3:
                        break
                if legato_cnt >= 3:
                    break

    return measures, landmarks


# =============================================================================
# 6. Exporter 1: score_data.py (Full Tab Engine Compatibility)
# =============================================================================

def _format_note_call(nt: NoteEvent) -> str:
    kw: List[str] = []
    if nt.harmonic:
        kw.append("harmonic=True")
    if nt.grace_from is not None:
        kw.append(f"grace_from={nt.grace_from}")
    if nt.palm_mute:
        kw.append("palm_mute=True")
    extra = (", " + ", ".join(kw)) if kw else ""
    return f'n({nt.string}, {nt.fret}, "{nt.role}"{extra})'


def render_score_data_py(parsed: Dict[str, Any], measures: List[Measure], landmarks: Dict[int, List[int]]) -> str:
    ks = parsed["key_spec"]
    capo = parsed["capo"]
    fing_name = parsed["fing_name"]
    tier: TierProfile = parsed["tier"]
    bpm = parsed["bpm"]
    title = parsed["title"]
    artist = parsed["artist"]
    composer = parsed["composer"]
    subtitle = parsed["subtitle"]
    file_prefix = parsed["file_prefix"] or (
        re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", title).strip("_") or "Fingerstyle_Score"
    )

    # Check if any non-diatonic passing notes exist in source melody
    has_chromatic = any(
        (note_concert_midi(nt, capo) % 12) not in ks.diatonic_pcs
        for meas in measures
        for e in meas.events
        for nt in e.notes
        if nt.role != "slap"
    )

    # Compact chord legend (kept <= 78 chars so Page 1 header never clips!)
    used_chords: List[str] = []
    for meas in measures:
        for _sl, cl in meas.chords:
            if cl not in used_chords:
                used_chords.append(cl)
    pref_flat_concert = ks.major_tonic_pc in {1, 3, 5, 8, 10}
    legend_pairs = [
        f"{c}={transpose_chord_label(c, capo, prefer_flat=pref_flat_concert)}"
        for c in used_chords[:7]
    ]
    chord_legend = f"和弦对照 (Capo {capo})：" + "  ".join(legend_pairs)

    bars_lines: List[str] = []
    for meas in measures:
        ch_str = ", ".join(f'({sl}, "{lbl}")' for sl, lbl in meas.chords)
        bars_lines.append(f"    # M{meas.bar:02d}" + (f" [{meas.section}]" if meas.section else ""))
        bars_lines.append("    m.append(Measure(")
        bars_lines.append(f"        bar={meas.bar},")
        if meas.is_pickup:
            bars_lines.append("        is_pickup=True,")
        if meas.section:
            bars_lines.append(f'        section="{meas.section}",')
        bars_lines.append(f"        dynamic={meas.dynamic:.2f},")
        if meas.chords:
            bars_lines.append(f"        chords=[{ch_str}],")
        bars_lines.append("        events=[")
        for e in meas.events:
            if e.rest:
                bars_lines.append(f"            ev({e.slot}, {e.dur}, rest=True),")
                continue
            n_calls = ", ".join(_format_note_call(nt) for nt in e.notes)
            ex: List[str] = []
            if e.lyric:
                safe_lyr = e.lyric.replace('"', '\\"')
                ex.append(f'lyric="{safe_lyr}"')
            if e.fermata:
                ex.append("fermata=True")
            if e.arpeggio:
                ex.append("arpeggio=True")
            if e.link_tech:
                ex.append(f'link_tech="{e.link_tech}"')
            ex_s = (", " + ", ".join(ex)) if ex else ""
            bars_lines.append(f"            ev({e.slot}, {e.dur}, {n_calls}{ex_s}),")
        bars_lines.append("        ],")
        bars_lines.append("    ))")
        bars_lines.append("")

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

Generated deterministically by `scripts/arrange_engine.py` (Viterbi Voicer + Style Arpeggiator).
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

TITLE = "{title}"
FILE_PREFIX = "{file_prefix}"
SUBTITLE = "{subtitle}"
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
    """Return arranged fingerstyle measures."""
    m: List[Measure] = []
{chr(10).join(bars_lines)}
    return m
'''


# =============================================================================
# 7. Exporter 2: AlphaTex (.atex) — Native Lossless Format for CoderLine/alphaTab
# =============================================================================

ATEX_DUR_MAP: Dict[int, Tuple[int, bool]] = {
    1: (16, False),
    2: (8, False),
    3: (8, True),
    4: (4, False),
    6: (4, True),
    8: (2, False),
    12: (2, True),
    16: (1, False),
}


def _atex_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _dyn_to_atex(dyn: float) -> str:
    if dyn < 0.80:
        return "p"
    if dyn < 0.88:
        return "mp"
    if dyn < 0.96:
        return "mf"
    if dyn < 1.04:
        return "f"
    return "ff"


def export_alphatex(mod_or_meta: Dict[str, Any], measures: List[Any]) -> str:
    """Export measures into clean, spec-compliant AlphaTex (.atex) for CoderLine/alphaTab."""
    title = _atex_escape(str(mod_or_meta.get("title") or "Untitled"))
    subtitle = _atex_escape(str(mod_or_meta.get("subtitle") or "Fingerstyle Guitar TAB"))
    artist = _atex_escape(str(mod_or_meta.get("artist") or "Unknown"))
    composer = _atex_escape(str(mod_or_meta.get("composer") or artist))
    bpm = int(mod_or_meta.get("bpm") or 80)
    capo = int(mod_or_meta.get("capo") or 0)

    lines: List[str] = [
        f'\\title "{title}"',
        f'\\subtitle "{subtitle}"',
        f'\\artist "{artist}"',
        f'\\music "{composer}"',
        f"\\tempo {bpm}",
        ".",
        '\\track "Acoustic Guitar"',
        "\\staff { score tabs }",
        "\\tuning E4 B3 G3 D3 A2 E2",
        f"\\capo {capo}",
        "\\instrument 25",
        "\\ts 4 4",
    ]

    last_dyn_mark: Optional[str] = None
    for m_idx, meas in enumerate(measures):
        bar_prefix: List[str] = []
        if getattr(meas, "section", None):
            bar_prefix.append(f'\\section "{_atex_escape(str(meas.section))}"')
        if getattr(meas, "is_pickup", False) and meas.events:
            p_len = 16 - meas.events[0].slot
            if p_len == 4:
                bar_prefix.append("\\ts 1 4")
            elif p_len == 8:
                bar_prefix.append("\\ts 2 4")
            elif p_len == 12:
                bar_prefix.append("\\ts 3 4")
        elif m_idx == 1 and getattr(measures[0], "is_pickup", False):
            bar_prefix.append("\\ts 4 4")

        ch_by_slot = {sl: lbl for sl, lbl in getattr(meas, "chords", [])}
        dyn_mark = _dyn_to_atex(float(getattr(meas, "dynamic", 0.92) or 0.92))
        emit_dyn = (dyn_mark != last_dyn_mark)
        last_dyn_mark = dyn_mark

        beat_tokens: List[str] = []
        for ev_i, e in enumerate(meas.events):
            base_d, is_dotted = ATEX_DUR_MAP.get(e.dur, (8, False))
            beat_props: List[str] = []
            if is_dotted:
                beat_props.append("d")
            if e.slot in ch_by_slot:
                beat_props.append(f'ch "{_atex_escape(ch_by_slot[e.slot])}"')
            if emit_dyn and ev_i == 0:
                beat_props.append(f"dy {dyn_mark}")
            if e.arpeggio:
                beat_props.append("au")
            if e.fermata:
                beat_props.append("fermata medium")
            if e.lyric:
                beat_props.append(f'txt "{_atex_escape(e.lyric)}"')

            if e.rest or not e.notes:
                prop_s = ("{" + " ".join(beat_props) + "}") if beat_props else ""
                beat_tokens.append(f"r.{base_d}{prop_s}")
                continue

            # Emit grace slide-in beat before main beat if present (note-level {sl} + beat-level {gr})
            grace_notes = [nt for nt in e.notes if getattr(nt, "grace_from", None) is not None]
            if grace_notes:
                gnt = grace_notes[0]
                beat_tokens.append(f"{gnt.grace_from}.{gnt.string}{{sl}}.16{{gr}}")

            # Determine which melody note carries link_tech (H/P/S)
            mel_link_str: Optional[int] = None
            if e.link_tech in ("H", "P", "S"):
                mels = [nt for nt in e.notes if nt.role == "melody"]
                if mels:
                    mel_link_str = mels[0].string

            note_parts: List[str] = []
            for nt in sorted(e.notes, key=lambda x: -x.string):
                if nt.role == "slap":
                    note_parts.append(f"x.{nt.string}")
                    continue
                n_fx: List[str] = []
                if nt.harmonic:
                    n_fx.append("nh")
                if nt.palm_mute:
                    n_fx.append("pm")
                if mel_link_str is not None and nt.string == mel_link_str:
                    if e.link_tech in ("H", "P"):
                        n_fx.append("h")
                    elif e.link_tech == "S":
                        n_fx.append("sl")
                fx_s = ("{" + " ".join(n_fx) + "}") if n_fx else ""
                note_parts.append(f"{nt.fret}.{nt.string}{fx_s}")

            prop_s = ("{" + " ".join(beat_props) + "}") if beat_props else ""
            if len(note_parts) == 1:
                beat_tokens.append(f"{note_parts[0]}.{base_d}{prop_s}")
            else:
                beat_tokens.append(f"({' '.join(note_parts)}).{base_d}{prop_s}")

        prefix_s = (" ".join(bar_prefix) + " ") if bar_prefix else ""
        sep = " |" if m_idx < len(measures) - 1 else ""
        lines.append(f"{prefix_s}{' '.join(beat_tokens)}{sep}")

    return "\n".join(lines) + "\n"


# =============================================================================
# 8. Exporter 3: MusicXML 4.0 TAB (.musicxml) — Open Standard (alphaTab / GP / MuseScore)
# =============================================================================

MXML_TYPE_MAP: Dict[int, Tuple[str, bool]] = {
    1: ("16th", False),
    2: ("eighth", False),
    3: ("eighth", True),
    4: ("quarter", False),
    6: ("quarter", True),
    8: ("half", False),
    12: ("half", True),
    16: ("whole", False),
}

STEP_ALTER = [
    ("C", 0), ("C", 1), ("D", 0), ("D", 1), ("E", 0), ("F", 0),
    ("F", 1), ("G", 0), ("G", 1), ("A", 0), ("A", 1), ("B", 0),
]

MXML_CHORD_KIND: Dict[str, str] = {
    "": "major",
    "m": "minor",
    "maj7": "major-seventh",
    "m7": "minor-seventh",
    "7": "dominant",
    "6": "major-sixth",
    "m6": "minor-sixth",
    "9": "dominant-ninth",
    "m9": "minor-ninth",
    "maj9": "major-ninth",
    "add9": "major",
    "sus2": "suspended-second",
    "sus4": "suspended-fourth",
    "sus": "suspended-fourth",
    "7sus4": "suspended-fourth",
    "dim": "diminished",
    "dim7": "diminished-seventh",
    "m7b5": "half-diminished",
    "aug": "augmented",
    "+": "augmented",
    "5": "power",
}


def _append_pitch(parent: ET.Element, written_midi: int) -> None:
    step, alter = STEP_ALTER[written_midi % 12]
    octave = (written_midi // 12) - 1
    p_el = ET.SubElement(parent, "pitch")
    ET.SubElement(p_el, "step").text = step
    if alter:
        ET.SubElement(p_el, "alter").text = str(alter)
    ET.SubElement(p_el, "octave").text = str(octave)


def _append_harmony(meas_el: ET.Element, label: str) -> None:
    m = CHORD_RE.match(label.strip())
    if not m:
        return
    root, qual, bass = m.group(1), m.group(2), m.group(3)
    h_el = ET.SubElement(meas_el, "harmony")
    r_el = ET.SubElement(h_el, "root")
    ET.SubElement(r_el, "root-step").text = root[0].upper()
    if len(root) > 1:
        ET.SubElement(r_el, "root-alter").text = "1" if root[1] == "#" else "-1"
    k_el = ET.SubElement(h_el, "kind")
    k_el.set("text", qual)
    k_el.text = MXML_CHORD_KIND.get(qual, "major")
    if bass:
        b_el = ET.SubElement(h_el, "bass")
        ET.SubElement(b_el, "bass-step").text = bass[0].upper()
        if len(bass) > 1:
            ET.SubElement(b_el, "bass-alter").text = "1" if bass[1] == "#" else "-1"


def export_musicxml(mod_or_meta: Dict[str, Any], measures: List[Any]) -> str:
    """Export measures into standard MusicXML 4.0 Guitar TAB (.musicxml)."""
    title = str(mod_or_meta.get("title") or "Untitled")
    composer = str(mod_or_meta.get("composer") or mod_or_meta.get("artist") or "Unknown")
    bpm = int(mod_or_meta.get("bpm") or 80)
    capo = int(mod_or_meta.get("capo") or 0)
    fifths = int(mod_or_meta.get("fifths") or 0)

    root = ET.Element("score-partwise", version="4.0")
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = title
    ident = ET.SubElement(root, "identification")
    cr = ET.SubElement(ident, "creator", type="composer")
    cr.text = composer

    plist = ET.SubElement(root, "part-list")
    sp = ET.SubElement(plist, "score-part", id="P1")
    ET.SubElement(sp, "part-name").text = "Acoustic Guitar"
    si = ET.SubElement(sp, "score-instrument", id="P1-I1")
    ET.SubElement(si, "instrument-name").text = "Acoustic Guitar (steel)"
    mi = ET.SubElement(sp, "midi-instrument", id="P1-I1")
    ET.SubElement(mi, "midi-channel").text = "1"
    ET.SubElement(mi, "midi-program").text = "26"

    part = ET.SubElement(root, "part", id="P1")
    tunings = [(6, "E", 2), (5, "A", 2), (4, "D", 3), (3, "G", 3), (2, "B", 3), (1, "E", 4)]

    for m_idx, meas in enumerate(measures):
        m_el = ET.SubElement(part, "measure", number=str(meas.bar))
        if getattr(meas, "is_pickup", False):
            m_el.set("implicit", "yes")

        if m_idx == 0:
            attr = ET.SubElement(m_el, "attributes")
            ET.SubElement(attr, "divisions").text = "4"  # 4 divisions per quarter = 16th-note slots!
            key_el = ET.SubElement(attr, "key")
            ET.SubElement(key_el, "fifths").text = str(fifths)
            time_el = ET.SubElement(attr, "time")
            ET.SubElement(time_el, "beats").text = "4"
            ET.SubElement(time_el, "beat-type").text = "4"
            clef = ET.SubElement(attr, "clef")
            ET.SubElement(clef, "sign").text = "TAB"
            ET.SubElement(clef, "line").text = "5"
            sd = ET.SubElement(attr, "staff-details")
            ET.SubElement(sd, "staff-lines").text = "6"
            for line_i, (_s_num, st_step, st_oct) in enumerate(tunings, start=1):
                st = ET.SubElement(sd, "staff-tuning", line=str(line_i))
                ET.SubElement(st, "tuning-step").text = st_step
                ET.SubElement(st, "tuning-octave").text = str(st_oct)
            if capo > 0:
                ET.SubElement(sd, "capo").text = str(capo)
                tr = ET.SubElement(attr, "transpose")
                ET.SubElement(tr, "diatonic").text = "0"
                ET.SubElement(tr, "chromatic").text = str(capo)

            dir_t = ET.SubElement(m_el, "direction", placement="above")
            dt = ET.SubElement(dir_t, "direction-type")
            met = ET.SubElement(dt, "metronome")
            ET.SubElement(met, "beat-unit").text = "quarter"
            ET.SubElement(met, "per-minute").text = str(bpm)
            ET.SubElement(dir_t, "sound", tempo=str(bpm))

        if getattr(meas, "section", None):
            dir_s = ET.SubElement(m_el, "direction", placement="above")
            dt_s = ET.SubElement(dir_s, "direction-type")
            ET.SubElement(dt_s, "rehearsal").text = str(meas.section)

        ch_by_slot = {sl: lbl for sl, lbl in getattr(meas, "chords", [])}

        for ev_i, e in enumerate(meas.events):
            if e.slot in ch_by_slot:
                _append_harmony(m_el, ch_by_slot[e.slot])

            type_str, is_dot = MXML_TYPE_MAP.get(e.dur, ("eighth", False))
            if e.rest or not e.notes:
                n_el = ET.SubElement(m_el, "note")
                ET.SubElement(n_el, "rest")
                ET.SubElement(n_el, "duration").text = str(e.dur)
                ET.SubElement(n_el, "type").text = type_str
                if is_dot:
                    ET.SubElement(n_el, "dot")
                continue

            # Emit grace note first if present
            grace_notes = [nt for nt in e.notes if getattr(nt, "grace_from", None) is not None]
            if grace_notes:
                gnt = grace_notes[0]
                gn_el = ET.SubElement(m_el, "note")
                ET.SubElement(gn_el, "grace", slash="yes")
                _append_pitch(gn_el, OPEN_TUNING_NO_CAPO[gnt.string] + gnt.grace_from)
                ET.SubElement(gn_el, "type").text = "16th"
                nots = ET.SubElement(gn_el, "notations")
                tech = ET.SubElement(nots, "technical")
                ET.SubElement(tech, "string").text = str(gnt.string)
                ET.SubElement(tech, "fret").text = str(gnt.grace_from)
                ET.SubElement(nots, "slide", type="start", number="1")

            prev_link = meas.events[ev_i - 1].link_tech if ev_i > 0 else None
            prev_mel_s = None
            if prev_link in ("H", "P", "S"):
                pm = [nt for nt in meas.events[ev_i - 1].notes if nt.role == "melody"]
                if pm:
                    prev_mel_s = pm[0].string
            cur_mel_s = next((nt.string for nt in e.notes if nt.role == "melody"), None)

            ordered = sorted(e.notes, key=lambda x: -x.string)
            for n_i, nt in enumerate(ordered):
                n_el = ET.SubElement(m_el, "note")
                if n_i > 0:
                    ET.SubElement(n_el, "chord")
                _append_pitch(n_el, OPEN_TUNING_NO_CAPO[nt.string] + nt.fret)
                ET.SubElement(n_el, "duration").text = str(e.dur)
                ET.SubElement(n_el, "type").text = type_str
                if is_dot:
                    ET.SubElement(n_el, "dot")
                if nt.role == "slap":
                    ET.SubElement(n_el, "notehead").text = "x"

                nots = ET.SubElement(n_el, "notations")
                tech = ET.SubElement(nots, "technical")
                ET.SubElement(tech, "string").text = str(nt.string)
                ET.SubElement(tech, "fret").text = str(nt.fret)
                if nt.harmonic:
                    harm = ET.SubElement(tech, "harmonic")
                    ET.SubElement(harm, "natural")
                if getattr(nt, "grace_from", None) is not None:
                    ET.SubElement(nots, "slide", type="stop", number="1")
                if prev_link == "H" and nt.string == prev_mel_s:
                    ET.SubElement(tech, "hammer-on", type="stop", number="1")
                elif prev_link == "P" and nt.string == prev_mel_s:
                    ET.SubElement(tech, "pull-off", type="stop", number="1")
                elif prev_link == "S" and nt.string == prev_mel_s:
                    ET.SubElement(nots, "slide", type="stop", number="2")
                if e.link_tech == "H" and nt.string == cur_mel_s:
                    ET.SubElement(tech, "hammer-on", type="start", number="1").text = "H"
                elif e.link_tech == "P" and nt.string == cur_mel_s:
                    ET.SubElement(tech, "pull-off", type="start", number="1").text = "P"
                elif e.link_tech == "S" and nt.string == cur_mel_s:
                    ET.SubElement(nots, "slide", type="start", number="2")
                if e.arpeggio and n_i == 0:
                    ET.SubElement(nots, "arpeggiate", direction="up")
                if e.fermata and n_i == 0:
                    ET.SubElement(nots, "fermata")
                if e.lyric and n_i == len(ordered) - 1:
                    lyr = ET.SubElement(n_el, "lyric", number="1")
                    ET.SubElement(lyr, "syllabic").text = "single"
                    ET.SubElement(lyr, "text").text = e.lyric

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# =============================================================================
# 9. Convenience Parser: Jianpu Text Spec -> Declarative lead_sheet.json
# =============================================================================

SEC_HDR_RE = re.compile(r"^#{2,4}\s*\[([^\]]+)\](.*)$")
BAR_LINE_RE = re.compile(r"^M(\d+)\s*\|\s*([^|]+?)\s*\|\s*(.*)$")
CHORD_AT_RE = re.compile(r"@(\d+):([A-Za-z0-9#b/+]+)")
MEL_TOK_RE = re.compile(r"s(\d+):([#b]?[1-7][',’+-]*)(?:\[d(\d+)\])?(?:\(([^)]*)\))?")


def _infer_style_from_section(sec_name: str, extra_desc: str) -> str:
    low = (sec_name + " " + extra_desc).lower()
    if "intro" in low or "前奏" in low:
        return "intro_arp"
    if "闷音" in low or "palm" in low or "interlude" in low or "间奏" in low:
        return "interlude_pm"
    if "拍弦" in low or "slap" in low or "chorus ii" in low or "副歌二" in low:
        return "chorus_slap"
    if "chorus" in low or "副歌" in low:
        return "chorus_open"
    if "outro" in low or "尾奏" in low:
        return "outro_calm"
    return "verse_breathing"


def parse_jianpu_text_to_lead_sheet(spec_path: Path) -> Dict[str, Any]:
    """Convert a structured jianpu spec markdown/text file into a clean `lead_sheet.json`."""
    text = spec_path.read_text(encoding="utf-8")
    title = "Untitled"
    artist = "Unknown"
    composer = "Unknown"
    key_str = "C"
    bpm = 80
    capo: Any = "auto"

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- 歌名：") or s.startswith("- Title:"):
            title = s.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif s.startswith("- 演唱：") or s.startswith("- Artist:"):
            artist = s.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif s.startswith("- 词曲：") or s.startswith("- Composer:"):
            composer = s.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif "1 =" in s or "1=" in s:
            km = re.search(r"1\s*=\s*([A-G][#b]?m?)", s)
            if km:
                key_str = km.group(1)
        elif "BPM" in s:
            bm = re.search(r"(\d+)\s*BPM", s)
            if bm:
                bpm = int(bm.group(1))
        if "Capo" in s:
            cm = re.search(r"Capo\s*=\s*(\d+)", s)
            if cm:
                capo = int(cm.group(1))

    sections: List[Dict[str, Any]] = []
    cur_sec: Optional[Dict[str, Any]] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        sm = SEC_HDR_RE.match(line)
        if sm:
            sec_name = sm.group(1).strip()
            sec_desc = sm.group(2).strip()
            style = _infer_style_from_section(sec_name, sec_desc)
            cur_sec = {"name": sec_name, "style": style, "dynamic": STYLE_DEFAULT_DYNAMICS.get(style, 0.90), "bars": []}
            sections.append(cur_sec)
            continue

        bm = BAR_LINE_RE.match(line)
        if not bm:
            continue
        if cur_sec is None:
            cur_sec = {"name": "Main", "style": "verse_breathing", "dynamic": 0.90, "bars": []}
            sections.append(cur_sec)

        bar_num = int(bm.group(1))
        ch_part = bm.group(2)
        mel_part = bm.group(3)
        chords = [[int(sl), lbl] for sl, lbl in CHORD_AT_RE.findall(ch_part)]

        mel_items: List[List[Any]] = []
        for sl_s, tok, d_s, paren in MEL_TOK_RE.findall(mel_part):
            sl = int(sl_s)
            du = int(d_s) if d_s else None
            lyr = ""
            tag = ""
            if paren:
                if "自然泛音" in paren or "harm" in paren.lower():
                    tag = "harm"
                elif "滑" in paren or "grace" in paren.lower():
                    tag = "grace"
                elif "收尾" in paren or "fermata" in paren.lower():
                    tag = "fermata"
                else:
                    lyr = paren.strip()
            if "fermata" in mel_part.lower() and du == 16:
                tag = "fermata"
            if "滑入" in mel_part and not tag and len(mel_items) == 0:
                tag = "grace"
            item: List[Any] = [sl, tok]
            if du is not None or lyr or tag:
                item.append(du)
            if lyr or tag:
                item.append(lyr)
            if tag:
                item.append(tag)
            mel_items.append(item)

        bar_entry: Dict[str, Any] = {"bar": bar_num, "chords": chords, "melody": mel_items}
        # Detect open crescendo second half of Interlude automatically if section description mentions it
        if cur_sec["style"] == "interlude_pm" and len(cur_sec["bars"]) >= 4:
            bar_entry["style"] = "interlude_open"
            bar_entry["dynamic"] = 0.92
        cur_sec["bars"].append(bar_entry)

    return {
        "title": title,
        "artist": artist,
        "composer": composer,
        "key": key_str,
        "bpm": bpm,
        "capo": capo,
        "chords_mode": "fingered",
        "tier": "standard",
        "sections": sections,
    }


# =============================================================================
# 10. CLI Entry Points
# =============================================================================

def cmd_arrange(args: argparse.Namespace) -> int:
    lead_path = Path(args.lead_sheet).expanduser().resolve()
    raw = json.loads(lead_path.read_text(encoding="utf-8"))
    parsed = load_lead_sheet(raw, tier_override=args.tier)
    measures, landmarks = arrange_lead_sheet(parsed)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {tok.strip().lower() for tok in args.formats.split(",") if tok.strip()}
    prefix = parsed["file_prefix"] or (
        re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", parsed["title"]).strip("_") or "Fingerstyle_Score"
    )

    meta = {
        "title": parsed["title"],
        "subtitle": parsed["subtitle"],
        "artist": parsed["artist"],
        "composer": parsed["composer"],
        "bpm": parsed["bpm"],
        "capo": parsed["capo"],
        "fifths": parsed["key_spec"].fifths,
    }

    if "score" in fmts:
        score_path = out_dir / "src" / "score_data.py"
        score_path.parent.mkdir(parents=True, exist_ok=True)
        score_path.write_text(render_score_data_py(parsed, measures, landmarks), encoding="utf-8")
        print(f"[OK] Wrote {score_path}")

    if "atex" in fmts:
        atex_path = out_dir / f"{prefix}.atex"
        atex_path.write_text(export_alphatex(meta, measures), encoding="utf-8")
        print(f"[OK] Wrote {atex_path} ({atex_path.stat().st_size:,} bytes)")

    if "musicxml" in fmts:
        mxml_path = out_dir / f"{prefix}.musicxml"
        mxml_path.write_text(export_musicxml(meta, measures), encoding="utf-8")
        print(f"[OK] Wrote {mxml_path} ({mxml_path.stat().st_size:,} bytes)")

    # Immediately validate generated score_data.py via tab_common.validate_project_score
    if "score" in fmts:
        mod = load_score_module(out_dir)
        errors, tech_counts, summary = validate_project_score(mod)
        if errors:
            print(f"[WARN] Validation reported {len(errors)} issue(s):")
            for idx, err in enumerate(errors, 1):
                print(f"  {idx}. {err}")
            return 1
        bd = summary["score_breakdown"]
        print(
            f"[PASS] Arrange + Validate OK | TIER={summary['tier']} | "
            f"{summary['quality_score']}分 (手型{bd['playability']}/编配{bd['arrangement']}/规范{bd['notation']}) | "
            f"Lv.{summary['difficulty']} | {summary['measures']} bars | {summary['total_notes']} notes/slaps | "
            f"max_fret={summary['max_fret']} | 0 voice crossings | techniques={tech_counts}"
        )
    return 0


def cmd_parse_jianpu(args: argparse.Namespace) -> int:
    data = parse_jianpu_text_to_lead_sheet(Path(args.spec).expanduser().resolve())
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n_bars = sum(len(s["bars"]) for s in data["sections"])
    print(f"[OK] Parsed {len(data['sections'])} sections / {n_bars} bars -> {out_path}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    proj = Path(args.project_dir).expanduser().resolve()
    mod = load_score_module(proj)
    measures = mod.build_measures()
    prefix = safe_file_prefix(mod)
    meta = {
        "title": getattr(mod, "TITLE", prefix),
        "subtitle": getattr(mod, "SUBTITLE", "Fingerstyle Guitar TAB"),
        "artist": getattr(mod, "ARTIST", getattr(mod, "COMPOSER", "Unknown")),
        "composer": getattr(mod, "COMPOSER", "Unknown"),
        "bpm": int(getattr(mod, "TEMPO_BPM", 80)),
        "capo": int(getattr(mod, "CAPO", 0)),
        "fifths": int(getattr(mod, "KEY_SIGNATURE_FIFTHS", 0)),
    }
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else proj
    out_dir.mkdir(parents=True, exist_ok=True)
    fmts = {tok.strip().lower() for tok in args.formats.split(",") if tok.strip()}
    if "atex" in fmts:
        p = out_dir / f"{prefix}.atex"
        p.write_text(export_alphatex(meta, measures), encoding="utf-8")
        print(f"[OK] Exported {p} ({p.stat().st_size:,} bytes)")
    if "musicxml" in fmts:
        p = out_dir / f"{prefix}.musicxml"
        p.write_text(export_musicxml(meta, measures), encoding="utf-8")
        print(f"[OK] Exported {p} ({p.stat().st_size:,} bytes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deterministic Fingerstyle Guitar Arranger (Viterbi + AlphaTex/MusicXML).")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_arr = sub.add_parser("arrange", help="Arrange a declarative lead_sheet.json into fingerstyle TAB.")
    p_arr.add_argument("lead_sheet", help="Path to lead_sheet.json")
    p_arr.add_argument("--out-dir", required=True, help="Output project directory")
    p_arr.add_argument("--tier", default=None, choices=("beginner", "standard", "advanced"))
    p_arr.add_argument("--formats", default="score,atex,musicxml", help="Comma-separated outputs: score,atex,musicxml")

    p_pj = sub.add_parser("parse-jianpu", help="Convert structured M01..MN jianpu text into lead_sheet.json.")
    p_pj.add_argument("spec", help="Path to jianpu spec file")
    p_pj.add_argument("--out", required=True, help="Output lead_sheet.json path")

    p_ex = sub.add_parser("export", help="Export an existing score_data.py project to .atex and .musicxml.")
    p_ex.add_argument("project_dir", help="Project directory containing src/score_data.py")
    p_ex.add_argument("--out-dir", default=None, help="Output directory (defaults to project_dir)")
    p_ex.add_argument("--formats", default="atex,musicxml", help="Comma-separated outputs: atex,musicxml")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "arrange":
        return cmd_arrange(args)
    if args.cmd == "parse-jianpu":
        return cmd_parse_jianpu(args)
    if args.cmd == "export":
        return cmd_export(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
