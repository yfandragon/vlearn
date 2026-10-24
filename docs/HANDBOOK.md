# vlearn 手册

> 部署 · 使用 · 测试。全部命令都在 macOS (Apple Silicon, Python 3.9) 上实测过；
> 标注「未验证」的地方是环境限制导致的，不是没实现。

---

## 一、它是什么

### 1.1 数据流

```
目标（URL 或本地文件）
   │
   ├─ 本地文件 ─────────────────────┐
   │                                │
   ├─ B 站 ──► 开放 API 拿元数据 ──►│ 有字幕？──是──► 下载 → 解析 VTT/JSON3
   │            (网页端 412 风控)   │        │
   ├─ 其他 ──► yt-dlp -J ───────────┘        否
   │            (YouTube 反爬时                   │
   │             回退 android 客户端)             ▼
   │                                      faster-whisper / whisper.cpp
   │                                             │
   ▼                                             ▼
段落（每段带起始时间戳）◄───────────────────────┘
   │
   ├─ 总字数 ≤ max-chars ──► 1 次调用出成品
   └─ 超出 ──► 分块出要点 ──► 合并成成品
                    │
                    ▼
        opencode run -m opencode-go/deepseek-v4.1-flash
                    │
                    ▼
      out/<id>/{notes.md, transcript.md, meta.json, chunks/}
```

### 1.2 三个关键设计决策

**为什么字幕优先、不做语音识别兜底一切**

大多数课程、访谈、发布会都有 CC 或自动字幕，下字幕是秒级操作；跑 Whisper 是分钟级，
还要下载几百 MB 模型。把 ASR 放在「字幕缺失」这一条分支上，成本和体验都是最优。

**为什么选 faster-whisper 而不是 whisper.cpp 做默认**

faster-whisper 用 PyAV 自带的 FFmpeg 解码，**不需要用户装系统 ffmpeg**；一行 pip 就能装完。
whisper.cpp 更快更省内存，但要自己编译/下载模型，还得系统有 ffmpeg 转 16k wav，所以放第二位。

**为什么复用 CLI 额度而不是 API key**

opencode Go 是订阅制：按请求次数计入订阅，不产生 API 账单。代价是不能并发（限流）、
输出是 agent 行为而非干净 API 响应，所以代码里要处理事件流解析、权限、超时和失效诊断。

---

## 二、部署手册

### 2.1 环境要求

| 项 | 要求 | 检查命令 |
|---|---|---|
| Python | 3.9+ | `python3 --version` |
| yt-dlp | 必需 | `.venv/bin/yt-dlp --version` |
| opencode CLI | 必需，1.x / 2.x 均可 | `opencode --version` |
| opencode Go 额度 | 必需（或改用 codex 额度） | `python3 vlearn.py doctor` |
| faster-whisper | 可选，无字幕视频才需要 | `python3 vlearn.py doctor` |
| 磁盘 | 约 2 GB（模型 + 音频临时文件） | `df -h .` |

> 不需要系统 ffmpeg。只有用 `--asr whisper-cpp` 时才需要 `brew install ffmpeg`。

### 2.2 安装（源码方式，推荐）

```bash
git clone <你的仓库地址> vlearn && cd vlearn

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install yt-dlp

# 可选：无字幕视频需要
.venv/bin/pip install faster-whisper
```

装完不需要激活虚拟环境——`vlearn.py` 启动时会自己切到仓库里的 `.venv`。

### 2.3 安装（作为命令，可选）

```bash
pipx install "git+https://github.com/<你>/vlearn.git"           # 只要字幕/总结
pipx install "git+https://github.com/<你>/vlearn.git[asr]"      # 带语音识别
vlearn doctor
```

### 2.4 配置额度

**方案 A：opencode Go（默认）**

```bash
# 1) 浏览器打开 https://opencode.ai/console 生成 API key
#    注意：必须是有 Go / Go Plus 订阅的 workspace
# 2) 写入本机凭据
opencode auth login -p opencode-go
#    选 API Key，粘贴，回车
# 3) 看是否在列表里
opencode auth list          # 应出现 OpenCode Go / stored
```

失效信号：`401 api_key_expired`。**重新登录不会续期**，必须去控制台新建 key 再执行第 2 步。

**方案 B：Codex 额度（备用）**

```bash
codex login status      # 确认 Logged in using ChatGPT
python3 vlearn.py doctor --backend codex
```

### 2.5 部署验收

```bash
python3 vlearn.py doctor
```

期望输出（逐项对照）：

