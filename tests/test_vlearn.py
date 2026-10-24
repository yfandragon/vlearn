#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线单元测试：不联网、不调用模型、不需要 opencode。

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vlearn as v  # noqa: E402

SAMPLE_VTT = """WEBVTT
Kind: captions
Language: zh

NOTE 这一行是注释，应该被忽略

00:00:00.120 --> 00:00:02.400 align:start position:0%
大家好<00:00:01.000><c>，</c><00:00:01.200><c>今天我们</c>

00:00:02.400 --> 00:00:04.800 align:start position:0%
大家好，今天我们
聊视频总结工具

00:00:02.400 --> 00:00:04.800
聊视频总结工具

00:10:30.000 --> 00:10:33.000
最后一段，讲成本和额度 &amp; 限额
"""

SAMPLE_JSON3 = json.dumps({
    "events": [
        {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "第一句"}]},
        {"tStartMs": 1500, "dDurationMs": 1000, "segs": [{"utf8": "第二句"}]},
        {"tStartMs": 2500, "dDurationMs": 1000, "segs": [{"utf8": "第二句"}]},
    ]
})


class TestTimeFormat(unittest.TestCase):
    def test_parse_ts_variants(self):
        self.assertEqual(v.parse_ts("00:00:01.500"), 1500)
        self.assertEqual(v.parse_ts("00:01:00,000"), 60000)
        self.assertEqual(v.parse_ts("01:02:03.004"), 3723004)
        self.assertEqual(v.parse_ts("02:03.500"), 123500)   # 无小时位
        self.assertEqual(v.parse_ts("坏数据"), 0)

    def test_ts_display(self):
        self.assertEqual(v.ts(0), "00:00")
        self.assertEqual(v.ts(95_000), "01:35")
        self.assertEqual(v.ts(3_725_000), "1:02:05")

    def test_ts_negative_clamped(self):
        self.assertEqual(v.ts(-5000), "00:00")


class TestVttParsing(unittest.TestCase):
    def setUp(self):
        self.cues = v.parse_vtt(SAMPLE_VTT)

    def test_cue_count_after_dedupe(self):
        # 4 个 cue，其中重复的两条被合并/丢弃
        self.assertEqual(len(self.cues), 2)

    def test_inline_tags_removed(self):
        self.assertNotIn("<c>", self.cues[0][2])
        self.assertNotIn("00:00:01.000", self.cues[0][2])

    def test_rolling_caption_merged(self):
        self.assertIn("聊视频总结工具", self.cues[0][2])
        self.assertTrue(self.cues[0][2].startswith("大家好"))

    def test_entities_decoded(self):
        self.assertIn("&", self.cues[1][2])
        self.assertNotIn("&amp;", self.cues[1][2])

    def test_late_timestamp(self):
        self.assertEqual(self.cues[1][0], 630000)

    def test_comment_block_ignored(self):
        for _, _, text in self.cues:
            self.assertNotIn("NOTE", text)

    def test_json3_parsing(self):
        cues = v.parse_json3(SAMPLE_JSON3)
        self.assertEqual(len(cues), 2)          # 第三段与第二段重复，被去掉
        self.assertEqual(cues[0][2], "第一句")
        self.assertEqual(cues[1][0], 1500)


