# Source-Purity Triage, Multi-Format Conversion & Difficulty Tiers

## 1. Mandatory Pre-Arrangement Triage (Step 0)

Always evaluate the input's signal-to-noise ratio before writing `score_data.py`. Low-purity polyphonic guitar MP3s mislead spectrum/F0 extractors (strong 2nd/3rd harmonics drop repeated notes and snap hooks to chord inner voices).

| Purity | Input Examples | Recommended Workflow | Ask User Before Starting? |
| :--- | :--- | :--- | :--- |
| **S (Purest)** | `.mid`, `.midi`, `.musicxml`, `.xml`, `.mxl`, AlphaTab/Jitashe `.json`, `.gp3`/`.gp4`/`.gp5` | `import_score.py` -> polish `score_data.py` -> `tab_engine.py build` | Confirm difficulty tier (`beginner` / `standard` / `advanced`) if unspecified |
| **A (High)** | Original numbered notation (简谱 image/PDF), vocal/piano lead sheet, high-res solo TAB | `fret_helper.py` + `init_project.py` -> visual bar-by-bar entry -> `tab_engine.py build` | Confirm difficulty tier (and vocal octave if ambiguous) |
| **B (Medium)** | Clean solo vocal acapella or solo piano recording | Search curated S/A sites ([score-sources.md](score-sources.md)) first; use audio only for BPM & Gate 4 chroma check | **Yes** — ask whether they have a sheet/MIDI file or want you to search curated S/A sites first |
| **C (Low)** | **Song title only**, full-band pop mix, or complex polyphonic guitar solo MP3 | Never transcribe melody solely from polyphonic audio FFT; search curated S/A sites ([score-sources.md](score-sources.md)) only after user confirms | **Yes (Mandatory Stop)** — run the confirmation prompt below |

### Standard Confirmation Prompt (Use When Input is B/C or Tier Unspecified)

In Chinese (or matching user language):

> 为了保证主旋律和节奏不跑偏（纯吉他或混音音频直接频谱反推很容易漏掉同音反复、把和弦内音当成主旋律），在开始编配前想跟您确认两件事：
> 1. **信源选择**：您手头有这首歌的 **MusicXML / MIDI / Guitar Pro 文件** 或 **原版简谱/五线谱图片**吗？如果没有，要不要我先帮您搜索更纯净的原版简谱/旋律谱资源，再开始转谱？
> 2. **难度档位**：您希望编配成哪个难度？
>    - **`beginner`（新手入门档）**：开放调/低把位（0–7品，偏好 0–3 品）、指法跨度 ≤ 2 品、无 16 分音符、清晰二声部（主旋律 + 低音）、仅基础拨弦/简单琶音
>    - **`standard`（标准指弹档，推荐默认）**：0–12 品、跨度 ≤ 3 品、含 16 分音符律动与跨小节切分延音、标准三声部 + 常用击勾滑泛音
>    - **`advanced`（进阶演奏档）**：0–15 品、跨度 ≤ 4 品、丰富经过低音与完整技巧（击勾滑 `H/P/S`、自然泛音 `<12>`、闷音 `P.M.`、拍弦 `X`）

---

## 2. Multi-Directional Conversion Map

```text
  [MIDI / MusicXML / .mxl] ──(import_score.py)──┐
                                                ├──> src/score_data.py ──(tab_engine.py build)──> *.pdf (TAB)
  [简谱 / 五线谱图片] ──────(fret_helper.py)─────┤         ^                                        *.mp3 (320k Audio)
                                                │         │                                        sync_manifest.json
  [纯净音频 / 歌名搜谱] ────(先找S/A谱确认)──────┘    check 报错自愈                               *_bundle.zip (在线练琴)
```

---

## 3. Hard-Validated Difficulty Tiers (`TIER_PROFILES`)

Every tier enforces deterministic thresholds inside `scripts/tab_common.py -> validate_project_score()`:

| Field | `beginner` | `standard` | `advanced` |
| :--- | :--- | :--- | :--- |
| `max_fret` (non-harmonic) | `7` | `12` | `15` |
| `max_span` (simultaneous fretted notes) | `2` | `3` | `4` |
| `max_simultaneous` (notes per slot) | `3` | `6` | `6` |
| `allow_16th` (`dur == 1`) | `False` | `True` | `True` |
| `allowed_techs` | `{"arpeggio"}` | `{"H","P","S","grace","arpeggio","harmonic","palm_mute","slap"}` | All |
| Default `required_techs` | `()` (none required) | `("arpeggio",)` | `("H","P","S","arpeggio","harmonic")` |
| Voice crossing (`bass/inner > active_melody_midi`) | **Forbidden (0)** | **Forbidden (0)** | **Forbidden (0)** |
| Landmark pitch assertions (`LANDMARKS`) | Checked if non-empty | Checked if non-empty | Checked if non-empty |

> Note: Projects may explicitly set `REQUIRED_TECHS = (...)` or `ALLOW_CHROMATIC = True` inside `src/score_data.py` when a specific piece requires passing chromatic tones or fewer mandatory techniques.
