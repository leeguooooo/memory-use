# ai-memory 上游架构与可借鉴实践

研究快照：2026-10-27 检出的 upstream main，commit `0e5488073d7073caa3181bbaf184102b215dcf66`。下列链接固定在该 commit；未独立运行上游完整 benchmark。

## 架构和技术边界是什么？

### Takeaway
ai-memory 面向项目级、跨 agent 和多人协作的自动捕获与交接；memory-use 面向人维护的可携带个人笔记，产品边界不同。

### Cited Findings
- 上游用 hooks 捕获事件，consolidation 形成 Git 版本化 Markdown，SQLite 为派生索引；默认零 LLM，单进程单写入者，召回融合 FTS、实体、图邻居、可选向量及 observation evidence。 [架构和 README](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/README.md#architecture)
- 上游交接是有 owner、claim-once 的状态协议；managed workstream 还使用租约和崩溃恢复。 [程序接口](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/programmatic-memory.md)；[workstreams](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/managed-workstreams.md)
- CJK 词法流并入普通 Unicode FTS，再融合其他候选；实现和适用范围应以代码为准。 [检索实现](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-store/src/reader.rs)
- 自动压缩、合并、分层衰减与 LLM dream 均为可选；Git 保留原版本可恢复。 [README](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/README.md)

### Inferences
- 不宜直接复制常驻服务器、原始会话采集或自动改写个人记忆：引入隐私、运行和同步复杂度，且当前需求未证明这些能力的必要性。
- RRF、CJK 索引是候选实验而非已证明的改进；当前 memory-use 已有规范化、子串、glossary 与症状显式路由，需在真实问题上比较。

### Gaps
- 未运行上游服务、未评测其在本项目私人语料和中文问题上的表现，因此不能把其检索指标直接映射到本项目。

## 哪些工程方法值得优先借鉴？

### Takeaway
优先借鉴可重复评估和防止截断泄漏的原则；针对候选实现比较逐案例收益和损失，而非按功能清单引入复杂度。

### Cited Findings
- 上游评测器使用生产 consolidation 路径，对比 baseline/candidate，使用 must-mention / must-not-mention 衡量字面保真；LongMemEval 子命令启动真实服务。 [评测器说明](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/evals/README.md)
- 检索 A/B 报告 hit@k、recall@k、延迟、分片与逐问题变化；hit@k 是至少一个证据命中，recall@k 衡量找到证据的比例，两者有不同分母。 [A/B 方法](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/benchmarks/retrieval-ab-r2.md)；[指标定义](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/benchmarks/longmemeval-s-2026-09-01-fts.md)
- 上游对输入先完整脱敏再截断，代码解释了跨截断边界的凭据片段在先截断时可能无法匹配的缺陷，并有回归测试。 [payload 实现与测试](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-hooks/src/payload.rs)
- 安全文档明确 sanitizer 是 best-effort；embedding、LLM provider、admission webhook 可能接收正文，不能把“经过脱敏”当作无泄漏保证。 [数据处理说明](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/DATA_HANDLING.md)

### Inferences
- memory-use 已经测单目标排名以及 brief 最终片段；可补多证据问题、必须保留/禁止出现的事实、逐案例基线差异，避免均值掩盖回归。
- 对 memory-use 现有流程先检查它是否先扫描完整数据，再做长度限制；不能假定已有同类漏洞。
- 若未来设计自动生成或自动压缩，必须是可选、保留来源与可恢复历史，并先做事实保真评测。

### Gaps
- 评测器的 literal fidelity 不能证明回答正确；任何新增多证据指标也不能替代回答质量评估。
