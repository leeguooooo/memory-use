#!/usr/bin/env python3
"""memory-use: a portable, git-backed long-term memory for AI coding agents.

Stdlib only. Your notes are plain Markdown in a private git repo; this script finds that repo, keeps it
in sync (by hand or on a timer), searches it (ranked by section, with glossary aliases), builds compact
context packs, lints structure, aggregates todos, scaffolds sections, sets it up on a new computer, and
refuses to commit anything that looks like a secret.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

VERSION = "0.2.4"
TOOL_REPO = "leeguooooo/memory-use"
INSTALL_URL = f"https://raw.githubusercontent.com/{TOOL_REPO}/main/install.sh"
DEFAULT_NAME = "personal-memory"
CONFIG_DIR = Path(os.environ.get("MEMORY_USE_CONFIG_DIR", Path.home() / ".config" / "memory-use"))
CONFIG = CONFIG_DIR / "config.json"
STATE_DIR = Path(os.environ.get("MEMORY_USE_STATE_DIR", Path.home() / ".local" / "state" / "memory-use"))
KNOWN_SECRETS = Path(os.environ.get("MEMORY_USE_SECRETS", CONFIG_DIR / "known-secrets"))
KNOWN_HASHES = Path(os.environ.get("MEMORY_USE_SECRET_HASHES", CONFIG_DIR / "known-secret-hashes"))
SECRET_FOLDER = os.environ.get("MEMORY_USE_SECRET_FOLDER", "memory")
POINTER = re.compile(r"\bbw:([0-9a-fA-F-]{36})")
TEXT_SUFFIXES = {".md", ".txt", ".sh", ".py", ".json", ".yaml", ".yml", ".toml", ".example", ".conf", ".plist"}
SKIP_DIRS = {".git", "skill", "archive"}   # not searched / linted as notes (archive is searchable with --all)
STALE_DAYS = 120
README_MAX_LINES = 70
TODO_MAX_OPEN = 20
TOOL_ROOT = Path(__file__).resolve().parents[1]      # this checkout of memory-use (SKILL.md, template/)

# ---------------------------------------------------------------- config + repo helpers


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def save_config(**kv) -> dict:
    cfg = {**load_config(), **{k: v for k, v in kv.items() if v is not None}}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return cfg


def repo_slug() -> str | None:
    return os.environ.get("MEMORY_USE_REPO") or load_config().get("repo")


def repo_dir() -> Path:
    env = os.environ.get("MEMORY_USE_DIR")
    if env:
        return Path(env).expanduser()
    cfg = load_config()
    if cfg.get("dir"):
        return Path(cfg["dir"]).expanduser()
    slug = repo_slug()
    return Path.home() / "github.com" / (repo_name(slug) if slug else DEFAULT_NAME)


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd or repo_dir(), text=True, capture_output=True, check=check)


def need_repo() -> Path:
    d = repo_dir()
    if not (d / ".git").exists():
        sys.exit(f"memory repo not found at {d}. Set it up: memory_use.py init --repo <owner>/<name>  (add --create for a new one)")
    return d


def md_files(root: Path, include_archive: bool = False) -> list[Path]:
    skip = SKIP_DIRS - ({"archive"} if include_archive else set())
    return sorted(p for p in root.rglob("*.md") if not (set(p.relative_to(root).parts) & skip))


def sections(root: Path) -> list[tuple[str, str]]:
    """(dir, description) from the table in the root README, falling back to dirs with README.md."""
    out: list[tuple[str, str]] = []
    readme = root / "README.md"
    if readme.exists():
        for line in readme.read_text().splitlines():
            m = re.match(r"\|\s*\[(.+?)\]\(([\w.-]+)/README\.md\)\s*\|\s*(.+?)\s*\|", line)
            if m:
                out.append((m.group(2), f"{m.group(1)} — {m.group(3)}"))
    known = {d for d, _ in out}
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".") and (p / "README.md").exists() and p.name not in known:
            out.append((p.name, "(not listed in root README)"))
    return out


# ---------------------------------------------------------------- glossary + ranked search


def load_glossary(root: Path) -> list[list[str]]:
    """Each concept is a list of lowercase aliases, from `- a / b / c — description` lines."""
    f = root / "glossary.md"
    groups: list[list[str]] = []
    if not f.exists():
        return groups
    for line in f.read_text().splitlines():
        if not line.startswith("- "):
            continue
        names = re.split(r"\s+—\s+", line[2:], maxsplit=1)[0]
        aliases = [a.strip().strip("`").lower() for a in names.split(" / ") if a.strip()]
        if aliases:
            groups.append(aliases)
    return groups


class Alias:
    """One way to spell a concept. ASCII aliases that came from the glossary, or contain a digit, match
    as whole tokens, so the alias 192.168.1.1 does not hit 192.168.1.14 and 190 does not hit 1900.
    A term typed by the user without digits keeps substring matching (rust → rustdesk); CJK always does."""
    __slots__ = ("text", "rx")

    def __init__(self, text: str, typed: bool = False):
        self.text = text
        exact = text.isascii() and (not typed or any(ch.isdigit() for ch in text))
        self.rx = re.compile(rf"(?<![a-z0-9_.]){re.escape(text)}(?![a-z0-9_]|\.[a-z0-9])") if exact else None

    def count(self, low: str) -> int:
        return len(self.rx.findall(low)) if self.rx else low.count(self.text)

    def __eq__(self, other):
        return isinstance(other, Alias) and other.text == self.text

    def __hash__(self):
        return hash(self.text)


def join_phrases(terms: list[str], glossary: list[list[str]]) -> list[str]:
    """Rejoin words that together form a multi-word alias: ["windows", "电脑"] → ["windows 电脑"]."""
    known = {a for g in glossary for a in g if " " in a}
    out, i = [], 0
    while i < len(terms):
        for n in (3, 2):
            phrase = " ".join(terms[i:i + n]).lower()
            if i + n <= len(terms) and phrase in known:
                out.append(phrase)
                i += n
                break
        else:
            out.append(terms[i])
            i += 1
    return out


def expand(terms: list[str], glossary: list[list[str]]) -> list[set[Alias]]:
    """One alternative-set per query term: the term plus every alias of a glossary concept containing it."""
    out = []
    for t in join_phrases(terms, glossary):
        t = t.lower()
        concepts = [g for g in glossary if t in g]
        alts = {Alias(a) for g in concepts for a in g} if concepts else {Alias(t, typed=True)}
        # Repeating a term or two names of the same concept must not change its weight.
        if alts not in out:
            out.append(alts)
    return out


def hits(text: str, alts: set[Alias]) -> int:
    low = text.lower()
    return sum(a.count(low) for a in alts)


def chunks(root: Path, include_archive: bool = False) -> list[dict]:
    """Split notes into heading-scoped chunks: {file, line, path (heading trail), heading, text}."""
    out = []
    for p in md_files(root, include_archive):
        rel = str(p.relative_to(root))
        trail: list[str] = []
        cur = {"file": rel, "line": 1, "path": "", "heading": "", "lines": []}
        fence = None
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if marker:
                token, tail = marker.groups()
                if fence is None:
                    fence = (token[0], len(token))
                elif token[0] == fence[0] and len(token) >= fence[1] and not tail.strip():
                    fence = None
                cur["lines"].append((i, line))
                continue
            m = re.match(r"^(#{1,4})\s+(.*)", line) if fence is None else None
            if m:
                if cur["lines"] or cur["heading"]:
                    out.append(cur)
                level, title = len(m.group(1)), m.group(2).strip()
                trail = trail[: level - 1] + [title]
                cur = {"file": rel, "line": i, "path": " › ".join(trail), "heading": title, "lines": []}
            else:
                cur["lines"].append((i, line))
        out.append(cur)
    for c in out:
        c["text"] = "\n".join(l for _, l in c["lines"])
    return out


def rank(root: Path, terms: list[str], include_archive: bool = False, top: int = 8) -> list[tuple[float, dict, list[tuple[int, str]]]]:
    glossary = load_glossary(root)
    groups = expand(terms, glossary)
    if not groups:
        return []
    cs = chunks(root, include_archive)
    n = len(cs) or 1
    counts = [[(hits(c["heading"], g), hits(c["text"], g),
                hits(re.sub(r"\.md$", "", c["file"]), g)) for g in groups] for c in cs]
    df = [sum(bool(row[i][0] or row[i][1]) for row in counts) for i in range(len(groups))]
    scored = []
    for c, row in zip(cs, counts):
        per = []
        named = False
        for (hh, bh, fh), d in zip(row, df):
            named |= bool(fh)
            h = 3 * hh + 3 * fh + bh
            per.append(h * math.log(1 + n / (1 + d)) if h else 0.0)
        if not any(per):
            continue
        matched = sum(1 for x in per if x)
        score = sum(math.log(1 + x) for x in per) * (matched / len(groups)) ** 2
        if c["file"].endswith("todo.md"):
            score *= 0.8
        if named and " › " not in c["path"]:
            score *= 2            # a whole article named after the topic beats a section that mentions it
        if c["file"] == "glossary.md":
            score *= 0.2          # the glossary names everything; it should not crowd out the notes
        c["matched"] = matched
        lines = [(i, l.strip()) for i, l in c["lines"] if any(hits(l, g) for g in groups)][:3]
        scored.append((score, c, lines))
    scored.sort(key=lambda x: -x[0])
    return scored[:top]


# ---------------------------------------------------------------- secret scanning

PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("OpenAI/Anthropic key", re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abpors]-[A-Za-z0-9-]{10,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Cloudflare API token", re.compile(r"\bCF_API_TOKEN\s*=\s*[A-Za-z0-9_-]{30,}")),
    ("assignment of a secret", re.compile(
        r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key|obfs-password|psk|passcode)\b\s*[:=：＝]\s*[\"'“]?(?P<val>[^\s\"'“”<>`|,)，。；]{8,})")),
    # 「sudo 密码：xxx」「路由器密码是 xxx」「管理员口令 = xxx」: no \b (CJK letters are \w), shorter values
    ("assignment of a secret", re.compile(
        r"(密码|口令|密钥|秘钥|令牌|暗号|パスワード)\s*(?:[:=：＝]|是|为|為)\s*[\"'“]?(?P<val>[^\s\"'“”<>`|,)，。；、]{6,})")),
    ("ssh public key blob", re.compile(r"\b(ssh-ed25519|ssh-rsa) AAAA[A-Za-z0-9+/=]{40,}")),
]
PLACEHOLDER = re.compile(r"(?i)^(<[^>]*>|\$\{?\w+\}?|x{4,}|\*{3,}|redacted|<redacted>|your_|example|changeme|\.\.\.|…|%s|%\(.*\)s|\{.*\}|bw:|🔑|profile-use:|~?/|\./|op://|keychain)")
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
NOT_A_VALUE = {"true", "false", "none", "null", "required", "optional", "unset", "empty", "same", "stdin"}


def is_placeholder(val: str) -> bool:
    """A value that names where a secret lives (a path, a pointer, prose) rather than being one."""
    return bool(PLACEHOLDER.match(val) or val.lower() in NOT_A_VALUE or CJK.search(val)
                or re.search(r"\.(env|txt|json|ya?ml|conf|key|pem|plist|toml)$", val, re.I))


def load_known_secrets() -> list[str]:
    if not KNOWN_SECRETS.exists():
        return []
    vals = []
    for line in KNOWN_SECRETS.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and len(line) >= 6:
            vals.append(line)
    return vals


def load_known_hashes() -> set[str]:
    if not KNOWN_HASHES.exists():
        return set()
    return {l.strip() for l in KNOWN_HASHES.read_text().splitlines() if re.fullmatch(r"[0-9a-f]{64}", l.strip())}


TOKEN_SPLIT = re.compile(r"[\s\"'`<>()\[\]{},;|，。；、]+")
TOKEN_SPLIT_FINE = re.compile(r"[\s\"'`<>()\[\]{},;|，。；、:=：＝]+")   # also "密码：xxx", "pw=xxx"


def scan_text(text: str, known: list[str], hashes: set[str] | None = None) -> list[tuple[int, str]]:
    import hashlib
    hashes = load_known_hashes() if hashes is None else hashes
    found: list[tuple[int, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if hashes and "memory-use: allow" not in line:
            toks = set(TOKEN_SPLIT.split(line)) | set(TOKEN_SPLIT_FINE.split(line))
            for tok in toks:
                for cand in {tok, tok.rstrip(".:。，")}:
                    if len(cand) >= 6 and hashlib.sha256(cand.encode()).hexdigest() in hashes:
                        found.append((n, "vault secret (hash match)"))
                        break
        if "memory-use: allow" in line:
            continue
        for name, rx in PATTERNS:
            m = rx.search(line)
            if not m:
                continue
            if name == "assignment of a secret" and is_placeholder(m.group("val")):
                continue
            if (n, name) not in found:
                found.append((n, name))
        for s in known:
            if s in line:
                found.append((n, "known secret value"))
    return found


def scan_paths(paths: list[Path]) -> list[str]:
    known = load_known_secrets()
    problems = []
    for p in paths:
        if not p.is_file() or (p.suffix not in TEXT_SUFFIXES and p.name not in {"Makefile", "Dockerfile"}):
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for n, name in scan_text(text, known):
            problems.append(f"{p}:{n}: {name}")
    return problems


def staged_blobs(root: Path) -> list[tuple[str, str]]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", cwd=root).stdout.split("\n")
    out = []
    for name in filter(None, names):
        p = Path(name)
        if p.suffix not in TEXT_SUFFIXES and p.name not in {"Makefile", "Dockerfile", "pre-commit"}:
            continue  # binaries (.pyc, images) are not notes; scanning them only yields noise
        blob = subprocess.run(["git", "show", f":{name}"], cwd=root, capture_output=True).stdout
        if b"\0" in blob:
            continue
        out.append((name, blob.decode(errors="ignore")))
    return out


# ---------------------------------------------------------------- bitwarden-use (vault) bridge


def bwu_bin() -> str | None:
    return os.environ.get("MEMORY_USE_BWU") or shutil.which("bitwarden-use") or shutil.which("bwu")


def bwu(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    b = bwu_bin()
    if not b:
        sys.exit("bitwarden-use not installed: curl -fsSL https://raw.githubusercontent.com/leeguooooo/bitwarden-use/main/install.sh | sh")
    return subprocess.run([b, *args], input=stdin, text=True, capture_output=True)


def bwu_confirmed(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    """A vault write the user already asked for (`secret put`). bitwarden-use 0.9+
    refuses writes without --yes ("confirmation required"); older releases don't
    know the flag, so retry without it there."""
    r = bwu(*args, "--yes", stdin=stdin)
    if r.returncode and "--yes" in (r.stderr or "") and "unexpected argument" in (r.stderr or ""):
        r = bwu(*args, stdin=stdin)
    return r


def vault_unlocked() -> bool:
    return bool(bwu_bin()) and bwu("unlocked").returncode == 0


def vault_items() -> list[dict]:
    """Metadata only (id, name, user, folder, type) — never values."""
    import json
    r = bwu("list", "--raw")
    if r.returncode:
        sys.exit("bitwarden-use list failed: " + (r.stderr.strip() or r.stdout.strip()))
    return [{k: i.get(k) for k in ("id", "name", "user", "folder", "type")} for i in json.loads(r.stdout or "[]")]


def pointer(item: dict) -> str:
    return f"bw:{item['id']}（{item.get('name')}）"


def cmd_secret(a) -> int:
    if a.action == "find":
        terms = [t.lower() for t in a.args]
        hits = [i for i in vault_items() if all(t in " ".join(str(i.get(k) or "") for k in ("name", "user", "folder")).lower() for t in terms)]
        for i in hits[:30]:
            print(f"{pointer(i)}  folder={i.get('folder') or '-'}  user={'yes' if i.get('user') else '-'}")
        if not hits:
            print("no vault item matches")
        return 0 if hits else 1
    if a.action == "put":
        if not a.args:
            sys.exit('usage: secret put <name> [user] [--generate N]   (value on stdin unless --generate)')
        name, user = a.args[0], (a.args[1] if len(a.args) > 1 else None)
        if a.generate:
            r = bwu_confirmed("generate", str(a.generate), name, *([user] if user else []), "--folder", SECRET_FOLDER)
        else:
            value = sys.stdin.read().rstrip("\n")
            if not value:
                sys.exit("no value on stdin")
            r = bwu_confirmed("add", name, *([user] if user else []), "--folder", SECRET_FOLDER, stdin=value + "\n")
        if r.returncode:
            sys.exit("bitwarden-use failed: " + (r.stderr.strip() or r.stdout.strip()))
        bwu("sync")
        new = [i for i in vault_items() if i.get("name") == name and (i.get("folder") or "") == SECRET_FOLDER]
        if not new:
            sys.exit("stored, but could not find it again — run `secret find " + name + "`")
        print("stored in Bitwarden folder '" + SECRET_FOLDER + "'. Put this in the note:\n  🔑 " + pointer(new[-1]))
        return 0
    if a.action == "get":
        if not a.args:
            sys.exit("usage: secret get <bw:uuid | uuid | name> [--field F] (--copy | --env VAR -- cmd … | --print)")
        ref = a.args[0][3:] if a.args[0].startswith("bw:") else a.args[0]
        r = bwu("get", ref, "--field", a.field, "--reveal")
        if r.returncode:
            sys.exit("bitwarden-use get failed: " + (r.stderr.strip() or r.stdout.strip()))
        value = r.stdout.rstrip("\n")
        if a.env:
            if not a.cmd:
                sys.exit("--env needs a command after --")
            return subprocess.call(a.cmd, env={**os.environ, a.env: value})
        if a.copy:
            subprocess.run(["pbcopy"], input=value, text=True)
            subprocess.Popen(["/bin/sh", "-c", "sleep 30; printf '' | pbcopy"], start_new_session=True)
            print("copied to clipboard; cleared in 30 s")
            return 0
        if a.print:
            print(value)
            return 0
        sys.exit("refusing to print a secret: use --copy, --env VAR -- cmd, or --print if the user asked to see it")
    if a.action == "refs":
        return check_pointers(verbose=True)
    if a.action == "hashes":
        import hashlib
        items = [i for i in vault_items() if (i.get("folder") or "").lower() == SECRET_FOLDER.lower()]
        out = []
        for i in items:
            r = bwu("get", i["id"], "--field", "password", "--reveal")
            if r.returncode == 0 and len(r.stdout.strip()) >= 6:
                out.append(hashlib.sha256(r.stdout.strip().encode()).hexdigest())
        KNOWN_HASHES.parent.mkdir(parents=True, exist_ok=True)
        KNOWN_HASHES.write_text("\n".join(sorted(set(out))) + ("\n" if out else ""))
        os.chmod(KNOWN_HASHES, 0o600)
        print(f"{len(set(out))} secret hash(es) from folder '{SECRET_FOLDER}' → {KNOWN_HASHES} (values never stored)")
        return 0
    sys.exit("unknown secret action")


def check_pointers(verbose: bool = False) -> int:
    d = need_repo()
    refs = [(str(p.relative_to(d)), n, m.group(1)) for p in md_files(d) for n, l in enumerate(p.read_text(errors="ignore").splitlines(), 1) for m in POINTER.finditer(l)]
    if not refs:
        if verbose:
            print("no bw: pointers in notes")
        return 0
    if not vault_unlocked():
        print(f"{len(refs)} bw: pointer(s) not checked — vault is locked (unlock it to verify)")
        return 0
    ids = {i["id"] for i in vault_items()}
    bad = [(f, n, u) for f, n, u in refs if u not in ids]
    for f, n, u in bad:
        print(f"WARN {f}:{n}: bw:{u} not found in the vault")
    if verbose and not bad:
        print(f"{len(refs)} bw: pointer(s) OK")
    return 1 if bad else 0


# ---------------------------------------------------------------- commands


# ---------------------------------------------------------------- Apple Reminders bridge (one-way: notes → Reminders)


def osa_bin() -> str:
    return os.environ.get("MEMORY_USE_OSASCRIPT") or shutil.which("osascript") or sys.exit("osascript not found: `remind` needs macOS")


def as_text(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def parse_due(s: str) -> dt.datetime:
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
        return d.replace(hour=9) if fmt == "%Y-%m-%d" else d
    sys.exit(f"bad --due {s!r}: use YYYY-MM-DD (09:00) or 'YYYY-MM-DD HH:MM'")


def osa_date(var: str, d: dt.datetime) -> str:
    # field by field, so the result does not depend on the Mac's locale or date format
    return "\n".join([f"set {var} to current date", f"set day of {var} to 1", f"set year of {var} to {d.year}",
                      f"set month of {var} to {d.month}", f"set day of {var} to {d.day}",
                      f"set time of {var} to {d.hour * 3600 + d.minute * 60}"])


def osa_list(name: str | None) -> str:
    return f"list {as_text(name)}" if name else "default list"


def osa(script: str) -> str:
    try:
        r = subprocess.run([osa_bin()], input=script, text=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        sys.exit("Reminders did not answer in 120 s (a permission dialog may be waiting on the Mac screen)")
    if r.returncode:
        err = (r.stderr or r.stdout).strip()
        if "-1743" in err or "not allowed" in err.lower():
            err += "\n→ allow once: System Settings → Privacy & Security → Automation → (your terminal) → Reminders"
        sys.exit("Reminders failed: " + err)
    return r.stdout


REMIND_LS = """set out to ""
tell application "Reminders"
  repeat with r in (reminders of LIST whose completed is false)
    set d to due date of r
    set ds to ""
    if d is not missing value then
      set ds to ((year of d) as string) & "-" & text -2 thru -1 of ("0" & ((month of d) as integer)) & "-" & text -2 thru -1 of ("0" & (day of d)) & " " & text -2 thru -1 of ("0" & (hours of d)) & ":" & text -2 thru -1 of ("0" & (minutes of d))
    end if
    set out to out & (name of r) & tab & ds & linefeed
  end repeat
