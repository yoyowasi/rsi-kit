"""RSI kit 규칙 사실 목록: 현재 규칙 문구, 채택일(dream 제안 파일), 매일 갱신."""
from __future__ import annotations

import difflib
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import rsi_common

BLOCK = re.compile(r"<!--\s*brain:start\s*-->(.*?)<!--\s*brain:end\s*-->", re.S)
RULE_LINE = re.compile(r"^- \[L(\d+)\] (.+)$", re.M)
SOURCE_DREAM = re.compile(r"^\s*source:\s*dream\s*$", re.M)
DESC_LINE = re.compile(r"^description:\s*(.*?)\s*$", re.M)
TYPE_DREAM = re.compile(r"^type:\s*dream\s*$", re.M)
PROPOSAL_HEAD = re.compile(r"^## (\d+)\. (추가|고치기|빼기) · (.+?)(?: · 대상 (.+))?$")
RESULT_HEAD = re.compile(r"^## 처리 결과 \((\d{4}-\d{2}-\d{2})\)")
MEM_SLUG = re.compile(r"memory:\s*([^\s)]+)")
MATCH_THRESHOLD = 0.6


# ---------------------------------------------------------------- 현재 규칙 문구

def read_global_rules(text: str) -> Dict[str, str]:
    """전역 CLAUDE.md 의 brain 블록 안 `- [Lnn] 문구` 줄만."""
    rules: Dict[str, str] = {}
    for block in BLOCK.findall(text.replace("\r\n", "\n")):
        for m in RULE_LINE.finditer(block):
            rules["L" + m.group(1)] = m.group(2).strip()
    return rules


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def read_memory_rules(projects: Path) -> Dict[str, str]:
    """`<projects>/<slug>/memory/*.md` 중 source: dream 인 것. id `<slug>/<file>.md`, 값은 description."""
    rules: Dict[str, str] = {}
    for path in sorted(Path(projects).glob("*/memory/*.md")):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        head = text[:1500]
        if not SOURCE_DREAM.search(head):
            continue
        m = DESC_LINE.search(head)
        desc = _unquote(m.group(1)) if m else ""
        rules["%s/%s" % (path.parent.parent.name, path.name)] = desc
    return rules


# ---------------------------------------------------------------- dream 제안 파일

def _numbers(segment: str) -> List[int]:
    return [int(n) for n in re.findall(r"(?<![\w\[])(\d+)(?=\s*(?:→|,|\(|$))", segment.strip())]


def _item_info(text: str, slug: Optional[str], key_default: Optional[str] = None) -> Dict[str, str]:
    """항목 하나의 글에서 {직접 id: 동작} 을 만든다."""
    text = text.split(" · ")[0]
    if "삭제" in text:
        action = "delete"
    elif "문구" in text or "수정" in text:
        action = "edit"
    else:
        action = "adopt"
    out: Dict[str, str] = {}
    for n in re.findall(r"\[L(\d+)\]", text):
        out["L" + n] = action
    m = re.search(r"메모리\s+([^\s,·()]+)", text)
    if m:
        name = m.group(1).rstrip(".,")
        if name.endswith(".md"):
            name = name[:-3]
        s = None
        sm = MEM_SLUG.search(text)
        if sm:
            s = sm.group(1)
        s = s or slug
        out[("%s/%s.md" % (s, name)) if s else name + ".md"] = action
    return out


def _parse_result(body: str, slugs: Dict[int, str]) -> dict:
    """처리 결과 절 본문 → {"adopted","rejected","held","direct"} (날짜는 호출한 쪽이 붙인다)."""
    body = body.replace("\r\n", "\n")
    adopted: List[int] = []
    rejected: List[int] = []
    held: List[int] = []
    direct: Dict[str, str] = {}
    direct_n: Dict[str, int] = {}

    for kw, bucket in (("반영", adopted), ("거절", rejected), ("보류", held)):
        for m in re.finditer(kw + r":\s*([^·\n]*)", body):
            seg = m.group(1)
            bucket.extend(_numbers(seg))
            for pm in re.finditer(r"(?<![\w\[])(\d+)\s*\(([^)]*)\)", seg):
                info = _item_info(pm.group(2), slugs.get(int(pm.group(1))))
                direct.update(info)
                direct_n.update({k: int(pm.group(1)) for k in info})

    marks = list(re.finditer(r"(?<![\w\[])(\d+)\s*(→|거절)", body))
    for i, m in enumerate(marks):
        n = int(m.group(1))
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        chunk = body[m.end():end].split("\n")[0]
        if m.group(2) == "거절":
            rejected.append(n)
            continue
        adopted.append(n)
        info = _item_info(chunk, slugs.get(n))
        direct.update(info)
        direct_n.update({k: n for k in info})

    def uniq(values: List[int]) -> List[int]:
        return sorted(set(values))

    return {"adopted": uniq(adopted), "rejected": uniq(rejected), "held": uniq(held), "direct": direct,
            "direct_n": direct_n}


