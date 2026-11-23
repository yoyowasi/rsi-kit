"""대화 기록(transcript)에서 사용자 말 발췌를 모아 저장한다."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import rsi_common

U_MAX = 1500
TAIL_MAX = 300
ARG_MAX = 200
NO_FOLDER = "폴더 없음"

# 출처: C:\dev\second-brain-kit\hooks\brain_common.py 의 _TAGS (그대로 복사)
_TAGS = re.compile(r"<(system-reminder|local-command-stdout|local-command-stderr|local-command-caveat|"
                   r"command-message|bash-stdout|bash-stderr)>.*?</\1>", re.S)
# 출처: 같은 파일의 ROUTINE (그대로 복사)
ROUTINE = re.compile(r"^\s*<scheduled-task\b|This is an automated run of a scheduled task|^자동 루틴\(", re.M)
# 출처: 같은 파일의 SKIP_PATH. 에이전트 scratchpad(임시 작업 폴더) 세션은 뺀다(저장 폴더 이름이나 cwd 에 있으면).
SKIP_PATH = re.compile(r"(?i)scratchpad")
# 앱·다른 세션이 사용자 줄에 끼워 넣는 알림 블록. 사람이 친 말이 아니다(지우고 남는 것이 없으면 사람 말이 아님).
_INJECTED = re.compile(r"<(side-sessions-event|artifact-view-context|ci-monitor-event|cross-session-message|"
                       r"task-notification)\b[^>]*>.*?</\1>", re.S)

_CMD_TOOLS = ("Bash", "PowerShell")
_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def _valid(text: str) -> str:
    """짝 없는 서로게이트(\\ud83d)는 UTF-8 로 쓸 수 없다. ? 로 바꾼다."""
    return text.encode("utf-8", "replace").decode("utf-8")


def _content(obj: dict):
    message = obj.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _is_human_origin(obj: dict) -> bool:
    origin = obj.get("origin")
    return not origin or (isinstance(origin, dict) and origin.get("kind") in (None, "human"))


def _skipped(obj: dict) -> bool:
    return bool(obj.get("isMeta") or obj.get("isSidechain") or obj.get("isCompactSummary")
                or obj.get("isVisibleInTranscriptOnly"))


def _human_text(obj: dict) -> Optional[str]:
    """사람이 친 말(태그 제거). 사람 말이 아니면 None."""
    if obj.get("type") != "user" or _skipped(obj) or not _is_human_origin(obj):
        return None
    content = _content(obj)
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b["text"] for b in content
                         if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
    else:
        return None
    text = _INJECTED.sub("", _TAGS.sub("", _valid(text))).strip()
    return text or None


def _assistant_parts(obj: dict) -> Tuple[List[str], List[dict]]:
    """assistant 줄의 (text 블록들, tool_use 도구들)."""
    if obj.get("type") != "assistant" or _skipped(obj):
        return [], []
    content = _content(obj)
    if not isinstance(content, list):
        return [], []
    texts, tools = [], []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            texts.append(_valid(b["text"]))
        elif b.get("type") == "tool_use" and isinstance(b.get("name"), str):
            tools.append({"name": _valid(b["name"]), "arg": _tool_arg(b["name"], b.get("input"))})
    return texts, tools


def _tool_arg(name: str, inp) -> str:
    if not isinstance(inp, dict):
        return ""
    if name in _CMD_TOOLS:
        value = inp.get("command")
        return _valid(value)[:ARG_MAX] if isinstance(value, str) else ""
    if name in _FILE_TOOLS:
        value = inp.get("file_path") or inp.get("notebook_path")
        return _valid(value) if isinstance(value, str) else ""
    return ""


def _timestamp(obj: dict) -> Optional[Tuple[str, str]]:
    """(표시·날짜용 현지 ISO, id 용 UTC `YYYY-MM-DDTHH:MM:SS(.ffffff)Z`). 시각이 없거나 깨졌으면 None."""
    try:
        parsed = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
        utc = parsed.astimezone(timezone.utc)
        key = utc.strftime("%Y-%m-%dT%H:%M:%S") + (".%06d" % utc.microsecond if utc.microsecond else "") + "Z"
        return parsed.astimezone().replace(microsecond=0).isoformat(), key
    except (KeyError, ValueError, AttributeError, TypeError, OverflowError, OSError):
        return None


def _sha8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", text)


def _pick_cwd(seen: List[str], stored: str) -> Optional[str]:
    """저장 폴더 이름(slug)과 맞는 cwd, 없으면 처음 나온 Windows 형식, 없으면 처음 것."""
    return (next((c for c in seen if _slug(c) == stored), None)
            or next((c for c in seen if re.match(r"^[A-Za-z]:[\\/]", c)), None)
            or (seen[0] if seen else None))


def _project_name(cwd: Optional[str]) -> str:
    if not cwd:
        return NO_FOLDER
    parts = [p for p in re.split(r"[\\/]", cwd) if p]
    return parts[-1] if parts else NO_FOLDER


def _iter_lines(path: Path):
    """줄바꿈(LF)에서만 나눈다(splitlines 는 U+2028 등 JSON 안의 글자에서도 끊는다). 줄마다 서로게이트를 없앤다."""
    with open(path, encoding="utf-8", errors="replace", newline=chr(10)) as f:
        for line in f:
            yield _valid(line)


def _loads(line: str):
    try:
        return json.loads(line)
    except (ValueError, RecursionError):
        return None


def parse_transcript(path: Path) -> Tuple[List[dict], Optional[str]]:
    """(발췌들, cwd). 깨진 줄은 건너뛴다."""
    path = Path(path)
    session = path.stem
    objs, seen = [], []
    for line in _iter_lines(path):
        obj = _loads(line)
        if not isinstance(obj, dict):
            continue
        cwd = obj.get("cwd")
        if isinstance(cwd, str) and cwd:
            cwd = _valid(cwd)
            if cwd not in seen:
                seen.append(cwd)
        objs.append(obj)
    cwd = _pick_cwd(seen, path.parent.name)
    project = _project_name(cwd)

    def make(ex_id: str, ts: str, u: str, texts: List[str], tools: List[dict]) -> dict:
        return {"id": ex_id, "ts": ts, "project": project,
                "session": session, "u": u[:U_MAX], "a_tail": "".join(texts)[-TAIL_MAX:], "tools": tools}

    # id 는 세션과 시간대에 기대지 않는다(원래 UTC 시각 + 내용 해시). 재개·포크한 세션 파일이 같은 사람 말을
    # 다시 담아도 id 가 같아 store_excerpts 가 하나만 남긴다(같은 교정이 세션 둘로 세어지지 않게).
    excerpts: List[dict] = []
    texts: List[str] = []
    tools: List[dict] = []
    last: Optional[Tuple[str, str]] = None
    for obj in objs:
        ts = _timestamp(obj)
        human = _human_text(obj)
        if human is not None:
            if ts is None:
                continue
            excerpts.append(make("%s:%s" % (ts[1], _sha8(human)), ts[0], human, texts, tools))
            texts, tools = [], []
            last = ts
            continue
        new_texts, new_tools = _assistant_parts(obj)
        texts.extend(new_texts)
        tools.extend(new_tools)
        if new_tools and ts:
            last = ts
    if tools and last:
        digest = _sha8("|".join("%s:%s" % (t["name"], t["arg"]) for t in tools))
        excerpts.append(make("%s:end:%s" % (last[1], digest), last[0], "", texts, tools))
    return excerpts, cwd


# ---------------------------------------------------------------- 수집

def _is_routine_start(path: Path, limit: int = 400) -> bool:
    """첫 사람 말이 예약 실행인가."""
    for n, line in enumerate(_iter_lines(path)):
        if n >= limit:
            break
        if '"user"' not in line:
            continue
        obj = _loads(line)
        if not isinstance(obj, dict) or obj.get("type") != "user" or obj.get("isMeta"):
            continue
        origin = obj.get("origin")
        if isinstance(origin, dict) and origin.get("subkind") == "scheduled-trigger":
            return True
        content = _content(obj)
        if isinstance(content, list):
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                continue
            content = "\n".join(b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
        if isinstance(content, str) and content.strip():
            return bool(ROUTINE.search(content.lstrip("\ufeff")))
    return False


def _excluded_cwd(cwd: Optional[str], excludes: List[str]) -> bool:
    norm = os.path.normcase(os.path.abspath(cwd)) if cwd else ""
    for part in excludes:
        p = os.path.normcase(part or "").rstrip("\\/")
        if p and norm and p + os.sep in norm + os.sep:
            return True
    return False


def _scrub_row(row: dict) -> dict:
    row = dict(row)
    row["u"] = rsi_common.scrub(row.get("u", ""))
    row["a_tail"] = rsi_common.scrub(row.get("a_tail", ""))
    row["tools"] = [{"name": t.get("name", ""), "arg": rsi_common.scrub(t.get("arg", ""))}
                    for t in row.get("tools", [])]
    return row


def collect(since_epoch: float) -> List[dict]:
    """since_epoch 뒤에 바뀐 대화 기록에서 발췌를 모은다(scrub 한 상태)."""
    excludes = rsi_common.journal_exclude()
    rows: List[dict] = []
    for path in sorted((rsi_common.claude_dir() / "projects").glob("*/*.jsonl")):
        try:
            if path.stat().st_mtime <= since_epoch:
                continue
            if SKIP_PATH.search(path.parent.name) or _is_routine_start(path):
                continue
            excerpts, cwd = parse_transcript(path)
        except OSError:
            continue
        if (cwd and SKIP_PATH.search(cwd)) or _excluded_cwd(cwd, excludes):
            continue
        rows.extend(_scrub_row(e) for e in excerpts)
    return rows


# ---------------------------------------------------------------- 저장·읽기

def _month_file(month: str) -> Path:
    return rsi_common.kit_dir() / "excerpts" / ("%s.jsonl" % month)


def store_excerpts(rows: List[dict]) -> int:
    """새 발췌만 월별 파일 끝에 붙인다. 이미 있는 줄은 고치지 않는다. 새로 쓴 수를 돌려준다."""
    by_month: dict = {}
    for row in rows:
        by_month.setdefault(str(row.get("ts", ""))[:7], []).append(row)
    written = 0
    for month, group in sorted(by_month.items()):
        if not re.match(r"^\d{4}-\d{2}$", month):
            continue
        path = _month_file(month)
        known = {r.get("id") for r in rsi_common.read_jsonl(path)}
        fresh = []
        for row in group:
            if row.get("id") in known:
                continue
            known.add(row.get("id"))
            fresh.append(_scrub_row(row))
        if fresh:
            rsi_common.append_jsonl(path, fresh)
            written += len(fresh)
    return written


def load_excerpts(since: date) -> List[dict]:
    """ts 날짜가 since 이상인 발췌를 ts 순으로 돌려준다."""
    folder = rsi_common.kit_dir() / "excerpts"
    first = since.strftime("%Y-%m")
    rows: List[dict] = []
    for path in sorted(folder.glob("*.jsonl")):
        if path.stem < first:
            continue
        rows.extend(r for r in rsi_common.read_jsonl(path) if str(r.get("ts", ""))[:10] >= since.isoformat())
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows
