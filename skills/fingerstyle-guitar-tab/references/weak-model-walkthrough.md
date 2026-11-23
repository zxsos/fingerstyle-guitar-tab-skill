# Step-by-Step Walkthrough & Musical Craft Guide (授人以渔 · 指弹编配实战手册)

Follow this mechanical + musical recipe so you never hallucinate melodies, never do fret math in your head, never produce bare single-note slop, and **never produce cluttered robotic "double-stop spam"**.

---

## 0. 三种对比示范：单音敷衍 ❌ vs 臃肿乱塞 ❌ vs 真正好听的指弹 ✅

### ❌ 反例 A：干瘪单音敷衍谱（缺乏低音与内声部）
```python
# 错因：只有单音 melody，没有 bass/inner/chords/section，听起来像电子门铃
Measure(bar=1, events=[
    ev(0, 4, n(2, 0, "melody")),
    ev(4, 4, n(2, 3, "melody")),
    ev(8, 4, n(3, 2, "melody")),
    ev(12, 4, n(2, 0, "melody")),
])
```

### ❌ 反例 B：机械臃肿“双音轰炸”谱（轻量模型最常见翻车！会被新门禁拦截）
```python
# 错因 1（根音错位）：Cmaj7 标了和弦却把 6弦3品(G=5音) 当 bass，把真正的根音 5弦3品(C) 塞去当 inner！
# 错因 2（6弦当内声部）：D 和弦空隙把 n(6, 2, "inner") 标成了 inner，低音浑浊倒置！
# 错因 3（弱拍双音轰炸）：每个弱拍旋律（slot 4/6/12/14 甚至 16 分音符）都强叠一个 inner，听起来像机械打字机！
# 错因 4（琶音滥用 + 选弦别扭）：空旋律小节也加 arpeggio=True；能用空弦 n(2,0) 却用 n(3,4)！
# 错因 5（拍弦乱塞 + 零连奏）：把 slap 拆成 16 分塞到 slot=3，且全曲 H/P/S 全为 0！
Measure(bar=17, chords=[(0, "Cmaj7"), (8, "D")], events=[
    ev(0, 2, n(6, 3, "bass"), n(5, 3, "inner"), n(1, 3, "melody"), arpeggio=True),  # ❌ Cmaj7 根音应是 5弦3品！
    ev(2, 2, n(4, 2, "inner")),
    ev(4, 2, n(1, 2, "melody"), n(4, 0, "inner")),   # ❌ 弱拍旋律不要强叠 inner！
    ev(6, 2, n(1, 0, "melody"), n(5, 2, "inner")),   # ❌ 同上！
    ev(8, 2, n(5, 0, "bass"), n(4, 0, "inner")),     # ❌ D 根音应是 4弦0品，不是 5弦0品 A！
    ev(10, 2, n(6, 2, "inner")),                     # ❌ 严禁把 6 弦标成 inner！
    ev(12, 2, n(2, 3, "melody"), n(4, 0, "inner")),  # ❌ 弱拍旋律强叠 inner！
    ev(14, 2, n(3, 4, "melody"), n(4, 0, "inner")),  # ❌ 优先用空弦 n(2,0) 而非 n(3,4)！
])
```

### ✅ 正例：清澈、会呼吸的 4/4 标准三声部指弹（好听且顺手！）
```python
# 正拍 1 (slot 0)：Cmaj7 真正根音 5弦3品 + 内声部 4弦2品 + 旋律 1弦3品（3音柱式才加 arpeggio=True）
# 旋律间隙 (slot 2 / slot 10)：单音流动 inner 填补空隙，保持稳健 8 分音符律动
# 弱拍旋律 (slot 4/6/12/14)：让主旋律单独歌唱（或配合 Chorus II 反拍 slap X）！绝不强叠多余 inner！
# 正拍 3 (slot 8)：D 和弦真正根音 4弦0品（前拍旋律延音跨拍时，只拨 bass，不重复拨旋律）
Measure(
    bar=17,
    dynamic=0.96,
    chords=[(0, "Cmaj7"), (8, "D")],
    events=[
        ev(0, 2, n(5, 3, "bass"), n(4, 2, "inner"), n(1, 3, "melody"), arpeggio=True),
        ev(2, 2, n(3, 0, "inner")),
        ev(4, 2, n(1, 2, "melody"), link_tech="P"),
        ev(6, 2, n(1, 0, "melody")),
        ev(8, 2, n(4, 0, "bass")),
        ev(10, 2, n(3, 2, "inner")),
        ev(12, 2, n(2, 3, "melody"), link_tech="P"),
        ev(14, 2, n(2, 0, "melody")),
    ],
)
```

