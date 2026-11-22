"""Generate sanitized sync_manifest.json from previews/page-*.png, MP3, and score_data.py."""

from __future__ import annotations

import json
import re
import struct
import subprocess
import tempfile
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


def _read_vlq(data: bytes, p: int) -> Tuple[int, int]:
    val = 0
    while True:
        b = data[p]
        p += 1
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            return val, p


def parse_midi_note_spans(
    path: Path,
) -> Tuple[int, List[Tuple[int, int]], Dict[Tuple[int, int], List[Tuple[int, int]]]]:
    """Parse a Type-1 MIDI file into (ppq, tempo_map, {(channel, pitch): [(on_tick, off_tick), ...]})."""
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"Not a MIDI file: {path}")
    _fmt, ntracks, ppq = struct.unpack(">HHH", data[8:14])
    pos = 8 + struct.unpack(">I", data[4:8])[0]

    tempo_map: List[Tuple[int, int]] = []
    ons: Dict[Tuple[int, int], List[int]] = {}
    offs: Dict[Tuple[int, int], List[int]] = {}

    for _ in range(ntracks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError(f"Corrupt MIDI track header at byte {pos}")
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        p = pos + 8
        end = p + length
        tick = 0
        running = 0
        while p < end:
            delta, p = _read_vlq(data, p)
            tick += delta
            status = data[p]
            if status == 0xFF:
                mtype = data[p + 1]
                mlen, p = _read_vlq(data, p + 2)
                if mtype == 0x51:
                    tempo_map.append((tick, int.from_bytes(data[p:p + 3], "big")))
                p += mlen
                continue
            if status in (0xF0, 0xF7):
                slen, p = _read_vlq(data, p + 1)
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
    """Return a tick -> seconds converter honouring every tempo change."""
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


def extract_note_events(measures: List[Measure], duration: float) -> List[Dict[str, object]]:
    """Build the per-note timeline from the exact MIDI that the MP3 is rendered from.

    Onsets and sustain lengths ("d") are read back from build_audio.write_midi_file, so every
    note's hold length equals what is actually audible (chord-change damping, melody clash
    cut-offs, legato hand-offs, palm-mute and harmonic ring times, final ritardando).
    """
    import build_audio  # local module in the same src/ directory

    with tempfile.TemporaryDirectory() as td:
        midi_path = Path(td) / "timeline.mid"
        build_audio.write_midi_file(measures, midi_path)
        ppq, tempo_map, spans = parse_midi_note_spans(midi_path)

    tick_to_sec = make_tick_to_sec(ppq, tempo_map)
    ticks_per_16th = ppq // 4
    span_index: Dict[Tuple[int, int, int], int] = {}
    for (ch, pitch), pairs in spans.items():
        for on_t, off_t in pairs:
            span_index[(ch, pitch, on_t)] = off_t

    notes: List[Dict[str, object]] = []
    missing: List[str] = []
    cursor_tick = int(build_audio.LEAD_IN_TICKS)

    for m_idx, meas in enumerate(measures):
        bar_start_slot = 12 if meas.is_pickup else 0
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
                    notes.append(
                        {"t": round(ev_sec, 4), "d": 0.12, "s": nt.string, "f": 0, "r": "x", "b": meas.bar}
                    )

            pitched = sorted((nt for nt in ev.notes if nt.role != "slap"), key=lambda x: -x.string)
            roll_step = 16 if ev.arpeggio else (6 if len(pitched) >= 2 else 0)

            for idx, nt in enumerate(pitched):
                on_tick = ev_tick + idx * roll_step
                channel = 2 if nt.harmonic else (3 if nt.palm_mute else 0)
                off_tick = span_index.get((channel, nt.concert_midi, on_tick))
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
                        dest = next(
                            (n2 for n2 in next_ev.notes if n2.role == "melody" and n2.string == nt.string),
                            None,
                        )
                        if dest is not None:
                            item["to"] = dest.fret
                if nt.harmonic:
                    item["h"] = 1
                if nt.palm_mute:
                    item["pm"] = 1
                if ev.arpeggio:
                    item["arp"] = 1
                notes.append(item)

        cursor_tick += (4 if meas.is_pickup else 16) * ticks_per_16th

    if missing:
        raise AssertionError(
            f"{len(missing)} notes have no matching MIDI span (timeline drifted from build_audio.py): {missing[:5]}"
        )

    notes.sort(key=lambda x: (float(x["t"]), -int(x["s"])))
    return notes


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
