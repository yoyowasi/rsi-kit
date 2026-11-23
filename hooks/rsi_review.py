"""보정 확인: 모델 판정을 사람이 표본 검사하고 일치율을 계산한다."""
from __future__ import annotations

import random
import re
from datetime import date
from pathlib import Path
from typing import List, Optional

import rsi_common
from rsi_common import atomic_write, kit_dir, read_jsonl, scrub

HEADING = "# 판정 확인"
INSTRUCTION = "각 항목에서 맞음 또는 틀림 하나만 [x]로 체크하세요."
_ITEM = re.compile(r"^### R(\d+) \[(.*)\] 판정: 교정 (yes|no)\s*$")
_EXCERPT = re.compile(r"^- 발췌: (.*?)\s*$")
_CHECKED = re.compile(r"^- \[[xX]\] (맞음|틀림)\s*$")


def review_dir() -> Path:
    return kit_dir() / "spec" / "review"


def _sort_key(path: Path):
    parts = path.stem.split(" ")
    try:
        n = int(parts[1]) if len(parts) > 1 else 1
    except ValueError:
        n = 1
    return (parts[0], n)


def _files() -> List[Path]:
    d = review_dir()
    if not d.is_dir():
        return []
    # 항목이 하나도 없는 파일(읽지 못한 파일 포함)은 끝난 빈 파일로 보고 무시한다(막히지 않게)
    return sorted((f for f in d.glob("*.md") if read_review(f)), key=_sort_key)


def _read_text(path: Path) -> str:
    """사람이 에디터로 고치는 파일이다. 깨진 바이트는 바꿔 읽고, 읽을 수 없으면(폴더·권한) 빈 글로 본다."""
    try:
        return Path(path).read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    except OSError:
        return ""


def _prompt_hash(path: Path) -> Optional[str]:
    lines = _read_text(path).split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if ln.startswith("prompt_hash:"):
            return ln.split(":", 1)[1].strip()
    return None


def read_review(path: Path) -> List[dict]:
    items: List[dict] = []
    cur = None
    checked: List[str] = []

    def close():
        if cur is not None:
            cur["answer"] = checked[0] if len(checked) == 1 else None
            items.append(cur)

    for ln in _read_text(path).split("\n"):
        m = _ITEM.match(ln)
        if m:
            close()
            cur = {"id": "", "rule": m.group(2), "judged": m.group(3), "answer": None}
            checked = []
            continue
        if cur is None:
            continue
        m = _EXCERPT.match(ln)
        if m and not cur["id"]:
            cur["id"] = m.group(1)
            continue
        m = _CHECKED.match(ln)
        if m:
            checked.append(m.group(1))
    close()
    return items


def _finished(path: Path) -> bool:
    items = read_review(path)
    return bool(items) and all(i["answer"] for i in items)


def pending_review() -> Optional[Path]:
    for path in reversed(_files()):
        if not _finished(path):
            return path
    return None


def needs_review(prompt_hash: str) -> bool:
    files = _files()
    if not files:
        return True
    if pending_review() is not None:
        return False
    return _prompt_hash(files[-1]) != prompt_hash


def make_review(day: date, prompt_hash: str, judgments: List[dict], excerpts: List[dict], size: int) -> Optional[Path]:
    """확인 파일을 쓰고 경로를 돌려준다. 후보가 하나도 없으면 아무것도 쓰지 않고 None."""
    by_id = {e.get("id"): e for e in excerpts}
    yes, no, seen = [], [], set()
    for j in judgments:
        rule, corr = j.get("rule"), j.get("correction")
        if rule == "*" or corr not in ("yes", "no"):
            continue
        key = (j.get("excerpt"), rule)
        if key in seen or j.get("excerpt") not in by_id:
            continue
        seen.add(key)
        (yes if corr == "yes" else no).append(j)
    picked = yes[:size]
    room = size - len(picked)
    if room > 0 and no:
        picked += random.Random(day.toordinal()).sample(no, min(room, len(no)))

    if not picked:
        return None

    out = ["---", "prompt_hash: %s" % prompt_hash,
           "created: %s" % rsi_common.now().strftime("%Y-%m-%dT%H:%M"), "---", "", HEADING, "", INSTRUCTION, ""]
    for n, j in enumerate(picked, 1):
        e = by_id[j["excerpt"]]
        out += ["### R%d [%s] 판정: 교정 %s" % (n, j["rule"], j["correction"]),
                "- 발췌: %s" % e["id"],
                "U: %s" % scrub(str(e.get("u", "")))[:500].replace("\n", " "),
                "A: %s" % scrub(str(e.get("a_tail", "")))[:500].replace("\n", " "),
                "- [ ] 맞음", "- [ ] 틀림", ""]

    d = review_dir()
    path = d / ("%s.md" % day.isoformat())
    k = 2
    while path.exists():
        path = d / ("%s %d.md" % (day.isoformat(), k))
        k += 1
    atomic_write(path, "\n".join(out))
    return path


def calibration() -> dict:
    cal_path = kit_dir() / "calibration.jsonl"
    rows = read_jsonl(cal_path)
    recorded = {r.get("file") for r in rows}
    new_rows: List[dict] = []
    finished_any = bool(recorded)
    pending = 0
    for path in _files():
        items = read_review(path)
        if _finished(path):
            finished_any = True
            if path.name not in recorded:
                new_rows += [{"file": path.name, "id": i["id"], "rule": i["rule"],
                              "judged": i["judged"], "answer": i["answer"]} for i in items]
        else:
            pending += sum(1 for i in items if not i["answer"])
    if new_rows:
        rsi_common.append_jsonl(cal_path, new_rows)
        rows += new_rows
    answered = [r for r in rows if r.get("answer") in ("맞음", "틀림")]
    agreement = (sum(1 for r in answered if r["answer"] == "맞음") / len(answered)) if answered else None
    return {"calibrated": finished_any, "agreement": agreement, "answered": len(rows), "pending": pending}
