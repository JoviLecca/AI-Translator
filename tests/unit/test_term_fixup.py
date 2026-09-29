"""反馈 2026-09-29 #6：新增/修改术语后，软件要指出哪些段落需要订正。

三个层面：

1. 纯函数 `segment_term_issues`（校对页段落内提示的判断依据）；
2. `TermImpactService.analyze` —— 除"旧译名仍在用"外，还要覆盖**新增术语**
   （源文含术语、译文里没有任何当前候选）；
3. 工作台保存术语后的影响提示（`WorkbenchPage._offer_term_fixup`）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest                                              # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox     # noqa: E402

from core.project import Project                            # noqa: E402
from core.term_impact import (                              # noqa: E402
    TermImpactService, segment_term_issues,
)
from app.context import Bridge                              # noqa: E402


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch):
    """无头环境不许弹模态框（exec() 会挂死测试）；需要断言的用例自行覆盖。"""
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


class Term:
    """最小术语对象（与 core.glossary.Term 同形）。"""

    def __init__(self, src, candidates):
        self.src, self.candidates = src, candidates


# ---------- 1. 纯函数 ----------
def test_segment_issues_detects_new_term_missing():
    """源文有术语、译文里没有约定译名 → kind=missing（新增术语的典型情形）。"""
    issues = segment_term_issues("魔王が現れた。", "敌人出现了。",
                                 [Term("魔王", ["魔王", "魔皇"])])
    assert len(issues) == 1
    it = issues[0]
    assert it["kind"] == "missing" and it["term"] == "魔王"
    assert it["new"] == "魔王" and it["old"] == ""


def test_segment_issues_ok_when_candidate_present():
    """译文里已经用了约定译名 → 不提示（避免天天误报）。"""
    assert segment_term_issues("魔王が現れた。", "魔王出现了。",
                               [Term("魔王", ["魔王", "魔皇"])]) == []


def test_segment_issues_ignores_terms_absent_from_source():
    """源文里没这个术语就不关它的事（哪怕译文里也没有）。"""
    assert segment_term_issues("村人が現れた。", "村民出现了。",
                               [Term("魔王", ["魔王"])]) == []


def test_segment_issues_detects_stale_old_candidate():
    """术语改过译名：译文里还留着旧译名 → kind=old（可一键替换）。"""
    issues = segment_term_issues("魔王が現れた。", "demon lord 出现了。",
                                 [Term("魔王", ["魔皇"])],
                                 old_state={"魔王": ["demon lord", "魔皇"]})
    assert len(issues) == 1
    it = issues[0]
    assert it["kind"] == "old" and it["old"] == "demon lord" and it["new"] == "魔皇"


def test_segment_issues_limit_and_empty_inputs():
    """术语太多时只报前几条；空输入不炸。"""
    entries = [Term(f"词{i}", [f"T{i}"]) for i in range(5)]
    src = "词0词1词2词3词4"
    assert len(segment_term_issues(src, "", entries)) == 3          # 默认 limit=3
    assert len(segment_term_issues(src, "", entries, limit=5)) == 5
    assert segment_term_issues("", "", entries) == []
    assert segment_term_issues("x", "y", []) == []


# ---------- 2. 服务层：新增术语也要能比出来 ----------
def make_project(tmp_path):
    """两段已译文本 + 一份含「灵石」的术语表快照（模拟改术语之前的状态）。"""
    proj = Project.create(tmp_path / "p", name="t", src_lang="zh-CN",
                          tgt_lang="en-US", provider_id="mock")
    doc = proj.db.upsert_document("source/a.md", "md", "h")
    proj.db.replace_segments(doc, [
        {"seq": 0, "text": "林凡握着灵石。", "is_heading": False, "translatable": True,
         "src_hash": "h0"},
        {"seq": 1, "text": "灵石可以兑换丹药。", "is_heading": False, "translatable": True,
         "src_hash": "h1"},
    ], proj.cfg_hash())
    proj.db.execute("UPDATE segments SET tgt_text='T:'||src_text, "
                    "status='machine_translated'")
    g = proj.glossary
    g.add("灵石", ["mana stone"], "货币")
    g.save()
    proj.snapshot_terms()
    return proj


def test_analyze_reports_newly_added_term(tmp_path):
    """**新增**术语后，源文含它、译文里没有它的段落必须被列出来（原来完全查不到）。"""
    proj = make_project(tmp_path)
    g = proj.glossary
    g.add("丹药", ["pill"], "道具")       # 新增一条：译文里还没有 pill
    g.save()
    proj.snapshot_terms()

    svc = TermImpactService(proj)
    affected = svc.analyze()
    missing = [it for it in affected if it["kind"] == "missing"]
    assert missing, f"新增术语后应列出「没用上新术语」的段落：{affected}"
    assert all(it["term"] == "丹药" and it["new"] == "pill" for it in missing)
    assert all(it["doc_id"] for it in missing)
    # 只有第 2 段（seq=1）的源文含「丹药」
    seg = proj.db.get_segment(missing[0]["seg_id"])
    assert "丹药" in seg["src_text"]
    proj.close()


def test_analyze_keeps_old_candidate_case(tmp_path):
    """改了候选之后，「旧译名仍在用」这一类照旧要比得出来（回归）。"""
    proj = make_project(tmp_path)
    proj.db.execute("UPDATE segments SET tgt_text=REPLACE(tgt_text,'灵石','mana stone')")
    g = proj.glossary
    g.add("灵石", ["soulstone"], "货币")
    g.save()
    proj.snapshot_terms()

    affected = TermImpactService(proj).analyze()
    old = [it for it in affected if it["kind"] == "old"]
    assert len(old) == 2 and all(it["old"] == "mana stone" and it["new"] == "soulstone"
                                 for it in old)
    proj.close()


def test_replace_all_skips_missing_items(tmp_path):
    """`kind=missing` 没有旧串可替换 —— 绝不能"到处插入新词"。"""
    proj = make_project(tmp_path)
    before = {s["id"]: s["tgt_text"] for s in proj.db.list_segments(translatable=True)}
    svc = TermImpactService(proj)
    g = proj.glossary
    g.add("丹药", ["pill"], "道具")
    g.save()
    proj.snapshot_terms()
    affected = [it for it in svc.analyze() if it["kind"] == "missing"]
    assert affected
    assert svc.replace_all(affected) == 0                      # 一段都不改
    after = {s["id"]: s["tgt_text"] for s in proj.db.list_segments(translatable=True)}
    assert after == before                                     # 译文原样不动
    proj.close()


def test_mark_retranslate_covers_new_term_segments(tmp_path):
    """「标记重译」对新增术语这一类同样有效（把段落置回待译，下次翻译带上新术语）。"""
    proj = make_project(tmp_path)
    svc = TermImpactService(proj)
    g = proj.glossary
    g.add("丹药", ["pill"], "道具")
    g.save()
    proj.snapshot_terms()
    affected = svc.analyze()
    assert svc.mark_retranslate(affected) >= 1
    assert any(s["status"] == "pending" for s in proj.db.list_segments(translatable=True))
    proj.close()


# ---------- 3. 工作台：保存术语后给出影响提示 ----------
class FakeMain:
    def __init__(self):
        self.pages = {}
        self.went = []

    def goto(self, name):
        self.went.append(name)


class FakeReviewPage:
    """替身校对页：只记录「术语变更影响」对话框被打开过。"""

    def __init__(self):
        self.opened = 0

    def _term_impact(self):
        self.opened += 1


class FakeCtx:
    """最小上下文：工作台只用到 project / term_impact / bridge / cfg。"""

    def __init__(self, proj):
        self.project = proj
        self.term_impact = TermImpactService(proj)
        self.bridge = Bridge()            # 真信号桥，toast 收集到 self.toasts
        self.toasts: list[str] = []
        self.bridge.toast.connect(self.toasts.append)
        self.cfg = {}

    def save_cfg(self):
        pass


def _workbench(proj):
    from app.pages.workbench_page import WorkbenchPage
    ctx, main = FakeCtx(proj), FakeMain()
    page = WorkbenchPage(ctx, main)
    review = FakeReviewPage()
    main.pages["校对编辑器"] = review        # goto 后要能拿到页面对象
    return page, ctx, main, review


def test_workbench_prompts_after_new_term(tmp_path, qapp):
    """新增术语后弹提示说明有多少段要订正，选「去校对页处理」会切页。"""
    proj = make_project(tmp_path)
    proj.db.execute("UPDATE segments SET tgt_text=REPLACE(tgt_text,'灵石','mana stone')")
    page, ctx, main, review = _workbench(proj)
    g = proj.glossary
    g.add("丹药", ["pill"], "道具")
    g.save()
    proj.snapshot_terms()

    opened = []
    from PySide6.QtWidgets import QMessageBox as Box
    orig_add = Box.addButton

    def add(self, *a, **k):
        btn = orig_add(self, *a, **k)
        opened.append((self.text(), a[0] if a else ""))
        return btn

    import app.pages.workbench_page as wp
    wp.QMessageBox.addButton = add
    wp.QMessageBox.exec = lambda self: 0
    wp.QMessageBox.clickedButton = lambda self: self.buttons()[0]
    try:
        page._offer_term_fixup("丹药")
    finally:
        wp.QMessageBox.addButton = orig_add

    assert opened, "有受影响段落时必须给出提示"
    assert "需要核对" in opened[0][0] and "丹药" in opened[0][0]
    assert main.went == ["校对编辑器"], "点「去校对页处理」应切到校对页"
    assert review.opened == 1, "切过去后应直接打开「术语变更影响」列表"
    proj.close()


def test_workbench_silent_when_nothing_affected(tmp_path, qapp):
    """术语改了但没有任何段落受影响 → 只发一条轻提示，不弹窗打扰。"""
    proj = make_project(tmp_path)
    # 源文与译文里都没有这个术语 → 不该有任何"需要订正"的段落
    proj.db.execute("UPDATE segments SET src_text='无关原文', tgt_text='完全无关的译文'")
    page, ctx, main, review = _workbench(proj)
    g = proj.glossary
    g.add("丹药", ["pill"], "道具")
    g.save()
    proj.snapshot_terms()

    page._offer_term_fixup("丹药")
    assert ctx.toasts and "没有需要订正" in ctx.toasts[-1]
    assert main.went == [] and review.opened == 0
    proj.close()


def test_workbench_skips_analysis_for_bulk_changes(tmp_path, qapp):
    """一次改了几十个术语（多为批量合并）时不逐个全表扫，改为提示去对话框按需分析。"""
    proj = make_project(tmp_path)
    page, ctx, main, review = _workbench(proj)
    g = proj.glossary
    for i in range(25):
        g.add(f"批量词{i}", [f"bulk{i}"], "")
    g.save()
    proj.snapshot_terms()

    page._offer_term_fixup("", merged=25)
    assert ctx.toasts and "术语变更影响" in ctx.toasts[-1]
    assert main.went == [] and review.opened == 0
    proj.close()
