"""反馈 2026-09-29：校对页的编辑落在下方编辑区（表格只选中、编辑区可拖拽、原文分情况可改）。

- #1 编辑落点：表格只负责选中，译文/原文都在下方编辑区改（自动保存）；
- #2 编辑区高度可用光标拖拽，比例记进配置；
- #3 去掉单元格悬停大框提示（看全文就看下方编辑区）；
- #4 拖动分隔条不再卡顿：行高只在「数据换了」或「宽度变了」时重算。

覆盖四层：

1. 数据层：`update_segment(src=...)` 同步改 src_hash；
2. 服务层：`ReviewService.edit_src`（原文修正 + 重算哈希）、`confirm` 清待复核；
3. 模型层：表格不再可内联编辑、`set_source` 置待复核、不提供悬停提示；
4. 页面层（offscreen）：编辑区可编辑并落库、切段先保存、Ctrl+Enter 确认用新译文、
   原文默认只读 / OCR 自动放开、分隔条可拖拽且记住比例、行高重算时机。
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                            # noqa: E402
from PySide6.QtCore import QEvent, QSize, Qt             # noqa: E402
from PySide6.QtGui import QResizeEvent                   # noqa: E402
from PySide6.QtWidgets import (                          # noqa: E402
    QApplication, QHeaderView, QMessageBox,
)

from app.context import Bridge                            # noqa: E402
from app.pages.review_page import ReviewPage, SegmentsModel    # noqa: E402
from core.pipeline import ReviewService                   # noqa: E402
from core.project import Project                          # noqa: E402
from core.term_impact import TermImpactService            # noqa: E402
from storage.db import Database                           # noqa: E402


# ---------- 夹具 ----------
@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """所有用例都不许弹模态框：无头环境里 exec() 会直接把测试挂死。

    需要断言弹窗内容的用例，在用例内部再 monkeypatch 一次覆盖即可。
    """
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class FakeCtx:
    """最小上下文：校对页只用到 project / review / term_impact / cfg / bridge / save_cfg。"""

    def __init__(self, project, cfg=None):
        self.project = project
        self.review = ReviewService(project)
        self.term_impact = TermImpactService(project)
        self.cfg = dict(cfg or {})
        self.bridge = Bridge()
        self.saved_cfg = 0

    def save_cfg(self):
        self.saved_cfg += 1


@pytest.fixture
def make_page(qapp, tmp_path):
    """建一个两段可译段的项目 + 校对页（已进页面、已选中第一段），用完收尾。"""
    made: list = []

    def make(doc_fmt: str = "md", texts=("第一段の原文。", "第二段の原文。")):
        root = tmp_path / f"p{len(made)}"
        proj = Project.create(root, name="rv", src_lang="ja-JP", tgt_lang="zh-CN",
                              provider_id="mock")
        doc = proj.db.upsert_document("source/a.md", doc_fmt, "h")
        blocks = [{"seq": i, "text": t, "is_heading": False, "translatable": True,
                   "src_hash": f"h{i}"} for i, t in enumerate(texts)]
        proj.db.replace_segments(doc, blocks, proj.cfg_hash())
        for r in proj.db.list_segments(translatable=True):
            proj.db.update_segment(r["id"], tgt="T:" + r["src_text"],
                                   status="machine_translated")
        ctx = FakeCtx(proj)
        page = ReviewPage(ctx, None)
        page.resize(1200, 800)
        page.show()
        page.on_enter()
        page.table.selectRow(0)
        made.append((page, proj))
        return page, ctx, proj

    yield make
    for page, proj in made:
        page.close()
        proj.close()


def segs(proj) -> list:
    return proj.db.list_segments(translatable=True)


def make_db(tmp_path, text="第一段の原文。", cfg="cfgA"):
    db = Database(tmp_path / "w.db")
    doc = db.upsert_document("source/a.md", "md", "h")
    db.replace_segments(doc, [{"seq": 0, "text": text, "is_heading": False,
                               "translatable": True, "src_hash": "h0"}], cfg)
    row = db.list_segments(translatable=True)[0]
    db.update_segment(row["id"], tgt="T:" + text, status="machine_translated")
    return db


# ---------- 1/2. 数据层 + 服务层 ----------
def test_update_segment_writes_source_and_hash(tmp_path):
    """原文改动要连 src_hash 一起改（哈希不变量，反馈 2026-09-29 #1）。"""
    db = make_db(tmp_path)
    row = db.list_segments(translatable=True)[0]
    db.update_segment(row["id"], src="改过的原文", src_hash="new-hash")
    got = db.get_segment(row["id"])
    assert got["src_text"] == "改过的原文" and got["src_hash"] == "new-hash"
    db.close()


