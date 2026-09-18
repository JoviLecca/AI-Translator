import asyncio

from core.engine import RunEngine
from llm.mock import MockProvider
from storage.db import Database


def make_db(tmp_path, n_docs=1, segs_per_doc=5, cfg="cfgA"):
    db = Database(tmp_path / "work.db")
    doc_ids = []
    for d in range(n_docs):
        doc = db.upsert_document(f"source/{d}.md", "md", f"h{d}")
        blocks = [{
            "seq": i, "text": f"第{i}段：林凡握着一块灵石。", "is_heading": False,
            "translatable": True, "src_hash": f"shared-{i}",
        } for i in range(segs_per_doc)]
        db.replace_segments(doc, blocks, cfg)
        doc_ids.append(doc)
    return db, doc_ids


def run(engine, run_id, rows):
    return asyncio.run(engine.run_translation(run_id, rows))


def make_engine(provider, db, **kw):
    return RunEngine(provider, db, cfg_hash="cfgA", src_lang="zh-CN", tgt_lang="en-US",
                     style_desc="测试", terms=[], doc_tags={}, backoff=0.0, **kw)


def pending_rows(db):
    return db.list_segments(translatable=True, status_in=("pending",))


def test_basic_run(tmp_path):
    db, docs = make_db(tmp_path)
    rows = pending_rows(db)
    run_id = db.create_run("translate", len(rows))
    stats = run(make_engine(MockProvider(), db), run_id, rows)
    assert stats["done"] == 5 and stats["failed"] == 0
    assert stats["tokens_in"] > 0 and stats["cost"] > 0
    assert db.get_run(run_id)["status"] == "completed"
    assert all(s["status"] == "machine_translated" for s in db.list_segments())
    assert all(s["tgt_text"].startswith("T:") for s in db.list_segments())
    db.close()


def test_tm_precheck_skips_api(tmp_path):
    db, docs = make_db(tmp_path, n_docs=2)
    for seg in db.list_segments(doc_id=docs[0]):
        db.update_segment(seg["id"], tgt="T:" + seg["src_text"], status="confirmed")
    rows = pending_rows(db)
    assert len(rows) == 5
    provider = MockProvider()
    run_id = db.create_run("translate", len(rows))
    stats = run(make_engine(provider, db), run_id, rows)
    assert stats["tm"] == 5 and stats["done"] == 5
    assert provider.calls == 0  # 全部命中翻译记忆，不耗 API（设计 §7.5）
    db.close()


def test_retry_then_success(tmp_path):
    db, docs = make_db(tmp_path)
    rows = pending_rows(db)
    run_id = db.create_run("translate", len(rows))
    stats = run(make_engine(MockProvider(retry_failures=2), db), run_id, rows)
    assert stats["done"] == 5
    db.close()


def test_auth_error_pauses_run(tmp_path):
    db, docs = make_db(tmp_path)
    rows = pending_rows(db)
    run_id = db.create_run("translate", len(rows))
    stats = run(make_engine(MockProvider(auth_fail=True), db), run_id, rows)
    assert stats["paused"] and stats["error"]
    assert db.get_run(run_id)["status"] == "paused"
    # 断点续跑：换好密钥的 Provider 重跑 → 完成
    rows2 = pending_rows(db)
    run_id2 = db.create_run("translate", len(rows2))
    stats2 = run(make_engine(MockProvider(), db), run_id2, rows2)
    assert stats2["done"] == 5 and not stats2["paused"]
    db.close()


def test_content_policy_only_fails_segment(tmp_path):
    db, docs = make_db(tmp_path)
    rows = pending_rows(db)
    victim = rows[2]["id"]
    run_id = db.create_run("translate", len(rows))
    stats = run(make_engine(MockProvider(policy_ids={victim}), db), run_id, rows)
    assert stats["done"] == 4 and stats["failed"] == 1
    assert not stats["paused"]
    seg = db.get_segment(victim)
    assert seg["status"] == "failed"
    db.close()


def test_dropped_segment_recovered_by_single(tmp_path):
    db, docs = make_db(tmp_path)
    rows = pending_rows(db)
    victim = rows[1]["id"]
    run_id = db.create_run("translate", len(rows))
    stats = run(make_engine(MockProvider(drop_ids={victim}), db), run_id, rows)
    # 漏段经单段降级补齐（设计 v0.4 #26）
    assert stats["done"] == 5 and stats["failed"] == 0
    assert db.get_segment(victim)["status"] == "machine_translated"
    db.close()


def test_cancel_keeps_pending(tmp_path):
    import threading
    db, docs = make_db(tmp_path)
    rows = pending_rows(db)
    run_id = db.create_run("translate", len(rows))
    ev = threading.Event()
    ev.set()
    stats = run(make_engine(MockProvider(), db, cancel_event=ev), run_id, rows)
    assert stats["cancelled"]
    assert db.get_run(run_id)["status"] == "cancelled"
    assert len(pending_rows(db)) == 5  # 未开始批次保持 pending（设计 v0.6 #37）
    db.close()


def test_placeholder_mismatch_marks_review(tmp_path):
    import re
    db, docs = make_db(tmp_path)
    # 构造含占位符的段（模拟行内代码保护）
    doc = docs[0]
    blocks = [{"seq": 0, "text": "运行 {0} 命令即可。", "is_heading": False,
               "translatable": True, "src_hash": "ph-0"}]
    db.replace_segments(doc, blocks, "cfgA")
    rows = pending_rows(db)
    run_id = db.create_run("translate", len(rows))
    provider = MockProvider(translator=lambda t: "T:" + re.sub(r"\{\d+\}", "", t))
    stats = run(make_engine(provider, db), run_id, rows)
    assert stats["done"] == 1
    seg = db.get_segment(rows[0]["id"])
    assert seg["review_flag"] == 1  # 占位符异常 → 待复核（设计 v0.5 #33）
    db.close()
