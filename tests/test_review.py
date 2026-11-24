import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_common  # noqa: E402
import rsi_review  # noqa: E402


def judgments(n_yes, n_no):
    rows = []
    for i in range(n_yes):
        rows.append({"excerpt": "y%d" % i, "rule": "R1", "opportunity": "yes", "correction": "yes", "quote": "q", "judge_hash": "h"})
    for i in range(n_no):
        rows.append({"excerpt": "n%d" % i, "rule": "R2", "opportunity": "yes", "correction": "no", "quote": "q", "judge_hash": "h"})
    rows.append({"excerpt": "u1", "rule": "R3", "opportunity": "yes", "correction": "unknown", "quote": "", "judge_hash": "h"})
    rows.append({"excerpt": "m", "rule": "*", "opportunity": "no", "correction": "no", "quote": "", "judge_hash": "h"})
    return rows


def excerpts(ids):
    return [{"id": i, "ts": "t", "project": "p", "session": "s", "u": "유저 " + i, "a_tail": "답 " + i, "tools": []} for i in ids]


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("RSI_KIT_HOME")
        os.environ["RSI_KIT_HOME"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("RSI_KIT_HOME", None)
        else:
            os.environ["RSI_KIT_HOME"] = self.old
        self.tmp.cleanup()

    def make(self, day=date(2026, 11, 1), h="H1", n_yes=3, n_no=20, size=10):
        ids = ["y%d" % i for i in range(n_yes)] + ["n%d" % i for i in range(n_no)] + ["u1", "m"]
        return rsi_review.make_review(day, h, judgments(n_yes, n_no), excerpts(ids), size)

    def answer(self, path, marks):
        """marks: 순서대로 '맞음'/'틀림'/None/'both'."""
        lines = path.read_text(encoding="utf-8").split("\n")
        out, k = [], -1
        for ln in lines:
            if ln.startswith("### R"):
                k += 1
            m = marks[k] if 0 <= k < len(marks) else None
            if ln == "- [ ] 맞음" and m in ("맞음", "both"):
                ln = "- [x] 맞음"
            if ln == "- [ ] 틀림" and m in ("틀림", "both"):
                ln = "- [X] 틀림"
            out.append(ln)
        path.write_text("\n".join(out), encoding="utf-8")

    def test_make_review_order(self):
        path = self.make()
        items = rsi_review.read_review(path)
        self.assertEqual(len(items), 10)
        self.assertEqual([i["judged"] for i in items[:3]], ["yes"] * 3)
        self.assertTrue(all(i["judged"] == "no" for i in items[3:]))
        self.assertNotIn("*", [i["rule"] for i in items])
        text = path.read_text(encoding="utf-8")
        self.assertIn("prompt_hash: H1", text)
        self.assertIn("### R1 [R1] 판정: 교정 yes", text)
        # 같은 날 다시 만들면 덮어쓰지 않는다
        p2 = self.make()
        p3 = self.make()
        self.assertEqual((path.name, p2.name, p3.name), ("2026-11-01.md", "2026-11-01 2.md", "2026-11-01 3.md"))

    def test_read_answers(self):
        path = self.make(n_yes=1, n_no=1, size=2)
        self.answer(path, ["맞음", "both"])
        items = rsi_review.read_review(path)
        self.assertEqual(items[0]["answer"], "맞음")
        self.assertIsNone(items[1]["answer"])

    def test_lenient_check_marks(self):
        # 사람이 에디터로 x 대신 v·o·✓ 를 쓰거나 칸 안에 띄어쓰기를 넣어도 체크로 읽는다(실사용에서 [ v] 로 체크한 일이 있었다).
        path = self.make(n_yes=8, n_no=0, size=8)
        marks = ["- [ v] 맞음", "- [v ] 틀림", "- [V] 맞음", "- [o] 맞음", "- [✓] 틀림",
                 "* [x] 맞음", "-  [x]  맞음", "- [ ] 맞음"]
        out, k = [], 0
        for ln in path.read_text(encoding="utf-8").split("\n"):
            if ln == "- [ ] 맞음":
                ln, k = marks[k], k + 1
            out.append(ln)
        path.write_text("\n".join(out), encoding="utf-8")
        answers = [i["answer"] for i in rsi_review.read_review(path)]
        self.assertEqual(answers, ["맞음", "틀림", "맞음", "맞음", "틀림", "맞음", "맞음", None])

    def test_review_mode_cycle(self):
        self.assertEqual(rsi_review.review_mode("H1"), "full")
        self.assertIsNone(rsi_review.pending_review())
        path = self.make(n_yes=1, n_no=1, size=2)
        self.assertIsNone(rsi_review.review_mode("H1"))
        self.assertIsNone(rsi_review.review_mode("H2"))
        self.assertEqual(rsi_review.pending_review(), path)
        self.answer(path, ["맞음", "틀림"])
        self.assertIsNone(rsi_review.pending_review())
        self.assertEqual(rsi_review.review_mode("H1"), "run")
        self.assertEqual(rsi_review.review_mode("H2"), "full")

    def test_calibration_agreement(self):
        path = self.make(n_yes=2, n_no=8, size=10)
        self.answer(path, ["맞음"] * 8 + ["틀림"] * 2)
        c = rsi_review.calibration()
        self.assertTrue(c["calibrated"])
        self.assertAlmostEqual(c["agreement"], 0.8)
        self.assertEqual((c["answered"], c["pending"]), (10, 0))
        c = rsi_review.calibration()  # 두 번 불러도 한 번만 쌓인다
        self.assertEqual(c["answered"], 10)
        self.assertEqual(len(rsi_common.read_jsonl(rsi_common.kit_dir() / "calibration.jsonl")), 10)

    def test_calibration_none_and_pending(self):
        c = rsi_review.calibration()
        self.assertFalse(c["calibrated"])
        self.assertIsNone(c["agreement"])
        self.make(n_yes=1, n_no=1, size=2)
        c = rsi_review.calibration()
        self.assertEqual((c["calibrated"], c["pending"], c["answered"]), (False, 2, 0))

    def test_skip_missing_excerpt(self):
        path = rsi_review.make_review(date(2026, 11, 2), "H", judgments(2, 0), excerpts(["y0"]), 10)
        self.assertEqual(len(rsi_review.read_review(path)), 1)

    def test_zero_candidates_writes_nothing(self):
        self.assertIsNone(rsi_review.make_review(date(2026, 11, 3), "H", [], excerpts([]), 10))
        self.assertFalse(rsi_review.review_dir().exists() and list(rsi_review.review_dir().glob("*.md")))

    def test_bad_bytes_and_unreadable_files_do_not_raise(self):
        d = rsi_review.review_dir()
        d.mkdir(parents=True)
        good = d / "2026-11-05.md"
        good.write_bytes(("---\nprompt_hash: H\ncreated: x\n---\n\n# 판정 확인\n\n### R1 [L01] 판정: 교정 yes\n"
                          "- 발췌: id1\n").encode("utf-8") + b"U: \xff\xfe \xed\xa0\x80\nA: -\n- [ ] \xeb\xa7\x9e\xec\x9d\x8c\n"
                         + "- [ ] 틀림\n".encode("utf-8"))
        (d / "2026-11-06.md").mkdir()  # 읽을 수 없는 .md(폴더)
        self.assertEqual(rsi_review.pending_review(), good)
        self.assertIsNone(rsi_review.review_mode("H"))
        c = rsi_review.calibration()
        self.assertEqual((c["calibrated"], c["pending"]), (False, 1))

    def test_empty_file_does_not_block(self):
        d = rsi_review.review_dir()
        d.mkdir(parents=True)
        (d / "2026-11-04.md").write_text("---" + chr(10) + "prompt_hash: H" + chr(10) + "created: x" + chr(10) + "---" + chr(10) + chr(10) + "# 판정 확인" + chr(10), encoding="utf-8")
        self.assertIsNone(rsi_review.pending_review())
        self.assertEqual(rsi_review.review_mode("H"), "full")
        self.assertFalse(rsi_review.calibration()["calibrated"])


def tjudgments(spec):
    """spec: [(excerpt, rule, correction)]"""
    return [{"excerpt": e, "rule": r, "opportunity": "yes", "correction": c, "quote": "q", "judge_hash": "h"}
            for e, r, c in spec]


def texcerpts(pairs):
    """pairs: [(id, ts)]"""
    return [{"id": i, "ts": t, "project": "p", "session": "s", "u": "유저 " + i, "a_tail": "답 " + i, "tools": []}
            for i, t in pairs]


class SamplingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("RSI_KIT_HOME")
        os.environ["RSI_KIT_HOME"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("RSI_KIT_HOME", None)
        else:
            os.environ["RSI_KIT_HOME"] = self.old
        self.tmp.cleanup()

    def pool(self, n_yes, n_no):
        """발췌 ts 는 번호가 클수록 최근. yes 는 y0.., no 는 n0.."""
        spec = [("y%d" % i, "L01", "yes") for i in range(n_yes)] + [("n%d" % i, "L02", "no") for i in range(n_no)]
        ex = texcerpts([("y%d" % i, "2026-11-%02dT00:00:00Z" % (i + 1)) for i in range(n_yes)]
                       + [("n%d" % i, "2026-11-%02dT00:00:00Z" % (i + 1)) for i in range(n_no)])
        return tjudgments(spec), ex

    def ids(self, path):
        return [(i["id"], i["judged"]) for i in rsi_review.read_review(path)]

    def finish(self, path):
        text = path.read_text(encoding="utf-8").replace("- [ ] 맞음", "- [x] 맞음")
        path.write_text(text, encoding="utf-8")

    def test_run_mode_two_yes_two_no_newest_first(self):
        j, ex = self.pool(5, 5)
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 4, mode="run")
        self.assertEqual(self.ids(path), [("y4", "yes"), ("y3", "yes"), ("n4", "no"), ("n3", "no")])

    def test_run_mode_skips_already_reviewed_pairs(self):
        j, ex = self.pool(5, 5)
        p1 = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 4, mode="run")
        self.finish(p1)
        p2 = rsi_review.make_review(date(2026, 11, 21), "H", j, ex, 4, mode="run")
        self.assertEqual(self.ids(p2), [("y2", "yes"), ("y1", "yes"), ("n2", "no"), ("n1", "no")])
        # 끝나지 않은 파일의 쌍도 다시 고르지 않는다
        p3 = rsi_review.make_review(date(2026, 11, 22), "H", j, ex, 4, mode="run")
        self.assertEqual(self.ids(p3), [("y0", "yes"), ("n0", "no")])
        self.assertIsNone(rsi_review.make_review(date(2026, 11, 23), "H", j, ex, 4, mode="run"))

    def test_run_mode_fills_from_other_side(self):
        j, ex = self.pool(1, 6)
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 4, mode="run")
        self.assertEqual(self.ids(path), [("y0", "yes"), ("n5", "no"), ("n4", "no"), ("n3", "no")])
        j, ex = self.pool(6, 1)
        path = rsi_review.make_review(date(2026, 11, 21), "H2", j, ex, 4, mode="run")
        self.assertEqual(self.ids(path), [("y5", "yes"), ("y4", "yes"), ("y3", "yes"), ("n0", "no")])

    def test_same_excerpt_other_rule_is_a_different_pair(self):
        j = tjudgments([("e1", "L01", "yes"), ("e1", "L02", "no")])
        ex = texcerpts([("e1", "2026-11-01T00:00:00Z")])
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 1, mode="run")
        self.assertEqual(len(rsi_review.read_review(path)), 1)
        self.finish(path)
        path2 = rsi_review.make_review(date(2026, 11, 21), "H", j, ex, 4, mode="run")
        self.assertEqual(len(rsi_review.read_review(path2)), 1)
        self.assertNotEqual(rsi_review.read_review(path)[0]["rule"], rsi_review.read_review(path2)[0]["rule"])

    def test_full_mode_balanced_and_deterministic(self):
        j, ex = self.pool(12, 12)
        d = date(2026, 11, 20)
        p1 = rsi_review.make_review(d, "H", j, ex, 10, mode="full")
        got = self.ids(p1)
        self.assertEqual(len(got), 10)
        self.assertEqual(sum(1 for _, k in got if k == "yes"), 5)
        self.assertEqual(sum(1 for _, k in got if k == "no"), 5)
        os.remove(str(p1))
        p2 = rsi_review.make_review(d, "H", j, ex, 10, mode="full")
        self.assertEqual(self.ids(p2), got)

    def test_full_mode_fills_other_side(self):
        j, ex = self.pool(2, 20)
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 10, mode="full")
        got = self.ids(path)
        self.assertEqual(len(got), 10)
        self.assertEqual(sum(1 for _, k in got if k == "yes"), 2)

    def test_rule_line_collapsed_truncated_and_ignored(self):
        j, ex = self.pool(1, 1)
        long = "첫 줄\n  둘째   줄 " + "가" * 300
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 2, mode="run",
                                      rule_texts={"L01": long, "L02": "짧은 규칙"})
        lines = path.read_text(encoding="utf-8").split("\n")
        k = lines.index("### R1 [L01] 판정: 교정 yes")
        self.assertTrue(lines[k + 1].startswith("- 규칙: 첫 줄 둘째 줄 "))
        self.assertLessEqual(len(lines[k + 1]) - len("- 규칙: "), 200)
        self.assertEqual(lines[k + 2], "- 발췌: y0")
        self.assertIn("- 규칙: 짧은 규칙", lines)
        items = rsi_review.read_review(path)
        self.assertEqual([i["id"] for i in items], ["y0", "n0"])
        self.assertTrue(all(i["answer"] is None for i in items))

    def test_rule_line_omitted_when_unknown(self):
        j, ex = self.pool(1, 0)
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 2, mode="run", rule_texts={})
        self.assertNotIn("- 규칙:", path.read_text(encoding="utf-8"))

    def test_review_mode_run_after_finished_same_prompt(self):
        self.assertEqual(rsi_review.review_mode("H"), "full")
        j, ex = self.pool(2, 2)
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 2, mode="full")
        self.assertIsNone(rsi_review.review_mode("H"))
        self.finish(path)
        self.assertEqual(rsi_review.review_mode("H"), "run")
        self.assertEqual(rsi_review.review_mode("OTHER"), "full")

    def test_prompt_change_allows_repicking_old_prompt_pairs(self):
        j, ex = self.pool(2, 2)
        p1 = rsi_review.make_review(date(2026, 11, 20), "H1", j, ex, 4, mode="full")
        self.finish(p1)
        self.assertEqual(rsi_review.review_mode("H2"), "full")
        p2 = rsi_review.make_review(date(2026, 11, 21), "H2", j, ex, 4, mode="full")
        self.assertIsNotNone(p2)
        self.assertEqual(sorted(self.ids(p2)), sorted(self.ids(p1)))
        # 같은 프롬프트 안에서는 다시 고르지 않는다
        self.assertIsNone(rsi_review.make_review(date(2026, 11, 22), "H2", j, ex, 4, mode="run"))

    def test_old_format_finished_file_regression(self):
        d = rsi_review.review_dir()
        d.mkdir(parents=True)
        lines = ["---", "prompt_hash: H", "created: 2026-11-23T16:25", "---", "", "# 판정 확인", "", "각 항목에서 맞음 또는 틀림 하나만 [x]로 체크하세요.", ""]
        for i in range(10):
            lines += ["### R%d [L01] 판정: 교정 yes" % (i + 1), "- 발췌: y%d" % i, "U: u", "A: a", "- [x] 맞음", "- [ ] 틀림", ""]
        (d / "2026-11-23.md").write_text("\n".join(lines), encoding="utf-8")
        rsi_common.append_jsonl(rsi_common.kit_dir() / "calibration.jsonl", [
            {"file": "2026-11-23.md", "id": "y%d" % i, "rule": "L01", "judged": "yes", "answer": "맞음"} for i in range(10)])
        self.assertEqual(rsi_review.review_mode("H"), "run")
        j, ex = self.pool(14, 3)
        path = rsi_review.make_review(date(2026, 11, 24), "H", j, ex, 4, mode="run")
        picked = {i for i, _ in self.ids(path)}
        self.assertFalse(picked & {"y%d" % i for i in range(10)})
        self.assertEqual(picked, {"y13", "y12", "n2", "n1"})
        c = rsi_review.calibration()
        self.assertEqual(c["agreement"], 1.0)
        self.assertEqual((c["yes_precision"], c["yes_answered"]), (1.0, 10))
        self.assertEqual((c["miss_rate"], c["no_answered"]), (None, 0))

    def test_calibration_two_rates(self):
        j = tjudgments([("a", "L01", "yes"), ("b", "L01", "yes"), ("c", "L01", "yes"),
                        ("d", "L02", "no"), ("e", "L02", "no"), ("f", "L02", "no"), ("g", "L02", "no")])
        ex = texcerpts([(x, "2026-11-01T00:00:00Z") for x in "abcdefg"])
        path = rsi_review.make_review(date(2026, 11, 20), "H", j, ex, 10, mode="run")
        marks = {"a": "맞음", "b": "맞음", "c": "틀림", "d": "맞음", "e": "맞음", "f": "맞음", "g": "틀림"}
        out, cur = [], None
        for ln in path.read_text(encoding="utf-8").split("\n"):
            if ln.startswith("- 발췌: "):
                cur = ln[len("- 발췌: "):]
            if ln in ("- [ ] 맞음", "- [ ] 틀림") and ln[6:] == marks[cur]:
                ln = ln.replace("[ ]", "[x]")
            out.append(ln)
        path.write_text("\n".join(out), encoding="utf-8")
        c = rsi_review.calibration()
        self.assertAlmostEqual(c["yes_precision"], 2 / 3)
        self.assertEqual(c["yes_answered"], 3)
        self.assertAlmostEqual(c["miss_rate"], 1 / 4)
        self.assertEqual(c["no_answered"], 4)
        self.assertAlmostEqual(c["agreement"], 5 / 7)

    def test_calibration_rates_none_when_empty(self):
        c = rsi_review.calibration()
        self.assertIsNone(c["yes_precision"])
        self.assertIsNone(c["miss_rate"])
        self.assertEqual((c["yes_answered"], c["no_answered"]), (0, 0))

    def test_calibration_uses_existing_judged_rows(self):
        rsi_common.append_jsonl(rsi_common.kit_dir() / "calibration.jsonl", [
            {"file": "old.md", "id": "x", "rule": "L01", "judged": "yes", "answer": "맞음"},
            {"file": "old.md", "id": "y", "rule": "L01", "judged": "no", "answer": "틀림"}])
        c = rsi_review.calibration()
        self.assertEqual((c["yes_precision"], c["yes_answered"]), (1.0, 1))
        self.assertEqual((c["miss_rate"], c["no_answered"]), (1.0, 1))


if __name__ == "__main__":
    unittest.main()
