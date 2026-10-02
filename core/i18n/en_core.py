r"""英语词条：core/ 与 llm/ 里会显示给用户的文案（状态名、错误提示等）。

格式：`(pattern, replacement)`，按 `re.sub` 语义替换；整串匹配用 `^…$` 锚定。
先匹配先赢 —— 具体规则写在前面，兜底的正则写在后面。
替换串里用 `\1`、`\2` 引用捕获组，不要写中文。

含正则转义的 pattern 一律用 `r"..."` 写（`\d`/`\w`/`\(` 在普通字符串里是
无效转义，会触发 `SyntaxWarning`；`r` 前缀让源码里看到的就是正则本身）。

本模块在 `CATALOG_MODULES` 里**排最前面**，所以规则一律用「足够长的字面前缀 +
整串锚点」：既能命中真实文案，又不会抢掉后面 `en_settings` / `en_workbench` /
`en_review` 的词条。凡是只有一两个汉字宽的宽泛规则（`^失败$` 之类）都不在这里加。
同理，界面上另有专用词条的短词（`源语言`、`注释`、`状态` 等）也不在这里下规则，
免得语义被抢（`源语言` 在术语表里是 "Source language"，在一致性报表里是
"Source term"，本模块先匹配会把它钉死成后者）。

覆盖范围：`core/`、`llm/`、`adapters/`、`storage/` 里经 `tr()` 显示的文案 ——
异常消息（`QMessageBox` 用 `str(e)` 显示）、状态/进度文案、轻提示、文件校验提示、
导入导出结果提示。**不含**发给模型的提示词与文件格式/内部标记，见 `ALLOWED`。
"""
from __future__ import annotations

