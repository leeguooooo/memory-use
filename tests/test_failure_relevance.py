"""Failure context and once-only Stop policy against synthetic notes."""
import contextlib
import io
import json
import sys
import unittest
from unittest import mock

import test_memory_use as base


class FailureRelevanceTest(unittest.TestCase):
    def setUp(self):
        self.case = base.MemoryUseTest()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.addCleanup(setattr, base.mu, "STATE_DIR", base.mu.STATE_DIR)
        base.mu.STATE_DIR = self.case.root.parent / "hook-state"
        glossary = self.case.root / "glossary.md"
        glossary.write_text(glossary.read_text() + "- warp / warp-cli — tunnel\n- 113 / mac-100 — another machine\n")

    def hook(self, event, **payload):
        out = io.StringIO()
        payload.setdefault("session_id", "same-session")
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), contextlib.redirect_stdout(out):
            base.mu.main(["hook", event])
        return out.getvalue()

    def fail_event(self, command, error, **payload):
        return self.hook("tool", tool_input={"command": command}, error=error, **payload)

    def test_local_shell_path_and_encoding_errors_do_not_arm_stop(self):
        cases = [
            ("printf 'warp =====USER'", "Exit code 127\nzsh:1: command not found: =====USER"),
            ("ls /tmp/mac-100/Chrome", "Exit code 2\nls: /tmp/mac-100/Chrome: No such file or directory"),
            ("cat tools/README.md", "Exit code 1\ncat: tools/README.md: No such file or directory"),
            ("sed -n '1,5p' 113 | cut -c 1", "Exit code 1\ncut: stdin: Illegal byte sequence"),
            ("ssh nas.example.com/*", "Exit code 1\nzsh: no matches found: nas.example.com/*"),
        ]
        for index, (command, error) in enumerate(cases):
            sid = "local-" + str(index)
            self.assertEqual(self.fail_event(command, error, session_id=sid), "")
            self.assertEqual(self.hook("stop", session_id=sid), "")

    def test_quoted_mentions_and_unused_command_operands_are_not_topics(self):
        commands = ["printf 'warp nas 113'", "python3 -c 'print(\"warp nas 113\")'",
                    "ssh -i /tmp/keys/nas.key other.example.com 'echo warp'",
                    "scp nas.txt other.example.com:/tmp/warp", "ssh '[113::1]'",
                    "curl --data 'http://nas.example.com' https://other.example.com"]
        for index, command in enumerate(commands):
            self.assertEqual(self.fail_event(command, "Exit code 255\nConnection refused", session_id="quoted-" + str(index)), "")

    def test_real_remote_target_is_the_only_command_topic(self):
        text = self.fail_event("ssh -p 2222 nas.example.com 'echo warp 113'", "Exit code 255\nConnection refused")
        self.assertIn("brief nas", text)
        self.assertNotIn("warp", text)
        self.assertNotIn("113", text)

    def test_actual_error_can_name_a_topic_without_command_text_matching(self):
        text = self.fail_event("deploy-tool check", "Exit code 1\nerror: warp connection refused")
        self.assertIn("brief warp", text)

    def test_printed_topic_is_not_part_of_the_actual_diagnostic(self):
        self.assertEqual(self.fail_event("printf 'warp'; deploy-tool check", "Exit code 1\nwarp\nerror: generic failure"), "")

    def test_same_topic_never_rearms_stop_after_first_reminder(self):
        self.fail_event("ssh nas.example.com", "Exit code 255\nConnection refused")
        self.assertIn('"decision": "block"', self.hook("stop"))
        for port in (2222, 22, 2022):
            self.fail_event(f"ssh -p {port} nas.example.com", "Exit code 255\nConnection refused")
            self.assertEqual(self.hook("stop"), "")
        self.fail_event("ssh officemac", "Exit code 255\nConnection refused")
        self.assertIn("190", self.hook("stop"))

    def test_legacy_duplicate_lessons_do_not_bypass_once_only_policy(self):
        self.fail_event("ssh nas.example.com", "Exit code 255\nConnection refused")
        self.hook("stop")
        with base.mu.session_update() as state:
            state["same-session"]["lessons"].append({"about": "nas", "t": 1})
        self.assertEqual(self.hook("stop"), "")
