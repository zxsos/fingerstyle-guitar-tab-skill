<div align="center">

# Fingerstyle Guitar TAB

一个 Agent Skill：把一首歌编配成指弹吉他六线谱 PDF 与同步试听 MP3。

[在线练习播放器](https://zxsos.github.io/guitar-tab-player/) · [播放器仓库](https://github.com/zxsos/guitar-tab-player) · [安装](#安装) · [快速开始](#快速开始)

<a href="https://zxsos.github.io/guitar-tab-player/"><img src="docs/cover.png" width="760" alt="Call of Silence 指弹六线谱"></a>

</div>

## 特性

- **单一数据源 & 共享通用引擎** — 模型只需专注写一份 `src/score_data.py`（其余制谱、合音、切片、校验、打包全部由共享 CLI `tab_engine.py` 一行命令完成），品格与音高绝不错位
- **信源纯净度前置分诊 & 多格式互转** — 先问用户是否有 `.mid` / MusicXML / 原版简谱等高纯净度信源；支持 `MIDI / MusicXML / .mxl` 一键直转草稿（`import_score.py`），拒绝纯混音音频盲扒
- **三档难度硬校验** — 支持 `beginner`（新手入门）、`standard`（标准指弹）、`advanced`（进阶独奏），在品位、跨度、节奏密度与技巧上做硬性校验
- **自愈报错 & 零心算弦位助手** — 内置 `fret_helper.py` 自动算变调夹与简谱弦位，`tab_engine.py check` 遇到声部交叉或跨度超限会直接给出可复制替换候选，弱/小模型也能一次写对
- **一键打包 & 同步练琴** — 产出精确到 MIDI 延音时长的 `sync_manifest.json` 与 `.zip` 乐谱包，拖入[在线练习播放器](https://zxsos.github.io/guitar-tab-player/)即可同步使用 2D 指板与 3D 透视下落轨道

## 安装

```bash
npx skills add zxsos/fingerstyle-guitar-tab-skill
```

或将 `skills/fingerstyle-guitar-tab` 复制到 `~/.agents/skills/`（Antigravity / Codex / Gemini CLI）或 `~/.claude/skills/`（Claude Code）。

## 快速开始

```bash
git clone https://github.com/zxsos/fingerstyle-guitar-tab-skill.git
cd fingerstyle-guitar-tab-skill
bash skills/fingerstyle-guitar-tab/scripts/setup_tools.sh   # 免 sudo 安装 LilyPond / FluidSynth

# 方式一：一键验证官方示例 Call of Silence
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py verify examples/call-of-silence

# 方式二：直接导入 MIDI / MusicXML 成指弹草稿并一键出谱
python3 skills/fingerstyle-guitar-tab/scripts/import_score.py song.mid --out my_song/src/score_data.py --tier beginner
python3 skills/fingerstyle-guitar-tab/scripts/tab_engine.py build my_song
```

依赖：Python 3、FFmpeg、Ghostscript、poppler-utils、Noto CJK 字体。macOS：`brew install lilypond fluid-synth ffmpeg ghostscript poppler`。

## 产物

| 文件 | 用途 |
| --- | --- |
| `*_bundle.zip` | 打包产物，可直接拖入在线播放器导入（[示例包下载](examples/call-of-silence/Call_of_Silence_bundle.zip)） |
| `*.pdf` | 六线谱 |
| `*.mp3` | 试听音频 |
| `previews/page-*.png` | 每页预览图 |
| `sync_manifest.json` | 播放器同步数据 |
| `src/` | 可复现源码 |

将 `*_bundle.zip` 或作品文件夹拖入[在线练习播放器](https://zxsos.github.io/guitar-tab-player/)（配套项目 [guitar-tab-player](https://github.com/zxsos/guitar-tab-player)）即可校验并同步播放。

## 让 AI 编配新曲

> 克隆 https://github.com/zxsos/fingerstyle-guitar-tab-skill ，按 `skills/fingerstyle-guitar-tab/SKILL.md` 为《曲名》编配指弹六线谱：先确认信源纯净度与难度档位（`beginner`/`standard`/`advanced`），用 `scripts/fret_helper.py` / `scripts/import_score.py` 生成 `src/score_data.py`，再通过 `scripts/tab_engine.py build` 一键出谱、校验并打包。

## License

[MIT](LICENSE)
