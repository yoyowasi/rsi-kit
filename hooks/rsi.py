"""RSI kit 진입 스크립트: init · collect · judge · status · hook-session-start · worker."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rsi_common  # noqa: E402
import rsi_inbox  # noqa: E402
import rsi_registry  # noqa: E402
import rsi_review  # noqa: E402
import rsi_spec  # noqa: E402
from rsi_common import claude_dir, kit_dir, log, now, today, vault_dir  # noqa: E402
from rsi_judge import judge_pending, run_judge  # noqa: E402
from rsi_model import AuthError  # noqa: E402
from rsi_spec import SpecError, SpecMissing, load_spec  # noqa: E402
from rsi_transcript import collect, load_excerpts, store_excerpts  # noqa: E402

LOCK_STALE_SECONDS = 2 * 3600
EPOCH = date(2000, 1, 1)
BUSY = "다른 작업자가 실행 중"


# ---------------------------------------------------------------- 수집

def _current_rules() -> dict:
    rules = {}
    try:
        text = (claude_dir() / "CLAUDE.md").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        text = ""
    rules.update(rsi_registry.read_global_rules(text))
    rules.update(rsi_registry.read_memory_rules(claude_dir() / "projects"))
    return rules


def _adoptions(rules: dict) -> dict:
    vault = vault_dir()
    if vault is not None and Path(vault).is_dir():
        return rsi_registry.match_adoptions(rules, rsi_registry.adoption_records(vault))
    return {}


def _update_registry() -> Tuple[dict, dict]:
    rules = _current_rules()
    registry = rsi_registry.load_registry()
    rsi_registry.update_registry(registry, rules, today(), _adoptions(rules))
    rsi_registry.save_registry(registry)
    return registry, rules


def _collect(state: dict, since: float) -> Tuple[int, dict]:
    started = time.time()
    written = store_excerpts(collect(since))
    state["collected_until"] = started
    registry, _ = _update_registry()
    state["last_collect"] = today().isoformat()
    return written, registry


def _run_collect() -> int:
    state = rsi_inbox.load_state()
    written, registry = _collect(state, state.get("collected_until") or 0)
    rsi_inbox.save_state(state)
    print("수집 완료: 새 발췌 %d개, 규칙 %d개" % (written, len(registry["rules"])))
    return 0


def _run_init() -> int:
    state = rsi_inbox.load_state()
    _collect(state, 0)
    rsi_inbox.save_state(state)
    registry = rsi_registry.load_registry()
    path = rsi_spec.write_draft(registry)
    active = {k: v for k, v in registry["rules"].items() if not v.get("removed")}
    unknown = [k for k, v in active.items() if v.get("match") == "unknown"]
    print("발췌 %d개, 규칙 %d개" % (len(load_excerpts(EPOCH)), len(active)))
    print("채택일을 못 찾은(match: unknown) 규칙: %s" % (", ".join(unknown) if unknown else "없음"))
    print("~/.rsi-kit/spec/rules.draft.json을 확인·수정한 뒤 rules.json으로 이름을 바꾸세요 (%s)" % path)
    return 0


# ---------------------------------------------------------------- 판정

def _record_attempt(ok: bool) -> None:
    state = rsi_inbox.load_state()
    state["last_judge"] = today().isoformat()
    if ok:
        state["auth"] = None
    rsi_inbox.save_state(state)


def _run_judge() -> int:
    try:
        spec = load_spec()
    except SpecError as e:
        print("판정 기준 없음: %s" % e)
        return 0
    state = rsi_inbox.load_state()
    rsi_inbox.read_decisions(state)
    rsi_inbox.save_state(state)
    registry = rsi_registry.load_registry()
    excerpts = load_excerpts(EPOCH)
    # 모델을 부르기 시작한 뒤에는 AuthError 가 아니면 어떻게 끝나든 시도일을 남긴다(다음 세션마다 다시 돌지 않게).
    called = ok = False
    try:
        called = True
        judged, pending = judge_pending(spec, registry, excerpts)
        verdicts = run_judge(rsi_review.calibration())
        mode = rsi_review.review_mode(spec["prompt_hash"])
        if mode:
            cache = [r for r in rsi_common.read_jsonl(kit_dir() / "judgments.jsonl")
                     if r.get("judge_hash") == spec["judge_hash"] and r.get("rule") not in ("*", "!")]
            size = spec["thresholds"]["review_size" if mode == "full" else "review_per_run"]
            rule_texts = {rid: r.get("text", "") for rid, r in (registry.get("rules") or {}).items()}
            rsi_review.make_review(today(), spec["prompt_hash"], cache, excerpts, size, mode, rule_texts)
        path = rsi_inbox.publish(verdicts, registry, spec, len(excerpts))
        ok = True
    except AuthError as e:
        called = False  # 로그인이 풀린 것은 시도로 치지 않는다(로그인하면 바로 다시)
        state = rsi_inbox.load_state()
        state["auth"] = now().isoformat(timespec="minutes")
        rsi_inbox.save_state(state)
        log("판정 중단: CLI 로그인 필요 (%s)" % e)
        print("CLI 로그인이 필요합니다: 터미널에서 claude auth login")
        return 0
    finally:
        if called:
            try:
                _record_attempt(ok)
            except Exception as e:  # noqa: BLE001 — 원래 예외를 가리지 않는다
                log("판정 시도일 기록 실패: %s: %s" % (type(e).__name__, e))
    failed = (verdicts.get("progress") or {}).get("failed", 0)
    print("판정 완료: 이번에 %d개 판정, 남은 발췌 %d개, 판정 실패 %d개%s" % (
        judged, pending, failed, (", 인박스 " + path.name) if path else ""))
    return 0


# ---------------------------------------------------------------- 상태

def cmd_status() -> int:
    state = rsi_inbox.load_state()
    print("마지막 수집: %s" % (state.get("last_collect") or "없음"))
    print("마지막 판정: %s" % (state.get("last_judge") or "없음"))
    print("발췌 수: %d" % len(load_excerpts(EPOCH)))
    cal = rsi_review.calibration()
    def pct(v):
        return "%d%%" % round(v * 100) if v is not None else "-"

    print("보정: %s (일치율 %s, 교정 판정 정확도 %s (%d건), 놓친 교정 비율 %s (%d건), 응답 %d건, 확인 대기 %d건)" % (
        "완료" if cal.get("calibrated") else "전", pct(cal.get("agreement")),
        pct(cal.get("yes_precision")), cal.get("yes_answered", 0), pct(cal.get("miss_rate")), cal.get("no_answered", 0),
        cal.get("answered", 0), cal.get("pending", 0)))
    try:
        spec = load_spec()
    except SpecError as e:
        print("판정 기준 없음: %s" % e)
    else:
        verdicts = sorted((kit_dir() / "verdicts").glob("*.json"))
        latest = rsi_common.load_json(verdicts[-1], {}) if verdicts else {}
        prog = latest.get("progress") or {}
        print("판정 진행: 판정함 %s, 남음 %s, 실패 %s" % (
            prog.get("judged", "-"), prog.get("pending", "-"), prog.get("failed", "-")))
        rules = latest.get("rules") or {}
        for rid in spec["rules"]:
            rec = rules.get(rid)
            print("  %s: %s" % (rid, ("%s (교정 %s·기회 %s 세션)" % (
                rec.get("verdict"), rec.get("correction_sessions"), rec.get("opportunity_sessions")))
                if rec else "판정 전"))
        for rid, entry in (rsi_registry.load_registry().get("rules") or {}).items():
            if not entry.get("removed") and rid not in spec["rules"]:
                print("  %s: 대장에 없음" % rid)
    if state.get("auth"):
        print("CLI 로그인 필요: 터미널에서 claude auth login (%s부터)" % state["auth"])
    return 0


# ---------------------------------------------------------------- hook·작업자

def _due(state: dict, day: date, spec_ok: bool, every: int) -> Tuple[bool, bool]:
    collect_due = state.get("last_collect") != day.isoformat()
    judge_due = False
    if spec_ok:
        try:
            last = date.fromisoformat(str(state.get("last_judge"))[:10]) if state.get("last_judge") else None
        except ValueError:
            last = None
        judge_due = last is None or (day - last).days >= every
    return collect_due, judge_due


def _evaluate_due(report: bool = False) -> Tuple[bool, bool]:
    """report=True(작업자)면 rules.json 이 깨진 것을 로그에 한 줄 남긴다. 없는 것은 정상이라 남기지 않는다.
    hook 은 세션마다 돌므로 남기지 않는다(작업자는 수집 때문에 하루 한 번은 돈다)."""
    try:
        every = int(load_spec()["thresholds"]["judge_every_days"])
        spec_ok = True
    except SpecMissing:
        every, spec_ok = rsi_spec.DEFAULT_THRESHOLDS["judge_every_days"], False
    except (SpecError, ValueError, TypeError, KeyError) as e:
        every, spec_ok = rsi_spec.DEFAULT_THRESHOLDS["judge_every_days"], False
        if report:
            log("판정 기준 오류로 판정 건너뜀: %s" % e)
    return _due(rsi_inbox.load_state(), today(), spec_ok, every)


def cmd_hook() -> int:
    try:
        sys.stdin.read()
    except Exception:
        pass
    collect_due, judge_due = _evaluate_due()
    if (collect_due or judge_due) and not os.environ.get("RSI_NO_SPAWN"):
        rsi_common.spawn_background(Path(__file__).resolve(), "worker")
    return 0


def _acquire_lock(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stale = time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS
            except OSError:
                stale = True  # 그 사이 사라졌다
            if attempt == 1 and stale:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            return False
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        return True
    return False


def _locked(fn, quiet: bool = False) -> int:
    """worker.lock 을 잡고 fn 을 돌린다. 수동 명령(init·collect·judge)과 작업자가 같은 잠금을 쓴다
    (수동 judge 가 작업자와 겹쳐 모델을 두 번 부르거나, init 이 첫 수집과 겹쳐 발췌가 두 번 쓰이지 않게)."""
    lock = kit_dir() / "worker.lock"
    if not _acquire_lock(lock):
        log("잠금: " + BUSY)
        if not quiet:
            print(BUSY)
        return 0
    try:
        return fn()
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _run_worker() -> int:
    collect_due, judge_due = _evaluate_due(report=True)
    if collect_due:
        _run_collect()
    if judge_due:
        _run_judge()
    return 0


def cmd_init() -> int:
    return _locked(_run_init)


def cmd_collect() -> int:
    return _locked(_run_collect)


def cmd_judge() -> int:
    return _locked(_run_judge)


def cmd_worker() -> int:
    return _locked(_run_worker, quiet=True)


# ---------------------------------------------------------------- 진입

COMMANDS = {"init": cmd_init, "collect": cmd_collect, "judge": cmd_judge, "status": cmd_status,
            "hook-session-start": cmd_hook, "worker": cmd_worker}
QUIET = ("hook-session-start", "worker")


def main(argv: List[str]) -> int:
    if os.environ.get("RSI_KIT"):
        return 0
    cmd = argv[0] if argv else ""
    fn = COMMANDS.get(cmd)
    if fn is None:
        print("사용법: rsi.py {%s}" % "|".join(COMMANDS))
        return 2
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log("%s 실패: %s: %s" % (cmd, type(e).__name__, e))
        if cmd not in QUIET:
            print("오류: %s" % e)
        return 0 if cmd == "hook-session-start" else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main(sys.argv[1:]))
