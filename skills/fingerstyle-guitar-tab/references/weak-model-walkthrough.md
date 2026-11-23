# Step-by-Step Walkthrough & Anti-Slop Guide (For Any Model)

Follow this mechanical recipe so you never hallucinate melodies, never do fret math in your head, and never produce a half-baked "四不像" tab.

---

## 0. 四不像反例 vs 合格指弹正例（写代码前必看）

### ❌ 错误示范（典型“四不像”，会被 `tab_engine.py check` 直接拦截）
```python
# 错因 1：只写单音主旋律，没有 role="bass" 和 role="inner"，听起来像干瘪电子门铃
# 错因 2：缺少 chords 和 section，且把同一小节复制粘贴 8 遍凑数
Measure(
    bar=1,
    events=[
        ev(0, 4, n(2, 0, "melody")),
        ev(4, 4, n(2, 3, "melody")),
        ev(8, 4, n(3, 2, "melody")),
        ev(12, 4, n(2, 0, "melody")),
    ],
)
```

### ✅ 合格示范（标准 4/4 三声部指弹小节模板）
```python
# 正拍 1 (slot 0)：主旋律 + 当前和弦根音 bass (+ 低于旋律的 inner 组成琶音)
# 弱拍 (slot 2/6/10)：用低于当前主旋律的 inner 分解填补空隙，制造流动感
# 正拍 3 (slot 8)：主旋律 + 换和弦根音 bass
# 拍尾 (slot 12/14)：乐句衔接或跨小节切分（若旋律延音跨小节，下一小节 slot 0 只拨 bass+inner，不重复拨旋律！）
Measure(
    bar=1,
    section="Verse",
    dynamic=0.90,
    chords=[(0, "G"), (8, "D/F#")],
    events=[
        ev(0, 2, n(6, 3, "bass"), n(3, 0, "inner"), n(2, 0, "melody"), arpeggio=True),
        ev(2, 2, n(4, 0, "inner")),
        ev(4, 2, n(2, 3, "melody")),
        ev(6, 2, n(3, 0, "inner")),
        ev(8, 2, n(6, 2, "bass"), n(3, 2, "melody")),
        ev(10, 2, n(4, 0, "inner")),
        ev(12, 4, n(2, 0, "melody"), n(4, 0, "inner")),
    ],
)
```

### 标准 4/4 指弹编配公式（直接套用不翻车）
1. **段落规划（至少 16 小节、至少 2 个 `section`）**：
   - `Intro`（4–8 小节，`dynamic=0.82–0.86`）：稀疏温柔，突出标志性动机；
   - `Verse`（8–16 小节，`dynamic=0.88–0.92`）：清晰「主旋律 + 根音低音 + 轻分解内声部」；
   - `Chorus`（8–16 小节，`dynamic=1.02–1.08`）：增加柱式琶音、16 分律动、击勾滑 `H/P/S` 或拍弦 `X`；
   - `Outro`（4 小节，`dynamic=0.78–0.84`）：放缓收尾，末小节长音 + 自然泛音 `<12>`。
2. **声部铁律**：
   - 主旋律 `role="melody"` 优先放在 **1–3 弦**（绝不把主旋律大面积丢到 5/6 低音弦）；
   - 低音 `role="bass"` 放在 **4–6 弦**，且**每逢和弦切换拍必须弹该和弦根音或和弦内音**（用 `fret_helper.py chord` 查）；
   - 内声部 `role="inner"` 的绝对音高必须严格 `<= 当前主旋律 MIDI`（绝不声部交叉）。

---

## Step 1: Ask the CLI Where to Put the Capo

Suppose the user gives an original numbered notation (简谱) in **1 = Bb**, **4/4**, **72 BPM**, and asks for **`beginner`** difficulty. Never guess capo math mentally. Run:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py recommend-capo --key Bb --tier beginner
```

Tool output tells you:
```text
Capo = 3 | G 调指法 | 顺阶和弦: G Am Bm C D Em | G调指法 (最易：G Em C D Am 均为经典开放和弦) <<< 推荐
```
Lock `CAPO = 3` (`G` chord shapes).

---

## Step 2: Convert Jianpu Melody Notes into `n(string, fret, "melody")`

Feed every jianpu token in order to `fret_helper.py jianpu`:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py jianpu --key Bb --capo 3 --tier beginner 3 5 2 3 1 7, 1 2
```

Tool output prints exact copy-paste strings and concert MIDI pitches:
```text
  3     -> D4   (62) : n(2, 0, "melody")   |   n(3, 4, "melody")
  5     -> F4   (65) : n(2, 3, "melody")   |   n(3, 7, "melody")
  2     -> C4   (60) : n(3, 2, "melody")   |   n(4, 7, "melody")
  1     -> A#3  (58) : n(3, 0, "melody")   |   n(4, 5, "melody")
  7,    -> A3   (57) : n(4, 4, "melody")
```
Pick the lowest-fret candidate on strings 1–3 printed by the tool, and record its concert MIDI (`62, 65, 60, ...`) into `LANDMARKS`.

---

## Step 3: Look Up Safe Accompaniment (`bass` & `inner`) Strictly Below Melody

Before placing bass or inner chord notes on a beat, pass the current melody `max_midi` from Step 2 to `fret_helper.py chord`:

```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py chord --chord G --capo 3 --max-midi 62 --tier beginner
```

For `G` (`max_midi=62`):
```text
  [Bass 低音弦位]
    n(6, 3, "bass")   -> A#2  (46) [ROOT 根音]
    n(4, 0, "bass")   -> F3   (53)
  [Inner 内声部弦位]
    n(2, 0, "inner")  -> D4   (62)
    n(3, 0, "inner")  -> A#3  (58)
```
When melody dips lower (e.g., `1 -> 58`), re-run with `--max-midi 58`. Copying the CLI output makes both **voice crossing** and **chord-bass mismatch** mathematically impossible.

