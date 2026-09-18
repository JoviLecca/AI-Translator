"""html 适配器（设计 §7.1 / v0.5 内联结构保真 / 增补设计 §1 振假名）。

- 块级整体成段；code/img/br/… 与整个 <a> 为保护占位符；
- <ruby>基<rt>注</rt></ruby>：基词内联 + {rN} 注音槽，渲染时锚定译词重建原生结构；
- 可译属性 alt/title/placeholder/meta@content 独立成段；
- 双语：bi_inter（译段后插原文段）、bi_table（生成左右表格文档）。
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from lxml import html as lhtml

from adapters.base import Block, DocumentModel, ProtectMap, effectively_empty, read_text
from adapters.ruby import ANY_TOKEN_SPLIT, RUBY_TOKEN_FULL, split_anchor
from core.segmentation import is_mostly_latin, join_parts, split_long

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td", "th",
               "figcaption", "dt", "dd", "title"}
_PROTECT_TAGS = {"code", "img", "br", "svg", "iframe", "script", "style", "input",
                 "pre", "audio", "video", "canvas", "a"}
_KEEP_INLINE = {"b", "i", "em", "strong", "span", "sub", "sup", "u", "mark", "small", "abbr"}
_TRANS_ATTRS = ("alt", "title", "placeholder")


def _has_block_descendant(el) -> bool:
    return any(isinstance(d.tag, str) and d.tag in _BLOCK_TAGS
               for d in el.iterdescendants())


def _find_blocks(el, out: list) -> None:
    for child in el:
        if not isinstance(child.tag, str):
            continue
        if child.tag == "pre":
            out.append(child)
            continue
        if child.tag in _PROTECT_TAGS:
            continue
        if (child.tag in _BLOCK_TAGS or child.tag == "div") and not _has_block_descendant(child):
            out.append(child)
        else:
            _find_blocks(child, out)


def _ruby_parts(el) -> tuple[str, str]:
    """<ruby> 元素 → (基词文本, 注音文本)；跳过 <rt>/<rp>。"""
    rt = "".join(t.text or "" for t in el.findall("rt"))
    skip = set()
    for tag in ("rt", "rp"):
        for sub in el.findall(tag):
            skip.add(id(sub))
    parts: list[str] = []

    def walk(node):
        if id(node) in skip:
            return
        if node.text:
            parts.append(node.text)
        for c in node:
            if isinstance(c.tag, str) and c.tag not in ("rt", "rp"):
                walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    return "".join(parts), rt


class HtmlAdapter:
    name = "html"

    def parse(self, path: Path, opts: dict | None = None) -> DocumentModel:
        text = read_text(path)
        is_doc = "<html" in text.lower()
        doc = lhtml.document_fromstring(text) if is_doc else \
            lhtml.fragment_fromstring(text, create_parent="div")

        blocks: list[Block] = []
        block_els: list = []
        tree_blocks: list = []
        _find_blocks(doc, tree_blocks)

        for para_i, el in enumerate(tree_blocks):
            if el.tag == "pre":
                blocks.append(Block(seq=len(blocks), text="", translatable=False,
                                    meta={"kind": "pre", "el_idx": len(block_els)}))
                block_els.append(el)
                continue
            pm = ProtectMap()
            ruby: list[dict] = []

            def flat(e, pm=pm, ruby=ruby):
                parts: list[str] = []
                if e.text:
                    parts.append(e.text)
                for c in e:
                    if not isinstance(c.tag, str):
                        if c.tail:
                            parts.append(c.tail)
                        continue
                    if c.tag == "ruby":
                        base, rt = _ruby_parts(c)
                        token = "{r%d}" % len(ruby)
                        ruby.append({"token": token, "base": base, "rt": rt,
                                     "style": "html"})
                        parts.append(base + token)
                    elif c.tag in _PROTECT_TAGS:
                        token = "{" + str(len(pm.map)) + "}"
                        pm.map[token] = c
                        parts.append(token)
                    elif c.tag in _KEEP_INLINE:
                        parts.append(f"<{c.tag}>" + flat(c) + f"</{c.tag}>")
                    else:
                        parts.append(flat(c))
                    if c.tail:
                        parts.append(c.tail)
                return "".join(parts)

            flat_text = flat(el)
            if effectively_empty(flat_text):
                # 仅占位符/空白构成的块（如纯图片段）→ 透传（反馈 #2/#3）
                blocks.append(Block(seq=len(blocks), text=flat_text, translatable=False,
                                    meta={"kind": "empty", "el_idx": len(block_els)}))
                block_els.append(el)
                continue
            parts = split_long(flat_text)
            for i, part in enumerate(parts):
                blocks.append(Block(
                    seq=len(blocks), text=part, is_heading=el.tag.startswith("h"),
                    meta={"kind": "block", "para": para_i, "part": i,
                          "ph": dict(pm.map), "ruby": ruby, "el_idx": len(block_els)}))
            block_els.append(el)

        attr_refs: list[tuple] = []
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            attrs = list(_TRANS_ATTRS)
            if el.tag == "meta" and el.get("name") == "description":
                attrs.append("content")
            for attr in attrs:
                if (el.get(attr) or "").strip():
                    blocks.append(Block(seq=len(blocks), text=el.get(attr),
                                        meta={"kind": "attr"}))
                    attr_refs.append((el, attr))

        model = DocumentModel(path=path, fmt="html", blocks=blocks)
        model.skeleton = {"doc": doc, "block_els": block_els, "attr_refs": attr_refs,
                          "is_doc": is_doc,
                          "doctype": text.lstrip()[:9].lower().startswith("<!doctype")}
        return model

    # ---------- 渲染 ----------
    def _group(self, model, first: Block):
        return [b for b in model.blocks
                if b.meta.get("kind") == "block"
                and b.meta.get("para") == first.meta["para"]
                and b.meta.get("el_idx") == first.meta["el_idx"]]

    def _group_final(self, model, first: Block, translations) -> tuple[str, str]:
        group = self._group(model, first)
        latin = is_mostly_latin(first.text)
        src_final = join_parts([b.text for b in group], latin)
        tgt_final = join_parts([translations.get(b.seq, b.text) for b in group], latin)
        return src_final, tgt_final

    @staticmethod
    def _append_text(el, text: str) -> None:
        if len(el):
            last = el[-1]
            last.tail = (last.tail or "") + text
        else:
            el.text = (el.text or "") + text

    @staticmethod
    def _trailing_text(el) -> str:
        return el.text if not len(el) else (el[-1].tail or "")

    @staticmethod
    def _set_trailing_text(el, value: str) -> None:
        if not len(el):
            el.text = value
        else:
            el[-1].tail = value

    def _emit(self, target, text: str, ph: dict, ruby: dict,
              rt_map: dict, ruby_built: list) -> None:
        """文本统一切分：保护 token → 原节点；注音 token → 原生 <ruby> 重建；普通文本并入。"""
        for part in ANY_TOKEN_SPLIT.split(text or ""):
            if not part:
                continue
            if part in ph:
                copy = deepcopy(ph[part])
                copy.tail = None
                target.append(copy)
                ruby_built.append(None)
            elif RUBY_TOKEN_FULL.fullmatch(part):
                entry = ruby.get(part)
                if entry is None:
                    self._append_text(target, part)
                    ruby_built.append(None)
                    continue
                rt = (rt_map or {}).get(part) or entry["rt"]
                trailing = self._trailing_text(target)
                before, base = split_anchor(trailing)
                if base is None or base == trailing.strip() == "":
                    self._append_text(target, f"（{rt}）")  # 无法锚定 → 括号降级
                    ruby_built.append("fallback")
                else:
                    self._set_trailing_text(target, before)
                    ruby_el = lhtml.Element("ruby")
                    ruby_el.text = base
                    rt_el = lhtml.Element("rt")
                    rt_el.text = rt
                    ruby_el.append(rt_el)
                    target.append(ruby_el)
                    ruby_built.append("native")
            else:
                self._append_text(target, part)
                ruby_built.append(None)

    def _walk(self, src, target, ph: dict, ruby: dict, rt_map: dict,
              ruby_built: list) -> None:
        self._emit(target, src.text, ph, ruby, rt_map, ruby_built)
        for child in src:
            tag = child.tag if isinstance(child.tag, str) else None
            if tag and (tag in _KEEP_INLINE or tag in ("code", "img", "br")):
                new_el = lhtml.Element(tag)
                for k, v in child.attrib.items():
                    new_el.set(k, v)
                self._emit(new_el, child.text, ph, ruby, rt_map, ruby_built)
                for sub in child:
                    if isinstance(sub.tag, str):
                        new_el.append(deepcopy(sub))
                target.append(new_el)
            elif tag:
                self._walk(child, target, ph, ruby, rt_map, ruby_built)
            self._emit(target, child.tail, ph, ruby, rt_map, ruby_built)

    def _set_block_content(self, el, new_html: str, ph: dict, ruby: dict,
                           rt_map: dict) -> list:
        try:
            wrapper = lhtml.fragment_fromstring(f"<div>{new_html}</div>")
        except Exception:
            return []
        attribs, tail = dict(el.attrib), el.tail
        el.clear()
        el.attrib.update(attribs)
        el.tail = tail
        ruby_built: list = []
        self._walk(wrapper, el, ph, ruby, rt_map, ruby_built)
        return ruby_built

    def render(self, out_path: Path, model: DocumentModel,
               translations: dict[int, str], mode: str = "target",
               ruby_maps: dict[int, dict[str, str]] | None = None) -> None:
        ruby_maps = ruby_maps or {}
        skel = model.skeleton
        doc = skel["doc"]
        block_els = skel["block_els"]
        table_rows: list[tuple[str, str]] = []
        done_els: set[int] = set()

        # 属性段先回填：块重建会深拷贝受保护节点，需先带上已译属性；
        # bi_table 模式丢弃原文档 → 属性段改为成对入表（审查第1轮修复）
        attr_i = 0
        for b in model.blocks:
            if b.meta.get("kind") == "attr":
                el, attr = skel["attr_refs"][attr_i]
                if mode == "bi_table":
                    table_rows.append((b.text, translations.get(b.seq, b.text)))
                else:
                    el.set(attr, translations.get(b.seq, b.text))
                attr_i += 1

        for b in model.blocks:
            kind = b.meta.get("kind")
            if kind != "block" or b.meta.get("part", 0) != 0:
                continue
            idx = b.meta["el_idx"]
            if idx in done_els:
                continue
            done_els.add(idx)
            el = block_els[idx]
            rt_map = {}
            for gb in self._group(model, b):
                rt_map.update(ruby_maps.get(gb.seq) or {})
            ruby_entries = {e["token"]: e for e in (b.meta.get("ruby") or [])}
            src_final, tgt_final = self._group_final(model, b, translations)
            if mode == "bi_table":
                from adapters.ruby import restore_ruby
                table_rows.append((restore_ruby(src_final, b.meta.get("ruby")),
                                   restore_ruby(tgt_final, b.meta.get("ruby"), rt_map)))
                continue
            self._set_block_content(el, tgt_final, b.meta.get("ph") or {},
                                    ruby_entries, rt_map)
            if mode == "bi_inter":
                from adapters.ruby import restore_ruby
                # 表格单元格内不能插入 <p>（非法 HTML 会被浏览器挤出表格）→ 用 <td>
                parent = el.getparent()
                in_row = parent is not None and isinstance(parent.tag, str) and parent.tag == "tr"
                src_p = lhtml.Element("td" if in_row else "p")
                src_p.set("class", "bilingual-src")
                src_p.text = restore_ruby(src_final, b.meta.get("ruby"))
                el.addnext(src_p)

        if mode == "bi_table":
            out_doc = lhtml.document_fromstring("<html><body></body></html>")
            body = out_doc.find("body")
            table = lhtml.Element("table")
            table.set("border", "1")
            head = lhtml.Element("tr")
            for name in ("原文", "译文"):
                th = lhtml.Element("th")
                th.text = name
                head.append(th)
            table.append(head)
            for s_, t in table_rows:
                tr = lhtml.Element("tr")
                for v in (s_, t):
                    td = lhtml.Element("td")
                    td.text = v
                    tr.append(td)
                table.append(tr)
            body.append(table)
            doc = out_doc

        text = lhtml.tostring(doc, encoding="unicode")
        if skel.get("doctype") and mode != "bi_table":
            text = "<!DOCTYPE html>\n" + text
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
