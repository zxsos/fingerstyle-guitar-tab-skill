<div align="center">

# Fingerstyle Guitar TAB

一个 Agent Skill：把一首歌编配成指弹吉他六线谱 PDF 与同步试听 MP3。

[在线试听](https://zxsos.github.io/fingerstyle-guitar-tab-skill/) · [安装](#安装) · [快速开始](#快速开始)

<a href="https://zxsos.github.io/fingerstyle-guitar-tab-skill/"><img src="docs/cover.png" width="760" alt="Call of Silence 指弹六线谱"></a>

</div>

## 特性

- **单一数据源** — 一份 `score_data.py` 同时生成 PDF 与音频，品格与音高不会错位
- **完整指弹技巧** — 击弦、勾弦、滑音、琶音、泛音、闷音、拍弦
- **自动校验** — 指法跨度、调内音、PDF 脱敏、音频电平逐项把关
- **在线播放器** — 连续看谱、逐拍高亮、点击小节跳转；拖入任意本 Skill 产物即可播放

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

cd examples/call-of-silence
python3 src/build_pdf.py && python3 src/build_audio.py
python3 src/verify_all.py
pdftoppm -png -r 150 *.pdf previews/page && python3 src/build_sync_manifest.py
```

依赖：Python 3、FFmpeg、Ghostscript、poppler-utils、Noto CJK 字体。macOS：`brew install lilypond fluid-synth ffmpeg ghostscript poppler`。

## 产物

| 文件 | 用途 |
| --- | --- |
| `*.pdf` | 六线谱 |
| `*.mp3` | 试听音频 |
| `previews/page-*.png` | 每页预览图 |
| `sync_manifest.json` | 播放器同步数据 |
| `src/` | 可复现源码 |

将作品文件夹拖入[在线播放器](https://zxsos.github.io/fingerstyle-guitar-tab-skill/)即可播放，最少需要 MP3 与 `previews/page-*.png`。

## 让 AI 编配新曲

> 克隆 https://github.com/zxsos/fingerstyle-guitar-tab-skill ，按 `skills/fingerstyle-guitar-tab/SKILL.md` 为《曲名》编配指弹六线谱：以 `examples/call-of-silence` 为模板，`verify_all.py` 全部通过后生成预览图与 `sync_manifest.json`。

## License

[MIT](LICENSE)
