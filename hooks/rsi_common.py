"""RSI kit 공통 모듈: 경로, 원자적 쓰기, jsonl, 비밀정보 거르기, 로그, 분리 실행."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Optional

CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------- 경로 (부를 때마다 환경 변수를 읽는다)

def kit_dir() -> Path:
    raw = os.environ.get("RSI_KIT_HOME")
    return Path(raw) if raw else Path.home() / ".rsi-kit"


def claude_dir() -> Path:
    raw = os.environ.get("RSI_CLAUDE_DIR")
    return Path(raw) if raw else Path.home() / ".claude"


def _second_brain_config() -> dict:
    home = os.environ.get("SECOND_BRAIN_HOME")
    base = Path(home) if home else Path.home() / ".second-brain"
    cfg = load_json(base / "config.json", {})
    return cfg if isinstance(cfg, dict) else {}


def vault_dir() -> Optional[Path]:
    """BRAIN_DIR > SECOND_BRAIN > second-brain config.json 의 vault. 없으면 None."""
    raw = os.environ.get("BRAIN_DIR") or os.environ.get("SECOND_BRAIN") or _second_brain_config().get("vault")
    return Path(raw) if raw else None


def journal_exclude() -> List[str]:
    value = _second_brain_config().get("journal_exclude")
    return [str(v) for v in value] if isinstance(value, list) else []


# ---------------------------------------------------------------- 시간

def now() -> datetime:
    return datetime.now().astimezone()


def today() -> date:
    return now().date()


# ---------------------------------------------------------------- 파일 입출력

def atomic_write(path: Path, text: str) -> None:
    """UTF-8(BOM 없음)·LF 로 임시 파일에 쓴 뒤 os.replace 로 바꾼다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\r\n", "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, obj: Any) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def read_jsonl(path: Path) -> List[dict]:
    try:
        # splitlines 는 U+2028 등 JSON 문자열 안의 글자에서도 끊으므로 \n 에서만 나눈다
        lines = Path(path).read_text(encoding="utf-8-sig").split("\n")
    except OSError:
        return []
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_jsonl(path: Path, rows: List[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    with open(path, "ab") as f:
        f.write(data)


# ---------------------------------------------------------------- 비밀정보 거르기
# 출처: C:\dev\second-brain-kit\hooks\brain_common.py 의 SECRET (그대로 복사)

SECRET = re.compile(
    r"(?i)(password|passwd|pwd|비밀번호|비번|api[_-]?key|secret|token|토큰)[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{4,}"
    r"|(?-i:\b[A-Z0-9_]*(?:PASS|PASSWORD|PWD|SECRET|TOKEN|API_?KEY)[A-Z0-9_]*\s*=\s*\S+)"
    r"|(?:비밀번호|비번|패스워드)\s*(?:은|는|이|가|:)?\s*\S{4,}"
    r"|(?-i:\s-P\s+\S+)"  # sqlcmd/mysql 의 -P <비밀번호>
    # gl*- 토큰은 인스턴스 접두어(회사-glpat-…)가 붙을 수 있어 경계 없이 잡는다
    r"|gl(?:pat|dt|rt|cbt|ptt)-[0-9A-Za-z_\-]{10,}"
    # 나머지 키 모양은 단어 중간(예: feedback-ask-...)의 sk- 를 지우지 않게 앞 글자가 영문·숫자·_·- 가 아닐 때만 잡는다.
    # 단 JSON 이스케이프(백슬래시 n·t·r, 백슬래시 u 네 자리)나 URL 인코딩(%3D) 바로 뒤는 붙여 넣은 JSON·URL 이라 잡는다.
    # (?-i: 는 대소문자 무시가 켈빈 기호(U+212A) 같은 비ASCII 글자를 영문으로 치지 않게 한다. sb.py 의 SECRET 과 같은 규칙
    r"|(?:(?-i:(?<![0-9A-Za-z_\-]))|(?<=\\[nrt])|(?<=\\u[0-9A-Fa-f]{4})|(?<=%[0-9A-Fa-f]{2}))"
    r"(?:gh[pousr]_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z_\-]{20,}|AKIA[0-9A-Z]{16})"
    r"|eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]*"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
    r"|\b(?:server|data source|user id|uid|initial catalog)\s*=\s*[^;\s]+"
    r"|jdbc:[^\s'\"]+|bearer\s+[0-9A-Za-z._\-]{10,}|\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s@/]+@"
    r"|\b\d{4}[- ]?\d{4,6}[- ]?\d{4,5}(?:[- ]?\d{4})?\b"  # 카드번호(15·16자리)
    r"|\b\d{6}-?[1-4]\d{6}\b|\b\d{3}-\d{2}-\d{5}\b|\b01[016789]-?\d{3,4}-?\d{4}\b"
)


def scrub(text: str) -> str:
    return SECRET.sub("[비밀정보 제거됨]", text)


# ---------------------------------------------------------------- 로그

def log(message: str) -> None:
    try:
        path = claude_dir() / "hooks" / "rsi.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = "%s [%d] %s\n" % (now().strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), message)
        with open(path, "ab") as f:
            f.write(line.encode("utf-8", "replace"))  # 짝 없는 서로게이트는 ? 로(로그가 예외를 내지 않게)
    except OSError:
        pass


# ---------------------------------------------------------------- 분리 실행

def spawn_background(script: Path, *args: str) -> None:
    """호출한 쪽이 바로 끝나도록 일을 떼어 낸 프로세스에서 돌린다 (second-brain-kit 과 같은 플래그)."""
    cmd = [sys.executable, str(script), *args]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, cwd=tempfile.gettempdir())
    if os.name != "nt":
        subprocess.Popen(cmd, start_new_session=True, **kw)
        return
    flags = 0x00000008 | 0x00000200 | CREATE_NO_WINDOW  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    error = None
    for breakaway in (0x01000000, 0):  # CREATE_BREAKAWAY_FROM_JOB: 막혀 있으면 없이 재시도
        try:
            subprocess.Popen(cmd, creationflags=flags | breakaway, **kw)
            return
        except OSError as e:
            error = e
    raise error
