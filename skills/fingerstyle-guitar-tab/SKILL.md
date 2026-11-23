---
name: fingerstyle-guitar-tab
description: Use when creating fingerstyle guitar tablature (6-line TAB) PDFs with synchronized acoustic audio previews and web practice bundles, converting MusicXML/MIDI/numbered-notation (jianpu) or songs into solo guitar arrangements across beginner/standard/advanced difficulty tiers, or fixing pitch/rhythm/voice-crossing/ergonomic drift.
---

# Fingerstyle Guitar TAB Engine

## The 4 Iron Laws (铁律 — 任何一条违反即视为不合格“四不像”)

1. **NO MEMORY HALLUCINATION (严禁凭记忆编造已知歌曲)** — If the user only gives a song name or a mixed/polyphonic MP3 (Tier C), **STOP IMMEDIATELY and ask** for MIDI / MusicXML / jianpu / staff sheet (or offer to search curated S/A sources first). LLMs cannot recall exact bar-by-bar pitches and rhythms from weights; guessing notes produces a "四不像" fake cover.
2. **ZERO MENTAL FRET MATH (禁止心算品格、和弦与泛音)** — Never compute open-string + Capo + fret pitches, chord roots, or 12th-fret harmonics in your head. Always run `python3 scripts/fret_helper.py` (`recommend-capo`, `jianpu`, `chord`, `harmonic`, `verify-melody`).
3. **MUSICAL 3-VOICE CRAFT — NEITHER BARE SINGLE-NOTE SLOP NOR CLUTTERED DOUBLE-STOP SPAM (拒绝干瘪单音，也拒绝弱拍双音轰炸)** — A real fingerstyle arrangement MUST have:
   - Complete song structure (`>= 16` full bars, `>= 2` distinct `section=` markers placed **ONLY on the first bar of each section**, dynamic arc);
   - **Singing melody with breathing room**: `role="melody"` on strings 1–3 (`>= 75%` bars, prefer open strings like `n(2, 0)` over `n(3, 4)`), **NEVER** clutter running weak-beat / 16th-note melody with simultaneous `inner` notes (weak-beat melody + `inner` must stay `<= 40%`, ideally `< 15%`; `dur=1` melody stays clean single-note or `H/P`);
   - **True root bass & clean inner flow**: `role="bass"` hits the **chord ROOT** (`>= 78%` on chord changes — e.g. `Cmaj7` uses `5弦3品 C`, NOT `6弦3品 G`; `D` uses `4弦0品 D`, NOT `5弦0品 A`); **NEVER** put `role="inner"` on string 6 (`inner` lives on strings 2–5, filling sustain/rest gaps strictly `<= active_melody_midi`);
   - **Section-driven idiomatic techniques**: restrained `arpeggio=True` ONLY on phrase-opening `>= 3`-note downbeats (`20%–45%` of bars, NEVER `> 65%`), real left-hand legato (`H`/`P`/`S` `>= 2` in `standard` tier), `slap` (`X`) strictly on 4/4 backbeats **Beat 2 (`slot=4`) & Beat 4 (`slot=12`)** inside the same `ev()` as the beat's melody/inner note, `palm_mute` on accompaniment only, and `<12>` harmonics via `fret_helper.py harmonic`;
   - **Playable physics & zero loop padding**: `0` chords needing `> 4` fingers, `0` stretches `> 14.5 cm`, `0` voice crossings, never repeat the same bar `>= 4` times in a row.
4. **NO BYPASSING THE ENGINE OR VALIDATOR (禁止作弊与跳过编译)** — You ONLY author `<project_dir>/src/score_data.py`. Never hand-write LilyPond/MIDI/audio scripts, never leave `IS_STARTER_TEMPLATE = True`, never silence errors by flipping `ALLOW_CHROMATIC = True` / `REQUIRED_TECHS = ()` / `ALLOW_SHORT_DEMO = True`, and **NEVER deliver to the user** until `python3 scripts/tab_engine.py build <proj>` finishes with `ALL VERIFICATION CHECKS PASSED.` and a real `*_bundle.zip`.

