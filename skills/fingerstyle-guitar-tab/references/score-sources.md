# Curated Score Sources & Targeted Search Protocol (定向源站清单，严禁盲搜)

When the user asks you to search for a score (Step 0 Tier B/C) or needs a cleaner S/A source, **NEVER run unfocused open-web searches or scrape random SEO blogs**. Follow the strict 3-round priority lookup below.

---

## 1. Strict 3-Round Search Order (定序定向检索)

1. **Round 1 — S-Tier Structured Guitar/Score Sources** (AlphaTab JSON, `.gp3/.gp4/.gp5`, `.mxl`/`.musicxml`, `.mid`) -> feed directly into `python3 scripts/import_score.py`.
2. **Round 2 — A-Tier Clean Fingerstyle TAB / Official Jianpu** (high-resolution 6-line fingerstyle TAB or numbered notation with melody + chords) -> convert bar-by-bar via `python3 scripts/fret_helper.py`.
3. **Stop Rule (3-Round Hard Stop):** If 3 targeted rounds find no accessible, unpaywalled S/A source, **STOP IMMEDIATELY** — list the sources you checked and ask the user to supply a `.mid`/`.musicxml`/`.gp5`/简谱 image. **NEVER** fall back to fabricating notes from memory or scraping chord-only lyric sheets.

> **Compliance rule:** If any site requires login, paywall, or CAPTCHA / anti-bot verification, **skip it immediately** and move to the next public source in the table below. Never attempt to bypass gates or captchas.

---

## 2. S-Tier Sources (Structured — Direct `import_score.py`)

### Chinese Pop / Guofeng / Fingerstyle (华语流行 · 古风 · 华语指弹首选)

| Priority | Source | Domain | Best For | Formats | Targeted Query Template |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | **吉他社** | `jitashe.org` | 卢家宏 / 陈亮 / 岸部真明 / 押尾 / 华语流行指弹 | AlphaTab `.json`, `.gp5` | `site:jitashe.org "<歌名>" 指弹` |
| **2** | **MuseScore** | `musescore.com` | ACG / 动漫 OST / 影视 / 钢琴与指弹改编 | `.mxl`, `.musicxml`, `.mid` | `site:musescore.com "<歌名或英文名>" fingerstyle OR guitar` |
| **3** | **GitHub 公开谱库** | `github.com` | 开源 MIDI / MusicXML / Guitar Pro 合集 | `.mid`, `.mxl`, `.gp5`, `.py` | `site:github.com "<歌名>" (midi OR musicxml OR gp5)` |

### Western / Japanese / ACG / Game OST / Classical (欧美 · 日系 · 游戏 · 古典首选)

| Priority | Source | Domain | Best For | Formats | Targeted Query Template |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | **Songsterr** | `songsterr.com` | 欧美流行/摇滚、日系、ACG、指弹分轨六线谱 | Structured TAB / GP | `site:songsterr.com "<Song>" fingerstyle OR acoustic` |
| **2** | **GuitarProTabs** | `guitarprotabs.org` | 欧美/日系/指弹免费 Guitar Pro | `.gp3`, `.gp4`, `.gp5` | `site:guitarprotabs.org "<Song>"` |
| **3** | **GProTab** | `gprotab.net` | 流行/动漫/影视 Guitar Pro 独奏 | `.gp4`, `.gp5` | `site:gprotab.net "<Song>"` |
| **4** | **BitMidi** | `bitmidi.com` | 经典流行、影视旋律（免登录直下） | `.mid` | `site:bitmidi.com "<Song>"` |
| **5** | **VGMusic** | `vgmusic.com` | 游戏原声 OST（免登录直下） | `.mid` | `site:vgmusic.com "<Game or Track>"` |
| **6** | **Mutopia Project** | `mutopiaproject.org` | 古典吉他公版名曲（含 LilyPond / MIDI） | `.mid`, `.ly`, `.pdf` | `site:mutopiaproject.org "<Piece>" guitar` |
| **7** | **Free-scores** | `free-scores.com` | 古典/民谣吉他免费乐谱 | `.mid`, `.xml`, `.pdf` | `site:free-scores.com "<Piece>" guitar` |

---

## 3. A-Tier Sources (High-Clarity Fingerstyle TAB & Official Jianpu — Use `fret_helper.py`)

### Chinese Fingerstyle TAB & Official Jianpu (华语指弹六线图 & 原版简谱)

| Priority | Source | Domain | Best For | Format | Targeted Query Template |
| :---: | :--- | :--- | :--- | :--- | :--- |
| **1** | **有谱么** | `yopu.co` | 华语流行指弹，原调/Capo 标注规范 | 高清六线谱 | `site:yopu.co "<歌名>" 指弹` |
| **2** | **虫虫吉他** | `ccguitar.cn` | 卢家宏 / 张紫宇等名家华语指弹独奏 | 高清六线谱图 | `site:ccguitar.cn "<歌名>" 指弹` |
| **3** | **17 吉他** | `17jita.com` | 华语经典老歌与流行指弹独奏 | 高清六线谱图 | `site:17jita.com "<歌名>" 指弹` |
| **4** | **中国曲谱网** | `qupu123.com` | **权威原版简谱**（主旋律音高与节拍最准确） | 原版简谱 | `site:qupu123.com "<歌名>" 简谱` |
| **5** | **歌谱简谱网** | `jianpu.cn` | 流行/影视/古风/动漫原版简谱 | 原版简谱 | `site:jianpu.cn "<歌名>"` |
| **6** | **弹琴吧** | `tan8.com` | 钢琴/声乐主旋律五线谱与简谱 | 钢琴谱 / 简谱 | `site:tan8.com "<歌名>"` |
| **7** | **Ultimate Guitar** | `ultimate-guitar.com` | 欧美/日系（**仅限 `Guitar Pro` 或 `Tab` 轨**） | TAB / GP | `site:ultimate-guitar.com "<Song>" fingerstyle tab` |

---

## 4. Hard Accept / Reject Filter (采信与过滤红线)

- **ACCEPT ONLY IF:**
  1. Contains **explicit melody pitches** (full 6-line solo fingerstyle TAB, official numbered notation / staff lead sheet, or structured `.mid` / `.musicxml` / `.mxl` / AlphaTab `.json` / `.gp3-.gp5`);
  2. Covers the **full song structure** (Intro + Verse + Chorus + Outro, not an 8-bar preview teaser);
  3. Key, Capo, and time signature are clearly stated or deterministically derivable.
- **REJECT IMMEDIATELY:**
  - ❌ **Chord-only lyric sheets (“弹唱和弦谱”)** with only lyrics + `C / G / Am / F` letters and no melody line;
  - ❌ **Partial preview images** (only page 1 / 8 bars visible, rest blurred or behind paywall);
  - ❌ **Blurry low-res screenshots** where fret numbers (`3` vs `5` vs `8`) or octave dots cannot be read unambiguously;
  - ❌ **ASCII text tabs from random blogs** with broken alignment and no rhythmic durations;
  - ❌ **Mixed full-band MP3 / polyphonic guitar recordings** as sole pitch source.
- **CONVERSION RULE:**
  - Whenever an S-tier file (`.mid`, `.musicxml`, `.mxl`, AlphaTab `.json`, `.gp3-.gp5`) is obtained, **ALWAYS run `python3 scripts/import_score.py`** — NEVER hand-write a custom parser script!
