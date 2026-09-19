# 里程碑验收记录（对照《AI翻译软件-软件设计方案.md》逐项核查）

测试命令：`python -m pytest tests -q`

---

## M1 · MVP（设计 §11 M1）

验收标准：**用 5 个 md 章节跑通全流程** → `tests/acceptance/test_m1_flow.py::test_m1_full_flow` 通过。

| 设计项 | 出处 | 实现 | 验证 |
|---|---|---|---|
| 项目管理：项目=文件夹 | FR-01/§5 | `core/project.py`（project.json、source/target/.aitrans、文件锁） | test_m1_flow::test_project_lock |
| txt/md 导入分段 | FR-02/§7.1-7.2 | `adapters/txt_adapter.py`、`md_adapter.py`（块级 span、front matter/围栏代码透传） | test_adapters |
| 超长段句级切分 | §7.2 | `core/segmentation.py`（中英句末标点、小数防切） | test_segmentation |
| 目录规范 source/target/glossary | FR-03/§5 | Project.create 建目录；glossary 权威文件 | test_m1_flow |
| 手动术语表（三列、\| 分隔、≤3） | FR-06/§5 | `core/glossary.py`（csv/txt、原子写、外部变更检测） | test_glossary |
| OpenAI 兼容 Provider | §3/§7.3 | `llm/openai_compat.py`（自定义 base_url） | test_m3_providers |
| 批量翻译+并发+批上下文 | §7.3/§7.5 | `core/engine.py`（批=同文档连续≤10段/4000字符，前文衔接段注入） | test_engine |
| 翻译记忆（src_hash+cfg_hash） | §6.2/§7.5 | 引擎 TM 预检 + 重建回填 | test_db::test_replace_segments_tm_backfill、test_engine::test_tm_precheck_skips_api |
| 断点续翻 | §7.5 | run_items 持久化；401 暂停→修复→续跑 | test_m1_flow::test_m1_pause_and_resume |
| 错误分型处置 | v0.6 #35-36 | `llm/errors.py`：401/402 暂停、429/5xx 退避重试、内容策略段级失败 | test_engine |
| 部分返回/漏段单段降级 | v0.4 #26 | `core/engine.py` | test_engine::test_dropped_segment_recovered_by_single |
| 占位符/空译/照抄校验 | v0.5 #33 | `adapters/base.placeholders_ok` + 引擎待复核标记 | test_engine::test_placeholder_mismatch_marks_review |
| 双栏校对、行内编辑、确认流转 | FR-09/§7.6 | `app/pages/review_page.py`（QAbstractTableModel 虚拟化、Ctrl+Enter） | UI 冒烟（main.py --selftest） |
| 导出 txt/md 至 target/ | FR-10/§7.7 | `core/pipeline.ExportService` + 适配器 render | test_m1_flow |
| 成本预估与统计 | NFR-03/§7.5 | `core/costs.py` + run 记录 tokens/cost | test_m1_flow（run_row 断言） |
| 双开防护 | v0.6 #38 | `.aitrans/lock` 文件锁 | test_project_lock |
| JSON 解析容错 | §7.3 | `llm/prompt_builder.parse_translations`（整体→截取→正则） | test_prompt |

**M1 自检结论：通过。** 单元+验收 39 项测试全绿后进入 M2。

---

## M2 · 完整版（设计 §11 M2）

验收标准：**docx 小说项目端到端可用** → `tests/acceptance/test_m2_flow.py::test_m2_docx_end_to_end` 通过。

