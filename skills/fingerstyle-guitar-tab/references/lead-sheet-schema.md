# Declarative `lead_sheet.json` Schema (`scripts/arrange_engine.py`)

Instead of hand-coding 800+ lines of raw `(slot, dur, string, fret)` Python tuples, author a compact declarative `lead_sheet.json` (pure musical intent: key, BPM, chords, jianpu melody, section styles) and let `scripts/arrange_engine.py` run **global Viterbi fretboard optimization + style-driven 3-voice arrangement** deterministically.

## 1. Command Usage

```bash
# Arrange lead_sheet.json -> src/score_data.py + <Title>.atex + <Title>.musicxml
python3 scripts/arrange_engine.py arrange <proj>/lead_sheet.json --out-dir <proj> --tier standard

# Optional: parse structured M01..MN jianpu text into lead_sheet.json
python3 scripts/arrange_engine.py parse-jianpu <jianpu.txt> --out <proj>/lead_sheet.json

# Optional: export any existing score_data.py project directly to .atex + .musicxml
python3 scripts/arrange_engine.py export <proj> --formats atex,musicxml
```

## 2. Minimal Example

```json
{
  "title": "Evening Breeze",
  "artist": "Example Artist",
  "composer": "Example Composer",
  "subtitle": "指弹吉他独奏六线谱",
  "key": "C",
  "bpm": 96,
  "capo": "auto",
  "chords_mode": "concert",
  "tier": "standard",
  "sections": [
    {
      "name": "Intro",
      "style": "intro_arp",
      "dynamic": 0.85,
      "bars": [
        {
          "bar": 1,
          "chords": [[0, "Fmaj7"], [8, "G"]],
          "melody": [[0, "3"], [6, "2"], [8, "1"], [10, "7,"], [14, "1"]]
        },
        {
          "bar": 2,
          "chords": [[0, "C"]],
          "melody": [[10, "5", 2, "", "grace"]]
        }
      ]
    },
    {
      "name": "Chorus II",
      "style": "chorus_slap",
      "dynamic": 0.98,
      "bars": [
        {
          "bar": 25,
          "chords": [[0, "Fmaj7"], [8, "G"]],
          "melody": [
            [0, "1'", 2, "晚"],
            [4, "5", 2, "风"],
            [6, "6", 2, "轻"],
            [12, "5", 2, "吹"],
            [14, "3", 2, "过"]
          ]
        }
      ]
    }
  ]
}
```

## 3. Field Reference

| Field | Required | Values / Notes |
| :--- | :---: | :--- |
| `title`, `artist`, `composer` | Yes | Song metadata |
| `key` | Yes | Concert key signature (`C`, `G`, `Bb`, `F#m`, `Dm`, etc.) |
| `bpm` | Yes | Tempo in quarter-note BPM |
| `capo` | No | `"auto"` *(default — picks optimal open-chord Capo)* or integer `0..7` |
| `chords_mode` | No | `"fingered"` (Capo-relative shapes) or `"concert"` *(engine automatically transposes concert chords by `-capo`!)* |
| `tier` | No | `"beginner"` / `"standard"` / `"advanced"` |
| `sections[].name` | Yes | Rehearsal label placed on the first bar of the section |
| `sections[].style` | No | Arranger texture profile (see table below); can also be overridden per bar |
| `sections[].dynamic` | No | Velocity multiplier (`0.80`–`1.05`); can also be overridden per bar |
| `bars[].chords` | Yes | List of `[slot, chord_label]`, e.g. `[[0, "G"], [8, "D/F#"]]` |
| `bars[].melody` | Yes | List of `[slot, token, dur?, lyric?, tag?]` (empty `[]` for sustain/arpeggio fill bars) |

### Melody Item Format (`[slot, token, dur?, lyric?, tag?]`)
- `slot`: `0..15` in 16th-note units (4/4).
- `token`: Jianpu token (`"5,"`, `"7,"`, `"1"`, `"2"`, `"3"`, `"#4"`, `"1'"`, `"3'"`) OR integer concert MIDI (`58`, `62`, `70`, etc.).
- `dur` *(optional)*: Explicit duration (`1`, `2`, `3`, `16`) — mainly needed for dotted-8th/16th syncopations (`[12, "5", 3, "风"], [15, "3", 1, "景"]`) and final whole-note (`[0, "1'", 16, "", "fermata"]`). Omit or `null` for standard 8th-note melody attacks.
- `lyric` *(optional)*: Single syllable/character lyric.
- `tag` *(optional)*: `"grace"` (direction-safe upward slide-in), `"harm"` (natural 12th-fret harmonic), or `"fermata"` (closing rolled chord).

### Section Styles (`style`)

| `style` | Arrangement Texture Generated Automatically |
| :--- | :--- |
| `intro_arp` | Delicate opening (`dyn ~0.85`), phrase-downbeat `arpeggio`, gentle 8th-note inner flow |
| `verse_breathing` | Singing melody (`dyn ~0.90`), 100% root bass, breathing weak beats, automatic same-string `H`/`P` |
| `chorus_open` | Open lifted chorus (`dyn ~0.95`), strong 3-voice phrase downbeats, expressive `H`/`P` + climax `grace` |
| `interlude_pm` | Right-hand `palm_mute=True` on bass & inner ONLY, keeping lead melody clear and unmuted |
| `interlude_open` | Open crescendo transition out of palm mute |
| `chorus_slap` | High-energy Chorus II with automatic backbeat `slap` (`X`) on Beat 2 (`slot=4`) & Beat 4 (`slot=12`) |
| `outro_calm` | Calm resolution + `<12>` bell harmonics + full idiomatic 6-string `fermata=True, arpeggio=True` closing chord |
