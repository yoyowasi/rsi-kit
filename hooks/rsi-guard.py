#!/usr/bin/env python3
"""RSI 킷 잠금 hook (PreToolUse). 판정 기준(~/.rsi-kit/spec/)과 설치된 킷 코드(~/.claude/hooks/rsi*)를
에이전트가 고치지 못하게 막는다. 모든 도구 호출마다 돌므로 표준 라이브러리만 쓰고, 고장 나면 막지 않는다."""
import json
import posixpath
import re
import sys

REASON = "RSI 킷 판정 기준·판정 코드는 사람이 에디터로만 고친다(평가 분리)."
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")

_PRE = r"(?:^|[/~\s\"'=(])"
_SPEC = _PRE + r"\.rsi-kit/spec(?![\w.\-])"
_HOOKS = _PRE + r"\.claude/hooks/rsi(?!\.log(?![\w.\-]))"
SPEC = re.compile(_SPEC)
MARKER = re.compile(_SPEC + "|" + _HOOKS)
_TOKEN = (r"(?:\"[^\"\r\n`<>]*\.claude/hooks/rsi\.py\"|'[^'\r\n`<>]*\.claude/hooks/rsi\.py'"
          r"|[^\s\"';&|<>`(){},*?\[\]]*\.claude/hooks/rsi\.py)")
CLI = re.compile(r"^\s*(?:py\s+-3|python3?)\s+" + _TOKEN + r"\s+(?:init|collect|judge|status)\s*$")
DRAFT_SUFFIX = "/.rsi-kit/spec/rules.draft.json"


def _norm(s):
    s = s.lower().replace("\\", "/")
    return re.sub(r"/{2,}", "/", s)


def _norm_path(s):
    n = posixpath.normpath(_norm(s))
    return "/".join(seg.rstrip(". ") if seg not in ("", ".", "..") else seg for seg in n.split("/"))


def blocked(tool_name, tool_input):
    if not isinstance(tool_input, dict):
        return None
    if tool_name in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(path, str):
            return None
        n = _norm_path(path.strip())
        if not MARKER.search(n):
            return None
        if n.endswith(DRAFT_SUFFIX) and "/../" not in n:
            return None
        return REASON
    if tool_name in SHELL_TOOLS:
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            return None
        n = _norm(cmd)
        if not MARKER.search(n):
            return None
        if CLI.match(n) and not SPEC.search(n) and not re.search(r"[\r\n<>`]|\$\(", n):
            return None
        return REASON
    return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig", errors="replace"))
        reason = blocked(data.get("tool_name"), data.get("tool_input"))
        if reason:
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                          "permissionDecision": "deny",
                                          "permissionDecisionReason": reason}}
            sys.stdout.write(json.dumps(out))
            sys.stdout.flush()
    except Exception as exc:  # 고장 나도 도구를 막지 않는다(fail open)
        try:
            sys.path.insert(0, __file__.rsplit("rsi-guard.py", 1)[0] or ".")
            from rsi_common import log, scrub
            log(scrub("rsi-guard 오류(막지 않고 통과): %s: %s" % (type(exc).__name__, str(exc)[:120])))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