```
vlearn 自检
----------------------------------------------
✓ yt-dlp      2025.10.14  (/…/.venv/bin/yt-dlp)
✓ opencode    opencode v2.1.2
· 后端        opencode · opencode-go/deepseek-v4.1-flash
· 实测额度（消耗 1 次请求）…
✓ 额度可用    模型回复：可用
✓ 语音识别    faster-whisper（模型 small）
· ffmpeg      无（faster-whisper 不需要，whisper.cpp 需要）
----------------------------------------------
结论：链路齐了，可以直接跑视频链接。
```

- 只想要「装好了没」：`python3 vlearn.py doctor --no-ping`（不消耗请求）
- 退出码：`0` = 全部就绪，`1` = 有项未通过（可直接写进 CI）

---

## 三、使用手册

### 3.1 三条命令

| 命令 | 作用 | 花额度吗 |
|---|---|---|
| `python3 vlearn.py doctor` | 环境 + 额度自检 | 1 次（`--no-ping` 不花） |
| `python3 vlearn.py sub <目标>` | 只出字幕，不总结 | 不花 |
| `python3 vlearn.py <目标>` | 完整笔记 | 1~N 次 |

### 3.2 五个典型场景

**场景 1：YouTube 有字幕（最常见）**

```bash
python3 vlearn.py "https://www.youtube.com/watch?v=aircAruvnKk"
```

会看到 `（默认客户端被拦，已回退到 player_client=android）`——这是正常的，yt-dlp 默认
客户端被 YouTube 反爬拦下后自动换客户端。

**场景 2：没有字幕的视频**

```bash
python3 vlearn.py "URL"
# 输出：· 没有可用字幕，改用语音识别 …
#       · 下载音轨（用于语音识别）…
#       · 引擎 faster-whisper · 模型 small（首次运行会自动下载）
```

先看模型多大再跑：

| 模型 | 体积 | 中文效果 | 适合 |
|---|---|---|---|
| `tiny` | 75 MB | 勉强 | 快速验证链路 |
| `base` | 145 MB | 一般 | 英文播客 |
| `small` | 484 MB | 够用 | **默认** |
| `medium` | 1.5 GB | 好 | 课程、访谈 |
| `large-v3` | 3 GB | 最好 | 重要资料 |

国内下载慢就加 `--hf-mirror https://hf-mirror.com`。

**场景 3：B 站**

```bash
python3 vlearn.py "https://www.bilibili.com/video/BV1xx411c7mD" --cookie-browser safari
```

B 站网页端会返回 `412 Precondition Failed`，所以元数据走开放 API；但**字幕列表匿名是空的**，
必须带登录态。`--cookie-browser` 的值就是你的浏览器名：`safari` / `chrome` / `edge`。

**场景 4：本地视频 / 音频**

```bash
python3 vlearn.py ~/Downloads/lecture.mp4 --asr-model medium
```

本地文件一律走语音识别（本地文件没有「字幕」这个概念）。

**场景 5：只想先要字幕，省额度**

```bash
python3 vlearn.py sub "URL 或 本地文件"
cat out/<id>/transcript.md
```

### 3.3 参数速查

| 参数 | 默认 | 说明 |
|---|---|---|
| `--backend` | `opencode` | `opencode` / `codex` |
| `--model` | `opencode-go/deepseek-v4.1-flash` | OpenCode Go 模型名 |
| `--out` | `./out` | 输出根目录 |
| `--max-chars` | `12000` | 单次喂给模型多少字；调大 → 调用次数少 |
| `--timeout` | `600` | 单次模型调用超时（秒） |
| `--asr` | `auto` | `auto` / `faster-whisper` / `whisper-cpp` / `none` |
| `--asr-model` | `small` | faster-whisper 模型名，或 whisper.cpp 的 ggml 路径 |
| `--asr-language` | `auto` | 强制语言能省时间、提准确率 |
| `--hf-mirror` | 无 | 例如 `https://hf-mirror.com` |
| `--cookie-browser` | 无 | `safari` / `chrome` / `edge` |
| `--force` | 关 | 忽略分块缓存 |
| `-y` | 关 | 不询问直接跑 |
| `--ping/--no-ping` | 开 | doctor 是否实测额度 |
| `--version` | — | 打印版本 |

环境变量：

| 变量 | 作用 |
|---|---|
| `VLEARN_OPENCODE` | 指定 opencode 二进制（同时装了 brew 版和 npm 版时有用） |
| `VLEARN_WHISPER_MODEL` | 指定 whisper.cpp 的 ggml 模型 |
| `VLEARN_NO_VENV=1` | 禁止自动切到 `.venv` |
| `HF_ENDPOINT` | 等价于 `--hf-mirror` |
| `VLEARN_MODEL` | 等价于 `--model` 的默认值 |

