import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "hooks"))

import rsi  # noqa: E402

CLAUDE_MD = """# 개인 규칙

# 배운 규칙 (dream)
<!-- brain:start -->
- [L01] 작업을 끝내면 보고에 바꾼 파일을 모두 적는다.
- [L06] 병합은 검사가 모두 통과한 뒤에만 한다.
<!-- brain:end -->
"""

DREAM = """---
type: dream
tags: []
created: 2026-10-27T08:00
---
# 2026-10-27 dream 규칙 제안

## 1. 추가 · 전역
- 규칙: 작업을 끝내면 보고에 바꾼 파일을 모두 적는다.
- 이유: 시험
- 근거: "파일 목록도 알려줘" (2026-10-26)

## 2. 추가 · 전역
- 규칙: 병합은 검사가 통과한 뒤에만 한다.
- 이유: 시험
- 근거: "체크 확인했어?" (2026-10-26)

## 처리 결과 (2026-10-27)

반영: 1, 2 · 거절: 없음 · 보류: 없음
- 1 → [L01] · 2 → [L06]
"""


def transcript(session, day, second_text):
    sid = session * 8
    rows = [
        {"cwd": "C:\\dev\\Study", "sessionId": sid, "isSidechain": False, "type": "user",
         "timestamp": "%sT01:00:00.000Z" % day, "message": {"role": "user", "content": "PR 병합해줘"}},
        {"cwd": "C:\\dev\\Study", "sessionId": sid, "isSidechain": False, "type": "assistant",
         "timestamp": "%sT01:00:05.000Z" % day,
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "병합합니다."},
             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "gh pr merge 12"}}]}},
        {"cwd": "C:\\dev\\Study", "sessionId": sid, "isSidechain": False, "type": "user",
         "timestamp": "%sT01:00:30.000Z" % day, "message": {"role": "user", "content": second_text}},
    ]
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


class CliBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.kit = root / "kit"
        self.claude = root / "claude"
        self.vault = root / "vault"
        self.home = root / "sbhome"
        for d in (self.kit, self.claude, self.home, self.vault / "00_Inbox", self.vault / "90_Archive"):
            d.mkdir(parents=True)
        (self.claude / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
        proj = self.claude / "projects" / "C--dev-Study"
        proj.mkdir(parents=True)
        (proj / "aaaaaaaa.jsonl").write_text(transcript("a", "2026-11-20", "체크 확인 안 했네"), encoding="utf-8")
        (proj / "bbbbbbbb.jsonl").write_text(transcript("b", "2026-11-21", "체크 안 보고 병합했네"), encoding="utf-8")
        (self.vault / "90_Archive" / "2026-10-27 dream 규칙 제안.md").write_text(DREAM, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def put_spec(self, drop=()):
        spec = self.kit / "spec"
        spec.mkdir(parents=True, exist_ok=True)
        data = json.loads((FIX / "rules.json").read_text(encoding="utf-8"))
        for rid in drop:
            del data["rules"][rid]
        (spec / "rules.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def env(self, **extra):
        env = dict(os.environ)
        env.pop("RSI_KIT", None)
        env.pop("SECOND_BRAIN", None)
        env.update({
            "RSI_KIT_HOME": str(self.kit), "RSI_CLAUDE_DIR": str(self.claude),
            "BRAIN_DIR": str(self.vault), "SECOND_BRAIN_HOME": str(self.home),
            "RSI_CLAUDE_EXE": json.dumps([sys.executable, str(FIX / "fake_claude.py")]),
            "FAKE_MODE": "match", "FAKE_RULE": "병합은", "FAKE_MATCH": "체크",
            "PYTHONIOENCODING": "utf-8", "RSI_NO_SPAWN": "1",
        })
        env.update(extra)
        return env

    def run_cmd(self, cmd, **extra):
        return subprocess.run([sys.executable, str(REPO / "hooks" / "rsi.py"), cmd], env=self.env(**extra),
                              input="{}", capture_output=True, text=True, encoding="utf-8", timeout=120)

    def inbox_files(self):
        return sorted((self.vault / "00_Inbox").glob("*dream 규칙 효과 판정*.md"))

    def excerpt_count(self):
        n = 0
        for f in (self.kit / "excerpts").glob("*.jsonl"):
            n += len([l for l in f.read_text(encoding="utf-8").split("\n") if l.strip()])
        return n

    def log_text(self):
        p = self.claude / "hooks" / "rsi.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def state(self):
        return json.loads((self.kit / "state.json").read_text(encoding="utf-8"))


class CliTests(CliBase):
    def test_init_writes_draft_only(self):
        r = self.run_cmd("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.kit / "spec" / "rules.draft.json").exists())
        self.assertFalse((self.kit / "spec" / "rules.json").exists())
        self.assertIn("rules.draft.json", r.stdout)

    def test_judge_without_spec(self):
        r = self.run_cmd("judge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("판정 기준 없음", r.stdout)
        self.assertEqual(self.inbox_files(), [])

    def test_end_to_end(self):
        self.put_spec()
        self.assertEqual(self.run_cmd("collect").returncode, 0)
        r = self.run_cmd("judge")
        self.assertEqual(r.returncode, 0, r.stderr + self.log_text())
        files = self.inbox_files()
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("## hook 후보", text)
        self.assertIn("[L06]", text.split("## hook 후보")[1].split("##")[0])
        self.assertNotIn("빼기", text)
        self.assertEqual(self.state()["last_judge"], rsi.today().isoformat())

    def test_lock_fresh_blocks_worker(self):
        self.put_spec()
        lock = self.kit / "worker.lock"
        lock.write_text("x", encoding="utf-8")
        r = self.run_cmd("worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(lock.exists())
        self.assertFalse((self.kit / "state.json").exists())
        self.assertIn("잠금", self.log_text())

    def test_lock_stale_is_replaced(self):
        self.put_spec()
        lock = self.kit / "worker.lock"
        lock.write_text("x", encoding="utf-8")
        old = time.time() - 3 * 3600
        os.utime(str(lock), (old, old))
        r = self.run_cmd("worker")
        self.assertEqual(r.returncode, 0, r.stderr + self.log_text())
        self.assertFalse(lock.exists())
        self.assertEqual(self.state()["last_collect"], rsi.today().isoformat())
        self.assertEqual(len(self.inbox_files()), 1)

    def test_manual_commands_respect_lock(self):
        self.put_spec()
        lock = self.kit / "worker.lock"
        lock.write_text("x", encoding="utf-8")
        for cmd in ("init", "collect", "judge"):
            r = self.run_cmd(cmd)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("다른 작업자가 실행 중", r.stdout)
        self.assertTrue(lock.exists())
        self.assertFalse((self.kit / "state.json").exists())
        self.assertFalse((self.kit / "excerpts").exists())
        self.assertFalse((self.kit / "spec" / "rules.draft.json").exists())
        self.assertEqual(self.inbox_files(), [])

    def test_status_lists_rules_missing_from_spec(self):
        self.put_spec(drop=("L01",))
        self.assertEqual(self.run_cmd("collect").returncode, 0)
        s = self.run_cmd("status")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("L01: 대장에 없음", s.stdout)
        self.assertNotIn("L06: 대장에 없음", s.stdout)

    def test_worker_logs_broken_spec_once(self):
        spec = self.kit / "spec"
        spec.mkdir(parents=True)
        (spec / "rules.json").write_text("{깨진", encoding="utf-8")
        r = self.run_cmd("worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([l for l in self.log_text().splitlines() if "판정 기준 오류" in l]), 1)
        self.assertEqual(self.state()["last_collect"], rsi.today().isoformat())

    def test_worker_missing_spec_is_quiet(self):
        r = self.run_cmd("worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("판정 기준 오류", self.log_text())

    def test_rerun_same_day_no_duplicates(self):
        self.put_spec()
        self.run_cmd("collect")
        first = self.excerpt_count()
        self.assertGreater(first, 0)
        self.run_cmd("collect")
        self.assertEqual(self.excerpt_count(), first)
        self.run_cmd("judge")
        self.run_cmd("judge")
        self.assertEqual(len(self.inbox_files()), 1)

    def test_status_shows_auth(self):
        self.put_spec()
        self.run_cmd("collect")
        r = self.run_cmd("judge", FAKE_MODE="auth")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = self.run_cmd("status")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("claude auth login", s.stdout)

    def test_recursion_guard(self):
        for cmd in ("init", "collect", "judge", "worker"):
            r = self.run_cmd(cmd, RSI_KIT="1")
            self.assertEqual(r.returncode, 0)
        self.assertEqual(list(self.kit.iterdir()), [])

    def test_hook_returns_zero_without_spawn(self):
        r = self.run_cmd("hook-session-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.kit.iterdir()), [])


class JudgeAttemptTests(CliBase):
    """모델을 부른 뒤 난 AuthError 가 아닌 실패도 판정 시도일(last_judge)을 남긴다(세션마다 다시 돌지 않게)."""

    def setUp(self):
        super().setUp()
        env = {k: v for k, v in self.env().items() if k.startswith(("RSI_", "BRAIN_", "SECOND_", "FAKE_"))}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("RSI_KIT", "SECOND_BRAIN"):
            os.environ.pop(k, None)
        self.put_spec()

    def test_failure_after_model_calls_records_attempt(self):
        out = io.StringIO()
        with mock.patch("rsi.judge_pending", return_value=(0, 0)) as jp, \
                mock.patch("rsi_inbox.publish", side_effect=OSError("볼트 쓰기 실패")), \
                contextlib.redirect_stdout(out):
            self.assertEqual(rsi.main(["judge"]), 1)
        self.assertTrue(jp.called)
        self.assertEqual(self.state()["last_judge"], rsi.today().isoformat())
        self.assertIn("볼트 쓰기 실패", self.log_text())
        self.assertFalse((self.kit / "worker.lock").exists())

    def test_auth_error_does_not_record_attempt(self):
        with mock.patch("rsi.judge_pending", side_effect=rsi.AuthError("로그인")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rsi.main(["judge"]), 0)
        self.assertIsNone(self.state()["last_judge"])
        self.assertTrue(self.state()["auth"])

    def test_failure_before_model_calls_does_not_record(self):
        with mock.patch("rsi.load_excerpts", side_effect=OSError("읽기 실패")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rsi.main(["judge"]), 1)
        self.assertIsNone(self.state()["last_judge"])


class DueTests(unittest.TestCase):
    def test_due(self):
        d = date(2026, 11, 23)
        self.assertEqual(rsi._due({"last_collect": None, "last_judge": None}, d, True, 7), (True, True))
        self.assertEqual(rsi._due({"last_collect": None, "last_judge": None}, d, False, 7), (True, False))
        st = {"last_collect": "2026-11-23", "last_judge": "2026-11-17"}
        self.assertEqual(rsi._due(st, d, True, 7), (False, False))
        st = {"last_collect": "2026-11-22", "last_judge": "2026-11-16"}
        self.assertEqual(rsi._due(st, d, True, 7), (True, True))


if __name__ == "__main__":
    unittest.main()
