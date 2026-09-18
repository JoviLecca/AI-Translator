from pathlib import Path

from core.pipeline import ImportService, TranslationService
from core.project import Project
from core.term_impact import TermImpactService
from llm.mock import MockProvider


def setup_project(tmp_path):
    project = Project.create(tmp_path / "p", name="t", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    f = tmp_path / "a.md"
    f.write_text("林凡握着灵石。\n\n灵石可以兑换丹药。\n", encoding="utf-8")
    ImportService(project).import_files([f])
    g = project.glossary
    g.add("灵石", ["mana stone"], "货币")
    g.save()
    project.db.save_terms_revision(g.effective_hash(), g.to_payload())
    svc = TranslationService(project, {})
    handle = svc.start(provider=MockProvider(
        translator=lambda t: "T:" + t.replace("灵石", "mana stone")))
    handle.join(timeout=30)
    assert handle.result["failed"] == 0
    return project


def test_impact_analyze_replace_undo(tmp_path):
    project = setup_project(tmp_path)
    # 用户把术语从 mana stone 改为 spirit stone（含快照历史）
    g = project.glossary
    g.add("灵石", ["spirit stone"], "货币")
    g.save()
    project.db.save_terms_revision(g.effective_hash(), g.to_payload())

    impact = TermImpactService(project)
    affected = impact.analyze()
    assert affected, "应检测到使用旧候选 mana stone 的段落"
    assert all(item["old"] == "mana stone" and item["new"] == "spirit stone"
               for item in affected)

    n = impact.replace_all(affected)
    assert n >= 2
    segs = project.db.list_segments(translatable=True)
    assert all("mana stone" not in (s["tgt_text"] or "") for s in segs)
    assert all("spirit stone" in (s["tgt_text"] or "") for s in segs
               if "灵石" in s["src_text"])

    # 撤销恢复
    m = impact.undo()
    assert m == n
    segs = project.db.list_segments(translatable=True)
    assert all("mana stone" in (s["tgt_text"] or "") for s in segs
               if "灵石" in s["src_text"])
    project.close()


def test_search_replace_preview_undo(tmp_path):
    project = setup_project(tmp_path)
    from core.pipeline import ReviewService
    review = ReviewService(project)
    preview = review.search_replace("mana stone", "spirit stone")
    assert len(preview.matches) >= 2
    n = preview.apply()
    assert n >= 2
    segs = project.db.list_segments(translatable=True)
    assert all("spirit stone" in (s["tgt_text"] or "") for s in segs
               if "灵石" in s["src_text"])
    label = review.undo_last()
    assert label and "替换" in label
    segs = project.db.list_segments(translatable=True)
    assert all("mana stone" in (s["tgt_text"] or "") for s in segs
               if "灵石" in s["src_text"])
    project.close()
