# 本地项目对照

## 现有能力与差距

### Takeaway
memory-use 已采用 Git 和 Markdown 原文、来源可追溯的摘要、时效检查、相似文章 lint 与分阶段召回评测；它主要面向个人手工维护的跨机器运维知识，而 ai-memory 增加自动会话采集、索引和多用户服务。对后者的收益需要本项目实际问题集验证。

### Cited Findings
- 本地 `scripts/memory_use.py` 的 `chunks` 将文章按标题切分，`rank` 使用别名、标题和正文权重及显式故障链接；`render_brief` 输出来源行号、复核告警和有界上下文。见 [源码](https://github.com/leeguooooo/memory-use/blob/main/scripts/memory_use.py)。
- 本项目 [召回评测](https://github.com/leeguooooo/memory-use/blob/main/docs/recall-evaluation.md) 区分排序、入口及预算内正文片段；明确指出这些不代表最终回答正确。
- ai-memory 自述包含自动生命周期采集、默认无模型调用的整合及多用户服务；Markdown 为事实源、数据库为派生索引。见 [固定版本 README](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/README.md)。
- 本项目 [生命周期文档](https://github.com/leeguooooo/memory-use/blob/main/docs/memory-lifecycle.zh-CN.md) 明确 agent 判断是否写入，工具的失败提示不证明修复有效。`review_warning` 基于作者更新日期而非访问频率提醒复核。

### Inferences
- 高优先级：借鉴确定性健康检查，修补 Markdown 断链检查；现有 `cmd_lint` 以正则扫描整篇正文，代码围栏中示例链接也会误报，仅判断路径存在而不检查目标标题锚点。应先用隔离样例复现，再支持代码围栏和行内代码、相对/百分号编码路径、同页或跨页锚点，并明确 GitHub 标题 slug 的兼容范围；保留只读、警告与 strict 返回码行为。注意编码、转义、HTML、自定义标题锚点及仓库外链接的既有兼容性，避免用简单正则承诺完整 Markdown。
- 中优先级：ai-memory 的重复非空标题检查可作为候选，但项目已用同专栏标题与要点的 Jaccard 检查近似重复；跨目录相同的通用标题（如安装、配置）可能是合理内容，不能直接等价于重复事实。上游 [规则检测代码](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-consolidate/src/lint.rs) 排除空标题。
- 低优先级：访问频率驱动衰减、后台自动提炼或采集完整会话可能扩大隐私与误归因风险；当前缺乏真实使用收益和故障预算，不建议默认引入。ai-memory [README](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/README.md) 说明改写和删减默认关闭。

### Gaps
- 未以独立真实问题集比较两项目最终回答质量、延迟、资源消耗或隐私风险，不能断言 ai-memory 召回更好。
- 上述本地源码链接指向 main，行号和工作区可能随版本变化；本次检查以当前工作区为准。

## 验证建议

### Takeaway
优先做单项可复现的改进：证明旧 lint 的误报或漏报，再以真实 Markdown 片段测试新行为，保持现有 CLI 契约。

### Cited Findings
- 本项目已有单元测试及 [召回实验](https://github.com/leeguooooo/memory-use/blob/main/docs/recall-evaluation.md)，评测样本须在搜索范围外，避免题目泄漏到检索结果。
- ai-memory [链接守卫](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/crates/ai-memory-consolidate/src/link_guard.rs) 对生成的跨作用域链接在写入之前做无创建查找；同项目普通正文链接被有意允许悬空，其行为不是通用 Markdown 校验。

### Inferences
- 断链测试覆盖真实和失效相对链接、围栏内代码、行内代码、同页标题、URL 与文件名带编码、重复标题 slug、无效目标以及正常/strict 状态；测试不能读取或改写真实私人笔记。
- 无独立语料和基线前，不声称提升召回率；不要将上游特定跨作用域 wikilink 规则复制成 Markdown 全链接规则。

### Gaps
- 尚未测量真实用户笔记中各类断链告警的误报与漏报率。
