# memory-use

[English](README.md) | **中文**

给 AI 编程助手用的长期记忆，换会话、换电脑都跟着走。笔记是放在**你自己的私有 git 仓库**里的普通 Markdown；这个 skill 让 Claude Code / Codex 干活前先读笔记、干完把该留下的事实写回去，后台自动同步，换电脑一行命令接上。

属于 `*-use` 家族：`profile-use` 管你是谁，`bitwarden-use` 管密码，**memory-use 管你的机器和服务是怎么配的**（NAS、VPN、服务器、网络、决定和回退办法）。笔记里只写指向前两者的指针，不写值。

## 为什么不用 AI 自带的记忆

| | AI 本地记忆（`~/.claude/projects/*/memory`） | memory-use |
| --- | --- | --- |
| 换电脑 | 没了 | `curl … \| sh -s -- --repo 你/notes` |
| 别的 AI（Codex、另一个 Claude 会话） | 看不到 | 同一个仓库、同一个 skill |
| 自己能看、能改、能搜 | 藏在隐藏目录里 | 普通仓库，带历史 |
| 不小心写进了密码 | 就留在那儿 | 提交前钩子直接拦下 |

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
memory-use lint                # 文章开头格式、README 长度、过期笔记、坏链接
```

## 召回钩子

AI 常常想不起先查笔记。Claude Code 插件自带两个钩子（`hooks/hooks.json`）：

- **UserPromptSubmit**：你的话里提到笔记认识的东西（术语表里的任何别名，包括「windows 电脑」这种口语叫法，或者专栏名），AI 会收到一行提示：`the notes know about leo-desktop — 先 memory-use brief leo-desktop`。没提到就什么都不加；约 50 ms，纯标准库。
- **SessionStart**：只有笔记仓库没接上、或自动同步卡住时才说话。

用 `install.sh` 装、没装插件的话，把下面这段加进 `~/.claude/settings.json`：

```json
"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "memory-use hook prompt", "timeout": 5}]}],
  "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "memory-use hook session", "timeout": 5}]}]
}
```

## 自动同步

`memory-use autosync on`（`init` 默认打开）每 15 分钟跑一次：macOS 用 launchd，Linux 用 systemd 用户定时器或 cron，Windows 用任务计划程序。

- 先 fetch，能快进就快进；本地有提交就 rebase 到远端再 push；
- **从不替你提交**，也不 stash：没提交的改动可能是另一个 AI 会话写到一半的笔记，原样不动；
- 有冲突就干净地放弃，`doctor` 和 `autosync status` 里能看到，留给人处理。

AI 用 `save` 提交，必须写明文件，所以同时开的几个会话不会把彼此的改动一起提交。

## 换电脑

在旧电脑上：

```sh
memory-use migrate          # 有东西还没上 GitHub（没推的提交、没提交的文件、stash）就返回 1
memory-use migrate --push   # 先推上去
```

它还会列出按设计不跟着走的东西（本地的已知密钥列表、AI 自己的记忆文件），并打印新电脑上要跑的那一行。其他东西（SSH 密钥、VPN、密码管理器）写在笔记仓库的 `setup-new-computer.md` 里，模板自带一份。

## 密钥

提交前钩子（以及 `save`、`check`）会拒绝看起来像密钥的内容：私钥，GitHub / OpenAI / Anthropic / AWS / Slack 令牌，JWT，还有中英文的赋值写法（`password: …`、`密码：…`、`路由器密码是 …`）。指针和说明文字能通过（`🔑 bw:<uuid>`、`~/.ssh/id_ed25519`、`在 secrets.env 里`）。配合 [bitwarden-use](https://github.com/leeguooooo/bitwarden-use)，`memory-use secret put` 存值并打印指针，`secret hashes` 让钩子按哈希拦下密码库 `memory` 文件夹里的任何密码。装了 [profile-use](https://github.com/leeguooooo/profile-use) 时，钩子还会拦个人资料。

## 配置

| | |
| --- | --- |
| `~/.config/memory-use/config.json` | `{"repo": "你/notes", "dir": "~/github.com/notes"}`，由 `init` 写入 |
| `MEMORY_USE_DIR` / `MEMORY_USE_REPO` | 覆盖仓库路径 / 仓库名 |
| `~/.config/memory-use/known-secrets` | 要拦的真实密钥，一行一个，不进 git |
| `MEMORY_USE_NO_UPDATE_CHECK=1` | 关掉每天一次的新版本提示 |

纯标准库 Python 3.9+，无依赖。测试：`python3 -m unittest discover -s tests`。

## 许可

MIT
