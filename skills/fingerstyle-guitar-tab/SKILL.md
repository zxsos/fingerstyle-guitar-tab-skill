---
name: fingerstyle-guitar-tab
description: Use when creating fingerstyle guitar tablature (6-line TAB) PDFs with synchronized acoustic audio previews and web practice bundles, converting MusicXML/MIDI/numbered-notation (jianpu) or songs into solo guitar arrangements across beginner/standard/advanced difficulty tiers, or fixing pitch/rhythm/voice-crossing drift.
---

# Fingerstyle Guitar TAB Engine

## Rule #0: Engine First — Never Hand-Write Boilerplate

**Core principle:** **Single Source of Truth + Shared Deterministic Engine.**
You ONLY create or edit **one file per song**: `<project_dir>/src/score_data.py`.
**NEVER** rewrite `build_pdf.py`, `build_audio.py`, `build_sync_manifest.py`, `build_bundle.py`, or `verify_all.py` from scratch. Always use the shared pure-stdlib CLI tools in `scripts/`:

- `scripts/setup_tools.sh <project_dir>` — one-time rootless LilyPond + FluidSynth bootstrap (or verify system tools).
- `scripts/fret_helper.py` — zero-mental-math Capo recommender, Jianpu -> string/fret lookup, and safe `bass`/`inner` voicing finder.
- `scripts/import_score.py` — automatic **Tier S** converter (`MIDI` / `MusicXML` / `.mxl` -> `src/score_data.py`).
- `scripts/init_project.py` — scaffold starter `src/score_data.py` for any key, capo, BPM, and difficulty tier.
- `scripts/tab_engine.py` — unified runner (`check` | `pdf` | `audio` | `verify` | `manifest` | `bundle` | `build`).

A complete reference project lives in [../../examples/call-of-silence/](../../examples/call-of-silence/).

---

## Step 0: Mandatory Source-Purity Triage & Tier Selection (先问再做)

Before writing any code, classify what the user provided and confirm their preference:

| Purity Tier | User Input | Conversion Path | Required Action |
| :--- | :--- | :--- | :--- |
| **S (Purest)** | `.mid` / `.midi` / `.musicxml` / `.xml` / `.mxl` / Guitar Pro export | Score File -> `score_data.py` -> PDF + MP3 + Bundle | Confirm difficulty tier, then run `scripts/import_score.py` |
| **A (High)** | Original numbered notation (简谱 image/PDF), vocal/piano lead sheet | Sheet Visual -> `fret_helper.py` -> `score_data.py` -> Artifacts | Confirm difficulty tier (and octave if needed), then transcribe melody first |
| **B (Medium)** | Clean solo vocal / solo piano audio | Audio -> *(ask to find S/A sheet first)* | **PAUSE & ASK:** explain audio pitch errors vs sheets; ask if they have S/A sheets or want you to search cleaner scores first |
| **C (Low / Blurry)** | **Only a song name**, or full-band / polyphonic acoustic guitar MP3 | **DO NOT blind-guess from guitar audio FFT/F0** | **PAUSE & ASK FIRST:** (1) Do they have `.mid`/MusicXML/简谱图? (2) Want you to search public S/A notation/MIDI first? (3) Pick difficulty tier |

### Choose One Difficulty Tier (`TIER` in `src/score_data.py`)

| `TIER` | Target Player | Max Fret | Fret Span | Rhythm & Voices | Allowed Techniques |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`beginner`** | 新手入门 | `<= 7` (open/0-3 preferred) | **`<= 2`** | No 16th notes (`dur != 1`), max 3 simultaneous notes (`melody + bass`) | Basic plucks + optional `arpeggio` (no `H/P/S/harmonic/palm_mute/slap`) |
| **`standard`** *(default)* | 标准指弹进阶 | `<= 12` | **`<= 3`** | Full 16th grid (`dur >= 1`), cross-bar syncopated ties, 3+ voices | `arpeggio` (`>= 1`) + optional `H`, `P`, `S`, `<12>`, `P.M.`, `X` |
| **`advanced`** | 进阶演出独奏 | `<= 15` | **`<= 4`** | Dense walking bass + 16th subdivisions | Full showcase (`H`, `P`, `S`, `arpeggio`, `<12>` required; `P.M.`, `X` supported) |

