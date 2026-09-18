"""代码审查五轮循环 · 第3轮回归：服务层与集成。"""
from pathlib import Path

from core.pipeline import ImportService, ReviewService, TranslationService
from core.project import Project
from llm.mock import MockProvider


def test_reimport_changed_file_resets_induction(tmp_path):
    """文件内容变更后重导入 → terms_extracted 重置，新内容可再归纳（第3轮修复）。"""
    proj = Project.create(tmp_path / "p", name="t", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock")
    f = tmp_path / "a.md"
    f.write_text("カズヒホが笑う。マリーも来た。\n", encoding="utf-8")
    imp = ImportService(proj)
    imp.import_files([f])
    doc = proj.db.list_documents()[0]
    proj.db.set_doc_fields(doc["id"], terms_extracted=1)

    f.write_text("カズヒホが笑う。マリーも来た。ナズル遺跡の鳥が鳴く。\n", encoding="utf-8")
    imp.import_files([f])
    doc2 = proj.db.get_document(doc["id"])
    assert doc2["terms_extracted"] == 0

    # 未变更的重导入不重置（避免重复归纳）
    imp.import_files([f])
    proj.db.set_doc_fields(doc["id"], terms_extracted=1)
    imp.import_files([f])
    assert proj.db.get_document(doc["id"])["terms_extracted"] == 1
    proj.close()


def test_precheck_reloads_external_glossary(tmp_path):
    """外部改术语表 → 预检自动重载并提示（第3轮修复）。"""
    proj = Project.create(tmp_path / "p", name="t", src_lang="ja-JP",
                          tgt_lang="zh-CN", provider_id="mock")
    f = tmp_path / "a.md"
    f.write_text("一段落。\n", encoding="utf-8")
    ImportService(proj).import_files([f])
    g = proj.glossary
    g.add("灵石", ["spirit stone"])
    g.save()
    (proj.root / "glossary.csv").write_text(
        "源语言,目标语,注释\n灵石,mana crystal,外部改的\n", encoding="utf-8-sig")
    assert g.external_changed()

    pre = TranslationService(proj, {}).precheck()
    assert pre["glossary_reloaded"] is True
    assert g.entries[0].candidates == ["mana crystal"]   # 已用新术语
    assert not g.external_changed()                       # 状态同步
    # 第二次预检不再提示
    assert TranslationService(proj, {}).precheck()["glossary_reloaded"] is False
    proj.close()


def test_provider_delete_removes_key(tmp_path):
    """删除 Provider 一并清除 keyring 密钥（第3轮修复，UI 逻辑，核心行为验证）。"""
    from storage import secrets as secrets_mod


    class _FakeSec:
        store = {}

        def set_password(self, s, a, v):
            self.store[(s, a)] = v

        def get_password(self, s, a):
            return self.store.get((s, a))

        def delete_password(self, s, a):
            self.store.pop((s, a), None)


    import app.pages.settings_pages as sp
    fake = _FakeSec()
    orig = secrets_mod.keyring if hasattr(secrets_mod, "keyring") else None
    # monkeypatch：secrets 模块内部 import keyring，直接替换函数级引用
    import storage.secrets as S
    S._keyring = fake
    sp.secrets = S
    S.set_api_key("t-prov", "k")
    assert S.get_api_key("t-prov") == "k"
    S.delete_api_key("t-prov")
    try:
        S.get_api_key("t-prov")
        assert False
    except KeyError:
        pass