end tell
return out"""


def cmd_remind(a) -> int:
    if a.action == "ls":
        rows = [(l.split("\t") + [""])[:2] for l in osa(REMIND_LS.replace("LIST", osa_list(a.list))).splitlines() if l.strip()]
        rows.sort(key=lambda r: (not r[1], r[1]))
        for title, due in rows:
            print(f"{due or '—':16}  {title}")
        print(f"\n{len(rows)} open reminder(s)" + (f" in {a.list}" if a.list else ""))
        return 0
    if not a.args:
        sys.exit('usage: remind add "<title>" [--due YYYY-MM-DD[ HH:MM]] [--list L] [--note N]')
    title = " ".join(a.args)
    hits = scan_text(title + "\n" + (a.note or ""), load_known_secrets())
    if hits:
        sys.exit("refusing: the reminder looks like it contains a secret (" + ", ".join(sorted({h for _, h in hits}))
                 + "). Reminders sync through iCloud — store the secret with `secret put` and write only the pointer.")
    due = parse_due(a.due) if a.due else None
    props = f"name:{as_text(title)}" + (f", body:{as_text(a.note)}" if a.note else "")
    script = [osa_date("d", due)] if due else []
    script += ['tell application "Reminders"', f"  tell {osa_list(a.list)}",
               f"    set r to make new reminder with properties {{{props}}}", "  end tell"]
    if due:
        script += ["  set due date of r to d", "  set remind me date of r to d"]
    script += ["  return id of r", "end tell"]
    osa("\n".join(script))
    when = f" due {due:%Y-%m-%d %H:%M}" if due else ""
    print(f"added to Reminders ({a.list or 'default list'}){when}. Optional pointer for the note:")
    print(f"  ⏰ 提醒事项：{title}" + (f"（{due:%Y-%m-%d}）" if due else ""))
    return 0


def cmd_path(_a) -> int:
    print(repo_dir())
    return 0


# ---------------------------------------------------------------- setup: init / clone / skill links / hook


def gh_login() -> str | None:
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True)
    return r.stdout.strip() or None if r.returncode == 0 else None


def is_url(slug: str) -> bool:
    """A git URL or local path (any host) rather than a GitHub owner/name."""
    return "://" in slug or slug.startswith(("git@", "/", "~", ".")) or slug.endswith(".git")


def repo_name(slug: str) -> str:
    return re.sub(r"\.git$", "", slug.rstrip("/").split("/")[-1].split(":")[-1])


def remote_exists(slug: str) -> bool:
    if is_url(slug):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        return subprocess.run(["git", "ls-remote", os.path.expanduser(slug)], capture_output=True, env=env).returncode == 0
    if shutil.which("gh"):
        return subprocess.run(["gh", "repo", "view", slug, "--json", "name"], capture_output=True).returncode == 0
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "ls-remote", f"https://github.com/{slug}.git"], capture_output=True, env=env).returncode == 0


def clone_repo(slug: str, d: Path) -> int:
    d.parent.mkdir(parents=True, exist_ok=True)
    if is_url(slug):
        cmd = ["git", "clone", os.path.expanduser(slug), str(d)]
    else:
        cmd = ["gh", "repo", "clone", slug, str(d)] if shutil.which("gh") else ["git", "clone", f"https://github.com/{slug}.git", str(d)]
    return subprocess.call(cmd)


def create_from_template(slug: str, d: Path) -> None:
    """A new private notes repo: the starter files from template/, one commit, pushed to GitHub."""
    if not shutil.which("gh"):
        sys.exit("creating the notes repo needs the GitHub CLI: install gh, run `gh auth login`, then retry")
    tpl = TOOL_ROOT / "template"
    if not tpl.is_dir():
        sys.exit(f"template not found at {tpl}; reinstall memory-use")
    shutil.copytree(tpl, d)
    for f in d.rglob("*"):
        if f.is_file() and f.suffix in {".md", ""}:
            try:
                f.write_text(f.read_text().replace("{{REPO}}", slug).replace("{{INSTALL_URL}}", INSTALL_URL))
            except UnicodeDecodeError:
                pass
    (d / ".githooks" / "pre-commit").chmod(0o755)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init notes from the memory-use template"]):
        r = git(*args, cwd=d, check=False)
        if r.returncode:
            sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    r = subprocess.run(["gh", "repo", "create", slug, "--private", "--source", str(d), "--remote", "origin", "--push"],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"gh repo create failed (notes are committed locally in {d}): {r.stderr.strip()}")
    print(f"created private repo {slug} from the template")


def claude_plugin() -> str | None:
    """'memory-use@<marketplace> <version>' when Claude Code has memory-use installed as a plugin."""
    try:
        plugins = json.loads((Path.home() / ".claude" / "plugins" / "installed_plugins.json").read_text()).get("plugins", {})
    except (OSError, ValueError, AttributeError):
        return None
    for key, installs in plugins.items():
        if key.startswith("memory-use@"):
            ver = next((i.get("version") for i in installs if isinstance(i, dict) and i.get("version")), None) \
                if isinstance(installs, list) else None
            return f"{key} {ver}" if ver else key
    return None


def claude_skill_link() -> Path:
    return Path.home() / ".claude" / "skills" / "memory-use"


def skill_links() -> list[Path]:
    home = Path.home()
    out = [home / ".agents" / "skills" / "memory-use", home / ".claude" / "skills" / "memory-use"]
    if (home / ".codex" / "skills").is_dir():
        out.append(home / ".codex" / "skills" / "memory-use")
    return out


def ensure_skill_links() -> None:
    """Link this checkout into the agents' skill folders; never replaces a real directory or someone else's link."""
    if not (TOOL_ROOT / "SKILL.md").exists() or os.environ.get("MEMORY_USE_NO_SKILL_LINKS"):
        return    # installed as a plugin: the host already loads the skill
    plugin = claude_plugin()
    for link in skill_links():
        if plugin and link == claude_skill_link():
            continue    # the Claude Code plugin already provides the skill; a link would load it twice
        if link.is_symlink() and link.resolve() == TOOL_ROOT:
            continue
        if link.exists() or link.is_symlink():
            print(f"skip: {link} exists (points elsewhere or is a folder); leaving it alone")
            continue
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(TOOL_ROOT, target_is_directory=True)
        except OSError:
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(TOOL_ROOT)], capture_output=True)
        print(f"linked: {link} -> {TOOL_ROOT}")


HOOK_MARKER = "memory-use secret scan"


def enable_hook(d: Path) -> str:
    if (d / ".githooks" / "pre-commit").exists():
        (d / ".githooks" / "pre-commit").chmod(0o755)
        git("config", "core.hooksPath", ".githooks", cwd=d)
        return "core.hooksPath=.githooks"
    hooks_path = git("config", "core.hooksPath", cwd=d, check=False).stdout.strip()
    hook = (d / hooks_path if hooks_path else d / ".git" / "hooks") / "pre-commit"
    if hook.exists() and HOOK_MARKER not in hook.read_text(errors="ignore"):
        return f"left your own hook alone: {hook} (add `memory_use.py check --staged` to it)"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text((TOOL_ROOT / "template" / ".githooks" / "pre-commit").read_text())
    hook.chmod(0o755)
    return f"installed {hook.relative_to(d)} (local, not committed)"


def hook_ok(d: Path) -> tuple[bool, str]:
    hooks_path = git("config", "core.hooksPath", cwd=d, check=False).stdout.strip()
    hook = (d / hooks_path if hooks_path else d / ".git" / "hooks") / "pre-commit"
    good = hook.exists() and ("memory_use.py" in hook.read_text(errors="ignore") or HOOK_MARKER in hook.read_text(errors="ignore"))
    return good, f"secret-scan pre-commit hook: {hook.relative_to(d) if hook.exists() else '(none)'}"


def cmd_init(a) -> int:
    """Set up (or re-check) the notes repo on this computer: clone or create, remember it, hook, skill, autosync."""
    cfg = load_config()
    slug = a.repo or repo_slug()
    if slug and "/" not in slug and not is_url(slug):
        slug = f"{gh_login() or sys.exit('give --repo as owner/name')}/{slug}"
    if not slug:
        login = gh_login()
        if not login:
            sys.exit("init needs --repo owner/name (or `gh auth login` first, then <you>/personal-memory is used)")
        slug = f"{login}/{DEFAULT_NAME}"
    if a.dir:
        d = Path(a.dir).expanduser().resolve()
    elif os.environ.get("MEMORY_USE_DIR"):
        d = Path(os.environ["MEMORY_USE_DIR"]).expanduser()
    elif cfg.get("dir") and cfg.get("repo") == slug:
        d = Path(cfg["dir"]).expanduser()
    else:
        d = Path.home() / "github.com" / repo_name(slug)
    if (d / ".git").exists():
        print(f"repo: {d} (already here)")
    elif d.exists() and any(d.iterdir()):
        sys.exit(f"{d} exists, is not a git repo and is not empty; pick another --dir")
    elif remote_exists(slug):
        if clone_repo(slug, d):
            sys.exit(f"clone of {slug} failed (private repo? run `gh auth login` first)")
    elif a.create and not is_url(slug):
        create_from_template(slug, d)
    else:
        sys.exit(f"{slug} was not found on GitHub (or this account cannot see it).\n"
                 f"New notes repo: memory_use.py init --repo {slug} --create   (private, from the starter template)")
    save_config(repo=slug, dir=str(d))
    print(f"config: {CONFIG}")
    print("hook: " + enable_hook(d))
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not KNOWN_SECRETS.exists():
        KNOWN_SECRETS.write_text("# One real secret per line (passwords, tokens). Never committed; used to catch literal leaks.\n")
    try:
        KNOWN_SECRETS.chmod(0o600)
    except OSError:
        pass
    ensure_skill_links()
    if not a.no_autosync:
        autosync_enable(a.every)
    print()
    return cmd_doctor(a)


def cmd_clone(a) -> int:
    d = repo_dir()
    if (d / ".git").exists():
        print(f"already cloned: {d}")
        return 0
    slug = repo_slug()
    if not slug:
        sys.exit("no repo configured: memory_use.py init --repo <owner>/<name>")
    return clone_repo(slug, d)


# ---------------------------------------------------------------- autosync: pull + push on a timer, never auto-commit

AUTOSYNC_LABEL = "com.github.memory-use.autosync"
STATE_FILE = STATE_DIR / "autosync.json"


def script_path() -> str:
    return str(Path(__file__).resolve())


def autosync_units() -> dict[str, Path]:
    home = Path.home()
    return {
        "darwin": home / "Library" / "LaunchAgents" / f"{AUTOSYNC_LABEL}.plist",
        "systemd-service": home / ".config" / "systemd" / "user" / "memory-use-autosync.service",
        "systemd-timer": home / ".config" / "systemd" / "user" / "memory-use-autosync.timer",
    }


def run_quiet(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def autosync_enable(every: int = 15) -> None:
    every = max(5, every)
    units = autosync_units()
    py, script = sys.executable, script_path()
    if sys.platform == "darwin":
        logs = Path.home() / "Library" / "Logs" / "memory-use"
        logs.mkdir(parents=True, exist_ok=True)
        path = os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", str(Path.home() / ".local" / "bin")])
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{AUTOSYNC_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>{script}</string><string>autosync</string><string>run</string></array>
  <key>StartInterval</key><integer>{every * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>StandardOutPath</key><string>{logs / 'autosync.log'}</string>
  <key>StandardErrorPath</key><string>{logs / 'autosync.log'}</string>
</dict>
</plist>
"""
        units["darwin"].parent.mkdir(parents=True, exist_ok=True)
        units["darwin"].write_text(plist)
        domain = f"gui/{os.getuid()}"
        run_quiet(["launchctl", "bootout", f"{domain}/{AUTOSYNC_LABEL}"])
        r = run_quiet(["launchctl", "bootstrap", domain, str(units["darwin"])])
        if r.returncode:
            sys.exit("launchctl bootstrap failed: " + (r.stderr or r.stdout).strip())
        print(f"autosync: on, every {every} min (launchd {AUTOSYNC_LABEL}; log {logs / 'autosync.log'})")
    elif sys.platform == "win32":
        r = run_quiet(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(every), "/TN", "memory-use-autosync",
                       "/TR", f'"{py}" "{script}" autosync run'])
        if r.returncode:
            sys.exit("schtasks failed: " + (r.stderr or r.stdout).strip())
        print(f"autosync: on, every {every} min (Task Scheduler: memory-use-autosync)")
    elif shutil.which("systemctl") and run_quiet(["systemctl", "--user", "show-environment"]).returncode == 0:
        units["systemd-service"].parent.mkdir(parents=True, exist_ok=True)
        units["systemd-service"].write_text(f"[Unit]\nDescription=memory-use autosync\n\n[Service]\nType=oneshot\nExecStart={py} {script} autosync run\n")
        units["systemd-timer"].write_text(f"[Unit]\nDescription=memory-use autosync every {every} min\n\n[Timer]\nOnBootSec=2min\n"
                                          f"OnUnitActiveSec={every}min\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
        run_quiet(["systemctl", "--user", "daemon-reload"])
        r = run_quiet(["systemctl", "--user", "enable", "--now", "memory-use-autosync.timer"])
        if r.returncode:
            sys.exit("systemctl failed: " + (r.stderr or r.stdout).strip())
        print(f"autosync: on, every {every} min (systemd --user memory-use-autosync.timer)")
    else:
        cur = run_quiet(["crontab", "-l"]).stdout
        lines = [l for l in cur.splitlines() if "memory-use-autosync" not in l]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lines.append(f"*/{every} * * * * {py} {script} autosync run >> {STATE_DIR / 'autosync.log'} 2>&1 # memory-use-autosync")
        r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, capture_output=True)
        if r.returncode:
            sys.exit("crontab failed: " + (r.stderr or r.stdout).strip())
        print(f"autosync: on, every {every} min (crontab)")


