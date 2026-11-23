import json
import os
import sys
import tempfile
import unittest
import warnings
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_common  # noqa: E402


class CommonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.kit = root / "kit"
        self.claude = root / "claude"
        self.brain_dir = root / "brain"
        self.second = root / "second"
        self.sb_home = root / "sbhome"
        for p in (self.kit, self.claude, self.brain_dir, self.second, self.sb_home):
            p.mkdir()
        env = {
            "RSI_KIT_HOME": str(self.kit),
            "RSI_CLAUDE_DIR": str(self.claude),
            "BRAIN_DIR": str(self.brain_dir),
            "SECOND_BRAIN": str(self.second),
            "SECOND_BRAIN_HOME": str(self.sb_home),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dirs_follow_env(self):
        self.assertEqual(rsi_common.kit_dir(), self.kit)
        self.assertEqual(rsi_common.claude_dir(), self.claude)

    def test_dirs_default_to_home(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("RSI_KIT_HOME")
            os.environ.pop("RSI_CLAUDE_DIR")
            self.assertEqual(rsi_common.kit_dir(), Path.home() / ".rsi-kit")
            self.assertEqual(rsi_common.claude_dir(), Path.home() / ".claude")

    def test_vault_precedence(self):  # BRAIN_DIR > SECOND_BRAIN > config.json
        (self.sb_home / "config.json").write_text(
            json.dumps({"vault": str(self.sb_home / "cfgvault")}), encoding="utf-8")
        self.assertEqual(rsi_common.vault_dir(), Path(self.brain_dir))
        os.environ.pop("BRAIN_DIR")
        self.assertEqual(rsi_common.vault_dir(), self.second)
        os.environ.pop("SECOND_BRAIN")
        self.assertEqual(rsi_common.vault_dir(), self.sb_home / "cfgvault")

    def test_vault_none_when_unset(self):
        os.environ.pop("BRAIN_DIR")
        os.environ.pop("SECOND_BRAIN")
        self.assertIsNone(rsi_common.vault_dir())

    def test_journal_exclude(self):
        self.assertEqual(rsi_common.journal_exclude(), [])
        cfg = self.sb_home / "config.json"
        cfg.write_text(json.dumps({"journal_exclude": ["a", "b"]}), encoding="utf-8")
        self.assertEqual(rsi_common.journal_exclude(), ["a", "b"])
        cfg.write_text("{broken", encoding="utf-8")
        self.assertEqual(rsi_common.journal_exclude(), [])

    def test_now_today(self):
        self.assertIsNotNone(rsi_common.now().tzinfo)
        self.assertIsInstance(rsi_common.now(), datetime)
        self.assertIsInstance(rsi_common.today(), date)

    def test_atomic_write_utf8_lf(self):
        p = self.kit / "sub" / "a.txt"
        rsi_common.atomic_write(p, "가\n")
        self.assertEqual(p.read_bytes(), b"\xea\xb0\x80\n")
        rsi_common.atomic_write(p, "a\r\nb")
        self.assertEqual(p.read_bytes(), b"a\nb")
        self.assertEqual([x.name for x in p.parent.iterdir()], ["a.txt"])

    def test_json_roundtrip_and_default(self):
        p = self.kit / "s.json"
        self.assertEqual(rsi_common.load_json(p, {"d": 1}), {"d": 1})
        rsi_common.save_json(p, {"k": "가"})
        self.assertEqual(rsi_common.load_json(p, None), {"k": "가"})
        self.assertIn("가".encode("utf-8"), p.read_bytes())
        p.write_text("{x", encoding="utf-8")
        self.assertEqual(rsi_common.load_json(p, []), [])

    def test_read_jsonl_skips_broken(self):
        p = self.kit / "r.jsonl"
        p.write_text('{"a": 1}\n{broken\n{"a": 3}\n', encoding="utf-8")
        self.assertEqual(rsi_common.read_jsonl(p), [{"a": 1}, {"a": 3}])
        self.assertEqual(rsi_common.read_jsonl(self.kit / "none.jsonl"), [])

    def test_append_jsonl(self):
        p = self.kit / "x" / "r.jsonl"
        rsi_common.append_jsonl(p, [{"a": "가"}])
        rsi_common.append_jsonl(p, [{"a": 2}, {"a": 3}])
        self.assertEqual(rsi_common.read_jsonl(p), [{"a": "가"}, {"a": 2}, {"a": 3}])
        self.assertNotIn(b"\r", p.read_bytes())

    def test_scrub_masks(self):
        self.assertIn("[비밀정보 제거됨]", rsi_common.scrub("password=hunter22"))
        self.assertEqual(rsi_common.scrub("feedback-ask-profiler"), "feedback-ask-profiler")

    def test_log_format(self):
        rsi_common.log("안녕")
        text = (self.claude / "hooks" / "rsi.log").read_text(encoding="utf-8")
        self.assertRegex(text, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[\d+\] 안녕\n$")

    def test_log_lone_surrogate_does_not_raise(self):
        rsi_common.log("깨진 \ud83d 글")
        text = (self.claude / "hooks" / "rsi.log").read_text(encoding="utf-8")
        self.assertIn("깨진 ? 글", text)

    def test_spawn_background_runs_script(self):
        import time
        out = self.kit / "out.txt"
        script = self.kit / "s.py"
        script.write_text(
            "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(sys.argv[2])",
            encoding="utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)  # 떼어 낸 프로세스라 wait 하지 않는다
            rsi_common.spawn_background(script, str(out), "hi")
        for _ in range(100):
            if out.exists() and out.read_text() == "hi":
                break
            time.sleep(0.1)
        self.assertEqual(out.read_text(), "hi")


if __name__ == "__main__":
    unittest.main()