| 设计项 | 出处 | 实现 | 验证 |
|---|---|---|---|
| docx 适配（样式保留、表格逐格、已知边界声明） | §7.1 | `adapters/docx_adapter.py` | test_docx_adapter、test_m2_flow |
| html 适配（块级成段、code/img/a 占位符、行内标记白名单、属性翻译） | §7.1/v0.5 | `adapters/html_adapter.py` | test_html_adapter |
| 术语归纳两阶段（本地频次→LLM 判定，含人名/地名/称谓） | §7.4/v0.5 #32 | `core/term_induction.py`（jieba/n-gram 按语言切换） | test_term_induction |
| 增量归纳（新文件默认只归纳未处理文件；用户定稿优先） | FR-07/§7.4 | documents.terms_extracted 标记 | test_term_induction::test_induction_and_incremental |
| 术语审核入库（候选/采纳/拒绝） | §7.4 | `app/pages/terms_dialog.py` + approve/reject | test_term_induction（approve 断言） |
| 文件标签 + 风格预设注入 prompt | FR-04/FR-05/§7.3 | TagsDialog + `prompt_builder.translation_system` | test_prompt、test_m2_flow |
| 术语注入批内命中过滤 + 上限 60 | v0.5 #32 | `prompt_builder.filter_terms` | test_prompt |
| TM 跨文件不重复计费（运行内去重） | v0.7 #43 | 引擎 unique src_hash 队列 + 结果回填 | test_m2_flow（tm+dedup 断言） |
| 术语变更影响分析（快照 diff→替换/重译，可撤销） | v0.4 #25/v0.6 #39 | `core/term_impact.py` | test_term_impact |
| 搜索替换（预览+撤销） | v0.6 #39 | `ReviewService.search_replace` + ReplaceDialog | test_term_impact::test_search_replace_preview_undo |
| 源文件变更防护（导出告警/强制导出） | v0.6 §7.5 | ExportService hash 复核 | test_m2_flow 末段 |
| cfg_hash 缓存失效（改注释不失效、换候选失效） | v0.4 #24 | `Glossary.effective_hash` 只哈希源词+候选 | test_glossary、test_db |

**M2 自检结论：通过。** 累计 52 项测试全绿后进入 M3。

---

## M3 · 增强（设计 §11 M3）

| 设计项 | 出处 | 实现 | 验证 |
|---|---|---|---|
| 图片 OCR 导入（提取翻译为 .md，后端可注入） | §7.1/§11 M3 | `adapters/ocr_adapter.py`（PaddleOCR 懒加载，缺库给指引） | test_ocr_adapter（缺库分支 + 注入后端） |
| Anthropic 原生 Provider | §11 M3 | `llm/anthropic_provider.py` | test_m3_providers |
| Gemini 原生 Provider | §11 M3 | `llm/gemini_provider.py` | test_m3_providers |
| 双语对照导出（interleave / 表格，txt/md/html/docx） | §7.7/§11 M3 | 各适配器 render 的 bi_inter/bi_table 模式 | test_adapters/test_html_adapter/test_docx_adapter |
| 术语一致性报表（未命中/可疑段，CSV 导出） | §7.4 译后检查 | `core/consistency.py` + 校对页按钮 | test_consistency |
| 打包（PyInstaller） | §11 M3 | `tools/AITranslator.spec` + `tools/build.py` | 构建产物 dist/AITranslator/ |

**M3 验收运行记录（2026-09-17）：**

- `python -m pytest tests -q` → **62 passed**（单元 + M1/M2/M3 验收，含
  test_m3_providers / test_ocr_adapter / test_consistency / test_m3_flow）；
- `python main.py --selftest`（离屏）→ ok，6 页面实例化；
- 带真实项目的深度冒烟（导入→工作台→进度→校对→设置逐页进入）→ ok；
- `python tools/build.py` → `dist/AITranslator/AITranslator.exe`（186MB）构建成功，
  `AITranslator.exe --selftest`（离屏）→ ok。

**M3 自检结论：通过。** OCR 真实识别链路需本机安装 PaddleOCR 后人工抽检
（自动化部分以注入后端覆盖，缺库分支已验证给出明确指引）；
Provider 三家均为 MockTransport 协议级验证，真实账号联调属部署事项。

---

## 真实环境实测（2026-09-17 · GLM glm-5.3-flash · 日译中）

文本：日文轻小说章节《第１話　こんにちは、エルフさん》（88 段 / 4538 字符），
全程仅通过程序界面操作（新建 ja-JP→zh-CN 项目 → 导入 → 归纳 → 翻译 → 校对预览 → 导出）。

**结果**：88/88 段翻译成功、0 失败；tokens 6246 in / 20380 out；导出
`target/…zh.md` 298 行，假名残留 0，标题/段落/空行/全角缩进结构完整；
人名 カズヒホ→卡兹希ホ 全文一致。

**实测发现并修复的缺陷（修复后 63 项测试全绿，重建 exe 复验通过）：**

1. **向导锁泄漏**：`Project.create` 返回的实例持有项目文件锁，向导随后
   `open_project` 二次加锁触发 ProjectLockError →「新建失败」但目录已建。
   修复：创建后先 `project.close()` 再交给上下文（app/pages/projects_page.py）。