---

## 1. 好听指弹的 5 条核心编配心法（适用任意歌曲）

### 心法 1：根音立骨架 + 严禁 6 弦当内声部
- `chords=` **永远填写 Capo 下的指法和弦名**（例如 `1=Bb, Capo 3` 写 `G / D/F# / Em7 / Bm7 / Cmaj7 / D`，绝不写原调发声名 `Bb / Ebmaj7 / F`）。
- 每次和弦切换（`slot 0` 或 `slot 8`），`bass` 必须直接按 `fret_helper.py chord` 打印的 **`[ROOT BASS]`** 取值，**不要凭记忆背弦号**（例如 G 调指法下 `Cmaj7` 根音在 5 弦 3 品、`D` 根音在 4 弦 0 品，其实际发声音高会自动加上 `CAPO`；遇到高把位旋律跨度超限时，可用工具同时打印的 `[高把位根音]` 如 `n(6, 8, "bass")`）。
- `role="inner"` 只放 **2–5 弦（优先 3–4 弦）**，**绝不能放 6 弦**，且绝对音高严格 `<= active_melody_midi`。
- 每个 `(slot, chord_label)` 处都必须有起始于该 `slot` 的事件（不能被前一音符延音跨过），且 `CHORD_LEGEND` 控制在 80 字符以内避免首页图例框右侧溢出。

### 心法 2：主旋律呼吸法则（拒绝弱拍双音轰炸！）
- **强拍（`slot 0` / `slot 8` 换和弦）**：`bass + melody` 齐奏；若为乐句起拍，再加 1 个中弦 `inner` 并设 `arpeggio=True`。
- **弱拍流动旋律（`slot 2/4/6/10/12/14`）与全部 16 分音符（`dur=1`）**：**只写干净的 `melody`（或 `H/P/S` 连奏）**，不要硬塞 `inner` 同时拨响！这样吉他高音旋律才能清澈歌唱（全曲 `avg_poly` 自然落在最佳甜点区 `1.15–1.42`）。
- **延音/休止空隙（无新旋律起音的 8 分格）**：填 1 个流动的 `[SAFE INNER]` 分解音，维持连贯的 8 分音符推进感。
- **跨小节切分延音**：若拍尾（如 `slot 14`）主旋律延音进入下一小节，下一小节 `slot 0` **只拨新和弦根音 `bass`**（随后 `slot 2/4/6` 拨低位 `inner`），不要重新起音打断延音！

### 心法 3：六大指弹技巧的正确落位（服务段落情绪，绝不机械堆砌）

