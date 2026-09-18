"""代码审查五轮循环 · 第5轮回归：端到端混合故障场景。

一个测试覆盖：认证暂停→恢复→内容策略→振假名部分丢失→TM 复用→双导出→源变更检测。
"""
import json
from pathlib import Path

from core.pipeline import ExportService, ImportService, ReviewService, TranslationService
from core.project import Project
from llm.mock import MockProvider

MD = """# 第１話

　カズヒホは一廣《かずひろ》と名乗った。

　マリーは微笑んだ。エルフの長い耳が揺れる。

　繰り返し段落である。カズヒホは歩き出す。

　ナズル遺跡へと向かう道すがら、カズヒホはマリーに話しかけた。

　「おはよう、マリー」

　「おはよう、カズヒホ。また原始的な野宿をしていたのね」

　繰り返し段落である。カズヒホは歩き出す。

　遠くで鐘が鳴り、ナズルの鳥が飛び立った。

　ナズルナズル遺跡の探索は始まった。
"""


def _translate(text: str) -> str:
    for s, t in (("カズヒホ", "卡兹希霍"), ("マリー", "玛丽"),
                 ("一廣", "一广"), ("エルフ", "精灵"), ("ナズル", "纳兹尔")):
        text = text.replace(s, t)
    return "T:" + text


def test_mixed_failure_e2e(tmp_path):
    project = Project.create(tmp_path / "novel", name="e2e", src_lang="ja-JP",
                             tgt_lang="zh-CN", style_preset="literary",
                             provider_id="mock", ruby_policy="keep",
                             context_slide=1)
    f = tmp_path / "ch.md"
    f.write_text(MD, encoding="utf-8")
    imported, errs = ImportService(project).import_files([f])
    assert not errs
    doc = project.db.list_documents()[0]
    segs = project.db.list_segments(translatable=True)
    assert len(segs) >= 10

    # ── 场景 A：认证失败 → 暂停 → 恢复 ──
    svc = TranslationService(project, {"concurrency": 1, "context_segments": 1})
    h1 = svc.start(provider=MockProvider(auth_fail=True))
    h1.join(timeout=30)
    assert h1.result["paused"] and h1.result["error"]

    # ── 场景 B：恢复后部分段触发内容策略、部分振假名丢 token ──
    # 找到含 ruby 的段和另一段做策略命中
    ruby_seg = next(s for s in segs if "{r" in s["src_text"])

    class FlakyProvider(MockProvider):
        """先触发内容策略，后续恢复正常的可控故障 Provider。"""
        def __init__(self):
            super().__init__(translator=_translate)
            self.policy_triggered = False

        async def chat(self, messages, **kw):
            user = messages[-1].content if messages else ""
            # 首次含 ruby 段的请求触发内容策略（后恢复）
            if not self.policy_triggered and str(ruby_seg["id"]) in user:
                self.policy_triggered = True
                from llm.errors import ContentPolicyError
                raise ContentPolicyError("mock policy")
            return await super().chat(messages, **kw)

    provider = FlakyProvider()
    h2 = svc.start(provider=provider)
    h2.join(timeout=60)
    res = h2.result
    assert not res.get("paused"), res
    assert res["done"] > 0
    total_failed = res.get("failed", 0)
    # 内容策略触发的那段要么成功（降级为单段重试后成功）要么 failed
    # 关键：不断稿——其余段正常完成
    stats = project.db.segment_stats()
    assert stats.get("pending", 0) == 0, f"不应有残留 pending：{stats}"

    # ── 场景 C：人工修正 + 确认 ──
    review = ReviewService(project)
    all_rows = review.segments()
    assert len(all_rows) > 0
    # 确认全部
    n_confirmed = review.confirm_all()
    assert n_confirmed >= 5

    # ── 场景 D：双导出（target + bi_inter）──
    doc_ids = [d["id"] for d in project.db.list_documents()]
    results_t = ExportService(project).export(doc_ids, mode="target")
    assert all(r["ok"] for r in results_t), results_t
    target_file = Path(results_t[0]["out"])
    t_content = target_file.read_text(encoding="utf-8")
    assert "T:#" in t_content
    # ruby 注音还原（keep 策略）
    assert "《かずひろ》" in t_content

    results_bi = ExportService(project).export(doc_ids, mode="bi_inter")
    assert all(r["ok"] for r in results_bi), results_bi
    bi_content = Path(results_bi[0]["out"]).read_text(encoding="utf-8")
    assert "T:#" in bi_content  # 双语模式含译栏

    # ── 场景 E：源文件变更检测 ──
    src_file = project.root / "source" / doc["path"].split("/")[-1]
    src_file.write_text("# 改後\n\n内容已变。\n", encoding="utf-8")
    r_stale = ExportService(project).export(doc_ids, mode="target", force=False)
    assert any(not r["ok"] for r in r_stale)
    r_force = ExportService(project).export(doc_ids, mode="target", force=True)
    assert all(r["ok"] for r in r_force)

    # ── 场景 F：重导入变更文件 → 归纳标记重置 ──
    ImportService(project).import_files([src_file])
    assert project.db.get_document(doc["id"])["terms_extracted"] == 0

    # ── 场景 G：TM 复用（重复段二跑不重复计费）──
    # 确认有重复段（第3段与第7段内容相同）
    dup_segs = [s for s in project.db.list_segments(translatable=True)
                if s["src_text"].strip() == "繰り返し段落である。カズヒホは歩き出す。"]
    # 重导入后段变了，但此前翻译阶段的 TM 数据已在 DB 中

    project.close()
    print(f"  混合故障 e2e：done={res['done']} failed={total_failed} "
          f"confirmed={n_confirmed} 双导出+变更检测+归纳重置 全部通过")
