# Roadmap（待开发项）

记录**尚未实现**的功能计划，与 `CHANGELOG.md`（已完成的变更）互补：
`CHANGELOG.md` 只写已经交付的东西，本文件只写还没做的东西。

**状态标记**：`📋 待评估` · `🔨 进行中` · `⏸ 挂起` · `✅ 已完成（转入 CHANGELOG 并从本文件移除）`

每个条目统一写：**目标 → 现状 → 方案 → 改动点 → 验收 → 风险**。
文中引用的行号以 2026-09-19 的工作区状态为准，改代码后请顺手校正。

建立日期：2026-09-19

---

## RM-01 · OCR 补充日语 / 韩语识别

**状态**：📋 待评估 ｜ **关联**：`adapters/ocr_adapter.py`、`requirements.txt`、`tools/AITranslator.spec`

### 目标

图片 OCR 在源语言为日语（含假名）或韩语（含谚文）时，识别结果可用 ——
而不是像现在这样只靠中英识别模型硬扛。

### 现状（已核实）

- `adapters/ocr_adapter.py:4-7` 的模块 docstring **已经把这条列为"待补能力"**：
  `rapidocr-onnxruntime` 内置的是中英识别模型，`RapidOCR()` 构造时没有"按语言选识别
  模型"的参数（需自行提供 `rec_model_path` 模型文件）。
- `_LANG_MAP`（第 23-28 行）已把 `ja/jp → japan`、`ko/kr → korean`，但目前这两个值
  **只用于** `_OCR_INSTANCES` 的引擎缓存键，以及 winsdk 兜底时请求的系统语言包；
  对 RapidOCR 的实际识别精度没有任何影响。
- `rapidocr_backend()`（第 44-48 行）无参构造 `RapidOCR()`，第 1 层修复
  （`src_lang` 已能传进来，见 `CHANGELOG.md` 与 `docs/架构导览.md` §10.8 ①）已完成，
  **第 2 层（按语言加载识别模型）是本条的全部工作量**。
- winsdk 兜底（`winsdk_backend`）依赖系统已安装对应语言的 OCR 语言包；
  日/韩包通常需要用户手动添加，目前只会抛"无可用语言引擎"。
- `requirements.txt:8` 固定 `rapidocr-onnxruntime>=1.3`。
- UI 侧无需改动：语言下拉已含 `ja-JP` / `ko-KR`（`core/langs.py:19-20`）。
- 已登记为开放项：`docs/架构导览.md` §10.13 第 1 条。

### 方案

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A. 升级到 `rapidocr` 3.x** | 新包名 `rapidocr`，`RapidOCR(params={"Rec.lang_type": LangRec.JAPAN / KOREAN})`，模型从 ModelScope **自动下载**并在本地缓存 | 最省事、语言覆盖最广；但 API 与 1.x 不同、引入首用联网下载、exe 体积与打包策略要重新决定 |
| **B. 沿用 `rapidocr-onnxruntime`，自带模型** | 自行下载 japan / korean 的 ONNX 识别模型，按语言传 `rec_model_path` | 离线可控、不换包；但要自己管理模型文件、体积与再分发许可 |
| **C. 强化 winsdk 兜底** | 设置页检测系统已装语言包，缺失时给出明确指引（Windows 设置 → 语言 → 添加语言 → 勾选"光学字符识别"） | 零额外下载；完全依赖用户装包，不能作为主路径 |

**建议**：B 或 A 作为主引擎，C 作为兜底并在设置页暴露"检测 OCR 语言包"按钮。
`_OCR_INSTANCES` 已经是**按语言分槽**的缓存（代码里就写着"便于日后各自加载对应的
`rec_model_path`"），选 A/B 都能直接用上，不必重构缓存结构。