def autosync_disable() -> None:
    units = autosync_units()
    if sys.platform == "darwin":
        run_quiet(["launchctl", "bootout", f"gui/{os.getuid()}/{AUTOSYNC_LABEL}"])
        units["darwin"].unlink(missing_ok=True)
    elif sys.platform == "win32":
        run_quiet(["schtasks", "/Delete", "/F", "/TN", "memory-use-autosync"])
    else:
        if shutil.which("systemctl"):
            run_quiet(["systemctl", "--user", "disable", "--now", "memory-use-autosync.timer"])
            units["systemd-timer"].unlink(missing_ok=True)
            units["systemd-service"].unlink(missing_ok=True)
        cur = run_quiet(["crontab", "-l"])
        if cur.returncode == 0 and "memory-use-autosync" in cur.stdout:
            keep = [l for l in cur.stdout.splitlines() if "memory-use-autosync" not in l]
            subprocess.run(["crontab", "-"], input="\n".join(keep) + "\n", text=True, capture_output=True)
    print("autosync: off")


def autosync_installed() -> bool:
    units = autosync_units()
    if sys.platform == "darwin":
        return units["darwin"].exists()
    if sys.platform == "win32":
        return run_quiet(["schtasks", "/Query", "/TN", "memory-use-autosync"]).returncode == 0
    return units["systemd-timer"].exists() or "memory-use-autosync" in run_quiet(["crontab", "-l"]).stdout