def test_edit_src_rehashes_so_tm_no_longer_hits(tmp_path):
    """改原文后必须重算哈希：否则重译时按旧原文命中翻译记忆，改原文等于没改。"""
    import hashlib
    db = Database(tmp_path / "w.db")
    doc = db.upsert_document("source/a.md", "md", "h")
    db.replace_segments(doc, [
        {"seq": 0, "text": "同じ原文。", "is_heading": False, "translatable": True,
         "src_hash": "dup"},
        {"seq": 1, "text": "同じ原文。", "is_heading": False, "translatable": True,
         "src_hash": "dup"},
    ], "cfgA")
    rows = db.list_segments(translatable=True)
    db.update_segment(rows[0]["id"], tgt="旧译文", status="machine_translated")

    ReviewService(type("P", (), {"db": db})()).edit_src(rows[1]["id"], "修正后的原文。")
    got = db.get_segment(rows[1]["id"])
    assert got["src_text"] == "修正后的原文。"
    assert got["src_hash"] == hashlib.sha256("修正后的原文。".encode()).hexdigest()
    # 旧哈希的历史 TM 还在（别的同源段仍能用），但这一段的修正稿查不到它
    assert db.find_tm("dup", "cfgA") is not None
    assert db.find_tm(got["src_hash"], "cfgA") is None
    db.close()


def test_confirm_clears_review_flag(tmp_path):
    """确认即已复核：⚑ 待复核标记随确认消失（原文修正过的段靠它收尾）。"""
    db = make_db(tmp_path)
    row = db.list_segments(translatable=True)[0]
    db.update_segment(row["id"], tgt="译文", status="machine_translated", review_flag=True)
    ReviewService(type("P", (), {"db": db})()).confirm(row["id"])
    got = db.get_segment(row["id"])
    assert got["status"] == "confirmed" and not got["review_flag"]
    db.close()


# ---------- 3. 模型层 ----------
def _row(seg_id=1, src="旧", tgt="译"):
    return {"id": seg_id, "doc_id": 1, "seq": 0, "src": src, "tgt": tgt,
            "status": "machine_translated", "review_flag": False, "is_heading": False,
            "doc_path": "source/a.md", "ruby_map": {}, "ruby_src": []}


def test_table_model_is_not_editable():
    """表格只负责选中：单元格不再带可编辑标志（反馈 2026-09-29 #1）。"""
    model = SegmentsModel()
    model.set_rows([_row()])
    for col in range(model.columnCount()):
        assert not (model.flags(model.index(0, col)) & Qt.ItemFlag.ItemIsEditable)


def test_model_set_source_flags_review():
    """程序化写原文：落库 + 置 ⚑ 待复核（译文可能已不对应）。"""
    calls = []
    model = SegmentsModel(None, lambda sid, txt: calls.append((sid, txt)))
    model.set_rows([_row(seg_id=7)])
    assert model.set_source(model.index(0, SegmentsModel.SRC_COL), "新")
    assert calls == [(7, "新")]
    assert model.rows[0]["src"] == "新" and model.rows[0]["review_flag"] is True