### Anti-Rationalization Table (常见偷懒借口与伪高分陷阱 → 强制纠偏)

| Tempting Excuse ("常见翻车习惯") | Hard Reality & Mandatory Action |
| :--- | :--- |
| "这首歌很有名，我大概记得旋律，直接编一段给用户" | **STOP.** Weight-memory melodies drift in key, bar length, and hook intervals. Pause at Step 0 and ask for S/A sheet/MIDI or search a real score first. |
| "先用 `init_project.py` 生成 4–8 小节改改就交付" | **BLOCKED.** `IS_STARTER_TEMPLATE = True` and `< 16` bars both hard-fail `tab_engine.py check`. Write the full arrangement unless the user explicitly requested a short snippet. |
| "为了凑够 16 小节，我把 2 个小节循环粘贴 8 遍" | **BLOCKED.** Consecutive repeats (`>= 4`) and low unique-bar ratio (`< 25%`) hard-fail `tab_engine.py check`. Arrange real Verse/Chorus progression. |
| "只写单音主旋律不容易声部交叉，也不容易报错" | **BLOCKED.** Single-note lines are NOT fingerstyle. Low bass coverage (`< 65%`), low polyphony, or score `< 75分` hard-fails `tab_engine.py check`. |
| "为了拉高复调分，我给每个弱拍旋律和 16 分音符都叠一个 inner，再每小节都加 `arpeggio=True`" | **BLOCKED.** Double-stop clutter (`> 40%` weak-beat melody w/ `inner`, `dur=1` w/ `inner`) and arpeggio spam (`> 65%` bars) both hard-fail `check` and make the audio sound muddy and robotic. Let running melody sing alone! |
| "和弦低音只要是和弦内音就行（Cmaj7 弹 6弦3品 G、D 弹 5弦0品 A），再把 6 弦当 inner 填一填" | **BLOCKED.** Root bass accuracy `< 78%` and ANY `role="inner"` on string 6 hard-fail `check`. Always take `[ROOT BASS]` and `[SAFE INNER]` from `fret_helper.py chord`. |
| "拍弦怕跟旋律冲突，我把拍子拆成 16 分把 `slap` 塞在 `slot=3`" | **BLOCKED.** `slap` outside backbeats (`slot 4 / 12 / 8`) hard-fails `check`. Always combine `ev(4, 2, n(6, 0, "slap"), n(2, 3, "melody"))` on Beat 2 & Beat 4. |
| "校验报调外音/技巧缺失，我设 `ALLOW_CHROMATIC=True` 或 `REQUIRED_TECHS=()` 过关" | **CHEATING.** `> 20%` out-of-key notes still hard-fails; fix wrong frets with `fret_helper.py jianpu` and add real `H/P/S` techniques instead. |
| "拿到了 AlphaTab / 吉他社 JSON，我自己手写个转换脚本生成 `score_data.py`" | **FORBIDDEN.** Hand-written parsers routinely invert AlphaTab strings (`string=1` is 6th low-E, NOT 1st high-E!), mis-divide ticks (`240/slot` vs `120`), wipe out H/P/S/grace/ties, and forge bogus `G/A` slash chords. Always run `python3 scripts/import_score.py <file.json>`. |
| "用户只是要谱子，我自己写个脚本拼个 PDF/文本谱更快" | **FORBIDDEN.** Always use `python3 scripts/tab_engine.py build <proj>` so PDF, 320k MP3, 4-Gate DSP verify, `sync_manifest.json`, and `*_bundle.zip` stay 100% synced. |

---

## Step 0: Mandatory Source-Purity Triage & Tier Selection (先问再做)

Classify user input BEFORE creating any project directory or writing any code:

