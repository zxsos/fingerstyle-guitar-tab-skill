"""Synthesize Call_of_Silence_指弹吉他试听.mp3 with fingerstyle articulations."""

from __future__ import annotations

import math
import os
import random
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from score_data import CAPO, OPEN_TUNING_NO_CAPO, Measure, build_measures, validate_score

PROJECT_DIR = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_DIR / "build"
OUTPUT_MIDI = BUILD_DIR / "Call_of_Silence.mid"
RAW_WAV = BUILD_DIR / "Call_of_Silence_raw.wav"
OUTPUT_MP3 = PROJECT_DIR / "Call_of_Silence_指弹吉他试听.mp3"


def resolve_fluidsynth_and_sf2() -> Tuple[Path, Path, Dict[str, str]]:
    """Resolve FluidSynth binary, FluidR3_GM.sf2, and env from TAB_TOOLS_ROOT, .tools/root, repo root, or system."""
    env = os.environ.copy()
    candidates: List[Path] = []
    if env.get("TAB_TOOLS_ROOT"):
        candidates.append(Path(env["TAB_TOOLS_ROOT"]).expanduser())
    candidates.extend([
        PROJECT_DIR / ".tools" / "root",
        PROJECT_DIR.parent.parent / ".tools" / "root",
    ])

    fs_bin: Optional[Path] = None
    for tools_root in candidates:
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
        raise FileNotFoundError(
            "FluidSynth not found in TAB_TOOLS_ROOT, .tools/root, repo root .tools/root, or system PATH. "
            "Run `bash skills/fingerstyle-guitar-tab/scripts/setup_tools.sh` to install rootless tools."
        )

    sf2_candidates: List[Path] = []
    if env.get("TAB_SF2_PATH"):
        sf2_candidates.append(Path(env["TAB_SF2_PATH"]).expanduser())
    for tools_root in candidates:
        sf2_candidates.extend([
            tools_root / "usr" / "share" / "sounds" / "sf2" / "FluidR3_GM.sf2",
            tools_root / "usr" / "share" / "soundfonts" / "FluidR3_GM.sf2",
        ])
    sf2_candidates.extend([
        Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
        Path("/usr/share/soundfonts/FluidR3_GM.sf2"),
        Path("/opt/homebrew/share/soundfonts/FluidR3_GM.sf2"),
        Path("/usr/local/share/soundfonts/FluidR3_GM.sf2"),
    ])

    sf2_path = next((p for p in sf2_candidates if p.is_file()), None)
    if sf2_path is None:
        raise FileNotFoundError(
            "FluidR3_GM.sf2 SoundFont not found in local tools or standard system paths. "
            "Run `bash skills/fingerstyle-guitar-tab/scripts/setup_tools.sh` to install rootless tools."
        )

    return fs_bin, sf2_path, env

PPQ = 480
TICKS_PER_16TH = PPQ // 4  # 120 ticks per 16th note
LEAD_IN_TICKS = 120        # ~0.21s clean lead-in before pickup


def vlq(value: int) -> bytes:
    """Encode non-negative integer as MIDI Variable-Length Quantity."""
    buf = [value & 0x7F]
    value >>= 7
    while value:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.reverse()
    return bytes(buf)