def _parse_dream(text: str, name: str) -> dict:
    proposals: Dict[int, dict] = {}
    slugs: Dict[int, str] = {}
    sections: List[tuple] = []  # (date, body)
    current = None
    cur_kind = None
    body_lines: List[str] = []

    def flush():
        if cur_kind == "result" and current is not None:
            sections.append((current, "\n".join(body_lines)))

    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            flush()
            body_lines = []
            cur_kind = None
            current = None
            rm = RESULT_HEAD.match(line)
            pm = PROPOSAL_HEAD.match(line)
            if rm:
                cur_kind, current = "result", rm.group(1)
            elif pm:
                n = int(pm.group(1))
                scope = pm.group(3).strip()
                sm = MEM_SLUG.search(scope)
                if sm:
                    slugs[n] = sm.group(1)
                proposals[n] = {"action": pm.group(2), "target": (pm.group(4) or scope).strip(),
                                "rule": "", "evidence": ""}
                cur_kind, current = "proposal", n
            continue
        if cur_kind == "proposal":
            prop = proposals[current]
            if line.startswith("- 규칙:"):
                prop["rule"] = line[len("- 규칙:"):].strip()
            elif line.startswith("- 근거:"):
                prop["evidence"] = line[len("- 근거:"):].strip()
        elif cur_kind == "result":
            body_lines.append(line)
    flush()

    results = []
    for day, body in sections:
        r = _parse_result(body, slugs)
        r["date"] = day
        results.append(r)
    return {"file": name, "proposals": proposals, "results": results}


def adoption_records(vault: Path) -> List[dict]:
    """볼트 90_Archive·00_Inbox 의 `type: dream` 파일마다 제안·처리 결과를 읽는다."""
    records: List[dict] = []
    if vault is None or not Path(vault).is_dir():
        return records
    files: List[Path] = []
    for folder in ("90_Archive", "00_Inbox"):
        files.extend(sorted((Path(vault) / folder).glob("*dream*.md")))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if not TYPE_DREAM.search(text[:600]):
            continue
        records.append(_parse_dream(text, path.name))
    return records


# ---------------------------------------------------------------- 맞추기

def _direct_key_matches(key: str, rid: str) -> bool:
    if key == rid:
        return True
    return "/" in rid and "/" not in key and rid.endswith("/" + key)


def match_adoptions(rules: Dict[str, str], records: List[dict]) -> Dict[str, dict]:
    events = []  # (date, file, key, action)
    for rec in records:
        for res in rec["results"]:
            for key, action in res["direct"].items():
                n = res.get("direct_n", {}).get(key)
                evidence = (rec["proposals"].get(n) or {}).get("evidence", "") if n is not None else ""
                events.append((res["date"], evidence, key, action))
    events.sort(key=lambda e: e[0])

    candidates = []  # (date, file, rule text)
    for rec in records:
        for res in rec["results"]:
            for n in res["adopted"]:
                prop = rec["proposals"].get(n)
                if prop and prop["action"] in ("추가", "고치기") and prop["rule"] and prop["rule"] != "(뺌)":
                    candidates.append((res["date"], prop.get("evidence", ""), prop["rule"]))

    out: Dict[str, dict] = {}
    for rid, text in rules.items():
        evs = [e for e in events if _direct_key_matches(e[2], rid)]
        live = [e for e in evs if e[3] != "delete"]
        if live:
            out[rid] = {"adopted": live[0][0], "origin": live[0][1], "match": "direct",
                        "edits": sorted({e[0] for e in live if e[3] == "edit"})}
            continue
        best, best_ratio = None, 0.0
        for cand in candidates:
            ratio = difflib.SequenceMatcher(None, cand[2], text).ratio()
            if ratio >= MATCH_THRESHOLD and ratio > best_ratio:
                best, best_ratio = cand, ratio
        if best:
            out[rid] = {"adopted": best[0], "origin": best[1], "match": "text", "edits": []}
        else:
            out[rid] = {"adopted": None, "origin": "", "match": "unknown", "edits": []}
    return out


# ---------------------------------------------------------------- 사실 목록 갱신

def _text_since(adoption: dict, default: str) -> str:
    edits = adoption.get("edits") or []
    return edits[-1] if edits else (adoption.get("adopted") or default)


def update_registry(reg: dict, rules: Dict[str, str], day: date, adoptions: dict) -> List[str]:
    """reg["rules"] 를 현재 rules 에 맞춘다. 새로 생기거나 문구가 바뀌거나 사라진 id 를 돌려준다."""
    iso = day.isoformat()
    entries = reg.setdefault("rules", {})
    changed: List[str] = []
    for rid, text in rules.items():
        ad = adoptions.get(rid) or {}
        entry = entries.get(rid)
        if entry is None or entry.get("removed"):
            entries[rid] = {
                "id": rid, "text": text, "adopted": ad.get("adopted") or iso,
                "text_since": _text_since(ad, iso), "origin": ad.get("origin", ""),
                "removed": None, "match": ad.get("match", "unknown"),
            }
            changed.append(rid)
            continue
        if entry.get("text") != text:
            entry["text"] = text
            entry["text_since"] = iso
            changed.append(rid)
        if ad.get("adopted") and (entry.get("match") == "unknown" or not entry.get("adopted")):
            entry["adopted"] = ad["adopted"]
            entry["origin"] = ad.get("origin", "")
            entry["match"] = ad.get("match", entry.get("match"))
    for rid, entry in entries.items():
        if rid not in rules and not entry.get("removed"):
            entry["removed"] = iso
            changed.append(rid)
    reg["updated"] = rsi_common.now().isoformat(timespec="seconds")
    return changed


def _registry_path() -> Path:
    return rsi_common.kit_dir() / "registry.json"


def load_registry() -> dict:
    reg = rsi_common.load_json(_registry_path(), None)
    if not isinstance(reg, dict) or not isinstance(reg.get("rules"), dict):
        return {"rules": {}, "updated": None}
    reg.setdefault("updated", None)
    return reg


def save_registry(reg: dict) -> None:
    rsi_common.save_json(_registry_path(), reg)
