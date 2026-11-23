# Score Data Schema & Validation Rules

`src/score_data.py` is the single source of truth shared by PDF engraving and audio synthesis.

## 1. Authoritative Melody Source First (Transcription Priority)

Always transcribe and lock the lead melody before designing guitar voicings:

1. **Authoritative notation first:** Start from original numbered musical notation (简谱 / jianpu with octave dots, dotted rhythms, beams, and ties), a vocal/piano lead sheet, standard staff notation, or a clean MusicXML/MIDI melody track.
2. **Map melody onto strings first:** Lock every melody note's `(slot, dur, string, fret, concert_midi)` across all sections before filling chord voicings.
3. **Harmonize strictly underneath:** Build `"bass"` and `"inner"` accompaniment around the locked melody so accompaniment pitches never cross above the active melody.
4. **Never rely on raw guitar audio F0/onset extraction as primary truth:** Polyphonic acoustic guitar recordings exhibit strong 2nd/3rd harmonics and inner chord voices that fool pitch/onset trackers, dropping repeated vocal notes and snapping hooks to harmony tones. Use reference audio only to verify tempo/groove and for secondary Gate 4 chroma checks.

## 2. Tuning & Pitch Model

Define open strings without capo (`1` = highest E4, `6` = lowest E2) and the `CAPO` offset:

```python
CAPO = 2
OPEN_TUNING_NO_CAPO = {
    6: 40,  # E2
    5: 45,  # A2
    4: 50,  # D3
    3: 55,  # G3
    2: 59,  # B3
    1: 64,  # E4
}
```

Every `NoteEvent` computes two pitches from `(string, fret)`:

- **`written_midi` (`OPEN_TUNING_NO_CAPO[string] + fret`)**: Used by `build_pdf.py` so LilyPond `TabStaff` prints the fingered capo-relative fret (`0` = open string at the capo).
- **`concert_midi` (`OPEN_TUNING_NO_CAPO[string] + CAPO + fret`)**: Used by `build_audio.py` and scale validation so audio and harmonic analysis match the true concert key.

## 3. Core Data Structures

### `NoteEvent`

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `string` | `int` (`1..6`) | Guitar string (`1` highest, `6` lowest) |
| `fret` | `int` (`0..15`) | Fret relative to `CAPO` (`12` for natural 12th-fret harmonic) |
| `role` | `str` | `"melody"`, `"bass"`, `"inner"`, or `"slap"` |
| `harmonic` | `bool` | Natural 12th-fret harmonic (`<12>` stencil, Harp + soft Nylon layer) |
| `grace_from` | `Optional[int]` | Slide-in grace fret (`\slashedGrace` before target note) |
| `palm_mute` | `bool` | Right-hand palm mute (`P.M.` spanner, MIDI Ch 3 muted guitar) |

### `BeatEvent`

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `slot` | `int` (`0..15`) | Start position in 16th-note units within a 4/4 measure |
| `dur` | `int` | Duration in 16th-note units (`{1, 2, 3, 4, 6, 8, 12, 16}`) |
| `notes` | `Tuple[NoteEvent, ...]` | Simultaneous notes / slaps at this event |
| `rest` | `bool` | True for a rest event (`notes` must be empty) |
| `lyric` | `Optional[str]` | Optional lyric metadata |
| `fermata` | `bool` | Places `\fermata` on the TAB chord |
| `arpeggio` | `bool` | Renders `\arpeggio` wavy line and rolls MIDI pluck timing |
| `link_tech` | `Optional[str]` | `"H"` (hammer-on), `"P"` (pull-off), or `"S"` (slide) to next event |

### `Measure`

