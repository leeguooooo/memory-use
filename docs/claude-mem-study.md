# claude-mem 对照研究

2026-11-01 读取 [claude-mem](https://github.com/thedotmack/claude-mem) 固定版本 `70aa400`（v15.7.0，2026-10-30）的源码、计划文档和更新记录，未运行其代码。它的召回效果没有和 memory-use 在同一语料上比较，这里只比较机制和故障记录。

claude-mem 自动记录工具调用，用 Claude 压缩成观察，存入 SQLite 和 Chroma，经常驻 worker 提供检索。memory-use 是用户维护的 Markdown 和 Git，没有后台服务和模型。两者的产品形态不同，最值得借的是它踩过的 hook 故障，不是它的架构。

## 拿它的故障类别审计自己

它的更新记录和计划文档反复出现几类问题。以下逐项在 memory-use 上实测。

| 故障类别 | 在 memory-use 上的结果 | 处理 |
|---|---|---|
| hook 的编码和管道 | 复现。`LC_ALL=C` 或 `PYTHONIOENCODING=cp1252` 下提醒静默消失，退出码 0 | hook 显式按 UTF-8 收发，笔记读写和 git 输出指定编码，hook 命令加 `PYTHONUTF8=1` |
| 静默失效 | 复现。自动同步停摆后状态永远显示 ok，hook 异常被直接吞掉 | 检查状态年龄和定时任务指向的文件；hook 异常记入 `hook-errors.jsonl`，`doctor` 显示 |
| 版本路径失效 | 由代码推断。定时任务固定解释器和脚本路径，文件消失后不会再有运行记录 | `doctor` 检查这两个路径 |
| 密钥模式遗漏 | 复现。26 种写法漏 15 种；`.env`、`.pem` 和无后缀文件完全不扫描 | 见下节 |
| 记忆内容带指令 | 机制存在，未构造攻击。`brief` 原样输出笔记 | 输出头部标明笔记内容是待核实的事实，不是指令 |
| 压缩后丢上下文 | 由代码推断。同一会话压缩后主题不再提醒，直到一天后状态过期 | 新增 `hook compact`，压缩后清除已提醒记录 |
| 自身递归 | 未复现。失败 hook 已跳过自身命令，Stop 已检查 `stop_hook_active` | 不改 |
| 超时预算 | 未复现。`hook tool` 实测 0.34 秒，提示词 hook 已有 3 秒限制 | 不扩展监管进程 |

CI 抓不到编码问题有原因：Windows 任务设置了 `PYTHONUTF8=1`，Linux 和 macOS 运行器本身就是 UTF-8。回归测试在子进程里强制非 UTF-8 环境，不依赖 CI 配置。命令行直接调用（非 hook）没有 `PYTHONUTF8`，但笔记读写已指定编码。

## 密钥扫描

可重跑的探针：`python3 -I scripts/research/secret_patterns_probe.py`。

| | 改动前 | 改动后 |
|---|---|---|
| 26 种合成密钥写法命中 | 11 | 26 |
| 11 种正常写法误报 | 0 | 0 |
| 本机笔记全量 `check` 误报 | 0 | 0 |

根因有两个结构问题：`\b` 把 `_` 当单词字符，所以 `DB_PASSWORD=` 不匹配；键名和冒号之间的引号让 `"password": "…"` 不匹配。其余是新增的 URL 凭据、`Authorization` 头、`mysql -p`、`sshpass -p` 和几种固定前缀。原始结果见 [改动前](evidence/secret-scan-before.json) 和 [改动后](evidence/secret-scan-after.json)。

这份列表永远补不完，claude-mem 的更新记录就是一次次追加模式。真正兜底的是 `known-secrets` 和保险库哈希，它们按实际值匹配。探针的 26 种写法是本次自选样本，不代表真实覆盖率。

## 不采用的部分

- **模型压缩观察。** 这是它的核心，也是成本和隐私边界的来源。memory-use 的笔记由人和代理显式写入。
- **常驻 worker、向量库、读文件门禁。** 门禁依赖 worker 和 tree-sitter。其自带评测每种情况只有 2 到 3 次运行，19 KB 文件约为零收益。
- **自动捕获的跳过规则和 `<private>` 标签。** 这些服务于自动捕获，memory-use 没有这条路径。
- **近重复合并和强化排序。** 它的近重复默认关闭，强化系数默认为 0。`lint` 已报告近重复文章。
- **项目身份和注入范围。** memory-use 只有一个个人笔记库，没有项目键。
- **渲染、测量、缩减的预算循环。** `pack_brief` 已有硬字符上限和条目优先级。
