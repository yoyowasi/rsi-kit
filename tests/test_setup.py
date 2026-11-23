import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import setup  # noqa: E402

BRAIN_SS = {"hooks": [{"type": "command", "command": 'python "$HOME/.claude/hooks/brain-dream.py"', "timeout": 10}]}
OTHER_PRE = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo other", "timeout": 5}]}


def seed(claude, extra=None):
    claude.mkdir(parents=True, exist_ok=True)
    data = {"theme": "dark",
            "hooks": {"SessionStart": [BRAIN_SS], "PreToolUse": [OTHER_PRE]}}
    if extra:
        data.update(extra)
    path = claude / "settings.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load(claude):
    return json.loads((claude / "settings.json").read_text(encoding="utf-8"))


def ours(groups, needle):
    return [g for g in groups if any(needle in h.get("command", "") for h in g["hooks"])]


def baks(claude):
    return sorted(claude.glob("settings.json.bak-*"))


class SetupTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.claude = Path(self._t.name) / ".claude"

    def test_install_idempotent(self):
        seed(self.claude)
        setup.install(self.claude, "python")
        s = load(self.claude)
        self.assertEqual(len(ours(s["hooks"]["SessionStart"], 'rsi.py" hook-session-start')), 1)
        self.assertEqual(len(ours(s["hooks"]["PreToolUse"], 'rsi-guard.py"')), 1)
        self.assertEqual(len(baks(self.claude)), 1)
        before = (self.claude / "settings.json").read_bytes()
        setup.install(self.claude, "python")
        self.assertEqual((self.claude / "settings.json").read_bytes(), before)
        self.assertEqual(len(baks(self.claude)), 1)

    def test_exact_group_shape_and_files(self):
        setup.install(self.claude, "py -3")
        s = load(self.claude)
        self.assertEqual(s["hooks"]["SessionStart"], [{"hooks": [{
            "type": "command", "command": 'py -3 "$HOME/.claude/hooks/rsi.py" hook-session-start', "timeout": 10}]}])
        self.assertEqual(s["hooks"]["PreToolUse"], [{
            "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
            "hooks": [{"type": "command", "command": 'py -3 "$HOME/.claude/hooks/rsi-guard.py" || true',
                       "timeout": 10}]}])
        installed = sorted(p.name for p in (self.claude / "hooks").iterdir())
        self.assertEqual(installed, sorted(p.name for p in (REPO / "hooks").glob("rsi*.py")))

    def test_keeps_other_hooks(self):
        seed(self.claude)
        setup.install(self.claude, "python")
        s = load(self.claude)
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["hooks"]["SessionStart"][0], BRAIN_SS)
        self.assertEqual(s["hooks"]["PreToolUse"][0], OTHER_PRE)

    def test_remove_only_rsi(self):
        seed(self.claude)
        setup.install(self.claude, "python")
        setup.remove(self.claude)
        s = load(self.claude)
        self.assertEqual(s["hooks"], {"SessionStart": [BRAIN_SS], "PreToolUse": [OTHER_PRE]})
        self.assertTrue((self.claude / "hooks" / "rsi.py").exists())
        self.assertTrue((self.claude / "hooks" / "rsi-guard.py").exists())

    def test_remove_cleans_empty_event(self):
        setup.install(self.claude, "python")
        setup.remove(self.claude)
        self.assertEqual(load(self.claude)["hooks"], {})

    def test_bad_hooks_shape(self):
        self.claude.mkdir(parents=True)
        path = self.claude / "settings.json"
        path.write_bytes(b'{"hooks": {"SessionStart": {}}}')
        for fn in (lambda: setup.install(self.claude, "python"), lambda: setup.remove(self.claude)):
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                fn()
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(path.read_bytes(), b'{"hooks": {"SessionStart": {}}}')
        self.assertEqual(baks(self.claude), [])
        self.assertFalse((self.claude / "hooks").exists())

    def test_invalid_json(self):
        self.claude.mkdir(parents=True)
        path = self.claude / "settings.json"
        path.write_bytes(b"{nope")
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            setup.install(self.claude, "python")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(path.read_bytes(), b"{nope")

    def test_python_change_updates_in_place(self):
        seed(self.claude)
        setup.install(self.claude, "python")
        setup.install(self.claude, "py -3")
        s = load(self.claude)
        ss = s["hooks"]["SessionStart"]
        self.assertEqual(len(ss), 2)
        self.assertEqual(ss[0], BRAIN_SS)
        self.assertTrue(ss[1]["hooks"][0]["command"].startswith("py -3 "))
        pre = s["hooks"]["PreToolUse"]
        self.assertEqual(len(pre), 2)
        self.assertEqual(pre[0], OTHER_PRE)
        self.assertTrue(pre[1]["hooks"][0]["command"].startswith("py -3 "))
        # in-place: ours placed before a later group stays before it
        s["hooks"]["SessionStart"].append({"hooks": [{"type": "command", "command": "echo z"}]})
        (self.claude / "settings.json").write_text(json.dumps(s), encoding="utf-8")
        setup.install(self.claude, "python3")
        ss = load(self.claude)["hooks"]["SessionStart"]
        self.assertEqual(len(ss), 3)
        self.assertTrue(ss[1]["hooks"][0]["command"].startswith("python3 "))
        self.assertEqual(ss[2]["hooks"][0]["command"], "echo z")

    def test_guard_command_never_blocks_when_file_missing(self):
        # 파일이 없으면 python 이 exit 2 → PreToolUse 가 모든 도구를 막는다. || true 로 0 을 돌려준다.
        setup.install(self.claude, "python")
        cmd = load(self.claude)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(cmd.endswith('rsi-guard.py" || true'))
        ss = load(self.claude)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertNotIn("|| true", ss)

    @unittest.skipUnless(shutil.which("sh"), "POSIX sh only")
    def test_guard_command_exit_codes_in_shell(self):
        home = Path(self._t.name) / "home"
        claude = home / ".claude"
        setup.install(claude, '"%s"' % sys.executable.replace("\\", "/"))
        cmd = load(claude)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), RSI_CLAUDE_DIR=str(claude),
                   RSI_KIT_HOME=str(home / ".rsi-kit"))
        deny = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "~/.rsi-kit/spec/rules.json"}})
        r = subprocess.run([shutil.which("sh"), "-c", cmd], input=deny, env=env, capture_output=True, text=True,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"permissionDecision": "deny"', r.stdout)
        (claude / "hooks" / "rsi-guard.py").unlink()
        r = subprocess.run([shutil.which("sh"), "-c", cmd], input=deny, env=env, capture_output=True, text=True,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_old_guard_registration_updated_in_place(self):
        self.claude.mkdir(parents=True)
        old = {"matcher": setup.MATCHER,
               "hooks": [{"type": "command", "command": 'python "$HOME/.claude/hooks/rsi-guard.py"', "timeout": 10}]}
        later = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo later"}]}
        (self.claude / "settings.json").write_text(
            json.dumps({"hooks": {"PreToolUse": [OTHER_PRE, old, later]}}), encoding="utf-8")
        setup.install(self.claude, "python")
        pre = load(self.claude)["hooks"]["PreToolUse"]
        self.assertEqual(len(pre), 3)
        self.assertEqual(pre[0], OTHER_PRE)
        self.assertEqual(pre[1]["hooks"][0]["command"], 'python "$HOME/.claude/hooks/rsi-guard.py" || true')
        self.assertEqual(pre[2], later)
        before = (self.claude / "settings.json").read_bytes()
        setup.install(self.claude, "python")
        self.assertEqual((self.claude / "settings.json").read_bytes(), before)
        setup.remove(self.claude)
        self.assertEqual(load(self.claude)["hooks"]["PreToolUse"], [OTHER_PRE, later])

    def test_missing_settings_created(self):
        setup.install(self.claude, "python")
        s = load(self.claude)
        self.assertEqual(set(s), {"hooks"})
        self.assertEqual(set(s["hooks"]), {"SessionStart", "PreToolUse"})
        self.assertEqual(baks(self.claude), [])
        raw = (self.claude / "settings.json").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.endswith(b"\n"))

    def test_no_rsi_kit_home_created(self):
        home = Path(self._t.name) / "home"
        home.mkdir()
        old = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "RSI_KIT_HOME")}
        os.environ.update(HOME=str(home), USERPROFILE=str(home))
        os.environ.pop("RSI_KIT_HOME", None)
        try:
            setup.install(self.claude, "python")
            setup.remove(self.claude)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertFalse((home / ".rsi-kit").exists())

    def test_no_trailing_newline_is_noop(self):
        seed(self.claude)
        path = self.claude / "settings.json"
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        setup.remove(self.claude)  # nothing of ours
        self.assertFalse(path.read_bytes().endswith(b"\n"))
        self.assertEqual(baks(self.claude), [])
        setup.install(self.claude, "python")
        self.assertEqual(len(baks(self.claude)), 1)
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        before = path.read_bytes()
        setup.install(self.claude, "python")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(baks(self.claude)), 1)

    def test_mixed_group_keeps_other_hook_and_matcher(self):
        self.claude.mkdir(parents=True)
        mixed = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo keep-me"},
                                              {"type": "command", "command": 'python "$HOME/.claude/hooks/rsi-guard.py"'}]}
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [mixed]}}), encoding="utf-8")
        setup.install(self.claude, "python")
        pre = load(self.claude)["hooks"]["PreToolUse"]
        self.assertEqual(len(ours(pre, 'rsi-guard.py"')), 1)
        keep = [g for g in pre if any(h["command"] == "echo keep-me" for h in g["hooks"])]
        self.assertEqual(keep, [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo keep-me"}]}])
        self.assertEqual(ours(pre, 'rsi-guard.py"')[0]["hooks"][0]["command"].count("echo"), 0)
        before = (self.claude / "settings.json").read_bytes()
        setup.install(self.claude, "python")
        self.assertEqual((self.claude / "settings.json").read_bytes(), before)
        setup.remove(self.claude)
        self.assertEqual(load(self.claude)["hooks"]["PreToolUse"],
                         [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo keep-me"}]}])

    def test_non_string_command_rejected_before_copy(self):
        self.claude.mkdir(parents=True)
        path = self.claude / "settings.json"
        raw = b'{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": null}]}]}}'
        path.write_bytes(raw)
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            setup.install(self.claude, "python")
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.claude / "hooks").exists())

    def test_warns_on_non_default_claude_dir(self):
        lines = setup.install(self.claude, "python")
        self.assertTrue(any("주의" in l and "$HOME/.claude/hooks" in l for l in lines))

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows PowerShell only")
    def test_install_ps1_smoke(self):
        def run(*extra):
            return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                   str(REPO / "install.ps1"), "-ClaudeDir", str(self.claude), *extra],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
        seed(self.claude)
        r = run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.claude / "hooks" / "rsi.py").exists())
        s = load(self.claude)
        self.assertEqual(len(ours(s["hooks"]["SessionStart"], 'rsi.py" hook-session-start')), 1)
        self.assertEqual(len(ours(s["hooks"]["PreToolUse"], 'rsi-guard.py"')), 1)
        r = run("-Remove")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = load(self.claude)
        self.assertEqual(s["hooks"], {"SessionStart": [BRAIN_SS], "PreToolUse": [OTHER_PRE]})

    def test_cli_subprocess(self):
        seed(self.claude)
        r = subprocess.run([sys.executable, str(REPO / "tools" / "setup.py"), "install",
                            "--claude-dir", str(self.claude), "--python", "py -3"],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        s = load(self.claude)
        self.assertEqual(len(ours(s["hooks"]["PreToolUse"], 'rsi-guard.py"')), 1)
        r = subprocess.run([sys.executable, str(REPO / "tools" / "setup.py"), "remove",
                            "--claude-dir", str(self.claude)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ours(load(self.claude)["hooks"]["PreToolUse"], 'rsi-guard.py"'), [])


if __name__ == "__main__":
    unittest.main()