See [references/intake-and-tiers.md](references/intake-and-tiers.md) for exact dialogue templates and conversion workflows.

---

## Step 1: Author `src/score_data.py` (Pick Path by Input)

- **Path S (MIDI / MusicXML -> Code -> Music):**
  ```bash
  python3 scripts/import_score.py <input.mid|.musicxml|.mxl> --out <proj>/src/score_data.py --tier <tier>
  ```
  Review/polish chord labels, fingerings, or techniques as needed, then proceed to Step 2.
- **Path A (Jianpu / Sheet -> Code -> Music — zero mental math):**
  1. Recommend Capo & open chord key: `python3 scripts/fret_helper.py recommend-capo --key <Key> --tier <tier>`
  2. Look up exact melody frets from jianpu: `python3 scripts/fret_helper.py jianpu --key <Key> --capo <N> --tier <tier> <tokens...>`
  3. Look up safe `bass`/`inner` voicings strictly `<= melody_midi`: `python3 scripts/fret_helper.py chord --chord <Chord> --capo <N> --max-midi <M> --tier <tier>`
  4. Scaffold starter file: `python3 scripts/init_project.py --dir <proj> --title "<Title>" --key <Key> --capo <N> --bpm <BPM> --tier <tier>`
  5. Fill measures into `<proj>/src/score_data.py` (locking melody first, keeping bass/inner `<= active_melody_midi`, preserving cross-bar syncopated ties without re-plucking beat 1, and recording key bar melody pitches in `LANDMARKS`).

**Weak / smaller model or first time?** Read the step-by-step worked tutorial & 1-second error lookup table in [references/weak-model-walkthrough.md](references/weak-model-walkthrough.md).

---

## Step 2: Self-Healing Check Loop

Run:
```bash
python3 scripts/tab_engine.py check <proj>
```
If any rule fails (bar sum != 16, voice crossing, span exceeded, non-diatonic pitch, beginner-forbidden technique, landmark mismatch), the validator prints concrete copy-paste fix suggestions (`-> 修复建议: ...`). Apply fixes in `src/score_data.py` and re-run until `check` reports `[PASS] ... 0 voice crossings`.

---

## Step 3: Build, Verify & Package in One Command

```bash
python3 scripts/tab_engine.py build <proj>
```
This runs: `check` -> LilyPond sanitized PDF (`\pointAndClickOff`, `-dno-point-and-click`) -> `pdftoppm -png -r 150` previews -> FluidSynth `-C 0` + FFmpeg 320 kbps MP3 (`98/74/48` velocity balance, `2880t` melody sustain, `<= 1` semitone clash damping) -> 4-Gate `verify` -> MIDI-exact `sync_manifest.json` -> verified `*_bundle.zip` (which also bundles `src/score_data.py` so the single source of truth travels with the deliverables for future re-editing or tier conversion) -> cleans temporary `build/` & `__pycache__/`.

Pass `--ref <audio_path_or_url>` (or set `TAB_REF_AUDIO`) if you also want optional Gate 4 reference chroma alignment (`>= 0.80`). Never commit third-party URLs into code. Import the resulting `*_bundle.zip` into `https://zxsos.github.io/guitar-tab-player/`.

---

## Reference Library (Load On Demand)

- [references/intake-and-tiers.md](references/intake-and-tiers.md) — S/A/B/C purity triage, user prompt scripts, bidirectional conversion map, and 3-tier validator rules.
- [references/weak-model-walkthrough.md](references/weak-model-walkthrough.md) — Worked jianpu-to-code tutorial using `fret_helper.py` + 8-error 1-second repair table.
- [references/score-schema.md](references/score-schema.md) — `score_data.py` fields, universal chord labels, voice-leading & syncopated tie rules, `validate_project_score()` invariants.
- [references/lilypond-tab-patterns.md](references/lilypond-tab-patterns.md) — LilyPond 2.24 engraving rules, `\slashedGrace` sync, consecutive `H`/`P` slurs, `\glissando` ordering, CJK fonts.
- [references/audio-and-verification.md](references/audio-and-verification.md) — Role velocity SNR (`98/74/48`), MIDI sustain caps (`2880/1920/3000/280`), WAV thumb slaps, 4-Gate verification thresholds, MIDI-exact `sync_manifest.json` timeline.