def build_midi_track(events: List[Tuple[int, int, bytes]]) -> bytes:
    """Sort (tick, order, raw_msg) events and encode an MTrk chunk."""
    events_sorted = sorted(events, key=lambda x: (x[0], x[1]))
    body = bytearray()
    prev_tick = 0
    for tick, _order, msg in events_sorted:
        delta = max(0, tick - prev_tick)
        prev_tick = tick
        body.extend(vlq(delta))
        body.extend(msg)
    body.extend(vlq(0))
    body.extend(b"\xff\x2f\x00")  # End of Track
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def write_midi_file(measures: List[Measure], path: Path) -> List[float]:
    """Generate Type-1 MIDI with H/P/S, arpeggio, <12> harmonics, P.M., and collect slap times."""
    meta_events: List[Tuple[int, int, bytes]] = []
    ch_events: Dict[int, List[Tuple[int, int, bytes]]] = {0: [], 1: [], 2: [], 3: []}
    slap_times: List[float] = []

    # Time signature 4/4, Key signature 3 sharps (A major / F# minor), Tempo 72 BPM
    meta_events.append((0, 0, b"\xff\x58\x04\x04\x02\x18\x08"))
    meta_events.append((0, 1, b"\xff\x59\x02\x03\x00"))
    us_per_beat_72 = round(60_000_000 / 72.0)  # 833333
    meta_events.append((0, 2, b"\xff\x51\x03" + us_per_beat_72.to_bytes(3, "big")))

    # Channel setup (Order 0..4 at tick 0, Chorus CC93 = 0 on all channels)
    # Ch 0: Prog 25 (Steel String Guitar)
    # Ch 1: Prog 24 (Nylon String Guitar)
    # Ch 2: Prog 46 (Orchestral Harp -> clean bell harmonic layer at exact concert pitch)
    # Ch 3: Prog 28 (Palm Muted Guitar)
    chan_cfg = [
        (0, 25, 112, 60, 22),
        (1, 24, 90, 68, 24),
        (2, 46, 92, 64, 32),
        (3, 28, 104, 62, 14),
    ]
    for ch, prog, vol, pan, rev in chan_cfg:
        ch_events[ch].extend([
            (0, 0, bytes([0xC0 | ch, prog])),
            (0, 1, bytes([0xB0 | ch, 7, vol])),
            (0, 2, bytes([0xB0 | ch, 10, pan])),
            (0, 3, bytes([0xB0 | ch, 91, rev])),
            (0, 4, bytes([0xB0 | ch, 93, 0])),
        ])

    # Active note per string 1..6: (concert_midi, on_tick, role, channels_used, max_age_ticks)
    active: Dict[int, Tuple[int, int, str, Tuple[int, ...], int]] = {}

    def stop_string(s: int, off_tick: int) -> None:
        if s not in active:
            return
        midi_pitch, on_tick, _role, channels_used, _max_age = active.pop(s)
        safe_off = max(on_tick + 20, off_tick)
        for ch in channels_used:
            ch_events[ch].append((safe_off, 10, bytes([0x80 | ch, midi_pitch, 0])))

    def damp_aged_strings(cur_tick: int) -> None:
        for s in list(active.keys()):
            _m, on_t, _r, _chs, max_age = active[s]
            if cur_tick - on_t >= max_age:
                stop_string(s, cur_tick - 8)

    cursor_tick = LEAD_IN_TICKS
    for meas in measures:
        chord_slots = {sl for sl, _lbl in meas.chords}
        bar_start_slot = 12 if meas.is_pickup else 0

        # Gentle ritardando ONLY on the final closing chord M44
        if meas.bar == 44:
            us_per_beat_end = round(60_000_000 / 60.0)
            meta_events.append((cursor_tick, 2, b"\xff\x51\x03" + us_per_beat_end.to_bytes(3, "big")))

        for ev_idx, ev in enumerate(meas.events):
            ev_tick = cursor_tick + (ev.slot - bar_start_slot) * TICKS_PER_16TH
            damp_aged_strings(ev_tick)

            if ev.rest:
                for s in list(active.keys()):
                    stop_string(s, ev_tick)
                continue

            # Determine if this event's melody note is the target of a link_tech ("H", "P", "S")
            incoming_link: Optional[str] = None
            incoming_string: Optional[int] = None
            if ev_idx > 0 and meas.events[ev_idx - 1].link_tech in ("H", "P", "S"):
                prev_ev = meas.events[ev_idx - 1]
                prev_mel = [x for x in prev_ev.notes if x.role == "melody"]
                if prev_mel:
                    incoming_link = prev_ev.link_tech
                    incoming_string = prev_mel[0].string

            # Record any thumb slap (X) timestamps (no MIDI note emitted)
            for nt in ev.notes:
                if nt.role == "slap":
                    slap_times.append(ev_tick / PPQ * (60.0 / 72.0))

            pitched_notes = [nt for nt in ev.notes if nt.role != "slap"]
            if not pitched_notes:
                continue

            # On harmonic changes, cleanly damp previous bass & inner accompaniment strings
            is_chord_change = (ev.slot in chord_slots) and not (meas.bar == 1 and ev.slot == 0)
            if is_chord_change:
                for s in list(active.keys()):
                    if active[s][2] in ("bass", "inner"):
                        stop_string(s, ev_tick - 10)

            # Prevent stepwise 2nd-interval clashes between consecutive melody notes on adjacent strings
            new_melodies = [nt for nt in pitched_notes if nt.role == "melody"]
            if new_melodies:
                new_mel_midi = new_melodies[0].concert_midi
                for s in list(active.keys()):
                    old_midi, _old_on, old_role, _chs, _ma = active[s]
                    if old_role == "melody" and abs(new_mel_midi - old_midi) <= 2:
                        if not (incoming_link is not None and s == incoming_string):
                            stop_string(s, ev_tick - 10)

            # Damp ringing melody string if an incoming bass/inner note lands within <= 1 semitone
            for nt in pitched_notes:
                if nt.role in ("bass", "inner"):
                    for s in list(active.keys()):
                        old_midi, _old_on, old_role, _chs, _ma = active[s]
                        if old_role == "melody" and abs(nt.concert_midi - old_midi) <= 1:
                            stop_string(s, ev_tick - 10)

            # Pluck notes lowest string -> highest string with subtle roll (wider when arpeggio=True)
            ordered = sorted(pitched_notes, key=lambda x: -x.string)
            n_notes = len(ordered)
            if ev.arpeggio:
                roll_step = 16
            else:
                roll_step = 6 if n_notes >= 2 else 0

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
                is_legato_dest = (incoming_link is not None and nt.string == incoming_string)

                if nt.string in active:
                    if is_legato_dest:
                        # 14ms legato overlap into Hammer-on / Pull-off / Slide destination
                        stop_string(nt.string, on_tick + 8)
                    else:
                        stop_string(nt.string, on_tick - 8)

                midi_p = nt.concert_midi

                # Grace-note slide-in (S)
                if nt.grace_from is not None:
                    grace_p = OPEN_TUNING_NO_CAPO[nt.string] + CAPO + nt.grace_from
                    g_on = max(0, on_tick - 26)
                    g_off = on_tick + 6
                    g_v0 = max(32, min(105, round(92 * meas.dynamic * 0.70)))
                    g_v1 = max(20, min(80, round(g_v0 * 0.50)))
                    ch_events[0].append((g_on, 18, bytes([0x90, grace_p, g_v0])))
                    ch_events[0].append((g_off, 19, bytes([0x80, grace_p, 0])))
                    ch_events[1].append((g_on, 18, bytes([0x91, grace_p, g_v1])))
                    ch_events[1].append((g_off, 19, bytes([0x81, grace_p, 0])))

                if nt.harmonic:
                    # Natural 12th-fret harmonic: Harp (Ch 2) + soft Nylon body (Ch 1)
                    v_ch2 = max(40, min(112, round(86 * meas.dynamic)))
                    v_ch1 = max(24, min(80, round(48 * meas.dynamic)))
                    ch_events[2].append((on_tick, 20, bytes([0x92, midi_p, v_ch2])))
                    ch_events[1].append((on_tick, 20, bytes([0x91, midi_p, v_ch1])))
                    active[nt.string] = (midi_p, on_tick, nt.role, (2, 1), 3000)
                elif nt.palm_mute:
                    # Palm mute (P.M.): Muted Guitar (Ch 3) + short gated Steel transient (Ch 0)
                    v_ch3 = max(36, min(108, round(80 * meas.dynamic)))
                    v_ch0 = max(24, min(80, round(48 * meas.dynamic)))
                    ch_events[3].append((on_tick, 20, bytes([0x93, midi_p, v_ch3])))
                    ch_events[0].append((on_tick, 20, bytes([0x90, midi_p, v_ch0])))
                    ch_events[0].append((on_tick + 95, 25, bytes([0x80, midi_p, 0])))
                    active[nt.string] = (midi_p, on_tick, nt.role, (3,), 280)
                else:
                    base_vel = {"melody": 98, "bass": 74, "inner": 48}[nt.role]
                    raw_vel = (base_vel + beat_acc) * meas.dynamic
                    if nt.grace_from is not None:
                        raw_vel *= 0.94
                    if is_legato_dest:
                        if incoming_link == "H":
                            vel_ch0 = max(32, min(110, round(raw_vel * 0.74)))
                            vel_ch1 = max(24, min(90, round(raw_vel * 0.52)))
                        elif incoming_link == "P":
                            vel_ch0 = max(32, min(110, round(raw_vel * 0.70)))
                            vel_ch1 = max(24, min(90, round(raw_vel * 0.52)))
                        else:  # "S"
                            vel_ch0 = max(32, min(110, round(raw_vel * 0.84)))
                            vel_ch1 = max(24, min(90, round(raw_vel * 0.45)))
                    else:
                        vel_ch0 = max(36, min(114, round(raw_vel)))
                        vel_ch1 = max(24, min(90, round(vel_ch0 * 0.38)))

                    ch_events[0].append((on_tick, 20, bytes([0x90, midi_p, vel_ch0])))
                    ch_events[1].append((on_tick, 20, bytes([0x91, midi_p, vel_ch1])))
                    max_age = 2880 if nt.role == "melody" else 1920
                    active[nt.string] = (midi_p, on_tick, nt.role, (0, 1), max_age)

        bar_sixteenths = 4 if meas.is_pickup else 16
        cursor_tick += bar_sixteenths * TICKS_PER_16TH

    # Let final M44 chord ring warmly, then release smoothly
    end_tick = cursor_tick + 720
    for s in list(active.keys()):
        stop_string(s, end_tick)

    tail_tick = end_tick + 960
    for ch in (0, 1, 2, 3):
        ch_events[ch].append((tail_tick, 30, bytes([0xB0 | ch, 123, 0])))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, 5, PPQ)
    midi_bytes = (
        header
        + build_midi_track(meta_events)
        + build_midi_track(ch_events[0])
        + build_midi_track(ch_events[1])
        + build_midi_track(ch_events[2])
        + build_midi_track(ch_events[3])
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(midi_bytes)
    return slap_times


def inject_thumb_slaps(wav_path: Path, slap_times: List[float]) -> None:
    """Mix subtle acoustic guitar thumb-slap (拍弦 X) transients into FluidSynth WAV."""
    if not slap_times:
        return

    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)

    assert n_channels == 2 and sampwidth == 2 and sr == 44100, (
        f"Unexpected WAV format: ch={n_channels}, sw={sampwidth}, sr={sr}"
    )

    pcm = list(struct.unpack(f"<{n_frames * 2}h", raw_bytes))
    slap_len = int(0.030 * sr)  # 1323 samples (30ms)

    for idx, t_sec in enumerate(slap_times):
        start_frame = int(round(t_sec * sr))
        if start_frame < 0 or start_frame >= n_frames:
            continue

        rng = random.Random(42 + idx)
        lp = 0.0
        for j in range(slap_len):
            f_idx = start_frame + j
            if f_idx >= n_frames:
                break
            t = j / sr
            # 2ms half-cosine fade-in ramp
            ramp = 0.5 - 0.5 * math.cos(math.pi * t / 0.002) if t < 0.002 else 1.0
            # Warm wooden soundboard thump
            thump = 0.038 * math.sin(2.0 * math.pi * (112.0 * t - 320.0 * t * t)) * math.exp(-t / 0.0085)
            # 1-pole low-passed (<3.2kHz) fret-touch click
            lp = 0.68 * lp + 0.32 * (rng.uniform(-1.0, 1.0))
            click_wave = 0.65 * math.cos(2.0 * math.pi * 2100.0 * t) + 0.35 * lp
            click = 0.020 * click_wave * math.exp(-t / 0.0026)

            val = ramp * (thump + click) * 32767.0
            li = 2 * f_idx
            ri = li + 1
            pcm[li] = max(-32767, min(32767, int(round(pcm[li] + 1.02 * val))))
            pcm[ri] = max(-32767, min(32767, int(round(pcm[ri] + 0.98 * val))))

    out_bytes = struct.pack(f"<{len(pcm)}h", *pcm)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(out_bytes)


