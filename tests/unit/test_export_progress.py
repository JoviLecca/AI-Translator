"""导出进度、取消与同名文件处理（用户反馈：导出无进度/完成无提示；二次导出静默覆盖）。"""
from pathlib import Path

from core.pipeline import ExportService, ImportService, TranslationService
from core.project import Project
from llm.mock import MockProvider


def _project_with_two_docs(tmp_path):
    project = Project.create(tmp_path / "p", name="p", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    files = []
    for name in ("a.md", "b.md"):
        f = tmp_path / name
        f.write_text(f"# {name}\n\n{name} 的正文。\n", encoding="utf-8")
        files.append(f)
    ImportService(project).import_files(files)
    handle = TranslationService(project, {"concurrency": 1, "context_segments": 0}).start(
        provider=MockProvider())
    handle.join(timeout=60)
    assert handle.result["failed"] == 0, handle.result
    return project


def test_export_reports_progress_per_document(tmp_path):
    """每个文档处理前上报一次，结束时再上报一次完成（供进度条使用）。"""
    project = _project_with_two_docs(tmp_path)
    ids = [d["id"] for d in project.db.list_documents()]
    seen: list[tuple[int, int, str]] = []

    results = ExportService(project).export(
        ids, mode="target", on_progress=lambda d, t, p: seen.append((d, t, p)))

    assert all(r["ok"] for r in results), results
    assert len(results) == 2
    assert [d for d, _, _ in seen] == [0, 1, 2], seen      # 0/2 → 1/2 → 2/2
    assert all(t == 2 for _, t, _ in seen), seen
    assert seen[0][2].endswith(".md"), seen                # 带当前文档路径
    assert seen[-1][2] == "", seen                         # 完成上报不带路径
    project.close()


def test_export_cancel_stops_remaining_documents(tmp_path):
    """取消后停止处理剩余文档，已导出的结果保留。"""
    project = _project_with_two_docs(tmp_path)
    ids = [d["id"] for d in project.db.list_documents()]
    calls = {"n": 0}

    def cancel_check() -> bool:
        calls["n"] += 1
        return calls["n"] > 1        # 处理完第一个文档后取消

    results = ExportService(project).export(ids, mode="target",
                                            cancel_check=cancel_check)
    assert len(results) == 1, results
    assert results[0]["ok"]
    project.close()


def test_export_without_callbacks_unchanged(tmp_path):
    """不传回调时行为与旧版一致（向后兼容）。"""
    project = _project_with_two_docs(tmp_path)
    ids = [d["id"] for d in project.db.list_documents()]
    results = ExportService(project).export(ids, mode="target")
    assert len(results) == 2 and all(r["ok"] for r in results)
    project.close()


# ---------- 同名文件处理（用户反馈：二次导出会静默覆盖已润色过的译文） ----------

def test_export_overwrite_is_default_and_replaces_file(tmp_path):
    """默认策略仍是覆盖（沿用旧行为），且会如实上报「覆盖了旧文件」。"""
    project = _project_with_two_docs(tmp_path)
    ids = [d["id"] for d in project.db.list_documents()]
    svc = ExportService(project)
    first = svc.export(ids, mode="target")
    out = Path(first[0]["out"])
    out.write_text("人工润色过的旧译文", encoding="utf-8")

    second = svc.export(ids, mode="target")
    assert Path(second[0]["out"]) == out, "默认应写回同一路径"
    assert out.read_text(encoding="utf-8") != "人工润色过的旧译文"
    assert second[0]["overwrote"] is True
    # 没有产生副本
    assert len(list(project.target_dir().glob("*.md"))) == 2
    project.close()


def test_export_keep_both_writes_copy_and_keeps_old(tmp_path):
    """keep_both：不覆盖，另存为 xxx(2).md，两份都保留。"""
    project = _project_with_two_docs(tmp_path)
    ids = [d["id"] for d in project.db.list_documents()]
    svc = ExportService(project)
    first = svc.export(ids, mode="target")
    kept = Path(first[0]["out"])
    kept.write_text("人工润色过的旧译文", encoding="utf-8")

    second = svc.export(ids, mode="target", on_conflict="keep_both")
    copy = Path(second[0]["out"])
    assert copy != kept
    assert copy.name.endswith("(2).md"), copy.name
    assert copy.exists()
    assert kept.read_text(encoding="utf-8") == "人工润色过的旧译文", "旧文件必须保住"
    assert any("另存为" in w for w in second[0]["warnings"]), second[0]["warnings"]
    project.close()


def test_export_skip_leaves_existing_file_untouched(tmp_path):
    """skip：已存在就跳过，既不覆盖也不产生副本。"""
    project = _project_with_two_docs(tmp_path)
    ids = [d["id"] for d in project.db.list_documents()]
    svc = ExportService(project)
    first = svc.export(ids, mode="target")
    kept = Path(first[0]["out"])
    kept.write_text("人工润色过的旧译文", encoding="utf-8")

    second = svc.export(ids, mode="target", on_conflict="skip")
    assert second[0]["skipped"] is True
    assert second[0]["out"] is None
    assert kept.read_text(encoding="utf-8") == "人工润色过的旧译文"
    assert not list(project.target_dir().glob("*(2).md"))
    project.close()


def test_target_path_matches_actual_export_path(tmp_path):
    """target_path（供导出对话框统计「已存在同名文件」）必须与实际写出路径一致。"""
    project = _project_with_two_docs(tmp_path)
    svc = ExportService(project)
    ids = [d["id"] for d in project.db.list_documents()]
    predicted = [svc.target_path(r) for r in project.db.list_documents()]
    results = svc.export(ids, mode="target")
    assert [Path(r["out"]) for r in results] == predicted
    # 跨格式导出（md → txt）也要一致
    predicted_txt = [svc.target_path(r, "txt") for r in project.db.list_documents()]
    results2 = svc.export(ids, mode="target", output_format="txt")
    assert [Path(r["out"]) for r in results2] == predicted_txt
    project.close()
