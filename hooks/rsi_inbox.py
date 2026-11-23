"""RSI kit 인박스: 주간 판정 결과를 볼트 00_Inbox 에 dream 제안 파일 형식으로 쓰고, 결정을 기억한다."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import rsi_common
from rsi_common import atomic_write, kit_dir, load_json, now, save_json, today, vault_dir
from rsi_review import pending_review
from rsi_spec import DEFAULT_THRESHOLDS, draft_rule

QUOTE_MAX = 60
TITLE = "dream 규칙 효과 판정"
RESULT_HEAD = re.compile(r"^## 처리 결과")
DECISION_KEYS = ("반영", "거절", "보류")


# ---------------------------------------------------------------- 상태

def _state_path() -> Path:
    return kit_dir() / "state.json"


def load_state() -> dict:
    raw = load_json(_state_path(), None)
    state = raw if isinstance(raw, dict) else {}
    state.setdefault("last_collect", None)
    state.setdefault("last_judge", None)
    state.setdefault("collected_until", None)
    state.setdefault("last_spec_hash", None)
    if not isinstance(state.get("proposals"), dict):
        state["proposals"] = {}
    state.setdefault("review_notice", None)
    if not isinstance(state.get("unregistered_notified"), list):
        state["unregistered_notified"] = []
    state.setdefault("auth", None)
    return state


def save_state(state: dict) -> None:
    save_json(_state_path(), state)


def proposal_key(kind: str, rule_id: str, text_since: str) -> str:
    return "%s:%s@%s" % (kind, rule_id, text_since)


def _key_rule_id(key: str) -> str:
    return key.split(":", 1)[-1].rsplit("@", 1)[0]


# ---------------------------------------------------------------- 결정 읽기

def parse_result_line(line: str) -> Dict[int, str]:
    """`반영: 1, 3 · 거절: 2 · 보류: 4` → {1:"반영", 3:"반영", 2:"거절", 4:"보류"}.

    대괄호·괄호 안과 `→` 뒤는 지우고 항목마다 맨 앞 정수만 읽는다."""
    text = re.sub(r"\[[^\]]*\]", "", line)
    prev = None
    while prev != text:  # 안쪽 괄호부터 반복해서 지운다
        prev = text
        text = re.sub(r"\([^()]*\)", "", text)
    out: Dict[int, str] = {}
    for m in re.finditer(r"(반영|거절|보류):\s*([^·\n]*)", text):
        for item in m.group(2).split(","):
            item = item.split("→")[0].strip()
            n = re.match(r"(\d+)", item)
            if n:
                out[int(n.group(1))] = m.group(1)
    return out


def _file_decisions(path: Path) -> Dict[int, str]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    out: Dict[int, str] = {}
    inside = False
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            inside = bool(RESULT_HEAD.match(line))
            continue
        if inside:
            out.update(parse_result_line(line))
    return out


def read_decisions(state: dict) -> None:
    vault = vault_dir()
    if vault is None:
        return
    cache: Dict[str, Dict[int, str]] = {}
    for p in state.get("proposals", {}).values():
        if p.get("decision") or p.get("number") is None or not p.get("file"):
            continue
        name = p["file"]
        if name not in cache:
            cache[name] = {}
            for folder in ("00_Inbox", "90_Archive"):
                f = Path(vault) / folder / name
                if f.is_file():
                    cache[name] = _file_decisions(f)
                    break
        d = cache[name].get(p["number"])
        if d:
            p["decision"] = d


# ---------------------------------------------------------------- 새 제안 고르기

def select_new(verdicts: dict, state: dict, th: dict) -> Tuple[List[dict], List[dict]]:
    known = state.get("proposals", {})
    cap = int(th.get("max_proposals", 5))
    removes: List[dict] = []
    hooks: List[dict] = []
    for rid, rec in (verdicts.get("rules") or {}).items():
        reco = rec.get("recommend")
        if reco == "remove":
            kind = "remove"
        elif isinstance(reco, str) and reco.startswith("hook:"):
            kind = "hook"
        else:
            continue
        since = rec.get("text_since") or ""
        key = proposal_key(kind, rid, since)
        if key in known:
            continue
        item = {"id": rid, "kind": kind, "text_since": since, "key": key, "rec": rec,
                "enforce": reco[5:] if kind == "hook" else None}
        (removes if kind == "remove" else hooks).append(item)
    order = lambda it: (-int(it["rec"].get("correction_sessions") or 0), it["id"])  # noqa: E731
    removes.sort(key=order)
    hooks.sort(key=order)
    return removes[:cap], hooks[:cap]


# ---------------------------------------------------------------- 파일 내용

def _quotes(rec: dict) -> List[str]:
    return [" ".join(str(q).split())[:QUOTE_MAX] for q in (rec.get("quotes") or [])[:3]]


def _quote_line(rec: dict, uncalibrated: bool) -> str:
    line = " / ".join('"%s"' % q for q in _quotes(rec)) or "-"
    return line + (" (보정 전)" if uncalibrated else "")


def _target(rid: str) -> Tuple[str, str]:
    """(위치, 대상). 전역 `L06` → ("전역", "[L06]"), 메모리 `slug/file.md` → ("프로젝트 slug (memory: slug)", "file.md")."""
    if "/" in rid:
        slug, name = rid.split("/", 1)
        return "프로젝트 %s (memory: %s)" % (slug, slug), name
    return "전역", "[%s]" % rid


def _agreement(v: dict) -> str:
    a = v.get("agreement")
    if isinstance(a, (int, float)):
        pct = a * 100 if a <= 1 else a
        return "보정 후(일치율 %d%%)" % round(pct)
    return "보정 후"


def _cell(value) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "/").replace("\n", " ")


def _progress_line(day: date, verdicts: dict) -> str:
    """판정 기간(가장 이른 기간 시작 ~ 오늘)과 진행(남은·실패 발췌). 판정이 덜 끝났으면 여기서 보인다."""
    starts = []
    for rec in (verdicts.get("rules") or {}).values():
        try:
            starts.append(date.fromisoformat(str(rec.get("text_since"))[:10]) + timedelta(days=1))
        except ValueError:
            continue
    prog = verdicts.get("progress") or {}
    return "> 판정 기간 %s ~ %s · 판정 남음 %s개 · 판정 실패 %s개" % (
        min(starts).isoformat() if starts else "-", day.isoformat(),
        prog.get("pending", 0), prog.get("failed", 0))


def render(day: date, verdicts: dict, removes: List[dict], hooks: List[dict], review_line: Optional[str],
           unregistered: List[dict], registry: dict, excerpt_count: int, spec_changed: bool,
           history: Dict[str, str], spec_rules: Optional[dict] = None) -> str:
    stamp = now()
    name = "%s %s" % (day.isoformat(), TITLE)
    calibrated = bool(verdicts.get("calibrated"))
    meta = "> 발췌 %d개 · %s · 판정 기준 변경: %s" % (
        excerpt_count, _agreement(verdicts) if calibrated else "보정 전", "있음" if spec_changed else "없음")
    lines = ["---", "type: dream", "tags: []", "created: %sT%s" % (day.isoformat(), stamp.strftime("%H:%M")),
             "---", "# " + name, "",
             "> 🤖 RSI 킷 주간 판정이 채택 규칙의 효과를 잰 **제안**이다. 아직 어떤 규칙에도 반영되지 않았다.",
             "> /inbox 에서 전부 / 번호 / 없음 으로 승인한 것만 반영된다. hook 후보·확인할 판정·전체 판정표는 보여 주기만 한다.",
             meta, _progress_line(day, verdicts), ""]
    rules = verdicts.get("rules") or {}
    for i, it in enumerate(removes, 1):
        rec = it["rec"]
        where, target = _target(it["id"])
        no_exc = ((spec_rules or {}).get(it["id"]) or {}).get("no_exceptions")
        reason = "결정적 강제 수단 없음" if no_exc else "예외 있는 선호"
        lines += ["## %d. 빼기 · %s · 대상 %s" % (i, where, target),
                  "- 규칙: (뺌)",
                  "- 이유: 재발: 서로 다른 세션 %d개에서 같은 교정 · %s" % (rec.get("correction_sessions") or 0, reason),
                  "- 근거: " + _quote_line(rec, not calibrated), ""]
    if hooks:
        lines.append("## hook 후보")
        for it in hooks:
            q = _quotes(it["rec"])
            lines.append("- [%s] 종류: %s — 재발 %d개 세션 · %s" % (
                it["id"], it["enforce"], it["rec"].get("correction_sessions") or 0,
                '"%s"' % q[0] if q else "-"))
        lines.append("")
    if review_line:
        lines += ["## 확인할 판정", review_line, ""]
    if unregistered:
        lines.append("## 대장에 없는 규칙")
        for u in unregistered:
            text = u.get("text") or (registry.get("rules", {}).get(u["id"]) or {}).get("text", "")
            text = " ".join(str(text).split())
            draft = json.dumps(draft_rule(u["id"], text), ensure_ascii=False, separators=(",", ":"))
            lines.append("- [%s] %s — %s" % (u["id"], text, draft))
        lines.append("")
    lines += ["## 전체 판정표", "| 규칙 | 판정 | 교정 세션 | 기회 세션 | 문구 적용일 | 지난 제안·결정 |",
              "|---|---|---|---|---|---|"]
    for rid, rec in rules.items():
        last = "hook으로 강제 중" if rec.get("enforced_by") else history.get(rid)
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            _cell(rid), _cell(rec.get("verdict")), _cell(rec.get("correction_sessions")),
            _cell(rec.get("opportunity_sessions")), _cell(rec.get("text_since")), _cell(last)))
    lines.append("")
    return "\n".join(lines)


def write_inbox(day: date, text: str) -> Path:
    inbox = Path(vault_dir()) / "00_Inbox"
    inbox.mkdir(exist_ok=True)  # 볼트 폴더 자체는 만들지 않는다(설정이 틀렸으면 엉뚱한 곳에 볼트가 생긴다)
    base = "%s %s" % (day.isoformat(), TITLE)
    path, n = inbox / (base + ".md"), 2
    while path.exists():  # 같은 날 다시 쓰면 덮어쓰지 않는다
        path, n = inbox / ("%s %d.md" % (base, n)), n + 1
    if path.stem != base:
        text = text.replace("\n# " + base + "\n", "\n# " + path.stem + "\n", 1)
    atomic_write(path, text)
    return path


# ---------------------------------------------------------------- 발행

def _history(state: dict) -> Dict[str, str]:
    latest: Dict[str, dict] = {}
    for key, p in state.get("proposals", {}).items():
        rid = _key_rule_id(key)
        if rid not in latest or str(p.get("date")) >= str(latest[rid].get("date")):
            latest[rid] = p
    return {rid: "제안함 %s, %s" % (p.get("date"), p.get("decision") or "결정 대기") for rid, p in latest.items()}


def publish(verdicts: dict, registry: dict, spec: dict, excerpt_count: int,
            day: Optional[date] = None, th: Optional[dict] = None) -> Optional[Path]:
    day = day or today()
    state = load_state()
    read_decisions(state)
    new_hash = spec.get("hash")
    spec_changed = bool(state.get("last_spec_hash")) and state["last_spec_hash"] != new_hash
    path: Optional[Path] = None
    vault = vault_dir()
    if vault is not None and not Path(vault).is_dir():
        # 볼트가 없으면 쓰지도 기록하지도 않는다(기록하면 그 제안은 다시 나오지 않는다)
        rsi_common.log("볼트 폴더가 없어 인박스를 쓰지 않음: %s" % vault)
    elif vault is not None:
        th = dict(DEFAULT_THRESHOLDS, **(th or spec.get("thresholds") or {}))
        removes, hooks = select_new(verdicts, state, th)
        review_line = None
        pending = pending_review()
        notice_name = Path(pending).name if pending else None
        if pending and notice_name != state.get("review_notice"):
            # 볼트는 동기화된다. 이 PC 의 절대 경로 대신 홈 기준 경로를 쓴다.
            review_line = "확인할 판정이 있다 — ~/.rsi-kit/spec/review/%s를 에디터로 열어 맞음/틀림을 체크한다." % notice_name
        spec_ids = set((spec.get("rules") or {}).keys())
        notified = set(state.get("unregistered_notified") or [])
        unregistered = [{"id": rid, "text": r.get("text", "")}
                        for rid, r in (registry.get("rules") or {}).items()
                        if not r.get("removed") and rid not in spec_ids and rid not in notified]
        if removes or hooks or review_line or unregistered:
            history = _history(state)
            for it in removes + hooks:
                history[it["id"]] = "제안함 %s, 결정 대기" % day.isoformat()
            text = render(day, verdicts, removes, hooks, review_line, unregistered, registry,
                          excerpt_count, spec_changed, history, spec.get("rules"))
            path = write_inbox(day, text)
            for n, it in enumerate(removes, 1):
                state["proposals"][it["key"]] = {"kind": "remove", "file": path.name, "number": n,
                                                 "date": day.isoformat(), "decision": None}
            for it in hooks:
                state["proposals"][it["key"]] = {"kind": "hook", "file": path.name, "number": None,
                                                 "date": day.isoformat(), "decision": None}
            if review_line:
                state["review_notice"] = notice_name
            state["unregistered_notified"] = sorted(notified | {u["id"] for u in unregistered})
    state["last_spec_hash"] = new_hash
    save_state(state)
    return path