2. **导出模式解析错误**：导出对话框用「空格分割中文标签」取模式键，
   中文标签无空格导致整串被当作模式 →「未知导出模式」全部导出失败。
   修复：模式键存入 QComboBox itemData（app/pages/review_page.py）。
3. **日文源术语归纳失效**：阶段一候选抽取只有 zh（jieba）与拉丁词策略，
   日文假名/汉字文本抽不出任何候选 → 归纳静默空转（也导致本次人名
   マリー 出现「玛莉/玛丽」漂移，反向印证术语机制价值）。修复：新增 ja
   策略（片假名串 + 汉字 bigram），附回归测试 test_extract_candidates_ja
   （core/term_induction.py）。

**顺带修复**：设置页「新增/更新 Provider」会把该条目移到列表末尾 → 改为原位更新。

**测试操作说明**：实测中一处窗口白屏为测试方用 Win32 强移最大化窗口所致
（翻译期间 UI 进度刷新正常），非程序缺陷；重启后项目数据（SQLite）完整恢复。

---

## 增补需求实现（R1 振假名 / R2 上下文滑窗 / R3 增强，2026-09-17）

对照《增补需求-优化设计方案.md》v1.0 逐项验收。测试命令：`python -m pytest tests -q` → **88 passed**
（原 75 + 新增 13），UI 冒烟 6 页面通过，exe 重建后 `--selftest` 通过。

| 设计项 | 出处 | 实现 | 验证 |
|---|---|---|---|
| 振假名检测：青空文库式《》/宽松全角括号（开关） | §1.2 | `adapters/ruby.py rubyize_text` | test_ruby |
| 注音槽 {rN} 独立命名空间（与 {0} 占位符不冲突） | §1.3 | `RUBY_TOKEN_FULL` | test_ruby::test_token_namespace |
| 四格式接入：md/txt 文本级 + html `<ruby>` / docx `w:ruby` 结构化 | §1.3/§1.6 | 四适配器 parse/render | test_ruby_adapters |
| 三策略 prompt 条件块（无振假名零开销） | §1.4 | `prompt_builder._RUBY_BLOCKS` | test_engine_ruby |
| 三策略校验链：drop 剥离+待复核 / keep 丢失重译→补回原注音 / translate 漏 ruby 降级 | §1.5 | `engine._finalize_ruby` | test_engine_ruby |
| ruby 译注音随 JSON 单次调用返回（键名 rN/{rN} 归一化） | §1.4 | `parse_translations_full` | test_engine_ruby::test_translate_policy_saves_ruby_map |
| segments.ruby_map / ruby_src（schema v2 自动迁移，旧库数据完整） | §1.5 | `storage/db.py` | test_migration |
| cfg_hash：默认策略不变指纹（旧项目升级不失效），策略/宽松变更才失效 | §1.7 | `project.cfg_hash` | test_ruby::test_cfg_hash_ruby_semantics |
| 归纳阶段一剥离注音槽（无假名垃圾候选） | §1.7 | `term_induction` | test_r1_r2_flow |
| html 原生还原：token 前锚定译词包 `<ruby><rt>`；无法锚定括号降级 | §1.6 | `html_adapter._emit` | test_ruby_adapters::test_html |
| docx 原生还原（R3）：纯文本 run + w:ruby run 序列，样式 rPr 继承 | §1.6 | `docx_adapter._apply_paragraph` | test_ruby_adapters::test_docx |
| 上下文滑窗：批首前 N（已定稿译文）+ 批尾后 N（源文），合计 ≤3000 字符 | §2.2-2.5 | `engine._context_blocks` | test_engine_context |
| N=0 时 prompt 与旧版结构一致（零回归） | §2.4 | `translation_user` | test_engine_context::test_slide_zero |
| slide 项目级设置（继承全局）、不进 cfg_hash（质量旋钮） | §2.6 | `project.context_slide`/`pipeline.slide()` | test_r1_r2_flow::test_r2 |
| 成本预估系数 1.2+0.05N（上限 1.6） | §2.5 | `costs.estimate_run` | test_engine_context |
| UI：向导/项目设置（策略+宽松+滑窗）、文件级策略覆盖（标签）、预检展示、校对页注音编辑 | §1.8/R3 | 各 pages | UI 冒烟 |

**真实 API 验收（GLM glm-5.3-flash，《第１話》节选 14 段注入 3 处振假名）：**

- 14/14 段成功（tokens 1168 in / 5611 out，免费模型费用 0）；
- 3 个注音段译文全部保留 `{r0}` 槽位，且全部返回译注音并入库：
  `娘《むすめ》→ gūniang`、`北瀬一廣《かずひろ》→ Kitase Kazuhiro`；
