"""增补需求验收测试：振假名 translate 策略 + 滑窗上下文端到端（Mock Provider）。"""
import json
from pathlib import Path

from core.pipeline import ExportService, ImportService, ReviewService, TranslationService
from core.project import Project
from llm.mock import MockProvider

MD_SAMPLE = """# 序章

　僕は北瀬一廣《かずひろ》。`code9` を起動する。

　一廣《かずひろ》は微笑んだ。

　遠くで鐘が鳴る。

""" + "\n\n".join(f"　第{i}段落の本文である。鐘は鳴り続ける。" for i in range(1, 13)) + "\n"


def mock_translate(text: str) -> str:
    return "T:" + text.replace("起動", "启动").replace("微笑", "微笑").replace("遠く", "远处")


def test_r1_translate_policy_full_flow(tmp_path):
    project = Project.create(tmp_path / "novel", name="ruby", src_lang="ja-JP",
                             tgt_lang="zh-CN", style_preset="literary",
                             provider_id="mock", ruby_policy="translate")
    f = tmp_path / "ch.md"
    f.write_text(MD_SAMPLE, encoding="utf-8")
    imported, errs = ImportService(project).import_files([f])
    assert not errs
    # 段内注音槽已 token 化
    segs = project.db.list_segments(translatable=True)
    assert any("{r0}" in s["src_text"] for s in segs)

    svc = TranslationService(project, {"concurrency": 1, "context_segments": 1})
    pre = svc.precheck()
    assert pre["ruby_policy"] == "translate" and pre["slide"] == 1

    # 归纳剥离注音槽（阶段一不产生假名垃圾候选）
    from core.term_induction import extract_candidates
    cands = extract_candidates([s["src_text"] for s in segs], "ja-JP")
    assert all("{r" not in w for w, _ in cands)

    handle = svc.start(provider=MockProvider(
        translator=mock_translate, ruby=lambda i: {"r0": "卡兹希罗", "r1": "卡兹希罗"}))
    handle.join(timeout=60)
    assert handle.result["failed"] == 0, handle.result

    segs = project.db.list_segments(translatable=True)
    for s in segs:
        if "{r0}" in s["src_text"]:
            assert "{r0}" in s["tgt_text"]                      # keep/translate 保留槽
            assert json.loads(s["ruby_map"])["{r0}"] == "卡兹希罗"  # 译注音入库

    # 导出：注音以《译注音》还原，格式为 md《》
    doc_ids = [d["id"] for d in project.db.list_documents()]
    results = ExportService(project).export(doc_ids, mode="target")
    assert all(r["ok"] for r in results), results
    out = Path(results[0]["out"]).read_text(encoding="utf-8")
    assert "《卡兹希罗》" in out
    assert "{r" not in out
    assert "`code9`" in out and "T:# 序章" in out
    project.close()


def test_r2_slide_prompt_in_real_service(tmp_path):
    """滑窗经服务层生效：第二批 prompt 含前文（已译）与后文（源文）。"""
    from llm.mock import MockProvider as MP

    class RecMock(MP):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.all_messages = []

        async def chat(self, messages, **kw2):
            res = await super().chat(messages, **kw2)
            self.all_messages.append(list(messages))
            return res

    project = Project.create(tmp_path / "novel2", name="slide", src_lang="ja-JP",
                             tgt_lang="zh-CN", provider_id="mock",
                             context_slide=1)
    f = tmp_path / "ch.md"
    f.write_text(MD_SAMPLE, encoding="utf-8")
    ImportService(project).import_files([f])
    svc = TranslationService(project, {"concurrency": 1, "context_segments": 2})
    assert svc.slide() == 1  # 项目级覆盖全局
    provider = RecMock(translator=mock_translate)
    handle = svc.start(provider=provider)
    handle.join(timeout=60)
    assert handle.result["failed"] == 0
    users = [m[-1].content for m in provider.all_messages]
    assert any("【前文" in u for u in users) or any("【后文" in u for u in users)
    # 段落数（>10 才分批）；本样例 4 段一批 → 只有后文出现于第一批
    assert users[0].count("· ") >= 1
    project.close()