| 技巧 | 正确用法与代码写法 | 严禁犯的错 |
| :--- | :--- | :--- |
| **琶音 `arpeggio=True`** | **只用在乐句起拍/情感重拍的 3 音及以上柱式和弦**（全曲占比 **20%–45%**），以及末小节收尾全音符和弦。 | ❌ 每小节都加 `arpeggio=True`（100% 滥用）；空旋律 2 音小节乱加琶音。 |
| **击弦/勾弦/滑音 `H`/`P`/`S`** | 扫描每段乐句，凡**相邻两个旋律音在同一根弦、品格差 1–3 品**（尤其下行的 `2弦1→0`、`3弦2→0`、`2弦3→0` 或上行 `3弦0→2`），用 `link_tech="H"` / `"P"` / `"S"` 串联，如：<br>`ev(8, 2, n(5, 2, "bass"), n(2, 1, "melody"), link_tech="P"),`<br>`ev(10, 2, n(2, 0, "melody"))` | ❌ 完整标准曲目 `H=0, P=0, S=0`；或把 `link_tech` 写在小节最后一个事件上。 |
| **滑入倚音 `grace_from`** | 在前奏铃音、副歌钩子起音、最高潮**上行/起拍主音**上加 1–2 品顺向滑入：`n(2, 3, "melody", grace_from=1)` / `n(1, 7, "melody", grace_from=5)`。 | ❌ 同小节下行旋律（如 `3'→2'`）上加反向低品上滑（如 `grace_from=3` 滑到 5 品，听感像插错音）；或把 `grace_from` 当成 `S` 而不写真实 `H/P/S`。 |
| **右手闷音 `palm_mute=True`** | 用在间奏前半段或主歌铺垫段，**只加在 `bass` 和 `inner` 上**，主旋律 `melody` 保持不闷音，形成「闷音贝斯脉动 + 清澈高音旋律」的质感。 | ❌ 给主旋律加闷音，或在每个弱拍旋律上再强叠闷音内声部。 |
| **拍弦 `slap` (`X`)** | 用在高潮副歌二（Chorus II），**必须落在 4/4 反拍第 2 拍 (`slot=4`) 和第 4 拍 (`slot=12`)**，且**直接和该拍旋律/内声部写在同一 `ev()` 中**：<br>`ev(4, 2, n(6, 0, "slap"), n(1, 2, "melody"))`<br>`ev(12, 2, n(6, 0, "slap"), n(2, 3, "melody"))` | ❌ 把 8 分音符拆成 16 分把 `slap` 塞到 `slot=3`！ |
| **自然泛音 `harmonic=True`** | 先跑 `fret_helper.py harmonic --key <Key> --capo <N>` 查出各弦 12 品对应简谱音高，用在副歌一收束或尾奏点缀：`ev(10, 2, n(3, 12, "melody", harmonic=True))`。 | ❌ 心算 12 品泛音弦号。 |

### 心法 4：选弦一致性 + 地道收尾和弦
- 优先选 **1–3 弦空弦与 0–3 品低把位**：同一音高有 `n(2, 0)` 时绝不绕远去按 `n(3, 4)`；同一小节相邻重复音必须保持同一弦同一品。
- **末小节全音符收尾**：直接复制 `fret_helper.py chord --chord <Tonic> --capo <N> --max-midi 84 --closing` 打印的完整地道开放和弦指型（例如 `G` 调指法 6 弦全按 `6:3, 5:2, 4:0, 3:0, 2:0, 1:3`），绝不写怪异高把位内声部。
- **`section=` 只写在每个段落的第一小节**（如 M01 `Intro`、M09 `Verse I`、M25 `Chorus I`、M35 `Interlude`、M43 `Verse II`、M51 `Chorus II & Outro`），不要每小节重复写！

### 心法 5：用 Python 辅助函数复用乐句（防 68 小节手抄漂移！）
长曲目（32–80 小节）不要复制粘贴 1000 行裸 `Measure(...)`。把重复出现的主歌/副歌乐句抽成短小的辅助函数，用参数控制 `bar`、`dynamic`、`lyrics`、`slap` 和 `section`：

```python
def _chorus_phrase(bar: int, dynamic: float, *, slap: bool = False, section: Optional[str] = None) -> Measure:
    """Reusable Chorus bar: slap=False in Chorus I, slap=True in Chorus II."""
    s4 = (n(6, 0, "slap"), n(1, 2, "melody")) if slap else (n(1, 2, "melody"),)
    s12 = (n(6, 0, "slap"), n(2, 3, "melody")) if slap else (n(2, 3, "melody"),)
    return Measure(
        bar=bar, section=section, dynamic=dynamic,
        chords=[(0, "Cmaj7"), (8, "D")],
        events=[
            ev(0, 2, n(5, 3, "bass"), n(4, 2, "inner"), n(1, 3, "melody"), arpeggio=True),
            ev(2, 2, n(3, 0, "inner")),
            ev(4, 2, *s4),
            ev(6, 2, n(1, 0, "melody")),
            ev(8, 2, n(4, 0, "bass")),
            ev(10, 2, n(3, 2, "inner")),
            ev(12, 2, *s12),
            ev(14, 2, n(2, 0, "melody")),
        ],
    )
```
这样做既能保证乐句高度统一、轻松实现 Chorus I → Chorus II 的反拍拍弦升级，又大幅降低代码行数与漏音概率！

