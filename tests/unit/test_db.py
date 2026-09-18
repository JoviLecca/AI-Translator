from storage.db import Database


def _mk_blocks(n, prefix="段"):
    return [{"seq": i, "text": f"{prefix}{i}号内容", "is_heading": False,
             "translatable": True, "src_hash": f"hash-{prefix}-{i}"} for i in range(n)]


def test_replace_segments_tm_backfill(tmp_path):
    """重新分段按 src_hash+cfg_hash 回填旧译文（设计 §6.2）。"""
    db = Database(tmp_path / "work.db")
    doc1 = db.upsert_document("source/a.md", "md", "h1")
    db.replace_segments(doc1, _mk_blocks(3), "cfgA")
    # 模拟第一篇已翻译
    for seg in db.list_segments(doc_id=doc1):
        db.update_segment(seg["id"], tgt="T:" + seg["src_text"], status="machine_translated")
    # 第二篇导入，含相同段落（hash-段-1）→ TM 复用
    doc2 = db.upsert_document("source/b.md", "md", "h2")
    stats = db.replace_segments(doc2, _mk_blocks(3), "cfgA")
    assert stats["reused"] == 3
    segs = db.list_segments(doc_id=doc2)
    assert all(s["tgt_text"].startswith("T:") for s in segs)
    assert all(s["status"] == "machine_translated" for s in segs)
    # cfg 变化 → 不复用（缓存失效）
    doc3 = db.upsert_document("source/c.md", "md", "h3")
    stats3 = db.replace_segments(doc3, _mk_blocks(3), "cfgB")
    assert stats3["reused"] == 0
    db.close()


def test_tm_excludes_same_doc(tmp_path):
    db = Database(tmp_path / "work.db")
    doc = db.upsert_document("source/a.md", "md", "h")
    db.replace_segments(doc, _mk_blocks(2), "cfgA")
    for seg in db.list_segments(doc_id=doc):
        db.update_segment(seg["id"], tgt="T", status="confirmed")
    assert db.find_tm("hash-段-0", "cfgA", exclude_doc=doc) is None
    db.close()


def test_run_lifecycle(tmp_path):
    db = Database(tmp_path / "work.db")
    run = db.create_run("translate", 5)
    db.create_run_items(run, [1, 2])
    db.update_run_item(run, 1, "done")
    db.update_run(run, status="completed", done=5, tokens_in=100, tokens_out=50, cost=0.01,
                  finished_at=1.0)
    row = db.get_run(run)
    assert row["status"] == "completed"
    assert row["cost"] == 0.01
    db.close()
