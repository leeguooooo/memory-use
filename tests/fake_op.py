#!/usr/bin/env python3
"""Fake 1Password `op` for tests: items live in $FAKE_OP_STATE, every call is logged to $FAKE_OP_LOG."""
import json
import os
import sys

args = sys.argv[1:]
stdin = sys.stdin.read() if args[:2] == ["item", "create"] else ""
with open(os.environ["FAKE_OP_LOG"], "a") as log:
    log.write(json.dumps({"argv": args, "stdin": stdin}) + "\n")
state = os.environ["FAKE_OP_STATE"]
items = json.load(open(state))
mode = os.environ.get("FAKE_OP_MODE", "")


def flag(name):
    return args[args.index(name) + 1] if name in args else None


def meta(i):
    out = {k: v for k, v in i.items() if k != "fields"}
    user = next((f["value"] for f in i["fields"] if f.get("purpose") == "USERNAME"), "")
    out["additional_information"] = user if i["category"] == "LOGIN" else "note excerpt"
    return out


if args[:1] == ["whoami"]:
    sys.exit(1 if mode == "signed-out" else 0)
if mode == "signed-out":
    sys.stderr.write("[ERROR] account is not signed in\n")
    sys.exit(1)
if args[:2] == ["item", "list"]:
    print(json.dumps([meta(i) for i in items]))
elif args[:2] == ["item", "get"]:
    hits = [i for i in items if args[2] in (i["id"], i["title"])
            and (flag("--vault") is None or flag("--vault") in (i["vault"]["name"], i["vault"]["id"]))]
    if len(hits) != 1:
        sys.stderr.write('[ERROR] "%s" isn\'t an item.\n' % args[2])
        sys.exit(1)
    item = json.loads(json.dumps(hits[0]))
    if "--reveal" not in args:
        for f in item["fields"]:
            if f.get("type") == "CONCEALED":
                f["value"] = None
    print(json.dumps(item))
elif args[:2] == ["item", "create"] and args[-1] == "-":
    t = json.loads(stdin)
    vault = flag("--vault") or "Private"
    item = {"id": "n%025d" % len(items), "title": t["title"], "category": t["category"],
            "vault": {"id": "v" + vault.lower(), "name": vault}, "tags": (flag("--tags") or "").split(","),
            "fields": t["fields"]}
    if mode == "mismatch":
        item["fields"] = [dict(f, value="wrong") if f.get("purpose") == "PASSWORD" else f for f in item["fields"]]
    if mode == "duplicate":
        items.append(dict(item, id="d%025d" % len(items)))
    items.append(item)
    json.dump(items, open(state, "w"))
    print(json.dumps(item))   # real op echoes the fields, values included
    if mode == "create-failed":
        sys.exit(1)
else:
    sys.stderr.write("fake op: unknown %r\n" % args)
    sys.exit(3)
