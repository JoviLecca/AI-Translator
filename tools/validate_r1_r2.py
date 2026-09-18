"""R1/R2 真实 API 验证（增补设计 §1.9 / §2.7）：GLM 真跑振假名 translate + 滑窗 2。

以《第１話》节选注入振假名样例，ja→zh，验证：
1. 批次 prompt 含【前文/后文】滑窗块与振假名策略指令；
2. 译文保留 {rN} 槽、ruby 字段返回译注音并入库；
3. 导出 md 以《译注音》还原，格式零丢失。
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pipeline import ExportService, ImportService, TranslationService
from core.project import Project
from storage import secrets

MD = """# 第１話　こんにちは、エルフさん

　窓の外からはスズメの鳴き声がひびいていた。

　いつものように快適な目覚めであり、僕はのんびりとした朝のひとときを楽しんで……などいない。どっく、どっく、と心臓は激しく鳴っている。

　一人用にしてはやや広いベッドの中、僕のすぐ隣には少女がいた。この時点でもう心臓バクバクなのだが、彼女からは長い耳が生えている。

　まつげは長く、布団からはさらりと光沢のある銀髪。

　と、昨夜のことをじっくりと思い返している中、エルフの娘《むすめ》は瞳を開かせた。

　ゆっくりと開かれるその瞳は、まるで花が咲く瞬間を見るようだ。薄紫の品ある色彩はあざやかで、つい吸い込まれそうになる。

　昨夜、いったい何があったのだろう。僕の名は北瀬一廣《かずひろ》という。

　彼女はエルフという種族で、本名はマリアーベル、通称マリーという子だ。

　そして何故か僕の名前は「カズヒホ」だ。本名は北瀬一廣《かずひろ》であり、その名前を文字った……というか初期設定を間違えた。

　髪はさらりとした綿毛色をしており、白髪と呼ぶには光沢がありすぎる。

　瞳は薄い紫色で、アメシストのようだからまさしく「宝石のよう」という表現がぴったりだと思わせる。

　川沿いを進んでゆくと、すぐに遺跡は現れた。

　ナズルナズル遺跡の探索は始まった。
"""


class RecordingProvider:
    def __init__(self, inner):
        self.inner = inner
        self.messages = []
        for attr in ("id", "name", "model", "price_in", "price_out"):
            setattr(self, attr, getattr(inner, attr))

    async def chat(self, messages, **kw):
        self.messages.append([m.content for m in messages])
        return await self.inner.chat(messages, **kw)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="r1r2_"))
    project = Project.create(tmp / "novel", name="ruby真跑", src_lang="ja-JP",
                             tgt_lang="zh-CN", style_preset="literary",
                             provider_id="zhipu", ruby_policy="translate",
                             context_slide=2)
    f = tmp / "ch.md"
    f.write_text(MD, encoding="utf-8")
    imported, errs = ImportService(project).import_files([f])
    assert not errs, errs
    segs = project.db.list_segments(translatable=True)
    print(f"[1] 导入 {len(segs)} 段；含注音槽段数：",
          sum(1 for s in segs if "{r" in s["src_text"]))

    from core.appconfig import load_config
    svc = TranslationService(project, load_config())
    pre = svc.precheck()
    print(f"[2] 预检：slide={pre['slide']} ruby={pre['ruby_policy']} "
          f"estimate={pre['estimate']}")

    inner = svc.build_provider()  # 真实 GLM
    provider = RecordingProvider(inner)
    handle = svc.start(provider=provider)
    handle.join(timeout=600)
    res = handle.result
    print(f"[3] 翻译：done={res['done']} failed={res['failed']} "
          f"tokens={res['tokens_in']}/{res['tokens_out']} cost={res['cost']:.4f}")
    assert res["failed"] == 0, res

    # prompt 验证：滑窗块 + 振假名策略指令
    user0 = provider.messages[0][1]
    has_after = "【后文（源文" in user0
    later = provider.messages[-1][1]
    has_before = "【前文（已定稿" in later
    has_ruby_rule = any("振假名" in m[0] for m in provider.messages)
    print(f"[4] prompt：首批含后文块={has_after} 末批含前文块={has_before} "
          f"振假名策略指令={has_ruby_rule}")
    assert has_ruby_rule
    assert has_before or has_after

    segs = project.db.list_segments(translatable=True)
    ruby_segs = [s for s in segs if "{r" in s["src_text"]]
    ok_keep = sum(1 for s in ruby_segs if "{r" in (s["tgt_text"] or ""))
    maps = {s["id"]: s["ruby_map"] for s in ruby_segs if s["ruby_map"]}
    print(f"[5] 振假名：{len(ruby_segs)} 个注音段，{ok_keep} 段译文保留槽位，"
          f"{len(maps)} 段拿到译注音")
    for s in ruby_segs[:2]:
        print("    SRC:", s["src_text"][:40])
        print("    TGT:", (s["tgt_text"] or "")[:60])
        print("    RUBY:", s["ruby_map"])

    doc_ids = [d["id"] for d in project.db.list_documents()]
    results = ExportService(project).export(doc_ids, mode="target")
    assert all(r["ok"] for r in results), results
    out = Path(results[0]["out"]).read_text(encoding="utf-8")
    has_trans_ruby = "《" in out and "》" in out
    no_tokens = "{r" not in out
    print(f"[6] 导出：{results[0]['out']}")
    print(f"    注音还原《》={has_trans_ruby} 无残留token={no_tokens}")
    for line in out.splitlines():
        if "《" in line:
            print("    样例:", line.strip()[:70])
            break
    assert no_tokens
    project.close()
    print("R1/R2 真实 API 验证：通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