def autosync_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def autosync_once(d: Path) -> tuple[bool, str]:
    """fetch; fast-forward or rebase onto upstream; push local commits. Never commits, never stashes,
    never touches a repo mid-rebase/merge. Uncommitted edits (maybe another session's) are left alone."""
    gd = d / ".git"
    if any((gd / x).exists() for x in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD")):
        return False, "skipped: a rebase/merge is in progress"
    f = git("fetch", "-q", "origin", cwd=d, check=False)
    if f.returncode:
        return False, "fetch failed (offline?): " + (f.stderr.strip().splitlines() or ["?"])[-1]
    if git("rev-parse", "--abbrev-ref", "@{u}", cwd=d, check=False).returncode:
        return False, "skipped: current branch has no upstream"
    counts = git("rev-list", "--left-right", "--count", "HEAD...@{u}", cwd=d).stdout.split()
    ahead, behind = int(counts[0]), int(counts[1])
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", cwd=d).stdout.strip())
    did = []
    if behind and not ahead:
        r = git("merge", "-q", "--ff-only", "@{u}", cwd=d, check=False)
        if r.returncode:
            return False, f"behind {behind}: fast-forward blocked by uncommitted edits to the same files; left alone"
        did.append(f"pulled {behind}")
    elif behind and ahead:
        if dirty:
            return False, f"diverged (ahead {ahead}, behind {behind}) with uncommitted edits; left alone until they are committed"
        r = git("rebase", "-q", "@{u}", cwd=d, check=False)
        if r.returncode:
            git("rebase", "--abort", cwd=d, check=False)
            return False, f"diverged (ahead {ahead}, behind {behind}): rebase conflicts; aborted, needs a human (`memory_use.py sync`)"
        did.append(f"rebased {ahead} onto {behind} new")
    if ahead:
        r = git("push", "-q", cwd=d, check=False)
        if r.returncode:
            return False, "push failed: " + (r.stderr.strip().splitlines() or ["?"])[-1]
        did.append(f"pushed {ahead}")
    note = " (uncommitted edits present, not touched)" if dirty else ""
    return True, (", ".join(did) or "up to date") + note


def autosync_run() -> int:
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")                  # never hang on a credential prompt
    os.environ.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")
    d = repo_dir()
    if not (d / ".git").exists():
        ok, msg = False, f"no repo at {d}"
    else:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lock = STATE_DIR / "autosync.lock"
        try:
            if lock.exists() and time.time() - lock.stat().st_mtime > 600:
                lock.unlink()
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            print("autosync: another run is in progress")
            return 0
        try:
            ok, msg = autosync_once(d)
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"at": now, "ok": ok, "result": msg, "repo": str(d)}, ensure_ascii=False) + "\n")
    print(f"{now} {'ok' if ok else 'WARN'} {msg}")
    return 0 if ok else 1


