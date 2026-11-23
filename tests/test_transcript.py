import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_judge  # noqa: E402
import rsi_transcript  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def jl(*objs):
    return "".join((o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)) + "\n" for o in objs)


def user(ts, content, cwd="C:\\dev\\Study", **kw):
    d = {"type": "user", "timestamp": ts, "cwd": cwd, "message": {"role": "user", "content": content}}
    d.update(kw)
    return d


def asst(ts, blocks, cwd="C:\\dev\\Study"):
    return {"type": "assistant", "timestamp": ts, "cwd": cwd, "message": {"role": "assistant", "content": blocks}}


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.kit, self.claude, self.sb = root / "kit", root / "claude", root / "sb"
        for p in (self.kit, self.claude, self.sb):
            p.mkdir()
        patcher = mock.patch.dict(os.environ, {
            "RSI_KIT_HOME": str(self.kit), "RSI_CLAUDE_DIR": str(self.claude),
            "SECOND_BRAIN_HOME": str(self.sb)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def put_session(self, project, name, text):
        path = self.claude / "projects" / project / (name + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_basic(self):
        ex, cwd = rsi_transcript.parse_transcript(FIX / "session_a.jsonl")
        self.assertEqual([e["u"][:4] for e in ex], ["첫 질문", "아니 그", ""])
        self.assertEqual(ex[1]["tools"], [{"name": "Bash", "arg": "gh pr merge 12"}])
        self.assertTrue(ex[1]["a_tail"].endswith("머지했어요."))
        self.assertEqual(cwd, "C:\\dev\\Study")
        self.assertEqual(ex[0]["project"], "Study")
        self.assertEqual(ex[0]["session"], "session_a")
        self.assertNotIn("system-reminder", ex[0]["u"])
        self.assertEqual(ex[0]["tools"], [])
        self.assertRegex(ex[2]["id"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z:end:[0-9a-f]{8}$")
        self.assertEqual(ex[2]["tools"], [{"name": "Edit", "arg": "C:/dev/Study/a.py"},
                                          {"name": "Read", "arg": ""}])
        expected = datetime.fromisoformat("2026-11-23T04:24:41+00:00").astimezone().isoformat()
        self.assertEqual(ex[1]["ts"], expected)
        # id 는 세션과 시간대에 기대지 않는다: 원래 UTC 시각 + 사람 말 해시
        self.assertEqual(ex[1]["id"], "2026-11-23T04:24:41Z:" + sha1(ex[1]["u"]))

    def test_ids_ignore_session_so_copies_dedupe(self):
        # 재개·포크한 세션은 같은 사람 말(같은 시각·글)을 새 세션 파일에 다시 담는다. 발췌는 하나만 남는다.
        same = user("2026-11-20T01:00:30.123Z", "체크 안 보고 병합했네")
        self.put_session("C--dev-Study", "aaaaaaaa", jl(user("2026-11-20T01:00:00Z", "PR 병합해줘"), same))
        self.put_session("C--dev-Study", "bbbbbbbb", jl(user("2026-11-20T01:00:00Z", "PR 병합해줘"), same,
                                                         user("2026-11-21T02:00:00Z", "새 말")))
        rows = rsi_transcript.collect(0)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rsi_transcript.store_excerpts(rows), 3)
        self.assertEqual(rsi_transcript.store_excerpts(rows), 0)
        stored = rsi_transcript.load_excerpts(date(2000, 1, 1))
        dup = [e for e in stored if e["u"] == "체크 안 보고 병합했네"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["session"], "aaaaaaaa")  # 먼저 본 것을 남긴다
        self.assertEqual(dup[0]["id"], "2026-11-20T01:00:30.123000Z:" + sha1("체크 안 보고 병합했네"))
        # 모델이 그 말을 교정으로 보면: 세션 1개 → 관찰(재발 아님)
        js = [{"excerpt": e["id"], "rule": "L06", "opportunity": "yes", "correction": "yes", "quote": "",
               "judge_hash": "h"} for e in stored if e["u"] == "체크 안 보고 병합했네"]
        rule = {"type": "restraint", "no_exceptions": True, "enforce": "pretooluse",
                "opportunity": {"kind": "llm", "when": "병합"}, "correction": "c", "enforced_by": None}
        th = {"recur_sessions": 2, "kept_opportunity_sessions": 5}
        v = rsi_judge.rule_verdict("L06", rule, {"text_since": "2026-11-01"}, js, stored, th)
        self.assertEqual((v["correction_sessions"], v["verdict"]), (1, "관찰"))

    def test_injected_blocks_stripped(self):
        text = jl(
            user("2026-11-23T04:00:00Z", "<side-sessions-event>세션 a 끝남</side-sessions-event>"),
            user("2026-11-23T04:00:01Z", "<task-notification>\n<task-id>1</task-id>\n</task-notification>"),
            user("2026-11-23T04:00:02Z", '<cross-session-message from="s2" kind="peer">안녕</cross-session-message>'),
            user("2026-11-23T04:00:03Z", [{"type": "text", "text":
                                           "<artifact-view-context>\n보는 중\n</artifact-view-context>진짜 말"}]),
            user("2026-11-23T04:00:04Z", "<ci-monitor-event>ci 실패</ci-monitor-event>\n끝 말"),
        )
        ex, _ = rsi_transcript.parse_transcript(self.put_session("p", "s8", text))
        self.assertEqual([e["u"] for e in ex], ["진짜 말", "끝 말"])

    def test_collect_skips_scratchpad_cwd(self):
        pad = "C:\\Users\\u\\AppData\\Local\\Temp\\claude\\x\\scratchpad\\work"
        self.put_session("C--dev-Study", "s1", jl(user("2026-11-23T04:00:00Z", "질문", cwd=pad)))
        self.assertEqual(rsi_transcript.collect(0), [])
        self.put_session("C--dev-Study", "s2", jl(user("2026-11-23T04:00:00Z", "질문 둘")))
        self.assertEqual([r["u"] for r in rsi_transcript.collect(0)], ["질문 둘"])

    def test_non_human_skipped(self):
        # 사이드체인 도구(rm -rf)·isMeta·origin 알림·tool_result 줄은 발췌가 아니고 tools 에도 없다.
        ex, _ = rsi_transcript.parse_transcript(FIX / "session_a.jsonl")
        self.assertEqual(len(ex), 3)
        joined = json.dumps(ex, ensure_ascii=False)
        for bad in ("메타 줄", "task-notification", "rm -rf"):
            self.assertNotIn(bad, joined)

    def test_broken_lines_skipped(self):
        text = jl(
            "{깨진",
            user("2026-11-23T04:00:00Z", "앞 질문"),
            user("2026-11-23T04:00:01Z", [{"type": "tool_result", "content": "y" * 200000}]),
            '{"type":"user","timestamp":"2026-11-23T04:00:02Z","message":{"content":"\\ud83d 서로게이트"}}',
            "[1, 2]",
            user("2026-11-23T04:00:03Z", "뒤 질문"),
        )
        path = self.put_session("p", "s1", text)
        ex, _ = rsi_transcript.parse_transcript(path)
        self.assertEqual([e["u"][-2:] for e in ex], ["질문", "이트", "질문"])
        json.dumps(ex, ensure_ascii=False).encode("utf-8")  # 서로게이트가 남았으면 여기서 예외

    def test_unicode_line_separators_kept(self):
        sep = chr(0x2028) + chr(0x85) + chr(0x0b)
        text = jl(user("2026-11-23T04:00:00Z", "앞" + sep + "뒤"))
        path = self.put_session("p", "s5", text)
        ex, _ = rsi_transcript.parse_transcript(path)
        self.assertEqual([e["u"] for e in ex], ["앞" + sep + "뒤"])
        self.assertEqual(rsi_transcript.store_excerpts(ex), 1)
        self.assertEqual(rsi_transcript.store_excerpts(ex), 0)

    def test_surrogate_in_tool_name_and_cwd(self):
        bs = chr(92)  # JSON 이스케이프용 백슬래시
        lone = bs + "ud83d"
        text = ('{"type":"assistant","timestamp":"2026-11-23T04:00:00Z","cwd":"C:' + bs + bs + 'd' + bs + bs + lone
                + '","message":{"content":[{"type":"tool_use","name":"X' + lone + '","input":{}}]}}\n')
        ex, cwd = rsi_transcript.parse_transcript(self.put_session("p", "s6", text))
        json.dumps(ex, ensure_ascii=False).encode("utf-8")
        cwd.encode("utf-8")
        self.assertEqual(rsi_transcript.store_excerpts(ex), 1)

    def test_deeply_nested_line_skipped(self):
        deep = "[" * 100000 + "]" * 100000
        text = deep + "\n" + jl(user("2026-11-23T04:00:00Z", "질문"))
        ex, _ = rsi_transcript.parse_transcript(self.put_session("p", "s7", text))
        self.assertEqual([e["u"] for e in ex], ["질문"])
        self.assertEqual(len(rsi_transcript.collect(0)), 1)

    def test_limits_and_project_fallback(self):
        long_u = "가" * 3000
        text = jl({"type": "assistant", "timestamp": "2026-11-23T04:00:00Z",
                   "message": {"content": [{"type": "text", "text": "나" * 500}]}},
                  {"type": "user", "timestamp": "2026-11-23T04:00:01Z", "message": {"content": long_u}},
                  asst("2026-11-23T04:00:02Z", [{"type": "tool_use", "name": "PowerShell",
                                                  "input": {"command": "x" * 500}}]))
        ex, cwd = rsi_transcript.parse_transcript(self.put_session("p", "s2", text))
        self.assertEqual(len(ex[0]["u"]), 1500)
        self.assertEqual(len(ex[0]["a_tail"]), 300)
        self.assertEqual(len(ex[1]["tools"][0]["arg"]), 200)

    def test_project_name_without_cwd(self):
        text = jl({"type": "user", "timestamp": "2026-11-23T04:00:01Z", "message": {"content": "질문"}})
        ex, cwd = rsi_transcript.parse_transcript(self.put_session("p", "s3", text))
        self.assertIsNone(cwd)
        self.assertEqual(ex[0]["project"], "폴더 없음")

    def test_collect_skips_excluded(self):
        excluded = self.sb / "excluded"
        (self.sb / "config.json").write_text(json.dumps({"journal_exclude": [str(excluded)]}), encoding="utf-8")
        ok = jl(user("2026-11-23T04:00:00Z", "정상 질문"))
        self.put_session("scratchpad-x", "s1", ok)
        self.put_session("p1", "s2", jl(user("2026-11-23T04:00:00Z", "질문", cwd=str(excluded / "sub"))))
        self.put_session("p2", "s3", jl(user("2026-11-23T04:00:00Z",
                                             "This is an automated run of a scheduled task")))
        self.assertEqual(rsi_transcript.collect(0), [])
        self.put_session("p3", "s4", ok)
        rows = rsi_transcript.collect(0)
        self.assertEqual([r["u"] for r in rows], ["정상 질문"])

    def test_collect_respects_since(self):
        self.put_session("p", "s1", jl(user("2026-11-23T04:00:00Z", "질문")))
        self.assertEqual(rsi_transcript.collect(10 ** 11), [])

    def test_store_dedupes(self):
        ex, _ = rsi_transcript.parse_transcript(FIX / "session_a.jsonl")
        self.assertEqual(rsi_transcript.store_excerpts(ex), 3)
        self.assertEqual(rsi_transcript.store_excerpts(ex), 0)
        files = list((self.kit / "excerpts").glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stem, ex[0]["ts"][:7])

    def test_store_keeps_existing_lines(self):
        ex, _ = rsi_transcript.parse_transcript(FIX / "session_a.jsonl")
        rsi_transcript.store_excerpts(ex[:1])
        path = next((self.kit / "excerpts").glob("*.jsonl"))
        before = path.read_bytes()
        self.assertEqual(rsi_transcript.store_excerpts(ex), 2)
        self.assertTrue(path.read_bytes().startswith(before))

    def test_scrubbed_before_store(self):
        self.put_session("p", "s1", jl(user("2026-11-23T04:00:00Z", "이거 token=abcd1234 로 해줘")))
        rows = rsi_transcript.collect(0)
        rsi_transcript.store_excerpts(rows)
        stored = "".join(p.read_text(encoding="utf-8") for p in (self.kit / "excerpts").glob("*.jsonl"))
        self.assertNotIn("abcd1234", stored)
        self.assertIn("비밀정보 제거됨", stored)

    def test_load_excerpts(self):
        def row(ts):
            return {"id": "x:" + ts, "ts": ts, "project": "p", "session": "s", "u": "u", "a_tail": "", "tools": []}
        rsi_transcript.store_excerpts([row("2026-11-30T10:00:00+09:00"), row("2026-10-31T23:00:00+09:00"),
                                       row("2026-12-02T09:00:00+09:00"), row("2026-09-01T09:00:00+09:00")])
        got = rsi_transcript.load_excerpts(date(2026, 10, 31))
        self.assertEqual([r["ts"] for r in got],
                         ["2026-10-31T23:00:00+09:00", "2026-11-30T10:00:00+09:00", "2026-12-02T09:00:00+09:00"])
        self.assertEqual(len(rsi_transcript.load_excerpts(date(2026, 11, 1))), 2)


if __name__ == "__main__":
    unittest.main()