### 3.4 输出怎么读

```
out/<视频id>/
├── notes.md        # 交付物：结论 → 核心要点 → 章节脉络（表格）→ 回看片段 → 术语与数字 → 行动清单
├── transcript.md   # 原文，每段一个 [mm:ss]
├── meta.json       # 溯源信息：标题/作者/时长/字幕来源/后端/模型/分块数
└── chunks/         # 每块中间笔记 + 缓存键（内容哈希）
```

- `notes.md` 里的时间戳都能在 `transcript.md` 里对得上；
- `meta.json` 的 `subtitle` 字段告诉你用的是人工字幕、自动字幕还是语音转写——**可信度不同**；
- 想复用中间结果改文案，直接编辑 `chunks/*.md` 再手工合并即可。

### 3.5 缓存与续跑

缓存键 = 块内容的 sha1。所以：

- 同一条命令重跑 → 全部命中缓存，0 次调用；
- 改了 `--max-chars` → 块变了，缓存失效（这是对的）；
- 改了 prompt 但内容没变 → **仍会命中旧缓存**，要重算加 `--force`。

中断（Ctrl-C）后已生成的分块都还在，重跑会从断点继续。

---

## 四、测试手册

测试分三层：**单测**（秒级、离线）→ **自检**（1 次请求）→ **端到端**（真实视频）。

### 4.1 第一层：单元测试

```bash
python3 -m unittest discover -s tests -v
```

期望：`Ran 39 tests ... OK`

覆盖范围：

| 测试类 | 覆盖 |
|---|---|
| `TestTimeFormat` | `mm:ss` / `h:mm:ss` 互转、跨小时、负值钳制 |
| `TestVttParsing` | 滚动字幕去重、内联 `<c>`/时间戳标签、HTML 实体、NOTE 块、JSON3 |
| `TestParagraphsAndBlocks` | 按长度/停顿切段、分块预算、内容不丢失 |
| `TestScanStream` | opencode 事件流的快照/增量/错误事件、垃圾行 |
| `TestErrorExtraction` | 从 help 与报错混杂输出里提取真正原因 |
| `TestOpencodeCmd` | v1/v2 参数差异（`--standalone` 必须在 `run` 之后） |
| `TestPrompts` | 成品/合并/分块三种 prompt 的结构 |
| `TestLocalMedia` | 本地文件识别、稳定 id |
| `TestSubtitlePicking` | 人工 > 自动、中文 > 英文 |
| `TestCliSmoke` | `--version` / `--help` / 非法参数退出码 |

这些测试**不联网、不调用模型、不需要 opencode**，所以可以直接放进 CI。

### 4.2 第二层：自检

```bash
python3 vlearn.py doctor            # 含实测额度
python3 vlearn.py doctor --no-ping  # 不花请求，只看装没装
```

通过标准：最后一行是 `结论：链路齐了，可以直接跑视频链接。`，且退出码为 0：

```bash
python3 vlearn.py doctor > /tmp/d.log 2>&1; echo "exit=$?"
grep -q "✓ 额度可用" /tmp/d.log && echo "额度 OK"
```

### 4.3 第三层：端到端（三个用例）

**T1 · 有字幕的长视频（验证分块/成稿）**

```bash
python3 vlearn.py "https://www.youtube.com/watch?v=aircAruvnKk" -y
```

预期：

```
· 字幕：人工字幕 · zh-CN
  解析出 290 条字幕 → 26 个段落
· 计划：1 次成稿，转写共 5,392 字 …
✓ 完成：out/aircAruvnKk/notes.md
```

验收：`notes.md` 有 6 个二级标题、时间戳 ≤ 视频时长、`meta.json` 里 `chunks: 1`。

**T2 · 多块 + 合并（验证 reduce）**

```bash
python3 vlearn.py "https://www.youtube.com/watch?v=aircAruvnKk" -y --max-chars 3000
```

预期：`计划：2 块 + 1 次合并`，耗时约 1 分 50 秒，`chunks/` 下出现 `01-*`、`02-*`。

**T3 · 本地文件 + 语音识别（不依赖任何反爬）**

没有现成音频时，可以用 macOS 自带的 `say` 造一个：

```bash
say -o /tmp/t.wav --data-format=LEI16@16000 \
  "All right, so here we are in front of the elephants."

python3 vlearn.py sub /tmp/t.wav --asr-model tiny --asr-language en
```

预期：