def cmd_autosync(a) -> int:
    if a.action == "run":
        return autosync_run()
    if a.action == "on":
        need_repo()
        autosync_enable(a.every)
        return autosync_run()
    if a.action == "off":
        autosync_disable()
        return 0
    st = autosync_state()
    print(f"autosync: {'on' if autosync_installed() else 'off'}")
    if st:
        print(f"last run: {st.get('at')} — {'ok' if st.get('ok') else 'WARN'} {st.get('result')}")
    return 0


# ---------------------------------------------------------------- doctor / migrate / upgrade


def cmd_doctor(_a) -> int:
    d = repo_dir()
    ok = True
    def line(good: bool, msg: str, warn: bool = True):
        nonlocal ok
        ok &= good or not warn
        print(("OK   " if good else ("WARN " if warn else "--   ")) + msg)
    print(f"memory-use {VERSION} ({TOOL_ROOT})")
    slug = repo_slug()
    line(bool(slug), f"notes repo: {slug or '(not configured — run init)'}; config {CONFIG}")
    line((d / ".git").exists(), f"checkout at {d}")
    if not (d / ".git").exists():
        print("     run: memory_use.py init --repo <owner>/<name>")
        return 1
    git("fetch", "-q", "origin", check=False)
    st = git("status", "-sb").stdout.splitlines()
    head = st[0] if st else ""
    line("behind" not in head and "ahead" not in head, f"branch: {head.lstrip('# ')}")
    dirty = [l for l in st[1:] if l.strip()]
    line(not dirty, f"uncommitted changes: {len(dirty)}" + ("" if not dirty else " (may belong to another session)"))
    line(*hook_ok(d))
    plugin = claude_plugin()
    for link in skill_links():
        if plugin and link == claude_skill_link():
            if link.exists() or link.is_symlink():
                line(False, f"skill loaded twice: Claude Code plugin {plugin} and {link} (remove the link)")
            else:
                line(True, f"skill installed: Claude Code plugin {plugin}")
            continue
        line(link.exists(), f"skill installed: {link}")
    auto = autosync_installed()
    stt = autosync_state()
    line(auto, "autosync: " + ("on" if auto else "off (memory_use.py autosync on)")
         + (f"; last run {stt.get('at')} {'ok' if stt.get('ok') else 'WARN'} {stt.get('result')}" if stt else ""))
    if stt and not stt.get("ok"):
        line(False, "last autosync did not finish: " + str(stt.get("result")))
    line(KNOWN_SECRETS.exists(), f"known-secrets list (optional, outside git): {KNOWN_SECRETS}", warn=False)
    return 0 if ok else 1


def cmd_migrate(a) -> int:
    """Run on the computer you are leaving: what would be lost, and the one line for the new one."""
    d = need_repo()
    slug = repo_slug() or "<owner>/<name>"
    risky = []
    print(f"# moving {slug} to another computer\n")
    git("fetch", "-q", "origin", check=False)
    if a.push:
        ok, msg = autosync_once(d)
        print(("pushed: " if ok else "WARN ") + msg)
    counts = git("rev-list", "--left-right", "--count", "HEAD...@{u}", cwd=d, check=False).stdout.split()
    if counts and counts[0] != "0":
        risky.append(f"{counts[0]} local commit(s) not pushed → `memory_use.py migrate --push` (or autosync run)")
    status = [l for l in git("status", "--porcelain", cwd=d).stdout.splitlines() if l.strip()]
    if status:
        risky.append(f"{len(status)} uncommitted file(s) — commit with `save <paths> -m …` or discard:\n"
                     + "\n".join("      " + l for l in status[:15]))
    stashes = git("stash", "list", cwd=d).stdout.strip().splitlines()
    if stashes:
        risky.append(f"{len(stashes)} stash(es) — `git stash list` in {d}")
    print("## would be lost" if risky else "## notes repo: everything is on GitHub")
    for r in risky:
        print("  - " + r)
    print("\n## stays on this computer (by design, outside git)")
    n_known = len(load_known_secrets())
    print(f"  - {KNOWN_SECRETS}: {n_known} literal secret(s) for leak detection — move them into your password manager, "
          "not a file copy; with bitwarden-use run `secret hashes` on the new computer instead")
    if KNOWN_HASHES.exists():
        print(f"  - {KNOWN_HASHES}: rebuilt on the new computer by `secret hashes`")
    agent_mem = sorted((Path.home() / ".claude" / "projects").glob("*/memory/*.md"))
    if agent_mem:
        print(f"  - {len(agent_mem)} Claude Code local memory file(s) under ~/.claude/projects/*/memory — they do not travel; "
              "anything durable in them belongs in the notes repo")
    guide = next((g for g in ("setup-new-computer.md", "new-computer.md") if (d / g).exists()), None)
    print("\n## on the new computer")
    print(f"  curl -fsSL {INSTALL_URL} | sh -s -- --repo {slug}")
    print("  (installs the skill, clones the notes, enables the secret-scan hook and autosync; needs `gh auth login` for a private repo)")
    if guide:
        print(f"  then follow {guide} in the notes repo for everything else (SSH keys, VPN, passwords…)")
    return 1 if risky else 0


def latest_release(timeout: float = 5) -> str | None:
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{TOOL_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "memory-use"})
    if os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {os.environ['GITHUB_TOKEN']}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("tag_name", "").lstrip("v") or None
    except Exception:
        return None


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def cmd_upgrade(a) -> int:
    latest = latest_release()
    skills = []
    if (TOOL_ROOT / ".git").exists():
        skills.append({"channel": "git-checkout", "path": str(TOOL_ROOT), "update": f"git -C {TOOL_ROOT} pull --ff-only"})
    plugin = claude_plugin()
    if plugin:
        skills.append({"channel": "claude-plugin", "path": plugin, "update": f"claude plugin update {plugin.split()[0]}"})
    avail = bool(latest and vtuple(latest) > vtuple(VERSION))
    if a.json:
        print(json.dumps({"name": "memory-use", "current": VERSION, "latest": latest, "update_available": avail, "skills": skills}))
        return 0 if latest else 2
    if a.check:
        if not latest:
            print("memory-use: could not reach GitHub releases")
            return 2
        print(f"memory-use {VERSION} -> {latest}" if avail else f"memory-use {VERSION} is up to date")
        return 0
    rc = 0
    for s in skills:
        if s["channel"] == "git-checkout":
            r = git("pull", "--ff-only", "-q", cwd=TOOL_ROOT, check=False)
            print(f"-- skill checkout {TOOL_ROOT}: " + ("updated" if not r.returncode else "not updated: " + r.stderr.strip()))
            rc |= 2 if r.returncode else 0
        elif shutil.which("claude"):
            r = run_quiet(["claude", "plugin", "update", s["path"].split()[0]])
            print("-- claude plugin: " + ((r.stdout or r.stderr).strip().splitlines() or ["done"])[-1])
        else:
            print("-- claude plugin: run " + s["update"])
    if not skills:
        print(f"not a git checkout; reinstall: curl -fsSL {INSTALL_URL} | sh")
    m = re.search(r'^VERSION = "([^"]+)"', (TOOL_ROOT / "scripts" / "memory_use.py").read_text(), re.M)
    now = m.group(1) if m else VERSION
    print(f"memory-use {VERSION} -> {now}" if now != VERSION else f"memory-use {VERSION}")
    return rc