RULES: list[tuple[str, str]] = [
    # --- 段落状态名（校对表格 / 筛选 / 状态栏共用） ---
    ("^未译$", "Untranslated"),
    ("^待译$", "Untranslated"),
    ("^机翻$", "MT"),
    ("^已修改$", "Edited"),
    ("^已确认$", "Confirmed"),
    ("^失败$", "Failed"),
    ("^已翻译$", "Translated"),
    ("^翻译中$", "Translating"),
    ("^待翻译$", "Pending"),
    ("^已跳过$", "Skipped"),
    ("^未开始$", "Not started"),
    ("^已完成$", "Done"),
    ("^校对完成$", "Reviewed"),
    ("^已校对完成$", "Review complete"),
    ("^就绪$", "Ready"),

    # --- 翻译风格预设（core/styles.py，显示在项目设置的风格下拉框里） ---
    # 注意：同文件的 _PROMPTS（发给模型的风格提示词）不翻，见 ALLOWED。
    ("^正式书面语$", "Formal written"),
    ("^轻松口语$", "Casual spoken"),
    ("^文学意译$", "Literary"),
    ("^学术严谨$", "Academic"),
    ("^自定义$", "Custom"),

    # --- 服务商显示名（core/appconfig.py，设置页服务商列表） ---
    ("^Ollama（本地）$", "Ollama (local)"),
    ("^LM Studio（本地）$", "LM Studio (local)"),
    (r"^llama\.cpp server（本地）$", "llama.cpp server (local)"),
    ("^vLLM（本地）$", "vLLM (local)"),
    ("^智谱 GLM$", "Zhipu GLM"),

    # --- 语言下拉名（core/langs.py，`代码（名称）`） ---
    # 只在整串（`英语-美国`）时替换。`core.langs.label()` 拼出来的
    # `代码（中文名）` 是另一种形态：`代码` 是 ASCII、`中文名` 是上面这些表项，
    # 整串长得像 `zh-CN（中文-中国（简体））`，本模块不为其写整形规则
    # （宽规则会抢后面词条，见文件头说明）。
    ("^中文-中国（简体）$", "Chinese - China (Simplified)"),
    ("^中文-台湾（繁体）$", "Chinese - Taiwan (Traditional)"),
    ("^中文-香港（繁体）$", "Chinese - Hong Kong (Traditional)"),
    ("^英语-美国$", "English - United States"),
    ("^英语-英国$", "English - United Kingdom"),
    ("^日语-日本$", "Japanese - Japan"),
    ("^韩语-韩国$", "Korean - Korea"),
    ("^法语-法国$", "French - France"),
    ("^德语-德国$", "German - Germany"),
    ("^西班牙语-西班牙$", "Spanish - Spain"),
    ("^葡萄牙语-巴西$", "Portuguese - Brazil"),
    ("^意大利语-意大利$", "Italian - Italy"),
    ("^俄语-俄罗斯$", "Russian - Russia"),
    ("^乌克兰语-乌克兰$", "Ukrainian - Ukraine"),
    ("^阿拉伯语-沙特阿拉伯$", "Arabic - Saudi Arabia"),
    ("^泰语-泰国$", "Thai - Thailand"),
    ("^越南语-越南$", "Vietnamese - Vietnam"),
    ("^印尼语-印度尼西亚$", "Indonesian - Indonesia"),
    ("^土耳其语-土耳其$", "Turkish - Turkiye"),
    ("^波兰语-波兰$", "Polish - Poland"),
    ("^荷兰语-荷兰$", "Dutch - Netherlands"),

    # --- 适配器 / 文件校验（adapters/） ---
    # `不支持的文件格式：{后缀}`：无后缀时后缀部分是「(无扩展名)」，故整串一起给。
    (r"^不支持的文件格式：\(无扩展名\)，请先转为 txt / md / html / docx / epub / srt$",
     "Unsupported file format: (no extension). Please convert it to txt / md / html / "
     "docx / epub / srt first"),
    ("^不支持的文件格式：(.+)，请先转为 txt / md / html / docx / epub / srt$",
     r"Unsupported file format: \1. Please convert it to txt / md / html / docx / "
     r"epub / srt first"),
    ("^未实现的格式：(.+)$", r"Format not implemented: \1"),
    ("^疑似二进制文件：(.+)$", r"Looks like a binary file: \1"),
    ("^无法识别文件编码：(.+)$", r"Unrecognized file encoding: \1"),
    (r"^container\.xml 未声明 rootfile$", "container.xml does not declare a rootfile"),
    ("^不是有效的 EPUB（zip 打不开）：(.+)$", r"Not a valid EPUB (cannot open the zip): \1"),
    ("^EPUB 内没有找到可翻译的 XHTML 内容文档$",
     "No translatable XHTML content documents found in the EPUB"),
    ("^EPUB 的内容文档都无法解析，无法导入$",
     "None of the EPUB content documents could be parsed; cannot import"),
    ("^epub 仅支持 target / bi_inter 模式$", "epub only supports target / bi_inter mode"),
    ("^txt 仅支持 target / bi_inter 模式$", "txt only supports target / bi_inter mode"),
    ("^srt 仅支持 target / bi_inter 模式$", "srt only supports target / bi_inter mode"),
    ("^图片导出仅支持 target / bi_inter 模式$",
     "Image export only supports target / bi_inter mode"),
    ("^未知导出模式：(.+)$", r"Unknown export mode: \1"),
    ("^docx 不支持导出模式：(.+)$", r"docx does not support export mode: \1"),
    ("^Windows OCR 无可用语言引擎（请在系统设置中安装语言包）$",
     "Windows OCR has no available language engine (install a language pack in "
     "Windows Settings)"),
    ("^无可用 OCR 引擎。请安装：pip install rapidocr-onnxruntime（推荐）"
     "或 pip install winsdk$",
     "No OCR engine available. Please install: pip install rapidocr-onnxruntime "
     "(recommended) or pip install winsdk"),
    ("^OCR 失败：(.+)$", r"OCR failed: \1"),
    ("^OCR 失败（RapidOCR：(.+)；winsdk 兜底亦失败）$",
     r"OCR failed (RapidOCR: \1; the winsdk fallback also failed)"),

    # --- 项目 / 术语表 / 存储（core/、storage/） ---
    ("^目录中已存在项目，请直接打开$", "A project already exists in this folder; open it instead"),
    ("^不是有效的项目目录（缺少 project.json）：(.+)$",
     r"Not a valid project folder (project.json is missing): \1"),
    (r"^该项目已被其他实例打开（\.aitrans/lock）$",
     "This project is already open in another instance (.aitrans/lock)"),
    ("^非法配置项：(.+)$", r"Invalid configuration key: \1"),
    ("^术语与候选不能为空$", "The term and its candidates cannot be empty"),
    ("^未找到 Provider：(.+)$", r"Provider not found: \1"),
    ("^未知 Provider 类型：(.+)$", r"Unknown provider type: \1"),
    ("^未配置 (.+) 的 API 密钥，请先在设置页填写$",
     r"API key for \1 is not configured; please fill it in on the Settings page"),
    ("^不支持的数据库版本：(.+)$", r"Unsupported database version: \1"),

    # --- 翻译流程与导出结果（core/pipeline.py） ---
    ("^所选范围内没有待翻译的段落$", "No segments to translate in the selected range"),
    ("^无法读取语言代码$", "Cannot read the language code"),
    ("^已有同名文件，本次另存为 (.+)$", r"A file with the same name exists; saved as \1 this time"),
    ("^替换「(.+)」×(.+)$", r"Replace \"\1\" x \2"),
    (r"^(\d+) 段未完成（pending/failed）$", r"\1 segments are unfinished (pending/failed)"),
    ("^源文件在翻译后被修改，译文可能与源文不一致$",
     "The source file changed after translation; the translation may no longer match it"),
    ("^源文件缺失$", "Source file is missing"),
    ("^不支持的跨格式导出：(.+)$", r"Unsupported cross-format export: \1"),
    ("^已从 (.+) 转换为 (.+)（结构按段落重排，源格式特有元素可能丢失）$",
     r"Converted from \1 to \2 (structure re-flowed by segment; source-format-specific "
     r"elements may be lost)"),

    # --- 服务商调用错误（llm/） ---
    ("^网络错误：(.+)$", r"Network error: \1"),
    ("^请求超时：(.+)$", r"Request timed out: \1"),
    ("^响应结构异常：(.+)$", r"Unexpected response structure: \1"),
    ("^模型列表响应异常：(.+)$", r"Unexpected model-list response: \1"),
    (r"^鉴权失败\((.+)\)：请检查 API 密钥$", r"Authentication failed (\1): check the API key"),
    (r"^余额不足\(402\)：请充值或更换 Provider$",
     "Insufficient balance (402): top up the account or switch providers"),
    (r"^触发限流\(429\)$", "Rate limit hit (429)"),
    (r"^服务端错误\((.+)\)$", r"Server error (\1)"),
    (r"^内容策略拒绝\(400\)$", "Rejected by the content policy (400)"),
    (r"^请求被拒绝\((.+)\)：(.+)$", r"Request rejected (\1): \2"),
    ("^(.+) 不支持模型列表拉取，请手动输入$",
     r"\1 does not support fetching the model list; please enter the model manually"),
]


