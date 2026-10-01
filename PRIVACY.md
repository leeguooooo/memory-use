# memory-use privacy policy

Last updated: 2026-10-01

memory-use is an open-source skill and command-line helper published by Guo Li (leeguooooo). It reads and writes Markdown notes in a git repository that you own.

## What data it handles

- The notes in your own git repository, and the files the helper reads to check them for secrets before a commit.
- A small local config file (`~/.config/memory-use/config.json`) recording where your notes repository is checked out.
- An optional local list of known secret values (`~/.config/memory-use/known-secrets`) used only to block them from being committed. It never leaves your computer.

## Where data goes

- memory-use has no server. The publisher does not receive, collect, store or sell any of your data.
- Notes are pushed and pulled only to the git remote you configure (for example, your private GitHub repository). That provider's own privacy policy applies to what is stored there.
- Unless disabled, the helper may ask the public GitHub API whether a newer release exists. That request contains no personal data. The plugin version disables this check.
- When you use memory-use through an AI assistant, the notes the assistant reads become part of that conversation and are handled under the assistant provider's privacy policy.

## Retention and control

Your data stays in your repository and on your computer for as long as you keep it. You can delete notes, the local config file, or the whole repository at any time; memory-use keeps no other copy.

## Contact

Questions: open an issue at https://github.com/leeguooooo/memory-use/issues or email leeguooooo@gmail.com.