class TestParagraphsAndBlocks(unittest.TestCase):
    def _cues(self, n=10, step=3000, chars=50):
        text = "这是一句测试用的中文内容。" * (chars // 12)
        return [(i * step, i * step + step - 100, text) for i in range(n)]

    def test_paragraphs_split_by_length(self):
        paras = v.to_paragraphs(self._cues(chars=200), max_len=240)
        self.assertGreater(len(paras), 1)
        for start, end, text in paras:
            self.assertLessEqual(start, end)

    def test_paragraphs_split_by_gap(self):
        cues = [(0, 1000, "a"), (60_000, 61_000, "b")]   # 中间空 59 秒
        paras = v.to_paragraphs(cues, gap_ms=2500)
        self.assertEqual(len(paras), 2)

    def test_blocks_respect_budget(self):
        paras = v.to_paragraphs(self._cues(chars=120), max_len=240)
        blocks = v.to_blocks(paras, max_chars=400)
        self.assertGreater(len(blocks), 1)
        for block in blocks[:-1]:                     # 最后一块允许超一点
            self.assertLessEqual(sum(len(p[2]) for p in block), 400)

    def test_blocks_keep_everything(self):
        paras = v.to_paragraphs(self._cues(), max_len=240)
        blocks = v.to_blocks(paras, max_chars=300)
        self.assertEqual(sum(len(b) for b in blocks), len(paras))


class TestScanStream(unittest.TestCase):
    def test_snapshot_parts(self):
        stream = "\n".join([
            json.dumps({"type": "message.part.updated",
                        "part": {"id": "p1", "type": "text", "text": "你好"}}),
            json.dumps({"type": "message.part.updated",
                        "part": {"id": "p1", "type": "text", "text": "你好世界"}}),
        ])
        text, err = v.scan_stream(stream)
        self.assertEqual(text, "你好世界")     # 同一个 id 取最后一次
        self.assertEqual(err, "")

    def test_delta_parts_concatenated(self):
        stream = "\n".join([
            json.dumps({"type": "text", "text": "第一段"}),
            json.dumps({"type": "text", "text": "第二段"}),
        ])
        text, _ = v.scan_stream(stream)
        self.assertEqual(text, "第一段第二段")

    def test_error_event(self):
        stream = json.dumps({
            "type": "error",
            "error": {"data": {"message": "This API key has expired."}},
        })
        text, err = v.scan_stream(stream)
        self.assertEqual(text, "")
        self.assertIn("expired", err)

    def test_garbage_lines_ignored(self):
        text, err = v.scan_stream("not json\n{broken\n")
        self.assertEqual((text, err), ("", ""))


class TestErrorExtraction(unittest.TestCase):
    def test_picks_error_line_from_help_dump(self):
        dump = """DESCRIPTION
  Run OpenCode with a message

ERROR
  Unrecognized flag: --standalone in command opencode run
"""
        self.assertEqual(v.extract_cli_error(dump),
                         "Unrecognized flag: --standalone in command opencode run")

    def test_single_line_error(self):
        self.assertEqual(v.extract_cli_error("ERROR: 401 unauthorized"), "401 unauthorized")

    def test_nothing(self):
        self.assertEqual(v.extract_cli_error("普通输出"), "")


class TestExplainFailure(unittest.TestCase):
    def test_expired_key_gets_remediation(self):
        msg = v.explain_failure("This API key has expired.")
        self.assertIn("opencode auth login", msg)
        self.assertIn("控制台", msg)

    def test_other_error_passthrough(self):
        msg = v.explain_failure("网络抖动")
        self.assertIn("网络抖动", msg)


class TestOpencodeCmd(unittest.TestCase):
    def _build(self, caps):
        with mock.patch.object(v, "OPENCODE_CAPS", caps):
            return v.build_opencode_cmd("/bin/opencode", "提示词", "m/x")

    def test_v1_flags(self):
        cmd = self._build({"format": True, "standalone": False, "auto": False})
        self.assertEqual(cmd[0], "/bin/opencode")
        self.assertEqual(cmd[1], "run")
        self.assertIn("--format", cmd)
        self.assertNotIn("--standalone", cmd)

    def test_v2_flags_after_run(self):
        cmd = self._build({"format": True, "standalone": True, "auto": True})
        self.assertEqual(cmd[:2], ["/bin/opencode", "run"])
        self.assertLess(cmd.index("run"), cmd.index("--standalone"))
        self.assertIn("--auto", cmd)

    def test_prompt_is_last(self):
        cmd = self._build({"format": True, "standalone": False, "auto": False})
        self.assertEqual(cmd[-1], "提示词")


class TestPrompts(unittest.TestCase):
    def test_final_prompt_has_title_and_spec(self):
        p = v.final_prompt("标题A", "[00:01] 内容")
        self.assertIn("标题A", p)
        self.assertIn("## 章节脉络", p)
        self.assertIn("[00:01] 内容", p)

    def test_reduce_prompt_lists_all_chunks(self):
        notes = ["笔记一", "笔记二", "笔记三"]
        p = v.reduce_prompt("标题B", notes)
        for i, n in enumerate(notes, 1):
            self.assertIn(f"第 {i} 段笔记", p)
            self.assertIn(n, p)

    def test_chunk_prompt_marks_position(self):
        p = v.chunk_prompt("标题C", 2, 5, 60_000, 120_000, "正文")
        self.assertIn("第 2/5 段", p)
        self.assertIn("01:00–02:00", p)


class TestLocalMedia(unittest.TestCase):
    def test_local_media_detects_file(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
            path = Path(fh.name)
        try:
            self.assertEqual(v.local_media(str(path)), path)
        finally:
            path.unlink(missing_ok=True)

    def test_local_media_rejects_url_and_missing(self):
        self.assertIsNone(v.local_media("https://example.com/a.mp4"))
        self.assertIsNone(v.local_media("/nonexistent/nope.mp4"))

    def test_media_meta(self):
        path = Path("/tmp/某课程 第01讲.mp4")
        meta = v.media_meta(path)
        self.assertEqual(meta["title"], "某课程 第01讲")
        self.assertTrue(meta["id"].startswith("local-"))
        self.assertEqual(meta["extractor_key"], "LocalFile")
        self.assertEqual(meta["id"], v.media_meta(path)["id"])   # 稳定

    def test_is_url(self):
        self.assertTrue(v.is_url("http://x"))
        self.assertTrue(v.is_url("https://x/y"))
        self.assertFalse(v.is_url("x.mp4"))
        self.assertFalse(v.is_url("/abs/x.mp4"))


class TestNormalizeTarget(unittest.TestCase):
    def test_bare_bv(self):
        self.assertEqual(v.normalize_target("BV1hVnh64Ere"),
                         "https://www.bilibili.com/video/BV1hVnh64Ere")

    def test_bare_av(self):
        self.assertEqual(v.normalize_target("av80433022"),
                         "https://www.bilibili.com/video/av80433022")

    def test_passthrough(self):
        url = "https://www.bilibili.com/video/BV1hVnh64Ere"
        self.assertEqual(v.normalize_target(url), url)
        self.assertEqual(v.normalize_target("./x.mp4"), "./x.mp4")

    def test_strips_whitespace(self):
        self.assertEqual(v.normalize_target("  BV1hVnh64Ere  "),
                         "https://www.bilibili.com/video/BV1hVnh64Ere")

    def test_not_a_bv(self):
        self.assertEqual(v.normalize_target("BV123"), "BV123")


class TestSubtitlePicking(unittest.TestCase):
    def test_prefers_manual_chinese(self):
        meta = {"subtitles": {"zh-Hans": [{"ext": "vtt", "url": "u1"}]},
                "automatic_captions": {"en": [{"ext": "vtt", "url": "u2"}]}}
        source, lang, entries = v.pick_subtitle(meta)
        self.assertEqual((source, lang), ("人工字幕", "zh-Hans"))
        self.assertEqual(entries[0]["url"], "u1")

    def test_falls_back_to_auto(self):
        meta = {"subtitles": {}, "automatic_captions": {"en-US": [{"ext": "vtt", "url": "u"}]}}
        source, lang, _ = v.pick_subtitle(meta)
        self.assertEqual(source, "自动字幕")
        self.assertEqual(lang, "en-US")

    def test_nothing_available(self):
        self.assertEqual(v.pick_subtitle({}), (None, None, []))


class TestCliSmoke(unittest.TestCase):
    def test_version_exits_zero(self):
        import subprocess
        r = subprocess.run([sys.executable, "vlearn.py", "--version"],
                           capture_output=True, text=True, cwd=Path(__file__).parent.parent)
        self.assertEqual(r.returncode, 0)
        self.assertIn("vlearn", r.stdout)

    def test_help_lists_asr(self):
        import subprocess
        r = subprocess.run([sys.executable, "vlearn.py", "--help"],
                           capture_output=True, text=True, cwd=Path(__file__).parent.parent)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--asr", r.stdout)

    def test_bad_target_rejected(self):
        import subprocess
        r = subprocess.run([sys.executable, "vlearn.py", "不是链接也不是文件"],
                           capture_output=True, text=True, cwd=Path(__file__).parent.parent)
        self.assertNotEqual(r.returncode, 0)


class TestWebUI(unittest.TestCase):
    """Web 层只是 CLI 的壳，这里只测不联网的部分。"""

    def setUp(self):
        import webui
        self.webui = webui

    def test_cmd_minimal(self):
        job = self.webui.Job("BV1xx411c7mD", {"asr": True})
        cmd = job.cmd()
        self.assertEqual(cmd[2], "BV1xx411c7mD")
        self.assertIn("-y", cmd)
        self.assertIn("--out", cmd)
        self.assertNotIn("--asr", cmd)          # 默认开，不用显式传
        self.assertNotIn("--cookie-browser", cmd)

    def test_cmd_passes_options(self):
        job = self.webui.Job("x", {
            "backend": "codex", "model": "m/x", "max_chars": 3000,
            "asr": False, "asr_model": "base", "mirror": True,
            "sessdata": "abc123", "cookie_browser": "safari",
        })
        cmd = " ".join(job.cmd())
        for expect in ("--backend codex", "--model m/x", "--max-chars 3000",
                       "--asr none", "--asr-model base", "--hf-mirror",
                       "--bili-sessdata abc123", "--cookie-browser safari"):
            self.assertIn(expect, cmd)

    def test_cookie_none_skipped(self):
        job = self.webui.Job("x", {"cookie_browser": "none"})
        self.assertNotIn("--cookie-browser", " ".join(job.cmd()))

    def test_result_finds_nested_notes(self):
        import tempfile
        job = self.webui.Job("x", {})
        job.out_dir = Path(tempfile.mkdtemp())
        self.assertIsNone(job.find_notes())
        nested = job.out_dir / "local-abc"
        nested.mkdir()
        (nested / "notes.md").write_text("# 标题\n", encoding="utf-8")
        (nested / "meta.json").write_text('{"chunks": 1}', encoding="utf-8")
        data = job.result()
        self.assertEqual(Path(data["out_dir"]), nested)
        self.assertIn("# 标题", data["notes"])
        self.assertEqual(data["meta"]["chunks"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
