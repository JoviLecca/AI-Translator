"""SQLite 工作库（设计 §6 / §4 线程与数据安全）。

- WAL 模式；所有写操作经 threading.RLock 串行化；
- meta.schema_version 支持后续自动迁移（NFR-08）；
- 查询索引：(doc_id, seq) 校对列表、(src_hash, cfg_hash) 翻译记忆、(status) 过滤。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 2

_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  format TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '{}',
  terms_extracted INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'imported'
);
CREATE TABLE IF NOT EXISTS segments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  src_text TEXT NOT NULL,
  src_hash TEXT NOT NULL,
  tgt_text TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  is_heading INTEGER NOT NULL DEFAULT 0,
  translatable INTEGER NOT NULL DEFAULT 1,
  cfg_hash TEXT,
  review_flag INTEGER NOT NULL DEFAULT 0,
  ruby_map TEXT,
  ruby_src TEXT
);
CREATE INDEX IF NOT EXISTS idx_seg_doc ON segments(doc_id, seq);
CREATE INDEX IF NOT EXISTS idx_seg_tm ON segments(src_hash, cfg_hash);
CREATE INDEX IF NOT EXISTS idx_seg_status ON segments(status);
CREATE TABLE IF NOT EXISTS terms(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  src_term TEXT UNIQUE NOT NULL,
  tgt_candidates TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'approved',
  occurrences INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS terms_revision(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  saved_at REAL NOT NULL,
  content_hash TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  total INTEGER NOT NULL DEFAULT 0,
  done INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0,
  tokens_out INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  finished_at REAL
);
CREATE TABLE IF NOT EXISTS run_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  segment_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  retries INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT ''
);
"""

# 状态机（设计 §6.2）：pending → machine_translated → human_edited → confirmed；failed 可重试回 pending
SEG_STATUSES = ("pending", "machine_translated", "human_edited", "confirmed", "failed")
_TM_STATUSES = ("confirmed", "human_edited", "machine_translated")


def _row(cur: sqlite3.Cursor) -> sqlite3.Row | None:
    return cur.fetchone()


