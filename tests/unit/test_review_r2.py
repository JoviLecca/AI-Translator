"""代码审查五轮循环 · 第2轮回归：引擎与 LLM 层（振假名规整、待复核清除）。"""
import asyncio
import re
from pathlib import Path

from core.engine import RunEngine
from core.pipeline import ImportService, ReviewService
from core.project import Project
from llm.mock import MockProvider
from storage.db import Database

ENTRIES = [
    {"token": "{r0}", "base": "一廣", "rt": "かずひろ", "style": "aozora"},
    {"token": "{r1}", "base": "魔法", "rt": "まほう", "style": "aozora"},
]


def make_db(tmp_path, text="一廣{r0}と魔法{r1}だ。", entries=ENTRIES, src_hash="rh"):
    db = Database(tmp_path / "w.db")
    doc = db.upsert_document("source/r.md", "md", "h")
    db.replace_segments(doc, [{
        "seq": 0, "text": text, "is_heading": False,
        "translatable": True, "src_hash": src_hash, "ruby": entries}], "cfgA")
    return db


def run(db, translator, policy, ruby=None):
    rows = db.list_segments(translatable=True, status_in=("pending",))
    engine = RunEngine(MockProvider(translator=translator, ruby=ruby), db,
                       cfg_hash="cfgA", src_lang="ja-JP", tgt_lang="zh-CN",
                       style_desc="t", terms=[], doc_tags={}, backoff=0.0,
                       ruby_policy=policy)
    return asyncio.run(engine.run_translation(1, rows))


def seg0(db):
    return db.list_segments(translatable=True)[0]


def test_partial_ruby_loss_restored(tmp_path):
    """部分丢失（留{r0}丢{r1}）：补回原注音 + 待复核（第2轮修复）。"""
    db = make_db(tmp_path)
    stats = run(db, lambda t: "T:" + re.sub(r"\{r1\}", "", t), "keep")
    assert stats["done"] == 1
    s = seg0(db)
    assert "{r0}" in s["tgt_text"]                 # 保留的槽不丢
    assert "《まほう》" in s["tgt_text"]            # 丢失的注音补回
    assert s["review_flag"] == 1
    db.close()


def test_duplicate_token_collapsed(tmp_path):
    """模型重复/伪造 token：折叠与清除（第2轮修复）。"""
    db = make_db(tmp_path, text="一廣{r0}だ。", entries=ENTRIES[:1], src_hash="dup")
    stats = run(db, lambda t: "T:" + t + "{r0}", "keep")
    assert stats["done"] == 1
    s = seg0(db)
    assert s["tgt_text"].count("{r0}") == 1        # 重复折叠
    assert s["review_flag"] == 1
    db.close()


def test_forged_token_without_src_stripped(tmp_path):
    """源文无注音槽但译文伪造 token：剥离 + 待复核（第2轮修复）。"""
    db = make_db(tmp_path, text="普通段落だ。", entries=[], src_hash="plain")
    stats = run(db, lambda t: "T:" + t + "{r5}", "keep")
    assert stats["done"] == 1
    s = seg0(db)
    assert "{r" not in s["tgt_text"] and s["review_flag"] == 1
    db.close()


def test_translate_full_success_unflagged(tmp_path):
    """全量成功路径不误伤：无待复核、译注音入库。"""
    db = make_db(tmp_path)
    stats = run(db, lambda t: "T:" + t, "translate",
                ruby=lambda i: {"r0": "卡兹希罗", "r1": "玛霍"})
    assert stats["done"] == 1
    s = seg0(db)
    assert "{r0}" in s["tgt_text"] and "{r1}" in s["tgt_text"]
    assert not s["review_flag"]
    assert "卡兹希罗" in (s["ruby_map"] or "")
    db.close()


def test_human_edit_clears_review_flag(tmp_path):
    """人工编辑清除待复核标记（第2轮修复）。"""
    proj = Project.create(tmp_path / "p", name="t", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock")
    f = tmp_path / "a.md"
    f.write_text("段落一。\n\n段落二。\n", encoding="utf-8")
    ImportService(proj).import_files([f])
    seg = proj.db.list_segments(translatable=True)[0]
    proj.db.update_segment(seg["id"], tgt="T:x", status="machine_translated",
                           review_flag=True)
    ReviewService(proj).edit(seg["id"], "人工修正")
    row = next(r for r in ReviewService(proj).segments() if r["id"] == seg["id"])
    assert row["status"] == "human_edited" and not row["review_flag"]
    proj.close()