| Purity Tier | User Input | Required Action |
| :--- | :--- | :--- |
| **S (Purest)** | `.mid` / `.midi` / `.musicxml` / `.xml` / `.mxl` / AlphaTab & Jitashe `.json` / Guitar Pro export | Confirm difficulty tier (`beginner`/`standard`/`advanced`) if unspecified, then run `python3 scripts/import_score.py` |
| **A (High)** | Original numbered notation (简谱 image/PDF), vocal/piano lead sheet | Confirm difficulty tier, extract exact bar-by-bar jianpu + chords, then use `python3 scripts/fret_helper.py` |
| **B (Medium)** | Clean solo vocal / solo piano audio | **PAUSE & ASK:** explain audio pitch drift vs sheet scores; ask for S/A sheet or permission to search a clean score first |
| **C (Low / Blurry)** | **Only a song name**, or full-band / polyphonic guitar MP3 | **HARD STOP — DO NOT WRITE ANY CODE.** Ask: (1) Do they have `.mid`/MusicXML/GP/简谱图? (2) Should you search the curated S/A source sites ([references/score-sources.md](references/score-sources.md)) first? (3) Which difficulty tier? |

**Curated Search Priority (禁止盲搜，严格按顺序定向检索，详见 [references/score-sources.md](references/score-sources.md)):**
- **S 级结构化（直导 `import_score.py`）**：华语指弹 `jitashe.org`（AlphaTab `.json`/`.gp5`）→ 欧美/日系/ACG `songsterr.com` · `guitarprotabs.org` · `gprotab.net` · `musescore.com`（`.mxl`/`.mid`）→ 游戏/古典/免登录 `bitmidi.com` · `vgmusic.com` · `mutopiaproject.org` · `free-scores.com` · `github.com`。
- **A 级高清图文（走 `fret_helper.py`）**：指弹六线 `yopu.co` · `ccguitar.cn` · `17jita.com` · `ultimate-guitar.com`（仅限 Tab/GP，禁 Chords）→ 原版简谱 `qupu123.com` · `jianpu.cn` · `tan8.com`。
- **硬红线**：最多 3 轮定向搜索；遇登录墙/付费墙/验证码直接换下一源站；严禁采信纯歌词和弦弹唱谱或残缺模糊图；3 轮无公开 S/A 源立刻停手向用户要谱。

### Difficulty Tiers (`TIER` in `src/score_data.py`)

| `TIER` | Target Player | Max Fret | Fret Span | Min Quality | Rhythm & Allowed Techniques |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`beginner`** | 新手入门 | `<= 7` (0–3 preferred) | `<= 2` | `>= 68分` | No 16ths (`dur != 1`), max 3 notes/beat (`melody + bass`), optional `arpeggio` only |
| **`standard`** *(default)* | 标准指弹 | `<= 12` | `<= 3` | `>= 75分` | Full 16th grid (`dur >= 1`), 3+ voices, `arpeggio` (`>= 1`) + `H/P/S/<12>/P.M./X` |
| **`advanced`** | 进阶独奏 | `<= 15` | `<= 4` | `>= 75分` | Dense walking bass + 16ths, `H/P/S/arpeggio/<12>` required + `P.M./X` |

See [references/intake-and-tiers.md](references/intake-and-tiers.md) for dialogue templates.

---

## Step 1: Author ONLY `<proj>/src/score_data.py`

Never touch or rewrite engine scripts. Complete reference example: [../../examples/call-of-silence/src/score_data.py](../../examples/call-of-silence/src/score_data.py).

**CRITICAL CAPO CHORD RULE:** `chords=[(slot, label)]` MUST ALWAYS use **Capo-relative fingered chord names** (e.g., for `1 = Bb` with `CAPO = 3`, write `G`, `D/F#`, `Em7`, `Bm7`, `Cmaj7`, `D` — NEVER concert `Bb`, `F/A`, `Gm7`). The engine adds `+ CAPO` automatically when validating harmony.

