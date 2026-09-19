"""S1-S4 冲刺验收测试：OCR 替代 + 跨格式导出 + 文件夹自然排序。"""
from pathlib import Path

from core.pipeline import ExportService, ImportService, TranslationService
from core.project import Project
from llm.mock import MockProvider


def make_project(tmp_path, src_lang="ja-JP", tgt_lang="zh-CN"):
    return Project.create(tmp_path / "p", name="s2", src_lang=src_lang,
                          tgt_lang=tgt_lang, provider_id="mock")


def test_cross_format_export(tmp_path):
    """S2：md 源 → 跨格式导出为 txt/html/docx。"""
    proj = make_project(tmp_path)
    f = tmp_path / "a.md"
    f.write_text("# 标题\n\n段落一。\n\n段落二。\n", encoding="utf-8")
    ImportService(proj).import_files([f])
    svc = TranslationService(proj, {})
    h = svc.start(provider=MockProvider())
    h.join(timeout=30)
    assert h.result["failed"] == 0
    doc_id = proj.db.list_documents()[0]["id"]

    exp = ExportService(proj)
    # 默认（跟随源文件）
    r0 = exp.export([doc_id], mode="target")[0]
    assert r0["ok"] and r0["out"].endswith(".md")

    # 跨格式：txt
    r1 = exp.export([doc_id], mode="target", output_format="txt")[0]
    assert r1["ok"] and r1["out"].endswith(".txt")
    t1 = Path(r1["out"]).read_text(encoding="utf-8")
    assert "T:标题" in t1 and "T:段落一。" in t1

    # 跨格式：html
    r2 = exp.export([doc_id], mode="target", output_format="html")[0]
    assert r2["ok"] and r2["out"].endswith(".html")
    t2 = Path(r2["out"]).read_text(encoding="utf-8")
    assert "<h1>" in t2 and "T:标题" in t2 and "<p>" in t2

    # 跨格式：docx
    r3 = exp.export([doc_id], mode="target", output_format="docx")[0]
    assert r3["ok"] and r3["out"].endswith(".docx")
    from docx import Document
    doc = Document(r3["out"])
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    assert "T:标题" in paras

    # 跨格式导出带警告
    assert any("转换" in w or "重排" in w for r in [r1, r2, r3]
               for w in r.get("warnings", []))
    proj.close()


def test_cross_format_preserves_headings(tmp_path):
    """S2：跨格式导出保留标题层级。"""
    proj = make_project(tmp_path)
    f = tmp_path / "a.md"
    f.write_text("# 大标题\n\n正文段。\n\n## 小标题\n\n更多正文。\n", encoding="utf-8")
    ImportService(proj).import_files([f])
    TranslationService(proj, {}).start(provider=MockProvider()).join(timeout=30)
    doc_id = proj.db.list_documents()[0]["id"]
    r = ExportService(proj).export([doc_id], mode="target", output_format="html")[0]
    t = Path(r["out"]).read_text(encoding="utf-8")
    assert "<h1>" in t and "<h2>" in t   # 标题层级保留
    proj.close()


def test_natural_sort_for_folder_import(tmp_path):
    """S3：文件名自然排序（page_2 < page_10）。"""
    import re
    def nat_key(p):
        return [int(s) if s.isdigit() else s.lower()
                for s in re.split(r"(\d+)", Path(p).stem)]
    files = ["page_10.png", "page_2.png", "page_1.png", "cover.png"]
    sorted_files = sorted(files, key=nat_key)
    assert sorted_files == ["cover.png", "page_1.png", "page_2.png", "page_10.png"]
