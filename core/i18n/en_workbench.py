"""英语词条：项目工作台 / 翻译进度 / 术语编辑弹窗。

覆盖：
- `app/pages/workbench_page.py`（文件列表、标签编辑、术语表、开始翻译预检、术语归纳）
- `app/pages/progress_page.py`（翻译进度）
- `app/pages/terms_dialog.py`（术语审核弹窗）

格式：`(pattern, replacement)`，按 `re.sub` 语义替换；整串匹配用 `^…$` 锚定。
先匹配先赢 —— 具体规则写在前面，兜底的正则写在后面。
替换串里用 `\\1`、`\\2` 引用捕获组，不要写中文。

只给「整串」写规则：`tr()` 拿到的是控件实际显示的完整文案，f-string 的**字面片段**
（`f"导入 {n} 个文件"` 拆出来的「导入 」）不会单独显示，扫描器也把片段排除了
（`tests/unit/test_i18n_coverage.py::_joined_children`），所以片段既不需要规则也不需要
白名单 —— 整串的覆盖由「把插值位采样成 1」的样本文案规则负责。

两处易错点：
- 源码里先拼接再显示的文案（`f"…" + "…"`、`"\n".join(lines)`、`"；".join(bits)`），
  `tr()` 看到的是拼接后的整串，所以要按**运行时整串**的形态额外写规则，
  否则只能翻到前半截（见「导入」与「术语改动影响提示」两段）。
- 替换串里的换行必须写真正的换行（源码里写 `\n`）：写成 `\\n` 会被 `re.sub` 当成
  非法转义（`error: bad escape \n`），显示时直接抛异常。模式串里相反，换行写 `\\n`。
"""
from __future__ import annotations

#: 确实不该翻译的字面量：本文件覆盖的三个页面里没有这类字符串 —— 文件对话框过滤器、
#: 页面键（`翻译进度` / `校对编辑器`）都要显示给用户，所以都写了词条。
ALLOWED: list[tuple[str, str]] = []

