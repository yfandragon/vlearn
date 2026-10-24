# vlearn

**把视频变成一份带时间戳、可回查的中文笔记。**

给它一个链接（YouTube / B 站）或一个本地文件，它抓字幕（没有字幕就本地跑语音识别），
再用你已有的 AI 订阅额度做总结——不额外买 API。

```
$ python3 vlearn.py "https://www.youtube.com/watch?v=aircAruvnKk"

· 读取视频信息 …
  （默认客户端被拦，已回退到 player_client=android）
  But what is a neural network? | Deep learning chapter 1
  时长 18:40 · 平台 Youtube
· 字幕：人工字幕 · zh-CN
  解析出 290 条字幕 → 26 个段落
· 计划：1 次成稿，转写共 5,392 字，后端 opencode · 模型 opencode-go/deepseek-v4.1-flash
  · 一次成稿，5,625 字 → 生成中 …

✓ 完成：out/aircAruvnKk/notes.md
```

产出的是一份能直接读的笔记，不是一堆摘要碎片：

```markdown
> 一句话结论：神经网络本质是一个由大量权重和偏置参数构成的复杂函数……

## 核心要点
- 神经元先被理解成“装数字的容器”，里面装 0 到 1 之间的激活值…… [02:38]

## 章节脉络
| 时间 | 主题 | 讲了什么 |
| [09:49] | 边缘检测与 sigmoid | 周围一圈赋负权重，中间亮、周围暗时加权和最大 |

## 值得回看的片段 / 术语与数字 / 行动清单
```

完整样例见 [examples/3blue1brown-chapter1.md](examples/3blue1brown-chapter1.md)。

## 它解决什么问题

| 痛点 | vlearn 的做法 |
|---|---|
| 一小时的课，只想记住 5 分钟 | 时间戳让每个结论都能点回原视频核对 |
| 视频没字幕，工具就废了 | 字幕优先；没有就自动跑本地语音识别（faster-whisper，**不需要系统 ffmpeg**） |
| 摘要不可信、张口就来 | 强制每条结论标注真实时间戳，禁止编造；不确定就写“（未听清）” |
| 总结要花 API 钱 | 直接复用你已有的 **opencode Go** 订阅额度（也支持 Codex 额度） |
| 重跑一次又烧一遍额度 | 分块结果按内容哈希缓存，中断可续 |

## 快速开始

```bash
# 1. 依赖（yt-dlp 用来抓字幕）
python3 -m venv .venv
.venv/bin/pip install yt-dlp

# 2. 可选：没有字幕的视频才需要，约 200MB
.venv/bin/pip install faster-whisper

# 3. 自检：检查 yt-dlp / opencode / 额度 / 语音识别
python3 vlearn.py doctor

# 4. 跑一个视频
python3 vlearn.py "https://www.youtube.com/watch?v=aircAruvnKk"
```

脚本会自动切到项目里的 `.venv`，所以你用系统 `python3` 调用也没关系。

### 不想用命令行？有网页界面

```bash
python3 vlearn.py web
```

会起一个本地服务（默认 <http://127.0.0.1:8765>）并自动打开浏览器：粘贴链接、点开始、
实时看日志、完成后直接渲染出笔记。界面只是 CLI 的壳，命令行能跑通的它都能跑。

### 额度怎么配

默认走 **opencode Go**（订阅制，按次计费，不产生 API 账单）：

1. 在 <https://opencode.ai/console> 生成 API key（确认 Go / Go Plus 订阅有效）
2. `opencode auth login -p opencode-go`，粘贴 key
3. `python3 vlearn.py doctor` 看到 `✓ 额度可用` 即可

> key 过期后**重新登录不会续期**，必须去控制台新建一个。
> 报 region / privacy 错误时，去 Settings › Privacy 把 DeepSeek 的 Region 设为 **Global**。

不订阅也能用：`--backend codex` 走 ChatGPT 订阅额度。

## 用法

```bash
# 只要字幕，不花额度（排查问题时很好用）
python3 vlearn.py sub "URL 或 本地文件"

# 本地视频 / 音频（自动跑语音识别）
python3 vlearn.py ~/Movies/课程.mp4 --asr-model small

# B 站：BV 号可以直接粘，不用拼完整链接
python3 vlearn.py BV1xx411c7mD --cookie-browser safari

# 抖音式超短视频也有 AI 字幕拿不到的时候，强制语音识别
python3 vlearn.py "URL" --asr faster-whisper --asr-language zh
```