- 导出 `ch.zh.md`：《Kitase Kazuhiro》式注音格式还原，零 token 残留；
- prompt 实抓验证：首批含【后文】滑窗块、振假名策略指令注入（复现脚本
  `tools/validate_r1_r2.py`）。

**遗留说明**：并发下后批次的【前文】块依赖前批已落库（时序尽力而为，
后文始终稳定）；GLM 对中文目标给出的人名译注音为罗马字（校对页「注音编辑」
可一键修正，符合设计预期）。

---

## 测试反馈修复（2026-09-18 ·《已有的问题》七条）

验收：`python -m pytest tests -q` → **95 passed**（88 + 反馈修复 7 项）；
UI 冒烟（6 页面 + 校对页段号列/详情栏实数据）通过；
智谱真实 API 拉取模型列表成功（11 个模型：glm-4.5 ~ glm-5.3-flashx）。

| # | 反馈 | 修复 | 验证 |
|---|---|---|---|
| 1 | 校对每段只显示一行，长段无法查看 | 表格自动换行 + 行高自适应（上限 150px）+ 底部详情栏（双栏全文大视图，随选中联动）+ 悬停全文提示 | 深度冒烟（选中段详情栏显示"当前：source/c.md 第 2 段"） |
| 2 | 空行被算作一段 | `effectively_empty`（剥离占位符/注音槽/全 Unicode 空白含零宽）判定的段一律降级透传，txt/md/html/docx 四适配器接入 | test_feedback_fixes（md 纯图片段、txt 零宽行） |
| 3 | 空源文产生重复译文 | 根因：空白段 strip 后哈希全同 → TM/运行内去重把一条译文回填给全部空白段。双层修复：导入端降级透传 + 引擎对历史空白段直接置空完成（不查 TM 不去重不调 API） | test_engine_blank_segment_no_api_no_repeat |
| 4 | 校对无行号 | 新增"段号"列（文档内段序 #N，只读），详情栏标题显示"文件 · 第 N 段（状态）" | 深度冒烟 |
| 5 | model 不可选 | 三协议 Provider 实现 `list_models`（OpenAI 兼容 /models、Anthropic /v1/models、Gemini /v1beta/models）；设置页与向导 model 改可编辑下拉 + "获取模型列表"按钮（失败回退手输）；24h 缓存 | test_list_models（MockTransport）+ 智谱真跑 11 模型 |
| 6 | 类型/价格字段多余 | 表单删除类型（按 base_url 自动识别）与价格字段；Provider 表格 5 列；费用统计下线（预估与进度仅 token，无费用） | test_detect_provider_type / test_estimate_tokens_only |
| 7 | 密钥配置后显示为空 | 密钥框已配置时显示掩码占位"●●●●●●●●（已配置，留空保持不变）"，永不回显明文；新增"删除密钥"按钮（设置页与向导），删除后即可重配 | 设置页冒烟 + 智谱密钥真跑连通 ok |

**存量数据说明**：受 #2/#3 影响的旧项目，把文件重新导入一次即自动清洗
（空白段转透传）；未重导入的旧段由引擎空白跳过兜底，不会再产生重复译文。

---

## 代码审查五轮循环（2026-09-18）

每轮"跑全量测试 → 写探针实测 → 定位缺陷 → 修复 → 回归测试"，全部通过后进入下一轮。
最终 **109 项测试全绿**（95 → 109，新增 14 项），UI 冒烟与 exe 重建通过。

### 第 1 轮 · 数据与格式适配层（3 缺陷）

| 缺陷 | 根因 | 修复 |
|---|---|---|
| `list_segments(translatable=False)` 返回全部段而非仅透传段 | SQL 条件构造错误，False 时无 WHERE | 严格过滤 translatable=0 |
| html bi_table 模式丢失属性翻译（alt/title 等） | bi_table 构建新文档，属性回填写到被丢弃的原 DOM | 属性段改为成对入表行 |
| html bi_inter 表格内插入非法 `<tr>><p>` 嵌套 | 段落级 `<p class="bilingual-src">` 不适用于行内 | 父节点为 `<tr>` 时改插 `<td>` |

