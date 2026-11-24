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
# 칸 안에 공백이 아닌 글자 하나(x·X·v·o·✓ 등)가 있으면 체크로 본다. 에디터마다 [ v]·* [x] 처럼 써도 읽히게.
_CHECKED = re.compile(r"^\s*[-*]\s+\[\s*\S\s*\]\s+(맞음|틀림)\s*$")


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


def review_mode(prompt_hash: str) -> Optional[str]:
    """새 확인 파일을 만들어야 하면 "full"(첫 회·프롬프트 변경 뒤) 또는 "run"(판정마다 작은 묶음), 아니면 None."""
    files = _files()
    if not files:
        return "full"
    if pending_review() is not None:
        return None
    return "full" if _prompt_hash(files[-1]) != prompt_hash else "run"


def _reviewed_pairs(prompt_hash: str) -> set:
    """지금 프롬프트(prompt_hash)로 만든 확인 파일(끝났든 아니든)에 들어간 (발췌 id, 규칙) 쌍.
    프롬프트가 바뀌면 같은 쌍도 새 프롬프트로 다시 판정된 것이라 다시 고를 수 있다."""
    return {(i["id"], i["rule"]) for f in _files() if _prompt_hash(f) == prompt_hash for i in read_review(f)}


def _rule_line(text) -> str:
    one = " ".join(scrub(str(text)).split())[:200].rstrip()
    return "- 규칙: %s" % one if one else ""


def make_review(day: date, prompt_hash: str, judgments: List[dict], excerpts: List[dict], size: int,
                mode: str = "full", rule_texts: Optional[dict] = None) -> Optional[Path]:
    """확인 파일을 쓰고 경로를 돌려준다. 후보가 하나도 없으면 아무것도 쓰지 않고 None.
    교정 yes/no 를 반반(yes 가 올림)으로 고르고 한쪽이 모자라면 다른 쪽으로 채운다.
    run: 발췌가 최근인 쌍부터. full: 날짜로 시드한 무작위 표본(결정적)."""
    by_id = {e.get("id"): e for e in excerpts}
    done = _reviewed_pairs(prompt_hash)
    yes, no, seen = [], [], set()
    for j in judgments:
        rule, corr = j.get("rule"), j.get("correction")
        if rule in ("*", "!") or corr not in ("yes", "no"):
            continue
        key = (j.get("excerpt"), rule)
        if key in seen or key in done or j.get("excerpt") not in by_id:
            continue
        seen.add(key)
        (yes if corr == "yes" else no).append(j)
    if mode == "run":
        newest = lambda j: str(by_id[j["excerpt"]].get("ts", ""))  # noqa: E731
        yes.sort(key=newest, reverse=True)  # sort 는 안정적이라 ts 가 같으면 판정 순서를 지킨다
        no.sort(key=newest, reverse=True)
    else:
        rng = random.Random(day.toordinal())
        yes = rng.sample(yes, len(yes))
        no = rng.sample(no, len(no))
    n_yes = (size + 1) // 2
    n_no = size - n_yes
    picked_yes, picked_no = yes[:n_yes], no[:n_no]
    if len(picked_yes) < n_yes:
        picked_no = no[:n_no + n_yes - len(picked_yes)]
    elif len(picked_no) < n_no:
        picked_yes = yes[:n_yes + n_no - len(picked_no)]
    picked = picked_yes + picked_no

    if not picked:
        return None

    texts = rule_texts or {}
    out = ["---", "prompt_hash: %s" % prompt_hash,
           "created: %s" % rsi_common.now().strftime("%Y-%m-%dT%H:%M"), "---", "", HEADING, "", INSTRUCTION, ""]
    for n, j in enumerate(picked, 1):
        e = by_id[j["excerpt"]]
        out.append("### R%d [%s] 판정: 교정 %s" % (n, j["rule"], j["correction"]))
        rl = _rule_line(texts.get(j["rule"], ""))
        if rl:
            out.append(rl)
        out += ["- 발췌: %s" % e["id"],
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

    def rate(judged: str, answer: str):
        group = [r for r in answered if r.get("judged") == judged]
        return ((sum(1 for r in group if r["answer"] == answer) / len(group)) if group else None), len(group)

    yes_precision, yes_answered = rate("yes", "맞음")
    miss_rate, no_answered = rate("no", "틀림")
    return {"calibrated": finished_any, "agreement": agreement, "answered": len(rows), "pending": pending,
            "yes_precision": yes_precision, "yes_answered": yes_answered,
            "miss_rate": miss_rate, "no_answered": no_answered}
