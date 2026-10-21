# 召回评测

`eval` 现在检查搜索排名，也检查最终 `brief` 是否保留目标笔记的完整入口和来源片段。它复用 CLI 的正文生成和字符截断逻辑。搜索排第一、文章进入候选集、片段最终被读到，是不同的结果；任何一项都不能直接证明回答正确。

## 用自己的问题测

在笔记库外建立一个 Markdown 问题集，保留原始问法，并指定经阅读确认能提供答案的文件：

```markdown
- 新电脑接入 VPN 需要什么 → network/new-computer.md
- SSH 启动程序桌面上看不到 → machines/windows.md
```

```sh
memory-use eval --cases /path/outside-notes/questions.md --max 1500
memory-use eval --cases /path/outside-notes/questions.md --max 1500 --json
```

问题文件必须在搜索范围外，或使用本来就不参与检索的根目录 `evals.md`。避免问题文本和答案路径自己进入搜索结果。`--cases` 只测指定文件；不加它则仍分别列出 `evals.md`、glossary、trouble、title 的检查。自动生成的标题和词汇表样本只能说明导航可用，不能替代用户问题。

`hit@1`、`hit@3` 和 MRR 按去重后的文件计算，先对全部匹配片段排名，再归并文件。旧实现只取前 20 个片段，一篇长文可能占满窗口，使第二篇匹配文件被误记为“没找到”。这项评测修复本身不改变排序；下文的故障表路由是另一项检索改动。

`brief` 列表示预算内保留目标笔记入口和来源片段的比例。它只统计“best matching notes”中的完整条目，不把仅在日志、别名或别的文章正文里出现的文件名算作命中。只有路径或工具生成的警告、要点标签、`trigger:` 路由元数据也不算命中。原文片段仍可能没有答案，因此下面另测必要证据。

逐题结果区分：

| 结果 | 含义 |
|---|---|
| `present` | 完整入口及来源片段出现在预算内 |
| `not-retrieved` | 搜索没有找到目标 |
| `filtered` | 找到了目标，但没进入 brief 选出的条目 |
| `budget-or-no-excerpt` | 条目已选中，但最终预算内没有完整入口和来源片段 |
| `invalid-target` | 目标不存在、越出仓库，或不在默认搜索范围 |

无效目标单独计数，不进入命中率分母，并使命令返回非零状态。正常评测中出现漏召回仍返回 0，表示评测已完成，不表示召回质量通过。JSON 中有逐题排名、前三个文件和失败阶段。没有可解析的问题时返回 1；JSON 模式提供 `error`。

## 既有故障表也能提供召回入口

Nocturne 将“何时召回”与正文分开。memory-use 不新增字段，而是利用专栏 README 已有的故障处理链接。例如公开合成语料中的一行：

```markdown
## Troubleshooting
| Symptom | Fix |
|---|---|
| SSH 里启动的程序在 windows-box 桌面上看不到 | [gui.md](gui.md) |
```

文章正文只需写交互式会话的修复，不必再塞入用户可能使用的整句问法。`search`、`brief` 会标出 `[symptom link]`，并在 `trigger:` 中给出源 README 与行号。它是一条作者明确维护的导航关系，不是从待测问题集学习答案路径。

仅从顶层专栏 README 的 `出问题先看这里`、`Troubleshooting` 或 `If something breaks` 表格读取。代码块、其他章节、归档入口、无效文件、仓库外链接和根目录 `evals.md` 都不会作为这类入口。这个限制针对路由索引，不代表对整个笔记读取器作出了新的文件隔离保证。

字面匹配保留几项边界：

- ASCII 名称与数字按完整 token 对应，不把 `HTTP` 当成 `HTTPS`，不把 `1900` 当成 `190`。
- 中文组保留连续的开头锚点，再要求字符按原顺序出现在条件中；登记的实体别名另外核对，包括单别名和无空格句子中的名称，不把“相机”拆成“相关机器”。
- 否定标记序列必须一致；“看不到”不会因删掉“不”而得到肯定形式的条件。
- 未命中的标识符、复杂 URL 或路径不拆碎重组。范围不清时保留普通检索行为，不凭空补出某台机器或服务。

这不是完整的语义解析：没有解释主客体关系、指代或同义否定。条件可能比问题写得更具体；代理必须确认其中的机器、OS 和前置操作确实适用，不能把“读到某个条件”说成“用户已经做过那一步”。

## 从问题追到答案片段

[公开语料](../scripts/research/fixtures/symptom-questions.json)包含操作笔记、7 条故障链接和 24 个检查样例。问题、固定检索词、期望文章、必要答案片段分别存放；问题没有进入可搜索笔记。脚本拒绝空检索词、空证据、错误的证据类型，以及目标文章中不存在的证据，防止评分空条件通过。

[symptom_probe.py](../scripts/research/symptom_probe.py)按阶段记录目标排名、正文片段、必要证据、路由来源和非预期路由。答案证据只从正文摘录中检查，路由条件、标题、警告和标签不能替代它。

| 样例组 | 数量 | 修改前首轮包含必要片段 | 修改后首轮包含必要片段 | 修改后非预期路由 |
|---|---:|---:|---:|---:|
| 开发问法 | 5 | 0 | 5 | 0 |
| 额外问法 | 6 | 1 | 6 | 0 |
| 否定、实体、协议、未知约束反例 | 10 | 不适用 | 不适用 | 0 |
| 范围不明 | 2 | 不适用 | 不适用 | 0 |
| 人工重写 | 1 | 0 | 0 | 0 |

人工重写那一例只有第二次查询才包含证据，单独记录为重试成功，不并入首轮结果。额外问法在 JSON 中的分组名为 `heldout_wording`，这里只作样例分组，**不是独立研究者的盲测集**。所有问题和检索词均为本次人工编写，没有运行代理生成检索词，也没有生成最终回答。必要片段出现只验证来源内容可见，不能当作回答正确率。

