r"""英语词条：校对编辑器（`app/pages/review_page.py`）。

用词沿用项目里的统一说法：project 项目 / segment 段落·段 / source 源文 /
translation 译文 / review 校对 / term 术语 / glossary 术语表 / API key 密钥 /
workbench 工作台 / progress 进度 / settings 设置 / export 导出 / import 导入 /
retranslate 重新翻译 / tag 标签 / status 状态。
状态名复用 `en_core` 的译法：Untranslated / MT / Edited / Confirmed / Failed /
Translated / Translating / Pending / Skipped / Not started / Done / Reviewed / Ready。

规则写法：`(pattern, replacement)`，按 `re.sub` 语义替换；整串匹配用 `^…$` 锚定，
动态部分按 f-string 采样后的样子写（插值位是 `1`），用 `(\d+)` / `(.+)` 捕获并 `\1` 回填。
`tr()` 把规则按 `re.MULTILINE` 编译、最多重扫 4 轮，所以弹窗正文能按行命中；
但**一轮只应用一条规则**：重叠的规则必须把更具体的那条写在前面（先匹配先赢）——
例如 `^已保存 (\d+) 条译注音$` 要排在 `^已保存 (.+)$` 前面，抢跑了就会留下中文。

覆盖范围：段表表头与筛选、上下编辑区（含源图标签页）、段落内术语提示、确认流程、
搜索替换对话框、导出对话框（格式/模式/同名文件）、术语变更影响弹窗、术语一致性报表、
注音编辑、导出进度与结果提示。**不含**导出报表的实际文件名，见 `ALLOWED`。

另注：`en_core.py:165/171` 有两条 `^（([^（）]{1,20})）$` / `^(\w+)（([^（）]{1,20})）$`，
会把「X（短说明）」的全角括号先改成半角 `(…)`；它们排在本文件前面、先匹配先赢，
所以**只有整串字面量词条（进 exact dict，dict 查表在正则之前）才能抢在它们前面**。
带动态部分（`(\d+)`）或含 ASCII 括号的全角写法抢不到，本文件因此对这类文案写了两种形态：
全角形态 + en_core 改过之后的半角形态（一轮一条规则，两步接力也能翻干净）。
"""
from __future__ import annotations

