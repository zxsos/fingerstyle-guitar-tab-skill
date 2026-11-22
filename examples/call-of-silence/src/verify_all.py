"""End-to-end verification for Call of Silence 6-line TAB PDF & preview MP3."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import subprocess
import urllib.request
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from score_data import DIATONIC_PCS, build_measures, validate_score

PROJECT_DIR = Path(__file__).resolve().parent.parent
PDF_PATH = PROJECT_DIR / "Call_of_Silence_指弹吉他六线谱.pdf"
MP3_PATH = PROJECT_DIR / "Call_of_Silence_指弹吉他试听.mp3"

SR = 11025


def decode_mono_pcm(source: Path | bytes) -> Tuple[int, ...]:
    if isinstance(source, Path):
        cmd = ["ffmpeg", "-i", str(source), "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"]
        res = subprocess.run(cmd, capture_output=True, check=True)
    else:
        cmd = ["ffmpeg", "-i", "pipe:0", "-ac", "1", "-ar", str(SR), "-f", "s16le", "pipe:1"]
        res = subprocess.run(cmd, input=source, capture_output=True, check=True)
    n = len(res.stdout) // 2
    return struct.unpack(f"<{n}h", res.stdout)


def chroma_in_segment(
    samples: Tuple[int, ...], t0: float, dur: float, lo_midi: int = 40, hi_midi: int = 81
) -> List[float]:
    i0 = max(0, int(t0 * SR))
    i1 = min(len(samples), int((t0 + dur) * SR))
    seg = samples[i0:i1]
    N = len(seg)
    if N < 64:
        return [0.0] * 12
    wseg = [seg[i] * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / (N - 1))) for i in range(N)]
    spec: Dict[int, float] = {}
    for m in range(lo_midi, hi_midi + 1):
        if (m % 12) not in DIATONIC_PCS:
            continue
        f = 440.0 * (2.0 ** ((m - 69) / 12.0))
        w = 2.0 * math.pi * f / SR
        c = 2.0 * math.cos(w)
        q1 = q2 = 0.0
        for x in wseg:
            q0 = c * q1 - q2 + x
            q2 = q1
            q1 = q0
        spec[m] = math.sqrt(max(0.0, q1 * q1 + q2 * q2 - q1 * q2 * c)) / N

    # Suppress 2nd/3rd/4th harmonic leakage from lower fundamentals
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


def cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def verify_score() -> None:
    measures = build_measures()
    tech_counts = validate_score(measures)
    for tech, cnt in tech_counts.items():
        assert cnt >= 1, f"Missing technique {tech}"

    def melody_pitches(bar_idx: int) -> List[int]:
        return [
            nt.concert_midi
            for e in measures[bar_idx].events
            for nt in e.notes
            if nt.role == "melody" and not nt.harmonic
        ]

    landmarks = {
        0: [66, 69],                                         # M00 Intro pickup: F#4, A4
        1: [73, 71, 68, 69],                                 # M01 Intro motif: C#5, B4, G#4, A4
        10: [73, 69, 73, 71, 64, 66],                        # M10 Verse hook
        28: [71, 69, 69, 64, 69, 68, 69],                    # M28 Chorus I: "know you're reborn tonight"
        36: [66, 64, 62, 71, 69, 68, 64],                    # M36 Chorus II: "more, oh my beloved"
        38: [66, 64, 62, 66, 68, 68, 66, 64, 71, 69],        # M38 Chorus II climax: "be proud and fight it out"
    }
    for bar_idx, expected in landmarks.items():
        actual = melody_pitches(bar_idx)
        assert actual == expected, f"Landmark M{bar_idx:02d} melody drift: expected {expected}, got {actual}"

    total_notes = sum(len(e.notes) for meas in measures for e in meas.events)
    print(
        f"[PASS] Score integrity: {len(measures)} measures (M00..M44), {total_notes} notes/slaps, "
        f"fret span <= 3, 100% A/F#m diatonic, 0 voice crossings, {len(landmarks)} landmark bars verified, "
        f"techniques={tech_counts}."
    )


def verify_pdf() -> None:
    assert PDF_PATH.exists(), f"Missing PDF: {PDF_PATH}"
    size = PDF_PATH.stat().st_size
    assert size > 30_000, f"PDF too small: {size} bytes"

    # Count pages via Ghostscript
    gs_cmd = [
        "gs",
        "-q",
        "-dNODISPLAY",
        "-dNOSAFER",
        "-c",
        f"({PDF_PATH}) (r) file runpdfbegin pdfpagecount = quit",
    ]
    res = subprocess.run(gs_cmd, capture_output=True, text=True, check=True)
    pages = int(res.stdout.strip())
    assert 2 <= pages <= 4, f"Expected 2-4 pages, got {pages}"

    # Ensure zero local path or point-and-click URI leaks in PDF bytes & compressed streams
    pdf_bytes = PDF_PATH.read_bytes()
    leak_pat = re.compile(
        b"(textedit://|" + b"/" + b"home/|" + b"/" + b"Users/|C:\\\\" + b"Users\\\\)"
    )
    leaks = leak_pat.findall(pdf_bytes)
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.S):
        try:
            dec = zlib.decompress(m.group(1))
            leaks.extend(leak_pat.findall(dec))
        except zlib.error:
            pass
    assert not leaks, f"PDF contains unsanitized local path leaks ({len(leaks)} hits): {leaks[:3]}"

    print(f"[PASS] PDF verified: {PDF_PATH.name} ({size:,} bytes, {pages} pages, sanitized (0 path leaks)).")


def verify_mp3() -> Tuple[int, ...]:
    assert MP3_PATH.exists(), f"Missing MP3: {MP3_PATH}"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(MP3_PATH)],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(probe.stdout)
    fmt = info["format"]
    st = info["streams"][0]
    dur = float(fmt["duration"])
    sr = int(st["sample_rate"])
    ch = int(st["channels"])
    br = int(fmt["bit_rate"])
    assert sr == 44100, f"Expected 44100 Hz, got {sr}"
    assert ch == 2, f"Expected stereo, got {ch}"
    assert br >= 310_000, f"Expected ~320 kbps, got {br}"
    assert 148.0 <= dur <= 153.5, f"Expected ~149-152s duration, got {dur:.2f}s"

    # Peak level check via ffmpeg astats
    stat_res = subprocess.run(
        ["ffmpeg", "-i", str(MP3_PATH), "-af", "astats=metadata=1:reset=0", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    peak_db = None
    for line in stat_res.stderr.splitlines():
        if "Peak level dB:" in line:
            val = float(line.split(":")[-1].strip())
            if peak_db is None or val > peak_db:
                peak_db = val
    assert peak_db is not None and peak_db < -1.0, f"Peak clipping risk: {peak_db} dBFS"

    # Stereo phase correlation & L/R balance check
    st_cmd = ["ffmpeg", "-i", str(MP3_PATH), "-ac", "2", "-ar", "22050", "-f", "s16le", "pipe:1"]
    st_res = subprocess.run(st_cmd, capture_output=True, check=True)
    lr = struct.unpack(f"<{len(st_res.stdout) // 2}h", st_res.stdout)
    left = lr[0::2]
    right = lr[1::2]
    l2 = sum(x * x for x in left)
    r2 = sum(x * x for x in right)
    lr_dot = sum(x * y for x, y in zip(left, right))
    lr_diff_db = abs(10.0 * math.log10((l2 + 1e-9) / (r2 + 1e-9)))
    phase_corr = lr_dot / (math.sqrt(l2 * r2) + 1e-9)
    assert lr_diff_db < 1.5, f"Stereo L/R imbalance too high: {lr_diff_db:.2f} dB"
    assert phase_corr > 0.50, f"Stereo phase cancellation risk: {phase_corr:.3f}"

    samples = decode_mono_pcm(MP3_PATH)
    bar_dur = 4.0 * 60.0 / 72.0
    t_b1 = (120 + 480) / 480.0 * (60.0 / 72.0)  # lead-in (1 beat/4 = 0.2083s) + M00 (1 beat = 0.8333s) = 1.0417s
    for b in range(1, 45):
        i0 = int((t_b1 + (b - 1) * bar_dur) * SR)
        i1 = min(len(samples), int((t_b1 + b * bar_dur) * SR))
        seg = samples[i0:i1]
        rms_db = 20.0 * math.log10(max(1.0, math.sqrt(sum(x * x for x in seg) / len(seg))) / 32768.0)
        assert rms_db > -35.0, f"Bar {b} unexpectedly silent: {rms_db:.1f} dBFS"

    # Global diatonic chroma energy share (all 12 chromatic pitch classes)
    pc_e = [0.0] * 12
    for b in range(1, 44):
        i0 = int((t_b1 + (b - 1) * bar_dur) * SR)
        i1 = int((t_b1 + b * bar_dur) * SR)
        sb = samples[i0:i1]
        nb = len(sb)
        for m in range(42, 81):
            f = 440.0 * (2.0 ** ((m - 69) / 12.0))
            w = 2.0 * math.pi * f / SR
            c = 2.0 * math.cos(w)
            q1 = q2 = 0.0
            for x in sb:
                q0 = c * q1 - q2 + x
                q2 = q1
                q1 = q0
            pc_e[m % 12] += math.sqrt(max(0.0, q1 * q1 + q2 * q2 - q1 * q2 * c)) / nb
    tot_e = sum(pc_e) or 1.0
    diatonic_energy = sum(pc_e[pc] for pc in DIATONIC_PCS) / tot_e
    assert diatonic_energy > 0.85, f"Non-diatonic harmonic drift: {diatonic_energy:.3f}"

    print(
        f"[PASS] MP3 audio verified: {dur:.2f}s, {sr} Hz, {ch}ch, {br // 1000} kbps, "
        f"peak {peak_db:.2f} dBFS, L/R diff {lr_diff_db:.2f} dB, phase corr {phase_corr:.3f}, "
        f"diatonic share {diatonic_energy * 100:.1f}%."
    )
    return samples


def verify_against_reference(our_samples: Tuple[int, ...], ref_source: str) -> None:
    if ref_source.startswith(("http://", "https://")):
        req = urllib.request.Request(ref_source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            ref_bytes = resp.read()
        ref_samples = decode_mono_pcm(ref_bytes)
    else:
        ref_path = Path(ref_source).expanduser()
        assert ref_path.is_file(), f"Reference audio file not found: {ref_path}"
        ref_samples = decode_mono_pcm(ref_path)

    bar_dur = 4.0 * 60.0 / 72.0
    s8 = bar_dur / 8.0
    t_our_b1 = (120 + 480) / 480.0 * (60.0 / 72.0)  # 1.0417s
    t_ref_b1 = 0.0439 + (60.0 / 72.0)               # 0.8772s

    sections = [
        ("Intro (M01-M09)", range(1, 10)),
        ("Verse (M10-M17)", range(10, 18)),
        ("Interlude (M18-M27)", range(18, 28)),
        ("Chorus I (M28-M35)", range(28, 36)),
        ("Chorus II (M36-M43)", range(36, 44)),
    ]

    all_sims: List[float] = []
    for sec_name, bars in sections:
        sec_sims = []
        for b in bars:
            slot_sims = []
            for k in range(8):
                c_our = chroma_in_segment(our_samples, t_our_b1 + (b - 1) * bar_dur + k * s8 + 0.015, 0.18)
                c_ref = chroma_in_segment(ref_samples, t_ref_b1 + (b - 1) * bar_dur + k * s8 + 0.015, 0.18)
                slot_sims.append(cosine(c_our, c_ref))
            b_avg = sum(slot_sims) / len(slot_sims)
            sec_sims.append((b, b_avg))
            all_sims.append(b_avg)
        avg_sim = sum(s for _, s in sec_sims) / len(sec_sims)
        min_bar, min_sim = min(sec_sims, key=lambda x: x[1])
        print(f"  {sec_name:22s}: mean 8th-slot cosine={avg_sim:.3f} (min M{min_bar:02d}={min_sim:.3f})")

    overall = sum(all_sims) / len(all_sims)
    assert overall >= 0.80, f"Overall chroma alignment too low: {overall:.3f}"
    print(f"[PASS] Reference score alignment verified across M01..M43: overall mean cosine = {overall:.3f}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Call of Silence TAB PDF and preview MP3.")
    parser.add_argument(
        "--ref",
        default=os.environ.get("TAB_REF_AUDIO"),
        help="Optional local path or URL of reference audio for Gate 4 chroma alignment (or set TAB_REF_AUDIO).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    verify_score()
    verify_pdf()
    our_pcm = verify_mp3()
    if args.ref:
        verify_against_reference(our_pcm, args.ref)
    else:
        print("[SKIP] Gate 4 (reference alignment): pass --ref <audio_path_or_url> or set TAB_REF_AUDIO to enable.")
    print("\nALL VERIFICATION CHECKS PASSED.")