```
· 本地文件：t.wav
  引擎 faster-whisper · 模型 tiny（首次运行会自动下载）
  识别语言 en（置信度 1.00）
· 已写入 out/local-xxxxxxxx/transcript.md
```

验收：`transcript.md` 里能读到 `in front of the elephants`。

> 这个用例是最稳的回归方式：不碰 YouTube 反爬、不碰 B 站风控，只测本机链路。

**T4 · 额度失效的诊断（选做，验证报错质量）**

把 `opencode auth login` 换成一个过期 key 后跑任意视频，应看到：

```
✗ opencode Go 额度凭证失效：This API key has expired…
修复步骤（注意：重新登录不会续期，必须换新 key）：
  1. 打开 https://opencode.ai/auth 控制台，重新生成 API key
  2. 本机执行 opencode auth login → 选 OpenCode Go → 粘贴新 key
  3. 跑 python3 vlearn.py doctor --ping 确认能回话
```

### 4.4 验收清单

发版前逐项打钩：

- [ ] `python3 -m unittest discover -s tests` → 39 passed
- [ ] `python3 vlearn.py doctor` → 退出码 0 且 `✓ 额度可用`
- [ ] T1 有字幕视频出成品，时间戳不超时长
- [ ] T2 分块 + 合并成功，`chunks/` 有 2 个文件
- [ ] T3 本地文件转写文本正确
- [ ] 重跑 T1 → 全部命中缓存（0 次调用）
- [ ] `python3 vlearn.py --help` 参数表完整
- [ ] `README.md` 里的命令能原样跑通

### 4.5 一键跑

```bash
make test    # 单测 + CLI 冒烟
make check   # 单测 + doctor（会消耗 1 次请求）
```

---

## 五、排错对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `Unrecognized flag: --standalone` | 把 v2 的子命令 flag 放到了 `run` 前面 | 已修；如需改代码，注意 flag 顺序 |
| 输出里出现一大段 `DESCRIPTION … FLAGS …` | CLI 参数不被接受，帮助文本被当成结果 | 跑 `opencode run --help` 看真实参数，更新 `opencode_caps()` |
| `api_key_expired` / `401` | Go 的 key 过期 | 控制台新建 key → `opencode auth login -p opencode-go` |
| `region` / `privacy` 报错 | Go 对 DeepSeek 有地区开关 | Settings › Privacy → DeepSeek → Region: Global |
| `The page needs to be reloaded` | YouTube 反爬 | 脚本会自动回退 android 客户端；仍失败就加 `--cookie-browser` |
| `412 Precondition Failed` | B 站网页端风控 | 会自动走 API；字幕仍需 `--cookie-browser` |
| `拿不到 B 站字幕` | 匿名 ↑ 或该视频没开 CC/AI 字幕 | 带登录态重试；否则只能 `--asr` |
| `Requested format is not available` / PO Token | YouTube 音轨下载受限 | 加 `--cookie-browser`，或改用本地文件 |
| `没有可用的语音识别引擎` | 没装 faster-whisper | `.venv/bin/pip install faster-whisper` |
| 模型下载卡住 | HuggingFace 慢 | `--hf-mirror https://hf-mirror.com` |
| `超时（>600s）` | 长视频 + 大模型 | 加 `--timeout 1800`，或换 `--asr-model small` |
| 转写字数明显偏少 | 视频确实没字幕/没音轨 | `python3 vlearn.py sub …` 先看 `transcript.md` |

---

## 六、发布到 GitHub

```bash
git init
git add .
git commit -m "feat: vlearn 0.2.0 —— 视频转带时间戳笔记（opencode Go / Codex 额度）"

# 新建空仓库后
git remote add origin git@github.com:<你>/vlearn.git
git branch -M main
git push -u origin main

# 打版本
git tag -a v0.2.0 -m "v0.2.0" && git push --tags
```

发布前确认：

- `.gitignore` 已排除 `out/`、`.venv/`、`.hf/`、`__pycache__/`（已配好）
- 仓库里**不要**提交 `auth.json`、cookie 文件、任何 key
- README 首屏的截图/示例命令可原样执行
- 打 tag 后在 GitHub 上点 `Create release`，正文直接抄 `README` 的「已知限制」

---

## 七、后续可做

- [ ] 输出 HTML（时间戳可点击跳转原视频）
- [ ] `--json` 输出结构化笔记，方便接 Obsidian / Notion
- [ ] 批量模式：读一个 URL 列表，串行跑、失败跳过
- [ ] 字幕纠错：用 LLM 修 ASR 的专有名词
- [ ] 章节拆分：按「章节脉络」自动切出短片段文案