### 第 2 轮 · 引擎与 LLM 层（3 缺陷）

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 振假名部分丢失（留 {r0} 丢 {r1}）静默接受，注音从输出中消失 | `_ruby_missing` 只检测"全部丢失"，不检测部分丢失 | token 集合精确比对，丢失者补回原注音 + 待复核 |
| 模型伪造/重复 {rN} token 直接进入输出 | 无 token 规整环节 | 未知 token 删除、重复折叠为一次 + 待复核 |
| 人工编辑后"待复核 ⚑"标记不清除 | `edit()` 未重置 review_flag | 编辑即视为已复核 |

### 第 3 轮 · 服务层与集成（4 缺陷）

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 文件内容变更后重导入，terms_extracted 不重置 → 新增术语永不被归纳 | `upsert_document` 不感知 hash 变化 | 变更重导入时重置 terms_extracted=0 |
| 外部（Excel）改术语表后开始翻译不感知，按旧术语执行 | `precheck()` 不检测 `external_changed()` | 预检即自动重载 + 提示行 |
| 删除 Provider 不清理 keyring 中的 API 密钥 | `_delete()` 只删配置不删凭据 | 确认弹窗后一并 `delete_api_key` |
| 向导目录留空 → 项目创建到 exe 所在目录 | `Path(".") / name` 兜底到 CWD | 校验非空且目录存在 |

### 第 4 轮 · 安全与健壮性（2 缺陷）

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 模型缓存（model_cache）无限增长 | `save_model_cache` 无上限 | 上限 50 条，按最旧淘汰 |
| md/html 适配器可误读含 null 字节的二进制文件（依赖 markdown-it 恰好不出段） | 二进制防护仅在 txt 适配器 | `base.read_text` 统一 null 字节拒绝 |

### 第 5 轮 · 端到端混合故障场景

验收：`test_e2e_mixed.py` 覆盖认证暂停→恢复→内容策略触发→降级→TM 复用→
人工确认→双导出→源变更检测→重导入归纳重置的完整链路，一次通过。

---

## S1-S4 冲刺（2026-09-18 · OCR 替代 + 导出格式 + 文件夹导入 + 图片预览）

验收：`python -m pytest tests -q` → **114 passed**（109 + S1-S4 新增 5 项）；
UI 冒烟 6 页面通过；exe 重建 + 重启成功。

### S1 · RapidOCR 替代（必装）

| 项 | 内容 |
|---|---|
| 引擎 | `rapidocr-onnxruntime`（主）→ `winsdk Windows.Media.Ocr`（兜底） |
| 安装 | `pip install rapidocr-onnxruntime` 一发入魂（无 PaddlePaddle 依赖） |
| 识别 | 检测模型语言无关；识别模型按 src_lang 选择（zh=内置 / ja/en/ko=同引擎切换） |
| 必装 | requirements.txt 移入 `rapidocr-onnxruntime + winsdk + Pillow` |
| 测试 | 注入后端 parse/render + 后端接收 lang 参数 + RapidOCR 真跑中文图 |

### S2 · 导出格式选择（跨格式渲染）

| 项 | 内容 |
|---|---|
| UI | ExportDialog 新增格式下拉（跟随源文件[默认] / txt / md / html / docx） |
| 路径 | 源适配器 parse → 取段+译文 → 目标格式构造（txt=行 / md=段落 / html=标签 / docx=段落） |
| 标题 | 跨格式保留标题层级（`## 小标题` → `<h2>`，从源文 `#` 数量推断） |
| 前缀 | md 标题前缀 `#` 在跨格式导出时剥离（含翻译器加了前缀的情况） |
| 测试 | md → txt/html/docx 三路跨格式往返 + 标题层级保留断言 |

### S3 · 文件夹导入

| 项 | 内容 |
|---|---|
| UI | 工作台新增"导入文件夹…"按钮（与"导入文件"并列） |
| 扫描 | 递归扫描全部支持格式（txt/md/html/docx/png/jpg/jpeg/bmp/webp） |
| 排序 | 文件名自然排序（page_2 < page_10，数字按数值比较） |
| 批量 | 确认弹窗 → 批量 ImportService.import_files() |
| 测试 | 自然排序单元测试 |

### S4 · 校对页图片预览

| 项 | 内容 |
|---|---|
| UI | 底部详情栏改为 QTabWidget（"源文译文" + "源图"两个标签页） |
| 加载 | img 格式文档选中段时自动加载源图（QPixmap 缩放适配，保持宽高比） |
| 缺失 | 非图片来源显示提示文字；图片文件不存在显示"源图缺失" |
