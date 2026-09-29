"""Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="memory-use-test-")
os.environ.update({"MEMORY_USE_NO_UPDATE_CHECK": "1", "MEMORY_USE_CONFIG_DIR": f"{_SANDBOX}/config",
                   "MEMORY_USE_STATE_DIR": f"{_SANDBOX}/state", "MEMORY_USE_REPO": "",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import memory_use as mu  # noqa: E402


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


ARTICLE_OK = "# Lucky\n\n> 更新：2026-09-15 · 状态：在用\n\n**要点**\n- Lucky 在 443\n\n## 细节\n\n反代规则\n"


class MemoryUseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        sh("git", "init", "-q", cwd=self.root)
        sh("git", "config", "user.email", "t@example.com", cwd=self.root)
        sh("git", "config", "user.name", "t", cwd=self.root)
        (self.root / "AGENTS.md").write_text("# rules\n\n## 专栏\n\n- `nas/` — NAS\n")
        (self.root / "README.md").write_text("# memory\n\n| 专栏 | 内容 |\n|---|---|\n| [NAS](nas/README.md) | 群晖 |\n")
        (self.root / "glossary.md").write_text("# 术语表\n\n- NAS / pan / nas.example.com / 群晖 — 家里的 NAS\n- 190 / jp2 / officemac — 办公室 Mac\n")
        (self.root / "nas").mkdir()
        (self.root / "nas" / "README.md").write_text("# NAS\n\n## 网络\n\nLucky 占用 443 端口\n\n## 文章\n\n- [lucky.md](lucky.md) — Lucky\n")
        (self.root / "nas" / "todo.md").write_text("# todo\n\n- [ ] 改密码\n- [x] done\n- [ ] 备份\n")
        (self.root / "nas" / "lucky.md").write_text(ARTICLE_OK)
        (self.root / "nas" / "office.md").write_text(ARTICLE_OK.replace("Lucky", "办公室").replace("443", "officemac 上跑出口"))
        sh("git", "add", "-A", cwd=self.root)
        sh("git", "commit", "-qm", "init", cwd=self.root)
        os.environ["MEMORY_USE_DIR"] = str(self.root)
        os.environ["MEMORY_USE_SECRETS"] = str(Path(self.tmp.name) / "known")
        mu.KNOWN_SECRETS = Path(os.environ["MEMORY_USE_SECRETS"])

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = mu.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, buf.getvalue()

    def test_search_ranks_sections_with_heading_trail(self):
        rc, out = self.run_cmd("search", "lucky", "443")
        self.assertEqual(rc, 0)
        first = out.splitlines()[0]
        self.assertIn("nas/", first)
        self.assertIn("Lucky", out)
        rc, _ = self.run_cmd("search", "nomatch-zzz")
        self.assertEqual(rc, 1)

    def test_heading_hits_outrank_body_hits(self):
        rc, out = self.run_cmd("search", "细节")
        self.assertIn("‹", out.splitlines()[0])
        self.assertIn("细节", out.splitlines()[0])

    def test_glossary_alias_expansion(self):
        rc, out = self.run_cmd("search", "190")          # the note only says "officemac"
        self.assertEqual(rc, 0, out)
        self.assertIn("nas/office.md", out)

    def test_brief_packs_section_summary_todos_and_history(self):
        rc, out = self.run_cmd("brief", "nas")
        self.assertEqual(rc, 0)
        self.assertIn("## nas/README.md (summary", out)
        self.assertNotIn("lucky.md](lucky.md)", out)          # article lists are not part of the summary
        self.assertIn("open todo (2)", out)
        self.assertIn("recent changes", out)
        rc, out = self.run_cmd("brief", "pan", "--max", "300")
        self.assertIn("aliases:", out)
        self.assertIn("truncated", out)

    def test_lint_flags_missing_headers_and_broken_links(self):
        rc, out = self.run_cmd("lint")
        self.assertIn("lint: clean", out)
        (self.root / "nas" / "bare.md").write_text("# bare\n\n[x](missing.md)\n")
        rc, out = self.run_cmd("lint", "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("nas/bare.md: no `> 更新", out)
        self.assertIn("no **要点**", out)
        self.assertIn("broken link → missing.md", out)

    def test_todo_lists_only_open_items(self):
        rc, out = self.run_cmd("todo")
        self.assertIn("改密码", out)
        self.assertIn("备份", out)
        self.assertNotIn("done", out)
        self.assertIn("2 open item(s)", out)

    def test_new_section_is_listed(self):
        rc, _ = self.run_cmd("new", "photos", "照片", "家里照片")
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / "photos" / "todo.md").exists())
        self.assertIn("[照片](photos/README.md)", (self.root / "README.md").read_text())
        self.assertIn("`photos/` — 家里照片", (self.root / "AGENTS.md").read_text())
        rc, out = self.run_cmd("ls")
        self.assertIn("photos", out)

    def test_check_flags_secrets_but_not_placeholders(self):
        bad = self.root / "nas" / "bad.md"
        bad.write_text("password: hunter2hunter2\nghp_" + "a" * 36 + "\n")  # memory-use: allow (test fixture)
        ok = self.root / "nas" / "ok.md"
        ok.write_text("password=<redacted>\n密码在 secrets.env 里\ntoken: ${CF_API_TOKEN}\n")
        rc, out = self.run_cmd("check", str(bad))
        self.assertEqual(rc, 1)
        self.assertIn("assignment of a secret", out)
        self.assertIn("GitHub token", out)
        rc, out = self.run_cmd("check", str(ok))
        self.assertEqual(rc, 0, out)

    def test_known_secret_literal_is_caught(self):
        mu.KNOWN_SECRETS.write_text("# comment\nblogpw-zz99\n")
        f = self.root / "nas" / "leak.md"
        f.write_text("博客密码是 blogpw-zz99\n")  # memory-use: allow (test fixture)
        rc, out = self.run_cmd("check", str(f))
        self.assertEqual(rc, 1)
        self.assertIn("known secret value", out)

    def test_save_refuses_secrets_and_unstages(self):
        f = self.root / "nas" / "leak.md"
        f.write_text("api_key = sk-ant-" + "b" * 30 + "\n")  # memory-use: allow (test fixture)
        rc, out = self.run_cmd("save", "nas/leak.md", "-m", "oops")
        self.assertEqual(rc, 1)
        self.assertIn("refusing to commit", out)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.root, capture_output=True, text=True).stdout
        self.assertEqual(staged.strip(), "")

    # ---- issue #1: Chinese secret assignments
    def test_chinese_secret_assignments_are_flagged(self):
        f = self.root / "nas" / "zh.md"
        f.write_text("# t\n- Mac mini 的 sudo 密码：Fakepw123ZZ\n- 路由器密码是 Fakepw456YY\n"  # memory-use: allow
                     "- 管理员口令 = Fakepw789XX\n- sudo password: Fakepw111VV\n")         # memory-use: allow
        found = mu.scan_text(f.read_text(), [], set())
        self.assertEqual(sorted(n for n, _ in found), [2, 3, 4, 5], found)

    def test_chinese_pointers_and_prose_are_not_flagged(self):
        text = ("密码：不记录，问 owner\n密钥：~/.ssh/id_ed25519_pan\n密码是 bw:11111111-2222-3333-4444-555555555555\n"
                "密码在 190 的 secrets.env 里\n密码是保存在钥匙串里的那个\n令牌：secrets.env\n密码：<在 Bitwarden>\n")
        self.assertEqual(mu.scan_text(text, [], set()), [])

    # ---- issue #2: aliases match whole tokens
    def _glossary(self, extra):
        g = self.root / "glossary.md"
        g.write_text(g.read_text() + extra)

    def test_ip_alias_does_not_match_longer_ip(self):
        self._glossary("- 光猫 / XG-100NE / 192.168.1.1 — 办公室光猫\n")
        (self.root / "nas" / "desk.md").write_text(ARTICLE_OK.replace("Lucky 在 443", "台式机 192.168.1.14"))
        (self.root / "nas" / "modem.md").write_text(ARTICLE_OK.replace("Lucky 在 443", "光猫后台 192.168.1.1。"))
        rc, out = self.run_cmd("search", "光猫")
        self.assertIn("nas/modem.md", out)
        self.assertNotIn("nas/desk.md", out)

    def test_typed_numbers_exact_but_typed_words_substring(self):
        (self.root / "nas" / "years.md").write_text(ARTICLE_OK.replace("Lucky 在 443", "since 1900, rustdesk"))
        rc, out = self.run_cmd("search", "190")
        self.assertNotIn("nas/years.md", out)
        rc, out = self.run_cmd("search", "rust")
        self.assertIn("nas/years.md", out)

    def test_multiword_alias_and_filename_rank_the_dedicated_article_first(self):
        self._glossary("- leo-desktop / windows 电脑 / 台式机 — desk\n")
        (self.root / "nas" / "leo-desktop.md").write_text(ARTICLE_OK.replace("Lucky 在 443", "账号 A 和 B"))
        big = "# 客户端\n\n> 更新：2026-09-15 · 状态：在用\n\n**要点**\n- iPhone 用小火箭\n\n## iPhone\n\nx\n\n## 台式机：leo-desktop\n\nwindows chrome 电脑\n"
        (self.root / "nas" / "clients.md").write_text(big)
        rc, out = self.run_cmd("search", "windows", "电脑")
        self.assertIn("nas/leo-desktop.md:1", out.splitlines()[0], out)
        rc, out = self.run_cmd("brief", "台式机")
        self.assertNotIn("iPhone 用小火箭", out)          # a mid-file hit does not drag in the file's key points

    def test_prompt_hook_nudges_only_on_known_names(self):
        self._glossary("- leo-desktop / windows 电脑 / 台式机 — desk\n- mp / moviepilot — media\n")
        def hook(prompt):
            sys_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"prompt": prompt}))
            try:
                return self.run_cmd("hook", "prompt")
            finally:
                sys.stdin = sys_stdin
        rc, out = hook("把 Chrome 登录态同步到 windows 电脑")
        self.assertEqual(rc, 0)
        self.assertIn("memory-use brief leo-desktop", out)
        rc, out = hook("the mp3 file is broken; what about the weather")   # 2-letter alias "mp" is ignored
        self.assertEqual(out, "")
        rc, out = hook("check the nas section")                               # section names count too
        self.assertIn("nas", out)

    def test_session_hook_silent_when_healthy(self):
        mu.STATE_FILE = Path(self.tmp.name) / "autosync.json"
        rc, out = self.run_cmd("hook", "session")
        self.assertEqual((rc, out), (0, ""))
        mu.STATE_FILE.write_text(json.dumps({"ok": False, "result": "push failed: denied"}))
        rc, out = self.run_cmd("hook", "session")
        self.assertIn("autosync is stuck", out)

    def test_lint_warns_on_router_ip_alias_only(self):
        self._glossary("- 光猫 / 192.168.1.1 — 办公室光猫\n- 台式机 / 192.168.1.14 — desk\n")
        rc, out = self.run_cmd("lint")
        self.assertIn("router address `192.168.1.1`", out)
        self.assertNotIn("192.168.1.14", out)

    # ---- issue #3: brief keeps the best match even with a huge README
    def test_brief_leads_with_best_match_and_trims_readmes(self):
        big = "# NAS\n\n## 关键事实\n\n" + "\n".join(f"- fact {i}" for i in range(200)) + "\n\n## 多用户\n\n" + "noise\n" * 200
        (self.root / "nas" / "README.md").write_text(big)
        (self.root / "nas" / "remote.md").write_text(
            "# RustDesk 远程桌面\n\n> 更新：2026-09-29 · 状态：在用\n\n**要点**\n- hbbs 在 mini 上\n\n## 细节\n\nrustdesk 细节\n")
        rc, out = self.run_cmd("brief", "rustdesk", "--max", "1500")
        self.assertIn("nas/remote.md", out)
        self.assertIn("hbbs 在 mini 上", out)
        self.assertLess(out.index("best matching notes"), out.index("## nas/README.md"))
        self.assertNotIn("noise", out)
        self.assertNotIn("fact 100", out)

    def test_new_section_in_empty_english_template(self):
        (self.root / "README.md").write_text("# Notes\n\n## Sections\n\n| Section | What |\n|---|---|\n\nmore\n")
        (self.root / "AGENTS.md").write_text("# rules\n\n## Sections\n\n<!-- x -->\n\n## Other\n")
        rc, out = self.run_cmd("new", "home", "Home", "home network")
        readme = (self.root / "README.md").read_text()
        self.assertIn("|---|---|\n| [Home](home/README.md) | home network |", readme)
        self.assertEqual(readme.count("| Section |"), 1)
        agents = (self.root / "AGENTS.md").read_text()
        self.assertLess(agents.index("`home/`"), agents.index("## Other"))
        self.assertIn("## Key facts", (self.root / "home" / "README.md").read_text())

    def test_save_commits_a_deleted_directory(self):
        bare = Path(self.tmp.name) / "r.git"
        sh("git", "clone", "-q", "--bare", str(self.root), str(bare), cwd=self.root)
        sh("git", "remote", "add", "origin", str(bare), cwd=self.root)
        sh("git", "push", "-q", "-u", "origin", "HEAD", cwd=self.root)
        sh("git", "rm", "-rq", "nas", cwd=self.root)
        rc, out = self.run_cmd("save", "nas", "-m", "drop nas")
        self.assertEqual(rc, 0, out)
        self.assertIn("pushed", out)

    def test_save_requires_paths(self):
        rc, _ = self.run_cmd("save", "-m", "x")
        self.assertNotEqual(rc, 0)


FAKE_BWU = """#!/bin/sh
# fake bitwarden-use for tests
case "$1" in
  unlocked) exit 0 ;;
  list) echo '[{"id":"11111111-2222-3333-4444-555555555555","name":"router","user":"admin","folder":"memory","type":"Login"},{"id":"99999999-2222-3333-4444-555555555555","name":"bank","user":null,"folder":"private","type":"Login"}]' ;;
  get) echo "s3cret-router-pw" ;;
  sync) exit 0 ;;
  *) exit 2 ;;
