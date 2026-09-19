# AI 项目翻译器

以「项目」为单位的桌面端 AI 辅助翻译工具：导入 → 分段 → 术语归纳 → AI 批量翻译 → 双栏校对 → 多格式导出。

| 文档 | 内容 |
|---|---|
| `AGENT.md` | 使用与开发速览（格式支持、怎么用、改代码前必读） |
| `docs/架构导览.md` | 分层架构、模块地图、数据流、关键机制、雷区清单、已知问题 |
| `docs/设计文档/AI翻译软件-软件设计方案.md` | 主设计文档（v0.7） |
| `docs/设计文档/增补需求-优化设计方案.md` | 振假名 / 上下文滑窗增补设计 |
| `docs/milestone-acceptance.md` | 逐项验收记录 |
| `CHANGELOG.md` | 变更记录（新增功能 / 修复缺陷） |

## 快速开始

```bash
pip install -r requirements.txt          # 运行依赖
python main.py                           # 启动桌面端
python main.py --selftest                # 离屏 UI 冒烟（6 页面实例化）
python -m pytest tests -q                # 全量测试（168 项，Mock Provider，不耗 API）
```

> **日常使用不需要编译**：`python main.py` 直接跑源码，改完代码重新启动即生效。
> 也可以双击 `启动翻译器.bat`（无控制台窗口）或桌面快捷方式；出问题时用
> `调试启动.bat` 启动，能看到错误信息。PyInstaller 只用于打包给别人。

OCR（M3）：`pip install rapidocr-onnxruntime`（主引擎，必装）；`pip install winsdk`
为可选兜底（用 Windows 自带 OCR，依赖系统已装语言包）
打包（M3）：`python tools/build.py` → `dist/AITranslator/AITranslator.exe`

## 项目文件夹结构

```
MyNovel/
├── project.json      # 语言方向、风格、Provider（不含密钥）
├── glossary.csv      # 术语表（三列：源语言,目标语,注释；候选用 | 分隔 ≤3；Excel 可直改）
├── source/           # 源文件（txt / md / html / docx / epub / srt / png…）
├── target/           # 导出译文（原名.en.md 等，支持双语模式；同名文件可按设置覆盖/另存/跳过）
└── .aitrans/         # work.db（分段/译文/进度）+ lock 文件锁 + logs（目录已建，暂未写入日志）
```

支持格式：txt、md（front matter/代码块/公式/链接 URL 保护）、html（属性翻译、
code/img/链接占位符）、docx（段落样式保留、表格逐格）、epub（保留容器与其余条目，
内容文档写回良构 XHTML）、srt 字幕（一个 cue = 一段，序号/时间轴逐字节保留）、
图片（OCR 提取翻译）。详见 `docs/架构导览.md` §11。

## 架构（四层，设计 §4）

```
app/      PySide6 UI（6 页面：项目/工作台/进度/校对/项目设置/设置；术语审核是对话框）
core/     project / pipeline（服务编排）/ engine（批量翻译引擎）
          glossary / term_induction（两阶段归纳）/ term_impact / consistency / segmentation
adapters/ txt / md / html / docx / epub / srt / ocr（IFormatAdapter：parse + render，双语模式）
llm/      provider 抽象 / openai_compat / anthropic / gemini / prompt_builder / errors
storage/  db（SQLite WAL + RLock 串行写）/ secrets（keyring）
tests/    unit + acceptance（黄金文件往返、Mock Provider 全流程、断点续跑）
```

## 关键机制（对照设计文档）

- **翻译记忆**：`src_hash + cfg_hash` 为键（改注释/换模型不失效）；运行内跨文件去重不重复计费；
- **断点续翻**：进度落 `run_items`；401/402 暂停 Run，修复后续跑；取消保留 pending；
- **错误分型**：429/5xx 退避重试；内容策略拒绝整批降级逐段，仅当事段失败；
- **术语**：两阶段归纳（本地频次→LLM 判定含人名地名，zh=jieba / ja=片假名+汉字 / 其他=拉丁词）
  → 人工审核 → glossary 文件为权威；变更后影响分析（快照 diff → 全局替换可撤销 / 标记重译，
  校对页「术语变更影响…」入口；**外部用 Excel 改过也能对比**）；译后一致性报表（复数/大小写宽松匹配）；
- **振假名**（增补需求）：`《》`/全角括号（可选宽松）/**markdown 内联 `<ruby>`**/html `<ruby>`/docx `w:ruby`
  检测 → 基词内联 + `{rN}` 注音槽；三策略 drop / keep / translate（注音随 JSON 单次调用译出）；
  md / html / epub / docx 原生格式还原（锚定按源基词长度截短）；文件级策略覆盖（文件标签）；
  校对页注音内联编辑；默认策略不改变缓存指纹；
- **上下文滑窗**（增补需求）：项目级 `context_slide`（前 N + 后 N 段，0-5，缺省继承全局）；
  前文取已定稿译文、后文取源文并禁止翻译；合计 ≤3000 字符保近邻截断；
  N=0 时 prompt 与旧版逐字节一致；
- **安全**：API Key 走系统凭据管理器（keyring）；prompt 注入防护（正文指令一律忽略）+ 强制 JSON。
- **Provider 管理**（测试反馈版）：类型按 base_url 自动识别；model 可从平台拉取
  （"获取模型列表"按钮，OpenAI 兼容 / Anthropic / Gemini 三协议，24h 缓存，失败可手输）；
  密钥永不回显——已配置显示掩码占位、留空保持不变、可一键删除重配；
  价格字段已从界面移除、费用不再展示（预估与进度只显示 token 用量；`runs.cost` 仍在
  按 Provider 价格折算，仅作记录）。

## 实现决策与已知边界

1. **OpenAI 兼容协议用 httpx 直连**（设计文档写 openai SDK）：协议完全一致，便于
   MockTransport 测试与裁剪依赖；功能无差异。
2. **html / epub 链接**：整个 `<a>` 作为占位符保护（URL 防注入优先），链接文字暂不翻译
   （md 的链接文字会翻译）；因此 **epub 的目录（nav.xhtml / NCX）里章节名保持原文**。
   如需翻译锚文本，要改成"字符串占位符 + 单独处理 NCX"。
3. **docx 段内混合样式**（如半句加粗）无法逐字保留：译文整段套用段落主样式（设计 §7.1 已声明）。
4. **OCR 只做「提取翻译」**，不做原文位置还原（设计 §7.1 M3 边界）；识别精度目前受
   中英模型限制，日/韩图片需另行提供该语言的识别模型文件。
5. **图片默认导出 `.md`**（`page1.png → target/page1.en.md`，遵循"跟随源文件"的默认设置）；
   在导出对话框显式选择其它格式即可跨格式导出为 txt / md / html / docx。
6. **epub 跨格式导出**（如 → md）只保留段落与标题层级，原书特有元素（样式表、分章文件
   结构、图片）会丢失，导出结果会给出提示。

## 配置

- 全局：`%APPDATA%/AITranslator/config.json`（Provider 列表、`concurrency` 并发数、
  `context_segments` 默认滑窗段数、`recent_projects` 最近项目、`model_cache` 模型列表缓存、
  `last_provider_id` / `last_model` 上次选择；Provider 项仍带 `price_*` 键但界面已不展示）
- 密钥：Windows 凭据管理器（服务名 AITranslator，账号=Provider id），项目文件可整体拷贝共享。
