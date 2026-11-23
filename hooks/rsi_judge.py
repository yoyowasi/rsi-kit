"""RSI kit 판정: 규칙별로 기간 안의 기회 세션과 교정 세션을 세어 판정을 낸다.

모델 판정은 judgments.jsonl 에 증분 캐시한다(judge_hash 가 같으면 다시 부르지 않는다).
run_judge 는 모델을 부르지 않고 캐시만으로 계산한다.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

import rsi_common
from rsi_model import AuthError, ModelTimeout, TransientError, ask_model, extract_json
from rsi_registry import load_registry
from rsi_spec import load_spec
from rsi_transcript import load_excerpts

YNU = ("yes", "no", "unknown")
QUOTE_MAX = 60


def _date(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def window_start(reg_rule: dict) -> date:
    return _date(reg_rule["text_since"]) + timedelta(days=1)


def _ex_date(e: dict) -> date:
    """발췌의 현지 날짜. ts 가 깨졌으면 date.min(어느 기간에도 들지 않음)."""
    try:
        return datetime.fromisoformat(e["ts"]).date()
    except (KeyError, TypeError, ValueError):
        return date.min


def active_rules(spec: dict, registry: dict) -> Dict[str, dict]:
    """판정 대상: spec 에 있고 registry 에서 지워지지 않았고 text_since 가 있는 규칙."""
    out = {}
    for rid, rule in spec["rules"].items():
        reg = (registry.get("rules") or {}).get(rid)
        if reg and not reg.get("removed") and reg.get("text_since"):
            out[rid] = rule
    return out


def tool_opportunity_sessions(rule: dict, excerpts: List[dict], start: date) -> Set[str]:
    opp = rule.get("opportunity") or {}
    tools = set(opp.get("tools") or [])
    pattern = opp.get("pattern") or ""
    found: Set[str] = set()
    for e in excerpts:
        if _ex_date(e) < start:
            continue
        for t in e.get("tools") or []:
            if t.get("name") in tools and re.search(pattern, t.get("arg") or "", re.I):
                found.add(e["session"])
                break
    return found


def _tool_names(tools: List[dict]) -> str:
    """도구 이름만, 이름별 개수로 접는다(처음 나온 순서): `Bash×40, Edit×3`. 명령·경로는 보내지 않는다."""
    counts: Dict[str, int] = {}
    for t in tools or []:
        name = str(t.get("name", ""))
        counts[name] = counts.get(name, 0) + 1
    return ", ".join("%s×%d" % (n, c) for n, c in counts.items()) or "-"


def _block(key: str, e: dict) -> str:
    """발췌 하나가 프롬프트에 들어가는 모양 그대로. 보내기 전에 다시 scrub 한다."""
    return "### %s [%s]\nTOOLS: %s\nA: %s\nU: %s" % (
        key, e.get("project", ""), _tool_names(e.get("tools") or []),
        rsi_common.scrub(str(e.get("a_tail", ""))), rsi_common.scrub(str(e.get("u", ""))))


def block_size(e: dict, n: int) -> int:
    """n 번째(E{n}) 발췌가 프롬프트에서 차지하는 UTF-8 바이트(줄바꿈 포함). 저장된 도구 인자는 세지 않는다."""
    return len(_block("E%d" % n, e).encode("utf-8")) + 1


def batches(excerpts: List[dict], limit: int) -> List[List[dict]]:
    out: List[List[dict]] = []
    cur: List[dict] = []
    total = 0
    for e in excerpts:
        if e.get("u") == "":
            continue
        s = block_size(e, len(cur) + 1)
        if cur and total + s > limit:
            out.append(cur)
            cur, total = [], 0
            s = block_size(e, 1)
        cur.append(e)
        total += s
    if cur:
        out.append(cur)
    return out


def excerpt_keys(batch: List[dict]) -> Dict[str, str]:
    """묶음 안의 짧은 키 → 발췌 id. 긴 id(시각:해시)를 모델이 옮겨 적다 틀리지 않게."""
    return {"E%d" % n: e["id"] for n, e in enumerate(batch, 1)}


def rule_keys(rules: Dict[str, dict]) -> Dict[str, str]:
    return {"R%d" % n: rid for n, rid in enumerate(rules, 1)}


def build_prompt(batch: List[dict], rules: Dict[str, dict], registry: dict) -> str:
    lines = ["<규칙>"]
    for key, rid in rule_keys(rules).items():
        rule = rules[rid]
        text = ((registry.get("rules") or {}).get(rid) or {}).get("text", "")
        line = "%s: %s | 교정: %s" % (key, text, rule.get("correction", ""))
        opp = rule.get("opportunity") or {}
        if opp.get("kind") == "llm":
            line += " | 적용 상황: %s" % opp.get("when", "")
        lines.append(line)
    lines.append("</규칙>")
    lines.append("<발췌>")
    lines.extend(_block(key, e) for key, e in zip(excerpt_keys(batch), batch))
    lines.append("</발췌>")
    return "\n".join(lines)


def _cache_path():
    return rsi_common.kit_dir() / "judgments.jsonl"


def _markers(judge_hash: str, kind: str) -> Set[str]:
    """kind "*" = 판정 끝난 발췌, "!" = 혼자 보내도 답이 깨져 건너뛰는 발췌."""
    return {r.get("excerpt") for r in rsi_common.read_jsonl(_cache_path())
            if r.get("rule") == kind and r.get("judge_hash") == judge_hash}


def _pending(spec: dict, registry: dict, excerpts: List[dict]) -> List[dict]:
    rules = active_rules(spec, registry)
    if not rules:
        return []
    earliest = min(window_start(registry["rules"][rid]) for rid in rules)
    done = _markers(spec["judge_hash"], "*") | _markers(spec["judge_hash"], "!")
    seen: Set[str] = set()
    out = []
    for e in excerpts:
        if e.get("u") == "" or e["id"] in done or e["id"] in seen or _ex_date(e) < earliest:
            continue
        seen.add(e["id"])
        out.append(e)
    return out


def _valid_items(answer: dict, ex_keys: Dict[str, str], r_keys: Dict[str, str], judge_hash: str) -> List[dict]:
    """모델 답의 E·R 키를 발췌 id·규칙 id 로 되돌린다. 답에 항목이 있는데 하나도 맞지 않으면 ValueError."""
    items = answer.get("items")
    if not isinstance(items, list):
        raise ValueError("답에 items 목록이 없음")
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ex_key, rule_key = it.get("excerpt"), it.get("rule")
        if not isinstance(ex_key, str) or not isinstance(rule_key, str):
            continue
        if ex_key not in ex_keys or rule_key not in r_keys:
            continue
        if it.get("opportunity") not in YNU or it.get("correction") not in YNU:
            continue
        quote = it.get("quote")
        quote = rsi_common.scrub(quote)[:QUOTE_MAX] if isinstance(quote, str) else ""
        out.append({"excerpt": ex_keys[ex_key], "rule": r_keys[rule_key], "opportunity": it["opportunity"],
                    "correction": it["correction"], "quote": quote, "judge_hash": judge_hash})
    dropped = len(items) - len(out)
    if dropped:
        rsi_common.log("판정 답 항목 버림 %d개 (받은 %d개, 묶음 %d건)" % (dropped, len(items), len(ex_keys)))
    if items and not out:
        raise ValueError("답 항목 %d개가 모두 키와 맞지 않음" % len(items))
    return out


def _judge_batch(spec: dict, rules: Dict[str, dict], registry: dict, batch: List[dict]) -> List[dict]:
    answer = extract_json(ask_model(spec["judge_prompt"], build_prompt(batch, rules, registry)))
    return _valid_items(answer, excerpt_keys(batch), rule_keys(rules), spec["judge_hash"])


def judge_pending(spec: dict, registry: dict, excerpts: List[dict]) -> Tuple[int, int]:
    """판정하지 않은 발췌를 묶음 단위로 모델에 보낸다. (이번에 판정한 수, 남은 수)를 돌려준다.

    답이 깨진 묶음(ValueError)과 시간 초과(ModelTimeout: 묶음이 커서 답이 늦음)는 반으로 나눠 이번 실행에서 다시
    보낸다(시도마다 max_batches_per_run 에 센다). 한 건짜리도 답이 깨지면 "!" 표시를 남기고 건너뛴다(남은 수에서
    빠지고 status 에 실패로 보인다). 단 이번 실행에서 성공한 묶음이 하나도 없으면 발췌 탓이 아니라 모델·CLI 쪽
    문제로 보고 "!" 표시를 남기지 않는다. 한 건짜리도 시간 초과면 표시 없이 이번 실행을 멈춘다.
    그 밖의 TransientError(사용량 한도·네트워크)는 이번 실행을 멈춘다. AuthError 는 그대로 올린다."""
    rules = active_rules(spec, registry)
    pending = _pending(spec, registry, excerpts)
    th = spec["thresholds"]
    h = spec["judge_hash"]
    queue = batches(pending, th["batch_bytes"])
    attempts = judged = failed = 0
    held: List[dict] = []  # 성공한 묶음이 나오기 전의 "!" 표시(나오면 쓴다)
    while queue and attempts < th["max_batches_per_run"]:
        batch = queue.pop(0)
        attempts += 1
        try:
            rows = _judge_batch(spec, rules, registry, batch)
        except AuthError:
            raise
        except (ModelTimeout, ValueError) as e:
            what = "시간 초과" if isinstance(e, ModelTimeout) else "답 깨짐"
            if len(batch) > 1:
                half = len(batch) // 2
                queue[:0] = [batch[:half], batch[half:]]
                rsi_common.log("판정 %s(%d건 묶음), 반으로 나눠 다시: %s" % (what, len(batch), e))
                continue
            if isinstance(e, ModelTimeout):
                rsi_common.log("판정 중단: 발췌 한 건도 시간 초과(실패 표시 안 함): %s" % batch[0]["id"])
                break
            rsi_common.log("판정 실패, 건너뜀: 발췌 %s: %s" % (batch[0]["id"], e))
            held.append({"excerpt": batch[0]["id"], "rule": "!", "judge_hash": h})
            if judged:
                rsi_common.append_jsonl(_cache_path(), held)
                failed += len(held)
                held = []
            continue
        except TransientError as e:
            rsi_common.log("판정 중단: %s" % e)
            break
        rows += [{"excerpt": e["id"], "rule": "*", "judge_hash": h} for e in batch]
        rsi_common.append_jsonl(_cache_path(), rows + held)
        judged += len(batch)
        failed += len(held)
        held = []
    if held:
        rsi_common.log("판정 답이 계속 깨짐(이번 실행 성공한 묶음 0개): 실패 표시 %d개를 남기지 않음, 다음 실행에서 다시"
                       % len(held))
    return judged, len(pending) - judged - failed


def rule_verdict(rule_id: str, rule: dict, reg_rule: dict, judgments: List[dict],
                 excerpts: List[dict], th: dict) -> dict:
    start = window_start(reg_rule)
    info = {e["id"]: e for e in excerpts if _ex_date(e) >= start}
    corr: Set[str] = set()
    opp: Set[str] = set()
    quotes: List[str] = []
    for r in judgments:
        e = info.get(r.get("excerpt"))
        if r.get("rule") != rule_id or e is None:
            continue
        if r.get("correction") == "yes":
            corr.add(e["session"])
            q = (r.get("quote") or "").strip()
            if q and len(quotes) < 3:
                quotes.append(q[:QUOTE_MAX])
        if r.get("opportunity") == "yes":
            opp.add(e["session"])
    if (rule.get("opportunity") or {}).get("kind") == "tool":
        opp = tool_opportunity_sessions(rule, excerpts, start)
    opp |= corr
    if len(corr) >= th["recur_sessions"]:
        verdict = "재발"
    elif corr:  # 교정은 있었지만 recur_sessions 에 못 미침(기본값 2 에서는 1개)
        verdict = "관찰"
    elif len(opp) >= th["kept_opportunity_sessions"]:
        verdict = "지켜짐"
    else:
        verdict = "판단 불가"
    return {"verdict": verdict, "correction_sessions": len(corr), "opportunity_sessions": len(opp),
            "quotes": quotes, "text_since": reg_rule.get("text_since")}


def recommend(rule: dict, verdict: dict) -> Optional[str]:
    if rule.get("enforced_by") or verdict.get("verdict") != "재발":
        return None
    if rule.get("no_exceptions") and rule.get("enforce") != "none":
        return "hook:%s" % rule["enforce"]
    return "remove"


def run_judge(calib: dict) -> dict:
    """모델을 부르지 않고 판정 캐시로 규칙별 판정을 계산해 verdicts/<오늘>.json 에 쓴다."""
    spec = load_spec()
    registry = load_registry()
    excerpts = load_excerpts(date(2000, 1, 1))
    rules = active_rules(spec, registry)
    h = spec["judge_hash"]
    judgments = [r for r in rsi_common.read_jsonl(_cache_path())
                 if r.get("judge_hash") == h and r.get("rule") not in ("*", "!")]
    out_rules = {}
    for rid, rule in rules.items():
        v = rule_verdict(rid, rule, registry["rules"][rid], judgments, excerpts, spec["thresholds"])
        v["recommend"] = recommend(rule, v)
        v["enforced_by"] = rule.get("enforced_by")
        out_rules[rid] = v
    record = {
        "date": rsi_common.today().isoformat(),
        "spec_hash": spec["hash"],
        "judge_hash": h,
        "calibrated": bool(calib.get("calibrated")),
        "agreement": calib.get("agreement"),
        "progress": {"judged": len(_markers(h, "*")), "pending": len(_pending(spec, registry, excerpts)),
                     "failed": len(_markers(h, "!"))},
        "rules": out_rules,
    }
    rsi_common.save_json(rsi_common.kit_dir() / "verdicts" / ("%s.json" % record["date"]), record)
    return record
