"""M3 验收测试：Provider 类型分发 + 双语导出端到端。"""
from pathlib import Path

from core.pipeline import ExportService, ImportService, TranslationService
from core.project import Project
from llm.mock import MockProvider


def test_provider_type_dispatch(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "p", name="t", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="anth")
    cfg = {"providers": [
        {"id": "anth", "name": "A", "type": "anthropic", "base_url": "https://x",
         "model": "m", "price_in": 0, "price_out": 0},
        {"id": "gem", "name": "G", "type": "gemini", "base_url": "https://y",
         "model": "m2", "price_in": 0, "price_out": 0},
        {"id": "oa", "name": "O", "type": "openai", "base_url": "https://z",
         "model": "m3", "price_in": 0, "price_out": 0},
    ]}
    import storage.secrets as secrets
    monkeypatch.setattr(secrets, "get_api_key", lambda pid: "test-key")
    import core.pipeline as pipeline
    monkeypatch.setattr(pipeline.secrets, "get_api_key", lambda pid: "test-key")

    svc = TranslationService(project, cfg)
    from llm.anthropic_provider import AnthropicProvider
    assert isinstance(svc.build_provider(), AnthropicProvider)
    project.update_config(provider_id="gem")
    from llm.gemini_provider import GeminiProvider
    assert isinstance(svc.build_provider(), GeminiProvider)
    project.update_config(provider_id="oa")
    from llm.openai_compat import OpenAICompatProvider
    assert isinstance(svc.build_provider(), OpenAICompatProvider)
    project.close()


def test_bilingual_export_e2e(tmp_path):
    project = Project.create(tmp_path / "p", name="t", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    f = tmp_path / "a.md"
    f.write_text("# 标题\n\n正文一段。\n", encoding="utf-8")
    ImportService(project).import_files([f])
    svc = TranslationService(project, {})
    h = svc.start(provider=MockProvider(translator=lambda t: "T:" + t))
    h.join(timeout=30)
    assert h.result["failed"] == 0
    doc_id = project.db.list_documents()[0]["id"]

    r1 = ExportService(project).export([doc_id], mode="bi_table")[0]
    t1 = Path(r1["out"]).read_text(encoding="utf-8")
    assert "| 原文 | 译文 |" in t1 and "T:# 标题" in t1 and "标题" in t1

    r2 = ExportService(project).export([doc_id], mode="bi_inter")[0]
    t2 = Path(r2["out"]).read_text(encoding="utf-8")
    assert "# 标题" in t2 and "T:# 标题" in t2
    project.close()
