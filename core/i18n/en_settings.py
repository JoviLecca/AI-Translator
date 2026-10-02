"""英语词条：设置页、项目设置页、新建/打开项目向导、主窗口框架。

用词沿用项目里的统一说法：project 项目 / file 文件 / segment 段落 / source 源文 /
translation 译文 / review 校对 / term 术语 / provider 服务商 / API key 密钥 /
ruby 振假名（注音）/ style 风格 / context window 上下文滑窗。

规则写法：`(pattern, replacement)` 按 `re.sub` 语义替换，整串用 `^…$` 锚定；
动态部分按 f-string 采样后的样子写（插值位是 `1`），用 `(\\d+)` / `(.+)` 捕获并 `\\1` 回填。
组合文案（弹窗前缀、服务清单行、问句）拆成多条规则，`tr()` 会多轮扫描逐层翻干净；
**重叠的规则要把更具体的那条写在前面**（每轮只应用第一条能改动文本的规则）。
"""
from __future__ import annotations

RULES: list[tuple[str, str]] = [
    # --- 主窗口框架（侧栏页名 = pages 字典的键，键本身不翻译，显示文本才翻）---
    ("^AI 项目翻译器$", "AI Project Translator"),
    ("^项目管理$", "Projects"),
    ("^项目工作台$", "Workbench"),
    ("^翻译进度$", "Progress"),
    ("^校对编辑器$", "Review"),
    ("^项目设置$", "Project settings"),
    ("^设置$", "Settings"),

    # --- 设置页：Provider 管理 ---
    ("^Provider 管理$", "Providers"),
    ("^名称$", "Name"),
    ("^密钥$", "API key"),
    ("^API 密钥$", "API key"),
    ("^本地模型$", "Local models"),
    ("^一键添加$", "Add preset"),
    ("^检测本机服务$", "Detect local services"),
    ("^获取模型列表$", "Fetch models"),
    ("^删除密钥$", "Delete key"),
    ("^新增/更新$", "Add / Update"),
    ("^连通测试$", "Test connection"),
    ("^密钥状态检查$", "Check key"),
    ("^删除$", "Delete"),
    ("^已配置$", "Configured"),
    ("^未配置$", "Not configured"),
    ("^不需要（本地）$", "Not needed (local)"),
    ("^本地服务不需要密钥（留空即可）$", "Local services need no key — leave it empty"),
    ("^●●●●●●●●（已配置，留空保持不变）$",
     "●●●●●●●● (configured — leave empty to keep it)"),
    ("^输入 API 密钥（存入系统凭据管理器，不回显）$",
     "Enter the API key (kept in the system credential manager, never shown)"),
    ("^本地模型不需要 API 密钥：先在本机启动服务（如 ollama serve、LM Studio 的 Local Server），"
     "再点「检测本机服务」自动添加，或直接选预设一键添加。本地推理较慢，建议把并发请求数降到 1~2。$",
     "Local models need no API key: start the service on this machine first "
     "(e.g. `ollama serve`, or LM Studio's Local Server), then click \"Detect local services\" "
     "to add it automatically — or pick a preset and click \"Add preset\". "
     "Local inference is slow, so consider lowering concurrent requests to 1–2."),
    ("^Ollama（本地）$", "Ollama (local)"),
    ("^LM Studio（本地）$", "LM Studio (local)"),
    ("^llama.cpp（本地）$", "llama.cpp (local)"),
    ("^vLLM（本地）$", "vLLM (local)"),
    ("^(.+?)（本地） · (https?://.+)$", r"\1 (local) · \2"),
    ("^并发请求数$", "Concurrent requests"),
    ("^默认上下文滑窗段数（前 N \\+ 后 N）$", "Default context window (N before + N after)"),
    ("^界面语言$", "Interface language"),
    ("^切换后界面立即按新语言重建；校对页里未保存的编辑会先落库。$",
     "The interface is rebuilt in the new language right away; unsaved edits on the "
     "review page are saved first."),
    ("^保存设置$", "Save settings"),
    ("^设置已保存$", "Settings saved"),

    # --- 设置页：动态提示（更具体的两条线在前，单行兜底在后）---
    ("^获取到 (\\d+) 个模型$", r"Fetched \1 models"),
    ("^已删除 (.+) 的密钥，可重新配置$", r"Deleted the key for \1 — you can configure it again"),
    ("^已添加 (.+)，可先「获取模型列表」再开始翻译$",
     r"Added \1 — click \"Fetch models\" before you start translating"),
    ("^请先填写 Provider ID$", "Enter a provider ID first"),
    ("^Provider ID 不能为空$", "The provider ID cannot be empty"),
    ("^缺少 ID$", "Missing ID"),
    ("^缺少 model$", "Missing model"),
    ("^请填写或获取 model$", "Type or fetch a model first"),
    ("^平台未返回模型列表，请手动输入。$", "The platform returned no model list — type it manually."),
    ("^失败：(.+)\\n可在 model 框手动输入模型名。$",
     "Failed: \\1\nYou can type the model name into the model box."),
    ("^失败：(.+)\\n可手动输入模型名。$",
     "Failed: \\1\nYou can type the model name manually."),
    ("^成功：(.+)$", r"Succeeded: \1"),
    ("^失败：(.+)$", r"Failed: \1"),
    ("^删除 Provider$", "Delete provider"),
    ("^删除 (.+)？其保存在系统凭据管理器中的 API 密钥将一并清除。$",
     r"Delete \1? The API key stored in the system credential manager is removed as well."),
    ("^请先填写或选择 Provider ID$", "Enter or pick a provider ID first"),
    ("^(.+) 尚未配置密钥$", r"\1 has no key configured"),
    ("^密钥后端$", "Key backend"),
    ("^系统凭据管理器可用。$", "The system credential manager is available."),
    ("^不可用：(.+)$", r"Unavailable: \1"),
    ("^密钥保存失败$", "Could not save the API key"),

    # --- 设置页：本地服务探测（前缀 / 每行 / 问句三条规则，多轮扫描接力）---
    ("^检测到以下本地服务：\\n$", "Detected these local services:\n"),
    ("^· (.+?)(（本地）)?：(.+)（已在列表，模型 (\\d+) 个）$",
     r"· \1: \3 (already in the list, \4 models)"),
    ("^· (.+?)(（本地）)?：(.+)（可添加，模型 (\\d+) 个）$",
     r"· \1: \3 (can be added, \4 models)"),
    ("^· (.+?)(（本地）)?：(.+)（已在列表，未返回模型列表）$",
     r"· \1: \3 (already in the list, no model list returned)"),
    ("^· (.+?)(（本地）)?：(.+)（可添加，未返回模型列表）$",
     r"· \1: \3 (can be added, no model list returned)"),
    ("^· (.+)：(.+)（(.+)，(.+)）$", r"· \1: \2 (\3, \4)"),
    ("^已在列表$", "already in the list"),
    ("^可添加$", "can be added"),
    ("^未返回模型列表$", "no model list returned"),
    ("^是否把其中 (\\d+) 个加入 Provider 列表？$", r"Add these \1 service(s) to the provider list?"),
    ("^已添加 (\\d+) 个本地服务，可直接开始翻译$",
     r"Added \1 local service(s) — ready to translate"),
    ("^没有检测到本机运行的本地推理服务。\\n请先启动服务（例如 ollama serve，或 LM Studio 的 Local Server）"
     "后重试；也可以直接选预设「一键添加」，手动填写地址与模型名。$",
     "No local inference service was detected on this machine.\n"
     "Start one first (for example `ollama serve`, or LM Studio's Local Server) and retry; "
     "you can also pick a preset with \"Add preset\" and fill in the address and model name yourself."),
    ("^检测失败：(.+)$", r"Detection failed: \1"),
    ("^(.+) 已在列表中$", r"\1 is already in the list"),

    # --- 项目设置页 ---
    ("^项目名$", "Project name"),
    ("^源语言$", "Source language"),
    ("^目标语言$", "Target language"),
    ("^风格$", "Style"),
    ("^自定义提示词$", "Custom prompt"),
    ("^振假名策略$", "Ruby policy"),
    ("^上下文滑窗（前 N \\+ 后 N 段）$", "Context window (N before + N after)"),
    ("^drop（丢弃注音）$", "drop (discard ruby)"),
    ("^keep（保留原假名）$", "keep (keep the kana)"),
    ("^translate（注音也翻译）$", "translate (translate the ruby too)"),
    ("^宽松识别：全角括号假名（汉字（かんじ））也当注音$",
     "Loose matching: treat full-width parenthesised kana as ruby too"),
    ("^继承全局设置$", "Inherit global setting"),
    ("^保存（振假名策略变更会使译文缓存失效）$",
     "Save (changing the ruby policy invalidates the translation cache)"),
    ("^重译过期机器译稿$", "Re-translate stale machine output"),
    ("^项目设置已保存$", "Project settings saved"),
    ("^配置已变更$", "Configuration changed"),
    ("^配置指纹已变化：(\\d+) 段已有译文的缓存已失效。\\n"
     "新翻译将采用新配置；如需重译旧的机器译稿，点击「重译过期机器译稿」。\\n"
     "（上下文滑窗为质量旋钮，不触发缓存失效；调整后可手动重译刷新质量）$",
     "The configuration fingerprint changed: cached translations for \\1 segment(s) are now stale.\n"
     "New translations will use the new configuration; click \"Re-translate stale machine output\" "
     "to redo the old machine translations.\n"
     "(The context window is a quality knob — it does not invalidate the cache; "
     "re-translate manually to refresh quality.)"),
    ("^(\\d+) 段过期机器译稿已标记重译$", r"\1 stale machine translation(s) marked for re-translation"),

    # --- 新建 / 打开项目向导 ---
    ("^新建翻译项目$", "New translation project"),
    ("^项目信息$", "Project info"),
    ("^选择目录…$", "Choose folder…"),
    ("^语言方向与风格$", "Languages and style"),
    ("^自定义风格提示词（选「自定义」时生效）$",
     "Custom style prompt (used when the style is \"Custom\")"),
    ("^自动（按目标语言：日语→保留，其他→丢弃）$",
     "Auto (by target language: Japanese → keep, others → discard)"),
    ("^宽松识别全角括号假名注音$", "Loose matching for full-width parenthesised kana"),
    ("^AI 服务与密钥$", "AI service and API key"),
    ("^连通性测试$", "Test connection"),
    ("^选择项目位置$", "Choose the project folder"),
    ("^选择项目文件夹$", "Choose the project folder"),
    ("^项目名称：$", "Project name:"),
    ("^项目文件夹（将自动创建 source/ target/）：$",
     "Project folder (source/ and target/ are created automatically):"),
    ("^翻译风格$", "Translation style"),
    ("^自定义提示词：$", "Custom prompt:"),
    ("^振假名（注音）策略：$", "Ruby (furigana) policy:"),
    ("^Provider：$", "Provider:"),
    ("^model（可点击右侧按钮从平台拉取）：$",
     "model (click the button on the right to fetch it from the platform):"),
    ("^API 密钥（存入系统凭据管理器，不写入项目文件）：$",
     "API key (kept in the system credential manager, not in the project file):"),
    ("^本地服务不需要密钥，留空即可$", "Local services need no key — leave it empty"),
    ("^输入 API 密钥（存入系统凭据管理器）$",
     "Enter the API key (kept in the system credential manager)"),
    ("^请先填写 API 密钥$", "Enter the API key first"),
    ("^请先填写密钥$", "Enter the API key first"),
    ("^请先选择 Provider$", "Pick a provider first"),
    ("^已删除 (.+) 的密钥，可重新配置。$", r"Deleted the key for \1 — you can configure it again."),
    ("^连通成功：(.+)$", r"Connection OK: \1"),
    ("^缺少目录$", "Missing folder"),
    ("^请填写项目文件夹位置（不建议留空）$",
     "Enter the project folder (leaving it empty is not recommended)"),
    ("^目录不存在$", "Folder not found"),
    ("^路径不存在：\\n(.+)$", "Path does not exist:\n\\1"),
    ("^新建失败$", "Could not create the project"),
    ("^打开失败$", "Could not open the project"),
    ("^(.+)\\n密钥未保存，请到设置页补填。$",
     "\\1\nThe key was not saved — please fill it in on the Settings page."),
    ("^未命名项目$", "Untitled project"),
    ("^新建项目$", "New project"),
    ("^打开现有项目$", "Open existing project"),
    ("^移出列表$", "Remove from list"),
    ("^最近项目$", "Recent projects"),
]

#: 不翻译的字符串 + 理由（覆盖率测试会要求它们仍然存在）。
ALLOWED: list[tuple[str, str]] = [
    (" 个", "「模型 3 个」的碎片，整行由 `^· …（…，模型 (\\d+) 个）$` 规则整体翻译"),
    ("模型 ", "同上：碎片只在拼行时出现，整行规则已覆盖"),
]
