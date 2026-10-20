"""Run: python3 -m unittest discover -s tests"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
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
        rc, out = hook("<task-notification>\n<result>review of the nas script</result>\n</task-notification>")
        self.assertEqual(out, "")                                             # a background agent's report

    def test_lint_review_interval_per_article(self):
        old = (mu.dt.date.today() - mu.dt.timedelta(days=200)).isoformat()
        recent = (mu.dt.date.today() - mu.dt.timedelta(days=35)).isoformat()
        art = lambda date, extra="": ARTICLE_OK.replace("2026-09-15 · 状态：在用", f"{date} · 状态：在用{extra}")
        (self.root / "nas" / "lucky.md").write_text(art(recent, " · 复核：30d"))      # short-lived fact, overdue
        (self.root / "nas" / "office.md").write_text(art(recent))                     # default 120 days, fine
        (self.root / "nas" / "wifi.md").write_text(art(old, " · 复核：长期"))          # does not drift
        (self.root / "nas" / "exit.md").write_text(art(old).replace("状态", "Review: never · 状态"))
        (self.root / "nas" / "old.md").write_text(art(old))                          # default, overdue
        rc, out = self.run_cmd("lint")
        stale = [l for l in out.splitlines() if "last updated" in l]
        self.assertEqual(sorted(l.split()[1].rstrip(":") for l in stale), ["nas/lucky.md", "nas/old.md"], out)
        self.assertIn("review every 30", out)

    def test_lint_flags_near_duplicate_articles(self):
        a = "# 群晖备份到 B2\n\n> 更新：2026-10-01 · 状态：在用\n\n**要点**\n- Hyper Backup 每晚 3 点推到 B2 bucket nas-backup\n- 失败看 DSM 通知\n"
        b = "# 群晖备份到 B2（新）\n\n> 更新：2026-10-10 · 状态：在用\n\n**要点**\n- Hyper Backup 每晚 3 点推到 B2 bucket nas-backup\n- 失败看 DSM 通知中心\n"
        (self.root / "nas" / "backup.md").write_text(a)
        (self.root / "nas" / "backup-new.md").write_text(b)
        rc, out = self.run_cmd("lint")
        dup = [l for l in out.splitlines() if "merge them" in l]
        self.assertEqual(len(dup), 1, out)
        self.assertIn("backup", dup[0])
        self.assertNotIn("lucky.md", out.split("merge them")[0].rsplit("WARN", 1)[-1])   # unrelated articles stay quiet

    def test_glossary_pointer_ranks_its_article_first(self):
        (self.root / "nas" / "README.md").write_text("# NAS\n\n## 出口\n\nofficemac officemac officemac 出口\n")
        rc, out = self.run_cmd("search", "officemac")
        self.assertIn("nas/README.md", out.splitlines()[0])                   # most mentions win without a pointer
        self._glossary("- officemac / jp2 — 办公室 Mac，见 nas/office.md\n")
        rc, out = self.run_cmd("search", "jp2")
        self.assertIn("nas/office.md", out.splitlines()[0], out)

    def test_eval_reports_hits_and_misses(self):
        self._glossary("- officemac / jp2 — 办公室 Mac，见 nas/office.md\n")
        (self.root / "evals.md").write_text("# evals\n\n- lucky 443 → nas/lucky.md\n- 改密码 -> nas/office.md\n")
        rc, out = self.run_cmd("eval")
        self.assertEqual(rc, 0)
        rows = {l.split()[0]: l.split() for l in out.splitlines() if l and l.split()[0] in ("evals.md", "glossary", "title", "all")}
        self.assertEqual(rows["evals.md"][1:3], ["2", "50%"])                  # lucky first; 改密码 lives in todo.md
        self.assertEqual(rows["glossary"][1:3], ["1", "100%"])
        self.assertIn("'改密码' → nas/office.md at miss", out)
        rc, out = self.run_cmd("search", "lucky", "443")
        self.assertNotIn("evals.md", out)                                   # the questions are not a note
        # a troubleshooting row whose fix links an article is a case; its own README is left out of the ranking
        (self.root / "nas" / "README.md").write_text(
            "# NAS\n\n## 出问题先看这里\n\n| 现象 | 处理 |\n|---|---|\n| Lucky 证书过期 | 443 被占，见 [lucky.md](lucky.md) |\n"
            "| 没链接的行 | 重启 |\n")
        rc, out = self.run_cmd("eval")
        trouble = next(l.split() for l in out.splitlines() if l.startswith("trouble"))
        self.assertEqual(trouble[1:3], ["1", "100%"], out)
        (self.root / "evals.md").unlink()
        self._glossary("")
        for p in (self.root / "nas").glob("*.md"):
            p.unlink()
        self.assertEqual(self.run_cmd("eval")[0], 1)

    def test_prompt_hook_once_per_session_and_ignores_paths(self):
        self.addCleanup(setattr, mu, "STATE_DIR", mu.STATE_DIR)
        mu.STATE_DIR = Path(self.tmp.name) / "state"
        def hook(prompt, sid):
            sys_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"prompt": prompt, "session_id": sid}))
            try:
                return self.run_cmd("hook", "prompt")[1]
            finally:
                sys.stdin = sys_stdin
        self.assertIn("brief nas", hook("群晖又连不上了", "a"))
        self.assertEqual(hook("群晖还是不行", "a"), "")                          # already pointed at this session
        self.assertIn("brief nas", hook("群晖", "b"))
        self.assertEqual(hook("output in /tmp/work/nas/log.txt", "c"), "")      # an absolute path is not a topic
        self.assertIn("nas", hook("open http://nas.example.com/admin", "d"))    # a URL host is

    def test_tool_failure_hook_nudges_once_per_concept(self):
        self.addCleanup(setattr, mu, "STATE_DIR", mu.STATE_DIR)
        mu.STATE_DIR = Path(self.tmp.name) / "state"
        def hook(command, error, sid="s1", **extra):
            sys_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"hook_event_name": "PostToolUseFailure", "session_id": sid,
                                                "tool_name": "Bash", "tool_input": {"command": command},
                                                "error": error, **extra}))
            try:
                return self.run_cmd("hook", "tool")
            finally:
                sys.stdin = sys_stdin
        rc, out = hook("ssh admin@nas.example.com", "Exit code 255\nConnection refused")
        self.assertEqual(rc, 0)
        ctx = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUseFailure")
        self.assertIn("memory-use brief nas", ctx["additionalContext"])
        rc, out = hook("ssh admin@nas.example.com", "Exit code 255")              # same concept, same session: quiet
        self.assertEqual(out, "")
        rc, out = hook("scp x officemac:", "Exit code 1\nofficemac: lost connection")   # new concept still nudges
        self.assertIn("memory-use brief 190", json.loads(out)["hookSpecificOutput"]["additionalContext"])
        rc, out = hook("ssh admin@nas.example.com", "Exit code 255", sid="s2")    # new session nudges again
        self.assertIn("nas", out)
        self.assertEqual(hook("npm test", "Exit code 1\n3 failing")[1], "")      # nothing the notes know
        self.assertEqual(hook("ssh nas.example.com", "x", sid="s3", is_interrupt=True)[1], "")
        self.assertEqual(hook("memory-use search nas", "Exit code 1", sid="s4")[1], "")   # our own command
        self.assertEqual(hook(f"cd {self.root} && head nas/zh.md; echo ===", "Exit code 1\n# NAS\n(eval):1: == not found",
                              sid="s5")[1], "")                                # editing the notes: their text isn't the problem

    def test_tool_failure_hook_searches_on_third_identical_failure(self):
        self.addCleanup(setattr, mu, "STATE_DIR", mu.STATE_DIR)
        mu.STATE_DIR = Path(self.tmp.name) / "state"
        (self.root / "nas" / "offsite.md").write_text(
            "# 异地备份\n\n> 更新：2026-10-01 · 状态：在用\n\n**要点**\n- rclone sync 推到 b2\n\n## 排错\n\n"
            "rclone 报 couldn't find section in config：rclone.conf 不在默认位置，加 --config ~/.config/rclone/rclone.conf\n")
        (self.root / "nas" / "misc.md").write_text(ARTICLE_OK.replace("Lucky 在 443", "find the section in config"))
        def fail(command, error, sid="r1"):
            sys_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": command},
                                                "error": error}))
            try:
                return self.run_cmd("hook", "tool")[1]
            finally:
                sys.stdin = sys_stdin
        cmd, err = "rclone sync photos b2:bucket", "Exit code 1\nFailed to create file system: couldn't find section in config file"
        self.assertEqual(fail(cmd, err), "")
        self.assertEqual(fail(cmd, err), "")
        ctx = json.loads(fail(cmd, err))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("failed 3 times", ctx)
        self.assertIn("nas/offsite.md", ctx)
        self.assertEqual(fail(cmd, err), "")                                     # pointed at once
        for _ in range(3):                                                       # a generic runner never triggers it
            out = fail("git push origin main", "Exit code 1\ncouldn't find section in config", sid="r2")
        self.assertEqual(out, "")
        for _ in range(3):                                                       # generic error words alone don't either
            out = fail("terraform apply", "Exit code 1\nfind the section in config", sid="r3")
        self.assertEqual(out, "")

    def test_stop_hook_asks_once_to_save_what_a_failure_taught(self):
        self.addCleanup(setattr, mu, "STATE_DIR", mu.STATE_DIR)
        mu.STATE_DIR = Path(self.tmp.name) / "state"
        def hook(event, payload):
            sys_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                return self.run_cmd("hook", event)[1]
            finally:
                sys.stdin = sys_stdin
        stop = lambda sid, **kw: hook("stop", {"session_id": sid, "stop_hook_active": False, **kw})
        self.assertEqual(stop("q1"), "")                                          # nothing failed: no question
        hook("tool", {"session_id": "q1", "tool_input": {"command": "ssh nas.example.com"}, "error": "Exit code 255"})
        self.assertEqual(stop("q1", stop_hook_active=True), "")                  # already continuing: never block
        out = json.loads(stop("q1"))
        self.assertEqual(out["decision"], "block")
        self.assertIn("failed on nas", out["reason"])
        self.assertEqual(stop("q1"), "")                                          # asked once
        # still stuck when asked: the next failure re-arms it, so the question lands after the last failure
        hook("tool", {"session_id": "q1", "tool_input": {"command": "ssh -p 2222 nas.example.com"}, "error": "Exit code 255"})
        hook("tool", {"session_id": "q1", "tool_input": {"command": "ssh -p 22 nas.example.com"}, "error": "Exit code 255"})
        self.assertIn("failed on nas", json.loads(stop("q1"))["reason"])         # one question for the streak
        self.assertEqual(stop("q1"), "")
        # a save after the failure answers it
        hook("tool", {"session_id": "q2", "tool_input": {"command": "ssh nas.example.com"}, "error": "Exit code 255"})
        time.sleep(1.1)
        (self.root / "nas" / "lucky.md").write_text(ARTICLE_OK + "\nssh 要走 2222\n")
        sh("git", "commit", "-qam", "nas: ssh port", cwd=self.root)
        self.assertEqual(stop("q2"), "")

    def test_lessons_profile_flags_notes_that_keep_failing(self):
        self.addCleanup(setattr, mu, "STATE_DIR", mu.STATE_DIR)
        mu.STATE_DIR = Path(self.tmp.name) / "state"
        def hook(event, payload):
            sys_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                return self.run_cmd("hook", event)[1]
            finally:
                sys.stdin = sys_stdin
        fail = lambda sid, host: hook("tool", {"session_id": sid, "tool_input": {"command": f"ssh {host}"}, "error": "Exit code 255"})
        stop = lambda sid: hook("stop", {"session_id": sid, "stop_hook_active": False})
        self.assertIn("no failures", self.run_cmd("lessons")[1])
        for sid in ("p1", "p2"):                                  # nas fails in two sessions, nobody saves
            fail(sid, "nas.example.com")
            self.assertIn("decision", stop(sid))
        fail("p3", "officemac")                                   # officemac: asked, then saved during the continuation
        self.assertIn("decision", stop("p3"))
        time.sleep(1.1)
        (self.root / "nas" / "office.md").write_text(ARTICLE_OK + "\n出口改到 2222\n")
        sh("git", "commit", "-qam", "nas: office exit", cwd=self.root)
        self.assertEqual(stop("p3"), "")                          # the save is noticed even though it was already asked
        rc, out = self.run_cmd("lessons")
        rows = {l.split()[0]: l for l in out.splitlines()[1:]}
        self.assertIn("rewrite this note", rows["nas"])
        self.assertEqual(rows["nas"].split()[1:3], ["2", "0"])
        self.assertEqual(rows["190"].split()[1:3], ["1", "1"])
        self.assertNotIn("rewrite", rows["190"])
        self.assertIn("notes that keep failing: nas", self.run_cmd("doctor")[1])

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
        self.addCleanup(os.environ.__setitem__, "MEMORY_USE_REPO", "")
        os.environ["MEMORY_USE_REPO"] = str(bare)                 # the repo this computer is bound to
        sh("git", "rm", "-rq", "nas", cwd=self.root)
        rc, out = self.run_cmd("save", "nas", "-m", "drop nas")
        self.assertEqual(rc, 0, out)
        self.assertIn("pushed", out)

    def test_save_requires_paths(self):
        rc, _ = self.run_cmd("save", "-m", "x")
        self.assertNotEqual(rc, 0)

    def test_repeated_terms_and_synonyms_do_not_change_rank(self):
        def ranking(terms):
            return [(score, c['file'], c['line']) for score, c, _ in mu.rank(self.root, terms)]
        self.assertEqual(ranking(['nas']), ranking(['nas', 'nas', '群晖']))

    def test_known_ascii_alias_does_not_match_unrelated_word(self):
        (self.root / 'space.md').write_text('# NASA\n\nspace agency\n')
        rc, out = self.run_cmd('search', 'nas', '--limit', '50')
        self.assertEqual(rc, 0)
        self.assertNotIn('] space.md:', out)

    def test_code_fences_do_not_create_fake_headings(self):
        p = self.root / 'nas' / 'commands.md'
        p.write_text('# Commands\n\n```sh\n# fake title\necho hello\n```\n\n'
                     '## Real heading\n\n~~~~python\n## also fake\n~~~\n~~~~\n\nlast line\n')
        cs = [c for c in mu.chunks(self.root) if c['file'] == 'nas/commands.md']
        self.assertEqual([c['heading'] for c in cs], ['Commands', 'Real heading'])
        self.assertIn('# fake title', cs[0]['text'])
        self.assertIn('last line', cs[1]['text'])

    def test_focused_brief_filters_unrelated_todos_and_summary(self):
        (self.root / 'nas' / 'todo.md').write_text('# todo\n\n- [ ] Lucky certificate renewal\n- [ ] unrelated backup\n')
        (self.root / 'nas' / 'README.md').write_text('# NAS\n\n## Key facts\n\n- Lucky port 443\n- unrelated backup config\n')
        rc, out = self.run_cmd('brief', 'lucky')
        self.assertEqual(rc, 0)
        self.assertIn('Lucky certificate renewal', out)
        self.assertIn('Lucky port 443', out)
        self.assertNotIn('unrelated backup', out)
        rc, out = self.run_cmd('brief', 'nas')
        self.assertIn('unrelated backup', out)

    def test_weak_unshown_match_does_not_pull_in_section(self):
        (self.root / 'root-topic.md').write_text('# RootTopic\n\n' + 'RootTopic instructions\n' * 12)
        with (self.root / 'nas' / 'office.md').open('a') as f:
            f.write('\nRootTopic mentioned incidentally\n')
        rc, out = self.run_cmd('brief', 'RootTopic')
        self.assertEqual(rc, 0)
        self.assertNotIn('## nas/README.md', out)
        self.assertNotIn('open todo', out)

    def test_brief_caps_chunks_per_file(self):
        p = self.root / 'nas' / 'dedicated.md'
        p.write_text('# Dedicated\n\n' + '\n'.join(f'## Dedicated {i}\n\nDedicated text\n' for i in range(10)))
        (self.root / 'nas' / 'other.md').write_text('# Dedicated reference\n\nDedicated text\n')
        rc, out = self.run_cmd('brief', 'dedicated')
        self.assertEqual(rc, 0)
        self.assertLessEqual(out.count('- nas/dedicated.md:'), 2)
        self.assertIn('- nas/other.md:', out)

    def test_brief_no_match_has_no_unrelated_history(self):
        rc, out = self.run_cmd('brief', 'unknown-zzzz')
        self.assertEqual(rc, 1)
        self.assertIn('no match', out)
        self.assertNotIn('recent changes', out)

    def test_brief_character_limit_includes_truncation_notice(self):
        _, full = self.run_cmd('brief', 'nas')
        for limit in (128, 300, 900):
            rc, out = self.run_cmd('brief', 'nas', '--max', str(limit))
            self.assertEqual(rc, 0)
            self.assertLessEqual(len(out.rstrip('\n')), limit)
            if len(full.rstrip('\n')) > limit:
                self.assertIn('truncated', out)
            else:
                self.assertNotIn('truncated', out)

    def test_focused_brief_filters_unrelated_commit_subjects(self):
        (self.root / 'nas' / 'lucky.md').write_text(ARTICLE_OK + '\nminor change\n')
        sh('git', 'add', 'nas/lucky.md', cwd=self.root)
        sh('git', 'commit', '-qm', 'unrelated project label', cwd=self.root)
        rc, out = self.run_cmd('brief', 'lucky')
        self.assertNotIn('unrelated project label', out)


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
        os.environ["MEMORY_USE_REPO"] = str(self.bare)           # the repo this computer is bound to

    def tearDown(self):
        os.environ["MEMORY_USE_REPO"] = ""
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

    def run_sync(self, repo):
        os.environ["MEMORY_USE_DIR"] = str(repo)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mu.main(["sync"])
        return rc, buf.getvalue()

    def test_sync_rebases_local_commits_onto_new_remote_ones(self):
        self.commit(self.a, "a.md", "from a\n"); sh("git", "push", "-q", cwd=self.a)
        self.commit(self.b, "b.md", "from b\n")                      # clean tree, ahead 1, behind 1
        rc, out = self.run_sync(self.b)
        self.assertEqual(rc, 0, out); self.assertIn("pushed 1", out)
        self.assertEqual(self.remote_head(self.bare), self.head(self.b))
        self.assertTrue((self.b / "a.md").exists())

    def test_sync_aborts_a_conflicting_rebase_and_keeps_the_commit(self):
        self.commit(self.a, "README.md", "from a\n"); sh("git", "push", "-q", cwd=self.a)
        self.commit(self.b, "README.md", "from b\n")
        before = self.head(self.b)
        rc, out = self.run_sync(self.b)
        self.assertNotEqual(rc, 0); self.assertIn("aborted", out)
        self.assertEqual(self.head(self.b), before)
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())

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

    def remote_head(self, bare):
        return subprocess.run(["git", "rev-parse", "main"], cwd=bare, capture_output=True, text=True).stdout.strip()

    def test_push_refused_when_the_target_is_not_the_bound_repo(self):
        """Issue #4: a push goes nowhere unless every resolved push URL is the bound notes repo."""
        evil = Path(self.tmp.name) / "evil.git"
        sh("git", "clone", "-q", "--bare", str(self.bare), str(evil), cwd=self.tmp.name)
        cases = {
            "remote URL changed": [("remote", "set-url", "origin", str(evil))],
            "separate pushurl": [("config", "remote.origin.pushurl", str(evil))],
            "branch.pushRemote": [("remote", "add", "other", str(evil)), ("config", "branch.main.pushRemote", "other")],
            "remote.pushDefault": [("remote", "add", "other", str(evil)), ("config", "remote.pushDefault", "other")],
            "insteadOf rewrite": [("config", f"url.{evil}.insteadOf", str(self.bare))],
            "second pushurl": [("config", "--add", "remote.origin.pushurl", str(self.bare)),
                               ("config", "--add", "remote.origin.pushurl", str(evil))],
        }
        for name, setup in cases.items():
            with self.subTest(name):
                repo = Path(self.tmp.name) / ("c-" + name.replace(" ", "-").replace(".", "-"))
                sh("git", "clone", "-q", str(self.bare), str(repo), cwd=self.tmp.name)
                for args in setup:
                    sh("git", *args, cwd=repo)
                self.commit(repo, "n.md", name + "\n")
                before, evil_before = self.remote_head(self.bare), self.remote_head(evil)
                ok, msg = mu.autosync_once(repo)
                self.assertFalse(ok, msg)
                self.assertIn("not the notes repo", msg)
                self.assertEqual(self.remote_head(self.bare), before)
                self.assertEqual(self.remote_head(evil), evil_before)          # nothing reached the other repo
                self.assertTrue((repo / "n.md").exists())                       # the commit stays local
                self.assertIn("n.md", subprocess.run(["git", "log", "--name-only", "-1"], cwd=repo,
                                                     capture_output=True, text=True).stdout)

    def test_github_target_must_stay_private_with_the_same_id(self):
        repo = Path(self.tmp.name) / "gh"
        sh("git", "clone", "-q", str(self.bare), str(repo), cwd=self.tmp.name)
        sh("git", "remote", "set-url", "origin", "https://x-access-token:SECRET123@github.com/Me/Notes.git", cwd=repo)
        os.environ["MEMORY_USE_REPO"] = "me/notes"
        lookups = []
        def fake(info):
            def github_repo(slug):
                lookups.append(slug)
                return info
            return github_repo
        self.addCleanup(setattr, mu, "github_repo", mu.github_repo)
        mu.github_repo = fake({"private": True, "id": 42, "full_name": "Me/Notes"})
        remote, branch, problem, verified = mu.push_preflight(repo)
        self.assertEqual((remote, branch, problem), ("origin", "main", ""))
        self.assertIn("private, id 42", verified)
        self.assertEqual(lookups, ["me/notes"])
        self.assertEqual(json.loads(mu.CONFIG.read_text())["repo_ids"], {"me/notes": 42})   # id pinned on first use
        for info, why in [({"private": False, "id": 42, "full_name": "Me/Notes"}, "PUBLIC"),
                          ({"private": True, "id": 7, "full_name": "Me/Notes"}, "different repository"),
                          ({"private": True, "id": 42, "full_name": "Someone/Notes"}, "renamed or transferred"),
                          (None, "could not confirm")]:
            mu.github_repo = fake(info)
            problem = mu.push_preflight(repo)[2]
            self.assertIn(why, problem)
            self.assertNotIn("SECRET123", problem)
        sh("git", "remote", "set-url", "origin", "git@github.com:someone/notes.git", cwd=repo)
        mu.github_repo = fake({"private": True, "id": 42, "full_name": "Me/Notes"})
        problem = mu.push_preflight(repo)[2]
        self.assertIn("not the notes repo", problem)

    def test_repo_identity_and_redact(self):
        same = ["git@github.com:Me/Notes.git", "ssh://git@github.com:22/me/notes", "https://u:tok@github.com/ME/notes/",
                "https://github.com/me/notes.git"]
        self.assertEqual({mu.repo_identity(u) for u in same}, {"github.com/me/notes"})
        self.assertEqual(mu.repo_identity("git@gitlab.com:Me/Notes.git"), "gitlab.com/Me/Notes")
        self.assertEqual(mu.repo_identity(str(self.bare) + "/"), "file:" + os.path.realpath(self.bare))
        self.assertEqual(mu.redact("https://u:tok@github.com/me/notes"), "https://***@github.com/me/notes")
        self.assertEqual(mu.redact("git@github.com:me/notes.git"), "git@github.com:me/notes.git")

    def test_save_no_push_and_doctor_shows_target(self):
        os.environ["MEMORY_USE_DIR"] = str(self.a)
        (self.a / "x.md").write_text("x\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mu.main(["save", "x.md", "-m", "x", "--no-push"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("not pushed", buf.getvalue())
        self.assertNotEqual(self.head(self.a), self.remote_head(self.bare))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mu.main(["doctor"])
        self.assertIn("OK   push target: origin/main", buf.getvalue())
        self.assertIn("visibility not verifiable on a local path", buf.getvalue())

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


class ProfileLeakHookTest(unittest.TestCase):
    """The pre-commit hook reads profile-use leak-scan's exit code: 0 clean, 1 leak, 2 skipped."""

    def commit_with_leak_scan_exit(self, code, output='{"clean": true, "skipped": "no profile found"}'):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / "home", Path(tmp) / "repo"
            pu = home / ".agents" / "skills" / "profile-use" / "scripts" / "profile_use.py"
            pu.parent.mkdir(parents=True)
            pu.write_text(f"import sys\nprint({output!r})\nsys.exit({code})\n")
            repo.mkdir()
            sh("git", "init", "-q", cwd=repo)
            hook = repo / ".git" / "hooks" / "pre-commit"
            hook.write_text((mu.TOOL_ROOT / "template" / ".githooks" / "pre-commit").read_text())
            hook.chmod(0o755)
            (repo / "note.md").write_text("# note\n")
            sh("git", "add", "note.md", cwd=repo)
            env = {**os.environ, "HOME": str(home), "MEMORY_USE_SCRIPT": str(Path(mu.__file__).resolve())}
            return subprocess.run(["git", "commit", "-qm", "note"], cwd=repo, capture_output=True, text=True, env=env)

    def test_clean_and_skipped_commit(self):
        for code in (0, 2):
            r = self.commit_with_leak_scan_exit(code)
            self.assertEqual(r.returncode, 0, f"exit {code}: {r.stdout}{r.stderr}")

    def test_leak_blocks_commit(self):
        r = self.commit_with_leak_scan_exit(1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("personal data from profile-use found", r.stdout + r.stderr)

    def test_usage_error_exit_2_blocks_commit(self):
        # argparse exits 2 too (e.g. an old profile-use without --all-profiles); that is not "skipped"
        r = self.commit_with_leak_scan_exit(2, "usage: profile_use.py: error: unrecognized arguments")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("leak-scan failed (exit 2)", r.stdout + r.stderr)

    def test_scan_error_blocks_commit(self):
        r = self.commit_with_leak_scan_exit(3)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("leak-scan failed (exit 3)", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