参考：[RapidOCR 模型列表](https://rapidai.github.io/RapidOCRDocs/latest/model_list/)
—— PP-OCRv5 的识别模型支持 `korean`（`rapidocr>=3.3.0`），其 `ch` 模型本身即
"中英日混合"；PP-OCRv6 的 `small` / `medium` 也覆盖 `japan`（`tiny` 不支持）。

### 改动点

1. `adapters/ocr_adapter.py`：`rapidocr_backend()` 内按 `lang` 传入模型参数/路径。
   ⚠️ 若换 `rapidocr` 3.x，返回结构也变了（1.x 是 `result[i][1]`，3.x 是带 `txts`
   字段的对象），第 49 行的取值逻辑必须同步改。
2. `requirements.txt` / `requirements-dev.txt`。
3. `tools/AITranslator.spec`：`hiddenimports` 同步新包及其后端；若模型随包分发还要加 `datas`。
4. `app/pages/settings_pages.py`：OCR 可用性/语言包检测提示。
5. 文档同步：`AGENT.md` §3.1 图片行、`README.md`、`docs/架构导览.md` §10.13 第 1 条与 §11.1。
6. 测试：`tests/unit/test_ocr_adapter.py` 增加"按语言选择模型"的断言（把下载/加载
   mock 掉，不依赖网络）。

### 验收

- 固定样本集（日文含假名 / 韩文含谚文各若干张）的字符准确率相对现状有**可量化**提升；
- 无网络或语言包缺失时给出**明确错误**，不静默退化成中英模型（现状就是静默退化）；
- 现有 2 个 OCR 测试与打包流程不被破坏。

### 风险

- 换包是新 API，回归面比看起来大（返回值结构、参数体系、异常类型）；
- 运行时下载模型 = 新的失败模式（代理、离线、企业网络），需要有缓存与降级路径；
- 模型来源（ModelScope / PP-OCR）的再分发许可需确认，尤其若决定"随 exe 分发"。

---

## RM-02 · PDF 格式的导入 / 导出

**状态**：📋 待评估（**导入与导出需分开立项**）｜ **关联**：`adapters/__init__.py`、新增 `adapters/pdf_adapter.py`、`requirements.txt`

### 目标

让 PDF 进入现有的「导入 → 分段 → 术语 → AI 翻译 → 校对 → 导出」流水线。
**PDF 不是一个格式，而是一族格式** —— 因此本条按内容形态拆分，不追求一次做完。

### 现状（已核实）

- **完全不支持**：`adapters/__init__.py:8-15` 的 `_FORMAT_BY_EXT` 没有 `.pdf`；
  `detect_format()` 直接抛 `FormatError`（提示"请先转为 txt / md / html / docx / epub / srt"）。
- `tests/unit/test_adapters.py:8,14` **就以 `.pdf` 被拒绝为断言** —— 加 PDF 必须同步改这个测试。
- `requirements.txt` 中没有任何 PDF 库。
- 导入对话框白名单 `app/pages/workbench_page.py:232` 也没有 pdf（它连 epub/srt 都漏了，见文末附录）。
- `AGENT.md:73`、`docs/架构导览.md:961` 明确写"不支持 `.pdf`"。

### 第一步：按内容形态分类（用户提示的落点）

导入链路完全不同，必须逐类实现、逐类验收：

| 类型 | 判定方式 | 导入方案 |
|---|---|---|
| **电子版**（有文字层） | 页面 `get_text()` 字符量足够 | 直接抽取文本块，**不 OCR**；保留段落与标题层级 |
| **扫描版**（纯图像） | 文字层近空、页面被整幅位图覆盖 | 页面渲染成位图 → **复用 `ocr_adapter`**（含 RM-01 的语言能力） |
| **混合版** | 逐页判定 | 逐页选择上面两条路径，并**在 UI 标注每页走了哪条** |

在此之上还要按**版式**分档，否则段落顺序会乱：

- 单栏正文（小说 / 轻小说）—— 最容易，建议作为第一个验收目标；
- 双栏（论文 / 杂志）—— 必须做分栏切分，否则左右栏段落会交错；
- 图文混排（画册 / 教材）；
- 表格、表单（跨页表格、无边框表）；
- 页眉页脚、页码、脚注 —— 需在分段阶段过滤，可复用现有的 `kind` 透传段机制；
- 跨页段落 —— 拼接策略要单独设计（不能简单地一页一段）。

### 第二步：先定导出边界（这比导入难得多）

| 方案 | 说明 | 难度 | 建议 |
|---|---|---|---|
| **① PDF 仅作导入源** | 导出走**已有的跨格式导出**（md / txt / html / docx） | 低 | **先做这个**，最快可用、零排版风险 |
| **③ 双语对照 PDF** | 原页 + 译文页，或原页加批注栏 | 中 | 第二阶段评估，对学习者最实用 |
| **② 原版式回写** | 把译文写回原位置（擦除原文本 + 重新排版） | 高 | 长期项；需处理 CJK 字体嵌入、译文长度变化导致的重排与溢出、跨页段落，**只能承诺"优先可读性"，无法承诺像素级还原** |

现有导出模式（`target` / `bi_inter` / `bi_table`，见 `docs/架构导览.md` §11.2）中，
`bi_table` 对 PDF 回写没有意义，方案 ② 下需要单独定义模式语义。

### 库选型（**许可证是硬约束**）

| 库 | 许可 | 能力 | 评价 |
|---|---|---|---|
| `pymupdf`（PyMuPDF） | **AGPL-3.0 或商业授权** | 抽取 + 渲染 + 回写一站式 | 本仓库要公开发布，**AGPL 传染性必须先决策**，不能顺手就引 |
| `pdfplumber`（基于 pdfminer.six） | MIT | 字符级坐标、表格抽取强 | 抽取首选 |
| `pypdfium2` | Apache-2.0 / BSD 系列 | 页面渲染成位图 | 给扫描版 OCR 用，许可宽松 |
| `pypdf` | BSD-3 | 页面级操作、解密 | 辅助（拆分、空密码解密） |

**建议组合**：`pdfplumber` / `pdfminer.six` 抽文字层 + `pypdfium2` 渲染扫描页 →
全程不引入 AGPL。

### 改动点

沿用 `docs/架构导览.md` §11.1 的"新增格式 = 4 步"：

1. 新增 `adapters/pdf_adapter.py`（实现 `parse` / `render`，参照 `epub_adapter` 的
   分文档 + `seq_base` 偏移写法）；
2. 注册：`adapters/__init__.py` 的 `_FORMAT_BY_EXT` + `get_adapter()` 分支；
3. `tools/AITranslator.spec`：`hiddenimports`（懒加载，漏了会在 exe 运行时才报 `ImportError`），
   若随包带字体/模型再补 `datas`；
4. UI：`app/pages/workbench_page.py:232` 导入过滤器；若支持导出，补
   `app/pages/review_page.py::ExportDialog.FORMATS`；
5. 文档：`AGENT.md` §3.1/§3.2、`README.md`、`docs/架构导览.md` §11（并把 §11.1
   "不支持 .pdf"那句删掉）；
6. 测试：改 `tests/unit/test_adapters.py`，新增 `tests/unit/test_pdf_adapter.py`。

### 验收

- 电子版 PDF：段落顺序正确、**未触发 OCR**；扫描版 PDF：OCR 文本可用；
  混合版 PDF：逐页分流正确且用户可见；
- 跨页段落不丢字；页眉页脚/页码可按设置过滤；
- 导出为 md / txt / docx 后结构无错乱；
- 未安装 PDF 依赖、或遇到加密/权限受限 PDF 时给出**明确提示**而非崩溃。

### 风险 / 开放问题

- 是否需要"每页走哪条路径（文字层 / OCR）"的用户可见说明？建议要，否则用户无法判断质量；
- 数百页 PDF 的渲染内存与耗时 —— 需要进度 + 取消（可复用 S3 的导出进度机制，
  见 `CHANGELOG.md`「导出进度」）；
- 日/中文 PDF 常使用**嵌入子集字体**，回写时无法直接复用 → 是否内置一套 CJK 字体
  （体积 + 字体许可）是方案 ② 的前置决策；
- 双栏切分与跨页表格的算法本身没有成熟现成方案，建议用真实样本先做可行性验证再排期。

---

## 附：实施中顺带发现的缺口（非本次新增需求）

**导入对话框缺少 EPUB / SRT 过滤器** —— `app/pages/workbench_page.py:232`

✅ **已于 2026-09-19 修复**（见 `CHANGELOG.md`「文档与导入过滤器批次」）。原白名单是：

```python
# 修复前
"支持的格式 (*.txt *.md *.html *.htm *.docx *.png *.jpg *.jpeg);;全部文件 (*)"
```

新增 EPUB / SRT 时漏了同步这一行，实际还一并漏了 `*.bmp` / `*.webp` / `*.markdown`。
影响面有限（用户仍可通过"全部文件"选中，"导入文件夹"走 `_FORMAT_BY_EXT` 不受影响）。
现已改为与 `_FORMAT_BY_EXT` 完全一致，并在代码里加了"必须同步维护"的注释。

**做 RM-02 时注意**：新增 PDF 注册后，这一行同样必须补 `*.pdf` —— 这正是本条附录
想留下的教训：**新增格式时容易忘的两处**是 `tools/AITranslator.spec` 的 `hiddenimports`
（已在 `AGENT.md` §6.2 强调）和这里的文件对话框白名单（此前没人提，现在写在注释里了）。
