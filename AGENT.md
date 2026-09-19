# AGENT.md

给在本仓库工作的 AI / 开发者的**一页速览**。
深度的架构说明、模块地图、雷区清单、已修复与未修复问题，见 **`docs/架构导览.md`**；
本次会话的变更见 **`CHANGELOG.md`**。

---

## 1. 这是什么

**AI 项目翻译器** —— 以「项目 = 一个文件夹」为单位的桌面端 AI 辅助翻译工具。

```
导入 → 分段 → 术语归纳 → AI 批量翻译 → 双栏校对 → 多格式导出
```

- **纯本地**桌面应用（PySide6），没有后端服务；唯一外联是用户自己配置的 LLM API。
- 技术栈：Python 3.12 左右 / PySide6-Essentials / SQLite(WAL) / httpx / lxml /
  markdown-it-py / python-docx / keyring / rapidocr-onnxruntime / jieba。
- **权威数据源**：术语表以项目根目录的 `glossary.csv` 为准（用户可用 Excel 直接改）；
  分段与译文以 SQLite 为准；`target/` 只是可随时重新生成的导出产物。

四层架构，依赖只允许自上而下：`app/`（UI）→ `core/`（服务 + 领域）→
`adapters/`、`llm/`、`storage/`（基础设施）。`core/` 不依赖 PySide6。

---

## 2. 跑起来（**不需要编译**）

```bash
pip install -r requirements.txt

python main.py                 # 启动桌面端
python main.py --selftest      # 离屏冒烟：6 个页面能否实例化
python -m pytest tests -q      # 全量测试（168 项，用 Mock Provider，不耗 API）
```

**打包给别人用**（日常使用不需要，见下）：

```bash
pip install -r requirements-dev.txt        # 含 pyinstaller（tools/build.py 缺了会自动装）
python tools/build.py                      # → dist/AITranslator/AITranslator.exe
dist/AITranslator/AITranslator.exe --selftest   # 打包后可同样冒烟验证
```

打包配置在 `tools/AITranslator.spec`。适配器与 Provider 都是**懒加载**，spec 里用
`hiddenimports` 显式声明 —— **新增格式适配器或 Provider 后必须同步补进去**，否则
exe 运行时才报 `ImportError`。

日常使用/改代码**不需要编译**：改完存盘、重新启动即生效。也可双击
`启动翻译器.bat`（无控制台窗口）；出问题时用 `调试启动.bat`，能看到错误信息。

> 本机若提示缺 `rapidocr-onnxruntime`：图片 OCR 会不可用，且 2 个 OCR 测试会失败，
> 其余功能不受影响。装上即恢复：`pip install rapidocr-onnxruntime`。打包时也要确认
> 它已安装，否则打出来的 exe 同样没有 OCR 能力。

---

## 3. 支持的格式

### 3.1 导入

| 格式 | 扩展名 | 说明 |
|---|---|---|
| txt | `.txt` | 每行一段；自动识别 UTF-8 / BOM / GB18030；含 NUL 字节判为二进制拒绝 |
| Markdown | `.md` `.markdown` | 保护行内代码 / 公式 / 图片 / 链接 URL；front matter 透传；支持 `《》` 与内联 `<ruby>` 振假名 |
| HTML | `.html` `.htm` | 保留标签与属性；`code/pre/script` 跳过；翻译 `alt` / `title` / `placeholder` 属性 |
| Word | `.docx` | 保留段落样式与表格；原生 `w:ruby` 还原 |
| **EPUB** | `.epub` | 只翻译内容文档正文，图片/CSS/字体/OPF 等条目原样保留 |
| **SRT 字幕** | `.srt` | 一个字幕条目 = 一个翻译段（不切分）；序号与时间轴逐字节保留 |
| 图片 | `.png` `.jpg` `.jpeg` `.bmp` `.webp` | OCR 提取文字后翻译，不做原图还原 |

不支持 `.doc` / `.pdf` / `.xlsx` / `.rtf` 等，导入时会明确报错提示先转换格式。

### 3.2 导出

导出对话框有「导出格式」和「导出模式」两个维度。

**导出格式**：`跟随源文件（默认）` / `纯文本 (.txt)` / `Markdown (.md)` / `HTML (.html)` / `Word (.docx)`

- 「跟随源文件」= 写回原格式（**epub / srt 走这条**）
- 选具体格式 = 跨格式转换（段级数据不变、按段落重排，源格式特有元素会丢失并给出提示）
- epub/srt **不能作为跨格式的目标**（epub 要重建容器、srt 没有时间轴）；
  但 epub 源可以跨格式导出为 md/txt/html/docx

**导出模式**（各格式支持面不同，不支持的组合会明确报错，不会静默降级）：

