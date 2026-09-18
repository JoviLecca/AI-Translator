"""引擎振假名三策略校验链测试（增补设计 §1.5）。"""
import asyncio
import json
import re

from core.engine import RunEngine
from llm.mock import MockProvider
from storage.db import Database


def make_ruby_db(tmp_path, cfg="cfgA"):
    db = Database(tmp_path / "work.db")
    doc = db.upsert_document("source/r.md", "md", "h")
    entries = [{"token": "{r0}", "base": "一廣", "rt": "かずひろ", "style": "aozora"}]
    blocks = [{
        "seq": 0, "text": "僕は一廣{r0}だ。", "is_heading": False,
        "translatable": True, "src_hash": "ruby-h0", "ruby": entries,
    }]
    db.replace_segments(doc, blocks, cfg)
    return db, doc


def run(provider, db, policy, **kw):
    rows = db.list_segments(translatable=True, status_in=("pending",))
    engine = RunEngine(provider, db, cfg_hash="cfgA", src_lang="ja-JP", tgt_lang="zh-CN",
                       style_desc="t", terms=[], doc_tags={}, backoff=0.0,
                       ruby_policy=policy, **kw)
    return asyncio.run(engine.run_translation(1, rows))


def seg(db):
    return db.list_segments(translatable=True)[0]


def test_drop_policy_strips_residual(tmp_path):
    db, _ = make_ruby_db(tmp_path)
    # 模拟模型违规残留 token：drop 策略应剥离并标待复核
    stats = run(MockProvider(translator=lambda t: "T:" + t), db, "drop")
    assert stats["done"] == 1
    s = seg(db)
    assert "{r0}" not in s["tgt_text"]
    assert s["tgt_text"].startswith("T:")
    db.close()


def test_keep_policy_preserves_token(tmp_path):
    db, _ = make_ruby_db(tmp_path)
    stats = run(MockProvider(translator=lambda t: "T:" + t), db, "keep")
    assert stats["done"] == 1
    s = seg(db)
    assert "{r0}" in s["tgt_text"]
    assert not s["review_flag"]
    assert not s["ruby_map"]
    db.close()


def test_keep_policy_missing_token_restores_original(tmp_path):
    db, _ = make_ruby_db(tmp_path)
    # 模拟模型两次都丢 token：单段重试也没救 → 补回原注音 + 待复核
    stats = run(MockProvider(translator=lambda t: re.sub(r"\{r\d+\}", "", "T:" + t)),
                db, "keep")
    assert stats["done"] == 1
    s = seg(db)
    assert "《かずひろ》" in s["tgt_text"]
    assert s["review_flag"] == 1
    db.close()


def test_translate_policy_saves_ruby_map(tmp_path):
    db, _ = make_ruby_db(tmp_path)
    seg_id = seg(db)["id"]
    stats = run(MockProvider(translator=lambda t: "T:" + t,
                             ruby=lambda i: {"r0": "卡兹希罗"}), db, "translate")
    assert stats["done"] == 1
    s = seg(db)
    assert "{r0}" in s["tgt_text"]
    assert json.loads(s["ruby_map"]) == {"{r0}": "卡兹希罗"}
    db.close()


def test_translate_policy_missing_ruby_falls_back(tmp_path):
    db, _ = make_ruby_db(tmp_path)
    # 模型保留 token 但漏给 ruby 字段 → 降级原假名 + 待复核（导出仍正确还原）
    stats = run(MockProvider(translator=lambda t: "T:" + t), db, "translate")
    assert stats["done"] == 1
    s = seg(db)
    assert s["review_flag"] == 1
    db.close()


def test_file_level_policy_override(tmp_path):
    db, doc = make_ruby_db(tmp_path)
    # 文件标签覆盖：drop（项目级 keep）
    import json as _json
    db.set_doc_fields(doc, tags={"ruby_policy": "drop"})
    rows = db.list_segments(translatable=True, status_in=("pending",))
    engine = RunEngine(MockProvider(translator=lambda t: "T:" + t), db,
                       cfg_hash="cfgA", src_lang="ja-JP", tgt_lang="zh-CN",
                       style_desc="t", terms=[], doc_tags={doc: {"ruby_policy": "drop"}},
                       backoff=0.0, ruby_policy="keep")
    asyncio.run(engine.run_translation(1, rows))
    s = seg(db)
    assert "{r0}" not in s["tgt_text"]
    db.close()
