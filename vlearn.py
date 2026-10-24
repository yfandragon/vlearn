#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vlearn —— 把视频链接变成带时间戳的中文笔记。

抓字幕用 yt-dlp，总结用 opencode Go 订阅额度里的 DeepSeek V4.1 Flash，
不单独付 API 钱。整条链路只依赖两个外部命令：yt-dlp、opencode。

    python3 vlearn.py "https://www.bilibili.com/video/BVxxxx"
    python3 vlearn.py doctor
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

__version__ = "0.2.0"

HERE = Path(__file__).resolve().parent
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_MODEL = os.environ.get("VLEARN_MODEL", "opencode-go/deepseek-v4.1-flash")

# 字幕语言偏好：中文优先，其次英文
LANG_PREF = [
    "zh-Hans", "zh-CN", "zh-Hans-CN", "zh", "zh-Hans-US",
    "zh-Hant", "zh-TW", "zh-HK", "en", "en-US", "en-GB",
]

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
TAG = re.compile(r"<[^>]+>")
ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"',
            "&#39;": "'", "&nbsp;": " ", "&apos;": "'"}


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def sh(cmd: list[str], timeout: int = 900, stdin: str | None = None,
       cwd: Path | None = None):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        input=stdin, env=clean_env(), cwd=str(cwd) if cwd else None,
    )


def clean_env() -> dict:
    """去掉可能干扰子进程的变量，保证 opencode 用默认配置目录。"""
    env = dict(os.environ)
    for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        env.pop(k, None)
    return env


def ensure_venv() -> None:
    """如果项目里有 .venv，就自动用它——否则用户用系统 python3 跑会缺依赖。"""
    if os.environ.get("VLEARN_NO_VENV") or os.environ.get("VLEARN_REEXEC"):
        return
    for venv in (HERE / ".venv", HERE.parent / ".venv"):
        py = venv / "bin/python"
        if not py.exists():
            continue
        # 用 sys.prefix 判断，不要用 resolve()：venv 里的 python 是指向基础解释器的符号链接
        if Path(sys.prefix).resolve() == venv.resolve():
            return
        env = dict(os.environ, VLEARN_REEXEC="1")
        os.execve(str(py), [str(py), str(Path(__file__).resolve())] + sys.argv[1:], env)


def which(name: str) -> str | None:
    override = os.environ.get(f"VLEARN_{name.upper().replace('-', '_')}")
    if override:
        return override
    for cand in (HERE / ".venv/bin" / name, HERE.parent / ".venv/bin" / name):
        if cand.exists():
            return str(cand)
    return shutil.which(name)


