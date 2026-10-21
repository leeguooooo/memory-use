# Nocturne 对照研究与 memory-use 改动

研究基线：Nocturne `1858957bf797722a9b731fb197bd9530f7ae67e4`，memory-use `6af7c00`。日期：2026-10-21。结论来自源码和现有测试的阅读；本次只运行 memory-use 的测试，没有部署 Nocturne，也没有验证其 README 的使用量和效果数据。

memory-use 应继续使用用户拥有的私有 Git 仓库存储 Markdown。这次采用 Nocturne 对召回入口的处理思路，修复明确链接仍找不到文章的问题，并让读取结果提示需要复核的笔记。数据库、MCP 服务、人格设定和管理页面不进入本轮实现。

## Nocturne 的一次读写如何工作

### 内容、路径和版本分开存

[`backend/db/models.py`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/db/models.py) 中有四层：Node 保存稳定身份；Memory 保存内容版本；Edge 保存父子关系、名称、priority 和 disclosure；Path 保存 namespace/domain/path 到节点、边的映射。更新正文会创建同一节点的新 Memory 行，旧行标为 deprecated。路径和别名无需随正文重建。

这适合多个路径共享一份内容、并允许路径各有召回条件的服务。需要区分两种别名：共享同一条 Edge 的 Path 共享边元数据；挂到不同父节点的边可以有各自的 priority/disclosure。不能把每条 URI 都理解为完全独立的元数据副本。

memory-use 已用文件作为内容身份、Git 作为版本历史、目录和词汇表作为入口。为了得到上述四层模型而迁移现有笔记，收益不足。重命名文件造成的断链应通过更新显式链接解决。

### 读取正文，同时给出下一步入口