def update_notice() -> None:
    """At most once a day, one stderr line when a newer release exists (family convention)."""
    if os.environ.get("CI") or os.environ.get("MEMORY_USE_NO_UPDATE_CHECK") or os.environ.get("USE_NO_UPDATE_CHECK"):
        return
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "memory-use" / "update-check.json"
    try:
        st = json.loads(cache.read_text())
    except (OSError, ValueError):
        st = {}
    if time.time() - st.get("checked_at", 0) > 86400:
        st = {"checked_at": int(time.time()), "latest": latest_release(timeout=2) or st.get("latest")}
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(st))
        except OSError:
            pass
    if st.get("latest") and vtuple(st["latest"]) > vtuple(VERSION):
        print(f"memory-use {st['latest']} is available (you have {VERSION}). Upgrade: memory-use upgrade", file=sys.stderr)


def prompt_mentions(root: Path, prompt: str, limit: int = 3) -> list[str]:
    """Glossary concepts (by first alias) and section names that a user message mentions."""
    low = prompt.lower()
    found: list[str] = []
    for g in load_glossary(root):
        for alias in g:
            if alias.isascii() and len(alias) < 3:
                continue                      # "mp", "ip": too noisy for a nudge
            if Alias(alias).count(low) if alias.isascii() else alias in low:
                if g[0] not in found:
                    found.append(g[0])
                break
    for name, _ in sections(root):
        if len(name) >= 3 and Alias(name).count(low) and name not in found:
            found.append(name)
    return found[:limit]


def cmd_hook(a) -> int:
    """Claude Code hooks. prompt: nudge to recall when the message names something the notes know.
    session: one line only when the memory is not healthy (no repo, autosync failing). Never fails the host."""
    try:
        d = repo_dir()
        if a.event == "session":
            if not (d / ".git").exists():
                print(f"memory-use: notes repo not set up on this computer — offer `memory-use init --repo <owner>/<name>`")
            else:
                st = autosync_state()
                if st and not st.get("ok"):
                    print(f"memory-use: autosync is stuck ({st.get('result')}); run `memory-use sync` / `doctor` before relying on the notes")
            return 0
        raw = sys.stdin.read()
        try:
            prompt = json.loads(raw).get("prompt", "") if raw.strip().startswith("{") else raw
        except ValueError:
            prompt = raw
        if not prompt.strip() or not (d / ".git").exists():
            return 0
        hits = prompt_mentions(d, prompt)
        if hits:
            print(f"memory-use: the notes know about {', '.join(hits)}. Before acting, run "
                  f"`memory-use brief {' '.join(h.split()[0] for h in hits)}` and read the matching notes.")
    except Exception:
        pass
    return 0


def cmd_sync(_a) -> int:
    d = need_repo()
    dirty = git("status", "--porcelain", cwd=d).stdout.strip()
    r = git("pull", "--rebase", "--autostash" if dirty else "--ff-only", "-q", cwd=d, check=False)
    if r.returncode:
        print(r.stderr.strip() or r.stdout.strip())
        return r.returncode
    ahead = git("rev-list", "--count", "@{u}..HEAD", cwd=d, check=False).stdout.strip()
    if ahead not in ("", "0"):
        p = git("push", "-q", cwd=d, check=False)
        print(f"pushed {ahead} local commit(s)" if not p.returncode else "push failed: " + p.stderr.strip())
    print(git("log", "--oneline", "-1", cwd=d).stdout.strip())
    return 0


def cmd_ls(_a) -> int:
    d = need_repo()
    for name, desc in sections(d):
        todo = d / name / "todo.md"
        open_items = len(re.findall(r"^\s*- \[ \]", todo.read_text(), re.M)) if todo.exists() else 0
        print(f"{name:<12} {desc}  [{open_items} open todo]")
    return 0


def cmd_show(a) -> int:
    d = need_repo()
    target = d / a.target
    if target.is_dir():
        target = target / "README.md"
    elif not target.exists() and not a.target.endswith(".md"):
        target = d / f"{a.target}.md"
    if not target.exists():
        sys.exit(f"not found: {a.target}")
    print(f"# {target.relative_to(d)}\n")
    print(target.read_text())
    return 0


def cmd_search(a) -> int:
    d = need_repo()
    res = rank(d, a.terms, a.all, a.limit)
    if not res:
        print("no match — try fewer words, or an alias (see glossary.md)")
        return 1
    for score, c, lines in res:
        print(f"[{score:4.1f}] {c['file']}:{c['line']}  ‹{c['path'] or c['file']}›")
        for i, l in lines:
            print(f"        {i}: {l[:150]}")
    return 0


def section_of(file: str) -> str:
    return file.split("/", 1)[0] if "/" in file else ""


SUMMARY_HEADINGS = re.compile(r"^##\s+(关键事实|一句话|概要|出问题先看这里|Key facts|Summary|Troubleshooting|If something breaks)", re.I)
KEYPOINTS = re.compile(r"^(\*\*(要点|Key points|TL;DR)\*\*|##\s+(要点|Key points|TL;DR))", re.I)


def readme_summary(text: str, max_lines: int, groups: list[set[Alias]] | None = None) -> str:
    """The intro plus the summary sections (key facts, troubleshooting) of a section README, capped."""
    out, keep = [], True
    for line in text.splitlines():
        if line.startswith("## "):
            keep = bool(SUMMARY_HEADINGS.match(line))
        if keep and line.strip():
            out.append(line)
    if groups is not None:
        relevant, heading = [], None
        for line in out:
            if line.startswith("#"):
                heading = line
            elif any(hits(line, g) for g in groups):
                if heading and heading not in relevant:
                    relevant.append(heading)
                relevant.append(line)
        out = relevant
    if len(out) > max_lines:
        out = out[:max_lines] + ["…"]
    return "\n".join(out)


def keypoints(path: Path, max_lines: int = 6) -> list[str]:
    """The **要点** / **Key points** bullets near the top of an article."""
    lines = path.read_text(errors="ignore").splitlines()[:30]
    for i, line in enumerate(lines):
        if KEYPOINTS.match(line.strip()):
            pts = []
            for l in lines[i + 1:]:
                if l.startswith("#") or (pts and not l.strip()):
                    break
                if l.strip():
                    pts.append(l.rstrip())
            return pts[:max_lines]
    return []


def brief_matches(results: list, limit: int = 6) -> list:
    """Keep strong hits while allowing more than one file to contribute to the brief."""
    notes = [r for r in results if r[1]["file"] != "glossary.md"]
    if not notes:
        return results[:1]
    threshold = notes[0][0] * 0.25
    selected, counts = [], {}
    for result in notes:
        score, chunk, _ = result
        file = chunk["file"]
        if score < threshold or counts.get(file, 0) >= 2:
            continue
        selected.append(result)
        counts[file] = counts.get(file, 0) + 1
        if len(selected) >= limit:
            break
    return selected


