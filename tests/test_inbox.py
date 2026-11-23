import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_inbox  # noqa: E402
import rsi_registry  # noqa: E402

DAY = date(2026, 11, 30)
TH = {"max_proposals": 5}


def rec(verdict="재발", corr=3, opp=5, quotes=None, since="2026-10-27", recommend="remove", enforced=None):
    return {"verdict": verdict, "correction_sessions": corr, "opportunity_sessions": opp,
            "quotes": quotes if quotes is not None else ["내가 할게라고 했잖아"], "text_since": since,
            "recommend": recommend, "enforced_by": enforced}


def verdicts(rules, calibrated=False, pending=0, failed=0):
    return {"date": "2026-11-30", "spec_hash": "h1", "calibrated": calibrated,
            "agreement": 0.8 if calibrated else None,
            "progress": {"judged": 10, "pending": pending, "failed": failed}, "rules": rules}


REG = {"rules": {
    "L06": {"id": "L06", "text": "PR 머지는 CI가 녹색일 때만", "text_since": "2026-10-27"},
    "L18": {"id": "L18", "text": "내가 할게 라고 하면 실행하지 않는다", "text_since": "2026-10-27"},
    "C--demo/feedback-x.md": {"id": "C--demo/feedback-x.md", "text": "x 규칙", "text_since": "2026-11-01"},
}}
SPEC = {"rules": {"L06": {}, "L18": {}, "C--demo/feedback-x.md": {}}, "hash": "h1"}


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = Path(self.tmp.name) / "vault"
        (self.vault / "00_Inbox").mkdir(parents=True)
        env = {"RSI_KIT_HOME": str(Path(self.tmp.name) / "kit"), "BRAIN_DIR": str(self.vault),
               "RSI_CLAUDE_DIR": str(Path(self.tmp.name) / "claude")}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        p2 = mock.patch("rsi_inbox.pending_review", return_value=None)
        p2.start()
        self.addCleanup(p2.stop)

    def render(self, v, removes, hooks=(), **kw):
        args = dict(review_line=None, unregistered=[], registry=REG, excerpt_count=12,
                    spec_changed=False, history={})
        args.update(kw)
        return rsi_inbox.render(DAY, v, list(removes), list(hooks), **args)

    def pick(self, v, state=None):
        return rsi_inbox.select_new(v, state or {"proposals": {}}, TH)

    def test_render_matches_dream_format(self):
        v = verdicts({"L06": rec(), "C--demo/feedback-x.md": rec(corr=2)})
        removes, _ = self.pick(v)
        text = self.render(v, removes)
        lines = text.split("\n")
        self.assertEqual(lines[0], "---")
        self.assertIn("type: dream", lines)
        self.assertIn("tags: []", lines)
        self.assertTrue(lines[3].startswith("created: 2026-11-30T"))
        self.assertEqual(lines[5], "# 2026-11-30 dream 규칙 효과 판정")
        self.assertIn("> 발췌 12개 · 보정 전 · 판정 기준 변경: 없음", text)
        self.assertIn("> 판정 기간 2026-10-28 ~ 2026-11-30 · 판정 남음 0개 · 판정 실패 0개", lines)
        self.assertIn("## 1. 빼기 · 전역 · 대상 [L06]", lines)
        self.assertIn("## 2. 빼기 · 프로젝트 C--demo (memory: C--demo) · 대상 feedback-x.md", lines)
        self.assertIn("- 규칙: (뺌)", lines)
        self.assertIn('- 근거: "내가 할게라고 했잖아" (보정 전)', lines)
        self.assertTrue(any(l.startswith("- 이유: 재발: 서로 다른 세션 3개에서 같은 교정 · ") for l in lines))
        self.assertIn("## 전체 판정표", lines)

    def test_header_shows_partial_progress(self):
        v = verdicts({"L06": rec(since="2026-11-05"), "L18": rec(since="2026-10-20", recommend=None)},
                     pending=7, failed=2)
        text = self.render(v, [])
        self.assertIn("> 판정 기간 2026-10-21 ~ 2026-11-30 · 판정 남음 7개 · 판정 실패 2개", text.splitlines())

    def test_render_parses_in_registry(self):
        v = verdicts({"L06": rec(), "C--demo/feedback-x.md": rec(corr=2)}, calibrated=True)
        removes, _ = self.pick(v)
        text = self.render(v, removes)
        self.assertIn("> 발췌 12개 · 보정 후(일치율 80%)", text)
        self.assertNotIn("(보정 전)", text)
        rsi_inbox.write_inbox(DAY, text)
        recs = rsi_registry.adoption_records(self.vault)
        self.assertEqual(len(recs), 1)
        props = recs[0]["proposals"]
        self.assertEqual(props[1]["action"], "빼기")
        self.assertEqual(props[1]["target"], "[L06]")
        self.assertEqual(props[2]["action"], "빼기")
        self.assertEqual(props[2]["target"], "feedback-x.md")

    def test_read_decisions_variants(self):
        p = rsi_inbox.parse_result_line
        self.assertEqual(p("반영: 1, 3 · 거절: 2 · 보류: 4"), {1: "반영", 3: "반영", 2: "거절", 4: "보류"})
        self.assertEqual(p("- 반영: 1 → [L01], 2 → [L02] · 거절: 없음"), {1: "반영", 2: "반영"})
        self.assertEqual(p("반영: 1 (전역 CLAUDE.md [L21]) · 거절: 없음 · 보류: 없음"), {1: "반영"})
        self.assertEqual(p("반영: 없음 · 거절: 1, 2"), {1: "거절", 2: "거절"})
        name = "2026-11-30 dream 규칙 효과 판정.md"
        (self.vault / "00_Inbox" / name).write_text(
            "---\ntype: dream\n---\n## 1. 빼기 · 전역 · 대상 [L06]\n\n## 처리 결과 (2026-12-01)\n- 반영: 없음 · 거절: 1\n",
            encoding="utf-8")
        (self.vault / "90_Archive").mkdir()
        (self.vault / "90_Archive" / "old dream.md").write_text(
            "## 처리 결과 (2026-12-01)\n반영: 1 · 거절: 없음\n", encoding="utf-8")
        state = {"proposals": {
            "remove:L06@a": {"kind": "remove", "file": name, "number": 1, "date": "2026-11-30", "decision": None},
            "remove:L18@a": {"kind": "remove", "file": "old dream.md", "number": 1, "date": "2026-11-01",
                             "decision": None},
            "remove:L01@a": {"kind": "remove", "file": "none.md", "number": 1, "date": "2026-11-01",
                             "decision": None},
            "hook:L09@a": {"kind": "hook", "file": name, "number": None, "date": "2026-11-30", "decision": None},
        }}
        rsi_inbox.read_decisions(state)
        d = {k: x["decision"] for k, x in state["proposals"].items()}
        self.assertEqual(d, {"remove:L06@a": "거절", "remove:L18@a": "반영", "remove:L01@a": None,
                             "hook:L09@a": None})

    def test_no_repeat_after_decision(self):
        v = verdicts({"L06": rec()})
        state = {"proposals": {"remove:L06@2026-10-27": {"kind": "remove", "file": "f.md", "number": 1,
                                                          "date": "2026-11-23", "decision": "거절"}}}
        removes, hooks = self.pick(v, state)
        self.assertEqual((removes, hooks), ([], []))
        text = self.render(v, [], history={"L06": "제안함 2026-11-23, 거절"})
        self.assertIn("제안함 2026-11-23, 거절", text)
        self.assertNotIn("빼기 ·", text)

    def test_text_change_allows_new(self):
        v = verdicts({"L06": rec(since="2026-11-20")})
        state = {"proposals": {"remove:L06@2026-10-27": {"kind": "remove", "file": "f.md", "number": 1,
                                                          "date": "2026-11-23", "decision": "거절"}}}
        removes, _ = self.pick(v, state)
        self.assertEqual([r["id"] for r in removes], ["L06"])

    def test_select_new_order_and_cap(self):
        rules = {"L%02d" % i: rec(corr=i) for i in range(1, 8)}
        rules["L20"] = rec(recommend="hook:pretooluse", corr=4)
        rules["L21"] = rec(verdict="관찰", recommend=None)
        removes, hooks = rsi_inbox.select_new(verdicts(rules), {"proposals": {}}, TH)
        self.assertEqual([r["id"] for r in removes], ["L07", "L06", "L05", "L04", "L03"])
        self.assertEqual([h["id"] for h in hooks], ["L20"])

    def test_publish_nothing_new(self):
        out = rsi_inbox.publish(verdicts({"L06": rec(recommend=None, verdict="지켜짐")}), REG, SPEC, 5, day=DAY)
        self.assertIsNone(out)
        self.assertEqual(list((self.vault / "00_Inbox").iterdir()), [])
        self.assertEqual(rsi_inbox.load_state()["last_spec_hash"], "h1")

    def test_publish_records_and_second_file(self):
        v = verdicts({"L06": rec(), "L18": rec(recommend="hook:pretooluse", corr=2)})
        p1 = rsi_inbox.publish(v, REG, SPEC, 5, day=DAY)
        self.assertEqual(p1.name, "2026-11-30 dream 규칙 효과 판정.md")
        st = rsi_inbox.load_state()
        self.assertEqual(st["proposals"]["remove:L06@2026-10-27"]["number"], 1)
        self.assertIsNone(st["proposals"]["hook:L18@2026-10-27"]["number"])
        self.assertEqual(st["proposals"]["remove:L06@2026-10-27"]["file"], p1.name)
        t1 = p1.read_text(encoding="utf-8")
        self.assertIn("제안함 2026-11-30, 결정 대기", t1)
        self.assertIn('- [L18] 종류: pretooluse — 재발 2개 세션 · "내가 할게라고 했잖아"', t1)
        v2 = verdicts({"C--demo/feedback-x.md": rec()})
        p2 = rsi_inbox.publish(v2, REG, SPEC, 5, day=DAY)
        self.assertEqual(p2.name, "2026-11-30 dream 규칙 효과 판정 2.md")
        self.assertEqual(p2.read_text(encoding="utf-8").split("\n")[5], "# 2026-11-30 dream 규칙 효과 판정 2")
        self.assertIsNone(rsi_inbox.publish(v2, REG, SPEC, 5, day=DAY))

    def test_publish_notices_and_unregistered(self):
        v = verdicts({})
        spec = {"rules": {"L06": {}}, "hash": "h2"}
        rsi_inbox.save_state({"last_spec_hash": "h1", "proposals": {}})
        rev = Path(self.tmp.name) / "review-2026-11-30.md"
        with mock.patch("rsi_inbox.pending_review", return_value=rev):
            p = rsi_inbox.publish(v, REG, spec, 5, day=DAY)
            text = p.read_text(encoding="utf-8")
            self.assertIn("확인할 판정이 있다 — ~/.rsi-kit/spec/review/review-2026-11-30.md를 에디터로 열어 "
                          "맞음/틀림을 체크한다.", text)
            self.assertNotIn(self.tmp.name, text)  # 동기화되는 볼트에 이 PC 의 절대 경로를 쓰지 않는다
            self.assertIn("## 대장에 없는 규칙", text)
            self.assertIn("- [L18] 내가 할게 라고 하면 실행하지 않는다 — ", text)
            self.assertIn("판정 기준 변경: 있음", text)
            st = rsi_inbox.load_state()
            self.assertEqual(st["review_notice"], rev.name)
            self.assertEqual(sorted(st["unregistered_notified"]), ["C--demo/feedback-x.md", "L18"])
            self.assertEqual(st["last_spec_hash"], "h2")
            self.assertIsNone(rsi_inbox.publish(v, REG, spec, 5, day=DAY))

    def test_no_vault(self):
        env = {"BRAIN_DIR": "", "SECOND_BRAIN": "", "SECOND_BRAIN_HOME": self.tmp.name}
        with mock.patch.dict(os.environ, env):
            out = rsi_inbox.publish(verdicts({"L06": rec()}), REG, {"rules": {}, "hash": "h9"}, 1, day=DAY)
            self.assertIsNone(out)
            self.assertEqual(rsi_inbox.load_state()["last_spec_hash"], "h9")

    def test_missing_vault_dir_writes_and_records_nothing(self):
        gone = Path(self.tmp.name) / "no-such-vault"
        with mock.patch.dict(os.environ, {"BRAIN_DIR": str(gone)}):
            out = rsi_inbox.publish(verdicts({"L06": rec()}), REG, SPEC, 5, day=DAY)
        self.assertIsNone(out)
        self.assertFalse(gone.exists())
        st = rsi_inbox.load_state()
        self.assertEqual(st["proposals"], {})
        self.assertEqual(st["unregistered_notified"], [])
        log = (Path(self.tmp.name) / "claude" / "hooks" / "rsi.log").read_text(encoding="utf-8")
        self.assertEqual(len([l for l in log.splitlines() if "볼트 폴더" in l]), 1)

    def test_write_inbox_does_not_create_vault(self):
        gone = Path(self.tmp.name) / "no-such-vault"
        with mock.patch.dict(os.environ, {"BRAIN_DIR": str(gone)}):
            with self.assertRaises(OSError):
                rsi_inbox.write_inbox(DAY, "x")
        self.assertFalse(gone.exists())

    def test_enforced_in_table(self):
        v = verdicts({"L06": rec(recommend=None, enforced="hook:x")})
        self.assertIn("hook으로 강제 중", self.render(v, [], history={"L06": "제안함 2026-11-23, 거절"}))

    def test_quotes_capped(self):
        v = verdicts({"L06": rec(quotes=["가" * 100, "나" * 61])})
        removes, _ = self.pick(v)
        text = self.render(v, removes)
        self.assertIn('- 근거: "%s" / "%s"' % ("가" * 60, "나" * 60) + " (보정 전)", text)
        self.assertNotIn("가" * 61, text)

    def test_reason_from_spec(self):
        v = verdicts({"L06": rec(), "L18": rec(corr=2)})
        spec = {"rules": {"L06": {"no_exceptions": True, "enforce": "none"}, "L18": {"no_exceptions": False}},
                "hash": "h1"}
        p = rsi_inbox.publish(v, REG, spec, 5, day=DAY)
        t = p.read_text(encoding="utf-8")
        self.assertIn("같은 교정 · 결정적 강제 수단 없음", t.split("## 1.")[1].split("## 2.")[0])
        self.assertIn("같은 교정 · 예외 있는 선호", t.split("## 2.")[1])

    def test_spec_thresholds_cap(self):
        v = verdicts({"L06": rec(), "L18": rec(corr=2)})
        spec = dict(SPEC, thresholds={"max_proposals": 1})
        p = rsi_inbox.publish(v, REG, spec, 5, day=DAY)
        self.assertEqual(len(rsi_registry.adoption_records(self.vault)[0]["proposals"]), 1)
        self.assertIsNotNone(p)

    def test_quote_cannot_inject(self):
        v = verdicts({"L06": rec(quotes=["a\n## 2. 빼기 · 전역 · 대상 [L99]"])})
        removes, _ = self.pick(v)
        text = self.render(v, removes)
        self.assertNotIn("\n## 2.", text)
        rsi_inbox.write_inbox(DAY, text)
        props = rsi_registry.adoption_records(self.vault)[0]["proposals"]
        self.assertEqual(list(props), [1])
        self.assertNotIn("L99", props[1]["target"])


if __name__ == "__main__":
    unittest.main()