def die(msg: str, code: int = 1):
    print(f"\n✗ {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str):
    print(msg, flush=True)


def ts(ms: int) -> str:
    """毫秒 -> mm:ss 或 h:mm:ss"""
    s = max(0, ms) // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def human(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------------------
# 元数据 / 字幕
# --------------------------------------------------------------------------

def _ytdlp_meta(url: str, cookie_browser: str | None, client: str | None):
    yt = which("yt-dlp")
    if not yt:
        die("找不到 yt-dlp。先跑：python3 -m venv .venv && .venv/bin/pip install yt-dlp")
    cmd = [yt, "-J", "--no-warnings", "--no-playlist"]
    if client:
        cmd += ["--extractor-args", f"youtube:player_client={client}"]
    if cookie_browser:
        cmd += ["--cookies-from-browser", cookie_browser]
    cmd.append(url)
    r = sh(cmd, timeout=240)
    if r.returncode != 0:
        tail = [l for l in (r.stderr or "").strip().splitlines()
                if l.startswith("ERROR")]
        return None, (tail[-1] if tail else "yt-dlp 退出码非 0")
    try:
        return json.loads(r.stdout), None
    except json.JSONDecodeError:
        return None, "yt-dlp 返回的不是合法 JSON"


def has_subs(meta: dict) -> bool:
    return bool(meta.get("subtitles") or meta.get("automatic_captions"))


def fetch_meta(url: str, cookie_browser: str | None) -> dict:
    """YouTube 的新反爬经常要求换播放器客户端，这里按顺序降级。"""
    clients = [None, "android", "tv", "web_safari"] if "youtube.com" in url or "youtu.be" in url else [None]
    fallback, errors = None, []
    for client in clients:
        meta, err = _ytdlp_meta(url, cookie_browser, client)
        if meta and has_subs(meta):
            if client:
                info(f"  （默认客户端被拦，已回退到 player_client={client}）")
            return meta
        if meta and fallback is None:
            fallback = meta
        if err:
            errors.append(f"{client or 'default'}: {err[:110]}")
    if fallback is not None:
        return fallback
    tail = "\n  ".join(errors[-3:]) or "原因未知"
    die("yt-dlp 取元数据失败：\n  " + tail)


def pick_subtitle(meta: dict):
    """返回 (来源, 语言, 条目列表)。人工字幕优先于自动字幕，中文优先于英文。"""
    for source, table in (("人工字幕", meta.get("subtitles") or {}),
                          ("自动字幕", meta.get("automatic_captions") or {})):
        if not table:
            continue
        for lang in LANG_PREF:
            if lang in table:
                return source, lang, table[lang]
        for lang in table:  # 兜底：任何 zh*/en* 变体
            if lang.startswith(("zh", "en")):
                return source, lang, table[lang]
    return None, None, []


def fetch_url(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def download_subtitle(entries: list[dict]) -> tuple[str, str]:
    """返回 (字幕原文, 格式)。优先 vtt，退而求其次 json3。"""
    by_ext = {e.get("ext"): e for e in entries if e.get("url")}
    for ext in ("vtt", "json3", "srv3"):
        if ext in by_ext:
            return fetch_url(by_ext[ext]["url"]), ext
    die("这个视频的字幕格式不认识（只有 " + ", ".join(by_ext) + "）。")


# --------------------------------------------------------------------------
# B 站：网页端常被风控回 412，改走开放 API + 登录态
# --------------------------------------------------------------------------

BV_RE = re.compile(r"/?(BV[0-9A-Za-z]{10})")
BILI_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def is_bilibili(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


def bili_bvid(url: str) -> str | None:
    m = BV_RE.search(url)
    if m:
        return m.group(1)
    try:  # b23.tv 短链
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": BILI_UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            m = BV_RE.search(resp.geturl())
            return m.group(1) if m else None
    except Exception:
        return None


def bili_cookies(browser: str | None) -> dict:
    if not browser:
        return {}
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        jar = extract_cookies_from_browser(browser)
        return {c.name: c.value for c in jar if "bilibili" in (c.domain or "")}
    except Exception as e:
        info(f"  （读取 {browser} cookie 失败：{type(e).__name__}，改走匿名请求）")
        return {}


def bili_api(path: str, params: dict, cookies: dict) -> dict:
    q = urllib.parse.urlencode(params)
    headers = {"User-Agent": BILI_UA, "Referer": "https://www.bilibili.com"}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(f"https://api.bilibili.com{path}?{q}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if data.get("code") != 0:
        raise RuntimeError(f"B站 API {path} 返回 {data.get('code')}: {data.get('message')}")
    return data.get("data") or {}


def bili_subtitle_cues(cid: int, bvid: str, cookies: dict):
    info_d = bili_api("/x/player/v2", {"bvid": bvid, "cid": cid}, cookies)
    subs = ((info_d.get("subtitle") or {}).get("subtitles")) or []
    if not subs:
        return [], ""
    # 中文人工/AI 字幕优先
    subs.sort(key=lambda s: (0 if str(s.get("lan", "")).startswith("zh") else 1,
                             0 if "ai" not in str(s.get("lan", "")) else 1))
    pick = subs[0]
    url = pick.get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    payload = json.loads(fetch_url(url))
    cues = []
    for row in payload.get("body") or []:
        content = clean_text(str(row.get("content", "")))
        if content:
            cues.append((int(float(row.get("from", 0)) * 1000),
                         int(float(row.get("to", 0)) * 1000), content))
    return cues, str(pick.get("lan") or "")


def bili_prepare(url: str, cookie_browser: str | None):
    bvid = bili_bvid(url)
    if not bvid:
        return None, None, "解析不出 BV 号"
    cookies = bili_cookies(cookie_browser)
    view = bili_api("/x/web-interface/view", {"bvid": bvid}, cookies)
    meta = {
        "id": bvid,
        "title": view.get("title") or "未命名视频",
        "uploader": (view.get("owner") or {}).get("name") or "",
        "duration": view.get("duration") or 0,
        "upload_date": str(view.get("pubdate") or ""),
        "webpage_url": f"https://www.bilibili.com/video/{bvid}",
        "extractor_key": "BiliBili(API)",
    }
    cid = (view.get("pages") or [{}])[0].get("cid") or view.get("cid")
    cues, lan = bili_subtitle_cues(cid, bvid, cookies)
    if not cues:
        hint = ("未登录只能拿到空字幕列表，解法：加 --cookie-browser safari（或 chrome）"
                if not cookies else "登录态里这个视频也没有可用字幕（UP 主没开 CC/AI 字幕）")
        return meta, None, hint
    return meta, cues, f"B站字幕（{lan or 'zh'}）"


# --------------------------------------------------------------------------
# 字幕解析
# --------------------------------------------------------------------------

def parse_ts(s: str) -> int:
    s = s.strip().replace(",", ".")
    m = re.match(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$", s)
    if not m:
        return 0
    h, mi, se, frac = m.groups()
    return ((int(h or 0) * 3600 + int(mi) * 60 + int(se)) * 1000
            + int((frac or "0").ljust(3, "0")))


def clean_text(raw: str) -> str:
    t = TAG.sub("", raw)
    for k, v in ENTITIES.items():
        t = t.replace(k, v)
    t = re.sub(r"[ \t\u00a0]+", " ", t)
    return t.strip()


def parse_vtt(text: str) -> list[tuple[int, int, str]]:
    """VTT -> [(start_ms, end_ms, text)]，顺带去掉滚动字幕的重复行。"""
    lines = text.splitlines()
    cues: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "-->" not in line:
            i += 1
            continue
        left, right = line.split("-->", 1)
        start = parse_ts(left)
        # 右侧可能带定位参数，例如 "00:00:03.000 align:start position:0%"
        end = parse_ts(right.strip().split(" ")[0])
        i += 1
        buf = []
        while i < len(lines) and lines[i].strip():
            buf.append(lines[i])
            i += 1
        t = clean_text(" ".join(buf))
        if t:
            cues.append((start, end, t))
    return dedupe_cues(cues)


def parse_json3(text: str) -> list[tuple[int, int, str]]:
    data = json.loads(text)
    cues = []
    for ev in data.get("events") or []:
        segs = ev.get("segs") or []
        t = clean_text("".join(s.get("utf8", "") for s in segs))
        if not t:
            continue
        start = int(ev.get("tStartMs") or 0)
        dur = int(ev.get("dDurationMs") or 0)
        cues.append((start, start + dur, t))
    return dedupe_cues(cues)


def dedupe_cues(cues: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for start, end, t in cues:
        if out:
            prev = out[-1][2]
            if t == prev:                      # 完全重复
                continue
            if t.startswith(prev) and len(t) > len(prev):   # 滚动字幕补全
                out[-1] = (out[-1][0], max(out[-1][1], end), t)
                continue
            if prev.endswith(t):               # 新 cue 是旧内容的尾巴
                continue
        out.append((start, end, t))
    return out


def to_paragraphs(cues, max_len: int = 240, gap_ms: int = 2500):
    """把 cue 合并成可读段落，段落带起始时间戳。"""
    paras: list[tuple[int, int, str]] = []
    cur: list[str] = []
    cur_start = cur_end = 0
    size = 0
    for start, end, text in cues:
        if cur and (size + len(text) > max_len or start - cur_end > gap_ms):
            paras.append((cur_start, cur_end, " ".join(cur)))
            cur, size = [], 0
        if not cur:
            cur_start = start
        cur.append(text)
        cur_end = end
        size += len(text)
    if cur:
        paras.append((cur_start, cur_end, " ".join(cur)))
    return paras


def to_blocks(paras, max_chars: int):
    """按字符预算切块，块内保留段落和时间戳。"""
    blocks, cur, size = [], [], 0
    for p in paras:
        if cur and size + len(p[2]) > max_chars:
            blocks.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p[2])
    if cur:
        blocks.append(cur)
    return blocks


# --------------------------------------------------------------------------
# 调 opencode Go 的额度
# --------------------------------------------------------------------------

def scan_stream(stdout: str) -> tuple[str, str]:
    """解析 opencode run --format json 的事件流，返回 (正文, 错误信息)。"""
    snapshots: dict[str, str] = {}
    order: list[str] = []
    deltas: list[str] = []
    errors: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                pid = node.get("id")
                if pid:
                    if pid not in snapshots:
                        order.append(pid)
                    snapshots[pid] = node["text"]
                else:
                    deltas.append(node["text"])
            if node.get("type") == "error":
                err = node.get("error") or {}
                data = err.get("data") or {}
                msg = data.get("message") or err.get("message") or data.get("responseBody")
                if msg:
                    errors.append(str(msg))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue

    text = "".join(snapshots[k] for k in order) + "".join(deltas)
    return text.strip(), (errors[0] if errors else "")


def strip_ansi_fallback(stdout: str) -> str:
    plain = ANSI.sub("", stdout)
    return "\n".join(l for l in plain.splitlines()
                     if l.strip() and not re.match(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s", l)).strip()


OPENCODE_CAPS: dict | None = None


def opencode_caps(oc: str) -> dict:
    """v1 和 v2 的 run 参数不同（v2 去掉了 --dir，多了 --standalone），
    所以不猜版本，直接问 CLI 自己支持什么。"""
    global OPENCODE_CAPS
    if OPENCODE_CAPS is not None:
        return OPENCODE_CAPS
    caps = {"version": "", "format": False, "standalone": False,
            "auto": False, "help_ok": False}
    try:
        r = sh([oc, "--version"], timeout=30)
        lines = (strip_ansi_fallback(r.stdout) or "").strip().splitlines()
        caps["version"] = lines[-1] if lines else ""
    except Exception:
        pass
    try:
        h = sh([oc, "run", "--help"], timeout=30)
        text = (h.stdout or "") + (h.stderr or "")
        caps["help_ok"] = h.returncode == 0 and "run" in text
        if caps["help_ok"]:
            caps["format"] = "--format" in text
            caps["standalone"] = "--standalone" in text
            caps["auto"] = "--auto" in text
        else:
            # 连 help 都拿不到（例如沙箱里无法写日志）：先假设 v1 参数
            caps["format"] = True
    except Exception:
        caps["format"] = True
    OPENCODE_CAPS = caps
    return caps


def build_opencode_cmd(oc: str, prompt: str, model: str | None) -> list[str]:
    caps = opencode_caps(oc)
    cmd = [oc, "run"]
    if caps["standalone"]:          # v2：开私有 server，避免串进用户在跑的 TUI 会话
        cmd.append("--standalone")  # 注意：v2 里这是 run 的 flag，必须放在 run 之后
    if caps["format"]:
        cmd += ["--format", "json"]
    if caps["auto"]:
        cmd += ["--auto"]           # 只在临时目录里跑，权限提示会卡死非交互流程
    if model:
        cmd += ["-m", model]
    cmd.append(prompt)
    return cmd


def extract_cli_error(text: str) -> str:
    """从 CLI 的帮助/报错混杂输出里挑出真正的原因那一行。"""
    lines = [l.strip() for l in strip_ansi_fallback(text).splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if line.upper() == "ERROR" and i + 1 < len(lines):
            return lines[i + 1]
        if line.upper().startswith("ERROR:"):
            return line[6:].strip()
    return ""


def attempt_once(prompt: str, model: str, workdir: Path,
                 timeout: int) -> tuple[str, str]:
    """跑一次模型，返回 (正文, 错误)。不抛异常，方便 doctor 直接展示原因。"""
    oc = which("opencode")
    if not oc:
        return "", "找不到 opencode 命令（npm i -g opencode-ai）"
    cmd = build_opencode_cmd(oc, prompt, model)
    try:
        r = sh(cmd, timeout=timeout, cwd=workdir)
    except subprocess.TimeoutExpired:
        return "", f"超时（>{timeout}s）"

    text, err = scan_stream(r.stdout or "")
    if text:
        return text, ""
    if not err:
        err = (extract_cli_error(r.stdout or "") or extract_cli_error(r.stderr or "")
               or f"exit={r.returncode}，无输出")
    return "", err


AUTH_WORDS = ("expired", "401", "authentication", "unauthorized", "invalid api key",
              "api key has expired")
RETRY_WORDS = ("rate", "429", "overload", "502", "503", "504", "timeout",
               "temporarily", "try again")


def explain_failure(err: str) -> str:
    low = err.lower()
    if any(w in low for w in AUTH_WORDS):
        return (
            f"opencode Go 额度凭证失效：{err}\n\n"
            "修复步骤（注意：重新登录不会续期，必须换新 key）：\n"
            "  1. 打开 https://opencode.ai/auth 控制台，重新生成 API key\n"
            "  2. 本机执行 opencode auth login → 选 OpenCode Go → 粘贴新 key\n"
            "  3. 跑 python3 vlearn.py doctor --ping 确认能回话\n"
        )
    return f"调用模型失败：{err}"


def call_model(prompt: str, model: str, workdir: Path, timeout: int,
               retries: int = 2) -> str:
    last = ""
    for attempt in range(retries + 1):
        text, err = attempt_once(prompt, model, workdir, timeout)
        if text:
            return text
        last = err
        low = err.lower()
        if any(w in low for w in AUTH_WORDS):
            die(explain_failure(err))
        if attempt < retries:
            wait = 30 if any(w in low for w in RETRY_WORDS) else 3 * (attempt + 1)
            info(f"  · 第 {attempt + 1} 次失败（{err[:80]}），{wait}s 后重试")
            time.sleep(wait)
    die(explain_failure(last))


# --------------------------------------------------------------------------
# 备用后端：codex（ChatGPT 订阅额度）。opencode 的 key 挂了也能先跑通。
# --------------------------------------------------------------------------

def attempt_codex(prompt: str, workdir: Path, timeout: int,
                  model: str | None) -> tuple[str, str]:
    cx = which("codex")
    if not cx:
        return "", "找不到 codex 命令"
    out_file = workdir / f".codex-{hashlib.sha1(prompt.encode()).hexdigest()[:8]}.md"
    cmd = [cx, "exec", "--skip-git-repo-check", "--ephemeral",
           "-s", "read-only", "-C", str(workdir), "-o", str(out_file)]
    if model:
        cmd[4:4] = ["-m", model]
    cmd.append(prompt)
    try:
        r = sh(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", f"超时（>{timeout}s）"
    if out_file.exists():
        text = out_file.read_text(encoding="utf-8").strip()
        out_file.unlink(missing_ok=True)
        if text:
            return text, ""
    err = strip_ansi_fallback(r.stderr or "") or f"exit={r.returncode}"
    return "", (err.splitlines() or ["无输出"])[-1]


def call_codex(prompt: str, workdir: Path, timeout: int,
               model: str | None, retries: int = 1) -> str:
    last = ""
    for attempt in range(retries + 1):
        text, err = attempt_codex(prompt, workdir, timeout, model)
        if text:
            return text
        last = err
        if attempt < retries:
            info(f"  · codex 第 {attempt + 1} 次失败（{err[:80]}），重试")
            time.sleep(3)
    die(f"codex 调用失败：{last}")


def generate(prompt: str, args, workdir: Path) -> str:
    """按 --backend 分发到 opencode 或 codex。"""
    if getattr(args, "backend", "opencode") == "codex":
        return call_codex(prompt, workdir, args.timeout, args.model or None)
    return call_model(prompt, args.model, workdir, args.timeout)


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

SYSTEM_RULES = """【硬性要求】
1. 只输出 Markdown，不要开场白、不要“好的，这是总结”这类过渡语。
2. 中文输出，保留原文里的专有名词、人名、产品名、数字、版本号。
3. 每个要点后面用 [mm:ss] 标注它在转写里真实出现的时刻，禁止编造时间戳。
4. 只写转写里确实出现过的内容。听不清或信息缺失就写“（未听清）”，不要靠常识补齐。
5. 不要复述这份说明，也不要解释你在做什么。"""


def chunk_prompt(title: str, idx: int, total: int,
                 start_ms: int, end_ms: int, body: str) -> str:
    return f"""你是视频笔记整理助手。下面是一部视频的转写片段，第 {idx}/{total} 段，时间范围 {ts(start_ms)}–{ts(end_ms)}。

{SYSTEM_RULES}

【输出格式】
- 用 4-8 条要点概括这一段，每条 1-2 句，带 [mm:ss]
- 最后单独一行：`关键词：词1、词2、词3`

视频标题：{title}
----
{body}"""


NOTE_SPEC = """【输出格式】
# {title}

> 一句话结论：……

## 核心要点
3-7 条，每条带 [mm:ss]，读者只看这一节也能掌握视频内容。

## 章节脉络
用表格，列为：| 时间 | 主题 | 讲了什么 |

## 值得回看的片段
2-5 条，标明时间点和回看理由。

## 术语与数字
视频里出现的概念、产品、数据、结论性数字。

## 行动清单
如果视频里给了可执行建议，列出来；没有就写“无”。"""


def final_prompt(title: str, body: str) -> str:
    """短视频：一次调用直接从转写出最终结构，不走分段。"""
    return f"""下面是一部视频的完整转写（带时间戳）。请整理成一份完整、可独立阅读的中文笔记。

{SYSTEM_RULES}

{NOTE_SPEC.format(title=title)}

视频标题：{title}
----
{body}"""


def reduce_prompt(title: str, chunk_notes: list[str]) -> str:
    joined = "\n\n".join(
        f"### 第 {i} 段笔记\n{n}" for i, n in enumerate(chunk_notes, 1)
    )
    return f"""下面是一部视频按时间切分后的分段笔记（共 {len(chunk_notes)} 段，按时间顺序排列）。请合并成一份完整、可独立阅读的笔记。

{SYSTEM_RULES}

{NOTE_SPEC.format(title=title)}

----
{joined}"""


# --------------------------------------------------------------------------
# 本地文件与语音转写（字幕缺失时的兜底）
# --------------------------------------------------------------------------

def is_url(target: str) -> bool:
    return bool(re.match(r"^https?://", target))


def local_media(target: str) -> Path | None:
    p = Path(target).expanduser()
    return p if p.is_file() else None


def media_meta(path: Path) -> dict:
    return {
        "id": "local-" + hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:8],
        "title": path.stem,
        "uploader": "",
        "duration": 0,
        "upload_date": "",
        "webpage_url": str(path.resolve()),
        "extractor_key": "LocalFile",
    }


def whisper_cpp_bin() -> str | None:
    for name in ("whisper-cli", "whisper-cpp", "whisper", "main"):
        found = which(name)
        if found:
            return found
    return None


def whisper_cpp_model() -> Path | None:
    env = os.environ.get("VLEARN_WHISPER_MODEL")
    if env and Path(env).is_file():
        return Path(env)
    for d in (HERE / "models", Path.home() / ".cache" / "whisper.cpp",
              Path.home() / "whisper.cpp" / "models"):
        if d.is_dir():
            for f in sorted(d.glob("ggml-*.bin")):
                return f
    return None


def asr_status() -> dict:
    status = {"faster-whisper": False, "whisper-cpp": False,
              "ffmpeg": bool(which("ffmpeg")),
              "cpp_bin": "", "cpp_model": ""}
    try:
        import faster_whisper  # noqa: F401
        status["faster-whisper"] = True
    except Exception:
        pass
    b, m = whisper_cpp_bin(), whisper_cpp_model()
    status["cpp_bin"], status["cpp_model"] = b or "", str(m or "")
    status["whisper-cpp"] = bool(b and m)
    return status


def pick_asr_backend(args) -> str:
    if args.asr == "none":
        return "none"
    status = asr_status()
    if args.asr in ("auto", "faster-whisper") and status["faster-whisper"]:
        return "faster-whisper"
    if args.asr in ("auto", "whisper-cpp") and status["whisper-cpp"]:
        return "whisper-cpp"
    if args.asr in ("auto", "faster-whisper"):
        die(
            "没有可用的语音识别引擎。装一个即可（推荐第一个，不需要系统 ffmpeg）：\n"
            "  .venv/bin/pip install faster-whisper\n"
            "或：brew install whisper-cpp 并把 ggml-*.bin 模型放进 videonotes/models/"
        )
    die(f"--asr {args.asr} 不可用："
        + ("faster-whisper 未安装" if args.asr == "faster-whisper"
           else "whisper.cpp 或模型缺失"))


def transcribe(path: Path, args) -> tuple[list, str]:
    backend = pick_asr_backend(args)
    if backend == "faster-whisper":
        return transcribe_faster_whisper(path, args)
    return transcribe_whisper_cpp(path, args)


def transcribe_faster_whisper(path: Path, args) -> tuple[list, str]:
    if args.hf_mirror:
        os.environ.setdefault("HF_ENDPOINT", args.hf_mirror)
    info(f"  引擎 faster-whisper · 模型 {args.asr_model}（首次运行会自动下载）…")
    from faster_whisper import WhisperModel

    model = WhisperModel(args.asr_model, device="auto", compute_type="int8")
    lang = None if args.asr_language == "auto" else args.asr_language
    segments, info_obj = model.transcribe(str(path), language=lang,
                                          vad_filter=True, beam_size=5)
    info(f"  识别语言 {info_obj.language}"
         f"（置信度 {getattr(info_obj, 'language_probability', 0):.2f}）")
    cues, n = [], 0
    for seg in segments:
        n += 1
        text = clean_text(seg.text or "")
        if text:
            cues.append((int(seg.start * 1000), int(seg.end * 1000), text))
        if n % 25 == 0:
            info(f"    已转写 {ts(int(seg.end * 1000))} …")
    return cues, f"语音转写（faster-whisper · {args.asr_model}）"


def transcribe_whisper_cpp(path: Path, args) -> tuple[list, str]:
    ff = which("ffmpeg")
    if not ff:
        die("whisper.cpp 需要先把音轨转成 16k wav，请先 brew install ffmpeg，"
            "或改用 faster-whisper（pip 安装，无需 ffmpeg）")
    wav = path.with_suffix(".16k.wav")
    info("  抽取音轨（ffmpeg）…")
    r = sh([ff, "-nostdin", "-y", "-i", str(path), "-vn", "-ac", "1",
            "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], timeout=1800)
    if r.returncode != 0 or not wav.exists():
        die(f"ffmpeg 抽音轨失败：{(r.stderr or '')[-200:]}")
    given = Path(args.asr_model).expanduser() if args.asr_model else None
    model_path = given if (given and given.is_file()) else whisper_cpp_model()
    if not model_path:
        die("找不到 whisper.cpp 的模型文件，用 --asr-model 指定 ggml-*.bin 路径")
    prefix = path.with_suffix("")
    info(f"  引擎 whisper.cpp · 模型 {model_path.name} …")
    cmd = [whisper_cpp_bin(), "-m", str(model_path),
           "-f", str(wav), "-osrt", "-of", str(prefix), "-np"]
    if args.asr_language != "auto":
        cmd += ["-l", args.asr_language]
    r = sh(cmd, timeout=3600)
    srt = prefix.with_suffix(".srt")
    if not srt.exists():
        die(f"whisper.cpp 没有产出字幕：{(r.stderr or '')[-200:]}")
    cues = parse_vtt(srt.read_text(encoding="utf-8", errors="replace"))
    wav.unlink(missing_ok=True)
    return cues, "语音转写（whisper.cpp）"


def ytdlp_extra_args(url: str) -> list[str]:
    if "youtube.com" in url or "youtu.be" in url:
        return ["--extractor-args", "youtube:player_client=android"]
    return []


def download_audio(url: str, outdir: Path, cookie_browser: str | None) -> Path:
    yt = which("yt-dlp")
    if not yt:
        die("找不到 yt-dlp")
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [yt, "--no-playlist", "--no-warnings", "-f", "bestaudio/best",
           "-o", str(outdir / "audio.%(ext)s")] + ytdlp_extra_args(url)
    if cookie_browser:
        cmd += ["--cookies-from-browser", cookie_browser]
    cmd.append(url)
    info("· 下载音轨（用于语音识别）…")
    r = sh(cmd, timeout=3600)
    files = [p for p in outdir.glob("audio.*")
             if p.suffix not in (".part", ".ytdl")]
    if not files:
        tail = [l for l in (r.stderr or "").splitlines() if l.startswith("ERROR")]
        die("音轨下载失败：" + (tail[-1] if tail else f"exit={r.returncode}"))
    return files[0]


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def prepare(target: str, args) -> tuple[dict, list, str, str]:
    """拿到 (meta, 段落, 来源说明, 视频 id)：字幕优先，字幕缺失时走语音识别。"""
    local = local_media(target)
    if local:
        meta = media_meta(local)
        info(f"· 本地文件：{local.name}")
        cues, note = transcribe(local, args)
        if not cues:
            die("语音识别没有产出文本，确认这个文件确实有音轨。")
        info(f"· 来源：{note}")
        paras = to_paragraphs(cues)
        info(f"  识别出 {len(cues)} 条 → {len(paras)} 个段落")
        return meta, paras, note, meta["id"]

    if not is_url(target):
        die(f"既不是可访问的链接，也不是存在的文件：{target}")

    info("· 读取视频信息 …")

    if is_bilibili(target):
        meta, cues, note = bili_prepare(target, args.cookie_browser)
        if meta is None:
            die(f"B 站链接解析失败：{note}")
        vid = str(meta["id"])
        info(f"  {meta['title']}")
        if meta.get("duration"):
            info(f"  时长 {ts(int(meta['duration']) * 1000)} · 平台 BiliBili(API)")
        if cues is None:
            if args.asr == "none":
                die(f"拿不到 B 站字幕：{note}\n"
                    "  加 --cookie-browser safari / chrome 传登录态后重试。")
            info(f"· 字幕不可用（{note}），改用语音识别 …")
            audio = download_audio(target, Path(args.out).expanduser() / "_audio" / vid,
                                   args.cookie_browser)
            cues, note = transcribe(audio, args)
        else:
            info(f"· 字幕：{note}")
        paras = to_paragraphs(cues)
        info(f"  {len(cues)} 条 → {len(paras)} 个段落")
        return meta, paras, note, vid

    meta = fetch_meta(target, args.cookie_browser)
    title = meta.get("title") or "未命名视频"
    vid = str(meta.get("id") or hashlib.sha1(target.encode()).hexdigest()[:10])
    dur = meta.get("duration") or 0
    info(f"  {title}")
    if dur:
        info(f"  时长 {ts(int(dur * 1000))} · 平台 {meta.get('extractor_key', '?')}")

    source, lang, entries = pick_subtitle(meta)
    if entries:
        info(f"· 字幕：{source} · {lang}")
        raw, fmt = download_subtitle(entries)
        cues = parse_vtt(raw) if fmt == "vtt" else parse_json3(raw)
        if not cues:
            die("字幕下载成功但解析出 0 条，格式可能是新的。")
        note = f"{source}（{lang}）"
    else:
        if args.asr == "none":
            die("这个视频没有可下载的字幕，而且 --asr none 关掉了语音识别。\n"
                "  去掉 --asr none 重跑，或加 --cookie-browser 传登录态。")
        info("· 没有可用字幕，改用语音识别 …")
        audio = download_audio(target, Path(args.out).expanduser() / "_audio" / vid,
                               args.cookie_browser)
        cues, note = transcribe(audio, args)
        if not cues:
            die("语音识别没有产出文本，确认视频有音轨。")

    paras = to_paragraphs(cues)
    info(f"  {len(cues)} 条 → {len(paras)} 个段落")
    return meta, paras, note, vid


def write_transcript(path: Path, meta: dict, paras, subtitle_note: str):
    lines = [f"# {meta.get('title', '未命名')}", ""]
    for k, label in (("uploader", "作者"), ("webpage_url", "链接"),
                     ("duration", "时长"), ("upload_date", "发布")):
        v = meta.get(k)
        if v:
            lines.append(f"- {label}：{ts(int(v * 1000)) if k == 'duration' else v}")
    lines += [f"- 字幕来源：{subtitle_note}", "", "---", ""]
    for start, _end, text in paras:
        lines.append(f"**[{ts(start)}]** {text}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_summarize(url: str, args) -> Path:
    meta, paras, sub_note, vid = prepare(url, args)
    title = meta.get("title") or "未命名视频"

    out_dir = Path(args.out).expanduser() / f"{vid}"
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    write_transcript(out_dir / "transcript.md", meta, paras, sub_note)

    blocks = to_blocks(paras, args.max_chars)
    total_chars = sum(len(p[2]) for p in paras)
    calls = "1 次成稿" if len(blocks) == 1 else f"{len(blocks)} 块 + 1 次合并"
    info(f"· 计划：{calls}，转写共 {human(total_chars)} 字，"
         f"后端 {args.backend}"
         + (f" · 模型 {args.model}" if args.model else ""))
    if not args.yes:
        ans = input("  继续？[Y/n] ").strip().lower()
        if ans not in ("", "y", "yes"):
            info("已取消，只有 transcript.md 落盘。")
            return out_dir

    if len(blocks) == 1:
        body = "\n".join(f"[{ts(s)}] {t}" for s, _e, t in blocks[0])
        digest = hashlib.sha1(body.encode()).hexdigest()[:10]
        cache = chunks_dir / f"final-{digest}.md"
        if cache.exists() and not args.force:
            info("  · 命中缓存（整篇）")
            final = cache.read_text(encoding="utf-8")
        else:
            info(f"  · 一次成稿，{human(len(body))} 字 → 生成中 …")
            final = generate(final_prompt(title, body), args, out_dir)
            cache.write_text(final, encoding="utf-8")
    else:
        notes: list[str] = []
        for i, block in enumerate(blocks, 1):
            body = "\n".join(f"[{ts(s)}] {t}" for s, _e, t in block)
            digest = hashlib.sha1(body.encode()).hexdigest()[:10]
            cache = chunks_dir / f"{i:02d}-{digest}.md"
            if cache.exists() and not args.force:
                info(f"  [{i}/{len(blocks)}] 命中缓存")
                notes.append(cache.read_text(encoding="utf-8"))
                continue
            info(f"  [{i}/{len(blocks)}] {ts(block[0][0])}–{ts(block[-1][1])} "
                 f"{human(len(body))} 字 → 生成中 …")
            note = generate(
                chunk_prompt(title, i, len(blocks), block[0][0], block[-1][1], body),
                args, out_dir,
            )
            cache.write_text(note, encoding="utf-8")
            notes.append(note)

        info(f"· 合并 {len(notes)} 段笔记 …")
        final = generate(reduce_prompt(title, notes), args, out_dir)

    (out_dir / "notes.md").write_text(final.rstrip() + "\n", encoding="utf-8")
    meta_out = {k: meta.get(k) for k in
                ("id", "title", "uploader", "duration", "upload_date",
                 "webpage_url", "extractor_key")}
    meta_out["subtitle"] = sub_note
    meta_out["backend"] = args.backend
    meta_out["model"] = args.model or "(后端默认)"
    meta_out["chunks"] = len(blocks)
    (out_dir / "meta.json").write_text(
        json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_dir


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

def cmd_doctor(args):
    ok = True
    info("vlearn 自检\n" + "-" * 46)

    yt = which("yt-dlp")
    if yt:
        r = sh([yt, "--version"], timeout=30)
        info(f"✓ yt-dlp      {r.stdout.strip()}  ({yt})")
    else:
        ok = False
        info("✗ yt-dlp      未安装 → pip install yt-dlp")

    oc = which("opencode")
    if oc:
        r = sh([oc, "--version"], timeout=30)
        info(f"✓ opencode    {ANSI.sub('', r.stdout).strip() or '?'}")
    else:
        ok = False
        info("✗ opencode    未安装 → npm i -g opencode-ai && opencode auth login")

    if args.backend == "opencode" and oc:
        info(f"· 后端        opencode · {args.model}")
        if args.ping:
            info("· 实测额度（消耗 1 次请求）…")
            work = HERE / "out" / "_ping"
            work.mkdir(parents=True, exist_ok=True)
            text, err = attempt_once("只回答两个字：可用", args.model, work, 120)
            if text:
                info(f"✓ 额度可用    模型回复：{text.splitlines()[0][:40]}")
            else:
                ok = False
                info("✗ 额度不可用")
                for line in explain_failure(err).splitlines():
                    info("  " + line)
        else:
            info("  跳过实测。加 --ping 可实测一次")

    if args.backend == "codex":
        cx = which("codex")
        if not cx:
            ok = False
            info("✗ codex       未安装")
        else:
            r = sh([cx, "--version"], timeout=30)
            ver = (strip_ansi_fallback(r.stdout) or "?").splitlines()[0]
            info(f"· 后端        codex · {ver}")
            if args.ping:
                info("· 实测额度（消耗 1 次请求）…")
                work = HERE / "out" / "_ping"
                work.mkdir(parents=True, exist_ok=True)
                text, err = attempt_codex("只回答两个字：可用", work, 180, None)
                if text:
                    info(f"✓ 额度可用    模型回复：{text.splitlines()[0][:40]}")
                else:
                    ok = False
                    info(f"✗ codex 不可用：{err[:160]}")

    st = asr_status()
    engines = [k for k in ("faster-whisper", "whisper-cpp") if st[k]]
    if engines:
        info(f"✓ 语音识别    {' / '.join(engines)}"
             + (f"（模型 {args.asr_model}）" if "faster-whisper" in engines and
                args.asr == "auto" else ""))
    else:
        ok = False if args.asr != "none" else ok
        info("· 语音识别    无 → 无字幕视频会失败；装：.venv/bin/pip install faster-whisper")
    info(f"· ffmpeg      {'有' if st['ffmpeg'] else '无（faster-whisper 不需要，whisper.cpp 需要）'}")
    info("-" * 46)
    info("结论：" + ("链路齐了，可以直接跑视频链接。" if ok else "先把上面 ✗ 的问题修掉。"))
    return 0 if ok else 1


def cmd_sub(url: str, args) -> int:
    meta, paras, sub_note, vid = prepare(url, args)
    out_dir = Path(args.out).expanduser() / vid
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "transcript.md"
    write_transcript(p, meta, paras, sub_note)
    info(f"· 已写入 {p}（{human(sum(len(x[2]) for x in paras))} 字）")
    return 0


def main(argv=None) -> int:
    ensure_venv()
    ap = argparse.ArgumentParser(
        prog="vlearn", description="视频链接 → 带时间戳的中文笔记（用 opencode Go 额度总结）")
    ap.add_argument("target", nargs="?", help="视频链接，或 doctor / sub <链接>")
    ap.add_argument("extra", nargs="*", help=argparse.SUPPRESS)
    ap.add_argument("--backend", choices=("opencode", "codex"), default="opencode",
                    help="opencode=用 Go 订阅额度；codex=用 ChatGPT 订阅额度")
    ap.add_argument("--model", default=None,
                    help=f"opencode 默认 {DEFAULT_MODEL}；codex 默认用其自身配置")
    ap.add_argument("--out", default=str(HERE / "out"), help="输出目录")
    ap.add_argument("--max-chars", type=int, default=12000, help="单次调用喂多少字")
    ap.add_argument("--timeout", type=int, default=600, help="单次调用超时秒数")
    ap.add_argument("--cookie-browser", default=None,
                    help="浏览器登录态，如 safari / chrome / edge")
    ap.add_argument("--yes", "-y", action="store_true", help="不问直接跑")
    ap.add_argument("--force", action="store_true", help="忽略分块缓存重跑")
    ap.add_argument("--asr", choices=("auto", "faster-whisper", "whisper-cpp", "none"),
                    default="auto", help="没有字幕时的语音识别引擎，默认 auto")
    ap.add_argument("--asr-model", default="small",
                    help="faster-whisper 模型名（tiny/base/small/medium/large-v3）"
                         "或 whisper.cpp 的 ggml 模型路径")
    ap.add_argument("--asr-language", default="auto", help="音频语言：auto / zh / en …")
    ap.add_argument("--hf-mirror", default=None,
                    help="HuggingFace 镜像，例如 https://hf-mirror.com")
    ap.add_argument("--version", action="version", version=f"vlearn {__version__}")
    ap.add_argument("--ping", action=argparse.BooleanOptionalAction, default=True,
                    help="doctor 时实测一次额度（默认开）")
    args = ap.parse_args(argv)
    if args.backend == "opencode":
        args.model = args.model or DEFAULT_MODEL

    if not args.target:
        ap.print_help()
        return 2
    if args.target == "doctor":
        return cmd_doctor(args)
    if args.target == "sub":
        url = args.extra[0] if args.extra else None
        if not url:
            ap.error("用法：vlearn.py sub <链接或本地文件>")
        return cmd_sub(url, args)
    if not is_url(args.target) and not local_media(args.target):
        ap.error(f"既不是链接也不是存在的文件：{args.target}"
                 "（要视频链接 / 本地文件 / doctor / sub）")

    try:
        out = run_summarize(args.target, args)
        info(f"\n✓ 完成：{out / 'notes.md'}")
        return 0
    except KeyboardInterrupt:
        info("\n已中断。已生成的 transcript.md / chunks 都还在，重跑会续上。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
