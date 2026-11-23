"""RSI 키트 설치 — hooks/rsi*.py 를 <claude>/hooks 로 복사하고 settings.json 에 hook 두 그룹을 등록한다.
Python 3.9+, 표준 라이브러리만.

  python tools/setup.py install [--python CMD] [--claude-dir DIR]
  python tools/setup.py remove [--claude-dir DIR]     등록만 뺀다(파일·~/.rsi-kit 은 그대로)

여러 번 실행해도 결과가 같다(멱등). 바꿀 것이 있을 때만 settings.json.bak-<날짜시각> 을 남기고 쓴다.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell"
# 이벤트별: (우리 그룹 판별 문자열들, 등록할 그룹을 만드는 함수)
MARKS = {"SessionStart": ('rsi.py" hook-session-start',), "PreToolUse": ('rsi-guard.py"',)}


def wanted(python):
    # 잠금 hook 은 파일이 없거나 python 이 없으면 exit 2·127 로 끝나 모든 도구를 막을 수 있다. `|| true` 로 늘 0 을
    # 돌려준다(막을 때는 rsi-guard.py 가 deny JSON 을 내고 0 으로 끝나므로 그대로 막힌다).
    return {
        "SessionStart": {"hooks": [{"type": "command",
                                    "command": f'{python} "$HOME/.claude/hooks/rsi.py" hook-session-start',
                                    "timeout": 10}]},
        "PreToolUse": {"matcher": MATCHER,
                       "hooks": [{"type": "command",
                                  "command": f'{python} "$HOME/.claude/hooks/rsi-guard.py" || true',
                                  "timeout": 10}]},
    }


def is_hook_ours(hook, event):
    return isinstance(hook, dict) and any(m in hook.get("command", "") for m in MARKS[event])


def is_ours(group, event):
    inner = group.get("hooks") if isinstance(group, dict) else None
    return isinstance(inner, list) and any(is_hook_ours(h, event) for h in inner)


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load_settings(path):
    if not path.exists():
        return None, {}
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        fail(f"settings.json 을 읽지 못해 고치지 않았습니다({e}): {path}\nJSON 형식을 고친 뒤 다시 실행하세요.")
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        fail(f"settings.json 모양이 예상과 달라 고치지 않았습니다(최상위·hooks 가 객체가 아님): {path}")
    odd = [e for e, g in data.get("hooks", {}).items() if not isinstance(g, list)]
    if odd:
        fail(f"settings.json 모양이 예상과 달라 고치지 않았습니다(hooks.{odd[0]} 이 목록이 아님): {path}")
    for event, groups in data.get("hooks", {}).items():
        for g in groups:
            inner = g.get("hooks") if isinstance(g, dict) else None
            if isinstance(inner, list) and any(isinstance(h, dict) and "command" in h and not isinstance(h["command"], str)
                                               for h in inner):
                fail(f"settings.json 모양이 예상과 달라 고치지 않았습니다(hooks.{event} 의 command 가 문자열이 아님): {path}")
    return raw, data


def dump(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def backup(path):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target, n = path.with_name(f"{path.name}.bak-{stamp}"), 2
    while target.exists():
        target, n = path.with_name(f"{path.name}.bak-{stamp}-{n}"), n + 1
    shutil.copy2(path, target)
    return target


def write_atomic(path, data):
    path = path.resolve()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save(path, raw, before, data):
    """파싱한 내용이 바뀔 때만 쓴다(Claude Code 는 끝 줄바꿈 없이 쓰므로 바이트 비교는 하지 않는다)."""
    if raw is not None and dump(data) == before:
        return "settings.json: 변경 없음"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = backup(path) if path.exists() else None
    write_atomic(path, dump(data))
    return f"settings.json: 갱신 (백업 {saved.name})" if saved else "settings.json: 만듦"


def same_dir(a, b):
    try:
        return Path(a).expanduser().resolve() == Path(b).expanduser().resolve()
    except OSError:
        return False


def path_warnings(claude):
    out = []
    if not same_dir(claude, Path.home() / ".claude"):  # 등록한 명령은 $HOME/.claude/hooks 를 가리킨다
        out.append(f'주의: settings.json 의 hook 명령은 "$HOME/.claude/hooks/..." 를 가리키는데 hook 파일은 {claude / "hooks"} 에 '
                   "복사했습니다. Claude Code 가 이 폴더를 설정 폴더로 쓰면 없는 파일을 실행해 잠금·수집이 동작하지 않으니 "
                   "settings.json 의 경로를 그 폴더로 직접 고치세요.")
    active = os.environ.get("CLAUDE_CONFIG_DIR")
    if active and not same_dir(claude, active):
        out.append(f"주의: CLAUDE_CONFIG_DIR 이 {active} 라서 Claude Code 는 방금 설치한 {claude} 를 읽지 않습니다. "
                   f'python tools/setup.py install --claude-dir "{active}" 로 다시 설치하고 settings.json 의 hook 경로를 그 폴더로 고치세요.')
    return out


def install(claude_dir, python):
    claude = Path(claude_dir)
    path = claude / "settings.json"
    raw, data = load_settings(path)  # 모양이 틀리면 여기서 멈춘다(아무것도 쓰기 전)
    done = []
    hooks_dir = claude / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(p.name for p in (KIT / "hooks").glob("rsi*.py"))
    for name in names:
        shutil.copyfile(KIT / "hooks" / name, hooks_dir / name)
    done.append(f"hooks 복사: {hooks_dir} ({', '.join(names)})")
    before = dump(data) if raw is not None else None
    hooks = data.setdefault("hooks", {})
    for event, group in wanted(python).items():
        new, placed = [], False
        for g in hooks.setdefault(event, []):
            if not is_ours(g, event):
                new.append(g)
                continue
            if not placed:  # 우리 그룹은 첫 자리에 정식 모양으로 둔다
                new.append(group)
                placed = True
            rest = [h for h in g["hooks"] if not is_hook_ours(h, event)]
            if rest:  # 다른 hook 이 섞인 그룹은 그 hook 과 matcher 를 살린다
                new.append(dict(g, hooks=rest))
        if not placed:
            new.append(group)
        hooks[event] = new
    done.append(save(path, raw, before, data))
    done.extend(path_warnings(claude))
    return done


def remove(claude_dir):
    claude = Path(claude_dir)
    path = claude / "settings.json"
    raw, data = load_settings(path)
    if raw is None:
        return ["settings.json: 변경 없음 — rsi hook 등록 없음"]
    before = dump(data)
    hooks = data.get("hooks", {})
    for event in MARKS:
        groups = hooks.get(event)
        if not groups:
            continue
        kept = []
        for g in groups:
            if not is_ours(g, event):
                kept.append(g)
                continue
            rest = [h for h in g["hooks"] if not is_hook_ours(h, event)]
            if rest:
                kept.append(dict(g, hooks=rest))
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    done = [save(path, raw, before, data)]
    done.append("남긴 것: hooks/rsi*.py 와 ~/.rsi-kit 은 지우지 않았습니다(필요하면 직접 지운다)")
    return done


def main():
    ap = argparse.ArgumentParser(description="RSI 키트 설치·제거")
    ap.add_argument("action", choices=("install", "remove"))
    ap.add_argument("--python", default="python", help="hook 을 돌릴 명령(기본 python, macOS·Linux 는 보통 python3)")
    # 기본값은 ~/.claude 다(RSI_CLAUDE_DIR 는 키트 런타임용이라 여기서는 읽지 않는다)
    ap.add_argument("--claude-dir", default=str(Path.home() / ".claude"), help="Claude Code 설정 폴더(기본 ~/.claude)")
    a = ap.parse_args()
    lines = install(a.claude_dir, a.python) if a.action == "install" else remove(a.claude_dir)
    for line in lines:
        print(line)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    main()