- **Path S (MIDI / MusicXML / AlphaTab JSON / Guitar Pro -> Code):**
  ```bash
  python3 scripts/import_score.py <input.mid|.musicxml|.mxl|.json|.gp5> --out <proj>/src/score_data.py --tier <tier> --artist "<Artist>"
  ```
  Polish `ARTIST`, `TITLE`, `section=` (first bar of each section ONLY), `chords=`, and phrasing dynamics as needed. Never hand-write a custom AlphaTab/GP JSON converter.
- **Path A (Jianpu / Sheet -> Code — zero mental math):**
  1. Recommend Capo & fingered chord set:
     ```bash
     python3 scripts/fret_helper.py recommend-capo --key <Key> --tier <tier>
     ```
  2. Convert EVERY melody phrase with `fret_helper.py jianpu` (pick the lowest-fret open/1–3 string candidate consistently — e.g. prefer open `n(2, 0)` over `n(3, 4)` — and record exact concert MIDI in `LANDMARKS`):
     ```bash
     python3 scripts/fret_helper.py jianpu --key <Key> --capo <N> --tier <tier> <tokens...>
     ```
  3. Batch-lookup ALL song chords' `[ROOT BASS]`, `[SAFE INNER]` (strings 2–5, NEVER string 6), and closing chord voicing in ONE command:
     ```bash
     python3 scripts/fret_helper.py chord --chord G D/F# Em7 Bm7 Cmaj7 D Am7 --capo <N> --max-midi <M> --tier <tier> --closing
     ```
  4. Look up `<12>` natural harmonics (if used for cadence/Outro — zero mental math):
     ```bash
     python3 scripts/fret_helper.py harmonic --key <Key> --capo <N>
     ```
  5. Scaffold `<proj>/src/score_data.py`:
     ```bash
     python3 scripts/init_project.py --dir <proj> --title "<Title>" --artist "<Artist>" --key <Key> --capo <N> --bpm <BPM> --tier <tier>
     ```
  6. **Write musical, DRY `build_measures()` and delete `IS_STARTER_TEMPLATE = True`**:
     - Extract repeated Verse/Chorus/Intro bar patterns into small Python helper functions (e.g. `_verse_line1(bar, dynamic, lyrics, section=None)`, `_chorus_hook(bar, dynamic, slap=False)`) to eliminate copy-paste typos and cleanly switch `slap=False` → `slap=True`.
     - Put `section=` **ONLY on the first bar** of each section (`Intro`/`Verse`/`Chorus`/`Interlude`/`Outro`); preserve `lyric=` on vocal melody notes.
     - Keep a steady 8th-note groove: **strong beats (`0`/`8`)** carry chord `[ROOT BASS]` + melody (+ `inner` + `arpeggio=True` on phrase openings); **weak-beat / 16th running melody** sings alone (plus `H`/`P`/`S` on same-string stepwise pairs); **sustain/rest gaps** flow with single `[SAFE INNER]` notes; cross-bar syncopated ties never re-pluck the held melody on bar `N+1` slot 0.

**READ BEFORE WRITING `score_data.py`:** [references/weak-model-walkthrough.md](references/weak-model-walkthrough.md) has the exact Good-vs-Bad templates (including anti-double-stop-clutter), 6-technique placement rules, helper-function pattern, and 20-error repair table.

---

## Step 2: Melody Audit + Self-Healing Hard Gate Loop (双校验不通过禁止构建)

