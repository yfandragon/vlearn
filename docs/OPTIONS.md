# 选项详解与决策指南

> 这个文件回答一个问题：**我这种情况，到底该加哪个参数？**
> 只想照抄命令的话，直接跳到最后的「常用配方」。

---

## 一、30 秒决策表

| 你的情况 | 加什么 |
|---|---|
| YouTube 视频，有字幕 | 什么都不用加 |
| B 站视频，想用官方字幕 | `--bili-sessdata "值"`（最省事）或 `--cookie-browser safari` |
| B 站视频，没字幕 | 什么都不用加（会自动语音识别） |
| 本地 mp4 / m4a | 什么都不用加（本地文件一律语音识别） |
| 国内网络，第一次跑、要下模型 | `--hf-mirror https://hf-mirror.com` |
| 中文内容，要准确 | `--asr-model medium` |
| 只想快速验证通不通 | `--asr-model tiny` |
| 不想花额度，只要字 | 用 `sub` 而不是默认命令 |
| opencode Go 额度用完了 | `--backend codex` |
| 视频特别长（2 小时+） | `--max-chars 20000`（少调用几次） |
| 想改输出风格 | 改代码里的 prompt，见 [ARCHITECTURE.md](ARCHITECTURE.md#七自己动手改三个练习) |
| 不想用终端 | `python3 vlearn.py web` |

---

## 二、输入源

```bash
python3 vlearn.py "https://www.youtube.com/watch?v=xxx"
python3 vlearn.py "https://www.bilibili.com/video/BV1xx411c7mD"
python3 vlearn.py BV1xx411c7mD              # BV 号可以直接粘
python3 vlearn.py av80433022                # av 号也行
python3 vlearn.py ~/Movies/lecture.mp4      # 本地文件
```

| 输入 | 走什么路 | 注意 |
|---|---|---|
| YouTube | yt-dlp 抓字幕；反爬时自动换播放器客户端 | 换客户端仍失败就加 `--cookie-browser` |
| B 站 | 开放 API 优先，失败回退 yt-dlp | 字幕要登录态；音轨匿名可用 |
| 本地文件 | 直接语音识别 | 文件必须有音轨；不认图片/纯音乐 |

**什么时候别用**：直播回放还没生成字幕且音轨是分片的、付费课程需要 DRM 解密的——这两类工具都搞不定。

---

## 三、额度后端 `--backend`

| 值 | 花谁的钱 | 什么时候用 |
|---|---|---|
| `opencode`（默认） | opencode Go 订阅额度，**不产生 API 账单** | 你有 Go / Go Plus 订阅 |
| `codex` | ChatGPT 订阅额度 | Go 额度用完、或你只订了 ChatGPT |

判断当前状态：

```bash
python3 vlearn.py doctor                    # 看 opencode 那条
python3 vlearn.py doctor --backend codex    # 看 codex 那条
```

**代价对比**：Go 的额度按请求次数算，一次视频 1~3 次；Codex 按 ChatGPT 的用量窗口，长任务更容易撞上限。

---

## 四、模型 `--model`

默认 `opencode-go/deepseek-v4.1-flash`。只在三种情况下改：

1. **想更聪明**（长视频、术语多）→ 换成 Go 里更强的模型，例如 `opencode-go/deepseek-v4-pro`
2. **想更快/更省** → 保持 flash 即可，它已经是最便宜档
3. **换后端**（`--backend codex`）→ 一般留空，用 Codex 自己的默认模型

```bash
python3 vlearn.py "URL" --model opencode-go/deepseek-v4-pro
```

⚠️ 换模型会让缓存失效，**同样的视频会重新花一次额度**（缓存键包含模型名）。

---

## 五、分块 `--max-chars`

控制「一次调用喂给模型多少字」，默认 12000。

| 视频长度 | 建议 | 调用次数 |
|---|---|---|
| < 20 分钟 | 默认 12000 | 1 次成稿 |
| 20~60 分钟 | 默认或 15000 | 1~3 次 |
| 1~3 小时 | 20000~30000 | 3~8 次 + 1 次合并 |
| 只是想省钱 | 调大 | 次数少但单次上下文长，质量可能下降 |

**调小**（如 3000）只用于测试多块合并逻辑，正常使用没必要。

---

## 六、语音识别（`--asr` / `--asr-model` / `--asr-language`）

### 6.1 什么时候会用到

只有两种情况：**视频没有字幕**，或者你给的是**本地文件**。

### 6.2 引擎选择 `--asr`

| 值 | 含义 | 什么时候用 |
|---|---|---|
| `auto`（默认） | 有 faster-whisper 就用它 | 99% 的情况 |
| `faster-whisper` | 强制用它 | 同时装了 whisper.cpp，想指定 |
| `whisper-cpp` | 用 C++ 版 | 已经有 ggml 模型、想省内存（**需要系统 ffmpeg**） |
| `none` | 关掉语音识别 | 只想抓字幕，没字幕就直接失败 |

### 6.3 模型大小 `--asr-model`

| 模型 | 体积 | 中文准确度 | 转录 10 分钟音频 | 建议 |
|---|---|---|---|---|
| `tiny` | 75 MB | 差（“五档”→“五党”） | ~1 分钟 | 只用来验证链路 |
| `base` | 145 MB | 一般 | ~2 分钟 | 英文内容够用 |
| `small` | 484 MB | 够用 | ~4 分钟 | **默认，日常首选** |
| `medium` | 1.5 GB | 好 | ~10 分钟 | 课程、访谈、重要资料 |
| `large-v3` | 3 GB | 最好 | ~20 分钟 | 一次性整理珍贵资料 |

准确度和速度大致线性增长。**先用 small 跑一遍看质量，不满意再上 medium**——因为换模型会让缓存失效，直接上大的更费时间。

### 6.4 语言 `--asr-language`

默认 `auto`（让模型自己判断）。**知道语言就显式指定**，能省时间也更准：

```bash
--asr-language zh    # 中文播客
--asr-language en    # 英文课程
```

混合语言（中英夹杂）就留 `auto`。

---

## 七、登录态：字幕 vs 音轨

这是最容易混淆的地方。

| 你想要 | 需要登录态吗 | 怎么给 |
|---|---|---|
| B 站**官方字幕** | **需要** | `--bili-sessdata` 或 `--cookie-browser` |
| B 站**音轨**（走语音识别） | 不需要，匿名可用 | 不用管 |
| YouTube 字幕 | 一般不需要 | 反爬严重时才需要 |
| YouTube 音轨 | 可能需要 | 下载失败时加 `--cookie-browser` |

### 7.1 推荐：`--bili-sessdata`（免授权）

```
浏览器登录 B站 → F12 → Application → Cookies → https://www.bilibili.com
→ 找 SESSDATA → 复制 Value → 粘进来
```

```bash
python3 vlearn.py BV1xx411c7mD --bili-sessdata "粘贴的值"
```

优点：不需要给终端任何系统权限。缺点：cookie 会过期（通常几个月），过期后重新复制。

### 7.2 备选：`--cookie-browser`

从浏览器数据库里读 cookie。

```bash
--cookie-browser safari    # 需要给终端「完全磁盘访问权限」
--cookie-browser chrome    # 一般不需要授权，但可能弹钥匙串
--cookie-browser auto      # 依次尝试 safari/chrome/edge/firefox/brave
```

macOS 授权路径：**系统设置 → 隐私与安全性 → 完全磁盘访问权限 → 加终端 → Cmd+Q 重启终端**。

### 7.3 判断有没有成功

| 日志 | 含义 |
|---|---|
| `（已读取 safari 登录态，37 条 bilibili cookie）` | 成功 |
| `（使用手动填写的 SESSDATA）` | 成功 |
| `（没找到可用的浏览器登录态，改走匿名请求）` | 失败，但能继续跑 |
| `· 字幕：B站字幕（ai-zh）` | 用上了官方字幕 |
| `· 字幕不可用（…），改用语音识别` | 没用上，降级了 |

**降级不是错误**。拿不到字幕照样能出笔记，只是慢一两分钟。

---

## 八、模型下载

首次跑语音识别要下模型（75MB~3GB），这是最容易卡住的一步。

| 情况 | 用什么 |
|---|---|
| 国内网络，下载卡住 | `--hf-mirror https://hf-mirror.com` |
| 已经有模型目录 | `--asr-model /path/to/faster-whisper-small` |
| 想换缓存位置 | 环境变量 `VLEARN_MODEL_DIR=/your/dir` |
| 公司代理 | 设 `HTTPS_PROXY`，代码会继承 |

`--hf-mirror` 会做两件事：禁用 HuggingFace 的 Xet 协议、改用直连 GET 下载。这样绕开了 `FileMetadataError` 那类问题。

---

## 九、输出与缓存

| 参数 | 作用 | 什么时候用 |
|---|---|---|
| `--out DIR` | 换输出根目录 | 想对比不同模型的结果：`--out out-medium` |
| `--force` | 忽略缓存 | 改了 prompt 但想重算 |
| `--timeout 1800` | 单次调用超时（秒） | 3 小时视频 + medium 模型 |
| `-y` | 不问直接跑 | 脚本/网页界面里用 |

**缓存规则**：键 = 后端 + 模型 + 分块内容。所以换模型/换分块会重算，换 `--out` 不会。

---

## 十、网页界面参数

```bash
python3 vlearn.py web                    # 127.0.0.1:8765，自动开浏览器
python3 vlearn.py web --port 9000        # 换端口（被占用时）
python3 vlearn.py web --no-open          # 不开浏览器（远程/服务器上）
python3 vlearn.py web --host 0.0.0.0     # ⚠️ 允许局域网访问，没有鉴权，别在公共网络用
```

---

## 十一、常用配方

```bash
# 1. 最省心：YouTube 有字幕
python3 vlearn.py "https://www.youtube.com/watch?v=xxx"

# 2. B 站想用官方字幕（推荐）
python3 vlearn.py BV1xx411c7mD --bili-sessdata "你的值"

# 3. B 站没字幕，走语音识别（中文，要准）
python3 vlearn.py BV1xx411c7mD --asr-model medium --asr-language zh \
    --hf-mirror https://hf-mirror.com

# 4. 本地课程视频
python3 vlearn.py ~/Downloads/课程01.mp4 --asr-model medium

# 5. 只想快速验证能不能跑（不花额度）
python3 vlearn.py sub BV1xx411c7mD --asr-model tiny

# 6. 长视频省额度
python3 vlearn.py "URL" --max-chars 24000

# 7. 额度用完了
python3 vlearn.py "URL" --backend codex

# 8. 对比两个模型的效果（互不覆盖）
python3 vlearn.py "URL" --asr-model small  --out out-small
python3 vlearn.py "URL" --asr-model medium --out out-medium

# 9. 图形界面
python3 vlearn.py web
```

---

## 十二、什么时候别用这个工具

- **需要精确到每句话的逐字稿**（字幕稿、庭审记录）→ 用专业转写服务，Whisper 会有错字
- **视频里有大量图表/PPT** → 这个工具只看文字，看不到画面
- **要翻译成其他语言** → 它只输出中文笔记，不做翻译
- **多人同时用** → 额度是串行的，并发会限流
- **商业批量处理** → 订阅额度有次数上限，且各平台条款需自行确认