esac
"""


class SecretBridgeTest(MemoryUseTest):
    def setUp(self):
        super().setUp()
        b = Path(self.tmp.name) / "fake-bwu"
        b.write_text(FAKE_BWU); b.chmod(0o755)
        os.environ["MEMORY_USE_BWU"] = str(b)
        mu.KNOWN_HASHES = Path(self.tmp.name) / "hashes"

    def tearDown(self):
        os.environ.pop("MEMORY_USE_BWU", None)
        super().tearDown()

    def test_find_prints_pointer_not_values(self):
        rc, out = self.run_cmd("secret", "find", "router")
        self.assertEqual(rc, 0)
        self.assertIn("bw:11111111-2222-3333-4444-555555555555（router）", out)
        self.assertNotIn("s3cret", out)

    def test_get_refuses_to_print_by_default(self):
        rc, out = self.run_cmd("secret", "get", "bw:11111111-2222-3333-4444-555555555555")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("s3cret", out)

    def test_get_env_passes_value_to_command_only(self):
        marker = Path(self.tmp.name) / "seen"
        rc, out = self.run_cmd("secret", "get", "router", "--env", "PW", "--", "/bin/sh", "-c", f'printf %s "$PW" > {marker}')
        self.assertEqual(rc, 0)
        self.assertEqual(marker.read_text(), "s3cret-router-pw")
        self.assertNotIn("s3cret", out)

    def test_refs_flag_dangling_pointers(self):
        (self.root / "nas" / "p.md").write_text(ARTICLE_OK + "\n🔑 bw:11111111-2222-3333-4444-555555555555\n🔑 bw:00000000-0000-0000-0000-000000000000\n")
        rc, out = self.run_cmd("secret", "refs")
        self.assertEqual(rc, 1)
        self.assertIn("bw:00000000-0000-0000-0000-000000000000 not found", out)

    def test_hashes_catch_literal_vault_secret(self):
        rc, out = self.run_cmd("secret", "hashes")
        self.assertIn("1 secret hash", out)
        self.assertNotIn("s3cret", mu.KNOWN_HASHES.read_text())
        f = self.root / "nas" / "leak2.md"
        f.write_text("路由器密码：s3cret-router-pw\n")  # memory-use: allow (test fixture)
        rc, out = self.run_cmd("check", str(f))
        self.assertEqual(rc, 1)
        self.assertIn("vault secret (hash match)", out)


FAKE_OSA = """#!/bin/sh
# fake osascript: record the script, answer like Reminders would
cat > "$OSA_LOG"
if grep -q "whose completed is false" "$OSA_LOG"; then
  printf '没日期的事\\t\\n续费域名\\t2026-10-01 09:00\\n'