反例检查的是没有新增强路由。普通关键词检索仍可能给出相关线索，这不表示库中有完整答案。语料还放入“相关机器 FTP”和“相机 HTTPS”两个干扰入口，避免只检查正确文件有没有出现、却漏掉同样被提升的错误入口。

```sh
python3 scripts/research/symptom_probe.py --max 1500
python3 scripts/research/symptom_probe.py --module /path/to/before.py --max 1500
```

原始记录：[修改前](evidence/symptom-before.json)、[修改后](evidence/symptom-after.json)。二者使用同一语料校验值，输出还记录了模块校验值。

修改前的工作区版本未单独发布，不能仅凭源码哈希下载。为保证对照可重放，保留了[召回还原补丁](evidence/before-symptom.patch)。后续同步与并发修复分别有[同步补丁](evidence/before-save.patch)和[并发补丁](evidence/before-concurrency.patch)；下面按逆序恢复召回验证时的源码，再恢复旧基线，并校验完整文件。不会修改当前工作区：

```python
import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

repo = Path.cwd()  # 在仓库根目录运行
source = repo / "scripts/memory_use.py"
before = json.loads((repo / "docs/evidence/symptom-before.json").read_text())
after = json.loads((repo / "docs/evidence/symptom-after.json").read_text())
current = json.loads((repo / "docs/evidence/sync-after.json").read_text())
latest = json.loads((repo / "docs/evidence/concurrency-after.json").read_text())
assert hashlib.sha256(source.read_bytes()).hexdigest() == latest["source_sha256"]
with tempfile.TemporaryDirectory(prefix="memory-use-probe-") as temp:
    root = Path(temp)
    baseline = root / "scripts/memory_use.py"
    baseline.parent.mkdir()
    baseline.write_bytes(source.read_bytes())
    subprocess.run(["git", "-C", temp, "init", "-q"], check=True)
    subprocess.run(["git", "-C", temp, "apply", str(repo / "docs/evidence/before-concurrency.patch")], check=True)
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == current["source_sha256"]
    subprocess.run(["git", "-C", temp, "apply", str(repo / "docs/evidence/before-save.patch")], check=True)
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == after["module_sha256"]
    subprocess.run(["git", "-C", temp, "apply", str(repo / "docs/evidence/before-symptom.patch")], check=True)
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == before["module_sha256"]
    result = subprocess.run([sys.executable, str(repo / "scripts/research/symptom_probe.py"),
                             "--module", str(baseline)], check=True, capture_output=True, text=True)
    print(json.loads(result.stdout)["summary"])
```

## 2026-10-21 的本机复查

现有笔记库为 71 个 Markdown 文件，评估只读，没有将私人正文复制到测试机或公开仓库。原来的 8 个场景来自笔记用途的人工问法，不是历史提问抽样：

| 字符预算 | 路由前目标条目及片段保留 | 路由后 |
|---|---:|---:|
| 512 | 6/8 | 7/8 |
| 1500 | 6/8 | 7/8 |
| 6000 | 6/8 | 7/8 |

`SSH 启动程序桌面上看不到` 的目标文章由第 11 位升到第 1 位；其真实正文片段包含“计划任务”和 `Interactive`。`trigger:` 中的字样没有参与这项证据检查。但该测试的 Windows 目标由人工预先指定，仍不能证明任意用户说出这句话时，机器就一定是 Windows。

`换电脑了怎么接回家里的网络` 没有被强制路由。该问法没有给出足够范围；此前重试加入“私人 VPN”是人工基于笔记选出的上下文，不能倒过来当作原问已经提供的事实。两份技能说明已改为区分查询失败、缺少范围和确实没有答案，并要求重写时保留标识符、否定及显式约束。

自动导航检查分别记录，避免它们掩盖自然问法：

| 来源 | 数量 | 修改前 hit@1 / hit@3 | 修改后 hit@1 / hit@3 |
|---|---:|---:|---:|
| 词汇表 | 15 | 15 / 15 | 15 / 15 |
| 故障处理表 | 11 | 9 / 10 | 10 / 11 |
| 标题 | 43 | 41 / 43 | 41 / 43 |

本表按同一问题集、以本轮修改前校验值 `1d056bf6…` 的快照重新计算，完整记录见 [query-validation.json](evidence/query-validation.json)。58 个词汇表和标题输入的完整排序及分数未变。故障表原句还可能带有用户原问没给出的错误码；例如旧检索能处理含 `267011` 的条件，却漏掉了不带该码的 SSH 问法。因此这些检查只证明导航可用，不能作为独立语义质量样本。

检索排序函数的性能采样采用前后版本交替执行、每组 20 对、暖缓存、本机只读语料，不含 CLI 启动和 brief 装配。症状查询的 CPU 中位数约从 43.59ms 到 45.34ms，关键词查询约从 44.84ms 到 47.64ms；墙钟 P95 仍受共享机器影响，不能据此给出稳定 SLA 或宣称加速。数据与源代码校验值见 [symptom-latency.json](evidence/symptom-latency.json)。

召回阶段工程验证：Mac 190、Python 3.14.7，216 项测试通过，包含路由边界、CLI 输出、正文证据、非法评测样例和单独运行测试模块的配置隔离检查。Windows、Linux、Python 3.9 和代理盲测尚未运行；未提交或发布。预算、并发及上游原语的前一阶段实验见[故障实验](deep-experiments.md)。

后续同步修复为 225 项、并发修复为 231 项，见 [Letta 同步研究](letta-sync-study.md)和[进程并发实验](repository-concurrency-study.md)。本页的召回指标和原始校验值仍对应召回阶段快照。
