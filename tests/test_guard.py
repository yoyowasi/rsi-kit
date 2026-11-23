import codecs
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rsi_guard", REPO / "hooks" / "rsi-guard.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

GUARD_PATH = str(REPO / "hooks" / "rsi-guard.py")
DENY = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "RSI 킷 판정 기준·판정 코드는 사람이 에디터로만 고친다(평가 분리).",
    }
}


def run_guard(stdin_text, tmp):
    env = dict(os.environ, RSI_KIT_HOME=tmp, RSI_CLAUDE_DIR=tmp)
    return subprocess.run(
        [sys.executable, GUARD_PATH],
        input=stdin_text if isinstance(stdin_text, bytes) else stdin_text.encode("utf-8"),
        env=env,
        capture_output=True,
        timeout=30,
    )


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def run_guard(self, text):
        return run_guard(text, self.tmp)

    def test_path_forms_blocked(self):
        for p in [r"C:\Users\x\.rsi-kit\spec\rules.json", "/c/Users/x/.rsi-kit/spec/judge-prompt.md",
                  "$HOME/.rsi-kit/SPEC/thresholds.json", r"C:\Users\x\.claude\hooks\rsi_judge.py",
                  "C://Users//x//.rsi-kit//spec//rules.json"]:
            self.assertIsNotNone(guard.blocked("Write", {"file_path": p}), p)

    def test_draft_allowed(self):
        p = r"C:\Users\x\.rsi-kit\spec\rules.draft.json"
        self.assertIsNone(guard.blocked("Write", {"file_path": p}))
        self.assertIsNone(guard.blocked("Edit", {"file_path": "~/.rsi-kit/spec/rules.draft.json"}))

    def test_multiedit_notebook(self):
        self.assertIsNotNone(guard.blocked("MultiEdit", {"file_path": "/c/Users/x/.rsi-kit/spec/rules.json"}))
        self.assertIsNotNone(guard.blocked("NotebookEdit", {"notebook_path": r"C:\Users\x\.claude\hooks\rsi.ipynb"}))
        self.assertIsNone(guard.blocked("NotebookEdit", {"notebook_path": r"C:\dev\a.ipynb"}))

    def test_shell_blocked(self):
        self.assertIsNotNone(guard.blocked("Bash", {"command": "sed -i s/2/9/ ~/.rsi-kit/spec/thresholds.json"}))
        self.assertIsNotNone(guard.blocked("Bash", {"command": "cat ~/.rsi-kit/spec/rules.json"}))
        self.assertIsNotNone(guard.blocked("Bash", {"command": "cd ~ && echo x > .rsi-kit/spec/rules.json"}))
        self.assertIsNotNone(guard.blocked(
            "PowerShell", {"command": r"Set-Content $env:USERPROFILE\.rsi-kit\spec\rules.json 'x'"}))

    def test_cli_run_allowed(self):
        for c in ['py -3 "$HOME/.claude/hooks/rsi.py" status',
                  r'python3 C:\Users\x\.claude\hooks\rsi.py judge',
                  "py -3 ~/.claude/hooks/rsi.py collect "]:
            self.assertIsNone(guard.blocked("Bash", {"command": c}), c)

    def test_cli_chained_denied(self):
        c = 'py -3 "$HOME/.claude/hooks/rsi.py" status; rm ~/.rsi-kit/spec/rules.json'
        self.assertIsNotNone(guard.blocked("Bash", {"command": c}))
        c2 = 'py -3 "$HOME/.claude/hooks/rsi.py" status && cat ~/.rsi-kit/spec/rules.json'
        self.assertIsNotNone(guard.blocked("Bash", {"command": c2}))

    def test_other_paths_pass(self):
        self.assertIsNone(guard.blocked("Write", {"file_path": r"C:\dev\rsi-kit\hooks\rsi_judge.py"}))
        self.assertIsNone(guard.blocked("Bash", {"command": "ls C:/dev/rsi-kit"}))
        self.assertIsNone(guard.blocked("Read", {"file_path": "~/.rsi-kit/spec/rules.json"}))
        self.assertIsNone(guard.blocked("Edit", {}))

    def test_main_deny(self):
        r = self.run_guard(json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/c/u/.rsi-kit/spec/rules.json"}}))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout.decode("utf-8")), DENY)

    def test_main_allow(self):
        r = self.run_guard(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b"")

    def test_bad_input_fails_open(self):
        for s in ["not json", "", "[1,2]", '{"tool_name": 5, "tool_input": "x"}']:
            r = self.run_guard(s)
            self.assertEqual(r.returncode, 0, s)
            self.assertEqual(r.stdout, b"", s)

    def test_traversal_forms_blocked(self):
        for p in ["~/.rsi-kit/x/../spec/rules.json", "C:/Users/x/.rsi-kit/./spec/rules.json",
                  ".claude/hooks/../hooks/rsi_judge.py", "C:/Users/x/.rsi-kit/spec./rules.json",
                  r"C:\Users\x\.rsi-kit\spec \rules.json"]:
            self.assertIsNotNone(guard.blocked("Edit", {"file_path": p}), p)
        self.assertIsNone(guard.blocked("Edit", {"file_path": "C:/Users/x/.rsi-kit/./spec/rules.draft.json"}))
        self.assertIsNotNone(guard.blocked("Edit", {"file_path": "C:/u/.rsi-kit/spec/../spec/rules.json"}))

    def test_cli_abuse_denied(self):
        for c in ["python3 >~/.rsi-kit/spec/rules.json ~/.claude/hooks/rsi.py status",
                  "py -3 <(rm ~/.rsi-kit/spec/x) ~/.claude/hooks/rsi.py status",
                  "python3 ~/.rsi-kit/spec/evil.py ~/.claude/hooks/rsi.py status",
                  "python3 ~/.claude/hooks/rsi.py status\rRemove-Item ~/.rsi-kit/spec/rules.json",
                  "python3 ~/.claude/hooks/rsi.py status\nrm ~/.rsi-kit/spec/rules.json",
                  "python3 ~/.claude/hooks/rsi.py status > ~/.rsi-kit/spec/rules.json",
                  "python3 $(rm ~/.rsi-kit/spec/x)/.claude/hooks/rsi.py status"]:
            self.assertIsNotNone(guard.blocked("Bash", {"command": c}), c)
            self.assertIsNotNone(guard.blocked("PowerShell", {"command": c}), c)

    def test_cli_allowed_forms(self):
        for c in ["py -3 '~/.claude/hooks/rsi.py' init", "python ~/.claude/hooks/rsi.py collect",
                  r'python3 "C:\Users\a b\.claude\hooks\rsi.py" judge']:
            self.assertIsNone(guard.blocked("Bash", {"command": c}), c)

    def test_log_readable_scripts_protected(self):
        self.assertIsNone(guard.blocked("Bash", {"command": "tail ~/.claude/hooks/rsi.log"}))
        for c in ["cat ~/.claude/hooks/rsi_judge.py", "cat ~/.claude/hooks/rsi-guard.py",
                  "cat ~/.claude/hooks/rsi.py"]:
            self.assertIsNotNone(guard.blocked("Bash", {"command": c}), c)

    def test_glob_and_quote_forms_denied(self):
        h = "~/.claude/hooks/"
        cmds = ["rm " + h + "rsi*", "rm -f " + h + "rsi_*.py", "sed -i s/a/b/ " + h + "rsi*.py",
                "cp evil.py " + h + "rsi_judge.p?", "cp evil.py " + h + "rsi_judge.p[y]",
                "cp evil.py " + h + "rsi_judge.'py'", "cp evil.py " + h + 'rsi_judge".py"',
                "tail " + h + "rsi.log && rm " + h + "rsi.py", "cat " + h + "rsi.logx",
                "cat " + h + "rsi.log.py", "cat " + h + "rsi_judge.py"]
        for c in cmds:
            self.assertIsNotNone(guard.blocked("Bash", {"command": c}), c)
        ps = [r"Remove-Item $env:USERPROFILE\.claude\hooks\rsi*",
              r'Remove-Item "$env:USERPROFILE\.claude\hooks\rsi_*.py"',
              r"Get-ChildItem ~/.claude/hooks/rsi*.py | Remove-Item"]
        for c in ps:
            self.assertIsNotNone(guard.blocked("PowerShell", {"command": c}), c)
        self.assertIsNone(guard.blocked("Bash", {"command": "tail ~/.claude/hooks/rsi.log"}))

    def test_cli_spec_and_brace_denied(self):
        for c in ["python3 ~/.rsi-kit/spec/x.claude/hooks/rsi.py status",
                  "python3 {~/.rsi-kit/spec/evil,~}/.claude/hooks/rsi.py status",
                  "python3 ~/.claude/hooks/rsi*.py status"]:
            self.assertIsNotNone(guard.blocked("Bash", {"command": c}), c)

    def test_fail_open_log_scrubbed_and_isolated(self):
        r = self.run_guard(codecs.BOM_UTF8 + json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}).encode())
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        self.run_guard(b"SECRET-RAW-INPUT " + b"x" * 500)
        logf = Path(self.tmp) / "hooks" / "rsi.log"
        self.assertTrue(logf.exists())
        txt = logf.read_text(encoding="utf-8")
        self.assertNotIn("SECRET-RAW-INPUT", txt)
        self.assertLess(len(txt), 400)

    def test_wall_time(self):
        t = time.time()
        self.run_guard(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        print("guard wall time: %.3fs" % (time.time() - t))


if __name__ == "__main__":
    unittest.main()