def test_model_row_lookup_by_seg_id():
    """按段 id 找行号（刷新后恢复选中用），找不到返回 None。"""
    model = SegmentsModel()
    model.set_rows([_row(seg_id=5), _row(seg_id=9)])
    assert model.row_of(9) == 1 and model.row_of(404) is None


# ---------- 4. 页面层 ----------
def test_edit_pane_applies_and_persists(make_page):
    """编辑区（译文）可编辑，落库后状态转「已修改」。"""
    page, ctx, proj = make_page()
    assert page.table.editTriggers() == page.table.EditTrigger.NoEditTriggers
    assert not page.detail_tgt.isReadOnly()
    assert page.detail_tgt.toPlainText() == "T:第一段の原文。"
    page.detail_tgt.setPlainText("人工改好的译文")
    assert page.save_btn.isEnabled()          # 有未保存改动
    assert page._flush() is True
    row = segs(proj)[0]
    assert row["tgt_text"] == "人工改好的译文"
    assert row["status"] == "human_edited"
    assert page.edit_state.text().startswith("已保存")
    assert not page.save_btn.isEnabled()


def test_switching_segment_saves_pending_edit(make_page):
    """切段前先把上一段未落库的改动写下去（否则一切换就丢）。"""
    page, ctx, proj = make_page()
    page.detail_tgt.setPlainText("第一段改过")
    page.table.selectRow(1)                   # 直接切到第二段
    assert segs(proj)[0]["tgt_text"] == "第一段改过"
    assert page.detail_tgt.toPlainText() == "T:第二段の原文。"


def test_confirm_and_next_uses_edited_text(make_page):
    """Ctrl+Enter：先落库再确认，确认的是编辑区里的新译文。"""
    page, ctx, proj = make_page()
    page.detail_tgt.setPlainText("确认用的译文")
    page._confirm_and_next()
    row = segs(proj)[0]
    assert row["tgt_text"] == "确认用的译文" and row["status"] == "confirmed"


def test_refresh_keeps_current_segment(make_page):
    """刷新（改筛选/搜索）后仍停在原来那段，不再把用户顶回第一行。"""
    page, ctx, proj = make_page()
    page.table.selectRow(1)
    page.refresh()
    assert page._cur_id == segs(proj)[1]["id"]


def test_source_readonly_for_imported_doc(make_page):
    """普通导入文档：原文默认只读（导出会重新解析源文件，改原文不进导出结果）。"""
    page, ctx, proj = make_page()
    assert not page.src_edit_check.isChecked()
    assert page.detail_src.isReadOnly()
    assert page.src_state.text() == "只读"


def test_source_editable_on_demand_for_segmentation_errors(make_page):
    """分段错误：手动勾选后可改原文，改动落库、重算哈希、置 ⚑ 待复核。"""
    page, ctx, proj = make_page()
    page.src_edit_check.setChecked(True)
    assert not page.detail_src.isReadOnly()
    assert ctx.cfg["review_allow_src_edit"] is True and ctx.saved_cfg >= 1
    page.detail_src.setPlainText("人工修正的原文")
    page._flush()
    row = segs(proj)[0]
    assert row["src_text"] == "人工修正的原文"
    assert row["src_hash"] != "h0"
    assert row["review_flag"]            # ⚑ 待复核：译文可能已不对应


def test_source_auto_editable_for_ocr_doc(make_page):
    """OCR 文档：原文自动放开（识别错误得能改）。"""
    page, ctx, proj = make_page(doc_fmt="img")
    assert page.src_edit_check.isChecked()
    assert not page.detail_src.isReadOnly()


