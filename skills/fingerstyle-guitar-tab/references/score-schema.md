# Score Data Schema & Validation Rules

`src/score_data.py` is the single source of truth shared by PDF engraving and audio synthesis.

## 1. Tuning & Pitch Model

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

## 2. Core Data Structures

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

## 3. `validate_score()` Invariants

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
