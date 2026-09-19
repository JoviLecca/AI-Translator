"""按文件范围翻译（用户反馈：此前只能全局翻译，无法只翻译某个文件）。

覆盖 `TranslationService.precheck(doc_ids)` / `start(doc_ids)` 的范围语义：
范围内的段被翻译、范围外的段保持 pending。
"""
from core.pipeline import ImportService, TranslationService
from core.project import Project
from llm.mock import MockProvider


def _import_two_docs(project, tmp_path):
    a = tmp_path / "a.md"
    a.write_text("# 甲\n\n第一章内容。\n", encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("# 乙\n\n第二章内容。\n", encoding="utf-8")
    ImportService(project).import_files([a, b])
    return {d["path"]: d["id"] for d in project.db.list_documents()}


def test_precheck_and_start_scoped_to_one_document(tmp_path):
    project = Project.create(tmp_path / "p", name="scope", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    docs = _import_two_docs(project, tmp_path)
    doc_a, doc_b = docs["source/a.md"], docs["source/b.md"]
    svc = TranslationService(project, {"concurrency": 1, "context_segments": 0})

    # 预检只统计选定文件
    pre_all = svc.precheck()
    pre_a = svc.precheck([doc_a])
    assert pre_a["doc_ids"] == [doc_a]
    assert pre_a["doc_count"] == 1
    assert pre_all["doc_count"] == 2
    assert 0 < pre_a["pending"] < pre_all["pending"]

    handle = svc.start(provider=MockProvider(), doc_ids=[doc_a])
    handle.join(timeout=60)
    assert handle.result["failed"] == 0, handle.result

    by_doc: dict[int, list[str]] = {}
    for s in project.db.list_segments(translatable=True):
        by_doc.setdefault(s["doc_id"], []).append(s["status"])

    assert by_doc[doc_a], "a.md 应有可译段"
    assert all(x == "machine_translated" for x in by_doc[doc_a]), by_doc[doc_a]
    assert all(x == "pending" for x in by_doc[doc_b]), by_doc[doc_b]  # b 未被动过

    # 范围内已无待译段 → 明确报错，而不是静默翻译别的文件
    try:
        svc.start(provider=MockProvider(), doc_ids=[doc_a])
    except RuntimeError as e:
        assert "所选范围" in str(e)
    else:
        raise AssertionError("范围内无待译段时应抛 RuntimeError")
    project.close()


def test_precheck_without_scope_covers_all_documents(tmp_path):
    """doc_ids=None 仍为全项目口径（保持原有行为）。"""
    project = Project.create(tmp_path / "p2", name="all", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    docs = _import_two_docs(project, tmp_path)
    svc = TranslationService(project, {"concurrency": 1, "context_segments": 0})

    pre = svc.precheck()
    assert pre["doc_ids"] is None
    assert pre["doc_count"] == 2

    handle = svc.start(provider=MockProvider())
    handle.join(timeout=60)
    assert handle.result["failed"] == 0, handle.result
    assert all(s["status"] == "machine_translated"
               for s in project.db.list_segments(translatable=True))
    assert len(docs) == 2
    project.close()
