#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vlearn 的本地 Web 界面。只用标准库：http.server + SSE，不需要装 Flask/FastAPI。

它本身不实现任何业务逻辑，只是把 CLI 当成子进程跑起来、把输出流到浏览器。
所以命令行能跑通的，界面一定能跑通。
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOBS: dict[str, "Job"] = {}
LOCK = threading.Lock()


class Job:
    """一个正在跑的任务：包住 `vlearn.py <target>` 子进程。"""

    def __init__(self, target: str, opts: dict):
        self.id = uuid.uuid4().hex[:12]
        self.target = target
        self.opts = opts
        self.out_dir = HERE / "out" / f"web-{self.id}"
        self.q: queue.Queue[str | None] = queue.Queue()
        self.status = "running"
        self.started = time.time()
        self.proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def cmd(self) -> list[str]:
        o = self.opts
        cmd = [sys.executable, str(HERE / "vlearn.py"), self.target, "-y",
               "--out", str(self.out_dir)]
        if o.get("backend"):
            cmd += ["--backend", o["backend"]]
        if o.get("model"):
            cmd += ["--model", o["model"]]
        if o.get("max_chars"):
            cmd += ["--max-chars", str(o["max_chars"])]
        if not o.get("asr", True):
            cmd += ["--asr", "none"]
        if o.get("asr_model"):
            cmd += ["--asr-model", o["asr_model"]]
        if o.get("asr_language") and o["asr_language"] != "auto":
            cmd += ["--asr-language", o["asr_language"]]
        if o.get("mirror"):
            cmd += ["--hf-mirror", "https://hf-mirror.com"]
        if o.get("sessdata"):
            cmd += ["--bili-sessdata", o["sessdata"]]
        cookie = o.get("cookie_browser")
        if cookie and cookie != "none":
            cmd += ["--cookie-browser", cookie]
        return cmd

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            self.proc = subprocess.Popen(
                self.cmd(), cwd=str(HERE), text=True, bufsize=1,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.q.put(line.rstrip())
            code = self.proc.wait()
            self.status = "done" if code == 0 else "error"
            if code != 0:
                self.q.put(f"✗ 进程退出码 {code}")
        except Exception as exc:  # 起不来也要让前端收到结束信号
            self.status = "error"
            self.q.put(f"✗ 无法启动：{type(exc).__name__}: {exc}")
        finally:
            self.q.put(None)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.status = "stopped"

    def find_notes(self) -> Path | None:
        """CLI 会在 --out 下再建一层 <视频id>/，所以这里要往下找一层。"""
        direct = self.out_dir / "notes.md"
        if direct.exists():
            return direct
        for cand in sorted(self.out_dir.glob("*/notes.md")):
            return cand
        return None

    def result(self) -> dict:
        notes = self.find_notes()
        meta = notes.parent / "meta.json" if notes else None
        data = {"status": self.status,
                "out_dir": str(notes.parent if notes else self.out_dir),
                "elapsed": round(time.time() - self.started, 1)}
        if notes:
            data["notes"] = notes.read_text(encoding="utf-8")
        if meta and meta.exists():
            try:
                data["meta"] = json.loads(meta.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return data


def _json(handler: BaseHTTPRequestHandler, payload: dict, code: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "vlearn-web"

    def log_message(self, fmt, *args):  # 别把访问日志打到终端里，干扰不大但很吵
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/stream":
            self._stream()
            return
        if path == "/api/result":
            job = self._job()
            if job:
                _json(self, job.result())
            return
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}

        if path == "/api/start":
            target = (payload.get("target") or "").strip()
            if not target:
                _json(self, {"error": "请输入视频链接、BV 号或本地文件路径"}, 400)
                return
            job = Job(target, payload)
            with LOCK:
                JOBS[job.id] = job
            job.start()
            _json(self, {"job": job.id})
            return

        if path == "/api/stop":
            job = JOBS.get(payload.get("job", ""))
            if job:
                job.stop()
            _json(self, {"ok": True})
            return

        self.send_error(404)

    def _job(self) -> Job | None:
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        job_id = ""
        for part in qs.split("&"):
            if part.startswith("job="):
                job_id = part[4:]
        job = JOBS.get(job_id)
        if not job:
            _json(self, {"error": "任务不存在"}, 404)
            return None
        return job

    def _stream(self):
        job = self._job()
        if not job:
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                try:
                    line = job.q.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if line is None:
                    break
                chunk = json.dumps({"line": line}, ensure_ascii=False)
                self.wfile.write(f"data: {chunk}\n\n".encode("utf-8"))
                self.wfile.flush()
            done = json.dumps({"status": job.status}, ensure_ascii=False)
            self.wfile.write(f"event: done\ndata: {done}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"vlearn 界面已启动：{url}")
    print("按 Ctrl-C 停止。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>vlearn · 视频转笔记</title>
<style>
  :root {
    --bg: #0c0f14; --panel: #141922; --panel-2: #1b2230; --line: #26303f;
    --text: #e8edf5; --muted: #8b98ab; --accent: #7cc4a4; --accent-2: #2f6d57;
    --warn: #e0b050; --danger: #e2705f; --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Segoe UI", sans-serif;
    padding: 32px 20px 80px;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  header { display: flex; align-items: center; gap: 12px; margin-bottom: 24px; }
  h1 { font-size: 20px; margin: 0; letter-spacing: .5px; }
  h1 span { color: var(--accent); }
  .pill {
    margin-left: auto; font-size: 12px; padding: 4px 10px; border-radius: 99px;
    border: 1px solid var(--line); color: var(--muted); background: var(--panel);
  }
  .pill.running { color: var(--warn); border-color: #4a3d1e; }
  .pill.done { color: var(--accent); border-color: var(--accent-2); }
  .pill.error { color: var(--danger); border-color: #4a2620; }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 18px; margin-bottom: 16px;
  }
  textarea, input, select {
    width: 100%; background: var(--panel-2); border: 1px solid var(--line);
    color: var(--text); border-radius: 8px; padding: 10px 12px; font-family: inherit;
    font-size: 14px; outline: none;
  }
  textarea { resize: vertical; min-height: 62px; }
  textarea:focus, input:focus, select:focus { border-color: var(--accent-2); }
  .row { display: flex; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
  button {
    background: var(--accent); color: #06231a; border: 0; border-radius: 8px;
    padding: 10px 20px; font-weight: 700; font-size: 14px; cursor: pointer;
  }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--line); font-weight: 500; }
  details { margin-top: 4px; }
  summary { cursor: pointer; color: var(--muted); font-size: 13px; user-select: none; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 14px; }
  .field label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; }
  .check { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); margin-top: 12px; }
  .check input { width: auto; }
  h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin: 0 0 12px; }
  #log {
    font-family: var(--mono); font-size: 12.5px; line-height: 1.75;
    background: #0a0d12; border: 1px solid var(--line); border-radius: 8px;
    padding: 12px; height: 260px; overflow: auto; white-space: pre-wrap; color: #b9c6d8;
  }
  #log .ok { color: var(--accent); } #log .err { color: var(--danger); }
  #result { display: none; }
  #result h1 { font-size: 19px; margin: 0 0 12px; }
  #result h2 { font-size: 15px; text-transform: none; letter-spacing: 0; color: var(--text);
               margin: 22px 0 10px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
  #result blockquote { margin: 0 0 16px; padding: 10px 14px; border-left: 3px solid var(--accent-2);
                       background: var(--panel-2); color: #cfdaea; border-radius: 0 8px 8px 0; }
  #result ul { padding-left: 20px; } #result li { margin: 6px 0; }
  #result table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 8px 0; }
  #result th, #result td { border: 1px solid var(--line); padding: 7px 10px; text-align: left; vertical-align: top; }
  #result th { background: var(--panel-2); font-weight: 600; }
  #result code { font-family: var(--mono); background: var(--panel-2); padding: 1px 5px; border-radius: 4px; font-size: 13px; }
  .path { font-family: var(--mono); font-size: 12px; color: var(--muted); margin-top: 14px; word-break: break-all; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>v<span>learn</span></h1>
    <span class="pill" id="pill">就绪</span>
  </header>

  <div class="card">
    <textarea id="target" placeholder="粘贴 YouTube / B 站链接、BV 号，或本地文件的绝对路径"></textarea>
    <div class="row">
      <button id="run">开始生成笔记</button>
      <button id="stop" class="ghost" disabled>停止</button>
    </div>

    <details>
      <summary>高级选项</summary>
      <div class="grid">
        <div class="field"><label>额度后端</label>
          <select id="backend">
            <option value="opencode">opencode Go（默认）</option>
            <option value="codex">Codex（ChatGPT 额度）</option>
          </select></div>
        <div class="field"><label>模型（留空用默认）</label>
          <input id="model" placeholder="opencode-go/deepseek-v4.1-flash"></div>
        <div class="field"><label>每块字数</label>
          <input id="max_chars" type="number" value="12000"></div>
        <div class="field"><label>语音识别模型</label>
          <select id="asr_model">
            <option value="small">small（推荐，484MB）</option>
            <option value="base">base（145MB）</option>
            <option value="tiny">tiny（75MB，仅验证用）</option>
            <option value="medium">medium（1.5GB，更准）</option>
            <option value="large-v3">large-v3（3GB）</option>
          </select></div>
        <div class="field"><label>浏览器登录态</label>
          <select id="cookie_browser">
            <option value="none">不用（字幕拿不到就走语音识别）</option>
            <option value="auto">auto（依次尝试）</option>
            <option value="safari">safari</option>
            <option value="chrome">chrome</option>
            <option value="edge">edge</option>
          </select></div>
        <div class="field"><label>B 站 SESSDATA（可选，免授权）</label>
          <input id="sessdata" type="password" placeholder="F12 → Application → Cookies"></div>
      </div>
      <div class="check"><input type="checkbox" id="asr" checked><label for="asr">没有字幕时自动语音识别</label></div>
      <div class="check"><input type="checkbox" id="mirror"><label for="mirror">用镜像下载模型（国内网络建议勾选）</label></div>
    </details>
  </div>

  <div class="card">
    <h2>运行日志</h2>
    <div id="log"></div>
  </div>

  <div class="card" id="result">
    <h2>笔记</h2>
    <div id="notes"></div>
    <div class="path" id="outdir"></div>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const logEl = $("log"), pill = $("pill");
let jobId = null, es = null;

function setPill(text, cls) { pill.textContent = text; pill.className = "pill " + (cls || ""); }
function appendLog(line) {
  const div = document.createElement("div");
  div.textContent = line;
  if (line.startsWith("✓")) div.className = "ok";
  if (line.startsWith("✗")) div.className = "err";
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function options() {
  return {
    backend: $("backend").value,
    model: $("model").value.trim(),
    max_chars: parseInt($("max_chars").value || "12000", 10),
    asr: $("asr").checked,
    asr_model: $("asr_model").value,
    mirror: $("mirror").checked,
    sessdata: $("sessdata").value.trim(),
    cookie_browser: $("cookie_browser").value,
  };
}

async function start() {
  const target = $("target").value.trim();
  if (!target) { $("target").focus(); return; }
  logEl.innerHTML = "";
  $("result").style.display = "none";
  $("run").disabled = true; $("stop").disabled = false;
  setPill("运行中", "running");

  const res = await fetch("/api/start", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(Object.assign({target}, options())),
  });
  const data = await res.json();
  if (!res.ok) { appendLog("✗ " + (data.error || "启动失败")); setPill("出错", "error"); reset(); return; }
  jobId = data.job;
  es = new EventSource("/api/stream?job=" + jobId);
  es.onmessage = (e) => { try { appendLog(JSON.parse(e.data).line); } catch (_) {} };
  es.addEventListener("done", async (e) => {
    es.close();
    const status = JSON.parse(e.data).status;
    setPill(status === "done" ? "完成" : (status === "stopped" ? "已停止" : "出错"),
            status === "done" ? "done" : "error");
    await showResult();
    reset();
  });
  es.onerror = () => { es.close(); setPill("连接断开", "error"); reset(); };
}

async function showResult() {
  const res = await fetch("/api/result?job=" + jobId);
  const data = await res.json();
  if (!data.notes) return;
  $("notes").innerHTML = renderMarkdown(data.notes);
  $("outdir").textContent = "输出目录：" + data.out_dir;
  $("result").style.display = "block";
  $("result").scrollIntoView({behavior: "smooth", block: "start"});
}

function reset() { $("run").disabled = false; $("stop").disabled = true; }

async function stop() {
  if (!jobId) return;
  await fetch("/api/stop", {method: "POST", headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({job: jobId})});
  setPill("正在停止", "running");
}

// 极简 Markdown 渲染：只覆盖本项目会产出的语法
function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>");
}
function renderMarkdown(md) {
  const lines = md.split("\\n");
  let html = "", i = 0, list = null;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  while (i < lines.length) {
    const line = lines[i];
    if (/^\\|/.test(line) && /^\\|[\\s:|-]+\\|?$/.test(lines[i + 1] || "")) {
      closeList();
      const head = line.split("|").slice(1, -1).map(c => `<th>${inline(c.trim())}</th>`).join("");
      html += `<table><thead><tr>${head}</tr></thead><tbody>`;
      i += 2;
      while (i < lines.length && /^\\|/.test(lines[i])) {
        const cells = lines[i].split("|").slice(1, -1).map(c => `<td>${inline(c.trim())}</td>`).join("");
        html += `<tr>${cells}</tr>`; i++;
      }
      html += "</tbody></table>";
      continue;
    }
    const h = line.match(/^(#{1,6})\\s+(.*)$/);
    if (h) { closeList(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; i++; continue; }
    if (/^>\\s?/.test(line)) { closeList(); html += `<blockquote>${inline(line.replace(/^>\\s?/, ""))}</blockquote>`; i++; continue; }
    const li = line.match(/^[-*]\\s+(.*)$/);
    if (li) {
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${inline(li[1])}</li>`; i++; continue;
    }
    if (!line.trim()) { closeList(); i++; continue; }
    closeList(); html += `<p>${inline(line)}</p>`; i++;
  }
  closeList();
  return html;
}

$("run").onclick = start;
$("stop").onclick = stop;
$("target").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") start();
});
</script>
</body>
</html>
"""
