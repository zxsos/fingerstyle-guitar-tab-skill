"""Generate sanitized sync_manifest.json from previews/page-*.png, MP3, and score_data.py."""

from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from build_pdf import PHRASE_CUES
from score_data import (
    ARRANGER_INFO,
    CAPO,
    COMPOSER,
    SUBTITLE,
    TEMPO_BPM,
    TITLE,
    Measure,
    build_measures,
    validate_score,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
PREVIEWS_DIR = PROJECT_DIR / "previews"
PDF_NAME = "Call_of_Silence_指弹吉他六线谱.pdf"
MP3_NAME = "Call_of_Silence_指弹吉他试听.mp3"
OUTPUT_MANIFEST = PROJECT_DIR / "sync_manifest.json"


def _page_sort_key(path: Path) -> Tuple[int, str]:
    m = re.search(r"(\d+)", path.stem)
    return (int(m.group(1)) if m else 0, path.name)


def read_png_gray(png_path: Path) -> Tuple[int, int, bytes]:
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
        raise RuntimeError(f"Unexpected decoded gray size for {png_path}: {len(res.stdout)} != {w * h}")
    return w, h, res.stdout


def probe_audio_duration(audio_path: Path) -> float:
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return round(float(res.stdout.strip()), 4)


def white_gap_mid(row_has_ink: List[bool], y_lo: int, y_hi: int, h: int) -> int:
    lo = max(0, y_lo)
    hi = min(h - 1, y_hi)
    if lo > hi:
        return max(0, min(h - 1, (y_lo + y_hi) // 2))

    best: Optional[Tuple[int, int]] = None
    cur: Optional[List[int]] = None
    for y in range(lo, hi + 1):
        if not row_has_ink[y]:
            if cur is None:
                cur = [y, y]
            else:
                cur[1] = y
        else:
            if cur is not None:
                if best is None or (cur[1] - cur[0]) > (best[1] - best[0]):
                    best = (cur[0], cur[1])
                cur = None
    if cur is not None and (best is None or (cur[1] - cur[0]) > (best[1] - best[0])):
        best = (cur[0], cur[1])
    if best is not None:
        return (best[0] + best[1]) // 2
    return (lo + hi) // 2


def detect_page_layout(
    png_path: Path, page_num: int
) -> Tuple[int, int, Optional[List[int]], List[Dict[str, object]]]:
    w, h, buf = read_png_gray(png_path)

    # 1. Detect 6-line TAB staves
    x0, x1 = int(w * 0.05), int(w * 0.95)
    dark_rows = [
        y
        for y in range(h)
        if sum(1 for v in buf[y * w + x0 : y * w + x1] if v < 190) > (x1 - x0) * 0.65
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
    staves = [s for s in grouped if len(s) == 6]
    if not staves:
        raise RuntimeError(f"No 6-line TAB systems detected on page {page_num} ({png_path.name})")

    row_has_ink = [any(v < 235 for v in buf[y * w : (y + 1) * w]) for y in range(h)]

    # 2. Compute pure-white horizontal cut boundaries (avoiding chords, section boxes, slurs, cues)
    first_staff = int(staves[0][0])
    if page_num == 1:
        top_cut = white_gap_mid(row_has_ink, max(0, first_staff - 170), max(0, first_staff - 60), h)
        header_ink = [y for y in range(0, top_cut + 1) if row_has_ink[y]]
        header_slice = (
            [max(0, min(header_ink) - 16), min(top_cut, max(header_ink) + 16)]
            if header_ink
            else None
        )
    else:
        top_cut = white_gap_mid(row_has_ink, 90, 125, h)
        header_slice = None

    cuts: List[int] = [top_cut]
    for i in range(len(staves) - 1):
        prev_bot = int(staves[i][-1])
        next_top = int(staves[i + 1][0])
        safe_lo = prev_bot + 40
        safe_hi = next_top - 78
        if safe_lo <= safe_hi and any(not row_has_ink[y] for y in range(safe_lo, safe_hi + 1)):
            cut = white_gap_mid(row_has_ink, safe_lo, safe_hi, h)
        else:
            cut = white_gap_mid(row_has_ink, prev_bot + 20, next_top - 20, h)
        cuts.append(cut)

    last_bot = int(staves[-1][-1])
    bot_cut = white_gap_mid(row_has_ink, last_bot + 40, min(h - 1, last_bot + 160), h)
    cuts.append(bot_cut)

    # 3. Measure exact ink bounds & detect barlines for each system
    sys_entries: List[Dict[str, object]] = []
    for si, sys_lines in enumerate(staves):
        y_top, y_bot = int(sys_lines[0]), int(sys_lines[-1])
        c0, c1 = cuts[si], cuts[si + 1]
        ink_ys = [y for y in range(c0, c1 + 1) if row_has_ink[y]]
        ink0, ink1 = (min(ink_ys), max(ink_ys)) if ink_ys else (y_top - 40, y_bot + 40)
        slice_y = [max(c0, ink0 - 14), min(c1, ink1 + 16)]
        assert slice_y[0] <= ink0 and slice_y[1] >= ink1, (
            f"Page {page_num} Sys {si}: slice {slice_y} clips ink [{ink0}..{ink1}]"
        )

        # Inter-string rows used to distinguish vertical barlines from wavy arpeggios
        gap_ys: List[int] = []
        for k in range(5):
            g0 = int(sys_lines[k]) + 3
            g1 = int(sys_lines[k + 1]) - 3
            gap_ys.extend(range(g0, g1 + 1))

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
                    side_dark = sum(
                        1
                        for y in gap_ys
                        if (buf[y * w + x - 3] < 160 or buf[y * w + x + 3] < 160)
                    )
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

        start_x = 152.0 if (page_num == 1 and si == 0) else 115.0
        boundaries = [start_x] + bar_xs[1:]
        bar_boxes = [[boundaries[i], boundaries[i + 1]] for i in range(len(boundaries) - 1)]

        sys_entries.append(
            {
                "page": page_num,
                "width": w,
                "height": h,
                "sliceY": slice_y,
                "inkY": [ink0, ink1],
                "staffY": [y_top, y_bot],
                "barBoxes": bar_boxes,
            }
        )

    return w, h, header_slice, sys_entries


def extract_note_events(measures: List[Measure], duration: float) -> List[Dict[str, object]]:
    """Build exact per-note playback timeline matching build_audio.py timing."""
    last_bar = measures[-1].bar
    raw_notes: List[Dict[str, object]] = []
    cursor_t = 120 / 480.0 * (60.0 / float(TEMPO_BPM))  # LEAD_IN_TICKS = 120

    for m_idx, meas in enumerate(measures):
        bpm = 60.0 if meas.bar == 44 else float(TEMPO_BPM)
        beats = 1.0 if meas.is_pickup else 4.0
        bar_dur = beats * (60.0 / bpm)
        sixteenth_sec = 60.0 / bpm / 4.0
        bar_start_slot = 12 if meas.is_pickup else 0

        for ev_idx, ev in enumerate(meas.events):
            ev_t = cursor_t + (ev.slot - bar_start_slot) * sixteenth_sec
            ev_dur = max(
                0.08,
                (duration - ev_t) if (meas.bar == last_bar and ev.fermata) else (ev.dur * sixteenth_sec),
            )
            if ev.rest:
                continue

            next_ev = None
            if ev_idx + 1 < len(meas.events):
                next_ev = meas.events[ev_idx + 1]
            elif m_idx + 1 < len(measures) and measures[m_idx + 1].events:
                next_ev = measures[m_idx + 1].events[0]

            slap_notes = [nt for nt in ev.notes if nt.role == "slap"]
            for nt in slap_notes:
                raw_notes.append(
                    {
                        "t": round(ev_t, 4),
                        "d": round(min(0.22, ev_dur), 4),
                        "s": nt.string,
                        "f": 0,
                        "r": "x",
                        "b": meas.bar,
                        "_pm": False,
                        "_final": False,
                    }
                )

            pitched = sorted((nt for nt in ev.notes if nt.role != "slap"), key=lambda x: -x.string)
            roll_ticks = 16 if ev.arpeggio else (6 if len(pitched) >= 2 else 0)
            tick_sec = (60.0 / bpm) / 480.0

            for idx, nt in enumerate(pitched):
                nt_t = ev_t + idx * roll_ticks * tick_sec
                role_code = {"melody": "m", "bass": "b", "inner": "i"}.get(nt.role, "m")
                item: Dict[str, object] = {
                    "t": round(nt_t, 4),
                    "d": round(min(0.18, ev_dur) if nt.palm_mute else ev_dur, 4),
                    "s": nt.string,
                    "f": nt.fret,
                    "r": role_code,
                    "b": meas.bar,
                    "_pm": nt.palm_mute,
                    "_final": (meas.bar == last_bar and ev.fermata),
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
                raw_notes.append(item)

        cursor_t = duration if meas.bar == 44 else (cursor_t + bar_dur)

    raw_notes.sort(key=lambda x: (float(x["t"]), -int(x["s"])))

    # Extend natural sustain per string up to next onset on same string
    by_string: Dict[int, List[int]] = {s: [] for s in range(1, 7)}
    for i, item in enumerate(raw_notes):
        if item["r"] != "x":
            by_string[int(item["s"])].append(i)

    for indices in by_string.values():
        for pos, i in enumerate(indices):
            item = raw_notes[i]
            t0 = float(item["t"])
            next_t = float(raw_notes[indices[pos + 1]]["t"]) if pos + 1 < len(indices) else duration
            if item["_pm"]:
                d_val = min(float(item["d"]), max(0.06, next_t - t0 - 0.015))
            else:
                cap = max(1.0, duration - t0) if item["_final"] else (1.8 if item["r"] in ("m", "b") else 1.1)
                max_ring = min(cap, max(0.06, next_t - t0 - 0.02))
                d_val = min(cap, max(float(item["d"]), max_ring))
                if pos + 1 < len(indices):
                    d_val = min(d_val, max(0.06, next_t - t0 - 0.02))
            item["d"] = round(max(0.06, d_val), 4)

    for item in raw_notes:
        item.pop("_pm", None)
        item.pop("_final", None)

    return raw_notes


def build_manifest() -> Path:
    measures: List[Measure] = build_measures()
    validate_score(measures)

    png_files = sorted(PREVIEWS_DIR.glob("page-*.png"), key=_page_sort_key)
    if not png_files:
        raise FileNotFoundError(f"No preview PNGs found in {PREVIEWS_DIR}")

    audio_path = PROJECT_DIR / MP3_NAME
    duration = probe_audio_duration(audio_path)

    all_raw_systems: List[Dict[str, object]] = []
    header_info: Optional[Dict[str, object]] = None
    page_rels: List[str] = []

    for page_idx, png_path in enumerate(png_files, start=1):
        page_rels.append(f"previews/{png_path.name}")
        w, h, header_slice, sys_entries = detect_page_layout(png_path, page_idx)
        if page_idx == 1 and header_slice is not None:
            header_info = {
                "page": 1,
                "width": w,
                "height": h,
                "sliceY": header_slice,
            }
        all_raw_systems.extend(sys_entries)

    total_bars_detected = sum(len(s["barBoxes"]) for s in all_raw_systems)
    if total_bars_detected != len(measures):
        raise AssertionError(
            f"Bar count mismatch: detected {total_bars_detected} bar boxes across PNGs, "
            f"expected {len(measures)} measures from score_data.py"
        )

    # Precompute bar timing and musical metadata
    cursor_t = 120 / 480.0 * (60.0 / TEMPO_BPM)  # LEAD_IN_TICKS = 120
    bar_records: List[Dict[str, object]] = []
    cur_section = "Intro"
    section_starts: List[Dict[str, object]] = []

    for meas in measures:
        if meas.section:
            cur_section = meas.section
            section_starts.append({"name": cur_section, "bar": meas.bar, "start": round(cursor_t, 4)})

        bpm = 60.0 if meas.bar == 44 else float(TEMPO_BPM)
        beats = 1.0 if meas.is_pickup else 4.0
        dur = beats * (60.0 / bpm)
        end_t = duration if meas.bar == 44 else (cursor_t + dur)

        chords = " → ".join(lbl for _, lbl in meas.chords) if meas.chords else ("弱起 Pick-up" if meas.is_pickup else "")
        cue = ""
        for (b, _slot), txt in PHRASE_CUES.items():
            if b == meas.bar:
                cue = txt

        bar_records.append(
            {
                "bar": meas.bar,
                "start": round(cursor_t, 4),
                "end": round(end_t, 4),
                "sec": cur_section,
                "chords": chords,
                "cue": cue,
            }
        )
        cursor_t = end_t

    # Combine detected barBoxes with bar_records
    systems_out: List[Dict[str, object]] = []
    bars_out: List[Dict[str, object]] = []
    bar_cursor = 0

    for sys_idx, raw_sys in enumerate(all_raw_systems):
        boxes = raw_sys["barBoxes"]
        sys_bars: List[Dict[str, object]] = []
        for x0, x1 in boxes:
            rec = dict(bar_records[bar_cursor])
            rec["sysIdx"] = sys_idx
            rec["page"] = raw_sys["page"]
            rec["x0"] = x0
            rec["x1"] = x1
            sys_bars.append(rec)
            bars_out.append(rec)
            bar_cursor += 1

        systems_out.append(
            {
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
            }
        )

    note_events = extract_note_events(measures, duration)

    manifest = {
        "version": 1,
        "title": TITLE,
        "subtitle": SUBTITLE,
        "composer": COMPOSER,
        "arrangerInfo": ARRANGER_INFO,
        "bpm": TEMPO_BPM,
        "capo": CAPO,
        "duration": duration,
        "audio": MP3_NAME,
        "pdf": PDF_NAME,
        "pages": page_rels,
        "header": header_info,
        "sections": section_starts,
        "systems": systems_out,
        "bars": bars_out,
        "notes": note_events,
    }

    OUTPUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Generated {OUTPUT_MANIFEST.name}: {len(systems_out)} systems, {len(bars_out)} bars, "
        f"{len(note_events)} notes, duration={duration:.2f}s across {len(page_rels)} pages."
    )
    return OUTPUT_MANIFEST


if __name__ == "__main__":
    build_manifest()
