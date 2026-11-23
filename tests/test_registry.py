import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import rsi_registry  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
VAULT = FIX / "vault"
MEM_NAME = "feedback-one-task-orchestrate-ask-as-you-go"


def rules_from_fixture():
    return rsi_registry.read_global_rules((FIX / "CLAUDE.md").read_text(encoding="utf-8"))


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.dict(os.environ, {"RSI_KIT_HOME": str(Path(self.tmp.name) / "kit")})
        p.start()
        self.addCleanup(p.stop)

    def test_read_global_rules(self):
        r = rules_from_fixture()
        self.assertEqual(sorted(r), ["L01", "L02", "L06", "L09", "L18", "L21"])
        self.assertNotIn("L99", r)
        self.assertNotIn("L98", r)
        self.assertNotIn("L10", r)
        self.assertTrue(r["L01"].startswith("작업을 끝내면"))

    def test_read_memory_rules(self):
        projects = Path(self.tmp.name) / "projects"
        mem = projects / "C--demo" / "memory"
        mem.mkdir(parents=True)
        (mem / "a.md").write_text(
            "---\nname: a\ndescription: 규칙 설명 A\nsource: dream\n---\n본문\n", encoding="utf-8")
        (mem / "b.md").write_text("---\nname: b\ndescription: 다른 것\ntype: user\n---\n", encoding="utf-8")
        (mem / "c.md").write_text(
            "---\nname: c\ndescription: \"따옴표 설명\"\nsource: dream\n---\n", encoding="utf-8")
        self.assertEqual(rsi_registry.read_memory_rules(projects),
                         {"C--demo/a.md": "규칙 설명 A", "C--demo/c.md": "따옴표 설명"})
        self.assertEqual(rsi_registry.read_memory_rules(projects / "none"), {})

    def test_adoption_records_shape(self):
        recs = rsi_registry.adoption_records(VAULT)
        self.assertEqual(len(recs), 4)
        by = {r["file"]: r for r in recs}
        b = by["2026-10-30 dream 규칙 정리 제안.md"]
        self.assertEqual(b["proposals"][1]["action"], "고치기")
        self.assertEqual(b["proposals"][2]["action"], "빼기")
        self.assertIn("직접 바꾼", b["proposals"][1]["rule"])
        self.assertEqual(b["results"][0]["direct"], {"L02": "edit", "L10": "delete"})
        self.assertEqual(b["results"][0]["adopted"], [1, 2])
        d = by["2026-11-12 dream 규칙 제안.md"]
        self.assertEqual([r["date"] for r in d["results"]], ["2026-11-12", "2026-11-17"])
        self.assertEqual(d["results"][0]["rejected"], [1])
        self.assertEqual(d["results"][0]["held"], [4])
        self.assertEqual(d["results"][1]["direct"], {"L21": "adopt"})
        self.assertEqual(rsi_registry.adoption_records(VAULT / "nope"), [])

    def test_result_line_variants(self):
        pr = rsi_registry._parse_result
        r = pr("- 반영: 1 → [L01], 2 → [L02], 4 → [L04](문구 수정: 'x' — 판단 방식 1번)"
               " · 거절: 없음 · 보류: 없음", {})
        self.assertEqual(r["adopted"], [1, 2, 4])
        self.assertEqual(r["direct"], {"L01": "adopt", "L02": "adopt", "L04": "edit"})
        r = pr("반영: 없음 · 거절: 1 · 보류: 없음", {})
        self.assertEqual((r["adopted"], r["rejected"], r["held"], r["direct"]), ([], [1], [], {}))
        r = pr("- 1 → [L11] · 2 → [L12] · 4 거절(이유 있음)", {})
        self.assertEqual((r["adopted"], r["rejected"]), ([1, 2], [4]))
        r = pr("- 2 → 데모 폴더 메모리 feedback-x · 3 → [L18] · 1 거절(...)", {2: "C--demo"})
        self.assertEqual(r["direct"], {"C--demo/feedback-x.md": "adopt", "L18": "adopt"})
        self.assertEqual(r["rejected"], [1])

    def test_adoption_variants(self):
        rules = rules_from_fixture()
        mid = "C--demo-proj/" + MEM_NAME + ".md"
        rules[mid] = "한 번에 한 작업만 맡긴다"
        m = rsi_registry.match_adoptions(rules, rsi_registry.adoption_records(VAULT))
        self.assertEqual(m["L01"], {"adopted": "2026-10-27", "origin": '"파일 목록도 알려줘" (2026-10-26)',
                                    "match": "direct", "edits": []})
        self.assertEqual(m["L09"]["match"], "direct")
        self.assertEqual(m["L09"]["adopted"], "2026-11-04")
        self.assertEqual(m["L21"]["adopted"], "2026-11-17")
        self.assertEqual(m["L21"]["match"], "direct")
        self.assertEqual(m["L18"]["match"], "text")
        self.assertEqual(m["L18"]["adopted"], "2026-11-12")
        self.assertEqual(m["L02"]["edits"], ["2026-10-30"])
        self.assertEqual(m["L02"]["adopted"], "2026-10-27")
        self.assertEqual(m["L06"]["edits"], ["2026-11-04"])
        self.assertEqual(m["L06"]["adopted"], "2026-10-27")
        self.assertEqual((m[mid]["match"], m[mid]["adopted"]), ("direct", "2026-11-04"))
        self.assertNotIn("L10", m)

    def test_origin_is_evidence_quote(self):
        m = rsi_registry.match_adoptions(rules_from_fixture(), rsi_registry.adoption_records(VAULT))
        self.assertEqual(m["L18"]["origin"], '"내가 할게" (2026-11-11)')  # text match
        self.assertEqual(m["L21"]["origin"], '"PC 느려" (2026-11-11)')    # N (..[L21]) form
        res = rsi_registry._parse_result("- [L77] 번호 없는 항목", {})
        rec = {"file": "f.md", "proposals": {}, "results": [dict(res, date="2026-11-01", direct={"L77": "adopt"})]}
        self.assertEqual(rsi_registry.match_adoptions({"L77": "x"}, [rec])["L77"]["origin"], "")

    def test_adoption_records_folders_and_encoding(self):
        v = Path(self.tmp.name) / "v"
        for folder in ("90_Archive", "00_Inbox", "99_System", "20_Projects/sub"):
            (v / folder).mkdir(parents=True)
        body = ("---\ntype: dream\n---\n## 1. 빼기 · 전역 · 대상 [L03]\n- 규칙: (뺌)\n"
                "- 근거: \"q\" (2026-11-01)\n")
        (v / "90_Archive" / "a dream.md").write_text(body, encoding="utf-8")
        (v / "00_Inbox" / "b dream.md").write_text(body, encoding="utf-8")  # 처리 결과 없음
        (v / "99_System" / "x dream.md").write_text(body, encoding="utf-8")
        (v / "20_Projects/sub" / "y dream.md").write_text(body, encoding="utf-8")
        (v / "90_Archive" / "bad dream.md").write_bytes(b"---\ntype: dream\n---\n\xff\xfe\x80 broken\n")
        recs = rsi_registry.adoption_records(v)
        self.assertEqual(sorted(r["file"] for r in recs), ["a dream.md", "b dream.md", "bad dream.md"])
        by = {r["file"]: r for r in recs}
        self.assertEqual(by["b dream.md"]["results"], [])
        self.assertEqual(by["a dream.md"]["proposals"][1]["action"], "빼기")

    def test_memory_reader_survives_bad_bytes(self):
        mem = Path(self.tmp.name) / "projects" / "S" / "memory"
        mem.mkdir(parents=True)
        (mem / "bad.md").write_bytes(b"---\nname: x\ndescription: d\xff\nsource: dream\n---\n")
        (mem / "ok.md").write_text("---\ndescription: fine\nsource: dream\n---\n", encoding="utf-8")
        r = rsi_registry.read_memory_rules(Path(self.tmp.name) / "projects")
        self.assertEqual(r["S/ok.md"], "fine")
        self.assertIn("S/bad.md", r)

    def test_unknown_and_only_present_ids(self):
        rules = {"L50": "아무 제안과도 닮지 않은 문구 xyz 123", "L01": "x"}
        m = rsi_registry.match_adoptions(rules, rsi_registry.adoption_records(VAULT))
        self.assertEqual(m["L50"], {"adopted": None, "origin": "", "match": "unknown", "edits": []})
        self.assertEqual(sorted(m), ["L01", "L50"])

    def test_memory_matched_by_file_name_alone(self):
        res = rsi_registry._parse_result("- 2 → 데모 폴더 메모리 feedback-x", {})
        self.assertEqual(res["direct"], {"feedback-x.md": "adopt"})
        res["date"] = "2026-11-01"
        rec = {"file": "f.md", "proposals": {}, "results": [res]}
        m = rsi_registry.match_adoptions({"ANY--slug/feedback-x.md": "t", "ANY--slug/other.md": "zzz"}, [rec])
        self.assertEqual(m["ANY--slug/feedback-x.md"]["match"], "direct")
        self.assertEqual(m["ANY--slug/other.md"]["match"], "unknown")

    def test_update_registry_text_change(self):
        reg = {"rules": {"L01": {"id": "L01", "text": "옛 문구", "adopted": "2026-10-27",
                                 "text_since": "2026-10-27", "origin": "o", "removed": None, "match": "direct"}}}
        changed = rsi_registry.update_registry(reg, {"L01": "새 문구"}, date(2026, 11, 20), {})
        self.assertEqual(changed, ["L01"])
        e = reg["rules"]["L01"]
        self.assertEqual((e["text"], e["text_since"], e["adopted"]), ("새 문구", "2026-11-20", "2026-10-27"))
        self.assertEqual(rsi_registry.update_registry(reg, {"L01": "새 문구"}, date(2026, 11, 21), {}), [])
        self.assertEqual(reg["rules"]["L01"]["text_since"], "2026-11-20")

    def test_update_registry_new_and_removed(self):
        reg = {"rules": {"L03": {"id": "L03", "text": "t", "adopted": "2026-10-01", "text_since": "2026-10-01",
                                 "origin": "", "removed": None, "match": "unknown"}}}
        adoptions = {"L22": {"adopted": "2026-11-18", "origin": "f.md", "match": "direct", "edits": ["2026-11-19"]},
                     "L23": {"adopted": None, "origin": "", "match": "unknown", "edits": []}}
        changed = rsi_registry.update_registry(reg, {"L22": "n", "L23": "m"}, date(2026, 11, 20), adoptions)
        self.assertEqual(sorted(changed), ["L03", "L22", "L23"])
        self.assertEqual(reg["rules"]["L03"]["removed"], "2026-11-20")
        n = reg["rules"]["L22"]
        self.assertEqual((n["adopted"], n["text_since"], n["origin"], n["match"], n["removed"]),
                         ("2026-11-18", "2026-11-19", "f.md", "direct", None))
        u = reg["rules"]["L23"]
        self.assertEqual((u["adopted"], u["text_since"], u["match"]), ("2026-11-20", "2026-11-20", "unknown"))
        # 이미 지워진 규칙은 다시 바뀐 것으로 치지 않는다
        self.assertEqual(rsi_registry.update_registry(
            reg, {"L22": "n", "L23": "m"}, date(2026, 11, 21), adoptions), [])

    def test_load_save_registry(self):
        self.assertEqual(rsi_registry.load_registry(), {"rules": {}, "updated": None})
        rsi_registry.save_registry({"rules": {"L01": {"id": "L01"}}, "updated": "x"})
        self.assertEqual(rsi_registry.load_registry()["rules"]["L01"]["id"], "L01")
        self.assertTrue((Path(os.environ["RSI_KIT_HOME"]) / "registry.json").exists())


if __name__ == "__main__":
    unittest.main()