#: 确实不该翻的文案：发给模型的提示词、文件格式契约、内部标记与正则。
#: 每条都要写理由（`tests/unit/test_i18n_coverage.py` 会核对是否仍然存在）。
#:
#: 注意：f-string 的**字面片段**（`f"未配置 {pid} 的密钥"` 拆出来的「未配置 」）
#: 不在这里 —— `ui_strings()` 的 `_joined_children()` 已经把片段排除，只按
#: `_sample()` 拼出的整串（「未配置 1 的密钥，请先在设置页填写」）要求词条，
#: 整串由上面的 RULES 覆盖，所以片段既不需要规则也不需要白名单。
ALLOWED: list[tuple[str, str]] = [
    # --- 发给模型的提示词（进 prompt_builder，不是界面文案） ---
    ("采用正式书面语风格：用词严谨、句式规范，避免口语化与网络用语。", "core/styles.py 的风格提示词，发给模型"),
    ("采用轻松口语风格：自然流畅的日常表达，可适度意译，贴近目标语言读者的阅读习惯。", "core/styles.py 的风格提示词，发给模型"),
    ("采用文学翻译风格：注重文气、节奏与画面感，修辞得体，允许创造性表达以传达原文韵味。", "core/styles.py 的风格提示词，发给模型"),
    ("采用学术风格：术语准确、表述客观、逻辑严密，遵循目标语言的学术写作惯例。", "core/styles.py 的风格提示词，发给模型"),
    ("按译者自定义要求翻译。", "core/styles.py 的自定义风格兜底提示词，发给模型"),

    # --- 文件格式契约（导出文件的内容，不是界面） ---
    ("源语言", "core/glossary.py 的术语表 CSV 表头：列名是文件格式契约，翻译会破坏与 Excel/旧文件的往返（界面表头由 en_settings 的 `^源语言$` 负责）"),
    ("目标语", "core/glossary.py 的术语表 CSV 表头：同上，列名不可翻"),
    ("注释", "core/glossary.py 的术语表 CSV 表头：同上，列名不可翻"),
    ("原文", "adapters/bi_table 与 docx 表头：导出文件里的固定列名，翻译会破坏旧软件打开与重导入（界面上叫「源文」，由 en_review 负责）"),
    ("| 原文 | 译文 |", "adapters/md_adapter.py 的双语导出表头：导出文件内容"),
    ("(无扩展名)", "adapters/__init__.py:24 里 `path.suffix or '(无扩展名)'` 的兜底片段，是给异常消息拼后缀用的；整串消息由 RULES 的「不支持的文件格式」规则负责"),
    ("标题", "adapters/docx_adapter.py 识别 Word 内置「标题」样式的名字，翻掉会让标题识别失效"),
    ("源文出现段数", "core/consistency.py:70 的一致性报表 CSV 列名：一次性导出文件的格式契约，不是界面文案（校对页表格表头由 en_review 负责）"),
    ("候选命中次数", "core/consistency.py:70 的一致性报表 CSV 列名：同上，列名不可翻"),
    ("可疑段落ID", "core/consistency.py:70 的一致性报表 CSV 列名：同上，列名不可翻"),

    # --- 拼进译文正文的注音标记（f-string 采样形态，不是界面文案） ---
    # 按设计它们不该有词条：没有任何界面控件拿它们当整串文案，适配器是把它们
    # **写进译文正文**的。宽规则（`^（(…)）$` 之类）会抢后面 catalog 的整串词条，
    # 所以既不下规则、也不整形，只在这里记录「故意不翻」。
    ("1（1）", "core/langs.py:50 `f\"{code}（{name}）\"` 与 adapters/docx_adapter.py:38 `f\"{base}（{rt}）\"` 的采样形态：前者名称部分由上面的语言名词条负责、后者是写进译文正文的注音"),
    ("（1）", "adapters/docx_adapter.py:178、adapters/html_adapter.py:230 的 `f\"（{rt}）\"` 注音兜底标记：写进译文正文，不是界面文案"),
    ("《1》", "core/engine.py:326 `f\"《{rt}》\"` 补回漏译注音的标记：写进译文正文，不是界面文案"),

    # --- 内部标记 / 正则（不显示，翻了会坏功能） ---
    # `core/engine.py:326` 的 `f"《{...}》"` 注音标记不在这里：它在 f-string 里，
    # 扫描器的 `_joined_children()` 已经把片段排除，也不需要规则。
    ("。！？；…", "core/segmentation.py 的中文句末标点集合，分段算法用"),
    ("」』”）\"’》〉〕", "core/segmentation.py 的收尾标点集合，分段算法用"),
    ("敏感内容", "llm/errors.py 的内容策略匹配关键字：用来匹配服务商返回的报错正文，翻了就匹配不上"),
    ("\"terms\"|候选词", "llm/mock.py 解析模型返回 JSON 的正则，不是文案"),
    (" +([，。！？；、）】》」』,.!?;:])", "core/engine.py 清理译文多余空格的内部正则"),
]