---

## Step 4: Initialize Project & Write ONLY `src/score_data.py`

```bash
python3 skills/fingerstyle-guitar-tab/scripts/init_project.py \
  --dir my_song_tab --title "My Song" --artist "Artist Name" --key Bb --capo 3 --bpm 72 --tier beginner
```

Open `my_song_tab/src/score_data.py`:
1. **Delete `IS_STARTER_TEMPLATE = True`** (leaving it blocks `check`!).
2. Replace the starter bars with the full song (`>= 16` bars with `section=` and `dynamic=` contrast).
3. Fill `LANDMARKS` with exact concert MIDI from Step 2.

### Rhythmic Slot Cheat Sheet (16th-Note Units in 4/4)

Every full measure covers slots `0..15` summing to `16`. Valid `dur` values are ONLY `{1, 2, 3, 4, 6, 8, 12, 16}`:
- `1` = 16分音符 *(forbidden in `beginner`!)*
- `2` = 8分音符 · `3` = 附点8分 · `4` = 4分 · `6` = 附点4分 · `8` = 2分 · `12` = 附点2分 · `16` = 全音符
- Need `5` sixteenths? Split `4 + 1` (put `inner`/`bass` on the `+1` slot).
- Need cross-bar syncopation? Enter melody on slot `12` or `14`, and on Bar `N+1` slot `0` pluck **ONLY `bass` + low `inner`** (`tab_engine.py` automatically sustains melody up to 6 beats).

---

## Step 5: Self-Healing Check & One-Command Build

```bash
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py check my_song_tab
```
Fix EVERY reported error until stdout prints `[PASS] Score check OK | ... | XX分 | Lv.X.X`, then run:
```bash
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py build my_song_tab
```
Do not report completion to the user until stdout prints `ALL VERIFICATION CHECKS PASSED.` and `<title>_bundle.zip` exists.

---

## 1-Second Error-to-Fix Lookup Table (15 Common Gates)

| Validator Error Symptom | Root Cause | 1-Second Fix |
| :--- | :--- | :--- |
| `模板拦截: 仍带有 IS_STARTER_TEMPLATE = True` | Forgot to replace starter stub from `init_project.py` | Write the full song measures and delete `IS_STARTER_TEMPLATE = True` |
| `完整性拦截: 曲目过短（仅 X 个完整小节，至少需 16 小节）` | Wrote a 4–8 bar stub instead of a real song | Arrange full Intro/Verse/Chorus/Outro (`>= 16` bars); never abuse `ALLOW_SHORT_DEMO` for full song requests |
| `反敷衍拦截: 连续 4+ 个小节主旋律完全一模一样` / `唯一旋律小节 < 25%` | Copy-pasted 1–2 bars in a loop to fake length | Transcribe/arrange actual melodic variations across bars |
| `指弹结构拦截: 低音声部严重不足` | Wrote a bare single-note melody without `bass` | Add `role="bass"` chord root notes on beat 1/3 via `fret_helper.py chord` (`>= 65%` bars, `>= 3` pitches) |
| `指弹质感拦截: 复调饱满度过低` | Mostly 1 note per beat | Pluck `melody + bass` simultaneously on strong beats + `inner` chord tones in gaps |
| `声部倒置拦截: > 25% 主旋律落在 5/6 低音弦` | Put melody too low on bass strings | Re-lookup melody on strings 1–3 via `fret_helper.py jianpu` |
| `和声失配拦截: 和弦与低音严重不符` | Chord label (e.g. `G`) disagrees with the `bass` note played at that slot | Run `fret_helper.py chord --chord <Chord>` and replace the wrong bass note |
| `段落结构拦截: 缺少段落划分` / `和弦标记覆盖率 < 75%` | Missing `section=` or `chords=` annotations | Add `section="Intro"/"Verse"/"Chorus"/"Outro"` (`>= 2`) and `chords=[(0, "...")]` on every bar |
| `不可弹指法 — 同时按弦需 5+ 根手指` | AI-hallucinated chord impossible for 4 fingers + barre | Drop redundant `inner` note or use an open string |
| `物理跨度超限 (> 14.5cm)` / `品格跨度超过档位上限` | Simultaneous fretted notes stretch too far physically | Apply the printed alternate string/fret candidate or drop the outlier `inner` note |
| `把位跳动拦截: 存在过多高速大跳把位` | Rapid `>= 5` fret jumps in `<= 0.28s` | Relocate adjacent phrases to the same neck region using alternate strings |
| `声部交叉 — inner/bass 高于当前主旋律` | Accompaniment pitch > `active_melody_midi` | Copy one of the printed `-> 修复建议: 替换为 n(...)` alternatives |
| `调外音` / `防作弊拦截: ALLOW_CHROMATIC=True 但调外音 > 20%` | Wrong key/capo or mental fret-math errors | Re-run `fret_helper.py jianpu` with the true concert key & Capo; never mask wrong notes with `ALLOW_CHROMATIC` |
| `小节时值之和 != 16` / `dur 不是合法 LilyPond 时值` | Slot durations don't sum to 16 or used `dur=5/7/10` | Use only `{1,2,3,4,6,8,12,16}` (split `5 -> 4+1`, `7 -> 4+3`) summing to `16` |
| `综合质量门禁未达标 (< 75分 / < 68分)` | Weak playability, sparse arrangement, or missing notation | Check printed `(手型/编配/规范)` breakdown and strengthen the lowest sub-score |