else
  echo "x-apple-reminder://FAKE-ID"
fi
"""


class RemindBridgeTest(MemoryUseTest):
    def setUp(self):
        super().setUp()
        b = Path(self.tmp.name) / "fake-osa"
        b.write_text(FAKE_OSA); b.chmod(0o755)
        self.log = Path(self.tmp.name) / "osa.log"
        os.environ["MEMORY_USE_OSASCRIPT"] = str(b)
        os.environ["OSA_LOG"] = str(self.log)
        mu.KNOWN_HASHES = Path(self.tmp.name) / "hashes"

    def tearDown(self):
        os.environ.pop("MEMORY_USE_OSASCRIPT", None)
        os.environ.pop("OSA_LOG", None)
        super().tearDown()

    def test_add_builds_locale_independent_date_and_escapes_title(self):
        rc, out = self.run_cmd("remind", "add", '续费 "NAS" 证书', "--due", "2026-10-01 18:30", "--list", "家里")
        self.assertEqual(rc, 0, out)
        script = self.log.read_text()
        self.assertIn('name:"续费 \\"NAS\\" 证书"', script)
        self.assertIn("set month of d to 10", script)
        self.assertIn("set time of d to 66600", script)
        self.assertIn('tell list "家里"', script)
        self.assertIn('⏰ 提醒事项：续费 "NAS" 证书（2026-10-01）', out)

    def test_date_only_defaults_to_nine_and_bad_date_fails(self):
        rc, _ = self.run_cmd("remind", "add", "x", "--due", "2026-10-01")
        self.assertEqual(rc, 0)
        self.assertIn("set time of d to 32400", self.log.read_text())
        self.log.unlink()
        rc, _ = self.run_cmd("remind", "add", "x", "--due", "next week")
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.log.exists())

    def test_refuses_secret_in_reminder(self):
        mu.KNOWN_SECRETS.write_text("blogpw-zz99\n")
        rc, _ = self.run_cmd("remind", "add", "博客密码 blogpw-zz99")
        self.assertNotEqual(rc, 0)
        self.assertFalse(self.log.exists())

    def test_ls_puts_dated_items_first(self):
        rc, out = self.run_cmd("remind", "ls")
        self.assertEqual(rc, 0)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("2026-10-01 09:00"), out)
        self.assertIn("没日期的事", lines[1])
        self.assertIn("2 open reminder(s)", out)


class SyncTest(unittest.TestCase):
    """init / autosync / migrate against a local bare remote, with a throwaway HOME."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.home = t / "home"; self.home.mkdir()
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        seed = t / "seed"; seed.mkdir()
        sh("git", "init", "-q", "-b", "main", cwd=seed)
        (seed / "README.md").write_text("# notes\n")
        sh("git", "add", "-A", cwd=seed); sh("git", "commit", "-qm", "seed", cwd=seed)
        self.bare = t / "remote.git"
        sh("git", "clone", "-q", "--bare", str(seed), str(self.bare), cwd=t)
        self.a, self.b = t / "a", t / "b"
        sh("git", "clone", "-q", str(self.bare), str(self.a), cwd=t)
        sh("git", "clone", "-q", str(self.bare), str(self.b), cwd=t)
        mu.CONFIG_DIR = t / "cfg"; mu.CONFIG = mu.CONFIG_DIR / "config.json"
        mu.KNOWN_SECRETS = mu.CONFIG_DIR / "known-secrets"
        mu.STATE_DIR = t / "state"; mu.STATE_FILE = mu.STATE_DIR / "autosync.json"

    def tearDown(self):
        os.environ.pop("MEMORY_USE_DIR", None)
        if self.old_home:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()

    def commit(self, repo, name, text):
        (repo / name).write_text(text)
        sh("git", "add", name, cwd=repo); sh("git", "commit", "-qm", name, cwd=repo)

    def head(self, repo):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()

    def test_autosync_rebases_pushes_and_pulls(self):
        self.commit(self.a, "a.md", "from a\n"); sh("git", "push", "-q", cwd=self.a)
        self.commit(self.b, "b.md", "from b\n")                      # b: ahead 1, behind 1
        ok, msg = mu.autosync_once(self.b)
        self.assertTrue(ok, msg); self.assertIn("pushed 1", msg)
        ok, msg = mu.autosync_once(self.a)
        self.assertTrue(ok, msg); self.assertIn("pulled 1", msg)
        self.assertTrue((self.a / "b.md").exists())
        self.assertEqual(self.head(self.a), self.head(self.b))

    def test_autosync_leaves_diverged_dirty_tree_alone(self):
        self.commit(self.a, "a.md", "from a\n"); sh("git", "push", "-q", cwd=self.a)
        self.commit(self.b, "b.md", "from b\n")
        (self.b / "README.md").write_text("half-written by another session\n")
        before = self.head(self.b)
        ok, msg = mu.autosync_once(self.b)
        self.assertFalse(ok); self.assertIn("left alone", msg)
        self.assertEqual(self.head(self.b), before)
        self.assertIn("another session", (self.b / "README.md").read_text())

    def test_autosync_fast_forwards_around_unrelated_edits(self):
        self.commit(self.a, "a.md", "from a\n"); sh("git", "push", "-q", cwd=self.a)
        (self.b / "README.md").write_text("local edit\n")
        ok, msg = mu.autosync_once(self.b)
        self.assertTrue(ok, msg); self.assertIn("uncommitted edits present", msg)
        self.assertTrue((self.b / "a.md").exists())
        self.assertEqual((self.b / "README.md").read_text(), "local edit\n")

    def test_init_clones_configures_hooks_and_links(self):
        target = Path(self.tmp.name) / "c"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                mu.main(["init", "--repo", str(self.bare), "--dir", str(target), "--no-autosync"])
            except SystemExit:
                pass
        self.assertTrue((target / ".git").exists(), buf.getvalue())
        cfg = json.loads(mu.CONFIG.read_text())
        self.assertEqual(cfg["dir"], str(target.resolve()))
        hook = target / ".git" / "hooks" / "pre-commit"
        self.assertIn("memory_use.py", hook.read_text())
        self.assertTrue((self.home / ".agents" / "skills" / "memory-use").is_symlink())
        # the hook really blocks a secret
        (target / "leak.md").write_text("sudo 密码：Fakepw123ZZ\n")  # memory-use: allow
        sh("git", "add", "leak.md", cwd=target)
        env = {**os.environ, "MEMORY_USE_SCRIPT": str(Path(mu.__file__).resolve())}
        r = subprocess.run(["git", "commit", "-qm", "leak"], cwd=target, capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def install_plugin(self):
        f = self.home / ".claude" / "plugins" / "installed_plugins.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"version": 2, "plugins": {"memory-use@leeguooooo-plugins": [{"scope": "user", "version": "0.2.0"}]}}))

    def doctor(self):
        os.environ["MEMORY_USE_DIR"] = str(self.b)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mu.main(["doctor"])
        return buf.getvalue()

    def test_doctor_accepts_claude_plugin_instead_of_link(self):
        self.install_plugin()
        mu.ensure_skill_links()
        self.assertFalse((self.home / ".claude" / "skills" / "memory-use").exists())   # no duplicate
        out = self.doctor()
        self.assertIn("OK   skill installed: Claude Code plugin memory-use@leeguooooo-plugins 0.2.0", out)
        self.assertNotIn("WARN skill installed", out)

    def test_doctor_warns_when_plugin_and_link_both_load(self):
        self.install_plugin()
        link = self.home / ".claude" / "skills" / "memory-use"
        link.parent.mkdir(parents=True); link.symlink_to(mu.TOOL_ROOT)
        self.assertIn("WARN skill loaded twice", self.doctor())

    def test_migrate_reports_unpushed_then_push_fixes_it(self):
        os.environ["MEMORY_USE_DIR"] = str(self.b)
        self.commit(self.b, "b.md", "from b\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mu.main(["migrate"])
        self.assertEqual(rc, 1); self.assertIn("not pushed", buf.getvalue())
        self.assertIn("install.sh | sh -s -- --repo", buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            rc = mu.main(["migrate", "--push"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