def test_splitter_resizable_and_remembers(make_page):
    """上下分隔条可拖拽（手柄够宽、不可折叠、实时跟随），比例写进配置并在下次恢复。"""
    page, ctx, proj = make_page()
    assert page.vsplit.count() == 2
    assert page.vsplit.handleWidth() >= 8
    assert not page.vsplit.childrenCollapsible()
    assert page.vsplit.opaqueResize()          # 拖动时实时跟随，不是松手才跳
    assert page.vsplit.widget(0) is page.table
    page.vsplit.setSizes([240, 460])
    page._save_layout()
    saved = ctx.cfg["review_layout"]["vsplit"]
    assert ctx.saved_cfg == 1 and len(saved) == 2 and saved[0] < saved[1]
    ratio = saved[0] / sum(saved)

    # 新开一个页面：比例应恢复到刚才拖出来的样子
    ctx2 = FakeCtx(proj, cfg=ctx.cfg)
    page2 = ReviewPage(ctx2, None)
    page2.resize(1200, 800)
    page2.show()
    got = page2.vsplit.sizes()
    assert abs(got[0] / sum(got) - ratio) < 0.05
    page2.close()


def test_splitter_falls_back_to_default(make_page):
    """配置里没有有效比例时用默认值，不能崩（0/None/缺字段都算无效）。"""
    page, ctx, proj = make_page()
    for bad in (None, [0, 0], "x", [100]):
        ctx.cfg["review_layout"] = {"vsplit": bad}
        page._restore_layout()
        assert page.vsplit.sizes() != [0, 0]


# ---------- 5. 悬停提示（反馈 2026-09-29 #3） ----------
def test_no_hover_tooltip_on_rows(make_page):
    """表格不再给单元格悬停提示：光标旁不再弹「源文+译文」大框，看全文看下方编辑区。"""
    page, ctx, proj = make_page()
    model = page.model
    for r in range(model.rowCount()):
        for c in range(model.columnCount()):
            assert not model.data(model.index(r, c), Qt.ItemDataRole.ToolTipRole)


# ---------- 6. 行高测量时机与规模（反馈 2026-09-29 #4） ----------
# 拖动卡顿与超大项目卡死的共同根因：量一行行高要做一次带自动换行的文本排版
# （实测约 120µs/行），所以「量哪些行、什么时候量」必须受控。
def _count_row_measures(monkeypatch, page):
    """记录每一行被测量（`resizeRowToContents`）的次数。"""
    calls = []
    orig = page.table.resizeRowToContents
    monkeypatch.setattr(page.table, "resizeRowToContents",
                        lambda r: (calls.append(r), orig(r))[1])
    return calls


def _settle(qapp, ms: int = 150):
    """等防抖计时器落地（宽度变化后的重量是防抖的）。"""
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        qapp.processEvents()
        time.sleep(0.005)


LONG_TEXT = ("クラスで一番の大食いだったり、算数は満点でも国語が駄目だったり、"
             "またた不細工なのに異性からモテたり……などなど。"
             "そのような不思議なことが僕にもあった一つだけある。")


def test_no_full_table_measurement_ever(make_page, qapp, monkeypatch):
    """任何用户路径都不再整表测量（`resizeRowsToContents`）—— 超大项目卡死的根因。"""
    page, ctx, proj = make_page(texts=tuple(LONG_TEXT for _ in range(400)))
    full = []
    monkeypatch.setattr(page.table, "resizeRowsToContents", lambda: full.append(1))
    page.refresh()
    page.search.setText("クラス")
    page.vsplit.setSizes([200, 500])
    page.table.verticalScrollBar().setValue(
        page.table.verticalScrollBar().maximum())
    qapp.processEvents()
    assert full == []


def test_refresh_measures_only_visible_rows(make_page, qapp):
    """刷新只量可见区间（±margin），其余行用默认单行高兜底 —— 代价与总段数无关。"""
    page, ctx, proj = make_page(texts=tuple(LONG_TEXT for _ in range(400)))
    page.refresh()
    _settle(qapp)                                      # 布局稳定 + 防抖落地
    default_h = page._default_row_height()
    measured = page._measured_rows
    assert 0 < len(measured) < 400                     # 量了一部分，不是全表
    assert page.table.rowHeight(0) > default_h         # 可见行是真实高度
    assert page.table.rowHeight(399) == default_h      # 远端的行还没量（兜底高度）


