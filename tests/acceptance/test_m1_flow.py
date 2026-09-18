"""M1 验收测试（设计 §11 M1）：5 个 md 章节跑通全流程。

新建项目 → 导入 → 手动术语表 → 预检 → 批量翻译（Mock）→ 双栏校对（编辑/确认）
→ 导出 target / 双语交错；另含 401 暂停→修复→断点续跑 场景。
"""
from pathlib import Path

from core.pipeline import ExportService, ImportService, ReviewService, TranslationService
from core.project import Project
from llm.mock import MockProvider

TERMS = {"灵石": "spirit stone", "宗门": "sect", "林凡": "Lin Fan"}


def term_translator(text: str) -> str:
    for src, tgt in TERMS.items():
        text = text.replace(src, tgt)
    return "T:" + text


def chapter_md(i: int) -> str:
    return f"""---
book: 测试小说
---

# 第{i}章 试炼

林凡走进宗门大殿，掌门递给他一块灵石。

- 灵石可兑换丹药
- 宗门每月发放俸禄

> 林凡心中一动：机会来了。
"""


def make_project(tmp_path: Path) -> Project:
    root = tmp_path / "novel"
    project = Project.create(
        root, name="测试小说", src_lang="zh-CN", tgt_lang="en-US",
        style_preset="literary", provider_id="mock")
    return project


def test_m1_full_flow(tmp_path):
    project = make_project(tmp_path)
    assert (project.root / "source").is_dir()
    assert (project.root / "target").is_dir()

    # 1) 准备 5 个章节文件并导入
    files = []
    for i in range(1, 6):
        f = tmp_path / f"ch{i}.md"
        f.write_text(chapter_md(i), encoding="utf-8")
        files.append(f)
    imported, errors = ImportService(project).import_files(files)
    assert not errors and len(imported) == 5
    total_segs = sum(r["stats"]["translatable"] for r in imported)
    assert total_segs >= 25  # 每章 ≥5 个可译段

    # 2) 手动术语表（M1：不自动归纳）
    g = project.glossary
    g.add("灵石", ["spirit stone"], "修炼货币")
    g.add("宗门", ["sect"], "组织")
    g.add("林凡", ["Lin Fan"], "主角")
    g.save()
    assert (project.root / "glossary.csv").exists()

    # 3) 预检：待译段、成本预估、术语数
    svc = TranslationService(project, {"concurrency": 4, "context_segments": 2})
    pre = svc.precheck()
    assert pre["pending"] == total_segs
    assert pre["glossary_terms"] == 3
    assert pre["estimate"] is None or pre["estimate"]["tokens_in"] > 0

    # 4) 批量翻译（Mock Provider，遵守术语表）
    handle = svc.start(provider=MockProvider(translator=term_translator))
    handle.join(timeout=60)
    assert not handle.running
    result = handle.result
    assert result["done"] == total_segs and result["failed"] == 0, result

    segs = project.db.list_segments(translatable=True)
    assert all(s["status"] in ("machine_translated", "human_edited", "confirmed") for s in segs)
    for s in segs:
        if "灵石" in s["src_text"]:
            assert "spirit stone" in s["tgt_text"]

    # 5) 校对：人工修正一段 + 确认
    review = ReviewService(project)
    rows = review.segments()
    target = next(r for r in rows if "机会来了" in r["src"])
    review.edit(target["id"], "T:Lin Fan's heart stirred: here was his chance.")
    review.confirm(target["id"])
    edited = next(r for r in review.segments() if r["id"] == target["id"])
    assert edited["status"] == "confirmed"

    # 6) 导出：target 模式 + 双语交错模式
    doc_ids = [d["id"] for d in project.db.list_documents()]
    exp = ExportService(project)
    results = exp.export(doc_ids, mode="target")
    assert all(r["ok"] for r in results), results
    assert len(list((project.root / "target").glob("*.en.md"))) == 5
    out1 = (project.root / "target" / "ch1.en.md").read_text(encoding="utf-8")
    assert "T:# 第1章 试炼" in out1
    assert "spirit stone" in out1
    assert "book: 测试小说" in out1  # front matter 透传

    bi = exp.export(doc_ids[:1], mode="bi_inter")
    assert bi[0]["ok"]
    bi_text = Path(bi[0]["out"]).read_text(encoding="utf-8")
    assert "宗门" in bi_text and "sect" in bi_text

    # 7) Run 记录与成本统计
    run_row = project.db.get_run(handle.run_id)
    assert run_row["status"] == "completed"
    assert run_row["tokens_in"] > 0 and run_row["cost"] > 0

    project.close()


def test_m1_pause_and_resume(tmp_path):
    project = make_project(tmp_path)
    f = tmp_path / "ch1.md"
    f.write_text(chapter_md(1), encoding="utf-8")
    ImportService(project).import_files([f])
    project.glossary.add("灵石", ["spirit stone"])
    project.glossary.save()

    svc = TranslationService(project, {})
    # 密钥失效 → 暂停
    h1 = svc.start(provider=MockProvider(auth_fail=True))
    h1.join(timeout=30)
    assert h1.result["paused"]
    stats = project.db.segment_stats()
    assert stats.get("pending", 0) > 0
    # 修复后重跑 → 断点续跑完成
    h2 = svc.start(provider=MockProvider(translator=term_translator))
    h2.join(timeout=30)
    assert h2.result["done"] > 0 and not h2.result["paused"]
    assert project.db.segment_stats().get("pending", 0) == 0
    project.close()


def test_project_lock(tmp_path):
    project = make_project(tmp_path)
    from core.project import ProjectLockError
    try:
        Project.open(project.root)
        assert False, "双开应被锁拒绝"
    except ProjectLockError:
        pass
    project.close()
    # 关闭后可重新打开
    p2 = Project.open(project.root)
    p2.close()
