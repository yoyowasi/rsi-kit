import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_common  # noqa: E402
import rsi_judge  # noqa: E402
import rsi_model  # noqa: E402
import rsi_spec  # noqa: E402

TH = {"recur_sessions": 2, "kept_opportunity_sessions": 5, "max_proposals": 5, "review_size": 10,
      "batch_bytes": 122880, "max_batches_per_run": 12, "judge_every_days": 7}


def ex(i, session, day="2026-11-20", u="말", tools=None, project="p"):
    return {"id": i, "ts": "2026-11-%sT10:00:00+09:00" % day[-2:], "project": project, "session": session,
            "u": u, "a_tail": "답", "tools": tools or []}


def j(i, rule="R1", opp="no", corr="no", quote=""):
    return {"excerpt": i, "rule": rule, "opportunity": opp, "correction": corr, "quote": quote, "judge_hash": "h"}


def llm_rule(**kw):
    r = {"type": "step", "no_exceptions": False, "enforce": "none",
         "opportunity": {"kind": "llm", "when": "언제"}, "correction": "교정 설명", "enforced_by": None}
    r.update(kw)
    return r


REG = {"text_since": "2026-11-10"}


class VerdictTest(unittest.TestCase):
    def verdict(self, js, exs, rule=None, reg=REG):
        return rsi_judge.rule_verdict("R1", rule or llm_rule(), reg, js, exs, TH)

    def test_window_start(self):
        self.assertEqual(rsi_judge.window_start({"text_since": "2026-11-10"}), date(2026, 11, 11))

    def test_verdict_table(self):
        exs = [ex("e%d" % n, "s%d" % n) for n in range(8)]
        v = self.verdict([j("e0", corr="yes"), j("e1", corr="yes")], exs)
        self.assertEqual(v["verdict"], "재발")
        self.assertEqual(v["correction_sessions"], 2)
        v = self.verdict([j("e0", corr="yes")], exs)
        self.assertEqual(v["verdict"], "관찰")
        v = self.verdict([j("e%d" % n, opp="yes") for n in range(5)], exs)
        self.assertEqual(v["verdict"], "지켜짐")
        self.assertEqual(v["opportunity_sessions"], 5)
        v = self.verdict([j("e%d" % n, opp="yes") for n in range(4)], exs)
        self.assertEqual(v["verdict"], "판단 불가")
        v = self.verdict([j("e%d" % n, opp="unknown", corr="unknown") for n in range(8)], exs)
        self.assertEqual(v["verdict"], "판단 불가")
        self.assertEqual(v["text_since"], "2026-11-10")

    def test_same_session_counts_once(self):
        exs = [ex("e1", "s1"), ex("e2", "s1")]
        v = self.verdict([j("e1", corr="yes", quote="가"), j("e2", corr="yes", quote="나")], exs)
        self.assertEqual(v["verdict"], "관찰")
        self.assertEqual(len(v["quotes"]), 2)

    def test_window_after_text_change(self):
        exs = [ex("old", "s1", day="2026-11-10"), ex("new", "s2", day="2026-11-12")]
        v = self.verdict([j("old", corr="yes"), j("new", corr="yes")], exs)
        self.assertEqual(v["correction_sessions"], 1)
        self.assertEqual(v["verdict"], "관찰")

    def test_other_rule_ignored_and_quotes_cut(self):
        exs = [ex("e%d" % n, "s%d" % n) for n in range(5)]
        js = [j("e%d" % n, corr="yes", quote="가" * 100) for n in range(5)] + [j("e0", rule="R2", corr="yes")]
        v = self.verdict(js, exs, reg=REG)
        self.assertEqual(len(v["quotes"]), 3)
        self.assertEqual(len(v["quotes"][0]), 60)

    def test_tool_opportunity(self):
        rule = llm_rule(opportunity={"kind": "tool", "tools": ["Bash"], "pattern": r"gh\s+pr\s+merge"})
        exs = [ex("a", "s1", tools=[{"name": "Bash", "arg": "gh pr merge 3"}]),
               ex("b", "s2", tools=[{"name": "Bash", "arg": "gh pr view"}]),
               ex("c", "s3", tools=[{"name": "Read", "arg": "gh pr merge"}]),
               ex("d", "s4", day="2026-11-09", tools=[{"name": "Bash", "arg": "gh pr merge 1"}])]
        got = rsi_judge.tool_opportunity_sessions(rule, exs, date(2026, 11, 11))
        self.assertEqual(got, {"s1"})

    def test_recommend(self):
        recur = {"verdict": "재발"}
        r = llm_rule
        self.assertEqual(rsi_judge.recommend(r(no_exceptions=True, enforce="pretooluse"), recur), "hook:pretooluse")
        self.assertEqual(rsi_judge.recommend(r(no_exceptions=True, enforce="none"), recur), "remove")
        self.assertEqual(rsi_judge.recommend(r(), recur), "remove")
        self.assertIsNone(rsi_judge.recommend(r(enforced_by="rsi-l06"), recur))
        self.assertIsNone(rsi_judge.recommend(r(), {"verdict": "관찰"}))


class PendingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"RSI_KIT_HOME": self.tmp.name, "RSI_CLAUDE_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.spec = {"rules": {"R1": llm_rule()}, "thresholds": dict(TH), "judge_prompt": "SYS", "hash": "h1",
                     "judge_hash": "j1"}
        self.registry = {"rules": {"R1": {"id": "R1", "text": "규칙문", "text_since": "2026-11-10", "removed": None}}}

    def ask(self, system, prompt, **kw):
        keys = [l.split()[1] for l in prompt.splitlines() if l.startswith("### ")]
        return json.dumps({"items": [{"excerpt": k, "rule": "R1", "opportunity": "yes", "correction": "no", "quote": ""}
                                     for k in keys]})

    def cache(self):
        return rsi_common.read_jsonl(rsi_common.kit_dir() / "judgments.jsonl")

    def marks(self, kind="*"):
        return sorted(r["excerpt"] for r in self.cache() if r["rule"] == kind)

    def log_text(self):
        p = Path(self.tmp.name) / "hooks" / "rsi.log"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def test_prompt_has_no_args(self):
        e = ex("e1", "s1", tools=[{"name": "Bash", "arg": "gh pr merge 3"}])
        p = rsi_judge.build_prompt([e], self.spec["rules"], self.registry)
        self.assertNotIn("gh pr merge", p)
        self.assertIn("TOOLS: Bash×1", p)
        self.assertIn("### E1 [p]", p)
        self.assertNotIn("e1", p)  # 긴 발췌 id 는 보내지 않는다
        self.assertIn("R1: 규칙문", p)
        self.assertIn("<규칙>", p)
        self.assertIn("<발췌>", p)
        self.assertIn("TOOLS: -", rsi_judge.build_prompt([ex("e2", "s")], self.spec["rules"], self.registry))

    def test_prompt_short_keys_and_collapsed_tools(self):
        rules = {"L06": llm_rule(correction="병합 지적"), "L01": llm_rule(correction="목록 지적")}
        reg = {"rules": {"L06": {"text": "병합 규칙", "text_since": "2026-11-10"},
                         "L01": {"text": "목록 규칙", "text_since": "2026-11-10"}}}
        tools = [{"name": "Bash", "arg": "x"}] * 40 + [{"name": "Edit", "arg": "a"}] * 3 + [{"name": "Bash", "arg": "y"}]
        p = rsi_judge.build_prompt([ex("2026-11-20T01:00:00Z:aaaa", "s", tools=tools), ex("b", "s")], rules, reg)
        self.assertIn("TOOLS: Bash×41, Edit×3", p)
        self.assertIn("### E1 [p]", p)
        self.assertIn("### E2 [p]", p)
        self.assertIn("R1: 병합 규칙 | 교정: 병합 지적", p)
        self.assertIn("R2: 목록 규칙 | 교정: 목록 지적", p)
        self.assertNotIn("L06", p)
        self.assertNotIn("aaaa", p)

    def test_prompt_scrubbed(self):
        e = ex("e1", "s1", u="비번은 password=hunter22 야")
        e["a_tail"] = "token=abcd1234 로 했어요"
        p = rsi_judge.build_prompt([e], self.spec["rules"], self.registry)
        self.assertNotIn("hunter22", p)
        self.assertNotIn("abcd1234", p)

    def test_batches_skip_empty_u_and_limit(self):
        exs = [ex("a", "s"), ex("b", "s", u=""), ex("c", "s")]
        one = rsi_judge.block_size(exs[0], 1)
        bs = rsi_judge.batches(exs, one + 1)
        self.assertEqual([[e["id"] for e in b] for b in bs], [["a"], ["c"]])

    def test_tool_args_do_not_inflate_batches(self):
        # 보내지 않는 도구 인자는 묶음 크기에 넣지 않는다
        tools = [{"name": "Bash", "arg": "x" * 200}] * 40
        exs = [ex("e%d" % n, "s%d" % n, tools=tools) for n in range(10)]
        one_row = len(json.dumps(exs[0], ensure_ascii=False).encode("utf-8"))
        prompt_bytes = sum(rsi_judge.block_size(e, n) for n, e in enumerate(exs, 1))
        self.assertLess(prompt_bytes, one_row)
        self.assertEqual(len(rsi_judge.batches(exs, prompt_bytes)), 1)

    def test_cache_and_cap(self):
        self.spec["thresholds"]["max_batches_per_run"] = 1
        exs = [ex("e%d" % n, "s%d" % n) for n in range(3)]
        self.spec["thresholds"]["batch_bytes"] = rsi_judge.block_size(exs[0], 1) * 2 + 1  # 묶음당 2건
        with mock.patch("rsi_judge.ask_model", side_effect=self.ask) as m:
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (2, 1))
            self.assertEqual(m.call_count, 1)
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (1, 0))
            self.assertEqual(m.call_count, 2)
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 0))
            self.assertEqual(m.call_count, 2)
        self.assertEqual(self.marks(), ["e0", "e1", "e2"])
        self.assertTrue(all(r["judge_hash"] == "j1" for r in self.cache()))

    def test_old_excerpts_not_sent(self):
        exs = [ex("old", "s", day="2026-11-10"), ex("new", "s2", day="2026-11-11")]
        with mock.patch("rsi_judge.ask_model", side_effect=self.ask):
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (1, 0))

    def test_invalid_items_dropped(self):
        def ask(system, prompt, **kw):
            return json.dumps({"items": [
                {"excerpt": "E1", "rule": "R1", "opportunity": "yes", "correction": "yes", "quote": "가" * 100},
                {"excerpt": "zz", "rule": "R1", "opportunity": "yes", "correction": "no", "quote": ""},
                {"excerpt": "E1", "rule": "RX", "opportunity": "yes", "correction": "no", "quote": ""},
                {"excerpt": "E1", "rule": "R1", "opportunity": "maybe", "correction": "no", "quote": ""}]})
        with mock.patch("rsi_judge.ask_model", side_effect=ask):
            rsi_judge.judge_pending(self.spec, self.registry, [ex("e1", "s1")])
        items = [r for r in self.cache() if r["rule"] != "*"]
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["excerpt"], items[0]["rule"]), ("e1", "R1"))
        self.assertEqual(len(items[0]["quote"]), 60)
        self.assertIn("버림 3", self.log_text())  # 버린 항목 수를 남긴다

    def test_unhashable_ids_and_scrubbed_quote(self):
        def ask(system, prompt, **kw):
            return json.dumps({"items": [
                {"excerpt": ["E1"], "rule": "R1", "opportunity": "yes", "correction": "no", "quote": ""},
                {"excerpt": "E1", "rule": {"x": 1}, "opportunity": "yes", "correction": "no", "quote": ""},
                "문자열",
                {"excerpt": "E1", "rule": "R1", "opportunity": "no", "correction": "yes",
                 "quote": "password=hunter22 라고 했잖아"}]})
        with mock.patch("rsi_judge.ask_model", side_effect=ask):
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, [ex("e1", "s1")]), (1, 0))
        items = [r for r in self.cache() if r["rule"] != "*"]
        self.assertEqual(len(items), 1)
        self.assertNotIn("hunter22", items[0]["quote"])

    def test_short_keys_map_back(self):
        spec = dict(self.spec, rules={"L06": llm_rule(), "L01": llm_rule()})
        reg = {"rules": {"L06": {"text": "a", "text_since": "2026-11-10"},
                         "L01": {"text": "b", "text_since": "2026-11-10"}}}
        exs = [ex("2026-11-20T01:00:00Z:aaaa1111", "s1"), ex("2026-11-20T02:00:00Z:bbbb2222", "s2")]

        def ask(system, prompt, **kw):
            return json.dumps({"items": [{"excerpt": "E2", "rule": "R2", "opportunity": "yes", "correction": "yes",
                                          "quote": "q"}]})
        with mock.patch("rsi_judge.ask_model", side_effect=ask):
            self.assertEqual(rsi_judge.judge_pending(spec, reg, exs), (2, 0))
        items = [(r["excerpt"], r["rule"]) for r in self.cache() if r["rule"] not in ("*", "!")]
        self.assertEqual(items, [("2026-11-20T02:00:00Z:bbbb2222", "L01")])

    def test_empty_items_is_a_valid_answer(self):
        with mock.patch("rsi_judge.ask_model", return_value='{"items": []}'):
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, [ex("e1", "s1")]), (1, 0))
        self.assertEqual(self.marks(), ["e1"])

    def test_unmapped_answer_is_not_marked_done(self):
        # 모델이 E 키 대신 번호로 답하면 하나도 맞지 않는다 → 그 묶음은 실패(완료 표시 없음)
        exs = [ex("e1", "s1"), ex("e2", "s2")]

        def ask(system, prompt, **kw):
            n = len([l for l in prompt.splitlines() if l.startswith("### ")])
            return json.dumps({"items": [{"excerpt": str(i), "rule": "R1", "opportunity": "yes", "correction": "yes",
                                          "quote": ""} for i in range(1, n + 1)]})
        with mock.patch("rsi_judge.ask_model", side_effect=ask) as m:
            rsi_judge.judge_pending(self.spec, self.registry, exs)
            self.assertEqual(m.call_count, 3)  # 2건 묶음 실패 → 1건씩 다시
        self.assertEqual(self.marks("*"), [])
        self.assertEqual([r for r in self.cache() if r["rule"] not in ("*", "!")], [])
        self.assertIn("버림 2", self.log_text())

    def test_failing_batch_is_split_and_isolated(self):
        exs = [ex("e0", "s0"), ex("e1", "s1"), ex("e2", "s2", u="나쁜"), ex("e3", "s3")]

        def ask(system, prompt, **kw):
            if "U: 나쁜" in prompt:
                return "설명만 하고 JSON 은 없음"
            return self.ask(system, prompt)
        with mock.patch("rsi_judge.ask_model", side_effect=ask) as m:
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (3, 0))
            self.assertEqual(m.call_count, 5)  # 4건 실패 → 2+2, 뒤 2건 실패 → 1+1
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 0))
            self.assertEqual(m.call_count, 5)  # 실패 표시한 발췌는 다시 보내지 않는다
        self.assertEqual(self.marks("*"), ["e0", "e1", "e3"])
        self.assertEqual(self.marks("!"), ["e2"])
        self.assertTrue(all(r["judge_hash"] == "j1" for r in self.cache()))
        self.assertIn("e2", self.log_text())

    def test_split_attempts_count_toward_cap(self):
        self.spec["thresholds"]["max_batches_per_run"] = 3
        exs = [ex("e0", "s0"), ex("e1", "s1"), ex("e2", "s2", u="나쁜"), ex("e3", "s3")]

        def ask(system, prompt, **kw):
            if "U: 나쁜" in prompt:
                return '{"items": [{"excerpt": "E1"'  # 잘린 답
            return self.ask(system, prompt)
        with mock.patch("rsi_judge.ask_model", side_effect=ask) as m:
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (2, 2))
            self.assertEqual(m.call_count, 3)  # 4건 실패 → 2건 성공 → 2건 실패(나눈 뒤 상한)
            # e2 가 먼저 실패해도(아직 성공 0) 같은 실행에서 e3 가 성공하면 실패 표시를 남긴다
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (1, 0))
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 0))
            self.assertEqual(m.call_count, 6)
        self.assertEqual(self.marks("!"), ["e2"])

    def test_zero_success_run_leaves_no_failed_markers(self):
        # 모든 답이 깨지면 발췌 탓이 아니라 모델·CLI 쪽 문제로 본다: 실패 표시 없이 멈추고 다음 실행에서 다시
        exs = [ex("e%d" % n, "s%d" % n) for n in range(4)]
        with mock.patch("rsi_judge.ask_model", return_value="설명만 하고 JSON 은 없음") as m:
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 4))
            self.assertEqual(m.call_count, 7)  # 4 → 2+2 → 1+1, 1+1
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 4))
            self.assertEqual(m.call_count, 14)
        self.assertEqual(self.cache(), [])
        self.assertIn("성공한 묶음 0개", self.log_text())

    def test_timeout_splits_batch(self):
        # 묶음이 크면 답이 늦어 시간 초과가 난다 → 반으로 나눠 이번 실행에서 다시
        exs = [ex("e%d" % n, "s%d" % n) for n in range(6)]

        def ask(system, prompt, **kw):
            if len([l for l in prompt.splitlines() if l.startswith("### ")]) > 2:
                raise rsi_model.ModelTimeout("claude -p 시간 초과(240초)")
            return self.ask(system, prompt)
        with mock.patch("rsi_judge.ask_model", side_effect=ask) as m:
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (6, 0))
            self.assertEqual(m.call_count, 7)  # 6 → 3+3, 3 → 1+2, ..., 3 → 1+2
        self.assertEqual(self.marks("*"), ["e%d" % n for n in range(6)])
        self.assertEqual(self.marks("!"), [])
        self.assertIn("시간 초과", self.log_text())

    def test_single_excerpt_timeout_stops_without_marker(self):
        exs = [ex("e1", "s1"), ex("e2", "s2", u="느린"), ex("e3", "s3")]
        self.spec["thresholds"]["batch_bytes"] = rsi_judge.block_size(exs[1], 1) * 2 + 1  # 묶음당 2건

        def ask(system, prompt, **kw):
            if "U: 느린" in prompt:
                raise rsi_model.ModelTimeout("claude -p 시간 초과(240초)")
            return self.ask(system, prompt)
        with mock.patch("rsi_judge.ask_model", side_effect=ask) as m:
            # [e1,e2] 시간 초과 → [e1] 성공 → [e2] 시간 초과 → 이번 실행 멈춤([e3] 은 다음에)
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (1, 2))
            self.assertEqual(m.call_count, 3)
        self.assertEqual(self.marks("*"), ["e1"])
        self.assertEqual(self.marks("!"), [])

    def test_rate_limit_stops_without_split(self):
        exs = [ex("e1", "s1"), ex("e2", "s2")]
        with mock.patch("rsi_judge.ask_model", side_effect=rsi_model.TransientError("사용량 한도")) as m:
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 2))
            self.assertEqual(m.call_count, 1)
        self.assertEqual(self.cache(), [])

    def test_corrupt_ts_skipped(self):
        bad = ex("bad", "s9")
        bad["ts"] = "깨진값"
        exs = [bad, ex("e1", "s1")]
        with mock.patch("rsi_judge.ask_model", side_effect=self.ask):
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (1, 0))
        v = rsi_judge.rule_verdict("R1", llm_rule(), REG, [j("bad", corr="yes")], exs, TH)
        self.assertEqual(v["correction_sessions"], 0)

    def test_judge_hash_change_rejudges(self):
        exs = [ex("e1", "s1")]
        with mock.patch("rsi_judge.ask_model", side_effect=self.ask) as m:
            rsi_judge.judge_pending(self.spec, self.registry, exs)
            self.spec["hash"] = "h2"  # 전체 해시(표시용)만 바뀌면 다시 묻지 않는다
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 0))
            self.assertEqual(m.call_count, 1)
            self.spec["judge_hash"] = "j2"
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (1, 0))
            self.assertEqual(m.call_count, 2)

    def test_auth_error_keeps_progress(self):
        exs = [ex("e%d" % n, "s%d" % n) for n in range(2)]
        self.spec["thresholds"]["batch_bytes"] = rsi_judge.block_size(exs[0], 1) + 1
        calls = []

        def ask(system, prompt, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise rsi_model.AuthError("로그인")
            return self.ask(system, prompt)
        with mock.patch("rsi_judge.ask_model", side_effect=ask):
            with self.assertRaises(rsi_model.AuthError):
                rsi_judge.judge_pending(self.spec, self.registry, exs)
        self.assertEqual(self.marks(), ["e0"])

    def test_transient_and_lone_bad_json_leave_nothing(self):
        exs = [ex("e1", "s1")]
        with mock.patch("rsi_judge.ask_model", side_effect=rsi_model.TransientError("x")):
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 1))
        self.assertEqual(self.cache(), [])
        with mock.patch("rsi_judge.ask_model", return_value="json 아님"):  # 성공한 묶음이 없는 실행
            self.assertEqual(rsi_judge.judge_pending(self.spec, self.registry, exs), (0, 1))
        self.assertEqual(self.cache(), [])