def test_scrolling_measures_newly_revealed_rows(make_page, qapp):
    """滚到底部：那一段的行被补量成真实高度（增量，不重来全表）。"""
    page, ctx, proj = make_page(texts=tuple(LONG_TEXT for _ in range(400)))
    page.refresh()
    _settle(qapp)
    before = set(page._measured_rows)
    sb = page.table.verticalScrollBar()
    sb.setValue(sb.maximum())
    qapp.processEvents()
    assert 399 in page._measured_rows
    assert page.table.rowHeight(399) > page._default_row_height()
    assert len(page._measured_rows - before) < 400     # 只补量了新露出的部分


def test_vertical_drag_measures_at_most_the_new_rows(make_page, qapp, monkeypatch):
    """拖上下分隔条：只补量新露出来的少数行，绝不逐帧重算全表。

    原来是 ResizeToContents：视口一动就把每行 sizeHint 全算一遍
    （88 段实测每次鼠标移动 24ms、2000 段 570ms，一帧只有 16.7ms → 必掉帧）。
    """
    page, ctx, proj = make_page(texts=tuple(LONG_TEXT for _ in range(200)))
    page.refresh()
    _settle(qapp)
    before = set(page._measured_rows)
    measures = _count_row_measures(monkeypatch, page)
    h = page.vsplit.height()
    for i in range(20):
        page.vsplit.setSizes([150 + i * 8, max(200, h - 150 - i * 8)])
        qapp.processEvents()
    new_rows = set(measures) - before
    assert len(new_rows) <= page.VISIBLE_MARGIN * 2    # 只补量新露出的那几行
    assert len(measures) < 200                         # 不是全表重算


def test_width_change_invalidates_and_remeasures(make_page, qapp):
    """宽度变了（换行宽度变了）才作废已量行高，且仍然只量可见区间。"""
    page, ctx, proj = make_page(texts=tuple(LONG_TEXT for _ in range(400)))
    page.refresh()
    _settle(qapp)
    page.resize(page.width() - 300, page.height())
    qapp.processEvents()
    _settle(qapp)                                      # 防抖落地后自动重量
    assert page.table.rowHeight(0) > page._default_row_height()
    assert len(page._measured_rows) < 400              # 仍然只量可见区间


def test_height_only_resize_does_not_invalidate(make_page, qapp):
    """只变高度（拖分隔条/上下缩放窗口）不作废已量行高、也不排程整轮重来。"""
    page, ctx, proj = make_page(texts=tuple(LONG_TEXT for _ in range(100)))
    page.refresh()
    _settle(qapp)
    measured = set(page._measured_rows)
    assert measured                                    # 前提：已经量过
    vp = page.table.viewport()
    old = QSize(vp.width(), vp.height())
    QApplication.sendEvent(vp, QResizeEvent(QSize(old.width(), old.height() - 90), old))
    assert not page._height_timer.isActive()           # 没有整轮重量的排程
    assert measured <= page._measured_rows             # 已量结果保留


def test_row_heights_follow_content_change(make_page, qapp):
    """换了数据（筛选后第 0 行变成短文本）可见区行高要跟着变，不能停在旧高度把字切掉。"""
    page, ctx, proj = make_page(texts=(LONG_TEXT, "短。"))
    long_h, short_h = page.table.rowHeight(0), page.table.rowHeight(1)
    assert long_h > short_h                    # 长段落确实更高（自动换行生效）
    page.search.setText("短。")                # 只剩第二段 → 现在的第 0 行是短文本
    qapp.processEvents()
    assert page.table.rowHeight(0) == short_h  # 高度跟着内容变矮


def test_header_uses_fixed_mode(make_page):
    """行高改成由页面按需测量（Fixed），不再交给 ResizeToContents 自动管。"""
    page, ctx, proj = make_page()
    assert (page.table.verticalHeader().sectionResizeMode(0)
            == QHeaderView.ResizeMode.Fixed)
    assert page._row_width > 0


