"""1Password backend against tests/fake_op.py; never touches a real account."""
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

from test_memory_use import ARTICLE_OK, MemoryUseTest, mu

FAKE_OP = Path(__file__).resolve().parent / "fake_op.py"
ROUTER = "routerid00000000000000000a"
BANK = "bankid0000000000000000000b"


def login(id_, title, user, pw, vault="Private", tags=()):
    return {"id": id_, "title": title, "category": "LOGIN", "vault": {"id": "v" + vault.lower(), "name": vault},
            "tags": list(tags), "fields": [
                {"id": "username", "type": "STRING", "purpose": "USERNAME", "label": "username", "value": user},
                {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "label": "password", "value": pw},
                {"id": "otp", "type": "OTP", "label": "one-time password", "value": "otpauth://x", "totp": "123456"}]}


class OnePasswordBackendTest(MemoryUseTest):
    def setUp(self):
        super().setUp()
        t = Path(self.tmp.name)
        self.state, self.log = t / "op-state.json", t / "op-log.jsonl"
        self.state.write_text(json.dumps([
            login(ROUTER, "router", "admin", "s3cret-router-pw", tags=["imported/memory"]),
            login(BANK, "bank", "me", "s3cret-bank-pw", vault="Other", tags=["finance"]),
            {"id": "noteid000000000000000000c", "title": "tunnel", "category": "SECURE_NOTE",
             "vault": {"id": "vprivate", "name": "Private"}, "tags": ["memory"],
             "fields": [{"id": "cred", "type": "CONCEALED", "label": "tunnel json", "value": "s3cret-tunnel-json"}]}]))
        self.log.write_text("")
        wrapper = t / "op"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_OP} \"$@\"\n"); wrapper.chmod(0o755)
        self.env = {"MEMORY_USE_OP": str(wrapper), "FAKE_OP_STATE": str(self.state),
                    "FAKE_OP_LOG": str(self.log), "MEMORY_USE_SECRET_BACKEND": "1password"}
        os.environ.update(self.env)
        mu.KNOWN_HASHES = t / "hashes"
        config = mock.patch.object(mu, "CONFIG", t / "config.json")   # `secret backend` writes config
        config.start()
        self.addCleanup(config.stop)

    def tearDown(self):
        for k in list(self.env) + ["FAKE_OP_MODE", "MEMORY_USE_OP_VAULT"]:
            os.environ.pop(k, None)
        super().tearDown()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def put(self, value, *extra):
        with mock.patch.object(sys, "stdin", io.StringIO(value)):
            return self.run_cmd("secret", "put", "new item", *extra)

    def assertNoValues(self, out):
        self.assertNotIn("s3cret", out)
        self.assertNotIn("synthetic-pw", out)

    def test_find_prints_op_pointer_without_values(self):
        rc, out = self.run_cmd("secret", "find", "router")
        self.assertEqual(rc, 0)
        self.assertIn(f"op://Private/{ROUTER}（router）", out)
        self.assertIn("user=yes", out)
        self.assertNoValues(out)
        self.assertTrue(all("--reveal" not in c["argv"] for c in self.calls()))

    def test_get_refuses_to_print_by_default(self):
        rc, out = self.run_cmd("secret", "get", f"op://Private/{ROUTER}")
        self.assertNotEqual(rc, 0)
        self.assertNoValues(out)

    def test_get_env_passes_value_to_command_only(self):
        marker = Path(self.tmp.name) / "seen"
        for ref, field, want in [(f"op://Private/{ROUTER}", [], "s3cret-router-pw"),
                                 (f"op://Private/{ROUTER}/username", [], "admin"),
                                 (f"op://Private/{ROUTER}", ["--field", "totp"], "123456"),
                                 ("noteid000000000000000000c", ["--field", "tunnel json"], "s3cret-tunnel-json")]:
            with self.subTest(ref=ref, field=field):
                rc, out = self.run_cmd("secret", "get", ref, *field, "--env", "PW", "--", "/bin/sh", "-c", f'printf %s "$PW" > {marker}')
                self.assertEqual(rc, 0, out)
                self.assertEqual(marker.read_text(), want)
                self.assertNoValues(out)

    def test_get_bw_pointer_still_goes_to_bitwarden(self):
        with mock.patch.object(mu, "bwu", return_value=mu.subprocess.CompletedProcess([], 0, "bw-value\n", "")) as bw:
            marker = Path(self.tmp.name) / "seen"
            rc, _ = self.run_cmd("secret", "get", "bw:11111111-2222-3333-4444-555555555555", "--env", "PW", "--",
                                 "/bin/sh", "-c", f'printf %s "$PW" > {marker}')
        self.assertEqual(rc, 0)
        self.assertEqual(marker.read_text(), "bw-value")
        self.assertEqual(bw.call_args.args[:2], ("get", "11111111-2222-3333-4444-555555555555"))

    def test_put_sends_value_on_stdin_tags_it_and_reads_back(self):
        os.environ["MEMORY_USE_OP_VAULT"] = "Private"
        rc, out = self.put("synthetic-pw\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("🔑 op://Private/n0000000000000000000000003（new item）", out)
        self.assertNoValues(out)
        create = next(c for c in self.calls() if c["argv"][:2] == ["item", "create"])
        self.assertNotIn("synthetic-pw", " ".join(create["argv"]))
        self.assertIn("synthetic-pw", create["stdin"])
        self.assertEqual(create["argv"][create["argv"].index("--tags") + 1], "memory")
        self.assertIn("--reveal", self.calls()[-1]["argv"])

    def test_put_unverified_outcomes_report_no_pointer(self):
        for mode in ["mismatch", "duplicate", "create-failed"]:
            with self.subTest(mode=mode):
                os.environ["FAKE_OP_MODE"] = mode
                rc, out = self.put("synthetic-pw\n")
                self.assertNotEqual(rc, 0)
                self.assertNotIn("🔑", out)
                self.assertNoValues(str(rc) + out)
                self.assertEqual(sum(c["argv"][:2] == ["item", "create"] for c in self.calls()), 1)  # never retried
                self.log.write_text("")

    def test_put_rejects_multiline_before_any_op_call(self):
        rc, out = self.put("line1\nline2\n")
        self.assertNotEqual(rc, 0)
        self.assertEqual(self.calls(), [])

    def test_generate_writes_a_password_of_that_length(self):
        rc, out = self.put("", "--generate", "24")
        self.assertEqual(rc, 0, out)
        item = json.loads(self.state.read_text())[-1]
        pw = next(f["value"] for f in item["fields"] if f["purpose"] == "PASSWORD")
        self.assertEqual(len(pw), 24)
        self.assertNotIn(pw, out)

    def test_refs_flag_dangling_op_pointers(self):
        (self.root / "nas" / "p.md").write_text(ARTICLE_OK + f"\n🔑 op://Private/{ROUTER}（router）\n🔑 op://Private/missing0000000000000000000\n")
        rc, out = self.run_cmd("secret", "refs")
        self.assertEqual(rc, 1)
        self.assertIn("op:missing0000000000000000000 not found in 1Password", out)
        self.assertNotIn(ROUTER, out)

    def test_refs_and_lint_skip_quietly_when_signed_out(self):
        os.environ["FAKE_OP_MODE"] = "signed-out"
        (self.root / "nas" / "p.md").write_text(ARTICLE_OK + "\n🔑 op://Private/missing0000000000000000000\n")
        rc, out = self.run_cmd("secret", "refs")
        self.assertEqual(rc, 0)
        self.assertIn("not checked", out)
        rc, out = self.run_cmd("lint")
        self.assertNotIn("pointer", out)
        self.assertEqual([c["argv"] for c in self.calls()], [["whoami"], ["whoami"]])

    def test_hashes_cover_tagged_items_including_nested_tags(self):
        rc, out = self.run_cmd("secret", "hashes")
        self.assertIn("2 secret hash", out)
        self.assertNotIn("s3cret", mu.KNOWN_HASHES.read_text())
        f = self.root / "nas" / "leak.md"
        f.write_text("路由器密码：s3cret-router-pw\n")  # memory-use: allow (test fixture)
        rc, out = self.run_cmd("check", str(f))
        self.assertEqual(rc, 1)
        self.assertIn("vault secret (hash match)", out)
        f.write_text("银行密码：s3cret-bank-pw\n")  # memory-use: allow (test fixture)
        self.assertNotIn("hash match", self.run_cmd("check", str(f))[1])

    def test_op_pointer_passes_the_secret_scan(self):
        f = self.root / "nas" / "ok.md"
        f.write_text(f"- 路由器密码：🔑 op://Private/{ROUTER}（router）\n- password: op://Private/{ROUTER}/password\n")
        rc, out = self.run_cmd("check", str(f))
        self.assertEqual(rc, 0, out)

    def test_backend_is_chosen_by_env_then_config(self):
        os.environ.pop("MEMORY_USE_SECRET_BACKEND")
        rc, out = self.run_cmd("secret", "backend", "bitwarden")
        self.assertIn("secret backend: bitwarden", out)
        self.assertEqual(mu.secret_backend(), "bitwarden")
        self.run_cmd("secret", "backend", "op")
        self.assertEqual(mu.secret_backend(), "1password")
        os.environ["MEMORY_USE_SECRET_BACKEND"] = "bitwarden"
        self.assertEqual(mu.secret_backend(), "bitwarden")
        rc, _ = self.run_cmd("secret", "backend", "lastpass")
        self.assertNotEqual(rc, 0)
