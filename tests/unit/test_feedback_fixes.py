"""测试反馈七项修复的回归测试（2026-09-18）。"""
import asyncio
from pathlib import Path

import httpx

from adapters import get_adapter
from core.pipeline import ImportService, TranslationService
from core.project import Project
from llm.mock import MockProvider
from storage.db import Database


# ---------- #2/#3 空白段 ----------
def test_md_placeholder_only_paragraph_passthrough(tmp_path):
    """仅图片/占位符构成的段落 → 透传段，不进翻译队列（反馈 #2/#3）。"""
    p = tmp_path / "a.md"
    p.write_text("# 标题\n\n正文一段。\n\n![图](pic.png)\n\n另一段。\n", encoding="utf-8")
    model = get_adapter("md").parse(p)
    translatable = [b.text for b in model.blocks if b.translatable]
    assert "正文一段。" in "".join(translatable)
    img = next(b for b in model.blocks if "pic.png" in (b.meta.get("span") and "x" or "") or
               not b.translatable)
    # 图片独段不可译
    assert all(not ("{0}" == b.text.strip() and b.translatable) for b in model.blocks)
    seg_texts = [b.text for b in model.blocks if b.translatable]
    assert all(t.strip() and t.strip() != "{0}" for t in seg_texts)


def test_txt_invisible_whitespace_line_is_filler(tmp_path):
    """零宽字符/不可见空白行 → 透传（反馈 #2）。"""
    p = tmp_path / "a.txt"
    p.write_text("第一段。\n\u200b\n第二段。\n", encoding="utf-8")
    model = get_adapter("txt").parse(p)
    kinds = [(b.text, b.translatable) for b in model.blocks]
    assert any(t == "\u200b" and not tr for t, tr in kinds)
    assert sum(1 for _, tr in kinds if tr) == 2


def test_engine_blank_segment_no_api_no_repeat(tmp_path):
    """历史遗留空白可译段：不调 API、译文置空、不产生重复内容（反馈 #3）。"""
    db = Database(tmp_path / "w.db")
    doc = db.upsert_document("source/a.md", "md", "h")
    blocks = [
        {"seq": 0, "text": "　", "is_heading": False, "translatable": True,
         "src_hash": "blank-1"},
        {"seq": 1, "text": "正文。", "is_heading": False, "translatable": True,
         "src_hash": "real-1"},
        {"seq": 2, "text": "\u200b", "is_heading": False, "translatable": True,
         "src_hash": "blank-2"},
    ]
    db.replace_segments(doc, blocks, "cfgA")
    from core.engine import RunEngine
    provider = MockProvider()
    rows = db.list_segments(translatable=True, status_in=("pending",))
    engine = RunEngine(provider, db, cfg_hash="cfgA", src_lang="ja-JP",
                       tgt_lang="zh-CN", style_desc="t", terms=[], doc_tags={},
                       backoff=0.0)
    stats = asyncio.run(engine.run_translation(1, rows))
    assert stats["failed"] == 0 and stats["done"] == 3
    segs = db.list_segments(translatable=True)
    blanks = [s for s in segs if not s["src_text"].strip()]
    assert all((s["tgt_text"] or "") == "" for s in blanks)  # 空白段译文为空，无重复
    real = next(s for s in segs if s["src_text"] == "正文。")
    assert real["tgt_text"].startswith("T:")
    db.close()


def test_blank_structure_preserved_on_export(tmp_path):
    """多空行结构在导出中原样保持（反馈 #2：源文译文保持空行格式即可）。"""
    project = Project.create(tmp_path / "p", name="t", src_lang="ja-JP",
                             tgt_lang="zh-CN", provider_id="mock")
    f = tmp_path / "a.txt"
    f.write_text("一段。\n\n\n二段。\n\u200b\n三段。\n", encoding="utf-8")
    ImportService(project).import_files([f])
    svc = TranslationService(project, {})
    h = svc.start(provider=MockProvider())
    h.join(timeout=30)
    assert h.result["failed"] == 0
    doc_id = project.db.list_documents()[0]["id"]
    from core.pipeline import ExportService
    r = ExportService(project).export([doc_id], mode="target")[0]
    out = Path(r["out"]).read_text(encoding="utf-8")
    assert out == "T:一段。\n\n\nT:二段。\n\u200b\nT:三段。\n"
    project.close()


# ---------- #5 模型列表 ----------
def test_detect_provider_type():
    from core.appconfig import detect_provider_type
    assert detect_provider_type("https://api.anthropic.com") == "anthropic"
    assert detect_provider_type("https://generativelanguage.googleapis.com/v1") == "gemini"
    assert detect_provider_type("https://api.deepseek.com/v1") == "openai"
    assert detect_provider_type("") == "openai"


def test_list_models_providers():
    from llm.anthropic_provider import AnthropicProvider
    from llm.gemini_provider import GeminiProvider
    from llm.openai_compat import OpenAICompatProvider

    def oa_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "deepseek-chat"},
                                                  {"id": "deepseek-reasoner"},
                                                  {"id": "deepseek-chat"}]})
    p = OpenAICompatProvider({"id": "x", "model": "m", "base_url": "https://api.test/v1"},
                             "k", transport=httpx.MockTransport(oa_handler))
    assert asyncio.run(p.list_models()) == ["deepseek-chat", "deepseek-reasoner"]

    def an_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "claude-a"}, {"id": "claude-b"}]})
    p2 = AnthropicProvider({"id": "a", "model": "m",
                            "base_url": "https://api.anthropic.com"}, "k",
                           transport=httpx.MockTransport(an_handler))
    assert asyncio.run(p2.list_models()) == ["claude-a", "claude-b"]

    def gm_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "models/gemini-2"},
                                                     {"name": "models/gemini-1"}]})
    p3 = GeminiProvider({"id": "g", "model": "m",
                         "base_url": "https://x.googleapis.com"}, "k",
                        transport=httpx.MockTransport(gm_handler))
    assert asyncio.run(p3.list_models()) == ["gemini-1", "gemini-2"]


# ---------- #6 费用下线 ----------
def test_estimate_tokens_only():
    from core.costs import estimate_run
    est = estimate_run(1000, slide=2)
    assert set(est) == {"tokens_in", "tokens_out"}     # 无 cost 字段
    assert est["tokens_in"] > 0 and est["tokens_out"] > 0
