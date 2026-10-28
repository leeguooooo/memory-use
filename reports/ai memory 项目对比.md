# 借鉴 ai-memory 改进记忆质量

对 memory-use 最有价值的借鉴是**用真实召回路径做逐案例评测，以及加强只读的笔记健康检查**，而不是直接引入服务端和自动记录。ai-memory 围绕跨代理交接和团队共享构建，使用事件采集、后台整理和派生索引；memory-use 以用户维护的个人 Markdown 知识为中心，已有来源行号、有界摘要和生命周期提醒。两者目标有交集，但数据来源与运行边界不同。本次比较固定 ai-memory 源码至 `0e5488073d7073caa3181bbaf184102b215dcf66`；没有运行其完整基准，也没有在同一私人语料上比较最终回答质量，不能宣称任一项目的召回效果更好。报告中的建议是研究结论，实际实现状态以工作区变更及测试结果为准。

## 跨代理交接依赖自动采集和有状态服务

ai-memory 的管线由生命周期 hook 采集事件，经整理后写成 Git 版本化 Markdown，SQLite 作为可重建的派生索引。默认流程不依赖 LLM；检索组合全文搜索、实体、图邻居和可选向量等信号。它支持多人共享项目知识，同时将个人交接与共享内容区分；交接有 owner 和领取状态，托管 workstream 还有租约与恢复机制。([README 与架构](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/README.md), [程序接口](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/programmatic-memory.md), [托管 workstream](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/managed-workstreams.md))

memory-use 已按标题切分笔记，对标题、别名和正文加权，并支持故障链接路由；`brief` 给出来源位置、复核提醒和有界上下文。它面向跨机器的个人知识，和自动捕获每次编程会话的产品目标并不相同。([memory-use 源码](https://github.com/leeguooooo/memory-use/blob/main/scripts/memory_use.py), [记忆生命周期](https://github.com/leeguooooo/memory-use/blob/main/docs/memory-lifecycle.zh-CN.md)) 交接协议对需要多代理争抢同一任务的团队有价值，但常驻服务、租约和完整会话采集会增加运行、授权和数据保留成本；当前没有证据表明这些成本能解决 memory-use 的主要问题，建议保留轻量的文件和 Git 模型。

## 召回改动先看多证据题和逐案例退化

ai-memory 的评测器走生产整理路径，对 baseline 与 candidate 做对照，并检查必须出现或禁止出现的内容；其检索对照报告区分至少一个证据命中的 hit@k 和多证据召回比例 recall@k，还报告延迟、分片及逐问题变化。字面包含检查只能验证可观察的保留情况，不能证明答案正确。([评测器](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/evals/README.md), [检索 A/B](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/benchmarks/retrieval-ab-r2.md), [指标定义](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/benchmarks/longmemeval-s-2026-09-01-fts.md))

memory-use 已分别评估排序、入口和预算内正文片段，并明确这些指标不等于最终答案正确。下一步应扩充需要多个来源才能回答的问题：为每题标记必要来源和事实，分别记录至少命中一项、必要证据召回比例，以及最终 brief 在既定预算下保留了哪些事实；同时输出每题的新增命中与退化、耗时和输出长度。测试题应放在搜索范围外，避免被检索器直接命中。([现有评测说明](https://github.com/leeguooooo/memory-use/blob/main/docs/recall-evaluation.md)) 这会让 CJK 分词、融合排序或图扩展成为可对照的实验，而不是先重写检索再猜收益。上游的 CJK 词法流和融合方案值得作为候选，但无法从上游指标推断它在本项目中文问题与笔记上的效果。([上游检索实现](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-store/src/reader.rs))

## 只读 lint 能改善笔记可维护性

本地检查发现，现有 lint 用正则扫描 Markdown 链接，围栏代码中的示例也可能被当作真实链接；它检查路径是否存在，但不核对目标标题锚点。对个人知识库来说，这会让修复提示混入误报，也会漏掉路径存在但章节已改名的链接。适合优先修复的是识别代码围栏和行内代码、保守处理相对路径与百分号编码，并在定义清楚支持范围后检查页内和跨页标题锚点；仍保持只读，不自动改写笔记。测试应覆盖有效和失效链接、代码示例、重复标题、编码路径以及普通和 strict 返回码，且使用隔离样例。完整 Markdown 有转义、HTML 和自定义锚点等复杂情况，不能把局部解析宣称为完整兼容。([memory-use 源码](https://github.com/leeguooooo/memory-use/blob/main/scripts/memory_use.py))

上游也提供规则检查与链接守卫，但其 link guard 处理的是生成内容中的跨作用域链接，并非通用 Markdown 断链检查，不能直接照搬。重复标题检查同样应谨慎：memory-use 已有同专栏近似重复检查，而不同目录下的“安装”或“配置”同名并不意味着内容重复。([上游链接守卫](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-consolidate/src/link_guard.rs), [上游 lint](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-consolidate/src/lint.rs))

## 自动提炼需要来源、恢复和明确的数据边界

ai-memory 的压缩、合并、分层衰减和 LLM 后台整理属于可选能力；原版本保留在 Git 中。这个设计说明自动整理可以先作为可恢复的试验，但 Git 历史只能帮助恢复，不能证明提炼后的事实完整。若 memory-use 将来支持自动整理，应先提供预览、来源追溯与恢复路径，再用必须保留和禁止出现的事实做回归；在此之前，继续由用户控制知识写入更符合现有定位。([上游 README](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/README.md))

上游采集代码先脱敏再截断，避免密钥恰好跨截断边界时留下无法识别的片段。其数据处理文档也明确脱敏只是尽力而为，配置 embedding、LLM 或 webhook 后正文可能发往外部。对 memory-use，应检查每条实际输出和写入路径的扫描与长度限制顺序，并用跨边界样例测试；不能根据上游的设计推断本项目存在同一漏洞，也不能把脱敏当作绝对安全。([上游 payload 实现](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-hooks/src/payload.rs), [数据处理说明](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/DATA_HANDLING.md))

## 决策

先修复有复现样例的 lint 误报和漏报，再完善多证据召回基线；任何排序或自动整理变更都用同一批隔离样例比较，并公开逐题退化和预算影响。服务器、多用户交接、访问衰减与后台会话采集暂不纳入默认架构。没有实际用户语料下的对照结果之前，不应把上游功能或其公开基准当作本项目收益的证明。

## 本次落地

工作区已新增多文件证据评测，输出每项证据的排名和 brief 保留状态，以及 recall@3 和预算内证据保留比例；它衡量文件和片段，不验证事实是否完整。lint 已跳过围栏与行内代码，支持百分号编码路径；目前不校验锚点、引用式链接或缩进代码块。测试结果以交付说明为准。

后续增加 `compare_recall.py`，对同一预算和题集的两份评测结果逐题比较，包括证据位置互换但平均值不变的情况。比较工具不能确认笔记快照一致；操作者仍需固定数据。既有合成语料的首轮必要片段结果为开发问法 5/5、额外问法 6/6；这些是已有样例的回归结果，不能作为真实用户问题的准确率。

进一步借鉴上游的显式矛盾关系：笔记作者可在前八行写 `> 冲突：[另一篇](other.md)`，读取、搜索、brief 和 lint 会提醒核对。它不推断冲突、不自动判定真伪；路径失效由链接检查另行报告。上游还提供按入库时间查询历史、分层启动摘要和不自动领取的交接；这些值得继续按具体需求验证，不能把 Git 提交时间直接等同事实生效时间。
