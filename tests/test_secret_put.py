"""Vault bridge regressions using an in-memory fake; never access a live vault."""
import contextlib
import io
import json
import subprocess
import sys
import unittest
from unittest import mock

from test_memory_use import mu


class SecretPutTest(unittest.TestCase):
    def setUp(self):
        self.items = []
        self.calls = []
        self.mode = "normal"
        self.syncs = 0
        self.adds = 0
        self.patch = mock.patch.object(mu, "bwu", side_effect=self.fake_vault)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def fake_vault(self, *args, stdin=None):
        self.calls.append(args)
        out, err, code = "", "", 0
        if args[0] == "sync":
            self.syncs += 1
            if self.mode == "sync-failed" and self.adds:
                code = 1
        elif args[0] == "generate":
            out = "G" * int(args[1]) + "\n"
        elif args[0] == "add":
            self.adds += 1
            item = {"id": "22222222-2222-3333-4444-555555555555", "name": args[1],
                    "folder": mu.SECRET_FOLDER, "user": None, "value": stdin.splitlines()[0]}
            self.items.append(item)
            if self.mode == "ambiguous":
                self.items.append({**item, "id": "33333333-2222-3333-4444-555555555555"})
            if self.mode == "write-failed":
                out, err, code = item["value"], "synthetic uncertain write", 1
        elif args[0] == "list":
            # Deliberately return the old duplicate last: choosing [-1] is unsafe.
            out = json.dumps([{k: v for k, v in i.items() if k != "value"} for i in reversed(self.items)])
        elif args[0] == "get":
            item = next(i for i in self.items if i["id"] == args[1])
            out = ("wrong" if self.mode == "mismatch" else item["value"]) + "\n"
            if self.mode == "read-failed":
                code = 1
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, code, out, err)

    def put(self, value, *extra):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(value)), contextlib.redirect_stdout(out):
            try:
                rc = mu.main(["secret", "put", "test item", *extra])
            except SystemExit as error:
                rc = error.code
        return rc, out.getvalue()

    def assert_unverified(self, value, *extra):
        rc, out = self.put(value, *extra)
        self.assertNotEqual(rc, 0)
        self.assertNotIn(value.strip(), str(rc) + out)
        self.assertNotIn("stored in", out)
        self.assertNotIn("🔑", out)
        self.assertEqual(self.adds, 1)

    def test_multiline_input_is_rejected_before_any_vault_call(self):
        for value in ["line1\nline2\n", "line1\r\nline2\r\n", "line1\n\n",
                      "-----BEGIN OPENSSH PRIVATE KEY-----\nfake-body\n-----END OPENSSH PRIVATE KEY-----\n"]:
            with self.subTest(value=value[:8]):
                rc, out = self.put(value)
                self.assertNotEqual(rc, 0)
                self.assertIn("multiline", str(rc).lower())
                self.assertNotIn("stored", out)
                self.assertEqual(self.calls, [])

    def test_single_line_is_read_back_before_reporting_success(self):
        for value in ["synthetic-password", "synthetic-password\n", "synthetic-password\r\n"]:
            with self.subTest(ending=repr(value[-2:])):
                self.items.clear()
                rc, out = self.put(value)
                self.assertEqual(rc, 0, out)
                self.assertIn("bw:22222222-2222-3333-4444-555555555555", out)
                self.assertNotIn("synthetic-password", out)
                self.assertEqual(self.calls[-1][0], "get")

    def test_existing_duplicate_name_does_not_supply_the_pointer(self):
        self.items.append({"id": "11111111-2222-3333-4444-555555555555", "name": "test item",
                           "folder": mu.SECRET_FOLDER, "user": None, "value": "old-password"})
        rc, out = self.put("synthetic-password\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("bw:22222222", out)
        self.assertNotIn("bw:11111111", out)

    def test_wrong_readback_is_not_success_and_is_not_retried(self):
        self.mode = "mismatch"
        self.assert_unverified("synthetic-password\n")

    def test_failed_readback_is_not_success_and_is_not_retried(self):
        self.mode = "read-failed"
        self.assert_unverified("synthetic-password\n")

    def test_post_write_sync_failure_does_not_retry_or_report_success(self):
        self.mode = "sync-failed"
        self.assert_unverified("synthetic-password\n")

    def test_ambiguous_new_items_are_not_arbitrarily_selected(self):
        self.mode = "ambiguous"
        self.assert_unverified("synthetic-password\n")

    def test_write_error_does_not_echo_a_secret_from_backend_stdout(self):
        self.mode = "write-failed"
        self.assert_unverified("synthetic-password\n")

    def test_generated_value_uses_the_same_verified_write_path(self):
        rc, out = self.put("", "--generate", "24")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.adds, 1)
        self.assertEqual(self.items[0]["value"], "G" * 24)
        self.assertNotIn("G" * 24, out)
        self.assertEqual(self.calls[-1][0], "get")