| 模式 | txt | md | html | docx | epub | srt | 图片 |
|---|---|---|---|---|---|---|---|
| `target`（仅译文） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `bi_inter`（段间交错双语） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `bi_table`（左右表格双语） | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |

**同名文件**：导出时可选「覆盖已有译文（默认）/ 保留两者（自动改名 `xxx(2).md`）/
跳过已存在的文件」，对话框会先告诉你有多少个同名文件。

---

## 4. 怎么用（用户视角）

**首次使用**：新建项目（选语言方向、风格、Provider，可直接填 API 密钥并做连通性测试）
→ 拖入源文件 → 给文件打标签（可选）→ 点「开始翻译」→ 预检确认（含 token 预估，
可选先自动归纳术语）→ 后台翻译（有进度、可取消）→ 校对编辑器逐段校对/确认 → 导出。

**增量使用**：往 `source/` 再放文件 → 「开始翻译」（只翻译新文件/新段落，旧的走翻译
记忆）→ 校对 → 导出。想只翻某个文件，在文件列表选中它后点「翻译选中文件」。

**改了术语译名之后**：校对编辑器 →「术语变更影响…」→ 列表列出仍在使用旧候选的已译
段落 → 「全局替换为新候选」（可撤销）或「标记重译」（下次翻译时重新生成）。
术语可以在软件里改，也可以直接在 Excel 里改 `glossary.csv` —— 重开项目或点
「检查术语表外部修改」后同样能比出差异。

**几个实用点**：
- 密钥存在系统凭据管理器里，**不会写进项目文件**，项目文件夹可整体拷贝分享；
- 「设置」页可拉取平台模型列表；并发数、默认上下文滑窗段数也在那里；
- 「项目设置」页可改语言方向、风格、振假名策略（drop/keep/translate）、上下文滑窗、
  以及「重译过期机器译稿」。

---

## 5. 配置位置

| 内容 | 位置 |
|---|---|
| 全局配置（Provider 列表、并发、默认滑窗、最近项目） | `%APPDATA%/AITranslator/config.json` |
| API 密钥 | 系统凭据管理器（服务名 `AITranslator`，账号 = Provider id） |
| 项目配置 | `<项目>/project.json` |
| 术语表（权威） | `<项目>/glossary.csv`（或 `glossary.txt`；csv 优先） |
| 分段/译文/任务/缓存 | `<项目>/.aitrans/work.db` |

---

## 6. 改代码前必读

1. **适配器契约**（`adapters/base.py`）：`parse(path, opts) -> DocumentModel`、
   `render(out_path, model, translations, mode, ruby_maps)`。
   最关键的**不变量**：`seq` 必须能对同一源文件**确定性重放** —— 导出时会重新 parse
   源文件再按 `seq` 对齐译文。不可译段也要占一个 `seq`。
2. **常见改动落点**：新增格式 = 加一个适配器文件 + 在 `adapters/__init__.py`
   注册扩展名 + 补 `tools/AITranslator.spec` 的 `hiddenimports`（懒加载模块否则打不进 exe）；
   新增 Provider = `llm/` 加文件 + `pipeline.build_provider` 分发。
3. **改了会炸的地方**（详见 `docs/架构导览.md` §10.4）：改 `cfg_hash` 成分会让全书缓存
   失效并触发大规模重译；改分段算法会让导入/导出 `seq` 错位；给 `segments` 加字段必须
   同步升 `SCHEMA_VERSION` 并写迁移分支；空白段绝不能参与翻译记忆/去重。
4. **UI 与引擎分离**：翻译跑在后台线程的独立 asyncio loop，UI 只通过 Qt 信号
   （`Bridge.progress / run_done / toast`）收状态；不要从 UI 线程直接写库。
5. **测试**：`tests/unit/` 覆盖各模块边界，`tests/acceptance/` 跑 Mock Provider 全流程；
   改行为请补回归测试。UI 部分靠 `python main.py --selftest` 冒烟。
6. **文档同步**：代码注释里引用了设计文档章节（`设计 §7.5`、`增补设计 §1.5`）与缺陷
   编号（`反馈 #2`、`审查第2轮`），改代码时请延续这个习惯；行为变更记得更新
   `docs/架构导览.md` 与 `CHANGELOG.md`。

---

## 7. 已知边界（不是 bug）

- docx 段内混合样式（半句加粗）无法逐字保留，译文整段套用段落主样式；
- html / epub 的整个 `<a>` 作为保护占位符（URL 防注入优先），**链接文字不翻译**
  —— 因此 **epub 的目录（nav.xhtml / NCX）里章节名保持原文**；
- epub 跨格式导出会丢弃源格式特有元素（有提示）；
- OCR 目前受中英识别模型限制，日文/韩文图片精度不足（需额外提供该语言模型文件）；
- 超长 SRT 字幕条目（>1500 字符）不切分，不自动重排换行。
