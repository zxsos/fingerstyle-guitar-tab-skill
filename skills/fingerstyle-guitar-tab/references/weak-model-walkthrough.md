# Step-by-Step Walkthrough (For Any Model)

Follow this mechanical 5-step recipe so you never have to calculate guitar frets, transposition, MIDI bytes, or LilyPond syntax in your head.

Suppose the user gives an original numbered notation (简谱) snippet in **1 = Bb**, **4/4**, **72 BPM**, and asks for **`beginner`** difficulty:

```text
Bar 1 (Bbmaj/G shape):  3(4分)  5(4分)  2(4分)  3(4分)
Bar 2 (Fm/Dm shape):    1(2分)  7,(4分) 1(4分)
```

---

## Step 1: Ask the CLI Where to Put the Capo

Never guess capo math mentally. Run:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py recommend-capo --key Bb --tier beginner
```

Tool output tells you:
```text
Capo = 3 | G 调指法 | 顺阶和弦: G Am Bm C D Em | G调指法 (最易：G Em C D Am 均为经典开放和弦) <<< 推荐
```
So lock `CAPO = 3` (`G` chord shapes).

---

## Step 2: Convert Jianpu Melody Notes into `n(string, fret, "melody")`

Feed every jianpu token in order to `fret_helper.py jianpu`:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py jianpu --key Bb --capo 3 --tier beginner 3 5 2 3 1 7, 1 2
```

Tool output prints exact copy-paste strings and concert MIDI pitches:
```text
简谱查弦: 1=Bb (1基准=A#3 midi=58) | Capo=3 (G调指法) | 档位=beginner (上限品=7)
------------------------------------------------------------------------------
  3     -> D4   (62) : n(2, 0, "melody")   |   n(3, 4, "melody")
  5     -> F4   (65) : n(2, 3, "melody")   |   n(3, 7, "melody")
  2     -> C4   (60) : n(3, 2, "melody")   |   n(4, 7, "melody")
  3     -> D4   (62) : n(2, 0, "melody")   |   n(3, 4, "melody")
  1     -> A#3  (58) : n(3, 0, "melody")   |   n(4, 5, "melody")
  7,    -> A3   (57) : n(4, 4, "melody")
```
**Key takeaway:** You never calculate open-string pitches + Capo + fret in your head—pick the lowest-fret candidate printed by the tool! Notice at `3 -> D4 (62)`: `max_midi = 62`; at `1 -> A#3 (58)`: `max_midi = 58`!

---

## Step 3: Look Up Safe Accompaniment (`bass` & `inner`) Strictly Below Melody

Before placing bass or inner chord notes on a beat, pass the current `max_midi` from Step 2 to `fret_helper.py chord`:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py chord --chord G --capo 3 --max-midi 62 --tier beginner
```

For `G` (`max_midi=62`):
```text
  [Bass 低音弦位]
    n(6, 3, "bass")   -> A#2  (46) [ROOT 根音]
    n(4, 5, "bass")   -> A#3  (58) [ROOT 根音]
    n(4, 0, "bass")   -> F3   (53)
    n(5, 2, "bass")   -> D3   (50)
  [Inner 内声部弦位]
    n(2, 0, "inner")  -> D4   (62)
    n(3, 0, "inner")  -> A#3  (58)
    n(4, 0, "inner")  -> F3   (53)
```
When melody dips lower (e.g., `1 -> 58` on Em/Dm), re-run with `--max-midi 58`! Because `fret_helper.py` filters `cm <= max_midi` and `fret <= tier.max_fret`, **voice crossing is impossible** when you copy its output!

---

## Step 4: Initialize Project & Write ONLY `src/score_data.py`

```bash
python3 skills/fingerstyle-guitar-tab/scripts/init_project.py \
  --dir my_song_tab --title "My Song" --key Bb --capo 3 --bpm 72 --tier beginner --bars 2
```

Open `my_song_tab/src/score_data.py`, paste your `Measure(...)` events, and fill `LANDMARKS`:

```python
LANDMARKS = {
    1: [62, 65, 60, 62],  # Copy directly from Step 2 Jianpu output!
    2: [58, 57, 58],
}
```

### Rhythmic Slot Cheat Sheet (16th-Note Units in 4/4)

Every measure covers slots `0..15` summing to `16`. Valid `dur` values are ONLY `{1, 2, 3, 4, 6, 8, 12, 16}`:
- `1` = 16分音符 *(forbidden in `beginner`!)*
- `2` = 8分音符
- `3` = 附点8分音符
- `4` = 4分音符
- `6` = 附点4分音符
- `8` = 2分音符
- `12` = 附点2分音符
- `16` = 全音符

Need a 5-sixteenth note? Split across `4 + 1` (put bass/inner on the `+1` sub-slot so melody isn't re-plucked).
Need a cross-bar syncopation? Let melody enter at slot `12` or `14`, and on Bar `N+1` slot `0` pluck **ONLY `bass` + low `inner`** (do not re-pluck `melody`—`tab_engine.py` sustains melody up to 6 beats!).

---

## Step 5: Self-Healing Check & One-Command Build

Run:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py check my_song_tab
```

Fix any reported lines using its built-in `-> 修复建议: ...`, then run:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py build my_song_tab
```

Wait for `ALL VERIFICATION CHECKS PASSED.` and `<title>_bundle.zip`.

---

## 1-Second Error-to-Fix Lookup Table

| Validator Error Symptom | Root Cause | 1-Second Fix |
| :--- | :--- | :--- |
| `小节时值之和=14 ... 少了 2 个 16 分时值` | Sum of `ev.dur` != `16` (or != `4/8/12` on pickup `M00`) | Add/adjust the last `ev(...)`'s `dur`, or append `ev(14, 2, n(...))` |
| `dur=5 不是合法 LilyPond 时值` | Only `{1,2,3,4,6,8,12,16}` allowed | Split `5` into `4` + `1` (or `6 -> 6`, `7 -> 4+3`, `10 -> 8+2`) |
| `档位 TIER='beginner' 禁止 16 分音符 (dur=1)` | `dur=1` used in `beginner` mode | Merge into `dur=2` (8th notes) OR change `TIER = "standard"` |
| `声部交叉 — inner n(2,0) ... 高于当前主旋律` | Accompaniment note pitch > `active_melody_midi` | Copy one of the printed `-> 修复建议: 替换为 n(...)` bass/inner alternatives |
| `品位跨度 3 超过档位 'beginner' 上限 (2)` | Non-open fretted notes in one chord span > `max_span` | Apply the printed `把 (s, f) 换成 n(...)` suggestion or drop extra inner note |
| `调外音 n(3,1) = C#4` | Accidental outside key's `DIATONIC_PCS` | Use printed adjacent diatonic fret, or set `ALLOW_CHROMATIC = True` if intentional |
| `档位 'beginner' 禁用 ...` (`H`/`P`/`S`/`slap`/`palm_mute`/`harmonic`) | Technique disallowed in `beginner` | Remove technique flag OR change `TIER = "standard"` |
| `Unsupported chord label` | Typo in `chords=[(slot, "...")]` | Use standard root + suffix like `G`, `Cmaj7`, `Am7`, `D/F#`, `Bm7b5`, `Gsus4` |
