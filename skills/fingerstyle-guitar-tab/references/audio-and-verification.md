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

### Role Velocity SNR & Sustain Caps

Separate the singing melody from bass support and soft inner arpeggios, while letting tied/syncopated melody notes ring across barlines:

| Role / Technique | MIDI Channel Layering | Base Velocity | Sustain Cap (`max_age` at `PPQ=480`) |
| :--- | :--- | :--- | :--- |
| `"melody"` | Ch 0 (`Steel`) + Ch 1 (`Nylon * 0.38`) | `98` | `2880` ticks (6 beats — supports cross-bar syncopated ties) |
| `"bass"` | Ch 0 (`Steel`) + Ch 1 (`Nylon * 0.38`) | `74` | `1920` ticks (4 beats) |
| `"inner"` | Ch 0 (`Steel`) + Ch 1 (`Nylon * 0.38`) | `48` | `1920` ticks (4 beats — stays softly beneath melody) |
| `harmonic` (`<12>`) | Ch 2 (`Harp=86`) + Ch 1 (`Nylon=48`) | `86` / `48` | `3000` ticks (bell-like harmonic bloom) |
| `palm_mute` (`P.M.`) | Ch 3 (`Muted=80`) + Ch 0 (`95t` gate `48`) | `80` / `48` | `280` ticks |

Track active ringing notes per string (`active[string]`):

- **String re-pluck & legato:** Stop any prior active note on the same string before starting the new note:
  - Normal pluck: stop at `on_tick - 8` ticks.
  - Legato destination (`H`, `P`, `S`): stop at `on_tick + 8` ticks (~14ms overlap) and scale destination velocity (`H: *0.74`, `P: *0.70`, `S: *0.84`).
- **Chord change damping:** On harmonic chord changes (`ev.slot in chord_slots`), damp ringing `"bass"` and `"inner"` strings at `ev_tick - 10`.
- **2nd-interval melody clash prevention:** If a new melody note is within `<= 2` semitones of an adjacent ringing melody string (and not a legato target), damp the previous melody string at `ev_tick - 10`.
- **Accompaniment-vs-melody semitone clash damping:** Whenever an incoming `"bass"` or `"inner"` note lands within `abs(nt.concert_midi - old_midi) <= 1` of a ringing `"melody"` string, stop that ringing melody string at `ev_tick - 10` to avoid minor-2nd mud.
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

## 4. The 4 Automated Verification Gates (`scripts/tab_engine.py verify` & `src/verify_all.py`)

Run all 4 gates uniformly on any project via `python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py verify <project_dir>`:

| Gate | Checks & Thresholds |
| :--- | :--- |
| 1. Score Integrity & Tier Compliance | `validate_project_score()` passes (`0` voice crossings where `bass/inner <= active_melody_midi`, tier fret/span/rhythm/technique compliance); tier `required_techs >= 1`; **landmark melody pitch-sequence assertions (`LANDMARKS`)** match the authoritative sheet/jianpu source |
| 2. PDF Integrity & Sanitization | Exists, size `> 15 KB` (`> 30 KB` for multi-page), Ghostscript page count `>= 1`, 0 `textedit://` or local workstation path leaks in raw bytes or decompressed streams |
| 3. Audio DSP & Scale Purity | `ffprobe`: `44100 Hz`, `2ch`, `>= 310 kbps`; `astats` peak `< -1.0 dBFS`; `L/R diff < 1.5 dB`; stereo phase corr `> 0.50`; every non-rest measure RMS `> -35 dBFS`; Goertzel diatonic pitch-class energy share `> 85%` (`> 70%` when `ALLOW_CHROMATIC = True`) |
| 4. Reference Chroma Alignment (Optional `--ref` / `TAB_REF_AUDIO`) | Accept a local path or URL via `--ref` or `TAB_REF_AUDIO` (never hardcode third-party URLs in committed code). Decode mono PCM (`11025 Hz`), align Bar 1 start times, compute 12-bin Goertzel chroma per 8th-note slot (`+15ms` offset, `180ms` Hann window) with harmonic leakage suppression (`-0.45*oct`, `-0.25*19st`, `-0.18*2oct`), require overall mean cosine similarity `>= 0.80`. **Note:** Chroma validates chord harmony/progression only—never rely on Gate 4 alone instead of Gate 1 landmark melody assertions |

## 5. MIDI-Exact `sync_manifest.json` Note Timeline for Interactive Playback

`scripts/tab_engine.py manifest` (and `build_sync_manifest.py`) bridges synthesized audio and the web practice player (`guitar-tab-player`) without heuristic sustain guesswork:

- Calls `write_midi_file()` to obtain exact `(on_tick, off_tick)` MIDI lifetimes produced by all string-damping, legato, `max_age` (`2880`/`1920`/`3000`/`280`), and ritardando rules.
- Parses Tempo (`0xFF 0x51`) changes + Ch 0 (`Steel`) / Ch 2 (`Harp`) / Ch 3 (`Muted`) note spans via `parse_midi_note_spans()`, converting ticks accurately into seconds (`t`, `d`).
- Emits `"notes"` entries (`t`, `d`, `s`, `f`, `r`, `b`, optional `gf`/`lt`/`to`/`h`/`pm`/`arp`) alongside system `sliceY`/`staffY` coordinates and `barBoxes`, driving exact note-hold highlights on both the 2D Fretboard and 3D Falling Highway.
