import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_common  # noqa: E402
import rsi_spec  # noqa: E402

GOOD = {"type": "step", "no_exceptions": False, "enforce": "none",
        "opportunity": {"kind": "llm", "when": "x"}, "correction": "c", "enforced_by": None}


class SpecTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kit = Path(self.tmp.name) / "kit"
        p = mock.patch.dict(os.environ, {"RSI_KIT_HOME": str(self.kit)})
        p.start()
        self.addCleanup(p.stop)
        self.spec = self.kit / "spec"
        self.spec.mkdir(parents=True)

    def write_rules(self, rules=None):
        (self.spec / "rules.json").write_bytes(
            json.dumps({"rules": rules if rules is not None else {"L01": GOOD}}).encode("utf-8"))

    def test_missing_rules_raises(self):
        with self.assertRaises(rsi_spec.SpecError):
            rsi_spec.load_spec()

    def test_broken_rules_raise(self):
        (self.spec / "rules.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(rsi_spec.SpecError):
            rsi_spec.load_spec()
        (self.spec / "rules.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(rsi_spec.SpecError):
            rsi_spec.load_spec()
        self.write_rules({"L01": dict(GOOD, enforce="maybe")})
        with self.assertRaises(rsi_spec.SpecError) as cm:
            rsi_spec.load_spec()
        self.assertIn("L01", str(cm.exception))

    def test_load_defaults_and_thresholds(self):
        self.write_rules()
        spec = rsi_spec.load_spec()
        self.assertEqual(spec["thresholds"], rsi_spec.DEFAULT_THRESHOLDS)
        self.assertEqual(spec["judge_prompt"], rsi_spec.DEFAULT_JUDGE_PROMPT)
        self.assertEqual(len(spec["hash"]), 12)
        (self.spec / "thresholds.json").write_text('{"review_size": 3}', encoding="utf-8")
        spec = rsi_spec.load_spec()
        self.assertEqual(spec["thresholds"]["review_size"], 3)
        self.assertEqual(spec["thresholds"]["recur_sessions"], 2)

    def test_missing_is_distinguished(self):
        with self.assertRaises(rsi_spec.SpecMissing):
            rsi_spec.load_spec()
        (self.spec / "rules.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(rsi_spec.SpecError) as cm:
            rsi_spec.load_spec()
        self.assertNotIsInstance(cm.exception, rsi_spec.SpecMissing)

    def test_thresholds_must_be_positive_ints(self):
        self.write_rules()
        for bad in ({"batch_bytes": "big"}, {"review_size": True}, {"review_per_run": 0}, {"review_per_run": True},
                    {"review_per_run": "4"}, {"max_proposals": 0},
                    {"judge_every_days": -1}, {"recur_sessions": 2.5}, {"max_batches_per_run": None}):
            (self.spec / "thresholds.json").write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(rsi_spec.SpecError) as cm:
                rsi_spec.load_spec()
            self.assertIn(list(bad)[0], str(cm.exception))
            self.assertIn("양의 정수", str(cm.exception))
        (self.spec / "thresholds.json").write_text('{"batch_bytes": 1000, "note": "x"}', encoding="utf-8")
        self.assertEqual(rsi_spec.load_spec()["thresholds"]["batch_bytes"], 1000)

    def test_review_per_run_default_and_old_thresholds_file(self):
        self.assertEqual(rsi_spec.DEFAULT_THRESHOLDS["review_per_run"], 4)
        self.write_rules()
        self.assertEqual(rsi_spec.load_spec()["thresholds"]["review_per_run"], 4)
        old = {k: v for k, v in rsi_spec.DEFAULT_THRESHOLDS.items() if k != "review_per_run"}
        (self.spec / "thresholds.json").write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(rsi_spec.load_spec()["thresholds"]["review_per_run"], 4)
        (self.spec / "thresholds.json").write_text('{"review_per_run": 6}', encoding="utf-8")
        self.assertEqual(rsi_spec.load_spec()["thresholds"]["review_per_run"], 6)

    def test_judge_hash_covers_prompt_and_rule_meaning_only(self):
        self.write_rules()
        base = rsi_spec.load_spec()["judge_hash"]
        self.assertEqual(len(base), 12)
        (self.spec / "thresholds.json").write_text('{"max_batches_per_run": 30}', encoding="utf-8")
        self.write_rules({"L01": dict(GOOD, type="restraint", no_exceptions=True, enforce="stop", enforced_by="x")})
        same = rsi_spec.load_spec()
        self.assertEqual(same["judge_hash"], base)
        self.write_rules({"L01": dict(GOOD, correction="다른 교정")})
        self.assertNotEqual(rsi_spec.load_spec()["judge_hash"], base)
        self.write_rules({"L01": dict(GOOD, opportunity={"kind": "llm", "when": "다른 상황"})})
        self.assertNotEqual(rsi_spec.load_spec()["judge_hash"], base)
        self.write_rules()
        (self.spec / "judge-prompt.md").write_text("다른 프롬프트", encoding="utf-8")
        self.assertNotEqual(rsi_spec.load_spec()["judge_hash"], base)

    def test_validate_rejects_bad(self):
        bad = dict(GOOD, enforce="maybe", opportunity={"kind": "tool", "tools": ["Bash"], "pattern": "("})
        self.assertEqual(len(rsi_spec.validate_rule(bad)), 2)
        self.assertEqual(rsi_spec.validate_rule(GOOD), [])

    def test_hash_changes(self):
        self.write_rules()
        (self.spec / "judge-prompt.md").write_text("판정 A", encoding="utf-8")
        a = rsi_spec.load_spec()
        raw = b"".join((self.spec / n).read_bytes() for n in ("rules.json", "judge-prompt.md"))
        self.assertEqual(a["hash"], hashlib.sha256(raw).hexdigest()[:12])
        (self.spec / "judge-prompt.md").write_text("판정 B", encoding="utf-8")
        b = rsi_spec.load_spec()
        self.assertNotEqual(a["hash"], b["hash"])
        self.assertNotEqual(a["prompt_hash"], b["prompt_hash"])
        self.assertEqual(b["judge_prompt"], "판정 B")

    def test_write_draft_never_touches_rules(self):
        self.write_rules()
        before = (self.spec / "rules.json").read_bytes()
        reg = {"rules": {
            "L01": {"id": "L01", "text": "모두 적는다.", "removed": None},
            "L02": {"id": "L02", "text": "옛 규칙", "removed": "2026-01-01"}}}
        path = rsi_spec.write_draft(reg)
        self.assertEqual(path.name, "rules.draft.json")
        self.assertEqual((self.spec / "rules.json").read_bytes(), before)
        draft = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(draft["rules"]), ["L01"])
        self.assertTrue((self.spec / "thresholds.json").exists())
        self.assertTrue((self.spec / "judge-prompt.md").exists())

    def test_write_draft_keeps_existing_defaults_files(self):
        (self.spec / "thresholds.json").write_text('{"review_size": 3}', encoding="utf-8")
        (self.spec / "judge-prompt.md").write_text("mine", encoding="utf-8")
        rsi_spec.write_draft({"rules": {}})
        self.assertEqual((self.spec / "thresholds.json").read_text(encoding="utf-8"), '{"review_size": 3}')
        self.assertEqual((self.spec / "judge-prompt.md").read_text(encoding="utf-8"), "mine")

    def test_draft_rule_examples(self):
        r = rsi_spec.draft_rule("L06", "PR 머지는 CI 체크가 모두 녹색인지 확인한 뒤에만 한다.")
        self.assertEqual(r["opportunity"]["kind"], "tool")
        self.assertEqual(rsi_spec.draft_rule("L01", "보고에 모두 적는다.")["type"], "step")
        r20 = rsi_spec.draft_rule("L20", "브랜치나 stash를 삭제하기 전에 백업한다.")
        self.assertIn("stash", r20["opportunity"]["pattern"])
        self.assertEqual(rsi_spec.draft_rule("L99", "삭제하지 않는다")["type"], "restraint")
        self.assertEqual(rsi_spec.draft_rule("L98", "짧게 쓴다")["type"], "preference")
        for t in ("PR 머지는 CI", "브랜치나 stash", "삭제하지 않는다", "짧게 쓴다"):
            self.assertEqual(rsi_spec.validate_rule(rsi_spec.draft_rule("X", t)), [])

    def test_prompt_content(self):
        p = rsi_spec.DEFAULT_JUDGE_PROMPT
        for token in ("<규칙>", "<발췌>", "opportunity", "correction", "unknown", "60", "items", "quote",
                      '"excerpt":"E1"', '"rule":"R1"', "yes 가 하나도 없는 쌍은"):
            self.assertIn(token, p)
        self.assertNotIn("발췌 번호", p)


if __name__ == "__main__":
    unittest.main()
