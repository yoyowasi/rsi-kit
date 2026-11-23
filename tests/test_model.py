import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_model  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = str(Path(self.tmp.name) / "argv.json")
        self._saved = dict(os.environ)
        self.addCleanup(self._restore)
        os.environ["RSI_CLAUDE_EXE"] = json.dumps([sys.executable, str(FIX / "fake_claude.py")])
        os.environ["FAKE_ARGV_OUT"] = self.out
        os.environ["RSI_KIT_HOME"] = self.tmp.name
        os.environ["RSI_CLAUDE_DIR"] = self.tmp.name

    def _restore(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_json_answer(self):
        os.environ["FAKE_MODE"] = "json"
        answer = rsi_model.ask_model("sys", "프롬프트")
        self.assertEqual(rsi_model.extract_json(answer)["items"], [])

    def test_auth_error(self):
        os.environ["FAKE_MODE"] = "auth"
        with self.assertRaises(rsi_model.AuthError):
            rsi_model.ask_model("sys", "p")

    def test_fail_is_transient(self):
        os.environ["FAKE_MODE"] = "fail"
        with self.assertRaises(rsi_model.TransientError) as cm:
            rsi_model.ask_model("sys", "p")
        self.assertNotIsInstance(cm.exception, rsi_model.ModelTimeout)

    def test_timeout(self):
        os.environ["FAKE_MODE"] = "sleep"
        start = time.time()
        with self.assertRaises(rsi_model.ModelTimeout) as cm:  # 판정은 시간 초과면 묶음을 나눈다
            rsi_model.ask_model("sys", "p", timeout=2)
        self.assertIsInstance(cm.exception, rsi_model.TransientError)
        self.assertLess(time.time() - start, 8)
        with open(self.out, encoding="utf-8") as f:
            pid = json.load(f)["pid"]
        deadline = time.time() + 3
        while pid_alive(pid) and time.time() < deadline:
            time.sleep(0.2)
        self.assertFalse(pid_alive(pid))

    def test_env_flags(self):
        os.environ["FAKE_MODE"] = "json"
        rsi_model.ask_model("나의 시스템", "p")
        with open(self.out, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("--no-session-persistence", data["argv"])
        self.assertIn("--safe-mode", data["argv"])
        self.assertIn("나의 시스템", data["argv"])
        self.assertEqual(data["RSI_KIT"], "1")

    def test_missing_exe(self):
        os.environ["RSI_CLAUDE_EXE"] = ""
        os.environ["PATH"] = self.tmp.name
        with self.assertRaises(rsi_model.TransientError):
            rsi_model.ask_model("sys", "p")

    def test_extract_json_noise(self):
        self.assertEqual(rsi_model.extract_json('설명 {"a": {"b": 1}} 끝'), {"a": {"b": 1}})
        with self.assertRaises(ValueError):
            rsi_model.extract_json("없음")


if __name__ == "__main__":
    unittest.main()