---

## 2. 零心算完整 CLI 流水线示范（Path A 简谱转指弹）

### Step 1: 选变调夹（Capo）
```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py recommend-capo --key Bb --tier standard
```
输出推荐 `Capo = 3 | G 调指法`。锁定 `CAPO = 3`，所有 `chords=` 一律写 G 调指法和弦名。

### Step 2: 查主旋律弦位与 `concert_midi`
```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py jianpu --key Bb --capo 3 --tier standard 5, 7, 1 2 3 4 5 6 7 1' 2' 3'
```
挑打印结果里第一个低品/空弦 1–3 弦候选（并把关键小节 MIDI 记入 `LANDMARKS`）。

### Step 3: 一条命令批量查全曲和弦的根音低音、安全内声部与收尾指型
```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py chord \
  --chord G D/F# Em7 Bm7 Cmaj7 D Am7 Dsus4 G/B --capo 3 --max-midi 62 --tier standard --closing
```
- 换和弦拍一律用 `★ [ROOT BASS]`；
- 空隙填充一律用 `★ [SAFE INNER]`（绝不碰 6 弦）；
- 当旋律低于 `62`（如 `1=58` 或 `7,=57`）时，用 `--max-midi 57` 复查一次，确保 0 声部交叉；
- 末小节直接套用主和弦的 `★ [CLOSING CHORD]`。

### Step 4: 查自然 12 品泛音（如需）
```bash
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py harmonic --key Bb --capo 3
```
直接复制对应简谱音高的 `n(s, 12, "melody", harmonic=True)`，零心算。

### Step 5: 初始化项目 & 撰写 `src/score_data.py`
```bash
python3 skills/fingerstyle-guitar-tab/scripts/init_project.py \
  --dir my_song_tab --title "My Song" --artist "Artist Name" --key Bb --capo 3 --bpm 106 --tier standard
```
删掉 `IS_STARTER_TEMPLATE = True`，写入完整曲谱（利用辅助函数 + 完整 `lyric=` + 段首 `section=` + 动态递进 `dynamic=`）。

### Rhythmic Slot Cheat Sheet (16th-Note Units in 4/4)
Every full measure covers slots `0..15` summing to `16`. Valid `dur` values are ONLY `{1, 2, 3, 4, 6, 8, 12, 16}`:
- `1` = 16分音符 *(forbidden in `beginner`!)* · `2` = 8分 · `3` = 附点8分 · `4` = 4分 · `6` = 附点4分 · `8` = 2分 · `12` = 附点2分 · `16` = 全音符
- Need `5` sixteenths? Split `4 + 1` (put `inner`/`bass` on the `+1` slot).

### Step 6: 双校验自愈 + 一键构建交付
```bash
# 1) 若有简谱文本 spec，先做 100% 逐小节旋律比对（0 漏音、0 拍位漂移）
python3 skills/fingerstyle-guitar-tab/scripts/fret_helper.py verify-melody --score my_song_tab --key Bb --spec jianpu.txt

# 2) 跑全套物理 + 乐理 + 织体门禁检查
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py check my_song_tab

# 3) 若 check 提示缺工具，先装一次共享工具链，再一键编译验证打包
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py build my_song_tab
```
*(需要排查 LilyPond 记号时加 `--keep-build`；分页引擎已默认按 4–5 行/页均衡分页，如需微调可在 `score_data.py` 设置 `PAGE_BREAKS = [...]`)*

---

## 3. 1-Second Error-to-Fix Lookup Table (21 Common Gates)