def cmd_brief(a) -> int:
    """Context pack for a topic, most useful first: best matching notes (with their key points), open todos,
    trimmed section summaries, recent changes. Truncation drops from the end, so the matches survive."""
    d = need_repo()
    if a.max < 128 or a.readme_lines < 1:
        sys.exit("--max must be at least 128 and --readme-lines must be positive")
    secs = [n for n, _ in sections(d)]
    glossary = load_glossary(d)
    wanted = [t.lower() for t in a.topic]
    groups = expand(a.topic, glossary) if a.topic else []
    res = brief_matches(rank(d, a.topic, top=48)) if a.topic else []
    if a.topic and not res:
        print("no match — try fewer words, or an alias (see glossary.md)")
        return 1
    # A section name or its glossary alias alone asks for the whole section.
    broad_sections = [s for s in secs if len(groups) == 1 and any(x.text == s for x in groups[0])]
    broad = not a.topic or bool(broad_sections)
    chosen = [s for s in secs if s in wanted]
    chosen += [s for s in broad_sections if s not in chosen]
    if res:
        top = res[0][0]
        for score, c, _l in res:
            s = section_of(c["file"])
            # a section joins only through a strong, non-README match (a README that merely mentions
            # one alias, e.g. "mac mini" in the NAS README, does not pull the whole section in)
            if (s in secs and s not in chosen and len(chosen) < 2 and not c["file"].endswith("README.md")
                    and score >= 0.5 * top and any(hits(c["heading"] + "\n" + c["file"], g) for g in groups)):
                chosen.append(s)
    if not a.topic:
        chosen = secs
    out: list[str] = []
    emit = out.append
    emit(f"# brief: {' '.join(a.topic) or '(all)'}  — {dt.date.today().isoformat()}, repo {git('log', '-1', '--format=%h %ad', '--date=short', cwd=d).stdout.strip()}")
    aliases = sorted({x.text for g in groups for x in g} - set(wanted))
    if aliases:
        emit(f"aliases: {', '.join(aliases[:12])}")
    if res:
        emit("\n## best matching notes")
        shown_points: set[str] = set()
        points: dict[str, list[str]] = {}
        for score, c, lines in res:
            emit(f"- {c['file']}:{c['line']} ‹{c['path'] or c['file']}›")
            top_of_file = " › " not in c["path"] or c["line"] <= 3     # key points describe the article, not a mid-file section
            if (top_of_file and len(shown_points) < 2 and c["file"] not in shown_points
                    and not c["file"].endswith(("README.md", "todo.md"))):
                pts = points.setdefault(c["file"], keypoints(d / c["file"]))
                if pts:
                    shown_points.add(c["file"])
                    emit("    要点/key points:")
                    emit("\n".join("    " + x.strip() for x in pts))
            shown = {x.strip() for x in points.get(c["file"], [])} if c["file"] in shown_points else set()
            for i, l in [x for x in lines if x[1] not in shown][:2]:
                emit(f"    {i}: {l[:160]}")
    todos = []
    for s in chosen:
        f = d / s / "todo.md"
        if f.exists():
            for line in f.read_text().splitlines():
                item = re.match(r"^\s*- \[ \]\s*(.*)", line)
                if item and (broad or any(hits(item[1], g) for g in groups)):
                    todos.append(f"- {s}: {item[1][:140]}")
    if todos:
        emit(f"\n## open todo ({len(todos)})")
        emit("\n".join(todos[:10]) + ("\n…" if len(todos) > 10 else ""))
    for s in chosen:
        readme = d / s / "README.md"
        if readme.exists():
            emit(f"\n## {s}/README.md (summary; full: `show {s}`)")
            summary = readme_summary(readme.read_text(), a.readme_lines, None if broad else groups)
            if summary:
                emit(summary)
    history_paths = chosen if broad else sorted({c["file"] for _, c, _ in res})
    log = git("log", "-6" if broad else "-48", "--date=short", "--pretty=format:- %ad %s", "--", *history_paths, cwd=d, check=False).stdout.strip()
    if not broad:
        log = "\n".join([line for line in log.splitlines() if any(hits(line, g) for g in groups)][:6])
    if log:
        emit("\n## recent changes")
        emit(log)
    text = "\n".join(out)
    if len(text) > a.max:
        suffix = f"\n…(truncated at {a.max} chars; use show/search for more)"
        prefix = text[:a.max - len(suffix)]
        text = (prefix.rsplit("\n", 1)[0] if "\n" in prefix else prefix) + suffix
    print(text)
    return 0


UPDATED = re.compile(r"(?:更新|Updated)[：:]\s*(\d{4}-\d{2}-\d{2})", re.I)
# router-style LAN addresses (x.x.x.1 / .254) repeat at every site, so as a glossary alias they tie unrelated places together
GATEWAY_IP = re.compile(r"(10|192\.168|172\.(1[6-9]|2\d|3[01]))(\.\d{1,3}){1,2}\.(1|254)")


def cmd_lint(a) -> int:
    d = need_repo()
    warns: list[str] = []
    today = dt.date.today()
    listed = {n for n, desc in sections(d) if "not listed" not in desc}
    for p in sorted(d.iterdir()):
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".") and (p / "README.md").exists():
            if p.name not in listed:
                warns.append(f"{p.name}/: not in the root README table")
            if not (p / "todo.md").exists():
                warns.append(f"{p.name}/: no todo.md")
    for p in md_files(d):
        rel = str(p.relative_to(d))
        text = p.read_text(errors="ignore")
        lines = text.splitlines()
        is_section_readme = rel.count("/") == 1 and p.name == "README.md"
        is_article = rel.count("/") >= 1 and p.name not in ("README.md", "todo.md")
        if is_section_readme and len(lines) > README_MAX_LINES:
            warns.append(f"{rel}: {len(lines)} lines — keep a section README within ~{README_MAX_LINES} (move detail into articles)")
        if is_article:
            head = "\n".join(lines[:8])
            m = UPDATED.search(head)
            if not m:
                warns.append(f"{rel}: no `> 更新：YYYY-MM-DD · 状态：…` (or `> Updated: YYYY-MM-DD · Status: …`) line near the top")
            if not any(KEYPOINTS.match(l.strip()) for l in lines[:8]):
                warns.append(f"{rel}: no **要点** / **Key points** (2–4 bullet summary) near the top")
            if m:
                age = (today - dt.date.fromisoformat(m.group(1))).days
                if age > STALE_DAYS and not re.search(r"已废弃|deprecated|obsolete", head, re.I):
                    warns.append(f"{rel}: last updated {age} days ago — re-verify or mark 已废弃 / move to archive/")
        if p.name == "todo.md":
            n_open = len(re.findall(r"^\s*- \[ \]", text, re.M))
            n_done = len(re.findall(r"^\s*- \[x\]", text, re.M | re.I))
            if n_open > TODO_MAX_OPEN:
                warns.append(f"{rel}: {n_open} open items — prioritise or split")
            if n_done > 5:
                warns.append(f"{rel}: {n_done} done items — delete them (git history keeps them)")
        for m in re.finditer(r"\]\(([^)#\s]+)(#[^)]*)?\)", text):
            target = m.group(1)
            if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
                continue
            if not (p.parent / target).exists():
                warns.append(f"{rel}: broken link → {target}")
    for g in load_glossary(d):
        for alias in g:
            if GATEWAY_IP.fullmatch(alias):
                warns.append(f"glossary.md: router address `{alias}` as an alias of {g[0]} — every site has one; "
                             "mention it in the description instead")
    if bwu_bin() and vault_unlocked():
        if check_pointers() != 0:
            warns.append("some bw: pointers do not resolve (see above)")
    if warns:
        print("\n".join("WARN " + w for w in warns))
        print(f"\n{len(warns)} warning(s)")
    else:
        print("lint: clean")
    return 1 if (warns and a.strict) else 0


def cmd_todo(a) -> int:
    d = need_repo()
    names = [a.section] if a.section else [n for n, _ in sections(d)]
    total = 0
    for name in names:
        f = d / name / "todo.md"
        if not f.exists():
            continue
        items = [(i + 1, l.strip()) for i, l in enumerate(f.read_text().splitlines()) if re.match(r"^\s*- \[ \]", l)]
        if items:
            print(f"## {name} ({len(items)})")
            for n, l in items:
                print(f"  {name}/todo.md:{n}  {l[6:][:150]}")
            total += len(items)
    print(f"\n{total} open item(s)")
    return 0


def cmd_new(a) -> int:
    d = need_repo()
    name = a.name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        sys.exit("section name: lowercase letters, digits, dashes")
    sec = d / name
    if sec.exists():
        sys.exit(f"section already exists: {name}")
    today = dt.date.today().isoformat()
    zh = bool(CJK.search((d / "README.md").read_text() if (d / "README.md").exists() else "")) or bool(CJK.search(a.title + a.desc))
    sec.mkdir()
    if zh:
        (sec / "README.md").write_text(
            f"# {a.title} 专栏\n\n{a.desc}。最后更新：{today}。\n\n## 关键事实\n\n| 项目 | 内容 |\n|---|---|\n\n"
            f"## 文章\n\n- [todo.md](todo.md) — 待办\n\n## 出问题先看这里\n\n| 现象 | 处理 |\n|---|---|\n")
        (sec / "todo.md").write_text(f"# {a.title} 待办\n\n更新：{today}\n\n")
    else:
        (sec / "README.md").write_text(
            f"# {a.title}\n\n{a.desc}. Last updated: {today}.\n\n## Key facts\n\n| Item | Value |\n|---|---|\n\n"
            f"## Articles\n\n- [todo.md](todo.md) — todo\n\n## Troubleshooting\n\n| Symptom | Fix |\n|---|---|\n")
        (sec / "todo.md").write_text(f"# {a.title} todo\n\nUpdated: {today}\n\n")
    readme = d / "README.md"
    text = readme.read_text()
    row = f"| [{a.title}]({name}/README.md) | {a.desc} |"
    lines = text.splitlines()
    idx = max((i for i, l in enumerate(lines) if re.match(r"\|\s*\[.+\]\([\w.-]+/README\.md\)", l)), default=None)
    if idx is None:   # an empty table: insert under its |---|---| separator
        idx = next((i for i, l in enumerate(lines) if re.match(r"\|\s*-+\s*\|\s*-+\s*\|", l)
                    and i and re.match(r"\|\s*(专栏|Section)", lines[i - 1])), None)
    if idx is None:
        lines += ["", "| 专栏 | 内容 |" if zh else "| Section | What |", "|---|---|", row]
    else:
        lines.insert(idx + 1, row)
    readme.write_text("\n".join(lines) + "\n")
    agents = d / "AGENTS.md"
    if agents.exists():
        at = agents.read_text().splitlines()
        j = max((i for i, l in enumerate(at) if re.match(r"- `[\w.-]+/` — ", l)), default=None)
        if j is None:     # first section: append under the "## Sections" / "## 专栏" heading, else at the end
            h = next((i for i, l in enumerate(at) if re.match(r"##\s+(Sections|专栏)\s*$", l)), None)
            j = len(at) - 1 if h is None else next((i - 1 for i in range(h + 1, len(at)) if at[i].startswith("## ")), len(at) - 1)
        at.insert(j + 1, f"- `{name}/` — {a.desc}")
        agents.write_text("\n".join(at) + "\n")
    print(f"created {name}/README.md, {name}/todo.md; listed in README.md" + (" and AGENTS.md" if agents.exists() else ""))
    return 0


