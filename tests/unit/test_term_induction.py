import json
from pathlib import Path

from core.pipeline import ImportService
from core.project import Project
from core.term_induction import InductionService, extract_candidates
from llm.mock import MockProvider


def induction_translator(user: str) -> str:
    return json.dumps({"terms": [
        {"src": "灵石", "candidates": ["spirit stone"], "note": "修炼货币"},
        {"src": "林凡", "candidates": ["Lin Fan", "Fan Lin"], "note": "主角"},
    ]}, ensure_ascii=False)


def make_project_with_doc(tmp_path, text: str, name="a.md"):
    project = Project.create(tmp_path / "p", name="t", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    ImportService(project).import_files([f])
    return project


DOC1 = "林凡握着灵石。灵石闪烁。林凡笑了。灵石、灵石、灵石。\n"


def test_extract_candidates_zh():
    cands = extract_candidates([DOC1 * 3], "zh-CN")
    words = [w for w, _ in cands]
    assert "林凡" in words and "灵石" in words
    assert all(len(w) >= 2 for w in words)
    freq = dict(cands)
    assert freq["灵石"] > freq["林凡"]


def test_extract_candidates_en():
    text = " ".join(["Eldric raised the runestone."] * 5)
    cands = extract_candidates([text], "en-US")
    words = dict(cands)
    assert words.get("Eldric") == 5 or words.get("runestone") == 5


def test_extract_candidates_ja():
    """日文源：片假名专有名词 + 汉字概念词应进入候选（GLM 实测发现的缺陷回归）。"""
    text = ("カズヒホはマリーとナズル遺跡へ向かった。" * 3
            + "エルフの娘は微笑む。スズメの声。カズヒホとマリー。" * 3)
    cands = extract_candidates([text], "ja-JP")
    words = dict(cands)
    assert "カズヒホ" in words and "マリー" in words
    assert words["カズヒホ"] >= 4
    # 汉字概念词（遺跡 等）也应被 bigram 覆盖到
    assert any("遺跡" in w for w in words)


def test_induction_and_incremental(tmp_path):
    project = make_project_with_doc(tmp_path, DOC1)
    service = InductionService(project, MockProvider(translator=induction_translator))
    res = service.run()
    assert res["docs"] == 1
    assert {c["src"] for c in res["candidates"]} == {"灵石", "林凡"}
    # 候选状态入库
    rows = {t["src_term"]: t for t in project.db.list_terms()}
    assert rows["灵石"]["status"] == "candidate"
    assert rows["灵石"]["origin"] == "ai_induced"
    assert rows["林凡"]["tgt_candidates"].split("|") == ["Lin Fan", "Fan Lin"]
    # 文件已标记归纳
    assert project.db.list_documents()[0]["terms_extracted"] == 1

    # 审核：只采纳灵石，拒绝林凡 → 写入 glossary + 快照
    n = service.approve([("灵石", ["spirit stone"], "货币")])
    service.reject(["林凡"])
    assert n == 1
    assert project.glossary.get("灵石").candidates == ["spirit stone"]
    assert (project.root / "glossary.csv").exists()
    assert project.db.last_terms_revision() is not None

    # 增量：新文件 → 只归纳新文件；已定稿术语不被 AI 覆盖
    f2 = tmp_path / "b.md"
    f2.write_text("林凡再次出现。宗门开会。\n", encoding="utf-8")
    ImportService(project).import_files([f2])
    res2 = service.run()
    assert res2["docs"] == 1  # 只有 b.md
    assert project.glossary.get("灵石").candidates == ["spirit stone"]  # 用户定稿优先
    project.close()


def test_induction_auth_pause(tmp_path):
    project = make_project_with_doc(tmp_path, DOC1)
    service = InductionService(project, MockProvider(translator=induction_translator,
                                                     auth_fail=True))
    res = service.run()
    assert res["paused"] and res["error"]
    # 未标记归纳，修复后可重跑
    assert project.db.list_documents()[0]["terms_extracted"] == 0
    project.close()