RULES: list[tuple[str, str]] = [
    # --- 段表表头 / 顶栏 ---
    # （`^状态$`、`^源词$`、`^候选$` 在 en_core 里也有同义短名；这里保留一份，
    #   为了让本页词条自成一套，改文案时不必跨文件核对。）
    ("^段号$", "Segment"),
    ("^状态$", "Status"),
    ("^源文$", "Source"),
    ("^译文$", "Translation"),          # 段表第 4 列表头（`SegmentsModel.COLS`）
    ("^术语$", "Term"),
    ("^类型$", "Type"),
    ("^需要变成$", "Should become"),
    ("^文档$", "Document"),
    ("^译文片段$", "Translation snippet"),
    ("^源词$", "Source term"),
    ("^候选$", "Candidates"),
    ("^源文段数$", "Source segments"),
    ("^候选命中$", "Candidate hits"),
    ("^全部文档$", "All documents"),
    ("^搜索源文/译文…$", "Search source / translation…"),
    ("^搜索替换…$", "Search & replace…"),
    ("^刷新$", "Refresh"),
    ("^(\\d+) 段$", r"\1 segment(s)"),

    # --- 搜索替换对话框（ReplaceDialog）---
    ("^搜索替换（仅译文，预览后执行，可撤销）$",
     "Search & replace (translation only; preview before applying, undoable)"),
    ("^查找$", "Find"),
    ("^替换为$", "Replace with"),
    ("^区分大小写$", "Case sensitive"),
    ("^正则$", "Regex"),
    ("^预览$", "Preview"),
    ("^先输入查找内容并点击预览。$", "Type what to find first, then click Preview."),
    ("^执行替换$", "Apply replace"),
    ("^撤销上次$", "Undo last"),
    ("^关闭$", "Close"),
    ("^正则错误：(.+)$", r"Regex error: \1"),
    # `f"命中 {n} 段" + f"：\n{sample}"` 拼起来后第一行是「命中 N 段：」，
    # 下面两条接力：带冒号的那条负责拼好的整段，不带冒号的那条是本条 f-string 的采样形态。
    ("^命中 (\\d+) 段：$", r"\1 segment(s) matched:"),
    ("^命中 (\\d+) 段$", r"\1 segment(s) matched"),
    (r"^：\n(.+)$", r":\n\1"),
    ("^已替换 (\\d+) 段（可撤销）。$", r"Replaced \1 segment(s) (undoable)."),
    ("^已撤销：(.+)$", r"Undone: \1"),
    ("^没有可撤销的操作。$", "There is nothing to undo."),

    # --- 导出对话框（ExportDialog）---
    ("^导出翻译结果到 target/$", "Export translations to target/"),
    ("^跟随源文件（默认）$", "Follow the source file (default)"),
    (r"^纯文本 \(\.txt\)$", "Plain text (.txt)"),
    ("^覆盖已有译文（默认）$", "Overwrite the existing translation (default)"),
    (r"^保留两者（自动改名 xxx\(2\)\.md）$", "Keep both (rename to xxx(2).md automatically)"),
    (r"^保留两者 \(自动改名 xxx\(2\)\.md\)$", "Keep both (rename to xxx(2).md automatically)"),
    ("^跳过已存在的文件$", "Skip files that already exist"),
    ("^target（仅译文）$", "target (translation only)"),
    ("^bi_inter（段间交错双语）$", "bi_inter (interleaved bilingual)"),
    ("^bi_table（左右表格双语）$", "bi_table (side-by-side bilingual table)"),
    ("^强制导出（忽略源文件变更/未完成警告）$",
     "Force export (ignore source-file changes / unfinished warnings)"),
    ("^将导出 (\\d+) 个文档$", r"Will export \1 document(s)"),
    ("^导出格式：$", "Export format:"),
    ("^导出模式：$", "Export mode:"),
    ("^同名文件：$", "Same-name files:"),
    ("^⚠ target/ 中已有 (\\d+) 个同名文件，将按上面的设置处理。$",
     r"⚠ target/ already has \1 file(s) with the same name; the setting above is applied."),
    ("^target/ 中没有同名文件。$", "No files with the same name in target/."),
    ("^导出$", "Export"),
    ("^导出…$", "Export…"),

    # --- 术语变更影响（TermImpactDialog）---
    ("^术语变更影响$", "Term change impact"),
    ("^术语变更影响…$", "Term change impact…"),
    ("^全局替换为新候选$", "Replace all with the new candidate"),
    ("^标记重译$", "Mark for retranslation"),
    ("^撤销上次替换$", "Undo last replace"),
    ("^全局替换$", "Replace globally"),
    ("^撤销$", "Undo"),
    ("^旧译名仍在用$", "Old name still in use"),
    ("^新术语未体现$", "New term not reflected"),
    (r"^应含「(.+)」$", r'Should contain "\1"'),
    ("^（该术语已删除）$", "(this term was deleted)"),
    ("^（对比依据：上一份术语表快照）$", "(basis: the previous glossary snapshot)"),
    ("^（对比依据：上次在软件内保存的术语缓存—— 历史快照不足时的兜底）$",
     "(basis: the term cache last saved in this app — used when snapshots are insufficient)"),
    ("^共 (\\d+) 处译文需要订正（涉及 (\\d+) 个文档）：$",
     r"\1 translation(s) to correct across \2 document(s):"),
    (r"^· (\d+) 处仍在用\*\*旧译名\*\* —— 「全局替换」可换成新首选候选（可撤销）；$",
     r'· \1 occurrence(s) still use the **old name** — "Replace globally" switches them to '
     r"the new preferred candidate (undoable);"),
    (r"^· (\d+) 处\*\*没用上新术语\*\* —— 没有旧串可替换，请「标记重译」"
     r"（下次按新术语重新生成）或在校对页逐段改；$",
     r"· \1 occurrence(s) do **not use the new term** — there is no old string to replace, "
     r'so use "Mark for retranslation" (regenerated from the new terms next time) or fix '
     r"them one by one on the review page;"),
    ("^校对页里选中这类段落时，编辑区下方也会直接提示并可一键处理。$",
     "Selecting such a segment on the review page also shows a hint below the editor with "
     "one-click actions."),
    # 这一条要放在上面两条 `· …` 之后：它最宽，只兜「· 名称：说明」这种拼出来的单行。
    ("^· (.+?)：(.+)$", r"· \1: \2"),
    ("^已替换 (\\d+) 段（可用「撤销上次替换」回退）。$",
     r'Replaced \1 segment(s) — use "Undo last replace" to revert.'),
    ("^已把 (\\d+) 段置回「未翻译」。下次「开始翻译」时会用新术语重新生成，其余段落不受影响。$",
     r'Set \1 segment(s) back to "Untranslated". The next "Start translation" regenerates '
     r"them with the new terms; other segments are unaffected."),
    ("^已撤销 (\\d+) 段替换。$", r"Undid \1 segment replacement(s)."),
    ("^没有可撤销的替换。$", "There is no replacement to undo."),
    # 多行弹窗正文：整串一条规则（`\n` 是源码里的真实换行）。
    (r"^将把 (\d+) 处译文中的旧译名替换为新首选候选。\n"
     "「新术语未体现」的那些段没有旧串可替换，不会被改动"
     "（请用「标记重译」或逐段修改）。\n该操作可撤销。要继续吗？$",
     "Replace the old term name with the new preferred candidate in \\1 translation(s).\n"
     "Segments that do not reflect the new term have no old string to replace and are left "
     'unchanged (use "Mark for retranslation" or edit them one by one).\n'
     "This can be undone. Continue?"),
    ("^没有可对比的旧术语状态：本项目既没有术语快照、也没有术语缓存。"
     "在软件内增删改术语会自动留快照；在 Excel 里改过之后重新加载"
     "（工作台「检查术语表外部修改」）或重开项目也会补上快照，"
     "之后即可比出「哪些旧译文还在用旧译名 / 还没用上新术语」。$",
     "There is no earlier term state to compare against: this project has neither a glossary "
     "snapshot nor a term cache. Adding, changing or deleting terms inside the app records a "
     "snapshot automatically; after editing the glossary in Excel, reload it (Workbench → "
     '"Check for external glossary changes") or reopen the project to create the missing '
     "snapshot — after that you can tell which old translations still use the old term name "
     "and which have not picked up the new term yet."),
    (r"^没有检测到需要订正的段落：术语改过之后，旧译名已不再出现，"
     r"源文含新术语的段落也都用上了约定译名。(.*)$",
     r"No segments need correction: the old term name no longer appears after the term "
     r"change, and every segment whose source contains a new term already uses the agreed "
     r"rendering. \1"),

    # --- 编辑区：标题 / 原文可编辑 / 源图标签页 ---
    ("^选中段落后在下方编辑（表格仅用于选择段落）$",
     "Select a segment to edit it below (the table is only for selecting segments)"),
    ("^允许编辑原文$", "Allow editing the source"),
    (r"^导入文档的原文默认只读：导出时会重新解析源文件，改原文不会进入导出结果。\n"
     r"OCR 识别错误、或分段不对时勾选它即可修正原文；修正后该段会标 ⚑ 待复核，\n"
     r"要按新原文重译请再点「标记重译选中」。$",
     "The source text of imported documents is read-only by default: exports re-parse the "
     "source file, so edits here do not reach the export.\n"
     "Tick this to fix OCR mistakes or wrong segmentation; a corrected segment is flagged "
     "⚑ needs review,\n"
     'and use "Mark selected for retranslation" to translate it again from the new source.'),
    ("^只读$", "Read-only"),
    ("^可编辑（仅本软件内）$", "Editable (inside this app only)"),
    (r"^保存 \(Ctrl\+S\)$", "Save (Ctrl+S)"),
    ("^保存$", "Save"),
    ("^未保存…$", "Unsaved…"),
    # 顺序要紧：带「条译注音」的那条必须排在 `^已保存 (.+)$` 前面。
    ("^已保存 (\\d+) 条译注音$", r"Saved \1 translated ruby annotation(s)"),
    ("^已保存 (.+)$", r"Saved \1"),
    ("^已保存到当前段落$", "Saved to the current segment"),
    ("^没有需要保存的改动$", "There are no changes to save"),
    ("^源文（原文）$", "Source (original text)"),
    ("^译文（在此编辑，自动保存）$", "Translation (edit here, auto-saved)"),
    ("^在此编辑译文（自动保存）…$", "Edit the translation here (auto-saved)…"),
    ("^先在上方表格里选中一个段落…$", "Select a segment in the table above first…"),
    ("^源文译文$", "Source & translation"),
    ("^源图$", "Source image"),
    ("^（此段非图片来源，或图片文件不存在）$",
     "(this segment does not come from an image, or the image file is missing)"),
    ("^（此段非图片来源）$", "(this segment does not come from an image)"),
    ("^（源图缺失：(.+)）$", r"(source image missing: \1)"),
    ("^（无法加载图片：(.+)）$", r"(cannot load image: \1)"),
    (r"^\(源图缺失：(.+)\)$", r"(source image missing: \1)"),
    (r"^\(无法加载图片：(.+)\)$", r"(cannot load image: \1)"),
    ("^⚠ 原文改动只在本软件内生效（用于修正 OCR / 分段错误并按新原文重新送翻）；"
     "导出会重新解析源文件，仍以源文件为准。$",
     "⚠ Source edits only take effect inside this app (to fix OCR / segmentation errors and "
     "translate again from the new source); exports re-parse the source file, which stays "
     "authoritative."),
    ("^拖动调整编辑区高度（双击恢复默认比例）$",
     "Drag to resize the editor area (double-click to restore the default ratio)"),
    ("^当前：(.*) 第 (\\d+) 段（(.*)）\\u3000—\\u3000右侧可直接编辑译文，自动保存$",
     r"Current: \1 · segment \2 (\3) — edit the translation on the right; changes save "
     r"automatically"),
    ("^ ⚑待复核$", " ⚑Needs review"),

    # --- 底部工具栏 ---
    (r"^确认选中（Ctrl\+Enter）$", "Confirm selected (Ctrl+Enter)"),
    (r"^确认选中 \(Ctrl\+Enter\)$", "Confirm selected (Ctrl+Enter)"),
    ("^确认选中$", "Confirm selected"),
    ("^确认当前文档全部$", "Confirm all in the current document"),
    ("^确认全部文档$", "Confirm all documents"),
    ("^确认全部$", "Confirm all"),
    ("^标记重译选中$", "Mark selected for retranslation"),
    ("^术语一致性报表…$", "Term consistency report…"),
    ("^注音编辑…$", "Ruby annotations…"),

    # --- 段落内术语提示（term_bar）---
    ("^替换$", "Replace"),
    (r"^替换为「(.+)」$", r'Replace with "\1"'),
    ("^标记重译本段$", "Mark this segment for retranslation"),
    ("^本次忽略$", "Ignore this time"),
    ("^本段不再提示这个术语（只影响本次会话的提示，不改数据）$",
     "Do not show this term for this segment again (affects this session's hints only; no "
     "data is changed)"),
    ("^⚑ 术语：本段应把旧译名「(.+?)」改成「(.+?)」（术语「(.+?)」改过译名）。$",
     r'⚑ Term: this segment should change the old name "\1" to "\2" (term "\3" was '
     r"renamed)."),
    ("^⚑ 术语：本段含「(.+?)」，约定译作「(.+?)」，但译文里没有出现"
     "（新增/改过的术语，需要订正或重译）。$",
     r'⚑ Term: this segment contains "\1", agreed to be translated as "\2", but the '
     r"translation does not use it (a new or changed term — correct it or re-translate)."),
    (r"^\u3000另有 (\d+) 个术语待核对。$", r" \1 more term(s) to check."),
    ("^已把本段标记为待重译（下次「开始翻译」按新术语重新生成）$",
     'This segment is marked for retranslation (the next "Start translation" regenerates it '
     "from the new terms)"),
    ("^已把「(.+?)」替换为「(.+?)」（可用「搜索替换」批量处理同类段）$",
     r'Replaced "\1" with "\2" (use "Search & replace" to handle similar segments in bulk)'),

    # --- 确认流程 ---
    ("^先在表格里选中要确认的段落。$", "Select the segments to confirm in the table first."),
    ("^没有段落被确认$", "No segments were confirmed"),
    ("^已确认 (\\d+) 段$", r"Confirmed \1 segment(s)"),
    ("^没有可确认的段落$", "No segments can be confirmed"),
    ("^(\\d+) 段还没有译文$", r"\1 segment(s) have no translation yet"),
    ("^(\\d+) 段已经是「已确认」$", r'\1 segment(s) are already "Confirmed"'),
    ("^第 (\\d+) 段已确认$", r"Segment \1 confirmed"),
    (r"^第 (\d+) 段还没有译文，无法确认 —— 请在下方编辑区填写后再按 Ctrl\+Enter$",
     r"Segment \1 has no translation yet and cannot be confirmed — fill it in below, then "
     r"press Ctrl+Enter"),
    ("^第 (\\d+) 段已经是「已确认」$", r'Segment \1 is already "Confirmed"'),
    (r"^先在表格里选中一个段落，再按 Ctrl\+Enter$",
     "Select a segment in the table, then press Ctrl+Enter"),
    # `"、".join(...)` / `"；".join(...)` 的分隔符：单独一条，翻了英文里才不是顿号。
    ("^、$", ", "),
    ("^；$", "; "),
    ("^；(\\d+) 段没确认（(.+)）$", r"; \1 segment(s) not confirmed (\2)"),
    (r"^选中的 (\d+) 段都没能确认：(.+)。\n\n可确认的条件是「已有译文且还没有确认」：\n"
     "· 未译 / 失败的段落 —— 请先「开始翻译」，或直接在下方编辑区填写译文"
     "（填写后会自动变成「已修改」，再确认即可）；\n"
     "· 已经是「已确认」的段落不需要重复确认。$",
     "None of the \\1 selected segment(s) could be confirmed: \\2.\\n\\n"
     "A segment can be confirmed when it has a translation and is not confirmed yet:\\n"
     "· Untranslated / failed segments — run \"Start translation\" first, or type a "
     'translation in the editor below (it then becomes "Edited" and can be confirmed);\\n'
     '· Segments that are already "Confirmed" do not need to be confirmed again.'),
    (r'^「(.+?)」范围内没有可确认的段落。\n\n可确认 = 已有译文且还没确认；'
     r"未译 / 失败的段落请先「开始翻译」。$",
     'No confirmable segments in "\\1".\\n\\n'
     'Confirmable = has a translation and is not confirmed yet; for untranslated / failed '
     'segments, run "Start translation" first.'),
    (r'^将把「(.+?)」范围内 (\d+) 段标记为「已确认」。\n要继续吗？$',
     'Mark \\2 segment(s) in "\\1" as "Confirmed".\\nContinue?'),

    # --- 注音编辑 / 术语一致性报表 ---
    ("^注音编辑（(\\d+) 个槽位）$", r"Ruby annotations (\1 slots)"),
    (r"^注音编辑 \((\d+) 个槽位\)$", r"Ruby annotations (\1 slots)"),
    ("^该段无振假名注音槽$", "This segment has no ruby annotation slots"),
    ("^请先选中一个段落$", "Select a segment first"),
    ("^译注音（原文注音：(.+?)，基词：(.+?)）$",
     r"Target ruby (source ruby: \1, base word: \2)"),
    (r"^译注音 \(原文注音：(.+?)，基词：(.+?)\)$",
     r"Target ruby (source ruby: \1, base word: \2)"),
    ("^术语一致性报表（(\\d+) 条未命中）$", r"Term consistency report (\1 missed)"),
    (r"^术语一致性报表 \((\d+) 条未命中\)$", r"Term consistency report (\1 missed)"),
    ("^✓ 一致$", "✓ Consistent"),
    ("^✗ 未命中$", "✗ Missed"),
    ("^导出 CSV（target/）$", "Export CSV (target/)"),
    (r"^已导出 target/术语一致性报表\.csv$",
     "Exported target/term-consistency-report.csv"),

    # --- 导出进度与结果 ---
    ("^准备导出…$", "Preparing export…"),
    ("^取消$", "Cancel"),
    ("^导出翻译结果$", "Export translations"),
    ("^正在导出 (\\d+)/(\\d+)：(.+)$", r"Exporting \1/\2: \3"),
    ("^已完成 (\\d+)/(\\d+)$", r"Finished \1/\2"),
    ("^已取消导出：未处理的文档保持原状。$",
     "Export cancelled: documents that were not processed were left unchanged."),
    ("^成功导出 (\\d+) 个文件到 target/（共 (\\d+) 个文档）$",
     r"Exported \1 file(s) to target/ (\2 document(s))"),
    ("^其中 (\\d+) 个覆盖了同名旧文件。$", r"\1 of them overwrote an existing file."),
    ("^跳过 (\\d+) 个已存在的文件（未覆盖）。$",
     r"Skipped \1 existing file(s) (not overwritten)."),
    ("^已导出 (\\d+) 个文件到 target/$", r"Exported \1 file(s) to target/"),
    ("^失败：$", "Failed:"),
    ("^提示：$", "Notes:"),
    ("^导出失败$", "Export failed"),
    ("^导出完成（有失败）$", "Export finished (with failures)"),
    ("^导出完成（有提示）$", "Export finished (with notes)"),
    ("^导出完成$", "Export finished"),
]


#: 确实不该翻的字符串 + 理由（覆盖率测试会要求它们仍然存在）。
ALLOWED: list[tuple[str, str]] = [
    ("术语一致性报表.csv",
     "`export_report_csv(...)` 要落盘的**实际文件名**（review_page.py:1370）：翻了会改变"
     "写出的文件名，与用户手里的文件对不上；显示这条文件名的那句提示已单独给词条"),
]
