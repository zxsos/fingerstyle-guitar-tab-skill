# Audio Synthesis, Mastering & 4-Gate Verification

## 1. Multi-Channel Type-1 MIDI (`PPQ = 480`)

Write a 5-track Type-1 MIDI file (1 tempo/meta track + 4 channel tracks) using `concert_midi` pitches and `LEAD_IN_TICKS = 120` (~0.21s clean silence before the first note):

| Channel | GM Program | Volume / Pan / Reverb | Purpose |
| :--- | :--- | :--- | :--- |
| Ch 0 | 25 (Steel Guitar) | `vol=112, pan=60, rev=22` | Primary plucked melody, bass, inner accompaniment |
| Ch 1 | 24 (Nylon Guitar) | `vol=90, pan=68, rev=24` | Warm body layer blended softly under steel & harmonics |
| Ch 2 | 46 (Orchestral Harp) | `vol=92, pan=64, rev=32` | Bell-like transient for `<12>` natural harmonics |
| Ch 3 | 28 (Palm Muted Guitar) | `vol=104, pan=62, rev=14` | `P.M.` notes (layered with a 95-tick gated Ch 0 attack) |

Send `CC93 = 0` (Chorus Depth = 0) at tick `0` on all channels and keep `Order 0..4` sorted before note events.

## 2. Physical Guitar Articulation Rules

Track active ringing notes per string (`active[string]`):

- **String re-pluck & legato:** Stop any prior active note on the same string before starting the new note:
  - Normal pluck: stop at `on_tick - 8` ticks.
  - Legato destination (`H`, `P`, `S`): stop at `on_tick + 8` ticks (~14ms overlap) and scale destination velocity (`H: *0.74`, `P: *0.70`, `S: *0.84`).
- **Chord change damping:** On harmonic chord changes (`ev.slot in chord_slots`), damp ringing `"bass"` and `"inner"` strings at `ev_tick - 10`.
- **2nd-interval clash prevention:** If a new melody note is within `<= 2` semitones of an adjacent ringing melody string (and not a legato target), damp the previous melody string at `ev_tick - 10`.
- **String roll & beat accents:** Sort simultaneous notes lowest string to highest (`key=lambda x: -x.string`) with `roll_step = 16` ticks for `arpeggio=True` and `6` ticks for multi-note plucks; vary velocity slightly by metric position (`slot 0: +4`, `slot 8: +2`, beat: `0`, 8th off-beat: `-2`, 16th: `-5`).
- **Gentle ending ritardando:** Lower tempo cleanly on the closing chord bar (e.g., 72 -> 60 BPM) and let it ring before sending `All Notes Off (CC123)`.

## 3. FluidSynth Render, WAV Thumb-Slap Injection & FFmpeg Mastering

1. **FluidSynth render:**
   ```bash
   fluidsynth -ni -C 0 -R 1 -g 0.70 -r 44100 -F build/raw.wav FluidR3_GM.sf2 build/score.mid
   ```
   `-C 0` disables FluidSynth's built-in chorus so guitar intervals stay pure and in tune.

2. **WAV-layer thumb-slap (`X`) injection:**
   Record exact `slap_times` whenever `nt.role == "slap"` and do not emit a MIDI note. After FluidSynth writes the 44.1 kHz 16-bit stereo WAV, mix a 30ms synthesized acoustic transient at each slap timestamp:
   - 2ms half-cosine fade-in ramp
   - Low-frequency wooden soundboard thump (`112 Hz` downward pitch sweep, exponential decay `tau=8.5ms`)
   - 1-pole low-passed click (`2100 Hz` cosine + filtered noise, decay `tau=2.6ms`)
   - Slight L/R stereo asymmetry (`1.02` / `0.98`) clamped safely within `[-32767, 32767]`.

3. **FFmpeg acoustic mastering -> 320 kbps MP3:**
   ```text
   highpass=f=55,
   equalizer=f=210:t=q:w=1.2:g=1.5,
   equalizer=f=3400:t=q:w=1.4:g=-1.8,
   aecho=0.85:0.88:35|62:0.12|0.07,
   loudnorm=I=-16:TP=-1.5:LRA=9,
   afade=t=out:st=<fade_start>:d=3.5
   ```
   Encode with `-ar 44100 -codec:a libmp3lame -b:a 320k -map_metadata -1` to strip unnecessary container tags.

## 4. The 4 Automated Verification Gates (`src/verify_all.py`)

| Gate | Checks & Thresholds |
| :--- | :--- |
| 1. Score Integrity | `validate_score()` passes; all techniques `>= 1` |
| 2. PDF Integrity & Sanitization | Exists, size `> 30 KB`, Ghostscript page count `2..4`, 0 `textedit://` or local workstation path leaks in raw bytes or decompressed streams |
| 3. Audio DSP & Scale Purity | `ffprobe`: `44100 Hz`, `2ch`, `>= 310 kbps`; `astats` peak `< -1.0 dBFS`; `L/R diff < 1.5 dB`; stereo phase corr `> 0.50`; every measure RMS `> -35 dBFS`; Goertzel diatonic pitch-class energy share `> 85%` |
| 4. Reference Chroma Alignment (Optional `--ref` / `TAB_REF_AUDIO`) | Accept a local path or URL via `--ref` or `TAB_REF_AUDIO` (never hardcode third-party URLs in committed code). Decode mono PCM (`11025 Hz`), align Bar 1 start times, compute 12-bin Goertzel chroma per 8th-note slot (`+15ms` offset, `180ms` Hann window) with harmonic leakage suppression (`-0.45*oct`, `-0.25*19st`, `-0.18*2oct`), require overall mean cosine similarity `>= 0.80` |