RULES: list[tuple[str, str]] = [
    # ===================== 文件标签弹窗 TagsDialog =====================
    ("^编辑文件标签$", "Edit file tags"),
    ("^继承项目设置$", "Inherit project settings"),
    ("^drop（丢弃注音）$", "drop (discard ruby)"),
    ("^keep（保留原假名）$", "keep (keep original kana)"),
    ("^translate（注音也翻译）$", "translate (translate ruby as well)"),
    ("^作品类型（如：奇幻小说）$", "Genre (e.g. fantasy novel)"),
    ("^语言风格（如：网文口语）$", "Language style (e.g. colloquial web-novel)"),
    ("^格式注意事项$", "Formatting notes"),
    ("^补充翻译指令$", "Additional translation instructions"),
    ("^振假名策略（文件级覆盖）$", "Ruby policy (file-level override)"),

    # ===================== 开始翻译预检 PrecheckDialog =====================
    ("^开始翻译 \u00b7 预检$", "Start translation \u00b7 Precheck"),
    ("^本次范围：选中的 (\\d+) 个文件$", "Scope: \\1 selected file(s)"),
    ("^本次范围：全部 (\\d+) 个文件$", "Scope: all \\1 file(s)"),
    ("^待翻译段落：(\\d+) 段（(\\d+) 字符）$", "Pending segments: \\1 (\\2 characters)"),
    ("^术语表：(\\d+) 条（有效）$", "Glossary: \\1 term(s) (active)"),
    ("^配置过期译文：(\\d+) 段（默认保留，可稍后在项目设置中重译）$",
     "Stale translations: \\1 (kept; retranslate later in project settings)"),
    ("^上下文滑窗：前 (\\d+) \\+ 后 (\\d+) 段 \\| 振假名策略：(.+)$",
     "Context window: \\1 before + \\2 after | Ruby policy: \\3"),
    ("^token 预估：输入约 (\\d+)~(\\d+)，输出约 (\\d+)$",
     "Token estimate: about \\1~\\2 in, about \\3 out"),
    ("^该范围内没有待翻译段落。$", "No pending segments in this range."),
    ("^\u26a0 检测到术语表被外部修改，已自动重新加载（本次翻译按新术语表执行）。$",
     "\u26a0 The glossary changed externally and was reloaded automatically "
     "(this run uses the new glossary)."),
    ("^先自动归纳术语（(\\d+) 个新文件，阶段一本地免费，阶段二调用 AI）$",
     "Induce terms automatically first (\\1 new file(s); stage 1 is local and free, "
     "stage 2 calls the AI)"),
    ("^重新归纳全部文件的术语（阶段一本地免费，阶段二调用 AI，可能产生新候选）$",
     "Re-induce terms for all files (stage 1 is local and free, stage 2 calls the AI; "
     "may produce new candidates)"),
    ("^确认开始$", "Confirm and start"),

    # ===================== 文件区按钮 / 文件表格 =====================
    ("^导入文件（复制到 source/）$", "Import files (copied into source/)"),
    ("^导入文件夹…$", "Import folder…"),
    ("^编辑标签$", "Edit tags"),
    ("^移除文件$", "Remove file"),
    ("^检查术语表外部修改$", "Check glossary for external changes"),
    ("^文件$", "File"),
    ("^格式$", "Format"),
    ("^可译段$", "Translatable segments"),
    ("^状态$", "Status"),
    ("^标签$", "Tags"),
    # 文件状态列（WorkbenchPage._doc_state）——状态词与 en_core 的用法保持一致
    ("^无内容$", "No content"),
    ("^未翻译 0/(\\d+)$", "Untranslated 0/\\1"),
    ("^翻译中 (\\d+)/(\\d+)$", "Translating \\1/\\2"),
    ("^已校对完成 (\\d+)/(\\d+)$", "Reviewed \\1/\\2"),
    ("^已翻译待校对 (\\d+)/(\\d+)$", "Translated, pending review \\1/\\2"),

    # ===================== 导入文件 / 导入文件夹 =====================
    ("^选择源文件$", "Select source files"),
    ("^支持的格式 \\(\\*\\.txt \\*\\.md \\*\\.markdown \\*\\.html \\*\\.htm \\*\\.docx "
     "\\*\\.epub \\*\\.srt \\*\\.png \\*\\.jpg \\*\\.jpeg \\*\\.bmp \\*\\.webp\\);;"
     "全部文件 \\(\\*\\)$",
     "Supported formats (*.txt *.md *.markdown *.html *.htm *.docx *.epub *.srt "
     "*.png *.jpg *.jpeg *.bmp *.webp);;All files (*)"),
    ("^选择包含源文件的文件夹$", "Select the folder containing source files"),
    ("^导入文件夹$", "Import folder"),
    ("^该文件夹内没有支持的文件格式$", "No supported file formats in this folder"),
    ("^在 (.+) 中找到 (\\d+) 个支持文件。\\n确定全部导入？$",
     "Found \\2 supported file(s) in \\1.\nImport them all?"),
    ("^导入 (\\d+) 个文件$", "Imported \\1 file(s)"),
    ("^导入结果$", "Import result"),
    # `msg` 是先拼接再显示的整串（f-string + 片段），按运行时整串补规则
    ("^导入 (\\d+) 个文件\\n失败：\\n", "Imported \\1 file(s)\nFailed:\n"),
    ("^导入 (\\d+) 个文件（(\\d+) 个失败）$", "Imported \\1 file(s) (\\2 failed)"),
    ("^\\n失败：\\n$", "\nFailed:\n"),
    # `f"（{len(errs)} 个失败）"`：en_core 的 `^（([^（）]{1,20})）$` 排在前面，会先把
    # 短括号转成半角，所以半角形态也要有一条；全角形态保留，以防那条规则以后收窄。
    ("^（(\\d+) 个失败）$", "(\\1 failed)"),
    ("^\\((\\d+) 个失败\\)$", "(\\1 failed)"),

    # ===================== 移除文件 / 术语表 =====================
    ("^从项目中移除该文件（source/ 内文件保留）？$",
     "Remove this file from the project (files in source/ are kept)?"),
    ("^术语表（glossary\\.\\*，可直接在 Excel 编辑）$",
     "Glossary (glossary.*, editable directly in Excel)"),
    ("^新增术语$", "Add term"),
    ("^编辑$", "Edit"),
    ("^删除$", "Delete"),
    ("^合并导入…$", "Merge import…"),
    # 术语表首列（源码写的是「源语言」，实际存的是源词，见 WorkbenchPage._refresh_gloss）
    ("^源语言$", "Source term"),
    ("^目标语（\\| 分隔，≤3）$", "Candidates (separated by |, ≤3)"),
    ("^注释$", "Note"),
    ("^开始翻译（全部文件）$", "Translate all files"),
    ("^翻译选中文件$", "Translate selected file"),
    ("^请先在上方文件列表中点选一个文件。$", "Select a file in the list above first."),
    # 术语编辑弹窗（WorkbenchPage._edit_term 里临时建的 QDialog）
    ("^术语$", "Term"),
    ("^源词$", "Source term"),
    ("^候选（\\| 分隔，≤3）$", "Candidates (separated by |, ≤3)"),
    ("^保存$", "Save"),
    ("^保存失败$", "Save failed"),
    ("^合并术语表$", "Merge glossary"),
    ("^合并 (\\d+) 条新术语$", "Merged \\1 new term(s)"),

    # ============== 术语改动影响提示（WorkbenchPage._offer_term_fixup）==============
    ("^本次共 (\\d+) 个术语有变动；到校对页用「术语变更影响…」可查看需要订正的段落$",
     "\\1 term(s) changed; use \"Term change impact…\" on the review page to see the "
     "segments that need fixing"),
    ("^合并了 (\\d+) 条术语$", "Merged \\1 term(s)"),
    ("^术语「(.+)」已保存$", "Term \"\\1\" saved"),
    ("^(\\d+) 段还在用旧译名（可一键替换）$",
     "\\1 segment(s) still use the old term (replaceable in one click)"),
    ("^(\\d+) 段还没用上新术语（需订正或重译）$",
     "\\1 segment(s) do not use the new term yet (fix or retranslate)"),
    # `{what}：现有译文里没有需要订正的段落`：what 运行时只有两种形态，先具体后通配
    ("^合并了 (\\d+) 条术语：现有译文里没有需要订正的段落$",
     "Merged \\1 term(s): no existing translations need fixing"),
    ("^术语「(.+)」已保存：现有译文里没有需要订正的段落$",
     "Term \"\\1\" saved: no existing translations need fixing"),
    ("^(.+)：现有译文里没有需要订正的段落$", "\\1: no existing translations need fixing"),
    # 提示框正文：box.setText(f"{what}。\n现有译文里有 {n} 段需要核对：" + "；".join(bits) + "。")
    # 先拼接后显示，tr() 看到的是整串 —— 按 (2 种 what × 3 种 bits 组合) 逐一写规则，
    # 最后一条兜住 AST 采样出的形态（what → 1、bits 为空）。
    ("^合并了 (\\d+) 条术语。\\n现有译文里有 (\\d+) 段需要核对："
     "(\\d+) 段还在用旧译名（可一键替换）；(\\d+) 段还没用上新术语（需订正或重译）。$",
     "Merged \\1 term(s).\n\\2 segment(s) need review: \\3 still use the old term "
     "(one-click replace); \\4 do not use the new term yet (fix or retranslate)."),
    ("^合并了 (\\d+) 条术语。\\n现有译文里有 (\\d+) 段需要核对："
     "(\\d+) 段还在用旧译名（可一键替换）。$",
     "Merged \\1 term(s).\n\\2 segment(s) need review: \\3 still use the old term "
     "(one-click replace)."),
    ("^合并了 (\\d+) 条术语。\\n现有译文里有 (\\d+) 段需要核对："
     "(\\d+) 段还没用上新术语（需订正或重译）。$",
     "Merged \\1 term(s).\n\\2 segment(s) need review: \\3 do not use the new term yet "
     "(fix or retranslate)."),
    ("^术语「(.+)」已保存。\\n现有译文里有 (\\d+) 段需要核对："
     "(\\d+) 段还在用旧译名（可一键替换）；(\\d+) 段还没用上新术语（需订正或重译）。$",
     "Term \"\\1\" saved.\n\\2 segment(s) need review: \\3 still use the old term "
     "(one-click replace); \\4 do not use the new term yet (fix or retranslate)."),
    ("^术语「(.+)」已保存。\\n现有译文里有 (\\d+) 段需要核对："
     "(\\d+) 段还在用旧译名（可一键替换）。$",
     "Term \"\\1\" saved.\n\\2 segment(s) need review: \\3 still use the old term "
     "(one-click replace)."),
    ("^术语「(.+)」已保存。\\n现有译文里有 (\\d+) 段需要核对："
     "(\\d+) 段还没用上新术语（需订正或重译）。$",
     "Term \"\\1\" saved.\n\\2 segment(s) need review: \\3 do not use the new term yet "
     "(fix or retranslate)."),
    ("^(.+)。\\n现有译文里有 (\\d+) 段需要核对：$", "\\1.\n\\2 segment(s) need review:"),
    ("^。$", "."),
    ("^；$", ";"),
    ("^术语已更新$", "Terms updated"),
    ("^要现在去校对页处理吗？（选中这些段落时，编辑区下方也会直接提示）$",
     "Go to the review page now? (When you select these segments, a hint also appears "
     "below the editor.)"),
    ("^去校对页处理$", "Go to the review page"),
    ("^稍后$", "Later"),

    # ===================== 术语表外部修改 =====================
    ("^已重新加载外部修改的术语表（可在校对页用「术语变更影响…」订正旧译文）$",
     "Reloaded the externally modified glossary (use \"Term change impact…\" on the "
     "review page to fix old translations)"),
    ("^已重新加载术语表（内容无变化）$", "Reloaded the glossary (no changes)"),
    ("^术语表无外部修改$", "No external glossary changes"),

    # ===================== 开始翻译 / 术语归纳 =====================
    ("^术语归纳已完成（所选范围内没有待翻译段落）$",
     "Term induction finished (no pending segments in the selected range)"),
    ("^无法启动$", "Cannot start"),
    ("^无法归纳术语$", "Cannot induce terms"),
    ("^(.+)\\n\\n请先在「设置」页配置该 Provider 的 API 密钥。$",
     "\\1\n\nConfigure this provider's API key on the Settings page first."),
    ("^阶段一：本地统计候选词（免费）…$", "Stage 1: counting candidates locally (free)…"),
    ("^共 (\\d+) 个候选词，开始分批送 AI 判定…$",
     "\\1 candidate(s) in total; sending them to the AI in batches…"),
    ("^AI 判定中：(\\d+)/(\\d+) 个候选词（可点「取消」中止）$",
     "AI judging: \\1/\\2 candidates (click \"Cancel\" to stop)"),
    ("^术语归纳$", "Term induction"),
    ("^取消$", "Cancel"),
    ("^术语归纳失败$", "Term induction failed"),
    ("^术语归纳已取消$", "Term induction cancelled"),
    ("^已取消本次归纳，未生成术语表。\\n之后可再次勾选「先自动归纳术语」重新归纳。$",
     "Induction cancelled; no glossary was generated.\n"
     "Tick \"Induce terms automatically first\" to run it again later."),
    ("^术语归纳暂停$", "Term induction paused"),
    ("^(.+)\\n请修复后重试。$", "\\1\nFix it and try again."),
    # 暂停时 error 为空串的兜底（此时整串以换行开头，上面那条 `(.+)` 规则匹配不到）
    ("^\\n请修复后重试。$", "\nFix it and try again."),
    ("^归纳完成，未发现新的术语候选$", "Induction finished; no new term candidates found"),

    # ===================== 页面键（侧栏导航也用同一份文案）=====================
    ("^翻译进度$", "Translation progress"),
    ("^校对编辑器$", "Review editor"),

    # ===================== 翻译进度页 progress_page.py =====================
    ("^尚未开始翻译。$", "Translation has not started yet."),
    ("^取消（未开始批次保留，可续跑）$",
     "Cancel (unstarted batches are kept; the run can be resumed)"),
    ("^完成 (\\d+)（含翻译记忆 (\\d+)）\u00b7 失败 (\\d+) \u00b7 共 (\\d+) 段$",
     "Done \\1 (incl. \\2 from translation memory) \u00b7 failed \\3 \u00b7 "
     "\\4 segment(s) in total"),
    ("^已暂停：(.*)\\n修复配置后可从断点继续。$",
     "Paused: \\1\nFix the configuration to resume from the checkpoint."),
    ("^已取消。未开始的段落保持待翻译，可随时续跑。$",
     "Cancelled. Segments that had not started stay pending and can be resumed at "
     "any time."),
    ("^完成：(\\d+) 段（TM 复用 (\\d+)），失败 (\\d+)。前往校对编辑器检查。$",
     "Done: \\1 segment(s) (TM reuse \\2), \\3 failed. Check them in the review editor."),

    # ===================== 术语审核弹窗 terms_dialog.py =====================
    ("^术语审核（(\\d+) 条候选）$", "Term review (\\1 candidate(s))"),
    ("^勾选=采纳入库（写入 glossary 并在翻译时强制一致）；双击单元格可修改；"
     "未勾选的候选将被拒绝。$",
     "Checked = accepted into the glossary (written to glossary and enforced during "
     "translation); double-click a cell to edit it; unchecked candidates are rejected."),
    ("^采纳$", "Accept"),
    ("^候选（\\| 分隔）$", "Candidates (separated by |)"),
    ("^频次$", "Count"),
    ("^全选$", "Select all"),
    ("^全不选$", "Select none"),
    ("^确认入库$", "Confirm and save"),
    ("^全部拒绝$", "Reject all"),
    ("^修改$", "Edit"),
    ("^已入库 (\\d+) 条，拒绝 (\\d+) 条$", "Saved \\1 term(s), rejected \\2"),

    # ===================== 兜底：错误清单的「文件名：错误」=====================
    # 导入失败清单是 f"{e['file']}：{e['error']}"（全角冒号）。只有两侧都不含中日韩/全角
    # 字符时才转换 —— 已经含中文的文案（用户原文/译文、其它词条负责的句子）不会命中，
    # 所以不会改坏任何中文内容。
    ("^([^\\u3000-\\u303f\\u3040-\\u30ff\\u3400-\\u9fff\\uf900-\\ufaff\\uff00-\\uffef]+)："
     "([^\\u3000-\\u303f\\u3040-\\u30ff\\u3400-\\u9fff\\uf900-\\ufaff\\uff00-\\uffef]+)$",
     "\\1: \\2"),
]
