# 召回、预算与并发：可重放的实验

日期：2026-10-21。完整应用的记忆质量尚未比较。本轮运行了上游的存储测试、选定检索原语，以及 memory-use 的故障实验；下文分别标明证据覆盖到哪里。

本篇记录的是症状路由引入前的预算和并发阶段，证据中的模块校验值对应那个快照。后续故障表路由与最新验证见[召回评测](recall-evaluation.md)，不要把本篇的“77 个输入排序未变”套用到后续检索改动上。

## 先定位自己的失败发生在哪里

此前 8 个自然问法是根据笔记用途人工编写的，不是历史对话抽样，也没有包含代理抽取关键词和生成回答的完整过程。6/8 只表示目标路径与片段进入了 brief，不能叫作用户问题正确回答率。

该阶段两条失败输入的调用链：

| 输入 | 查询分组 | 检索与筛选结果 | 直接影响 |
|---|---|---|---|
| 换电脑了怎么接回家里的网络 | 无空格，整个句子成为一个字面匹配组 | 句子未出现在目标正文，目标未召回 | 改排序或增加元数据提示都无法产生候选；而“家里的网络”还存在目标含义不明确的问题 |
| SSH 启动程序桌面上看不到 | `ssh` 与 `启动程序桌面上看不到` 两组 | 目标正文的 SSH 出现 10 次，后半句命中 0 次；最佳目标片段排第 19 | 大量泛 SSH 结果先进入上下文，目标没进前 6 条 |

第二条的目标分数约 0.5364，当前弱匹配阈值约 0.2254，目标已经高于阈值。降低阈值不会改变前 6 条已经占满的事实。扩大候选数量可以让更多文章成为候选，但还需要检查哪些内容真正承载问题的答案以及预算是否容得下。

因此下一次自然语言检索试验应检验“如何从原问产生有区分力的候选”，同时保护机器名、数字和错误码；不能把它简化成降低现有过滤阈值。

## 运行 Basic Memory 的有序双字分析

固定版本：`basicmachines-co/basic-memory@976a10c8977345e1f84b9d1a1557a0e2d0bad540`。直接加载其 [`script_ngrams.py`](https://github.com/basicmachines-co/basic-memory/blob/976a10c8977345e1f84b9d1a1557a0e2d0bad540/src/basic_memory/repository/script_ngrams.py)，调用真实的 query analyzer 和 document gram builder。这里只检查有序双字通道，没有启动它的 SQL 查询、向量检索或完整 MCP 服务。

公开合成语料为 5 篇短笔记；问题分三组，每组 5 条：直接措辞、改写问法、同主题但笔记没有答案的问题。语料、问题和判定代码在 [competitor_primitives.py](../scripts/research/competitor_primitives.py)。另加入一个我们自己的试验策略：无序双字重叠达到 25% 就给候选。它不是 Basic Memory 的策略。

| 策略 | 直接问法找到目标 | 改写问法找到目标 | 无答案问题仍给候选 |
|---|---:|---:|---:|
| memory-use 当前字面检索 | 5/5 | 0/5 | 0/5 |
| 上游有序双字通道 | 5/5 | 0/5 | 0/5 |
| 我们试验的无序双字重叠 | 5/5 | 3/5 | 5/5 |

有序双字表示保留字符顺序，能补足数据库分词能力，但不自动产生同义表达。把顺序条件丢掉后，改写问题的候选覆盖增多，却也把“相机上传照片的版权怎么登记”路由到只有端口、目录权限的笔记。返回这些候选并不意味着代理一定答错，但它已经不能把“存在候选”当作“记忆中有答案”。

这组样本是机制探针，没有盲测，也没有独立调参集；25% 是固定试验值，不是优化后的阈值。依据这些结果，本轮不把无序双字兜底加入产品。它们也不能用来评判 Basic Memory 完整产品的问答表现。

## 重放 QMD 的融合函数和强命中保护

固定版本：`tobi/qmd@86d2365d17fc682e491c2c53d1e4a884aee8682a`。运行 [qmd_fusion_probe.cjs](../scripts/research/qmd_fusion_probe.cjs)，用 TypeScript AST 提取原文件里的 [`reciprocalRankFusion`](https://github.com/tobi/qmd/blob/86d2365d17fc682e491c2c53d1e4a884aee8682a/src/store.ts#L5371)、强命中判断与常量，在 ES2022 目标下编译执行。没有加载 store、向量或模型。脚本检查首位奖励的数值，避免编译方式改变迭代行为后仍把输出当作上游结果。

固定输入：原问候选是 `exact.md`、`neighbor.md`，原问权重为 2；两条扩展各把 `neighbor.md` 放第一位，权重均为 1。

| 融合输入 | exact.md | neighbor.md | 第一名 |
|---|---:|---:|---|
| 只有原问列表 | 0.082787 | 0.052258 | exact.md |
| 再加入两条扩展列表 | 0.082787 | 0.115045 | neighbor.md |

原问双倍权重仍不保证它的第一名不变。对这个输入，QMD 的强命中判断为真，因此实际编排会跳过扩展；带显式 intent 时保护关闭，接近的前两名分数也不会走捷径。这里验证的是两个机制的配合，没有验证完整 hybrid pipeline 的检索质量。

对 memory-use 的影响是：将来试验查询改写或第二路检索，要同时记录原问、候选来源及重排变化，验证新增通道是否把本来正确的精确匹配挤掉。单独搬一个 RRF 函数并不能解决自然提问。

原始结果：[qmd-fusion.json](evidence/qmd-fusion.json)。

## 预算选择改变最终能读到什么

固定版本：`vectorize-io/hindsight@a7ab53c84a847664d3377dfb7987d3e6bbaf4b17`。直接运行 [`select_facts_within_budget`](https://github.com/vectorize-io/hindsight/blob/a7ab53c84a847664d3377dfb7987d3e6bbaf4b17/hindsight-api-slim/hindsight_api/engine/fact_budget.py)，将计量函数设为 `len`，隔离选择逻辑。下表单位是字符模拟成本，不是某个模型的真实 token 数。

| 候选成本与预算 | 上游实际返回 |
|---|---|
| 1000、40、40；预算 120 | 跳过大项，保留后两个，总成本 80 |
| 只有 1000；预算 120 | 保留第一项，实际成本 1000，超过预算 |
| 只有 1000；预算 0 | 不返回事实 |

它保护完整事实与最少返回量。memory-use 的 `--max` 已经是硬字符上限，不能采用超预算兜底，但可以借鉴“按条目分配，不能让大项挡住后面所有内容”。

可重放的 memory-use 语料是两篇都匹配 `route` 的笔记：第一篇有 3000 字符长要点，第二篇很短，包含实际恢复步骤。旧版先拼完整正文、再按字符串截断；大要点未能放进预算时，连两篇文章的可用片段一起丢掉。

| 字符预算 | 基线 `6af7c00` 可用来源 | 修改后可用来源 | 修改后实际字符数 |
|---|---:|---:|---:|
| 128 | 0 | 0 | 127 |
| 512 | 0 | 2 | 394 |
| 1500 | 0 | 2 | 395 |
| 6000 | 2 | 2 | 3213 |

修改后的 `pack_brief` 优先保存完整文件入口、必要复核警告和有省略标记的短来源片段，再放背景内容；内容本来就能放下时不改展示。它仍是片段视图，并非完整事实或完整命令的保证。路径不会切在半截，读取后仍应按需 `show` 全文。没有来源条目的专栏概览沿用原有截断行为。

真实笔记库的既有 77 个输入，检索分数与排序前后全部一致。8 个人工场景在 512 字符预算下，目标条目及片段保留从 5/8 到 6/8；1500、6000 预算均保持 6/8。这证明改动作用于上下文装配，未解决另外两条整句提问。它仍不是最终答案正确率。评估前后笔记 Markdown 校验值相同，未复制私人正文到测试机。

重放脚本：[context_probe.py](../scripts/research/context_probe.py)。结果：[修改前](evidence/context-before.json)、[修改后](evidence/context-after.json)。

## 并发状态：锁住整个读改写过程

上游固定版本：`modelcontextprotocol/servers@51b6382a3e827aceb6bce430b1208d69b93d95ea`。在隔离目录安装声明依赖，保留原源码与上游 tsconfig，Node 24.22.0 下运行这四个上游测试文件：

```text
atomic-save.test.ts
file-lock.test.ts
persistence.test.ts
knowledge-graph.test.ts
```

共 91 项通过。该次运行使用 TypeScript 5.9.3、Vitest 4.1.12、MCP server SDK 2.3.1。不是上游全套测试，也没有验收完整 MCP 连接或 Windows 行为。运行文件的 Git blob 为 `d9a135de820b09115a009b5ff82d9b655a9c67ae`，与固定提交中的 `src/memory/index.ts` 相同。

memory-use 的故障实验启动两个独立进程，各增加 30 次同一失败计数，并更新各自的会话。为了稳定扩大交错窗口，在真实读取操作之后加入 3ms 延迟，未替换实际写入。修复前的工作区版本最终只记到 30/60；对公开基线 `6af7c00` 重放，另一轮只记到 10/60。这些数字证明丢失可发生，不是生产环境丢失率。

仅把 JSON 写入改成 rename 仍会丢失更新：两个进程可能各自从旧数据计算新值，再依次原子替换。因此现在将 `session_store → 修改 → atomic_write_text` 放在同一把锁内，覆盖计数、提醒去重、lesson 状态和 Stop 提醒状态。

具体取舍：

- 本地状态使用操作系统文件锁，持有进程退出即释放。锁文件保留，不用删除锁文件的方式解锁，也不用按文件年龄猜测进程是否还活着。
- 单次等锁最多 1 秒；忙、不可读或落盘失败时，hook 最多跳过这次尽力而为的记录，不改变业务命令的执行结果。不能把它描述成任何故障下都绝不丢提醒。
- 临时文件与目标在同一目录；写入、flush、fsync 后原子替换，保留已有权限并尊重只读文件。失败不截断旧文件。SIGKILL 发生在替换前可留下临时文件，但已提交 JSON 仍完整；不声称已验证硬断电持久性。
- 损坏 JSON 不再当成空库写回；保留原文件，`doctor` 报告不可读，供用户检查恢复。
- lesson 日志追加和裁剪使用独立锁，裁剪也原子替换。状态文件与日志仍是两个独立文件，不承诺跨文件事务。

独立回归在 [test_hook_state.py](../tests/test_hook_state.py)，覆盖并发计数、不同会话保留、同一提醒只领取一次、锁超时保留状态、持有进程被杀、替换前进程被杀、损坏状态保留、替换异常、修改异常、日志并发裁剪和 POSIX 文件权限。

每个版本各测 200 次合成计数写入，Mac 190 当次 P50/P95：旧版 0.057/0.067ms，修复版 0.193/0.253ms。它是本次本地文件系统热缓存下的开销测量，不能推广到所有机器或网络盘。原始记录：[state-latency.json](evidence/state-latency.json)。

## 复查和复跑

该阶段运行 206 项测试通过，脚本与新增测试 SHA-256 同当时本地快照一致。新增预算回归在 [test_context_budget.py](../tests/test_context_budget.py)。运行主机是 Mac 190，Python 3.14.7；Windows 锁分支、Linux、Python 3.9 和硬断电恢复尚未运行，未发布版本。最新检索改动的测试结果见[召回评测](recall-evaluation.md)。

```sh
python3 -m unittest discover -s tests

# 在仓库外存放对照源码；baseline.py 可从公开提交取出。
git show 6af7c00:scripts/memory_use.py > /tmp/baseline.py
python3 scripts/research/context_probe.py --module /tmp/baseline.py
python3 scripts/research/context_probe.py

# 固定版本下载的原文件，分别命名为 basic.py、hindsight.py、qmd.ts。
python3 scripts/research/competitor_primitives.py --upstream /path/to/pinned-files
NODE_PATH=/path/to/typescript/node_modules node scripts/research/qmd_fusion_probe.cjs /path/to/pinned-files/qmd.ts
```

原语输入与输出完整保存在 [competitor-primitives.json](evidence/competitor-primitives.json)，包含源文件校验值。原问法处理与负例实验仍需要更大的独立样本，才能决定下一次检索算法变更。本轮选择修复已经复现的状态和上下文装配缺陷，没有用字符处理或测试数量代替语义召回的验收。