[`backend/system_views.py::fetch_and_format_memory`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/system_views.py#L21) 返回当前正文、元数据、命中的 glossary 链接及子节点 URI。子节点有 disclosure 时显示何时召回，没有时才展示短摘要；不会把所有子树正文都展开。`generate_boot_memory_view` 从选定的核心 URI 启动。

这是上下文预算管理。memory-use 的 `brief → show` 已有同类作用：先给文件路径、匹配片段和要点，需要时再读全文。本轮保留已有的每篇最多两个片段、弱匹配过滤和字符预算；不增加默认全库启动包或自动递归追链接。

### 触发词不必出现在目标正文中

[`backend/mcp_server.py::manage_triggers`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/mcp_server.py#L895) 将关键词绑定到节点。读取别的内容命中关键词时，系统展示目标链接。触发词服务于“从什么情况想到这条记录”，与目标文章采用什么措辞是两件事。`create_memory` 还检查 disclosure 必须非空，但 priority 的建议数量是工具说明中的约定，不能当作硬性配额保证。

memory-use 原来已有 `glossary.md` 的 `见 / see article.md`，但 `rank` 先要求正文、标题或文件名命中查询，再给指向的文件加权。于是入口存在，目标仍不可达。最直接的修复是把已有链接用于召回，而非让用户再维护一份触发词配置。

后续也利用了 README 故障处理表中的“症状 → 文章”关系，作为有来源的召回入口，而非新增 disclosure 字段。条件、实体与否定标记的检查，以及“路由文字不算答案”的验证见[召回评测](recall-evaluation.md)。

### 全文索引是派生数据

[`backend/db/search.py`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/db/search.py) 将可达路径、正文、disclosure、glossary 词生成搜索文档。SQLite 使用 FTS5/BM25，PostgreSQL 使用 tsvector；结果按节点去重，多条路径不会各占一个结果。`search_terms.py` 用 jieba 处理中文并注册自定义词。索引支持从图数据重建。

这不是语义检索。SQLite 查询把分词结果用 AND 连接，所有词都要命中；自然语言提问是否更好找，仍需数据验证。memory-use 当前按标题、文件名、正文、别名和显式链接检索。没有实际规模和延迟证据前，不加入数据库索引、分词依赖或向量模型。

### 写入版本化，审核记录另行保存

[`backend/mcp_server.py::update_memory`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/mcp_server.py#L508) 支持片段替换和追加，不提供整篇覆盖参数。它拒绝有歧义的替换，另有标点、空格、换行的规范化匹配。先读后改是工具说明中的前置要求；不能据此声称服务端强制校验了读取凭证。

[`backend/db/graph.py::update_memory`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/db/graph.py#L1456) 在数据库会话中修改版本并刷新搜索索引。返回后，MCP 层再把 before/after 记录到 [`backend/db/snapshot.py`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/db/snapshot.py) 的 JSON changeset，审核 API 用它呈现差异和回滚。

JSON 更新有文件锁，但这不能使数据库提交和 changeset 写入成为一个原子事务。源码中两步之间存在崩溃窗口；本次没有做故障注入，不能把它描述成已复现的数据丢失。memory-use 已有 Git diff/history 和明确路径提交，不复制另一套审核存储。代理编辑时应保留原文、先读再改；本轮也不引入宽松模糊替换命令。

### 维护信号与事实有效性不同

[`backend/db/graph.py::get_diagnostics`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/db/graph.py#L438) 根据上次访问和优先级找出久未读取的节点，另查子节点过多和正文过大的情况。[审计入口](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/docs/skills/memory-audit/SKILL.md) 将这些信号作为人工审查入口，不要求自动删除。

对服务器配置笔记，常被读取并不说明配置仍然正确。memory-use 已有 `更新 / Updated` 和 `复核 / Review`，应按事实的复核日期提醒，读取操作不能刷新它。本轮将 lint 已有的判断带到 `search`、`brief`、`show`，保留过期、废弃记录供追溯，不自动删除或降低其排名。

## 采用与取舍

| 机制 | memory-use 的处理 | 理由 |
|---|---|---|
| 独立召回入口 | 修复已有 glossary 显式链接 | 目标文章无需重复所有昵称；无需迁移格式 |
| 按需展开 | 保留现有 brief/show | 已有片段限制和字符预算，无需再建一套 |
| 内容版本与审核 | 保留 Git | 用户可直接编辑、迁移和查看差异 |
| 久未访问诊断 | 改用已有复核日期，在读取时提示 | 访问热度不能证明事实有效 |
| Graph/SQL/FTS 服务 | 不引入 | 未证明现有规模需要额外索引和守护进程 |
| 多 namespace | 不引入 | 当前按用户私有仓库隔离；请求选择 namespace 本身也不等于授权 |
| UI、桌宠、人格与认知审计 | 不引入 | 不解决机器、服务配置笔记的召回和维护问题 |
| 模糊正文替换 | 不引入 | 现有文件编辑足够；误匹配和并发覆盖需要单独设计 |

namespace 的判断来自 [`backend/namespace_middleware.py`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/namespace_middleware.py) 和 [`backend/auth.py`](https://github.com/Dataojitori/nocturne_memory/blob/1858957bf797722a9b731fb197bd9530f7ae67e4/backend/auth.py)：请求可用 header/query 选择 namespace，HTTP 使用配置的共享 token。不能把这种分区直接用作不互信用户的权限边界。

## 本轮改动的验收条件

- `glossary.md` 写 `- gateway-recovery / broken exit — see nas/route.md`，文章只写 Route repair 和恢复步骤：两个别名都能通过 search/brief 找到文章并展示内容，标注 `[glossary link]`。
- 链接只给文章开头一个召回入口，不把所有无关小节都变成匹配。多个查询词仍分别计分，重复别名不增加权重。
- 无关查询不召回目标；缺失文件、目录、越出仓库的路径或链接不作为显式入口。归档仍只在 `search --all` 中参与排名。
- 查询命中正文中部时，也能看到整篇笔记的复核提示。默认 120 天、自定义期限及 `never / 长期` 沿用已有约定；废弃标记单独提示；非法日期提示而不使 lint/brief 崩溃。
- 读取不修改日期、正文、Git 历史或代理状态。现有 brief 的字符上限和搜索回归继续通过。

验证使用人工构造的临时笔记和临时 Git 仓库，避免把私人笔记复制到测试机。旧版本已在 190 复现“只返回 glossary.md，找不到目标文章”。这证明具体缺陷存在，不代表对所有自然语言查询或真实笔记库的召回率作出承诺。

2026-10-21 在 Mac 190 的隔离目录完成验证：Python 3.14.7，`python3 -m unittest discover -s tests` 共 164 项通过；安装、发布及模板 pre-commit 脚本的 `sh -n` 检查通过。最终运行的脚本和测试文件 SHA-256 与本地工作区一致。未在 Linux、Windows 或 Python 3.9 上重跑；未修改私人笔记，未提交或发布版本。

## 仍需实际使用数据才能决定的事项

此次没有给笔记新增必填字段，也没有自动扩充关键词。遇到真实漏召回，应先将原问题记入用户笔记库的 `evals.md`，再判断缺少昵称、适用条件还是搜索能力。只有现有机制无法解决且可测出收益时，才考虑新的检索依赖。外部项目的功能数量不作为采用标准。

后续实测和评测改动见[召回评测](recall-evaluation.md)。自然语言原问、关键词重试、自动导航测试分别统计，避免把标题命中或重试成功当作首次召回成功。
