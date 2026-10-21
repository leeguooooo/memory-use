# GitHub 同类项目对照

2026-10-21 核对 GitHub 主分支并读取下列固定版本的源码。后续已运行 MCP memory 的 91 项存储测试，以及 QMD、Basic Memory、Hindsight 的选定原语，详见[故障实验](deep-experiments.md)。完整产品的问答质量与模型效果尚未比较。

| 项目 | 在这里比较什么 | 源码版本 |
|---|---|---|
| [QMD](https://github.com/tobi/qmd) | Markdown 本地检索组件，邻近能力 | `86d2365d17fc682e491c2c53d1e4a884aee8682a` |
| [Basic Memory](https://github.com/basicmachines-co/basic-memory) | Markdown 记忆、派生索引、上下文生成 | `976a10c8977345e1f84b9d1a1557a0e2d0bad540` |
| [MCP memory server](https://github.com/modelcontextprotocol/servers/tree/51b6382a3e827aceb6bce430b1208d69b93d95ea/src/memory) | 官方参考实现，作为存储正确性的对照 | `51b6382a3e827aceb6bce430b1208d69b93d95ea` |
| [Mem0](https://github.com/mem0ai/mem0) | 对话事实提取与写入结果处理 | `2c81af3bbe27d9bccb09c772d583df9e9be72ee5` |
| [Hindsight](https://github.com/vectorize-io/hindsight) | 多路召回、候选预算和诊断 | `a7ab53c84a847664d3377dfb7987d3e6bbaf4b17` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Git 文件记忆、待推提交、同步失败恢复 | `c25f091d0fce1f032e5cdf67ce131d5b474e6576` |

## QMD：先保护已经找对的结果

[`hybridQuery`](https://github.com/tobi/qmd/blob/86d2365d17fc682e491c2c53d1e4a884aee8682a/src/store.ts#L6264) 先探测关键词结果。第一名足够强、又明显领先第二名时，跳过昂贵的查询扩展；指定 intent 时则关闭这条捷径。过滤条件直接进入初始检索，避免库外强结果影响判断。后续对候选片段重排，而非把整篇长文交给模型。

其 [`reciprocalRankFusion`](https://github.com/tobi/qmd/blob/86d2365d17fc682e491c2c53d1e4a884aee8682a/src/store.ts#L5371) 按名次融合多路结果，原问与扩展问法有不同权重。它用的强命中阈值依赖自身分数定义，不能搬到 memory-use 的分数上。

可借鉴的是引入新检索通道时保留精确查询的价值，并先确认在哪个阶段丢掉答案。memory-use 已能区分未检索、brief 筛选、预算或片段不足。本轮不加入扩展模型或 RRF：当前尚无第二条经过独立评测的检索通道。对机器名、端口和错误码的回归应继续单独保留。

## Basic Memory 的多文字检索处理

[`script_ngrams.py`](https://github.com/basicmachines-co/basic-memory/blob/976a10c8977345e1f84b9d1a1557a0e2d0bad540/src/basic_memory/repository/script_ngrams.py) 在应用层处理无可靠空格词界的文字：NFKC 规范化、文字区间识别、组合字符处理、双字序列，再将派生搜索文本交给数据库。显式布尔查询保留原有解释方式。序列匹配增加字符串召回能力，不等于理解自然提问的含义。

[`sqlite_search_query.py`](https://github.com/basicmachines-co/basic-memory/blob/976a10c8977345e1f84b9d1a1557a0e2d0bad540/src/basic_memory/repository/sqlite_search_query.py#L509) 还提供受控的严格查询失败重试：仅在调用方启用且严格查询没有结果时放宽；不是每次把所有词都 OR 起来。[共享查询规则](https://github.com/basicmachines-co/basic-memory/blob/976a10c8977345e1f84b9d1a1557a0e2d0bad540/src/basic_memory/repository/search_query.py#L304) 会约束哪些输入允许放宽。

本轮采用其中最小且可复现的思路：统一字符比较。使用 Python 标准库独立实现搜索文本的 NFKC + 小写转换，覆盖输入、词汇表、显式入口和钩子；不重写笔记和真实路径。没有复制该项目源码，也没有引入它的数据库或双字索引。其固定版本的 LICENSE 为 AGPL-3.0。

双字索引是否能改善两条已知整句提问，需要另外验证。特别是双字匹配带来的误召回、混合英文/数字的精确边界和性能，不能凭另一个项目“有中文搜索”就视为解决。

## MCP 参考实现对并发写入的处理

[`withFileLock` 和 mutation queue](https://github.com/modelcontextprotocol/servers/blob/51b6382a3e827aceb6bce430b1208d69b93d95ea/src/memory/index.ts#L239) 分别串行化跨进程与进程内的修改。锁覆盖加载、修改、保存整个过程；仅在写入最后一刻上锁并不能防止两个进程基于旧副本互相覆盖。

[`saveGraph`](https://github.com/modelcontextprotocol/servers/blob/51b6382a3e827aceb6bce430b1208d69b93d95ea/src/memory/index.ts#L437) 写同目录临时文件，再 rename 替换，并保留权限、在失败时清理临时文件。原子替换解决读者看到半截内容的问题；不能把它和互斥更新、断电持久性混为一谈。其 [`searchNodes`](https://github.com/modelcontextprotocol/servers/blob/51b6382a3e827aceb6bce430b1208d69b93d95ea/src/memory/index.ts#L656) 仍是简单子串匹配，官方参考实现不意味着检索最适合我们的用途。

memory-use 的笔记用 Git 保存，本地 hook 状态另外维护。后续隔离进程实验已复现计数丢失，修复覆盖整个状态读改写过程，并对日志追加、裁剪互斥；进程被杀、替换失败和损坏文件也有回归。状态和日志仍不是双文件事务，锁分支的验证范围见[实验记录](deep-experiments.md)。

## Mem0：提取、归因和落盘分别看

固定版本的 [`_add_to_vector_store`](https://github.com/mem0ai/mem0/blob/2c81af3bbe27d9bccb09c772d583df9e9be72ee5/mem0/memory/main.py#L881) 在推断分支检索已有记忆，再调用模型提取新增事实。这个版本使用 additive extraction，不能照旧介绍把该入口描述成模型自动执行 ADD/UPDATE/DELETE 的统一流程；显式更新仍有独立路径。

其[提取提示](https://github.com/mem0ai/mem0/blob/2c81af3bbe27d9bccb09c772d583df9e9be72ee5/mem0/configs/prompts.py#L468) 区分用户陈述和助手建议的归因。提示词要求不能当成实现已经消除了归因错误的证明。写入阶段只把确认存储成功的记录加入返回结果，批量写失败会逐条尝试；历史记录又是另一条存储路径，不能据此假定向量库和历史库始终原子一致。

我们采用归因约束：笔记要分清实测结果、用户报告和尚未执行的方案，并保留来源和核对时间。两份技能说明已补入，未新增必填元数据或自动对话提取。现有 `save` 也必须继续区分本地提交成功与推送成功。

## Hindsight 对不同召回任务采用不同策略

[`fusion.py`](https://github.com/vectorize-io/hindsight/blob/a7ab53c84a847664d3377dfb7987d3e6bbaf4b17/hindsight-api-slim/hindsight_api/engine/search/fusion.py) 在融合前限制单通道候选数，避免一个后端挤占全部预算。除 RRF 外还提供交错融合：用于去重时，最相近旧记忆只在一条通道排第一，也应获得被查看的机会。说明“回答问题的排序”和“写入前查重的排序”可能需要不同目标。

[`fact_budget.py`](https://github.com/vectorize-io/hindsight/blob/a7ab53c84a847664d3377dfb7987d3e6bbaf4b17/hindsight-api-slim/hindsight_api/engine/fact_budget.py) 按排名挑选完整事实，单条过大可以跳过，继续容纳后面的短事实；若所有事实都过大，会允许返回第一条并超出预算。这个兜底与 memory-use 已承诺的严格字符上限不同，不能直接移植。

[`SearchTracer`](https://github.com/vectorize-io/hindsight/blob/a7ab53c84a847664d3377dfb7987d3e6bbaf4b17/hindsight-api-slim/hindsight_api/engine/search/tracer.py#L31) 记录各检索阶段、融合、重排与最终结果；详细正文和向量跟踪是可选的。我们先使用已有逐题失败阶段诊断，不增加常驻遥测或正文日志。没有多路检索时，也不需要为了使用融合算法而先创建更多通道。

## 已验证的改动和剩余问题

[进程并发实验](repository-concurrency-study.md)进一步运行了 Letta 的实际文件锁、Git 目录与进程身份模块，5 项检查通过；memory-use 的 6 项定向场景从 2 项到 6 项通过，三个写入入口共用 checkout 锁。外部 Git 和跨电脑不在锁协议内。

后续 [Letta 同步研究](letta-sync-study.md)执行了上游同步函数的 9 个合成依赖分支，并用真实本地 Git 复现 memory-use 的错误返回码、无变化重试不推送、autostash 影响其他会话、冲突残留等路径。`save`、`sync` 与 autosync 已共用保守同步实现；定向恢复回归从 1/9 到 9/9。不引入 Letta 的后台修复代理。

本机现有笔记中，ASCII 写法 `Hopper`、`190`、`WARP` 原本都有结果；对应全角写法原本全部没有结果。规范化后，三组查询的全部匹配片段、顺序和分数分别完全一致。数字仍按完整 token 匹配，`１９０` 不会借此命中 `1900`。组合方式不同的重音字符也有隔离回归覆盖。

字符规范化阶段的 8 个场景保持 6/8，MRR 约 0.761；它没有改善整句理解。后续利用已有故障表的显式链接，目标条目与片段覆盖已到 7/8，具体范围与答案片段检查见[召回评测](recall-evaluation.md)。各次只读评估未修改笔记，也没有上传私人正文。

当前优先级：

1. 字符规范化：已独立实现，不引入依赖，核对全角数字边界、别名去重、显式链接、钩子和原文展示。
2. 来源和实测状态：已更新技能约束，不把建议写成已完成配置。
3. hook 状态并发/中断：已复现并修复，详见进程和故障回归。
4. 整句检索：无序双字兜底未采用；已利用故障表显式链接，加入实体、否定和来源证据检查，仍需更大的独立样本。
5. 多路召回、模型重排、自动提取与数据库：没有足够收益证据，本轮不引入。

当前工程验证：Mac 190、Python 3.14.7，231 项 memory-use 测试通过；同步恢复阶段为 225 项，召回阶段为 216 项；前一阶段 MCP 上游另有 91 项定向测试通过。原语输入、结果与源码校验值见[实验记录](deep-experiments.md)，最新路由见[召回评测](recall-evaluation.md)。未安装模型；上游测试依赖仅在隔离目录安装。Linux、Windows 和 Python 3.9 尚未重跑。改动保留在工作区，未提交或发布。
