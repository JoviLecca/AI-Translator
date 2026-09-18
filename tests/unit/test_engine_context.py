"""上下文滑窗测试（增补设计 §2）：批级前 N + 后 N、N=0 零上下文、字符上限。"""
import asyncio

from core.engine import RunEngine
from llm.mock import MockProvider
from storage.db import Database


class RecordingMock(MockProvider):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.all_messages = []

    async def chat(self, messages, **kwargs):
        res = await super().chat(messages, **kwargs)
        self.all_messages.append(list(messages))
        return res


def make_db(tmp_path, n=30):
    db = Database(tmp_path / "work.db")
    doc = db.upsert_document("source/s.md", "md", "h")
    blocks = [{"seq": i, "text": f"第{i}段原文内容。", "is_heading": False,
               "translatable": True, "src_hash": f"ctx-{i}"} for i in range(n)]
    db.replace_segments(doc, blocks, "cfgA")
    return db


def run(provider, db, slide):
    rows = db.list_segments(translatable=True, status_in=("pending",))
    engine = RunEngine(provider, db, cfg_hash="cfgA", src_lang="zh-CN", tgt_lang="en-US",
                       style_desc="t", terms=[], doc_tags={}, backoff=0.0,
                       concurrency=1, context_n=slide)
    return asyncio.run(engine.run_translation(1, rows))


def user_texts(provider):
    return [msgs[-1].content for msgs in provider.all_messages]


def test_slide_window_contains_before_and_after(tmp_path):
    db = make_db(tmp_path)
    provider = RecordingMock()
    stats = run(provider, db, slide=2)
    assert stats["done"] == 30 and stats["failed"] == 0
    users = user_texts(provider)
    # 第一个批次（段0-9）：无前文，后文 = 第10、11 段源文
    assert "【后文（源文，仅供衔接参考，不得翻译或输出）】" in users[0]
    assert "· 第10段原文内容。" in users[0] and "· 第11段原文内容。" in users[0]
    assert "第12段原文内容" not in users[0]
    # 第二个批次（段10-19）：前文 = 已定稿译文（T:第8、9段），后文 = 第20、21 段
    assert "【前文（已定稿，仅供衔接参考，不得翻译或输出）】" in users[1]
    assert "· T:第8段原文内容。" in users[1] and "· T:第9段原文内容。" in users[1]
    assert "· 第20段原文内容。" in users[1]
    # 末批（段20-29）：无后文块
    assert "【后文" not in users[-1] and "【前文" in users[-1]
    db.close()


def test_slide_zero_no_context_blocks(tmp_path):
    db = make_db(tmp_path, n=15)
    provider = RecordingMock()
    run(provider, db, slide=0)
    for u in user_texts(provider):
        assert "【前文" not in u and "【后文" not in u
        assert u.startswith("待译段落：")  # 与旧版 prompt 结构一致
    db.close()


def test_slide_char_cap(tmp_path):
    db = Database(tmp_path / "work.db")
    doc = db.upsert_document("source/s.md", "md", "h")
    long_text = "很长的段落" + "内" * 1500 + "。"
    blocks = [{"seq": i, "text": long_text, "is_heading": False,
               "translatable": True, "src_hash": f"cap-{i}"} for i in range(6)]
    db.replace_segments(doc, blocks, "cfgA")
    provider = RecordingMock()
    run(provider, db, slide=3)  # 3+3 段 × 1500+ 字 → 必须触发截断
    for u in user_texts(provider):
        ctx_lines = [ln for ln in u.splitlines() if ln.startswith("· ")]
        assert sum(len(ln) - 2 for ln in ctx_lines) <= 3000 + 10
    db.close()
