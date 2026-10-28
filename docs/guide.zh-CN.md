# 使用指南

[English](guide.md) | **中文**

[项目介绍](../README.zh-CN.md) · [更新日志](../CHANGELOG.zh-CN.md) · [记忆机制](memory-lifecycle.zh-CN.md)

## 安装

```sh
gh auth login   # 笔记仓库是私有的

# 已经有笔记仓库
curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo 你/notes

# 从零开始：用模板新建一个私有仓库
curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo 你/notes --create
```

它会把 skill 链进 `~/.agents/skills`、`~/.claude/skills`（有 `~/.codex/skills` 也链），把 `memory-use` 命令放到 `~/.local/bin`，克隆笔记，启用提交前的密钥检查，打开自动同步。`memory-use doctor` 看状态。不在 GitHub 上也行：`--repo git@gitlab.com:你/notes.git`。

也可以装整个家族：Claude Code 里 `/plugin install use-family@leeguooooo-plugins`，或者 [leeguooooo/plugins](https://github.com/leeguooooo/plugins) 的 `install-use-family.sh`；装完运行一次 `memory-use init --repo 你/notes`。

## 用法

直接跟 AI 说：「记一下」「我们之前 VPN 怎么弄的」「换电脑了」。底下对应的命令：

```sh
memory-use brief vpn warp      # 上下文包：最相关的笔记和要点、待办、专栏摘要
memory-use search nas backup    # 排序搜索；术语表里的别名会自动展开
memory-use save vpn/servers.md -m "vpn: 新出口节点"   # 查密钥、只提交这几个文件、pull --rebase、push
memory-use new photos "照片" "照片放在哪、怎么备份"
memory-use lint                # 文章开头格式、README 长度、过期笔记、重复文章、坏链接
memory-use eval                # 搜索把对的笔记排第一的比例（evals.md、术语表里的「见」链接、「出问题先看这里」表格、文章标题）
memory-use lessons             # 哪些笔记反复撞上命令失败、事后有没有人补过修法
```

在笔记前八行写 `> 冲突：[另一篇](other.md)`，可标记尚未解决的矛盾。`show`、`search`、`brief` 和 `lint` 会提醒；核对并解决后再移除声明。工具不会自行判断哪篇正确。

`lint` 检查简单行内本地链接的文件是否存在，支持百分号编码的路径，并跳过围栏代码块和行内代码示例。缩进代码块仍会按普通文本检查；锚点、引用式链接和远程网址不在检查范围内。

`brief` 查询具体主题时，每篇笔记最多展示两个相关小节，略过弱匹配，只带入主题相关的待办、摘要行和提交标题。单独查询专栏名或它的别名（例如 `brief nas` / `brief pan`），或不带主题时，仍展示专栏概览。`--max 1500` 限制整个上下文正文的字符数，包括截断提示（最小 128）；重复输入同一术语的别名不会重复计分。Markdown 代码块中的 `#` 不作为标题。

术语表可以写 `- 出口故障 / gateway-recovery — 见 vpn/route.md`。即使文章正文没用这些词，搜索也能找到它，并标注 `[glossary link]`。`search`、`brief`、`show` 会提示超过复核期限或已废弃的笔记；默认期限是 120 天，可用 `复核：30d` 调整，用 `复核：长期` 关闭按日期提醒。读取不会刷新笔记的更新日期。

搜索和召回钩子统一处理 Unicode 兼容写法：`ＷＡＲＰ` 能匹配 `WARP`，组合方式不同的重音字符也能匹配。笔记、展示的片段和文件路径保留原文。这只是字符规范化，不提供语义搜索。

`brief` 预算不足时，会缩短过长条目并标出省略，让后面的相关来源也有机会保留；完整路径和复核警告随片段展示，背景摘要使用剩余空间。多个钩子同时更新本地状态时会串行处理；不可读的状态文件会保留，并由 `doctor` 提示。[故障实验和验证边界](deep-experiments.md)记录了具体结果。

专栏 README 的故障处理表也会参与召回。表中条件能覆盖查询的字面内容时，即使文章正文措辞不同，也能沿显式链接找到它，并标注 `[symptom link]` 和 `trigger:` 来源；实体词和否定标记必须对应。条件文字只是导航依据，仍要核对正文片段是否包含答案。具体见[提问到答案片段的验证](recall-evaluation.md)。

`eval` 同时检查搜索排名和最终 `brief` 是否保留了目标笔记的入口与片段。可运行 `memory-use eval --cases /笔记库外/questions.md --max 1500 --json`，问题按 `- 提问 → 专栏/文章.md` 编写。外部问题集不进入搜索范围；结果会区分没搜到、被筛掉、预算或片段不足，目标文件无效则单独报告。标题和术语表测试只验证导航，不能证明自然提问有效。方法和实测缺口见[召回评测](recall-evaluation.md)。

## 召回钩子

AI 常常想不起先查笔记。Claude Code 插件自带四个钩子（`hooks/hooks.json`）：

- **UserPromptSubmit**：你的话里提到笔记认识的东西（术语表里的任何别名，包括「windows 电脑」这种口语叫法，或者专栏名），AI 会收到一行提示：`the notes know about leo-desktop — 先 memory-use brief leo-desktop`。没提到就什么都不加。插件通过 `scripts/prompt_hook.py` 给工作进程三秒预算；超时便终止、回收进程，安静返回成功。只有成功完成的输出才会转发。Python 启动与进程清理另有开销，宿主超时仍为五秒。
- **PostToolUseFailure**（Bash）：命令失败了，而诊断输出或解析出的操作目标能对应已有笔记，AI 在重试之前会收到同样的提示：`that command failed and the notes know about nas — 先 memory-use brief nas`。普通 shell、路径不存在和编码错误保持安静，引用文本和无关文件参数不参与话题判断。同一条命令第三次失败、又没提到任何已知的东西时，会拿程序名和诊断词去搜全部笔记，命中一篇提到这个命令的笔记才提醒。同一个会话里每个东西、每篇笔记只提醒一次，反复重试不会刷屏。
- **Stop**：出过上面那种失败提醒、而且之后笔记仓库没有新提交时，AI 准备结束这一轮前对同一话题、同一会话最多问一次：绕过这个失败的办法要不要写进笔记。后续同话题失败不会再次触发。其余时候不说话。（PreCompact 和 SessionEnd 的输出到不了 AI，这里是上下文被压缩掉之前最后一次能把经验记下来的机会。）
- **SessionStart**：只有笔记仓库没接上、或自动同步卡住时才说话。

用 `install.sh` 装、没装插件的话，把下面这段加进 `~/.claude/settings.json`：

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ~/.agents/skills/memory-use/scripts/prompt_hook.py", "timeout": 5}]}],
  "PostToolUseFailure": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "memory-use hook tool", "timeout": 5}]}],
  "Stop": [{"hooks": [{"type": "command", "command": "memory-use hook stop", "timeout": 5}]}],
  "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "memory-use hook session", "timeout": 5}]}]
}
```

## 自动同步

`memory-use autosync on`（`init` 默认打开）每 15 分钟跑一次：macOS 用 launchd，Linux 用 systemd 用户定时器或 cron，Windows 用任务计划程序。

- 先 fetch，能快进就快进；本地有提交就 rebase 到远端再 push；
- **从不替你提交**，也不 stash：没提交的改动可能是另一个 AI 会话写到一半的笔记，原样不动；
- 有冲突就干净地放弃，`doctor` 和 `autosync status` 里能看到，留给人处理。

AI 用 `save` 提交，必须写明文件，所以同时开的几个会话不会把彼此的改动一起提交。

`save`、`sync` 和自动同步共用处理规则。推送被拒绝会返回非零，保留本地提交；即使文件没有新变化，重试 `save` 也会尝试同步待推提交。`--no-push` 仍只操作本地。双方都有新提交且存在未提交修改时，保留现场；rebase 冲突则中止并恢复原提交。对照源码和故障实验见 [Letta 同步研究](letta-sync-study.md)。

三个命令共用 checkout 的 Git 操作锁，`save` 从暂存到提交、同步全程持锁。等待最多 1 秒仍被占用则返回非零，稍后重试；linked worktree 各自持锁。`--no-push` 只让这次保存不推送，后续同步仍可推送该提交。边界和回归见[进程并发实验](repository-concurrency-study.md)。

**只推到绑定的私有仓库。** `save`、`sync` 和自动同步在推送之前，会先算出这次推送实际会去哪（`branch.pushRemote`、`remote.pushDefault`、每一个 `pushurl`、`insteadOf` 改写），只要有一个地址不是 `init` 时绑定的那个仓库就拒绝。在 GitHub 上还会通过 API 确认仓库**仍是私有**，并且和第一次核对时是同一个仓库 id（删掉重建、转移过的都不算）。凡是核对不了的情况——`gh` 没登录或登录的账号看不到这个仓库、离线、仓库变成了公开——一律不推：提交留在本地，`doctor` 和 `autosync status` 会说明原因。推送时显式写明核对过的 remote 和分支。其他平台（GitLab、自建、本地路径）只核对地址，`doctor` 会标明那里无法确认可见性。`save --no-push` 只提交、不推送。

## 换电脑

在旧电脑上：

```sh
memory-use migrate          # 有东西还没上 GitHub（没推的提交、没提交的文件、stash）就返回 1
memory-use migrate --push   # 先推上去
```

它还会列出按设计不跟着走的东西（本地的已知密钥列表、AI 自己的记忆文件），并打印新电脑上要跑的那一行。其他东西（SSH 密钥、VPN、密码管理器）写在笔记仓库的 `setup-new-computer.md` 里，模板自带一份。

## 密钥

提交前钩子（以及 `save`、`check`）会拒绝看起来像密钥的内容：私钥，GitHub / OpenAI / Anthropic / AWS / Slack 令牌，JWT，还有中英文的赋值写法（`password: …`、`密码：…`、`路由器密码是 …`）。指针和说明文字能通过（`🔑 bw:<uuid>`、`~/.ssh/id_ed25519`、`在 secrets.env 里`）。配合 [bitwarden-use](https://github.com/leeguooooo/bitwarden-use)，`memory-use secret put` 接收单行密码，核对新条目和回读内容后才打印指针；多行内容和私钥块应使用原生 `bwu item put` JSON，`secret hashes` 让钩子按哈希拦下密码库 `memory` 文件夹里的任何密码。装了 [profile-use](https://github.com/leeguooooo/profile-use) 时，钩子还会拦个人资料。

## 配置

| | |
| --- | --- |
| `~/.config/memory-use/config.json` | `{"repo": "你/notes", "dir": "~/github.com/notes"}`，由 `init` 写入 |
| `MEMORY_USE_DIR` / `MEMORY_USE_REPO` | 覆盖仓库路径 / 仓库名 |
| `~/.config/memory-use/known-secrets` | 要拦的真实密钥，一行一个，不进 git |
| `MEMORY_USE_NO_UPDATE_CHECK=1` | 关掉每天一次的新版本提示 |

纯标准库 Python 3.9+，无依赖。测试：`python3 -m unittest discover -s tests`。

发版时同步更新中英两份更新日志。Release 说明先写英文，再写中文，并链接对应语言的更新日志。

## 许可

MIT