def probe_wav_duration(wav_path: Path) -> float:
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(wav_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


def build_mp3() -> Path:
    measures = build_measures()
    validate_score(measures)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    slap_times = write_midi_file(measures, OUTPUT_MIDI)
    print(f"Wrote MIDI: {OUTPUT_MIDI} ({OUTPUT_MIDI.stat().st_size} bytes), {len(slap_times)} thumb-slap cues")

    fluidsynth_bin, sf2_path, env = resolve_fluidsynth_and_sf2()

    # Render MIDI -> 44.1 kHz stereo WAV (-C 0 disables chorus so zero artificial detune!)
    fs_cmd = [
        str(fluidsynth_bin),
        "-ni",
        "-C", "0",
        "-R", "1",
        "-g", "0.70",
        "-r", "44100",
        "-F", str(RAW_WAV),
        str(sf2_path),
        str(OUTPUT_MIDI),
    ]
    res = subprocess.run(fs_cmd, env=env, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        print(res.stdout, res.stderr)
        raise RuntimeError(f"FluidSynth failed with exit code {res.returncode}")

    inject_thumb_slaps(RAW_WAV, slap_times)

    wav_dur = probe_wav_duration(RAW_WAV)
    target_dur = min(wav_dur, 151.8)
    fade_start = max(145.0, target_dur - 3.6)
    print(f"Rendered raw WAV: {RAW_WAV} ({wav_dur:.2f}s), mastering to {target_dur:.2f}s (fade at {fade_start:.2f}s)")

    # Gentle warm acoustic guitar mastering -> 44.1 kHz, 320 kbps stereo MP3
    af = (
        "highpass=f=55,"
        "equalizer=f=210:t=q:w=1.2:g=1.5,"
        "equalizer=f=3400:t=q:w=1.4:g=-1.8,"
        "aecho=0.85:0.88:35|62:0.12|0.07,"
        "loudnorm=I=-16:TP=-1.5:LRA=9,"
        f"afade=t=out:st={fade_start:.2f}:d=3.5"
    )
    ff_cmd = [
        "ffmpeg",
        "-y",
        "-i", str(RAW_WAV),
        "-t", f"{target_dur:.2f}",
        "-af", af,
        "-ar", "44100",
        "-codec:a", "libmp3lame",
        "-b:a", "320k",
        "-map_metadata", "-1",
        str(OUTPUT_MP3),
    ]
    ff_res = subprocess.run(ff_cmd, capture_output=True, text=True, check=False)
    if ff_res.returncode != 0:
        print(ff_res.stderr)
        raise RuntimeError(f"FFmpeg mastering failed with exit code {ff_res.returncode}")

    print(f"Generated MP3: {OUTPUT_MP3} ({OUTPUT_MP3.stat().st_size} bytes)")
    return OUTPUT_MP3


if __name__ == "__main__":
    build_mp3()
