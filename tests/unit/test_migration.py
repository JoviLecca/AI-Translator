"""DB v1→v2 迁移测试（增补设计 §1.5）：旧库升级保留数据、自动加列。"""
import sqlite3

from storage.db import Database, SCHEMA_VERSION

_V1_SEGMENTS = """
CREATE TABLE segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  src_text TEXT NOT NULL,
  src_hash TEXT NOT NULL,
  tgt_text TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  is_heading INTEGER NOT NULL DEFAULT 0,
  translatable INTEGER NOT NULL DEFAULT 1,
  cfg_hash TEXT,
  review_flag INTEGER NOT NULL DEFAULT 0
);
"""


def test_v1_migration(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    INSERT INTO meta VALUES('schema_version','1');
    CREATE TABLE documents(
      id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE, format TEXT,
      content_hash TEXT, tags TEXT DEFAULT '{}', terms_extracted INTEGER DEFAULT 0,
      status TEXT DEFAULT 'imported');
    """ + _V1_SEGMENTS + """
    INSERT INTO documents(path,format,content_hash) VALUES('source/a.md','md','h');
    INSERT INTO segments(doc_id,seq,src_text,src_hash,tgt_text,status)
      VALUES(1,0,'原文','hash-0','译文','confirmed');
    """)
    conn.commit()
    conn.close()

    db = Database(path)  # 打开即自动迁移
    assert SCHEMA_VERSION == 2
    seg = db.list_segments()[0]
    assert seg["tgt_text"] == "译文"          # 数据完整
    assert seg["ruby_map"] is None and seg["ruby_src"] is None
    db.update_segment(seg["id"], ruby_map='{"{r0}": "注音"}')
    assert db.get_segment(seg["id"])["ruby_map"] == '{"{r0}": "注音"}'
    db.close()


def test_fresh_db_is_v2(tmp_path):
    db = Database(tmp_path / "new.db")
    row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert row[0] == "2"
    db.close()