| Field | Type | Meaning |
| :--- | :--- | :--- |
| `bar` | `int` | Sequential measure index (`0` for pickup, `1..N` for full bars) |
| `events` | `List[BeatEvent]` | Contiguous events covering the bar |
| `chords` | `List[Tuple[int, str]]` | `(slot, chord_label)` pairs placed in `ChordNames` (**MUST be Capo-relative fingered chord names**, NOT concert chord names!) |
| `section` | `Optional[str]` | Rehearsal box label (e.g., `"Intro"`, `"Verse"`, `"Chorus"`) — **set ONLY on the first bar of each section**, never repeated on consecutive bars |
| `is_pickup` | `bool` | Quarter-note anacrusis (`\partial 4`, slots `12..15`, sum `4`) |
| `dynamic` | `float` | Bar-level MIDI velocity multiplier (e.g., `0.80`–`1.14`) |

Compact constructor helpers `n(...)` and `ev(...)` keep measure definitions concise and readable.

> **CAPO CHORD NAMING HARD RULE:** Always write **Capo-relative fingered chord names** in `chords=`. Example: when `1 = Bb` and `CAPO = 3` (G-shape fingering), write `chords=[(0, "G"), (8, "D/F#")]` or `chords=[(0, "Cmaj7"), (8, "D")]` — **NEVER** write concert names `Bb`, `F/A`, `Ebmaj7`, `F`! The engine automatically transposes `chords` by `+ CAPO` during bass-root validation.

## 4. Voice-Leading, Melody Breathing & Syncopated Cross-Bar Tie Rules

- **Eliminate voice crossing (高音掩蔽效应):** Listeners hear the highest sounding pitch as the melody. During low/mid-register verses where melody sits on strings 3–4, NEVER place open strings 1–2 (`B3`/`E4`) or high chord fills as `"bass"` or `"inner"` notes above the sounding melody. All non-harmonic accompaniment notes on non-closing bars must satisfy `concert_midi <= active_melody_midi`.
- **Keep String 6 strictly for `bass` / `slap`:** NEVER assign `role="inner"` to string 6 (`n(6, ..., "inner")` is forbidden). Inner accompaniment lives on strings 2–5 (prefer 3–4) above the chord root bass.
- **Melody breathing (主旋律呼吸法则):** Pluck `bass + melody` (+ optional `inner` with `arpeggio=True`) on strong beats (`slot 0` / `slot 8`), let running weak-beat and 16th-note melody lines sing cleanly without simultaneous `inner` double-stop clutter, and fill only the sustain/rest gaps with flowing single `inner` notes.
- **Syncopated cross-bar sustain ties (切分跨拍延音):** When a vocal phrase anticipates on beat 4-and (e.g., slot `14` or `12`) and ties across the barline into the next measure, do NOT re-pluck the melody note on beat 1 (`slot 0`) of the next bar—that chops one held syllable into two attacks. Instead, pluck only the new `"bass"` root and low `"inner"` accompaniment on beat 1 and let `build_audio.py`'s `2880`-tick (6-beat) melody sustain cap carry the held melody note cleanly across the barline.

## 5. Optional Module Constants (`src/score_data.py`)

`scripts/tab_engine.py` reads these module-level constants from `src/score_data.py` with safe defaults:

| Constant | Default | Purpose |
| :--- | :--- | :--- |
| `TIER` | `"standard"` | Difficulty preset: `"beginner"`, `"standard"`, or `"advanced"` |
| `FILE_PREFIX` | Derived from `TITLE` | Output filename prefix (`<FILE_PREFIX>_指弹吉他六线谱.pdf`, `.mp3`, `_bundle.zip`) |
| `ALLOW_CHROMATIC` | `False` | Allow non-diatonic chromatic passing/borrowed tones cleanly |
| `REQUIRED_TECHS` | Tier default | Override required technique names tuple (e.g., `()` for beginner/minimalist arrangements) |
| `LANDMARKS` | `{}` | `Dict[bar_idx, List[int]]` enforcing exact `concert_midi` melody sequences per landmark bar |
| `PHRASE_CUES` | `{}` | `Dict[(bar_idx, slot), str]` lyric/expression cues printed below the TAB staff |
| `LINE_BREAKS` / `PAGE_BREAKS` | Auto-balanced (4 bars/line, balanced 4–5 systems/page) | Explicit bar numbers to force `\break` or `\pageBreak` |
| `RITARDANDO_BAR` | Auto on closing bar | Measure number to slow tempo gently (`*0.833`) at the ending chord |