class Database:
    """单实例线程安全封装。UI 线程与引擎线程共用。"""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ---------- 基础 ----------
    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_DDL)
            cur = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'")
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?)",
                    (str(SCHEMA_VERSION),),
                )
                version = 1
            else:
                version = int(row[0])
            # 增量迁移（NFR-08）：v1 → v2 增加振假名列（增补设计 §1.5）
            #   ruby_map：translate 策略的译注音（引擎写入）
            #   ruby_src：源文振假名构造 entries（导入时写入，兜底还原用）
            if version == 1:
                cols = [r[1] for r in self._conn.execute("PRAGMA table_info(segments)")]
                for col in ("ruby_map", "ruby_src"):
                    if col not in cols:
                        self._conn.execute(f"ALTER TABLE segments ADD COLUMN {col} TEXT")
                version = 2
            self._conn.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                               (str(version),))
            if version != SCHEMA_VERSION:
                raise RuntimeError(f"不支持的数据库版本：{version}")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- documents ----------
    def upsert_document(self, path: str, fmt: str, content_hash: str, tags: dict | None = None) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO documents(path,format,content_hash,tags,status) VALUES(?,?,?,?, 'imported') "
                "ON CONFLICT(path) DO UPDATE SET format=excluded.format, content_hash=excluded.content_hash "
                "RETURNING id",
                (path, fmt, content_hash, json.dumps(tags or {}, ensure_ascii=False)),
            )
            return int(cur.fetchone()[0])

    def get_document(self, doc_id: int) -> sqlite3.Row | None:
        return _row(self.execute("SELECT * FROM documents WHERE id=?", (doc_id,)))

    def get_document_by_path(self, path: str) -> sqlite3.Row | None:
        return _row(self.execute("SELECT * FROM documents WHERE path=?", (path,)))

    def list_documents(self) -> list[sqlite3.Row]:
        return self.execute("SELECT * FROM documents ORDER BY path").fetchall()

    def set_doc_fields(self, doc_id: int, **fields) -> None:
        allowed = {"tags", "terms_extracted", "status"}
        assert fields and set(fields) <= allowed
        if "tags" in fields and isinstance(fields["tags"], dict):
            fields["tags"] = json.dumps(fields["tags"], ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE documents SET {sets} WHERE id=?", (*fields.values(), doc_id))

    def delete_document(self, doc_id: int) -> None:
        self.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    # ---------- segments ----------
    def replace_segments(self, doc_id: int, blocks: list[dict], cfg_hash: str) -> dict:
        """文件（重新）导入时整体重建段记录；按 src_hash+cfg_hash 回填旧译文（设计 §6.2）。

        blocks: [{seq, text, is_heading, translatable, src_hash, ruby(可选 entries)}]
        返回统计 {total, translatable, reused}。
        """
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM segments WHERE doc_id=?", (doc_id,))
            stats = {"total": len(blocks), "translatable": 0, "reused": 0}
            for b in blocks:
                status = "pending"
                tgt = None
                ruby_src = json.dumps(b["ruby"], ensure_ascii=False) if b.get("ruby") else None
                if not b["translatable"]:
                    cfg = cfg_hash
                else:
                    stats["translatable"] += 1
                    tm = self._conn.execute(
                        "SELECT tgt_text,status FROM segments WHERE src_hash=? AND cfg_hash=? "
                        "AND status IN ('confirmed','human_edited','machine_translated') "
                        "AND tgt_text IS NOT NULL AND doc_id!=? "
                        f"ORDER BY CASE status WHEN 'confirmed' THEN 0 WHEN 'human_edited' THEN 1 ELSE 2 END LIMIT 1",
                        (b["src_hash"], cfg_hash, doc_id),
                    ).fetchone()
                    if tm:
                        tgt, status = tm["tgt_text"], tm["status"]
                        stats["reused"] += 1
                self._conn.execute(
                    "INSERT INTO segments(doc_id,seq,src_text,src_hash,tgt_text,status,is_heading,"
                    "translatable,cfg_hash,ruby_src) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, b["seq"], b["text"], b["src_hash"], tgt, status,
                     int(b["is_heading"]), int(b["translatable"]),
                     cfg_hash if b["translatable"] else None, ruby_src),
                )
            return stats

    def list_segments(self, doc_id: int | None = None, translatable: bool = True,
                      status_in: tuple | None = None, search: str | None = None,
                      review_flag: bool | None = None) -> list[sqlite3.Row]:
        # translatable 严格过滤：True→仅可译段，False→仅透传段（审查第1轮修复：
        # 旧实现 False 时返回全部段）
        sql = "SELECT * FROM segments WHERE translatable=?"
        params: list = [int(translatable)]
        if doc_id is not None:
            sql += " AND doc_id=?"
            params.append(doc_id)
        if status_in:
            sql += f" AND status IN ({','.join('?' * len(status_in))})"
            params.extend(status_in)
        if review_flag is not None:
            sql += " AND review_flag=?"
            params.append(int(review_flag))
        if search:
            sql += " AND (src_text LIKE ? OR tgt_text LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])
        sql += " ORDER BY doc_id, seq"
        return self.execute(sql, tuple(params)).fetchall()

    def get_segment(self, seg_id: int) -> sqlite3.Row | None:
        return _row(self.execute("SELECT * FROM segments WHERE id=?", (seg_id,)))

    def update_segment(self, seg_id: int, tgt: str | None = None, status: str | None = None,
                       review_flag: bool | None = None, cfg_hash: str | None = None,
                       ruby_map: str | None = None) -> None:
        fields, params = [], []
        if tgt is not None:
            fields.append("tgt_text=?"); params.append(tgt)
        if status is not None:
            assert status in SEG_STATUSES
            fields.append("status=?"); params.append(status)
        if review_flag is not None:
            fields.append("review_flag=?"); params.append(int(review_flag))
        if cfg_hash is not None:
            fields.append("cfg_hash=?"); params.append(cfg_hash)
        if ruby_map is not None:
            fields.append("ruby_map=?"); params.append(ruby_map)
        if not fields:
            return
        params.append(seg_id)
        self.execute(f"UPDATE segments SET {', '.join(fields)} WHERE id=?", tuple(params))

    def find_tm(self, src_hash: str, cfg_hash: str, exclude_doc: int | None = None) -> sqlite3.Row | None:
        sql = ("SELECT tgt_text,status FROM segments WHERE src_hash=? AND cfg_hash=? "
               "AND status IN ('confirmed','human_edited','machine_translated') AND tgt_text IS NOT NULL")
        params: list = [src_hash, cfg_hash]
        if exclude_doc is not None:
            sql += " AND doc_id!=?"
            params.append(exclude_doc)
        sql += " LIMIT 1"
        return _row(self.execute(sql, tuple(params)))

    def segment_stats(self) -> dict:
        rows = self.execute(
            "SELECT status, COUNT(*) c FROM segments WHERE translatable=1 GROUP BY status"
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}

    # ---------- terms（缓存/中间态；权威数据在 glossary 文件，设计 §4） ----------
    def upsert_term(self, src: str, candidates: list[str], note: str = "",
                    origin: str = "manual", status: str = "approved", occurrences: int = 1) -> None:
        self.execute(
            "INSERT INTO terms(src_term,tgt_candidates,note,origin,status,occurrences) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(src_term) DO UPDATE SET tgt_candidates=excluded.tgt_candidates, note=excluded.note,"
            "origin=excluded.origin, status=excluded.status, occurrences=excluded.occurrences",
            (src, "|".join(candidates), note, origin, status, occurrences),
        )

    def list_terms(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.execute("SELECT * FROM terms WHERE status=? ORDER BY occurrences DESC", (status,)).fetchall()
        return self.execute("SELECT * FROM terms ORDER BY occurrences DESC").fetchall()

    def delete_term(self, src: str) -> None:
        self.execute("DELETE FROM terms WHERE src_term=?", (src,))

    def save_terms_revision(self, content_hash: str, payload: list[dict]) -> None:
        self.execute(
            "INSERT INTO terms_revision(saved_at,content_hash,payload) VALUES(?,?,?)",
            (time.time(), content_hash, json.dumps(payload, ensure_ascii=False)),
        )

    def last_terms_revision(self) -> sqlite3.Row | None:
        return _row(self.execute("SELECT * FROM terms_revision ORDER BY id DESC LIMIT 1"))

    def list_terms_revisions(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT * FROM terms_revision ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    # ---------- runs ----------
    def create_run(self, run_type: str, total: int) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO runs(type,total,status,created_at) VALUES(?,?,'running',?)",
                (run_type, total, time.time()),
            )
            return int(cur.lastrowid)

    def update_run(self, run_id: int, **fields) -> None:
        allowed = {"status", "done", "failed", "tokens_in", "tokens_out", "cost", "error", "finished_at"}
        assert fields and set(fields) <= allowed
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), run_id))

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return _row(self.execute("SELECT * FROM runs WHERE id=?", (run_id,)))

    def last_run(self, run_type: str | None = None) -> sqlite3.Row | None:
        if run_type:
            return _row(self.execute("SELECT * FROM runs WHERE type=? ORDER BY id DESC LIMIT 1", (run_type,)))
        return _row(self.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1"))

    def create_run_items(self, run_id: int, segment_ids: list[int]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO run_items(run_id,segment_id,status) VALUES(?,?,'pending')",
                [(run_id, sid) for sid in segment_ids],
            )

    def update_run_item(self, run_id: int, segment_id: int, status: str, error: str = "", retries: int | None = None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE run_items SET status=?, error=?, retries=COALESCE(?,retries) "
                "WHERE run_id=? AND segment_id=?",
                (status, error, retries, run_id, segment_id),
            )
