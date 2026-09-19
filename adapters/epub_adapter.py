"""epub 适配器（设计 §7.1 扩展）。

EPUB 是 zip 容器：`mimetype` / `META-INF/container.xml` / OPF（manifest + spine）/
若干 XHTML 内容文档 / 图片、CSS、字体等资源。

策略：**只翻译内容文档的正文**，其余条目原样拷回新 zip；内容文档按 OPF 的
spine 顺序解析（= 阅读顺序），并且**多个内容文档共用同一个 seq 空间** ——
导出时会重新 parse 源文件再按 seq 对齐译文，seq 必须可确定性重放。

三条必须守住的规范/不变量：
1. `mimetype` 必须是新 zip 的**第一个**条目且**不压缩**（EPUB 规范硬要求）；
2. 内容文档必须写回**良构 XHTML** —— 因此用 XML 方式序列化（自闭合空标签、
   保留根元素上的 `xmlns`），HTML 方式会输出 `<br>` 之类破坏良构性的写法；
3. 解析失败的内容文档不参与翻译、原样保留，不能因此让整本书导入失败。

已知边界：`<a>` 与 html 适配器一致整体作为保护占位符（URL 防注入优先），
因此**目录（nav.xhtml / NCX）里的链接文字不会被翻译**，正文中的链接文字同理。
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from lxml import etree
from lxml import html as lhtml

from adapters.base import Block, DocumentModel, FormatError
from adapters.html_adapter import HtmlAdapter

_MEDIA_TYPES = ("application/xhtml+xml", "text/html")
_XHTML_EXTS = (".xhtml", ".html", ".htm")


def _head_parts(raw: bytes) -> tuple[str, str]:
    """取出内容文档开头的 XML 声明与 DOCTYPE（有则按原样写回）。"""
    head = raw[:512]
    m = re.search(rb"<\?xml[^>]*\?>", head, re.S)
    decl = m.group(0).decode("utf-8", "replace") if m else ""
    m2 = re.search(rb"<!DOCTYPE[^>]*>", head, re.I | re.S)
    doctype = m2.group(0).decode("utf-8", "replace") if m2 else ""
    return decl, doctype


class EpubAdapter:
    name = "epub"

    def __init__(self) -> None:
        # 复用 html 适配器的单文档解析/渲染核心（含占位符保护、振假名、属性段）
        self._html = HtmlAdapter()

    # ---------- 容器解析 ----------
    @staticmethod
    def _resolve(base_dir: str, href: str) -> str:
        """把 OPF 里的相对 href 解析成 zip 内的条目名。"""
        href = unquote((href or "").split("#", 1)[0])
        return posixpath.normpath(posixpath.join(base_dir, href) if base_dir
                                  else posixpath.normpath(href))

    def _content_docs(self, zin: zipfile.ZipFile) -> list[str]:
        """按 spine 顺序返回内容文档的条目名；OPF 不可用时退回扫描扩展名。"""
        try:
            container = etree.fromstring(zin.read("META-INF/container.xml"))
            nodes = container.xpath("//*[local-name()='rootfile']")
            opf_path = nodes[0].get("full-path") if nodes else None
            if not opf_path:
                raise ValueError("container.xml 未声明 rootfile")
            opf = etree.fromstring(zin.read(opf_path))
            opf_dir = posixpath.dirname(opf_path)
            items: dict[str, str] = {}
            for it in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
                if (it.get("media-type") or "").lower() in _MEDIA_TYPES:
                    items[it.get("id") or ""] = self._resolve(
                        opf_dir, it.get("href") or "")
            order: list[str] = []
            for ref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
                name = items.get(ref.get("idref") or "")
                if name and name not in order:
                    order.append(name)
            # spine 没覆盖到的内容文档按 manifest 顺序补齐（保持确定性）
            for name in items.values():
                if name not in order:
                    order.append(name)
            have = set(zin.namelist())
            found = [n for n in order if n in have]
            if found:
                return found
        except Exception:  # noqa: BLE001 OPF 损坏/缺失 → 退回扩展名扫描
            pass
        return sorted(n for n in zin.namelist()
                      if n.lower().endswith(_XHTML_EXTS))

    # ---------- 解析 ----------
    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        try:
            zin = zipfile.ZipFile(path)
        except zipfile.BadZipFile as e:
            raise FormatError(f"不是有效的 EPUB（zip 打不开）：{e}") from e

        blocks: list[Block] = []
        docs: list[dict] = []
        with zin:
            doc_names = self._content_docs(zin)
            if not doc_names:
                raise FormatError("EPUB 内没有找到可翻译的 XHTML 内容文档")
            for name in doc_names:
                raw = zin.read(name)
                try:
                    # 必须传 bytes：内容文档通常带 <?xml ...?>，lxml 不接受
                    # 带编码声明的 str 输入
                    tree = lhtml.document_fromstring(raw)
                except Exception:  # noqa: BLE001 单个文档坏了不影响整本书
                    continue
                decl, doctype = _head_parts(raw)
                lo = len(blocks)
                sub, block_els, attr_refs = self._html.parse_tree(tree, seq_base=lo)
                blocks.extend(sub)
                docs.append({"name": name, "tree": tree, "block_els": block_els,
                             "attr_refs": attr_refs, "lo": lo, "hi": len(blocks),
                             "decl": decl, "doctype": doctype})

        if not docs:
            raise FormatError("EPUB 的内容文档都无法解析，无法导入")
        model = DocumentModel(path=path, fmt="epub", blocks=blocks)
        model.skeleton = {"docs": docs}
        return model

    # ---------- 渲染 ----------
    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        if mode not in ("target", "bi_inter"):
            raise FormatError("epub 仅支持 target / bi_inter 模式")

        modified: dict[str, bytes] = {}
        for d in model.skeleton["docs"]:
            sub = model.blocks[d["lo"]:d["hi"]]
            text = self._html.render_tree(
                d["tree"], sub, d["block_els"], d["attr_refs"], translations,
                mode=mode, ruby_maps=ruby_maps, xml=True)
            head = "".join(x + "\n" for x in (d["decl"], d["doctype"]) if x)
            modified[d["name"]] = (head + text).encode("utf-8")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(model.path) as zin, \
                zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
            # EPUB 规范：mimetype 必须是第一个条目，且不得压缩
            if "mimetype" in zin.namelist():
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                zout.writestr(info, zin.read("mimetype"))
            for item in zin.infolist():
                if item.filename == "mimetype":
                    continue
                data = modified.get(item.filename)
                if data is None:
                    data = b"" if item.is_dir() else zin.read(item.filename)
                # 沿用原 ZipInfo：保留压缩方式与时间戳
                zout.writestr(item, data)