`chords=[(slot, label)]` accepts any standard chord root (`A..G` + optional `#`/`b`), quality (`m`, `maj7`, `m7`, `7`, `6`, `m6`, `9`, `m9`, `maj9`, `add9`, `sus2`, `sus4`, `7sus4`, `dim`, `dim7`, `m7b5`, `aug`, `5`), and slash bass (`/F#`, `/B`, `/G`, etc.) via `tab_common.chord_to_lilypond()`.

## 6. `validate_project_score()` Invariants

Run `python3 scripts/tab_engine.py check <project_dir>` (backed by `tab_common.validate_project_score()`) before compiling PDF or audio:

1. **Sequential bars & exact rhythm coverage:** Pickup bar `0` (`is_pickup=True`, starting at slot `12`, `8`, or `4`) covers `[start..16)`; full bars `1..N` cover `[0..16)` summing to `16`. Every event starts at `cursor` (`e.slot == cursor`) with `e.dur in {1, 2, 3, 4, 6, 8, 12, 16}` (`dur == 1` disallowed when `TIER="beginner"`). No duplicate consecutive `section=` markers.
2. **Rest hygiene:** Rest events have `notes == ()`, `arpeggio == False`, and `link_tech is None`.
3. **String exclusivity & note count:** Non-rest events have `1 <= len(notes) <= tier.max_simultaneous` (`3` for `beginner`, `6` otherwise) and no duplicate `string` numbers.
4. **Diatonic key purity:** Unless `ALLOW_CHROMATIC = True`, every pitched note and `grace_from` note checks `(concert_midi % 12) in DIATONIC_PCS` and prints adjacent diatonic fret fixes on failure.
5. **Playability (`fret <= tier.max_fret`, `span <= tier.max_span`):** Non-harmonic frets stay within `0..tier.max_fret` (`7` / `12` / `15`), and simultaneous fretted notes (`fret > 0 and not harmonic`) satisfy `max(fretted) - min(fretted) <= tier.max_span` (`2` / `3` / `4`), printing alternative string positions when exceeded.
6. **Harmony, root bass & texture craft:**
   - Chord-change bass hits the true chord root `>= 78%` (`>= 65%` chord tone);
   - Zero `role="inner"` notes on string 6;
   - Weak-beat melody (`slot not in {0, 8}`) carries simultaneous `inner` on `<= 40%` of attacks; `dur=1` melody carries `inner` on `<= 25%`;
   - `arpeggio=True` appears on `<= 65%` of full bars (sweet spot `20%–45%`).
7. **Technique integrity & tier whitelist:**
   - `arpeggio=True` requires `len(notes) >= 2`.
   - `harmonic=True` validates expected natural harmonic fret (`12`).
   - `grace_from` checks `1 <= grace_from <= 15`, `grace_from != fret`, and `abs(fret - grace_from) <= 3` (counted strictly under `grace`, separate from `S`).
   - `slap` (`X`) must land on 4/4 backbeat/strong slots `{4, 12, 8}` (co-located in the same `ev()` with the beat's melody/inner note).
   - `link_tech` (`"H"`, `"P"`, `"S"`) verifies a melody note exists, the next event contains the same `string`, and fret delta satisfies `1..3` for `H`, `-3..-1` for `P`, and `!= 0` for `S`. Full `standard` arrangements (`>= 16` bars) require `H + P + S >= 2`.
   - Rejects techniques outside `tier.allowed_techs` (`beginner` allows only basic plucks & optional `arpeggio`).
8. **Voice-crossing guard (`active_melody_midi`) & `LANDMARKS`:** Track `active_melody_midi` continuously across measures and reject any non-harmonic accompaniment note higher than the active melody (`meas.bar < last_bar`), printing ready-to-paste `bass`/`inner` replacements derived from the bar's chord & diatonic tones; assert `LANDMARKS` melody sequences match exact `concert_midi` targets.