# ---------- 7. 确认流转（反馈 2026-09-29 #5：按确认/ Ctrl+Enter 没反应） ----------
@pytest.mark.parametrize("status", ["machine_translated", "human_edited", "pending",
                                    "failed", "confirmed"])
def test_confirm_works_for_any_status_with_translation(make_page, qapp, status):
    """只要有译文，任何状态都能被人工确认。

    原来只认 (机翻, 已修改)，于是 `未译 / 失败` 的段点确认时**静默什么都不做**，
    用户只知道"按了没反应"。
    """
    page, ctx, proj = make_page()
    row = segs(proj)[0]
    proj.db.update_segment(row["id"], tgt="已有译文", status=status)
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    page._confirm_selected()
    qapp.processEvents()
    assert proj.db.get_segment(row["id"])["status"] == "confirmed"
    assert page.model.data(page.model.index(0, 1),
                           Qt.ItemDataRole.DisplayRole) == "已确认"


def test_confirm_refuses_empty_translation_with_reason(make_page, qapp, monkeypatch):
    """没有译文的段不能确认（否则导出会产出空段），但必须把原因说出来。"""
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")))
    page, ctx, proj = make_page()
    row = segs(proj)[0]
    proj.db.update_segment(row["id"], tgt="", status="pending")
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    page._confirm_selected()
    qapp.processEvents()
    assert proj.db.get_segment(row["id"])["status"] == "pending"    # 确实没确认
    assert shown and "还没有译文" in shown[-1]                       # 但不再是静默


def test_confirm_and_next_stays_on_empty_and_reports(make_page, qapp):
    """Ctrl+Enter 遇到没译文的段：留在原地 + 提示（原来静默跳过，像按键坏了）。"""
    page, ctx, proj = make_page()
    rows = segs(proj)
    proj.db.update_segment(rows[0]["id"], tgt="", status="pending")
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    toasts = []
    ctx.bridge.toast.connect(toasts.append)
    page._confirm_and_next()
    qapp.processEvents()
    assert proj.db.get_segment(rows[0]["id"])["status"] == "pending"
    assert page.table.currentIndex().row() == 0        # 不跳走，方便就地补译文
    assert any("还没有译文" in t for t in toasts)


def test_confirm_and_next_advances_after_confirm(make_page, qapp):
    """正常路径：确认后跳到下一段（键盘流不被破坏）。"""
    page, ctx, proj = make_page()
    rows = segs(proj)
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    page._confirm_and_next()
    qapp.processEvents()
    assert proj.db.get_segment(rows[0]["id"])["status"] == "confirmed"
    assert page.table.currentIndex().row() == 1


def test_confirmable_count_matches_confirm(make_page, qapp):
    """「待确认 N 段」的口径必须和确认动作一致（别再说有 N 段却一段都确认不了）。"""
    page, ctx, proj = make_page()
    rows = segs(proj)
    proj.db.update_segment(rows[0]["id"], tgt="", status="pending")   # 空译文 → 不可确认
    proj.db.update_segment(rows[1]["id"], status="confirmed")         # 已确认 → 不可确认
    page.refresh()
    assert page._confirmable_count(None) == 0
    assert ctx.review.confirm_all() == 0

    proj.db.update_segment(rows[0]["id"], tgt="补上的译文", status="failed")
    assert ctx.review.confirmable_count(None) == 1
    assert ctx.review.confirm_all() == 1                              # 口径一致
    assert proj.db.get_segment(rows[0]["id"])["status"] == "confirmed"


def test_confirm_document_covers_pending_and_failed_with_text(make_page, qapp):
    """「确认当前文档全部」同样覆盖 pending/failed 但有译文的段。"""
    page, ctx, proj = make_page()
    rows = segs(proj)
    proj.db.update_segment(rows[0]["id"], status="failed")
    proj.db.update_segment(rows[1]["id"], status="pending")
    assert ctx.review.confirmable_count(rows[0]["doc_id"]) == 2
    assert ctx.review.confirm_document(rows[0]["doc_id"]) == 2
    assert all(s["status"] == "confirmed" for s in segs(proj))


