# memory-use

[English](README.md) | **中文**

[更新日志](CHANGELOG.zh-CN.md) · [版本发布与下载](https://github.com/leeguooooo/memory-use/releases) · [使用指南](docs/guide.zh-CN.md)

给 Claude Code、Codex 用的长期记忆，保存在**你自己的私有 Git 仓库**中。干活前查已有笔记，做完后保存核实过的改动，供下次会话或换电脑时使用。

## 技术强项

| 技术强项 | 对用户的好处 |
| --- | --- |
| **Markdown + Git，可直接迁移** | 笔记能直接读写、追溯历史，也能带到另一个助手或电脑。 |
| **召回带来源** | 片段排序、术语别名、Unicode 规范化和故障链接，把问题带到可核对的文件与行号。 |
| **Claude Code 失败触发召回** | 重试前提示查相关笔记，收尾时提醒写下新学到的修复；同一会话去重提示。 |
| **上下文有预算** | 在字符上限内保留有用片段、来源和复核提醒，给当前任务留下更多上下文空间。 |
| **检索效果可测** | 用自己的问题集生成 JSON 报告，分清没搜到、没选中还是被预算挤掉。 |
| **提交前检查密钥** | 提交前识别常见凭据及配置过的已知密钥，笔记里可改存密码库指针。 |
| **本地写入可恢复** | 明确文件范围的提交、checkout 锁、hook 状态原子更新和同步重试，帮助并行会话保留工作成果。 |

## 安装

需要 Git 和 Python 3.9+；工具本身使用 Python 标准库。

```sh
gh auth login  # 用于访问私有 GitHub 笔记仓库
curl -fsSL https://raw.githubusercontent.com/leeguooooo/memory-use/main/install.sh | sh -s -- --repo 你/notes
```

新建私有笔记仓库时加 `--create`。安装器会链接技能、启用密钥检查和定时同步；需要手动同步时加 `--no-autosync`。

## 使用

直接让助手查「之前 VPN 怎么配的」，或说「记下这次改动和回滚方法」。也可以用命令：

```sh
memory-use brief vpn warp --max 1500
memory-use save vpn/servers.md -m "vpn: 更新出口节点"
```

命令和配置见[使用指南](docs/guide.zh-CN.md)；失败到经验的流程、实现证据和导入缺口见[记忆机制](docs/memory-lifecycle.zh-CN.md)。

[MIT 许可](LICENSE)