class JudgeHashTest(unittest.TestCase):
    """판정 캐시는 judge_hash(판정 프롬프트 + 규칙의 correction·opportunity)로 묶인다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"RSI_KIT_HOME": self.tmp.name, "RSI_CLAUDE_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spec_dir = Path(self.tmp.name) / "spec"
        self.spec_dir.mkdir()
        self.registry = {"rules": {"L06": {"id": "L06", "text": "규칙", "text_since": "2026-11-10", "removed": None}}}
        self.exs = [ex("e1", "s1"), ex("e2", "s2")]

    def write(self, rule=None, thresholds=None):
        (self.spec_dir / "rules.json").write_text(json.dumps({"rules": {"L06": rule or llm_rule()}}), encoding="utf-8")
        if thresholds is not None:
            (self.spec_dir / "thresholds.json").write_text(json.dumps(thresholds), encoding="utf-8")

    def test_threshold_and_enforcement_edits_keep_cache(self):
        self.write()
        first = rsi_spec.load_spec()
        with mock.patch("rsi_judge.ask_model", return_value='{"items": []}') as m:
            self.assertEqual(rsi_judge.judge_pending(first, self.registry, self.exs), (2, 0))
            self.assertEqual(m.call_count, 1)
            self.write(rule=llm_rule(type="restraint", no_exceptions=True, enforce="pretooluse",
                                     enforced_by="rsi-l06"),
                       thresholds={"max_batches_per_run": 20, "recur_sessions": 3})
            second = rsi_spec.load_spec()
            self.assertNotEqual(second["hash"], first["hash"])
            self.assertEqual(second["judge_hash"], first["judge_hash"])
            self.assertEqual(rsi_judge.judge_pending(second, self.registry, self.exs), (0, 0))
            self.assertEqual(m.call_count, 1)
            self.write(rule=llm_rule(correction="다른 교정 설명"))
            third = rsi_spec.load_spec()
            self.assertNotEqual(third["judge_hash"], first["judge_hash"])
            self.assertEqual(rsi_judge.judge_pending(third, self.registry, self.exs), (2, 0))
            self.assertEqual(m.call_count, 2)


class RunJudgeTest(unittest.TestCase):
    def test_run_judge_writes_record(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"RSI_KIT_HOME": tmp, "RSI_CLAUDE_DIR": tmp}):
            kit = Path(tmp)
            spec = {"rules": {"R1": llm_rule(no_exceptions=True, enforce="stop"), "R2": llm_rule()},
                    "thresholds": dict(TH), "judge_prompt": "SYS", "hash": "s1", "judge_hash": "h1",
                    "prompt_hash": "p"}
            reg = {"rules": {"R1": {"id": "R1", "text": "t", "text_since": "2026-11-10", "removed": None},
                             "R2": {"id": "R2", "text": "t", "text_since": "2026-11-10", "removed": "2026-11-15"},
                             "R3": {"id": "R3", "text": "t", "text_since": "2026-11-10", "removed": None}}}
            exs = [ex("e1", "s1"), ex("e2", "s2"), ex("e3", "s3"), ex("e4", "s4")]
            rows = [dict(j("e1", corr="yes", opp="yes"), judge_hash="h1"), dict(j("e2", corr="yes"), judge_hash="h1"),
                    dict(j("e1", corr="yes"), judge_hash="old"),
                    {"excerpt": "e1", "rule": "*", "judge_hash": "h1"}, {"excerpt": "e2", "rule": "*", "judge_hash": "h1"},
                    {"excerpt": "e3", "rule": "*", "judge_hash": "old"}, {"excerpt": "e4", "rule": "!", "judge_hash": "h1"}]
            rsi_common.append_jsonl(kit / "judgments.jsonl", rows)
            with mock.patch("rsi_judge.load_spec", return_value=spec), \
                    mock.patch("rsi_judge.load_registry", return_value=reg), \
                    mock.patch("rsi_judge.load_excerpts", return_value=exs), \
                    mock.patch("rsi_judge.ask_model", side_effect=AssertionError("모델 호출 금지")):
                rec = rsi_judge.run_judge({"calibrated": True, "agreement": 0.9})
            self.assertEqual(list(rec["rules"]), ["R1"])
            r1 = rec["rules"]["R1"]
            self.assertEqual(r1["verdict"], "재발")
            self.assertEqual(r1["recommend"], "hook:stop")
            self.assertIsNone(r1["enforced_by"])
            self.assertEqual(rec["progress"], {"judged": 2, "pending": 1, "failed": 1})
            self.assertEqual(rec["spec_hash"], "s1")
            self.assertEqual(rec["judge_hash"], "h1")
            self.assertTrue(rec["calibrated"])
            self.assertEqual(rec["agreement"], 0.9)
            path = kit / "verdicts" / ("%s.json" % rsi_common.today().isoformat())
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), rec)


if __name__ == "__main__":
    unittest.main()