1. **Mandatory Source-Melody Diff (Path A — 0 dropped/wrong notes):**
   ```bash
   python3 scripts/fret_helper.py verify-melody --score <proj> --key <Key> --spec <jianpu_spec.txt>
   ```
   Confirm `[PASS] 主旋律逐小节核对 100% 匹配` before moving on (or populate `LANDMARKS` across every section's key bars when working directly from an image).
2. **Engine Mechanical & Musical Gate Check:**
   ```bash
   python3 scripts/tab_engine.py check <proj>
   ```
   `check` enforces ALL physical + musical gates:
   - No `IS_STARTER_TEMPLATE`, `>= 16` bars, no `>= 4` consecutive identical bars, unique melody ratio `>= 25%`, no duplicate consecutive `section=`;
   - Bar slot sum `== 16`, legal `dur` in `{1,2,3,4,6,8,12,16}`, `0` voice crossings, `0` `inner` on string 6, `0` impossible `>4`-finger chords, `0` physical stretches `> 14.5 cm`;
   - Melody coverage `>= 75%` (high strings 1–3), bass coverage `>= 65%` (`>= 3` pitches), **chord-root bass hit rate `>= 78%`**, chord labels `>= 75%`, `>= 2` sections;
   - **Melody breathing & technique hygiene**: weak-beat melody w/ `inner` `<= 40%`, `dur=1` melody w/ `inner` `<= 25%`, `arpeggio` bars `<= 65%`, `slap` strictly on backbeats (`slot 4/12/8`), `standard` tier `H+P+S >= 2`;
   - Honest quality score `>= 75分` (`>= 68分` beginner, sweet-spot polyphony `1.14–1.48`) + difficulty `Lv. 1.0–10.0`.

   Fix EVERY reported `-> 修复建议: ...` until `[PASS] Score check OK | ... | XX分 | Lv.X.X`.

---

## Step 3: Build, Verify & Package in One Command (未通过禁止交付)

If `check` prints `[TIP] 未检测到 LilyPond/FluidSynth`, install the shared toolchain once into the skill root (`bash scripts/setup_tools.sh <skill_root>`, reused by all future projects), then run:

```bash
python3 scripts/tab_engine.py build <proj>
```

- Auto-pagination balances systems across pages cleanly (override with `PAGE_BREAKS = [...]` in `score_data.py` if custom page turns are desired).
- Pass `--keep-build` to retain `build/*.ly` and `build/*.mid` when inspecting LilyPond engraving details.
- Pass `--ref <audio_path_or_url>` for optional Gate 4 chroma alignment (`>= 0.80`). Never commit third-party URLs into code.

Confirm stdout ends with `ALL VERIFICATION CHECKS PASSED.` and outputs:
- `<Title>_指弹吉他六线谱.pdf` (sanitized, 0 path leaks)
- `<Title>_指弹吉他试听.mp3` (320 kbps, stereo balanced, diatonic verified)
- `sync_manifest.json` + `previews/page-*.png`
- `<Title>_bundle.zip` (contains `score_data.py`, ready to drag into `https://zxsos.github.io/guitar-tab-player/`)

---

## Reference Library (Load On Demand)

- [references/score-sources.md](references/score-sources.md) — **Read before searching any song:** Curated 17-site S/A source directory (`jitashe`, `songsterr`, `musescore`, `guitarprotabs`, `bitmidi`, `yopu`, `ccguitar`, `qupu123`, etc.), exact `site:` query templates, and accept/reject red lines.
- [references/weak-model-walkthrough.md](references/weak-model-walkthrough.md) — **Read first if unsure:** Good vs Bad ("四不像反例 vs 合格正例"), 4/4 fingerstyle bar formula, worked `fret_helper.py` tutorial, and 15-error 1-second repair table.
- [references/intake-and-tiers.md](references/intake-and-tiers.md) — S/A/B/C purity triage, user prompt scripts, and tier profiles.
- [references/score-schema.md](references/score-schema.md) — `score_data.py` fields, universal chord labels, voice-leading & syncopated tie rules.
- [references/lilypond-tab-patterns.md](references/lilypond-tab-patterns.md) — LilyPond 2.24 engraving rules, `\slashedGrace`, consecutive `H`/`P`, `\glissando`, CJK fonts.
- [references/audio-and-verification.md](references/audio-and-verification.md) — Velocity SNR (`98/74/48`), MIDI sustain caps, WAV thumb slaps, 4-Gate verification thresholds.
