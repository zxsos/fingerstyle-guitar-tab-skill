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
| `chords` | `List[Tuple[int, str]]` | `(slot, chord_label)` pairs placed in `ChordNames` |
| `section` | `Optional[str]` | Rehearsal box label (e.g., `"Intro"`, `"Verse"`, `"Chorus"`) |
| `is_pickup` | `bool` | Quarter-note anacrusis (`\partial 4`, slots `12..15`, sum `4`) |
| `dynamic` | `float` | Bar-level MIDI velocity multiplier (e.g., `0.80`–`1.14`) |

Compact constructor helpers `n(...)` and `ev(...)` keep measure definitions concise and readable.

## 4. Voice-Leading & Syncopated Cross-Bar Tie Rules

- **Eliminate voice crossing (高音掩蔽效应):** Listeners hear the highest sounding pitch as the melody. During low/mid-register verses where melody sits on strings 3–4, NEVER place open strings 1–2 (`B3`/`E4`) or high chord fills as `"bass"` or `"inner"` notes above the sounding melody. All non-harmonic accompaniment notes on non-closing bars must satisfy `concert_midi <= active_melody_midi`.
- **Syncopated cross-bar sustain ties (切分跨拍延音):** When a vocal phrase anticipates on beat 4-and (e.g., slot `14` or `12`) and ties across the barline into the next measure, do NOT re-pluck the melody note on beat 1 (`slot 0`) of the next bar—that chops one held syllable into two attacks. Instead, pluck only the new `"bass"` root and low `"inner"` accompaniment on beat 1 and let `build_audio.py`'s `2880`-tick (6-beat) melody sustain cap carry the held melody note cleanly across the barline.

## 5. `validate_score()` Invariants

Run `validate_score()` before both PDF compilation and audio synthesis:

1. **Sequential bars & exact rhythm coverage:** Pickup bar `0` covers `[12..16)` summing to `4`; full bars `1..N` cover `[0..16)` summing to `16`. Every event starts at `cursor` (`e.slot == cursor`) with `e.dur in {1, 2, 3, 4, 6, 8, 12, 16}`.
2. **Rest hygiene:** Rest events have `notes == ()`, `arpeggio == False`, and `link_tech is None`.
3. **String exclusivity:** Non-rest events have `>= 1` note and no duplicate `string` numbers.
4. **Diatonic key purity:** Unless intentionally chromatic, every pitched note and `grace_from` note checks `(concert_midi % 12) in DIATONIC_PCS`.
5. **Playability (`fret span <= 3`):** For non-harmonic fretted notes (`fret > 0 and not harmonic`) in a simultaneous event, `max(fretted) - min(fretted) <= 3`.
6. **Technique integrity:**
   - `arpeggio=True` requires `len(notes) >= 2`.
   - `harmonic=True` validates expected natural harmonic fret (`12`).
   - `grace_from` checks `1 <= grace_from <= 15`, `grace_from != fret`, and `abs(fret - grace_from) <= 3`.
   - `link_tech` (`"H"`, `"P"`, `"S"`) verifies a melody note exists, the next event contains the same `string`, and fret delta satisfies `1..3` for `H`, `-3..-1` for `P`, and `!= 0` for `S`.
   - Confirm each required technique count (`H`, `P`, `S`, `arpeggio`, `harmonic`, `palm_mute`, `slap`) is `>= 1`.
7. **Voice-crossing guard (`active_melody_midi`):** Track `active_melody_midi` across measures and reject any non-harmonic accompaniment note higher than the active melody (allowing full-chord voicings on the final closing bar if desired):

   ```python
   active_melody_midi: Optional[int] = None
   for idx, meas in enumerate(measures):
       for ev_idx, e in enumerate(meas.events):
           if e.rest:
               active_melody_midi = None
               continue
           pitched_notes = [nt for nt in e.notes if nt.role != "slap"]
           mel_notes = [nt for nt in pitched_notes if nt.role == "melody"]
           if mel_notes:
               active_melody_midi = None if any(nt.harmonic for nt in mel_notes) else max(nt.concert_midi for nt in mel_notes)
           if meas.bar < last_bar and active_melody_midi is not None:
               for nt in pitched_notes:
                   if nt.role in ("bass", "inner") and not nt.harmonic and nt.concert_midi > active_melody_midi:
                       errors.append(
                           f"M{meas.bar:02d} slot {e.slot}: voice crossing — {nt.role} "
                           f"({nt.string},{nt.fret}) {nt.pitch_name} ({nt.concert_midi}) > "
                           f"melody ({active_melody_midi})"
                       )
   ```