| 参数 | 说明 |
|---|---|
| `--backend` | `opencode`（默认，Go 额度）或 `codex`（ChatGPT 额度） |
| `--model` | 默认 `opencode-go/deepseek-v4.1-flash`；可换 Go 里的其他模型 |
| `--max-chars` | 单次喂给模型多少字，默认 12000；调大减少调用次数 |
| `--asr` | `auto` / `faster-whisper` / `whisper-cpp` / `none` |
| `--asr-model` | faster-whisper 的 `tiny`~`large-v3`，或 whisper.cpp 的 ggml 路径 |
| `--asr-language` | `auto` / `zh` / `en` … |
| `--hf-mirror` | 模型下载慢时用 `https://hf-mirror.com` |
| `--cookie-browser` | `safari` / `chrome` / `edge` / `auto`，需要登录态的视频 |
| `--bili-sessdata` | 直接填 B 站 SESSDATA，**不需要给终端任何系统权限** |
| `--hf-mirror` | 模型下载走镜像站，国内网络建议加 |
| `--force` | 忽略缓存重跑 |
| `-y` | 不询问直接开跑 |

## 输出

```
out/<视频id>/
├── notes.md        # 成品：结论 / 核心要点 / 章节脉络 / 回看片段 / 术语 / 行动清单
├── transcript.md   # 带 [mm:ss] 的完整原文
├── meta.json       # 标题、作者、时长、来源、模型、分块数
└── chunks/         # 中间笔记（缓存，重跑可复用）
```

## 一次要花多少额度

| 视频 | 转写量 | 调用次数 | 实测耗时 |
|---|---|---|---|
| 19 秒 | 217 字 | 1 次成稿 | ~40 秒 |
| 18 分钟 | 5,392 字 | 1 次成稿 | ~43 秒 |
| 18 分钟（`--max-chars 3000`） | 5,392 字 | 2 块 + 1 合并 = 3 次 | 1 分 48 秒 |

1 小时内的中文视频，默认参数基本是 **1 次请求**。

## 已知限制

- **模型下载**：`faster-whisper` 默认走 HuggingFace Hub。国内网络建议
  `--hf-mirror https://hf-mirror.com`（会自动改用直连下载，绕过 Hub 的 Xet 校验）。
- **音轨下载依赖 yt-dlp 的反爬状态**。YouTube 现在对部分客户端要求 PO Token，
  纯字幕路径不受影响；要走语音识别时如果下载失败，加 `--cookie-browser` 通常能解决。
- **B 站有两条通道**：优先走开放 API（元数据 + playurl 音轨），失败时回退 yt-dlp。
  字幕仍然需要登录态；没有字幕时会自动用 API 拉音轨做语音识别（**这条匿名也能跑通**）。
- **whisper.cpp 后端需要系统 ffmpeg**（faster-whisper 不需要）。
- **串行调用**：别并发，容易触发限流。

排错对照表在 [docs/HANDBOOK.md](docs/HANDBOOK.md#四排错对照表)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/HANDBOOK.md](docs/HANDBOOK.md) | 部署、使用、测试、排错对照表 |
| [docs/OPTIONS.md](docs/OPTIONS.md) | 每个参数什么意思、什么情况该加哪个 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 项目讲解：用了什么技术、怎么实现的（面向非技术读者） |
| [docs/PUBLISH.md](docs/PUBLISH.md) | 发布到 GitHub 的完整步骤 |

## 开发

```bash
python3 -m unittest discover -s tests -v    # 48 个离线单测，不联网、不调用模型
```

结构：单文件 `vlearn.py`（约 1200 行）+ `tests/` + `docs/`。改代码前先看
[docs/HANDBOOK.md](docs/HANDBOOK.md)，里面写了为什么是现在这个设计。

## English

`vlearn` turns a video (YouTube / Bilibili / local file) into timestamped notes in Chinese.
Captions first; local speech-to-text (`faster-whisper`, no system ffmpeg needed) when there
are none. Summarisation runs through your existing **opencode Go** subscription
(`opencode run -m opencode-go/deepseek-v4.1-flash`) or Codex quota — no extra API bill.

```bash
python3 vlearn.py doctor                                  # self-check
python3 vlearn.py "https://www.youtube.com/watch?v=..."   # summarize
python3 vlearn.py sub ./lecture.mp4                       # transcript only
```

Requires Python 3.9+, `yt-dlp`, and the `opencode` CLI logged in with an OpenCode Go key.

## License

[MIT](LICENSE)