def cmd_check(a) -> int:
    d = need_repo()
    if a.staged:
        known = load_known_secrets()
        problems = [f"{name}:{n}: {what}" for name, text in staged_blobs(d) for n, what in scan_text(text, known)]
    else:
        paths = [Path(p) if Path(p).is_absolute() else d / p for p in a.paths] if a.paths else [p for p in d.rglob("*") if ".git" not in p.parts and "__pycache__" not in p.parts]
        problems = scan_paths(paths)
    if problems:
        print("possible secrets (write where the secret lives instead; add `memory-use: allow` on a line only if it is a false positive):")
        print("\n".join("  " + p for p in problems))
        return 1
    print("no secrets found")
    return 0


def cmd_save(a) -> int:
    d = need_repo()
    if not a.paths:
        sys.exit("save needs explicit paths (never `git add -A`: other sessions may have uncommitted work here)")
    staged_del = set(git("diff", "--cached", "--name-only", "--diff-filter=D", cwd=d).stdout.split("\n"))
    to_add = []
    for path in a.paths:
        if (d / path).exists() or git("ls-files", "--", path, cwd=d).stdout.strip():
            to_add.append(path)
        elif not any(f == path or f.startswith(path.rstrip("/") + "/") for f in staged_del):
            sys.exit(f"no such path: {path}")
    if to_add:
        add = git("add", "-A", "--", *to_add, cwd=d, check=False)      # -A: deletions under the paths too
        if add.returncode:
            sys.exit("git add failed: " + add.stderr.strip())
    known = load_known_secrets()
    problems = [f"{name}:{n}: {what}" for name, text in staged_blobs(d) for n, what in scan_text(text, known)]
    if problems:
        if to_add:
            git("reset", "-q", "--", *to_add, cwd=d, check=False)
        print("refusing to commit, possible secrets:\n" + "\n".join("  " + p for p in problems))
        return 1
    if not git("diff", "--cached", "--name-only", "--", *a.paths, cwd=d).stdout.strip():
        print("nothing to commit for those paths")
        return 0
    msg = a.message + (f"\n\n{a.trailer}" if a.trailer else "")
    c = git("commit", "-q", "-m", msg, "--", *a.paths, cwd=d, check=False)   # only these paths, never others' staged work
    if c.returncode:
        print(c.stderr or c.stdout)
        return c.returncode
    git("fetch", "-q", cwd=d, check=False)
    behind = git("rev-list", "--count", "HEAD..@{u}", cwd=d, check=False).stdout.strip()
    if behind not in ("", "0"):   # only then: autostash does not restore what other sessions had staged
        dirty = git("status", "--porcelain", cwd=d).stdout.strip()
        pull = git("pull", "-q", "--rebase", *(["--autostash"] if dirty else []), cwd=d, check=False)
        if pull.returncode:
            print("committed locally, but pull --rebase failed:\n" + (pull.stderr or pull.stdout))
            return pull.returncode
    push = git("push", "-q", cwd=d, check=False)
    if push.returncode:
        print("committed locally, push failed:\n" + (push.stderr or push.stdout))
        return push.returncode
    print("pushed " + git("log", "--oneline", "-1", cwd=d).stdout.strip())
    return 0


def cmd_log(a) -> int:
    d = need_repo()
    print(git("log", f"-{a.n}", "--date=short", "--pretty=format:%h %ad %s", cwd=d).stdout)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="memory_use.py", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"memory-use {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="set up the notes repo on this computer: clone (or --create), hook, skill links, autosync")
    s.add_argument("--repo", help="owner/name on GitHub (default: <gh user>/personal-memory)")
    s.add_argument("--dir", help="where to keep the checkout (default ~/github.com/<name>)")
    s.add_argument("--create", action="store_true", help="create a new private repo from the starter template if it does not exist")
    s.add_argument("--no-autosync", action="store_true"); s.add_argument("--every", type=int, default=15, help="autosync interval, minutes")
    s.set_defaults(fn=cmd_init)
    sub.add_parser("path", help="print the repo path").set_defaults(fn=cmd_path)
    sub.add_parser("clone", help="clone the configured repo if missing").set_defaults(fn=cmd_clone)
    sub.add_parser("doctor", help="check config, repo, sync state, hook, skill install, autosync").set_defaults(fn=cmd_doctor)
    sub.add_parser("sync", help="pull latest (rebase, autostash if dirty) and push local commits").set_defaults(fn=cmd_sync)
    s = sub.add_parser("autosync", help="background pull/push on a timer (launchd / systemd / cron / Task Scheduler): on | off | status | run")
    s.add_argument("action", choices=["on", "off", "status", "run"]); s.add_argument("--every", type=int, default=15, help="minutes")
    s.set_defaults(fn=cmd_autosync)
    s = sub.add_parser("migrate", help="before leaving this computer: what is not on GitHub yet, and the one-liner for the new one")
    s.add_argument("--push", action="store_true", help="first push local commits (same as one autosync run)")
    s.set_defaults(fn=cmd_migrate)
    s = sub.add_parser("hook", help="Claude Code hook entry points: prompt (UserPromptSubmit, JSON on stdin) | session (SessionStart)")
    s.add_argument("event", choices=["prompt", "session"]); s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("upgrade", help="update this skill checkout (and the Claude Code plugin, if installed)")
    s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_upgrade)
    sub.add_parser("ls", help="list sections with open todo counts").set_defaults(fn=cmd_ls)
    s = sub.add_parser("show", help="print a section README or a file"); s.add_argument("target"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("search", help="ranked section search; glossary aliases expand each term")
    s.add_argument("terms", nargs="+"); s.add_argument("--limit", type=int, default=8); s.add_argument("--all", action="store_true", help="include archive/")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("brief", help="compact context pack for a topic (section summaries, best matches, todos, recent changes)")
    s.add_argument("topic", nargs="*"); s.add_argument("--max", type=int, default=6000, help="max characters (default 6000 ≈ 2k tokens)")
    s.add_argument("--readme-lines", type=int, default=25, help="max lines per section README summary")
    s.set_defaults(fn=cmd_brief)
    s = sub.add_parser("lint", help="structure check: README length, 要点/更新 headers, stale notes, todo size, broken links")
    s.add_argument("--strict", action="store_true"); s.set_defaults(fn=cmd_lint)
    s = sub.add_parser("todo", help="open todo items across sections"); s.add_argument("section", nargs="?"); s.set_defaults(fn=cmd_todo)
    s = sub.add_parser("new", help="scaffold a new section"); s.add_argument("name"); s.add_argument("title"); s.add_argument("desc"); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("check", help="scan for secrets"); s.add_argument("paths", nargs="*"); s.add_argument("--staged", action="store_true"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("save", help="secret-scan, commit given paths, pull --rebase, push")
    s.add_argument("paths", nargs="+"); s.add_argument("-m", "--message", required=True); s.add_argument("--trailer", default=os.environ.get("MEMORY_USE_TRAILER", ""))
    s.set_defaults(fn=cmd_save)
    s = sub.add_parser("secret", help="Bitwarden pointers: find | put | get | refs | hashes (values never printed by default)")
    s.add_argument("action", choices=["find", "put", "get", "refs", "hashes"])
    s.add_argument("args", nargs="*")
    s.add_argument("--field", default="password")
    s.add_argument("--generate", type=int, metavar="N", help="put: generate an N-char password instead of reading stdin")
    s.add_argument("--copy", action="store_true", help="get: copy to clipboard, cleared after 30 s")
    s.add_argument("--env", metavar="VAR", help="get: run the command after -- with the secret in $VAR")
    s.add_argument("--print", action="store_true", help="get: print it (only when the user asked to see it)")
    s.set_defaults(fn=cmd_secret)
    s = sub.add_parser("remind", help="Apple Reminders, one-way: add a dated reminder | ls open ones")
    s.add_argument("action", choices=["add", "ls"]); s.add_argument("args", nargs="*")
    s.add_argument("--due", help="YYYY-MM-DD (alert at 09:00) or 'YYYY-MM-DD HH:MM'")
    s.add_argument("--list", help="Reminders list name (default: the default list)")
    s.add_argument("--note", help="reminder body")
    s.set_defaults(fn=cmd_remind)
    s = sub.add_parser("log", help="recent commits"); s.add_argument("n", nargs="?", type=int, default=10); s.set_defaults(fn=cmd_log)
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd: list[str] = []
    if "--" in argv:                       # `secret get X --env VAR -- command args…`
        i = argv.index("--")
        argv, cmd = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)
    if a.cmd not in ("upgrade", "autosync", "hook"):
        update_notice()
    a.cmd = cmd
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