| Validator Error Symptom | Root Cause | 1-Second Fix |
| :--- | :--- | :--- |
| `模板拦截: 仍带有 IS_STARTER_TEMPLATE = True` | Forgot to replace starter stub | Write full song measures and delete `IS_STARTER_TEMPLATE = True` |
| `完整性拦截: 曲目过短 (< 16 小节)` | Wrote a 4–8 bar stub | Arrange full Intro/Verse/Chorus/Outro (`>= 16` bars) |
| `反敷衍拦截: 连续 4+ 小节相同` / `唯一旋律 < 25%` | Copy-pasted 1–2 bars in a loop | Arrange real melodic progression across phrases |
| `段落标记重复: 与上一小节重复标注相同 section` | Put `section=` on every bar | Keep `section=` ONLY on the first bar of each section |
| `和弦根音拦截: 根音命中率 < 78%` | Played 5th/3rd in bass (e.g. `Cmaj7` on 6th str G, `D` on 5th str A) or wrote concert chord names instead of Capo fingered names | Verify `chords=` uses Capo fingered names, and replace bass with `[ROOT BASS]` from `fret_helper.py chord` |
| `声部错位拦截: 把 6 弦标成了 role='inner'` | Assigned string 6 to `inner` | Move `inner` to strings 2–5 (`[SAFE INNER]`); string 6 is strictly `bass` or `slap` |
| `织体臃肿拦截: 弱拍主旋律 > 40% 叠加 inner` | Hammered `inner` onto every melody note | Delete redundant `inner` on weak-beat melody slots so melody sings cleanly; fill `inner` only on sustain/rest gaps |
| `16分织体拦截: dur=1 旋律叠加 inner` | Attached `inner` to fast 16th-note melody | Keep `dur=1` melody clean single-note or `H`/`P` legato |
| `琶音滥用拦截: > 65% 小节加了 arpeggio=True` | Added `arpeggio=True` to every bar | Keep `arpeggio=True` ONLY on phrase-opening `>= 3`-note downbeats (`20%–45%` of bars) and final chord |
| `拍弦节拍拦截: slap 落在非反拍细分格` | Split 16th note to put `slap` on `slot=3` | Move `n(6, 0, "slap")` into Beat 2 (`slot=4`) & Beat 4 (`slot=12`) alongside that beat's melody/inner |
| `反向滑入倚音` | Put upward `grace_from` on a descending melody note in the same bar (sounds like an injected wrong note) | Remove `grace_from` on the descending note, or move the upward slide-in onto an ascending phrase peak |
| `和弦标记落在延音/空档中` | A chord `(slot, label)` has no event starting at `slot` | Split the previous event's `dur` and place that chord's `[ROOT BASS]` event at `slot` |
| `连奏表现力拦截: H+P+S < 2` | Mechanical plucking with 0 hammer-on/pull-off/slide | Add real `link_tech="H"`/`"P"`/`"S"` on same-string stepwise melody pairs (e.g. `2:1->2:0`, `3:2->3:0`, `3:0->3:2`) |
| `指弹结构拦截: 低音声部严重不足` | Bare single-note melody without `bass` | Add `[ROOT BASS]` on beat 1/3 via `fret_helper.py chord` |
| `指弹质感拦截: 复调饱满度过低` | Mostly 1 note per beat | Pinch `bass + melody` on strong beats + `inner` chord tones in gaps |
| `声部倒置拦截: > 25% 主旋律落在 5/6 弦` | Melody placed too low on bass strings | Re-lookup melody on strings 1–3 via `fret_helper.py jianpu` |
| `和声失配拦截: 和弦与低音严重不符` | Chord label disagrees with bass note | Check Capo-fingered chord name and use `[ROOT BASS]` from `fret_helper.py chord` |
| `段落结构拦截` / `和弦标记覆盖率 < 75%` | Missing `section=` or `chords=` | Add `>= 2` section headers and `chords=[(0, "...")]` on every bar |
| `不可弹指法 — 需 5+ 根手指` | Impossible chord shape | Drop redundant `inner` note or use an open string |
| `物理跨度超限 (> 14.5cm)` / `品格跨度超上限` | Fret span too wide | Apply the printed alternate string/fret candidate or drop outlier `inner` |
| `把位跳动拦截: 高速大跳把位` | Rapid `>= 5` fret jumps in `<= 0.28s` | Relocate adjacent notes to same neck region (prefer open strings) |
| `声部交叉 — inner/bass 高于主旋律` | Accompaniment pitch > `active_melody_midi` | Replace with one of the printed `-> 修复建议` candidates |
| `调外音` / `小节时值之和 != 16` / `综合质量 < 75分` | Fret math error, slot sum != 16, or weak sub-score | Re-lookup via `fret_helper.py jianpu`, fix `dur` in `{1,2,3,4,6,8,12,16}`, and strengthen lowest `(手型/编配/规范)` score |