# ---------- 8. 段落内术语提示（反馈 2026-09-29 #6） ----------
def _add_term(proj, src, cands):
    proj.glossary.add(src, cands)
    proj.glossary.save()
    proj.snapshot_terms()


def test_term_hint_warns_when_new_term_missing(make_page, qapp):
    """新增术语后，选中源文含它、译文里却没有它的段落 → 编辑区下方直接提示。"""
    page, ctx, proj = make_page()
    _add_term(proj, "第一段", ["新词"])
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    assert page.term_bar.isVisible()
    assert "第一段" in page.term_hint.text() and "新词" in page.term_hint.text()
    assert not page.term_replace_btn.isVisible()   # 没有旧串可替换，只能重译/人工改
    assert page.term_retrans_btn.isVisible()


def test_term_hint_silent_when_translation_is_consistent(make_page, qapp):
    """译文里已经用了约定译名 → 不提示（避免天天误报）。"""
    page, ctx, proj = make_page()
    row = segs(proj)[0]
    proj.db.update_segment(row["id"], tgt="新词の訳")
    _add_term(proj, "第一段", ["新词"])
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    assert not page.term_bar.isVisible()


def test_term_hint_offers_replace_for_stale_candidate(make_page, qapp):
    """术语改过译名、译文里还留着旧译名 → 提示 + 一键替换（替换后落库）。"""
    page, ctx, proj = make_page()
    row = segs(proj)[0]
    proj.db.update_segment(row["id"], tgt="旧词出现了")
    _add_term(proj, "第一段", ["旧词"])          # 第一版快照（旧候选）
    _add_term(proj, "第一段", ["新词"])          # 改过之后（新候选）
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    assert page.term_bar.isVisible()
    assert page.term_replace_btn.isVisible()
    assert "旧词" in page.term_hint.text() and "新词" in page.term_hint.text()

    page.term_replace_btn.click()
    qapp.processEvents()
    assert page.detail_tgt.toPlainText() == "新词出现了"
    assert proj.db.get_segment(row["id"])["tgt_text"] == "新词出现了"
    assert not page.term_bar.isVisible()          # 处理完提示自动消失


def test_term_hint_retranslate_button_marks_pending(make_page, qapp):
    """「标记重译本段」把该段置回待译（下次翻译会用新术语重新生成）。"""
    page, ctx, proj = make_page()
    _add_term(proj, "第一段", ["新词"])
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    page.term_retrans_btn.click()
    qapp.processEvents()
    assert proj.db.get_segment(segs(proj)[0]["id"])["status"] == "pending"


def test_term_hint_can_be_ignored_for_this_session(make_page, qapp):
    """「本次忽略」只让提示不再烦人，不动数据。"""
    page, ctx, proj = make_page()
    _add_term(proj, "第一段", ["新词"])
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    page.term_ignore_btn.click()
    qapp.processEvents()
    assert not page.term_bar.isVisible()
    assert proj.db.get_segment(segs(proj)[0]["id"])["tgt_text"] == "T:第一段の原文。"


def test_term_hint_follows_selected_segment(make_page, qapp):
    """提示跟着选中段走：不含术语的那段不提示。"""
    page, ctx, proj = make_page()
    _add_term(proj, "第一段", ["新词"])
    page.refresh()
    page.table.selectRow(0)
    qapp.processEvents()
    assert page.term_bar.isVisible()
    page.table.selectRow(1)
    qapp.processEvents()
    assert not page.term_bar.isVisible()          # 第二段源文里没有「第一段」


def test_editing_target_resizes_only_that_row(make_page, monkeypatch):
    """改完译文只重算这一行的高度，不惊动整张表。"""
    page, ctx, proj = make_page()
    resized = []
    monkeypatch.setattr(page.table, "resizeRowToContents",
                        lambda r: resized.append(r))
    page.detail_tgt.setPlainText("改短一点")
    page._flush()
    assert resized == [0]
