from pathlib import Path

from core.pipeline import ImportService, TranslationService
from core.project import Project
from core.term_impact import SOURCE_CACHE, SOURCE_NONE, SOURCE_REVISION, TermImpactService
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


def test_reload_glossary_records_external_change(tmp_path):
    """外部（Excel）改过 glossary.csv 后重载 → 也能比出受影响的已译段落。

    回归：外部改动原先完全不落快照，最近两份快照都是改之前的状态，
    diff 什么都比不出来（用户反馈：Excel 里改完术语，这里列不出旧译文）。
    """
    project = setup_project(tmp_path)
    # 直接改文件，模拟用户在 Excel 里把候选从 mana stone 改成 spirit stone
    (project.root / "glossary.csv").write_text(
        "源语言,目标语,注释\n灵石,spirit stone,货币\n", encoding="utf-8-sig")
    assert project.glossary.external_changed(), "应检测到外部改动"

    impact = TermImpactService(project)
    assert impact.history_source() == SOURCE_NONE or impact.analyze() is not None

    assert project.reload_glossary() is True, "重载应报告内容已变化"
    assert project.glossary.get("灵石").candidates == ["spirit stone"]

    affected = impact.analyze()
    assert affected, "外部改动后应能列出仍在使用旧候选的段落"
    assert all(it["old"] == "mana stone" and it["new"] == "spirit stone"
               for it in affected)
    assert impact.history_source() == SOURCE_REVISION

    # 替换同样可用
    assert impact.replace_all(affected) >= 2
    segs = project.db.list_segments(translatable=True)
    assert all("mana stone" not in (s["tgt_text"] or "") for s in segs)
    project.close()


def test_reload_glossary_reports_no_change(tmp_path):
    """内容没变时重载返回 False，且不写重复快照。"""
    project = setup_project(tmp_path)
    before = len(project.db.list_terms_revisions(50))
    assert project.reload_glossary() is False
    assert len(project.db.list_terms_revisions(50)) == before, "内容没变不应新增快照"
    project.close()


def test_snapshot_terms_skips_unchanged(tmp_path):
    """snapshot_terms 内容未变时跳过 —— 否则会把上一次真实变更挤出对比窗口。"""
    project = setup_project(tmp_path)
    n0 = len(project.db.list_terms_revisions(50))
    assert project.snapshot_terms() is False          # 内容没变
    assert len(project.db.list_terms_revisions(50)) == n0
    project.glossary.add("灵石", ["soulstone"], "货币")
    assert project.snapshot_terms() is True           # 变了才写
    assert len(project.db.list_terms_revisions(50)) == n0 + 1
    project.close()


def test_analyze_falls_back_to_terms_cache(tmp_path):
    """老项目没有快照历史时，退回 terms 缓存表也能比出差异。

    场景：项目在本功能之前就存在（terms_revision 为空），但 terms 缓存表里
    还留着上次保存的候选。
    """
    project = setup_project(tmp_path)
    # 老库的 terms 缓存表里还留着上次保存的候选（正常路径由 snapshot_terms 写入）
    project.db.upsert_term("灵石", ["mana stone"], "货币",
                           origin="manual", status="approved")
    project.db.execute("DELETE FROM terms_revision")   # 模拟升级前的老库
    assert project.db.list_terms_revisions(2) == []

    impact = TermImpactService(project)
    assert impact.history_source() == SOURCE_CACHE, impact.history_source()

    # 术语改成新候选（仅内存，模拟外部改完被读入）
    project.glossary.add("灵石", ["spirit stone"], "货币")
    affected = impact.analyze()
    assert affected, "缓存表兜底应能列出仍在使用旧候选的段落"
    assert all(it["old"] == "mana stone" for it in affected)
    project.close()


def test_history_source_none_without_any_state(tmp_path):
    """既无快照也无缓存 → 明确报告 none（UI 据此给出原因提示）。"""
    project = Project.create(tmp_path / "empty", name="e", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    project.db.execute("DELETE FROM terms_revision")
    project.db.execute("DELETE FROM terms")
    impact = TermImpactService(project)
    assert impact.history_source() == SOURCE_NONE
    assert impact.analyze() == []
    project.close()


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


def test_can_undo_reflects_undo_stack(tmp_path):
    """can_undo() 供 UI 决定「撤销」按钮是否可用。"""
    project = setup_project(tmp_path)
    g = project.glossary
    g.add("灵石", ["spirit stone"], "货币")
    g.save()
    project.db.save_terms_revision(g.effective_hash(), g.to_payload())

    impact = TermImpactService(project)
    assert impact.can_undo() is False          # 还没替换过
    assert impact.undo() == 0                   # 撤销无副作用

    affected = impact.analyze()
    assert affected
    impact.replace_all(affected)
    assert impact.can_undo() is True
    impact.undo()
    assert impact.can_undo() is False
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
