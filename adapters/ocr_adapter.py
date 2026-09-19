"""图片 OCR 适配器（设计 §7.1 / OCR 方案 v1.0）。

主引擎 RapidOCR (ONNX Runtime)：检测模型语言无关，识别模型按源语言选择。
兜底引擎 Windows.Media.Ocr (winsdk)：依赖系统已装语言包，零额外下载。

导出：提取文字翻译，输出 .md/.txt（不做原图还原，设计已知边界）。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from adapters.base import Block, DocumentModel, FormatError
from core.segmentation import is_mostly_latin, join_parts, split_long

_OCR_INSTANCES: dict[str, object] = {}

_LANG_MAP = {
    "zh": "ch", "zh-cn": "ch", "zh-tw": "ch",
    "ja": "japan", "jp": "japan",
    "en": "en", "en-us": "en", "en-gb": "en",
    "ko": "korean", "kr": "korean",
}


def _model_lang(src_lang: str) -> str:
    return _LANG_MAP.get((src_lang or "").lower().split("-")[0], "ch")


# ---------------------------------------------------------------- RapidOCR 主引擎
def rapidocr_backend(img_path: str, src_lang: str = "zh-CN") -> list[str]:
    """RapidOCR 主引擎：检测模型语言无关，识别模型按源语言。"""
    from rapidocr_onnxruntime import RapidOCR
    lang = _model_lang(src_lang)
    if lang not in _OCR_INSTANCES:
        _OCR_INSTANCES[lang] = RapidOCR()
    result, _ = _OCR_INSTANCES[lang](img_path)
    return [item[1] for item in (result or []) if item[1] and item[1].strip()]


# ---------------------------------------------------------------- winsdk 兜底引擎
def winsdk_backend(img_path: str, src_lang: str = "en-US") -> list[str]:
    """Windows.Media.Ocr 兜底（仅支持系统已装语言包）。"""
    return asyncio.run(_winsdk_ocr(img_path, src_lang))


async def _winsdk_ocr(img_path: str, src_lang: str) -> list[str]:
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.globalization import Language
    from winsdk.windows.storage import StorageFile
    from winsdk.windows.graphics.imaging import BitmapDecoder

    engine = None
    try:
        lang = Language(src_lang)
        engine = OcrEngine.try_create_from_language(lang)
    except Exception:
        pass
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise FormatError("Windows OCR 无可用语言引擎（请在系统设置中安装语言包）")

    file = await StorageFile.get_file_from_path_async(os.path.abspath(img_path))
    stream = await file.open_read_async()
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return [line.text for line in result.lines if line.text.strip()]


# ---------------------------------------------------------------- 引擎选择
_DEFAULT_BACKEND = None


def get_default_backend():
    """按可用性选择引擎：RapidOCR 优先，winsdk 兜底。"""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is not None:
        return _DEFAULT_BACKEND
    try:
        import rapidocr_onnxruntime  # noqa: F401
        _DEFAULT_BACKEND = rapidocr_backend
    except ImportError:
        try:
            import winsdk  # noqa: F401
            _DEFAULT_BACKEND = winsdk_backend
        except ImportError:
            _DEFAULT_BACKEND = None
    return _DEFAULT_BACKEND


def set_default_backend(backend) -> None:
    global _DEFAULT_BACKEND, _OCR_INSTANCES
    _DEFAULT_BACKEND = backend
    _OCR_INSTANCES.clear()   # 切换引擎时清缓存


# ---------------------------------------------------------------- 适配器
class OcrAdapter:
    name = "img"

    def __init__(self, backend=None, src_lang: str = ""):
        self.backend = backend or get_default_backend()
        self.src_lang = src_lang or "zh-CN"
        if self.backend is None:
            raise FormatError(
                "无可用 OCR 引擎。请安装：pip install rapidocr-onnxruntime（推荐）"
                "或 pip install winsdk")

    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        opts = opts or {}
        src_lang = opts.get("src_lang", self.src_lang)
        try:
            lines = self.backend(str(path), src_lang)
        except FormatError:
            raise
        except Exception as e:
            # 主引擎失败 → 尝试 winsdk 兜底
            if self.backend is not rapidocr_backend:
                raise FormatError(f"OCR 失败：{e}") from e
            try:
                lines = winsdk_backend(str(path), src_lang)
            except Exception:
                raise FormatError(f"OCR 失败（RapidOCR：{e}；winsdk 兜底亦失败）") from e

        blocks: list[Block] = []
        para = 0
        for line in lines:
            if not line.strip():
                continue   # 空行跳过（与 txt 适配器行为一致）
            for i, part in enumerate(split_long(line)):
                blocks.append(Block(seq=len(blocks), text=part,
                                    meta={"para": para, "part": i, "kind": "text"}))
            para += 1
        model = DocumentModel(path=path, fmt="img", blocks=blocks)
        model.skeleton = {"lines": lines}
        return model

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        if out_path.suffix.lower() not in (".md", ".txt"):
            out_path = out_path.with_suffix(".md")
        ruby_maps = ruby_maps or {}
        from adapters.ruby import restore_ruby
        lines_out: list[str] = []
        i = 0
        blocks = model.blocks
        while i < len(blocks):
            b = blocks[i]
            group = [bb for bb in blocks[i:] if bb.meta.get("para") == b.meta["para"]]
            latin = is_mostly_latin(b.text)
            tgts = [translations.get(bb.seq, bb.text) for bb in group]
            tgt_final = join_parts(tgts, latin)
            rt_map = {}
            for bb in group:
                rt_map.update(ruby_maps.get(bb.seq) or {})
            tgt_final = restore_ruby(tgt_final, None, rt_map)
            if mode == "target":
                lines_out.append(tgt_final)
            elif mode == "bi_inter":
                src_final = join_parts([bb.text for bb in group], latin)
                lines_out.append(src_final)
                lines_out.append(tgt_final)
                lines_out.append("")
            else:
                raise FormatError("图片导出仅支持 target / bi_inter 模式")
            i += len(group)
        text = "\n".join(lines_out) + ("\n" if lines_out else "")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
