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


def test_md_inline_html_ruby_follows_project_policy(tmp_path):
    """markdown 内联 `<ruby>` 必须走项目的振假名策略（端到端）。

    回归：md 适配器此前不认内联 `<ruby>`，标签连同内容被当正文送翻，
    三种策略全部失效 —— 批文本里没有 {rN}，连策略指令都不会注入。
    """
    from llm.mock import MockProvider as MP

    class RecMock(MP):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.systems: list[str] = []

        async def chat(self, messages, **kw2):
            res = await super().chat(messages, **kw2)
            self.systems.append(messages[0].content)
            return res

    f = tmp_path / "ch.md"
    f.write_text("彼は<ruby>魔法<rt>まほう</rt></ruby>を使った。\n", encoding="utf-8")
    keep_translation = "他使用了魔法{r0}。"

    # ---- keep：注音原样保留，导出还原为原生 <ruby> ----
    proj = Project.create(tmp_path / "keep", name="k", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock", ruby_policy="keep")
    ImportService(proj).import_files([f])
    seg = proj.db.list_segments(translatable=True)[0]
    assert "魔法{r0}" in seg["src_text"], seg["src_text"]
    assert "まほう" not in seg["src_text"]      # 注音不再进送翻文本
    assert "<rt>" not in seg["src_text"]

    prov = RecMock(translator=lambda _t: keep_translation)
    h = TranslationService(proj, {"concurrency": 1, "context_segments": 0}).start(
        provider=prov)
    h.join(timeout=60)
    assert h.result["failed"] == 0, h.result
    assert any("原样保留" in s for s in prov.systems), "keep 策略指令未注入 prompt"

    ids = [d["id"] for d in proj.db.list_documents()]
    out = Path(ExportService(proj).export(ids, mode="target")[0]["out"])
    text = out.read_text(encoding="utf-8")
    assert "<ruby>魔法<rt>まほう</rt></ruby>" in text, text
    proj.close()

    # ---- drop：策略指令为删除，残留 token 被剥离且标待复核 ----
    proj2 = Project.create(tmp_path / "drop", name="d", src_lang="ja-JP",
                           tgt_lang="zh-CN", provider_id="mock", ruby_policy="drop")
    ImportService(proj2).import_files([f])
    prov2 = RecMock(translator=lambda _t: keep_translation)   # 故意不守规矩
    h2 = TranslationService(proj2, {"concurrency": 1, "context_segments": 0}).start(
        provider=prov2)
    h2.join(timeout=60)
    assert h2.result["failed"] == 0, h2.result
    assert any("删除全部" in s for s in prov2.systems), "drop 策略指令未注入 prompt"

    ids2 = [d["id"] for d in proj2.db.list_documents()]
    text2 = Path(ExportService(proj2).export(ids2, mode="target")[0]["out"]) \
        .read_text(encoding="utf-8")
    assert "{r" not in text2 and "まほう" not in text2, text2
    assert proj2.db.list_segments(translatable=True)[0]["review_flag"] == 1
    proj2.close()


def test_drop_policy_removes_ruby_markup_entirely(tmp_path):
    """drop 必须让译文里既没有注音、也没有 `<ruby>` 格式，只留基词。

    覆盖两条泄漏路径（用户反馈：drop 只去掉了注音，标签还留在译文里）：
    ① 模型把 `<ruby>` 标签原样抄回；
    ② 修复前导入的旧数据 / 旧译文 —— 库里本来就带着标签，不该逼用户重译。
    """
    f = tmp_path / "ch.md"
    f.write_text("彼は<ruby>魔法<rt>まほう</rt></ruby>を使った。\n", encoding="utf-8")
    dirty = "他使用了<ruby>魔法<rt>まほう</rt></ruby>。"

    # ---- ① 模型不守规矩，把标签抄回来 → 落库时就应清理 ----
    proj = Project.create(tmp_path / "d1", name="d1", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock", ruby_policy="drop")
    ImportService(proj).import_files([f])
    h = TranslationService(proj, {"concurrency": 1, "context_segments": 0}).start(
        provider=MockProvider(translator=lambda _t: dirty))
    h.join(timeout=60)
    assert h.result["failed"] == 0, h.result
    seg = proj.db.list_segments(translatable=True)[0]
    assert "<ruby>" not in seg["tgt_text"], seg["tgt_text"]
    assert "まほう" not in seg["tgt_text"], seg["tgt_text"]
    assert "魔法" in seg["tgt_text"], seg["tgt_text"]          # 基词保留
    ids = [d["id"] for d in proj.db.list_documents()]
    text = Path(ExportService(proj).export(ids, mode="target")[0]["out"]) \
        .read_text(encoding="utf-8")
    assert "<ruby>" not in text and "まほう" not in text and "魔法" in text, text
    proj.close()

    # ---- ② 已有译文本身就带标签 → 导出兜底清理，无需重新翻译 ----
    proj2 = Project.create(tmp_path / "d2", name="d2", src_lang="ja-JP",
                           tgt_lang="zh-CN", provider_id="mock", ruby_policy="drop")
    ImportService(proj2).import_files([f])
    seg2 = proj2.db.list_segments(translatable=True)[0]
    proj2.db.update_segment(seg2["id"], tgt=dirty, status="machine_translated")
    ids2 = [d["id"] for d in proj2.db.list_documents()]
    text2 = Path(ExportService(proj2).export(ids2, mode="target")[0]["out"]) \
        .read_text(encoding="utf-8")
    assert "<ruby>" not in text2 and "まほう" not in text2, text2
    assert "魔法" in text2, text2
    proj2.close()

    # ---- keep 策略不能被误清理：标签与注音都要保留 ----
    proj3 = Project.create(tmp_path / "d3", name="d3", src_lang="ja-JP",
                           tgt_lang="zh-CN", provider_id="mock", ruby_policy="keep")
    ImportService(proj3).import_files([f])
    seg3 = proj3.db.list_segments(translatable=True)[0]
    proj3.db.update_segment(seg3["id"], tgt="他使用了魔法{r0}。",
                            status="machine_translated")
    ids3 = [d["id"] for d in proj3.db.list_documents()]
    text3 = Path(ExportService(proj3).export(ids3, mode="target")[0]["out"]) \
        .read_text(encoding="utf-8")
    assert "<ruby>魔法<rt>まほう</rt></ruby>" in text3, text3
    proj3.close()


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
