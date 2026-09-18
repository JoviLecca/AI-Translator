from pathlib import Path

from core.consistency import export_report_csv, term_consistency_report
from core.pipeline import ImportService, TranslationService
from core.project import Project
from llm.mock import MockProvider


def test_consistency_report(tmp_path):
    project = Project.create(tmp_path / "p", name="t", src_lang="zh-CN",
                             tgt_lang="en-US", provider_id="mock")
    f = tmp_path / "a.md"
    f.write_text("灵石闪光。\n\n丹炉冒烟。\n", encoding="utf-8")
    ImportService(project).import_files([f])
    g = project.glossary
    g.add("灵石", ["spirit stone"], "")
    g.add("丹炉", ["cauldron"], "")
    g.save()
    # 只译出 spirit stone，cauldron 缺失
    svc = TranslationService(project, {})
    h = svc.start(provider=MockProvider(
        translator=lambda t: "T:" + t.replace("灵石", "spirit stone")))
    h.join(timeout=30)
    assert h.result["failed"] == 0

    rows = term_consistency_report(project)
    by_src = {r["src"]: r for r in rows}
    assert by_src["灵石"]["status"] == "ok"
    assert by_src["灵石"]["candidate_hits"] >= 1
    assert by_src["丹炉"]["status"] == "missing"          # 0 命中
    assert by_src["丹炉"]["suspicious_segments"]           # 源文有但译文无候选

    out = tmp_path / "report.csv"
    export_report_csv(rows, out)
    text = out.read_text(encoding="utf-8-sig")
    assert "missing" in text and "cauldron" in text
    project.close()


def test_variants_plural():
    from core.consistency import count_occurrences, variants
    assert "spirit stones" in variants("spirit stone")
    assert count_occurrences("Spirit Stones and spirit stone", variants("spirit stone")) == 2
    assert count_occurrences("cauldron", variants("cauldron")) == 1
