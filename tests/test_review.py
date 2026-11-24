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

    def test_needs_review_cycle(self):
        self.assertTrue(rsi_review.needs_review("H1"))
        self.assertIsNone(rsi_review.pending_review())
        path = self.make(n_yes=1, n_no=1, size=2)
        self.assertFalse(rsi_review.needs_review("H1"))
        self.assertFalse(rsi_review.needs_review("H2"))
        self.assertEqual(rsi_review.pending_review(), path)
        self.answer(path, ["맞음", "틀림"])
        self.assertIsNone(rsi_review.pending_review())
        self.assertFalse(rsi_review.needs_review("H1"))
        self.assertTrue(rsi_review.needs_review("H2"))

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
        self.assertFalse(rsi_review.needs_review("H"))
        c = rsi_review.calibration()
        self.assertEqual((c["calibrated"], c["pending"]), (False, 1))

    def test_empty_file_does_not_block(self):
        d = rsi_review.review_dir()
        d.mkdir(parents=True)
        (d / "2026-11-04.md").write_text("---" + chr(10) + "prompt_hash: H" + chr(10) + "created: x" + chr(10) + "---" + chr(10) + chr(10) + "# 판정 확인" + chr(10), encoding="utf-8")
        self.assertIsNone(rsi_review.pending_review())
        self.assertTrue(rsi_review.needs_review("H"))
        self.assertFalse(rsi_review.calibration()["calibrated"])


if __name__ == "__main__":
    unittest.main()
