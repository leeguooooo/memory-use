# Letta：提交之后，怎样确认记忆同步完成

研究版本为 [letta-ai/letta-code `c25f091d0fce1f032e5cdf67ce131d5b474e6576`](https://github.com/letta-ai/letta-code/tree/c25f091d0fce1f032e5cdf67ce131d5b474e6576)，2026-10-21 通过 GitHub API 固定。它与 memory-use 都使用 Git 管理文件记忆，因此这次比较提交、推送和恢复的行为，不比较模型效果。

## 从调用链看同步完成的含义

[`runPostTurnMemorySync`](https://github.com/letta-ai/letta-code/blob/c25f091d0fce1f032e5cdf67ce131d5b474e6576/src/reminders/memory-git-sync.ts#L162) 在回合后认领仓库操作锁，调用同步函数；只有 `pushed` 才触发 `onMemoryPushed`。冲突和验证失败可以交给后台修复，推送失败则告知以后重试。文件提交、本地同步结果、提醒和后续修复是不同步骤。

[`claimMemoryOperation`](https://github.com/letta-ai/letta-code/blob/c25f091d0fce1f032e5cdf67ce131d5b474e6576/src/agent/memory-operation.ts#L35) 的注释明确限定了锁的范围：harness 管理的写入者受锁保护，前台代理的编辑并不经过这把锁。不能因此断言所有编辑已经串行化。这里没有运行后台代理或采用这套修复机制。

[`syncPendingMemoryCommitsAfterTurn`](https://github.com/letta-ai/letta-code/blob/c25f091d0fce1f032e5cdf67ce131d5b474e6576/src/agent/memory-git.ts#L1837) 的主要分支：

1. 先检查未完成操作与未提交修改；存在时返回 `conflict` 或 `dirty`。
2. 查询本地待推提交并校验历史。没有新文件修改，并不意味着没有提交待推。
3. 推送失败返回 `push_failed`；只有识别为非快进失败，才拉取并 rebase。
4. rebase 后再次检查冲突、校验历史，再推送。

有一个不能忽略的边界：`getMemoryAheadBehind` 读取失败返回 `null`，调用方把它与“没有待推提交”一起返回 `clean`。这一分支没有完成推送，也不能证明远端已经包含本地内容。它在函数边界上是一种宽松降级；本研究没有证明常规初始化流程会触发它。

## 实际执行上游分支

[`letta_sync_probe.cjs`](../scripts/research/letta_sync_probe.cjs) 从固定文件提取原函数体，删除 TypeScript 签名，仅将一次 backend 动态导入改成依赖注入。脚本校验整份原文件 SHA-256，再注入合成的 Git、鉴权和文件系统返回值。没有安装 Letta，也没有调用 hosted backend。

9 个分支检查覆盖 dirty、已有冲突、没有待推提交、推送失败、下一次调用重新推送、非快进后拉取再推送、拉取后冲突、历史验证失败，以及无法读取 ahead/behind。调用序列另有断言：例如冲突后不能发生第二次 push。下一次调用的输入由脚本指定，不是持久化恢复测试。[结果](evidence/letta-sync.json)保留状态与调用序列。

```sh
curl -fsSL https://raw.githubusercontent.com/letta-ai/letta-code/c25f091d0fce1f032e5cdf67ce131d5b474e6576/src/agent/memory-git.ts -o /tmp/letta-memory-git.ts
node scripts/research/letta_sync_probe.cjs /tmp/letta-memory-git.ts
```

这证明所选函数在这些依赖返回值下如何分支，不证明传输重试、锁、后台修复、服务端或最终 prompt 已更新。上游代码未复制进产品，没有新增运行依赖。

## 在 memory-use 中复现的故障

使用本地裸仓库、两份克隆及真实 Git 操作，远端拒绝由临时 `pre-receive` hook 注入。输入都是公开合成笔记；没有打开私人笔记或连接 GitHub 推送。

| 场景 | 修改前 | 修改后 |
|---|---|---|
| `sync` 推送被拒绝 | 输出错误却返回 0 | 返回非零；解除拒绝后重试同步 |
| `save` 已提交、推送失败，文件没有新变化 | 重试直接返回 0，提交仍未推送 | 重试已有提交，不生成重复提交 |
| 重试 `save` 时远端仍拒绝 | 返回 0 | 继续返回失败，保留本地提交 |
| `save --no-push` 重试 | 保留本地提交 | 保持原行为 |
| `save` 遇到远端分叉、其他会话有暂存和未暂存修改 | autostash 恢复冲突，另一会话的工作区和暂存区被改变；已推送且返回 0 | 返回失败；工作区、暂存区、远端原样保留，不产生 stash |
| `sync` 遇到分叉和未提交修改 | autostash 后可能留冲突却返回 0 | 返回失败，保留现场 |
| `save` 的新提交与远端冲突 | 停留在未完成 rebase 和冲突正文中 | abort rebase，保留新建的本地提交与原正文 |
| linked worktree 正在 merge | 未统一识别操作目录；共享 autosync 也有遗漏 | 从 Git 解析实际操作目录，跳过同步 |
| upstream remote 改名为 `notes` | 手动 sync 可用，autosync 写死 `origin` | 共用实现读取分支配置，均可同步 |

旧场景中的内容仍可能从 stash 和 Git 历史恢复，不能描述成已证实永久丢失。修复采用现有 `autosync_once` 的保守处理规则，移除 `save`、`sync` 的重复实现；没有新建同步守护进程、后台代理或自动合并策略。远端绑定与私有性核验仍经过 `safe_push`。

[`sync_probe.py`](../scripts/research/sync_probe.py) 执行 9 项回归，并额外读取 dirty 场景的工作区、暂存区、未合并路径、stash 和裸仓库 HEAD。[修改前](evidence/sync-before.json)通过 1/9，[修改后](evidence/sync-after.json)通过 9/9。这是为故障编写的定向测试比例，不是生产故障率，也不是整体同步可靠性百分比。

```sh
python3 scripts/research/sync_probe.py
```

前一阶段源码未发布。[同步还原补丁](evidence/before-save.patch)从本页验证的同步修复版重建旧源码。后续加入了[并发修复](repository-concurrency-study.md)，因此当前源码须先应用[并发还原补丁](evidence/before-concurrency.patch)。每步校验完整 SHA-256，仅在临时目录中回放：

```python
import hashlib, json, subprocess, sys, tempfile
from pathlib import Path

repo = Path.cwd()
before = json.loads((repo / "docs/evidence/sync-before.json").read_text())
after = json.loads((repo / "docs/evidence/sync-after.json").read_text())
source = repo / "scripts/memory_use.py"
current = json.loads((repo / "docs/evidence/concurrency-after.json").read_text())
assert hashlib.sha256(source.read_bytes()).hexdigest() == current["source_sha256"]
with tempfile.TemporaryDirectory(prefix="memory-use-sync-replay-") as temp:
    baseline = Path(temp) / "scripts/memory_use.py"
    baseline.parent.mkdir()
    baseline.write_bytes(source.read_bytes())
    subprocess.run(["git", "-C", temp, "init", "-q"], check=True)
    subprocess.run(["git", "-C", temp, "apply", str(repo / "docs/evidence/before-concurrency.patch")], check=True)
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == after["source_sha256"]
    subprocess.run(["git", "-C", temp, "apply", str(repo / "docs/evidence/before-save.patch")], check=True)
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == before["source_sha256"]
    result = subprocess.run([sys.executable, str(repo / "scripts/research/sync_probe.py"),
                             "--module", str(baseline)], capture_output=True, text=True)
    assert result.returncode == 1
    print(json.loads(result.stdout)["passed"])
```

## 验证范围

本页同步修复阶段为 Mac 190、Python 3.14.7，完整 memory-use 测试 225 项；后续并发修复为 231 项。上游分支探针使用 Node 22.22.3。构建/测试命令按项目约定固定 Xcode 27.0 的 `DEVELOPER_DIR` 与 `SDKROOT`。校验值见[同步验证记录](evidence/sync-validation.json)。

没有验证 Linux、Windows、Git 服务端宕机恢复、跨电脑同时写入、断电持久性或真实代理回答。Git 自身的文件锁也不等于整个跨进程同步事务的互斥锁。此轮只修复上述可复现路径，不能据此声称多会话编辑已经完全隔离。
