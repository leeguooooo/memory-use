# 同一份记忆仓库的进程并发

上轮修好了单次同步的返回码与失败恢复，本轮检查 `save`、`sync`、`autosync run` 同时操作一个 checkout 时的行为。所有实验使用 Mac 190 的临时裸仓库、合成笔记和独立 Python/Node 进程；没有读写私人笔记，也没有向 GitHub 推送。

## Letta 的锁究竟保护什么

继续使用 [Letta Code `c25f091d0fce1f032e5cdf67ce131d5b474e6576`](https://github.com/letta-ai/letta-code/tree/c25f091d0fce1f032e5cdf67ce131d5b474e6576)。[`memory-operation.ts`](https://github.com/letta-ai/letta-code/blob/c25f091d0fce1f032e5cdf67ce131d5b474e6576/src/agent/memory-operation.ts) 将锁放在 checkout 的实际 Git 目录中：同一 checkout 共用，linked worktree 各自独立。

底层 [`file-lock.ts`](https://github.com/letta-ai/letta-code/blob/c25f091d0fce1f032e5cdf67ce131d5b474e6576/src/utils/file-lock.ts) 先写完整记录，再用硬链接发布独占锁，避免读取者看到半截 JSON。记录包含 PID、进程启动时间和本次获取的随机 token；释放或回收时核对所有权，避免旧持锁者删掉新锁。仓库操作特别启用 `reapOnlyDeadOwner`：仅因时间过去不会抢走锁；PID 存活且启动时间一致时继续保留。启动时间查询失败则按存活处理，倾向于保留锁。

这套锁覆盖的是接入它的 harness 写入者。前台代理自己改文件，并不自动经过锁，源码对此有明确注释。因此不能把“有仓库锁”表述成所有编辑均已隔离。

## 实际运行上游模块

[`letta_lock_probe.mjs`](../scripts/research/letta_lock_probe.mjs) 校验五个上游文件的 SHA-256，将别名 import 改为临时目录的相对路径，再由 Node 22.22.3 的类型擦除加载。算法、文件系统、Git、`ps` 和子进程都使用真实实现，没有注入它们的返回值。未安装 Bun，也未运行上游原始 Bun 测试套件。

5 项实验通过：

| 检查 | 实验方式 |
|---|---|
| 时间很早的活锁不能被抢 | 获取锁时临时设置 JS 的 `Date.now`，保持真实 PID/启动时间；另一时间区的子进程获取失败 |
| linked worktree 不被无关 checkout 阻塞 | 主 checkout 持锁，子进程对 linked worktree 获取成功 |
| 正常释放后可再次获取 | 释放后另一个进程获取成功 |
| 已退出持锁者可回收 | 子进程真实获取并保持锁，SIGKILL 后重新获取成功 |
| PID 身份不符可回收 | 写入当前 PID、错误启动时间的合成记录，获取成功；没有实际制造 OS 的 PID 复用 |

结果及原始源码校验值见 [letta-lock.json](evidence/letta-lock.json)。回放所需文件均来自上述固定 commit：`src/agent/memory-operation.ts`、`src/agent/memory-git-dir.ts`、`src/utils/file-lock.ts`、`src/utils/process-liveness.ts`、`src/utils/sleep.ts`。下载到同一临时目录后执行：

```sh
node --experimental-strip-types scripts/research/letta_lock_probe.mjs /path/to/downloaded-files
```

这验证所选锁模块在本机的行为，不代表 Letta 的全部调用方都正确持锁，也不验证 hosted 服务或跨电脑的锁。

## memory-use 原来的空隙

原自动同步锁在用户的状态目录，只有 `autosync run` 接入；手动 `sync` 与 `save` 绕过它。配置不同状态目录的两个进程也不共用这把锁。超过 600 秒时，仅根据文件 mtime 删除锁，没有确认旧持锁者是否仍活着。

为复现事务中插入提交的问题，进程 A 已有一个待推提交，在进入 `safe_push` 前暂停。进程 B 执行 `save later.md --no-push`。修改前 B 成功创建新提交，A 恢复后用 `HEAD` 推送，远端最终含有 B 的内容；A 仍按之前读取的数量输出 `pushed 1`。这不是模拟 Git 推送结果：远端裸仓库中实际出现了 `later.md`。

`--no-push` 只约束这次保存命令；后续正常同步仍可以推送这个提交。本实验检查的是新提交能否插入另一进程已经开始的同步事务，不提供“永不上传”模式。

## 采用的最小改动

memory-use 已有标准库实现的 OS 文件锁，本轮直接复用：

- 锁位于实际 Git 目录的 `memory-use-operation.lock`，不受用户状态目录配置影响。
- `save` 从检查/暂存到扫描、提交、同步持有同一把锁；手动同步和自动同步也接入。
- 最多等 1 秒；仍被占用则返回非零，保留待办修改供重试。
- 保留锁文件 inode，不因文件年龄删除；进程退出由 OS 释放持有关系。
- linked worktree 各用自己的 Git 目录；共享引用的单次更新仍由 Git 的锁处理。
- `save` 发现已有 merge/rebase/cherry-pick 时，在暂存文件之前退出。
- 自动同步的最后结果用已有原子写入函数替换，避免多个状态写入者留下半截 JSON；这个文件仍只表示最后一次写入的结果。

没有引入 PID 记录、租约续期或新的后台服务，也没有复制上游锁代码。

## 进程级回归结果

[`test_repository_concurrency.py`](../tests/test_repository_concurrency.py) 与 [`concurrency_probe.py`](../scripts/research/concurrency_probe.py) 使用相同进程屏障回放：

| 场景 | 修改前 | 修改后 |
|---|---|---|
| 同步暂停时 `save --no-push` | 新提交被进行中的同步一并推送 | 保存返回忙；原同步完成后重试可本地提交，远端暂不含新内容 |
| 同步暂停时手动 `sync` | 第二个进程进入并推送 | 返回忙，远端保持原状直到 A 恢复 |
| 活锁被修改为很旧的时间 | 第二个 autosync 抢锁并运行 | 返回忙；原持锁者正常完成 |
| 在推送前杀死持锁者 | 手动 sync 可继续 | 手动 sync 可继续，不需删锁文件 |
| 另一 checkout 和 linked worktree | 可独立同步 | 仍可独立同步 |
| 已有 merge 冲突时 `save` | 提交失败，但新文件先被暂存了 | 返回失败，暂存区逐条记录不变 |

修改前 2/6，通过的控制项是退出恢复和独立 checkout；修改后 6/6。完整原始结果见 [before](evidence/concurrency-before.json)、[after](evidence/concurrency-after.json)。这是定向回归，不是生产故障率。调用暂停发生在 Python 进入 `safe_push` 之前；没有在 Git 子进程执行途中杀死父进程。

```sh
python3 scripts/research/concurrency_probe.py
python3 scripts/research/concurrency_probe.py --module /path/to/earlier-memory_use.py
```

前一阶段未提交源码由[并发修复还原补丁](evidence/before-concurrency.patch)保留。它从本轮源码重建 `6a479d51…`，然后可继续应用 `before-save.patch` 重建 `31b3d44f…`，再应用 `before-symptom.patch` 重建 `1d056bf6…`。每步都必须核对对应 JSON 的完整源码哈希；仅在临时目录应用。前两篇研究中的示例已更新这一顺序。

完整 memory-use 测试 **231 项通过**，Python 3.14.7；命令固定 Xcode 27.0 的 `DEVELOPER_DIR` 与 `SDKROOT`。[验证记录](evidence/concurrency-validation.json)包含源码、测试、探针和补丁校验值。

边界仍然存在：锁只协调使用本轮实现的本地 memory-use 命令。直接运行 Git、编辑器写文件、旧版本命令、跨电脑或网络文件系统不在此协议内。linked worktree 的共享引用也没有变成跨 worktree 的整体事务。Windows 的 byte-range 锁分支和 Linux 尚未运行；SIGKILL 测试不能证明断电持久性或 Git 子进程中途终止的恢复。真实代理的检索问法和最终回答仍未评测。
