# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（设计 §11 M3：打包安装程序）。

构建：python tools/build.py（或 pyinstaller tools/AITranslator.spec）
产物：dist/AITranslator/ 目录，运行 AITranslator.exe。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821  SPECPATH 由 PyInstaller 注入

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        # 适配器与 Provider 均为懒加载，显式声明防止漏打
        "adapters.txt_adapter", "adapters.md_adapter", "adapters.html_adapter",
        "adapters.docx_adapter", "adapters.ocr_adapter",
        "adapters.epub_adapter", "adapters.srt_adapter",
        "llm.openai_compat", "llm.anthropic_provider", "llm.gemini_provider",
        # 本地推理服务探测（settings_pages 里是函数内 import）
        "llm.local_probe",
        # 界面英文词条：core/i18n 用 importlib 按语言动态加载，
        # 静态分析看不到 —— 漏打的话 exe 里切到 English 会把 KeyError/ImportError 抛到界面上
        "core.i18n.en_core", "core.i18n.en_settings",
        "core.i18n.en_workbench", "core.i18n.en_review",
        "keyring.backends.Windows", "keyring.backends.chainer",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AITranslator",
    debug=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AITranslator")
