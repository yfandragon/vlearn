# 贡献指南

## 先跑起来

```bash
python3 -m venv .venv
.venv/bin/pip install yt-dlp
.venv/bin/pip install faster-whisper   # 可选，无字幕视频才需要
python3 vlearn.py doctor --no-ping
```

## 改代码前

1. 读 [docs/HANDBOOK.md](docs/HANDBOOK.md) 的「三个关键设计决策」——很多看起来绕的地方是有原因的。
2. 跑一遍测试，确认基线是绿的：

   ```bash
   python3 -m unittest discover -s tests -v
   ```

3. 涉及 CLI 参数的地方，**同时兼容 opencode 1.x 和 2.x**。不要在代码里写死参数，
   走 `opencode_caps()` 探测。

## 约定

- **单文件**。`vlearn.py` 是刻意保持单文件的：方便 `pipx install` 和直接下载运行。
  不要为了「模块化」拆成包。
- **中文注释、中文输出**。这是一个中文优先的工具，报错信息要能让用户直接照做。
- **不引入重依赖**。`yt-dlp` 是必需，`faster-whisper` 只在 `[asr]` extra 里。
  新依赖必须可选、必须能降级。
- **时间戳是硬约束**。任何改动都不能让笔记里的时间戳脱离原始转写。
- 代码风格接近 PEP 8，行宽 100 左右，不强制格式化工具。

## 加一个后端（比如换模型供应商）

1. 写 `attempt_xxx(prompt, workdir, timeout, model) -> (text, err)`：不抛异常，返回错误字符串。
2. 在 `generate()` 里加分支。
3. 在 `cmd_doctor` 里加对应的探针。
4. 在 `tests/` 里补「命令拼接」和「错误提取」的用例。

## 加一个语音识别引擎

1. 写 `transcribe_xxx(path, args) -> (cues, note)`，`cues` 是 `[(start_ms, end_ms, text)]`。
2. 在 `pick_asr_backend()` 和 `transcribe()` 里注册。
3. 在 `asr_status()` 里报告可用性，`doctor` 的输出要同步更新。

## 提交

```
feat: 支持 xxx
fix: 修复 v2 的 --standalone 位置
docs: 补充 B 站排错
test: 补 scan_stream 的错误事件用例
```

PR 描述里请写：**改了什么、怎么验证的、有没有影响额度消耗**。
涉及额度的改动（调用次数、prompt 变长）请在 PR 里说明新增的每视频成本。
