---
name: fingerstyle-guitar-tab
description: Use when creating fingerstyle guitar tablature (6-line TAB) PDFs with synchronized acoustic audio previews, transcribing or arranging songs for solo guitar with techniques (hammer-on, pull-off, slide, harmonics, palm mute, thumb slap), or fixing pitch/rhythm drift between guitar scores and rendered MIDI/MP3 previews.
---

# Fingerstyle Guitar TAB & Audio Preview

## Overview

**Core principle:** **Single Source of Truth.** One Python score module (`src/score_data.py`) drives both GNU LilyPond `TabStaff` PDF engraving and FluidSynth + FFmpeg MP3 audio synthesis. Frets, capo transposition, techniques, and concert pitches are derived from the same data structures so the printed sheet music and preview audio never drift apart.

A working end-to-end reference implementation lives in [../../examples/call-of-silence/](../../examples/call-of-silence/).

## Workflow Contract

1. **Specify musical targets & authoritative melody source:** Lock concert key, guitar tuning, capo fret, fingered key, time signature, BPM, and section map. Acquire an **authoritative melody source FIRST**—original numbered musical notation (简谱 / jianpu with octave dots, dotted rhythms, beams, and ties), vocal lead sheet, staff score, or MusicXML/MIDI melody track. **NEVER** rely on raw acoustic guitar audio spectrum / F0 / onset extraction as the primary melody source (guitar 2nd/3rd harmonics and inner chord voices fool F0/onset detectors, dropping repeated notes and snapping vocal hooks to chord tones). Use reference recordings only to confirm tempo/groove and for secondary Gate 4 chroma alignment.
2. **Bootstrap rootless toolchain:** Run `bash scripts/setup_tools.sh <project_dir>` (on Debian/Ubuntu without `sudo`) or verify system `lilypond`, `fluidsynth`, `FluidR3_GM.sf2`, `ffmpeg`, `ffprobe`, `gs`, and `pdftoppm`.
3. **Author `src/score_data.py`:** Transcribe melody onto fretboard strings first from the authoritative score/jianpu, then arrange bass & inner accompaniment strictly underneath (`bass`/`inner` non-harmonic pitches `<= active_melody_midi` to prevent voice crossing). Preserve **cross-bar syncopated ties** (when a melody phrase anticipates on beat 4-and and ties across the barline, pluck only bass + low accompaniment on beat 1 rather than re-plucking the melody string). Encode every measure on a 16th-note slot grid (`slot: 0..15` in 4/4) with technique flags (`H`, `P`, `S`, `grace_from`, `arpeggio`, `harmonic`, `palm_mute`, `slap`) and implement `validate_score()` including the voice-crossing invariant. See [references/score-schema.md](references/score-schema.md).
4. **Render 6-line TAB PDF (`src/build_pdf.py`):** Emit `\pointAndClickOff` and synchronized `ChordNames` + `TabStaff` (`\tabFullNotation`) LilyPond source using `written_midi`, compile with `-dno-point-and-click` via local or system `lilypond`, and verify CJK fonts. See [references/lilypond-tab-patterns.md](references/lilypond-tab-patterns.md).
5. **Synthesize preview MP3 (`src/build_audio.py`):** Build a 4-channel Type-1 MIDI (`PPQ=480`) using `concert_midi`, touch velocity SNR (`melody=98 / bass=74 / inner=48`), extended melody sustain cap (`2880` ticks = 6 beats for cross-bar phrasing vs `1920` for bass/inner, `3000` for harmonics, `280` for palm mute), and accompaniment-vs-melody semitone clash damping (`<= 1` semitone). Render WAV via FluidSynth with chorus disabled (`-C 0`), inject synthesized thumb-slap (`X`) transients into PCM, and master with FFmpeg (`-map_metadata -1`) to 44.1 kHz / 320 kbps stereo MP3. See [references/audio-and-verification.md](references/audio-and-verification.md).
6. **Verify & iterate (`src/verify_all.py`):** Run all verification gates: Gate 1 score integrity (`0` voice crossings + explicit **landmark melody pitch-sequence assertions** on signature verse/chorus/climax bars against the authoritative score/jianpu), Gate 2 PDF page/size + 0-path-leak sanitization check, Gate 3 audio DSP/diatonic energy check, and Gate 4 optional `--ref` / `TAB_REF_AUDIO` per-bar reference chroma cosine alignment (which validates harmony/progression only and cannot substitute for landmark melody assertions). Fix any failing measure in `src/score_data.py` and rebuild until every gate passes.
7. **Package & publish:** Render preview PNGs with `pdftoppm -png -r 150`, generate `sync_manifest.json` via `src/build_sync_manifest.py` (white-gap system slicing + barline coordinates + timing metadata + exact `(on_tick, off_tick)` MIDI note spans read back from `build_audio.write_midi_file()` via `parse_midi_note_spans` so the web player's 2D Fretboard & 3D Falling Highway match real audible sustains 100%), package the final deliverable bundle `*_bundle.zip` via `src/build_bundle.py` (containing `sync_manifest.json`, `*.mp3`, `*.pdf`, and `previews/page-*.png`), strip/exclude `.tools/` and `build/` intermediates (`.wav`, `.mid`, `.ly`), and import or share the bundle with the decoupled practice player (`https://zxsos.github.io/guitar-tab-player/`).

## Quick Reference

| Component / Gate | Specification / Threshold |
| :--- | :--- |
| `src/score_data.py` | Authoritative score/jianpu melody first; `NoteEvent`, `BeatEvent`, `Measure`; `written_midi` (TAB) vs `concert_midi` (audio); `fret span <= 3`; voice-crossing check (`bass/inner <= active_melody_midi`); 100% diatonic check |
| `src/build_pdf.py` | LilyPond `\version "2.24.0"`, `\pointAndClickOff`, `-dno-point-and-click`, `TabStaff`, `ChordNames`, custom stencils (`X`, `<12>`), `TextSpanner` (`P.M.`) |
| `src/build_audio.py` | MIDI Ch0 Prog25 Steel, Ch1 Prog24 Nylon, Ch2 Prog46 Harp Harmonics, Ch3 Prog28 Palm Mute; vel SNR `98/74/48`, melody sustain `2880t`, semitone clash damping; `-C 0`; WAV thumb slaps; FFmpeg `loudnorm` + `-map_metadata -1` |
| `src/build_sync_manifest.py` | Generates `sync_manifest.json` (ink-safe `sliceY`, `staffY`, `barBoxes`, timings, cues, and MIDI-exact `"notes"` spans `t`, `dur`, `string`, `fret`, `role`, `tech`) for seamless unpaginated web playback |
| `src/build_bundle.py` | Packages `sync_manifest.json`, MP3, sanitized PDF, and `previews/page-*.png` into `*_bundle.zip` for one-step player import |
| Score & PDF gates | Continuous 16th-slot coverage per bar, `0` voice crossings, landmark melody pitch assertions, all target techniques `>= 1`, PDF `2–4` pages, `> 30 KB`, `0` local path leaks |
| Audio & alignment gates | Peak `< -1.0 dBFS`, L/R diff `< 1.5 dB`, phase corr `> 0.50`, Goertzel diatonic energy `> 85%`, runtime `--ref` 8th-slot chroma cosine `>= 0.80` |

## Critical Pitfalls

- **Audio-spectrum melody trap (信源偏差):** Never transcribe melody purely from acoustic guitar audio F0/onset analysis—harmonics and chord inner voices mask repeated notes and pull vocal hooks onto inner chord tones. Always transcribe from original numbered notation (jianpu), vocal lead sheet, or MusicXML/MIDI melody track first, and enforce explicit landmark melody pitch-sequence assertions in `verify_all.py` because Chroma similarity alone validates harmony rather than melody.
- **Voice crossing / high-note masking (声部交叉):** Listeners perceive the highest sounding pitch as the melody. Never place non-harmonic `bass`/`inner` accompaniment notes above the currently ringing melody pitch (`active_melody_midi`), especially during low/mid-register verses on strings 3–4; enforce `concert_midi <= active_melody_midi` inside `validate_score()`.
- **Syncopated cross-bar melody re-pluck (切分跨拍重拨):** When a melody phrase anticipates on beat 4-and and ties across the barline, do NOT re-pluck the melody string on beat 1 of the next measure; pluck only bass + low accompaniment and keep melody `max_age = 2880` ticks so the melody note rings naturally over the barline.
- **Accompaniment masking melody (力度信噪比):** Keep base velocity balance at `melody=98 / bass=74 / inner=48` and damp ringing melody strings when an incoming `bass`/`inner` note lands within `<= 1` semitone (`ev_tick - 10`).
- **PDF & repo sanitization:** LilyPond embeds clickable `textedit://` local filesystem URIs by default unless `\pointAndClickOff` and `-dno-point-and-click` are set. Enforce a 0-path-leak scan in `verify_pdf()`, strip audio metadata (`-map_metadata -1`), and never hardcode third-party scraped audio URLs in committed code (pass `--ref` or `TAB_REF_AUDIO` at runtime).
- **Capo pitch split:** Always compute TAB frets from `written_midi = open_string + fret` and MIDI pitches from `concert_midi = open_string + capo + fret`. Mixing them transposes either the printed frets or the audio.
- **FluidSynth chorus detune:** FluidSynth enables chorus by default, causing guitar chorusing/detune artifacts. Always pass `-C 0` to `fluidsynth` and send MIDI `CC93 = 0` on every channel.
- **`\slashedGrace` stream sync:** Every `\slashedGrace` in `TabStaff` shifts musical timing relative to un-graced streams unless `ChordNames` inserts a matching `\grace { s16 }` at that exact slot.
- **Consecutive `H`/`P` slurs:** For chains like `0h3p0`, close the previous slur `)` on the destination note before opening the next `(`.
- **`\glissando` string alignment:** Sort notes inside chord brackets `<...>` ascending by `string` (`1..6`) so `\glissando` maps melody string 1 to melody string 1 rather than crossing into bass strings.
- **Physical string damping & slaps:** Stop old notes on the same string before re-plucking (`+8` ticks overlap for `H`/`P`/`S`, `-8` ticks otherwise), damp bass/inner rings on chord changes, and synthesize thumb slaps (`X`) directly in WAV PCM instead of relying on GM percussion.
- **Evidence before completion:** Never declare the score or audio finished without running `python3 src/verify_all.py` after the final edit and observing `ALL VERIFICATION CHECKS PASSED.`
