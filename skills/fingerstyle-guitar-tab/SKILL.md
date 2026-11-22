---
name: fingerstyle-guitar-tab
description: Use when creating fingerstyle guitar tablature (6-line TAB) PDFs with synchronized acoustic audio previews, transcribing or arranging songs for solo guitar with techniques (hammer-on, pull-off, slide, harmonics, palm mute, thumb slap), or fixing pitch/rhythm drift between guitar scores and rendered MIDI/MP3 previews.
---

# Fingerstyle Guitar TAB & Audio Preview

## Overview

**Core principle:** **Single Source of Truth.** One Python score module (`src/score_data.py`) drives both GNU LilyPond `TabStaff` PDF engraving and FluidSynth + FFmpeg MP3 audio synthesis. Frets, capo transposition, techniques, and concert pitches are derived from the same data structures so the printed sheet music and preview audio never drift apart.

A working end-to-end reference implementation lives in [../../examples/call-of-silence/](../../examples/call-of-silence/).

## Workflow Contract

1. **Specify musical targets:** Lock concert key, guitar tuning, capo fret, fingered key, time signature, BPM, section map, and—when available—a public reference performance audio for acoustic alignment.
2. **Bootstrap rootless toolchain:** Run `bash scripts/setup_tools.sh <project_dir>` (on Debian/Ubuntu without `sudo`) or verify system `lilypond`, `fluidsynth`, `FluidR3_GM.sf2`, `ffmpeg`, `ffprobe`, `gs`, and `pdftoppm`.
3. **Author `src/score_data.py`:** Encode every measure on a 16th-note slot grid (`slot: 0..15` in 4/4) with technique flags (`H`, `P`, `S`, `grace_from`, `arpeggio`, `harmonic`, `palm_mute`, `slap`) and implement `validate_score()`. See [references/score-schema.md](references/score-schema.md).
4. **Render 6-line TAB PDF (`src/build_pdf.py`):** Emit `\pointAndClickOff` and synchronized `ChordNames` + `TabStaff` (`\tabFullNotation`) LilyPond source using `written_midi`, compile with `-dno-point-and-click` via local or system `lilypond`, and verify CJK fonts. See [references/lilypond-tab-patterns.md](references/lilypond-tab-patterns.md).
5. **Synthesize preview MP3 (`src/build_audio.py`):** Build a 4-channel Type-1 MIDI (`PPQ=480`) using `concert_midi`, render WAV via FluidSynth with chorus disabled (`-C 0`), inject synthesized thumb-slap (`X`) transients into PCM, and master with FFmpeg (`-map_metadata -1`) to 44.1 kHz / 320 kbps stereo MP3. See [references/audio-and-verification.md](references/audio-and-verification.md).
6. **Verify & iterate (`src/verify_all.py`):** Run all verification gates (score integrity, PDF page/size + 0-path-leak sanitization check, audio DSP/diatonic energy check, and optional `--ref` / `TAB_REF_AUDIO` per-bar reference chroma cosine alignment). Fix any failing measure in `src/score_data.py` and rebuild until every gate passes.
7. **Package & publish:** Render preview PNGs with `pdftoppm -png -r 150`, strip/exclude `.tools/` and `build/` intermediates (`.wav`, `.mid`, `.ly`), include a zero-download `index.html` + `.nojekyll` GitHub Pages player alongside full-width `README.md` previews, and publish sanitized showcase artifacts.

## Quick Reference

| Component / Gate | Specification / Threshold |
| :--- | :--- |
| `src/score_data.py` | `NoteEvent`, `BeatEvent`, `Measure`; `written_midi` (TAB) vs `concert_midi` (audio); `fret span <= 3`; 100% diatonic check |
| `src/build_pdf.py` | LilyPond `\version "2.24.0"`, `\pointAndClickOff`, `-dno-point-and-click`, `TabStaff`, `ChordNames`, custom stencils (`X`, `<12>`), `TextSpanner` (`P.M.`) |
| `src/build_audio.py` | MIDI Ch0 Prog25 Steel, Ch1 Prog24 Nylon, Ch2 Prog46 Harp Harmonics, Ch3 Prog28 Palm Mute; `-C 0`; WAV thumb slaps; FFmpeg `loudnorm` + `-map_metadata -1` |
| Score & PDF gates | Continuous 16th-slot coverage per bar, all target techniques `>= 1`, PDF `2–4` pages, `> 30 KB`, `0` local path leaks |
| Audio & alignment gates | Peak `< -1.0 dBFS`, L/R diff `< 1.5 dB`, phase corr `> 0.50`, Goertzel diatonic energy `> 85%`, runtime `--ref` 8th-slot chroma cosine `>= 0.80` |

## Critical Pitfalls

- **PDF & repo sanitization:** LilyPond embeds clickable `textedit://` local filesystem URIs by default unless `\pointAndClickOff` and `-dno-point-and-click` are set. Enforce a 0-path-leak scan in `verify_pdf()`, strip audio metadata (`-map_metadata -1`), and never hardcode third-party scraped audio URLs in committed code (pass `--ref` or `TAB_REF_AUDIO` at runtime).
- **Capo pitch split:** Always compute TAB frets from `written_midi = open_string + fret` and MIDI pitches from `concert_midi = open_string + capo + fret`. Mixing them transposes either the printed frets or the audio.
- **FluidSynth chorus detune:** FluidSynth enables chorus by default, causing guitar chorusing/detune artifacts. Always pass `-C 0` to `fluidsynth` and send MIDI `CC93 = 0` on every channel.
- **`\slashedGrace` stream sync:** Every `\slashedGrace` in `TabStaff` shifts musical timing relative to un-graced streams unless `ChordNames` inserts a matching `\grace { s16 }` at that exact slot.
- **Consecutive `H`/`P` slurs:** For chains like `0h3p0`, close the previous slur `)` on the destination note before opening the next `(`.
- **`\glissando` string alignment:** Sort notes inside chord brackets `<...>` ascending by `string` (`1..6`) so `\glissando` maps melody string 1 to melody string 1 rather than crossing into bass strings.
- **Physical string damping & slaps:** Stop old notes on the same string before re-plucking (`+8` ticks overlap for `H`/`P`/`S`, `-8` ticks otherwise), damp bass/inner rings on chord changes, and synthesize thumb slaps (`X`) directly in WAV PCM instead of relying on GM percussion.
- **Evidence before completion:** Never declare the score or audio finished without running `python3 src/verify_all.py` after the final edit and observing `ALL VERIFICATION CHECKS PASSED.`
