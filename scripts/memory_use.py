#!/usr/bin/env python3
"""memory-use: a portable, git-backed long-term memory for AI coding agents.

Stdlib only. Your notes are plain Markdown in a private git repo; this script finds that repo, keeps it
in sync (by hand or on a timer), searches it (ranked by section, with glossary aliases), builds compact
context packs, lints structure, aggregates todos, scaffolds sections, sets it up on a new computer, and
refuses to commit anything that looks like a secret.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import datetime as dt
import errno
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERSION = "0.5.1"
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
    return sorted(p for p in root.rglob("*.md")
                  if p.is_file() and not (set(p.relative_to(root).parts) & skip))


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


def search_text(text: str) -> str:
    """Compare compatibility-equivalent text without rewriting notes or paths."""
    return unicodedata.normalize("NFKC", text).lower()


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
        aliases = [search_text(a.strip().strip("`")) for a in names.split(" / ") if a.strip()]
        if aliases:
            groups.append(aliases)
    return groups


class Alias:
    """One way to spell a concept. ASCII aliases that came from the glossary, or contain a digit, match
    as whole tokens, so the alias 192.168.1.1 does not hit 192.168.1.14 and 190 does not hit 1900.
    A term typed by the user without digits keeps substring matching (rust → rustdesk); CJK always does."""
    __slots__ = ("text", "rx")

    def __init__(self, text: str, typed: bool = False):
        text = search_text(text)
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
            phrase = search_text(" ".join(terms[i:i + n]))
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
        t = search_text(t)
        concepts = [g for g in glossary if t in g]
        alts = {Alias(a) for g in concepts for a in g} if concepts else {Alias(t, typed=True)}
        # Repeating a term or two names of the same concept must not change its weight.
        if alts not in out:
            out.append(alts)
    return out


def hits(text: str, alts: set[Alias]) -> int:
    low = search_text(text)
    return sum(a.count(low) for a in alts)


def chunks(root: Path, include_archive: bool = False) -> list[dict]:
    """Split notes into heading-scoped chunks: {file, line, path (heading trail), heading, text}."""
    out = []
    for p in md_files(root, include_archive):
        rel = p.relative_to(root).as_posix()
        if rel == "evals.md":
            continue    # the eval questions, word for word: ranking them would answer every case with the test itself
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


SEE_ARTICLE = re.compile(r"(?:见|see)\s*`?([\w./-]+\.md)", re.I)


def glossary_pointers(root: Path) -> dict[str, list[str]]:
    """alias → the articles its concept's description points at (`… — 家里的台式机，见 machines/leo-desktop.md`)."""
    out: dict[str, list[str]] = {}
    f = root / "glossary.md"
    for line in f.read_text().splitlines() if f.exists() else []:
        if not line.startswith("- "):
            continue
        names, _, desc = line[2:].partition(" — ")
        files = []
        for target in SEE_ARTICLE.findall(desc):
            path = (root / target).resolve()
            if path.is_file() and root.resolve() in path.parents:
                files.append(path.relative_to(root.resolve()).as_posix())
        for alias in names.split(" / "):
            if files and alias.strip():
                out.setdefault(search_text(alias.strip().strip("`")), []).extend(files)
    return out


def trouble_routes(root: Path) -> list[dict]:
    """Authored symptom -> article links, not eval questions or inferred facts."""
    routes = []
    resolved = root.resolve()
    for readme in sorted(root.glob("*/README.md")):
        if readme.parent.name in SKIP_DIRS or readme.parent.name.startswith(".") or resolved not in readme.resolve().parents:
            continue
        active, fence = False, None
        for number, line in enumerate(readme.read_text(errors="ignore").splitlines(), 1):
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if marker:
                token, tail = marker.groups()
                if fence is None:
                    fence = (token[0], len(token))
                elif token[0] == fence[0] and len(token) >= fence[1] and not tail.strip():
                    fence = None
                continue
            if fence is not None:
                continue
            if re.match(r"^#{1,2}\s", line):
                active = bool(re.match(r"^##\s+(?:出问题先看这里|Troubleshooting|If something breaks)\s*$", line, re.I))
            if not active or not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: ") or cells[0].lower() in ("现象", "symptom"):
                continue
            for linked, plain in re.findall(r"\]\(([^)#\s]+\.md)|(?<![\w/\\:.(])([\w.-]+\.md)\b", cells[1]):
                target = (readme.parent / (linked or plain)).resolve()
                if not target.is_file() or resolved not in target.parents:
                    continue
                relative = target.relative_to(resolved)
                if target.name == "README.md" or relative.as_posix() == "evals.md" or set(relative.parts) & SKIP_DIRS:
                    continue
                routes.append({"condition": cells[0].replace("`", ""), "target": relative.as_posix(),
                               "source": readme.relative_to(root).as_posix(), "line": number})
                break
    return routes


NEGATION = re.compile(r"[不没未无勿]|\b(?:not|never|cannot|without|no)\b", re.I)


def symptom_matches(terms: list[str], condition: str, glossary: list[list[str]]) -> bool:
    """Conservative literal routing: all entities and ordered CJK characters must be supported."""
    query = search_text(" ".join(terms)).strip(" ?？!！。")
    query = re.sub(r"^(?:请问|为什么|如何|怎么)\s*", "", query)
    query = re.sub(r"(?:怎么办|怎么处理|怎么解决|怎么排查)$", "", query).strip()
    condition = search_text(condition)
    if NEGATION.findall(query) != NEGATION.findall(condition):
        return False
    if query == condition:
        return True
    if len(re.findall(r"[\u3400-\u9fff]", query)) < 4:
        return False
    for group in expand(query.split(), glossary):
        if any(Alias(alias.text).count(condition) for alias in group):
            continue
        # A named concept that does not occur under any alias is a different
        # entity; never weaken it into character fragments.
        if len(group) != 1:
            return False
        text = next(iter(group)).text
        if text.isascii() or re.search(r"[a-z][a-z0-9+.-]*://|[/\\]|[a-z0-9][a-z0-9_-]*\.[a-z0-9]", text):
            return False  # do not rebuild an unmatched identifier or URL from fragments
        words = re.findall(r"[a-z0-9][a-z0-9_.-]*", text)
        if not all(Alias(word).count(condition) for word in words):
            return False
        runs = re.findall(r"[\u3400-\u9fff]+", text)
        if any(run[:2] not in condition for run in runs):
            return False  # retain a contiguous anchor rather than matching dispersed letters
        chars = "".join(runs)
        if not chars:
            return False
        available = iter("".join(re.findall(r"[\u3400-\u9fff]", condition)))
        if not all(any(want == actual for actual in available) for want in chars):
            return False
    # Recognize registered names even inside an unspaced Chinese question.
    # A one-alias concept is still an entity, not a sequence of loose characters.
    for concept in glossary:
        if any(Alias(alias).count(query) for alias in concept) and not any(Alias(alias).count(condition) for alias in concept):
            return False
    return True


def rank(root: Path, terms: list[str], include_archive: bool = False, top: int | None = 8) -> list[tuple[float, dict, list[tuple[int, str]]]]:
    glossary = load_glossary(root)
    groups = expand(terms, glossary)
    if not groups:
        return []
    pointers = glossary_pointers(root)
    pointed_groups = [{f for a in g for f in pointers.get(a.text, ())} for g in groups]
    pointed = set().union(*pointed_groups)
    symptoms = {}
    for route in trouble_routes(root):
        if symptom_matches(terms, route["condition"], glossary):
            symptoms.setdefault(route["target"], route)
    cs = chunks(root, include_archive)
    # A pointer names an article, not every section in it. Give its opening chunk
    # the trigger even when the author used different words in the article itself.
    openings = {}
    for c in cs:
        if c["heading"] or c["text"].strip():
            openings.setdefault(c["file"], c["line"])
    n = len(cs) or 1
    counts = [[(hits(c["heading"], g), hits(c["text"], g),
                hits(re.sub(r"\.md$", "", c["file"]), g)) for g in groups] for c in cs]
    df = [sum(bool(row[i][0] or row[i][1]) for row in counts) for i in range(len(groups))]
    scored = []
    for c, row in zip(cs, counts):
        per = []
        named = False
        linked = False
        routed = c["file"] in symptoms and c["line"] == openings.get(c["file"])
        for (hh, bh, fh), d, targets in zip(row, df, pointed_groups):
            named |= bool(fh)
            h = 3 * hh + 3 * fh + bh
            if (c["file"] in targets and c["line"] == openings.get(c["file"])) or routed:
                h = max(h, 3)
                linked |= c["file"] in targets
            per.append(h * math.log(1 + n / (1 + d)) if h else 0.0)
        if not any(per):
            continue
        matched = sum(1 for x in per if x)
        score = sum(math.log(1 + x) for x in per) * (matched / len(groups)) ** 2
        if c["file"].endswith("todo.md"):
            score *= 0.8
        if named and " › " not in c["path"]:
            score *= 2            # a whole article named after the topic beats a section that mentions it
        if c["file"] in pointed:
            score *= 2            # the glossary says "见 this article" for the concept asked about
        if routed:
            score *= 2            # an authored troubleshooting row explicitly names this article
        if c["file"] == "glossary.md":
            score *= 0.2          # the glossary names everything; it should not crowd out the notes
        c["matched"] = matched
        c["via_glossary"] = linked
        if routed:
            c["via_symptom"] = symptoms[c["file"]]
        lines = [(i, l.strip()) for i, l in c["lines"] if any(hits(l, g) for g in groups)][:3]
        if not lines:
            # A title/filename (or authored pointer) can match without the body
            # repeating it. Return source text instead of a content-free entry.
            lines = [(i, l.strip()) for i, l in c["lines"] if l.strip()
                     and not KEYPOINTS.match(l.strip())
                     and not re.match(r"^>\s*(?:更新|Updated|状态|Status|复核|Review|冲突|Conflicts)[：:]", l.strip(), re.I)][:3]
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


def single_line_secret(text: str) -> str:
    """Accept one optional terminal line ending, never discard additional lines."""
    value = text[:-2] if text.endswith("\r\n") else text[:-1] if text.endswith("\n") else text
    if not value:
        sys.exit("no value on stdin")
    if "\n" in value or "\r" in value or re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", value):
        sys.exit("multiline secrets and private-key blocks are not supported by secret put; nothing was written. "
                 "Use `bwu item put` with a native SSH-key or secure-note JSON item instead.")
    return value


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
        if a.generate is not None:
            if a.generate < 1:
                sys.exit("--generate must be positive")
            generated = bwu("generate", str(a.generate))  # no name: generate in memory, without a vault write
            if generated.returncode:
                sys.exit("password generation failed (values hidden); nothing was written")
            value = single_line_secret(generated.stdout)
            if len(value) != a.generate:
                sys.exit("generated password length did not match; nothing was written")
        else:
            value = single_line_secret(sys.stdin.read())
        if bwu("sync").returncode:
            sys.exit("vault sync failed before writing; nothing was written")
        before = {i["id"] for i in vault_items()}
        r = bwu_confirmed("add", name, *([user] if user else []), "--folder", SECRET_FOLDER, stdin=value + "\n")
        uncertain = ("vault write was not verified; it may already exist. Inspect the named item before retrying. "
                     "No pointer was reported (values hidden).")
        if r.returncode:
            sys.exit(uncertain)
        if bwu("sync").returncode:
            sys.exit(uncertain)
        try:
            after = vault_items()
        except SystemExit:
            sys.exit(uncertain)
        new = [i for i in after if i["id"] not in before and i.get("name") == name
               and (i.get("folder") or "") == SECRET_FOLDER and (user is None or i.get("user") == user)]
        if len(new) != 1:
            sys.exit(uncertain)
        readback = bwu("get", new[0]["id"], "--field", "password", "--reveal")
        # The CLI adds one output line ending. Compare exact UTF-8 bytes in
        # memory, without exposing either value or automatically replaying writes.
        import hmac
        actual = readback.stdout[:-2] if readback.stdout.endswith("\r\n") else readback.stdout[:-1] if readback.stdout.endswith("\n") else readback.stdout
        if readback.returncode or not hmac.compare_digest(actual.encode(), value.encode()):
            sys.exit(uncertain)
        print("stored in Bitwarden folder '" + SECRET_FOLDER + "' and read-back verified. Put this in the note:\n  🔑 " + pointer(new[0]))
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
    refs = [(p.relative_to(d).as_posix(), n, m.group(1)) for p in md_files(d) for n, l in enumerate(p.read_text(errors="ignore").splitlines(), 1) for m in POINTER.finditer(l)]
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


# ---------------------------------------------------------------- push safety: notes go only to the bound private repo


def redact(url: str) -> str:
    """Hide credentials in a URL before it is printed or written to the autosync state file."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", url)


def repo_identity(spec: str) -> str:
    """One string per repository however it is spelled: git@github.com:O/R.git, ssh://git@github.com:22/O/R and
    https://user:token@github.com/O/R/ are all github.com/o/r; a local path is file:<realpath>."""
    s = spec.strip()
    if s.startswith("file://"):
        s = s[len("file://"):]
    if s.startswith(("/", "~", ".")) or Path(os.path.expanduser(s)).exists():
        return "file:" + os.path.realpath(os.path.expanduser(s)).rstrip("/")
    m = re.match(r"^(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^/:@]+)(?::\d+)?[:/](.+?)(?:\.git)?/*$", s, re.I)
    if not m:
        return "?" + s
    host, path = m[1].lower(), m[2].strip("/")
    return f"{host}/{path.lower() if host == 'github.com' else path}"


def bound_identity() -> tuple[str, str] | None:
    """(identity, configured spec) of the notes repo `init` bound this computer to."""
    slug = repo_slug()
    if not slug:
        return None
    return (repo_identity(slug) if is_url(slug) else "github.com/" + slug.lower()), slug


def github_repo(slug: str) -> dict | None:
    """GitHub's REST view of owner/name (private, numeric id, full_name) as the gh account sees it; None when gh is
    missing, logged out, offline, or the account cannot see the repo. Tests replace this function."""
    if not shutil.which("gh"):
        return None
    r = subprocess.run(["gh", "api", f"repos/{slug}"], capture_output=True, text=True, timeout=20)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


def push_preflight(d: Path) -> tuple[str, str, str, str]:
    """Where a push of the current branch would really go, checked before anything leaves the machine:
    (remote, branch, problem, what was verified). The remote is picked the way git picks it (branch.pushRemote, then
    remote.pushDefault, then the upstream; get-url --push --all expands pushurl and insteadOf) and the push names it
    explicitly, so what is checked is what is pushed. Every
    push URL must be the bound notes repo, and on GitHub the repo must still be private with the id recorded the
    first time it was verified. Anything that can't be checked is a problem: fail closed, keep the commit local."""
    bound = bound_identity()
    if not bound:
        return "", "", "no notes repo bound on this computer; run `memory-use init --repo <owner>/<name>`", ""
    want, spec = bound
    cfg = lambda key: git("config", "--get", key, cwd=d, check=False).stdout.strip()
    here = git("symbolic-ref", "--short", "-q", "HEAD", cwd=d, check=False).stdout.strip()
    upstream = cfg(f"branch.{here}.remote") if here else ""
    remote = (cfg(f"branch.{here}.pushRemote") or cfg("remote.pushDefault") or upstream) if here else ""
    if not remote:
        return "", "", "the current branch has no upstream to push to; push it once by hand after checking the remote", ""
    merge = cfg(f"branch.{here}.merge")
    branch = merge[len("refs/heads/"):] if remote == upstream and merge.startswith("refs/heads/") else here
    urls = git("remote", "get-url", "--push", "--all", remote, cwd=d, check=False).stdout.split()
    if not urls:
        return "", "", f"remote {remote} has no URL", ""
    for u in urls:
        if repo_identity(u) != want:
            return "", "", (f"push target {redact(u)} (remote {remote}) is not the notes repo {redact(spec)}; "
                             "not pushing. Fix the remote or re-run `memory-use init`"), ""
    if not want.startswith("github.com/"):
        host = want.split("/", 1)[0] if not want.startswith("file:") else "a local path"
        return remote, branch, "", f"{remote}/{branch} → {redact(urls[0])} (visibility not verifiable on {host})"
    slug = want.split("/", 1)[1]
    info = github_repo(slug)
    if info is None:
        return "", "", (f"could not confirm github.com/{slug} is still private (gh missing or logged out, an account "
                        "that cannot see it, or offline); commit kept locally — check `gh auth status`"), ""
    if info.get("private") is not True:
        return "", "", f"github.com/{slug} is PUBLIC now; not pushing private notes. Make it private again first", ""
    if str(info.get("full_name", "")).lower() != slug:
        return "", "", f"github.com/{slug} now resolves to {info.get('full_name')} (renamed or transferred); re-run init", ""
    ids = load_config().get("repo_ids", {})
    if slug not in ids:
        save_config(repo_ids={**ids, slug: info["id"]})          # trust on first use: the id, never the identity
    elif ids[slug] != info["id"]:
        return "", "", (f"github.com/{slug} is a different repository than the one first verified (id {ids[slug]} → "
                        f"{info['id']}: deleted and recreated?); not pushing. If that is expected, remove repo_ids "
                        f"from {CONFIG}"), ""
    return remote, branch, "", f"{remote}/{branch} → github.com/{slug} (private, id {info['id']})"


def safe_push(d: Path) -> tuple[bool, str]:
    """Push the current branch to the verified target only, never with a bare `git push`."""
    remote, branch, problem, _ok = push_preflight(d)
    if problem:
        return False, problem
    p = git("push", "-q", remote, f"HEAD:refs/heads/{branch}", cwd=d, check=False)
    return (p.returncode == 0), redact((p.stderr or p.stdout).strip())


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
    if not is_url(slug):
        info = github_repo(slug)
        if info and info.get("private") is not True:
            print(f"WARNING: github.com/{slug} is public — memory-use will not push notes to it until it is private")
        if info and str(info.get("full_name", "")).lower() == slug.lower():
            save_config(repo_ids={**load_config().get("repo_ids", {}), slug.lower(): info["id"]})
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


def repository_operation_lock(d: Path):
    """Share one local lock across save and sync transactions for this checkout."""
    gd = Path(git("rev-parse", "--absolute-git-dir", cwd=d).stdout.strip())
    return local_file_lock(gd / "memory-use-operation.lock")


def repository_operation_in_progress(d: Path) -> bool:
    gd = Path(git("rev-parse", "--absolute-git-dir", cwd=d).stdout.strip())
    return any((gd / x).exists() for x in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"))


def autosync_once(d: Path) -> tuple[bool, str]:
    try:
        with repository_operation_lock(d):
            return _autosync_locked(d)
    except TimeoutError:
        return False, "skipped: another memory-use operation owns this checkout; retry later"


def _autosync_locked(d: Path) -> tuple[bool, str]:
    """fetch; fast-forward or rebase onto upstream; push local commits. Never commits, never stashes,
    never touches a repo mid-rebase/merge. Uncommitted edits (maybe another session's) are left alone."""
    if repository_operation_in_progress(d):
        return False, "skipped: a rebase/merge is in progress"
    if git("rev-parse", "--abbrev-ref", "@{u}", cwd=d, check=False).returncode:
        return False, "skipped: current branch has no upstream"
    branch = git("symbolic-ref", "--short", "HEAD", cwd=d).stdout.strip()
    remote = git("config", "--get", f"branch.{branch}.remote", cwd=d).stdout.strip()
    f = git("fetch", "-q", remote, cwd=d, check=False)
    if f.returncode:
        return False, "fetch failed (offline?): " + (f.stderr.strip().splitlines() or ["?"])[-1]
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
        ok, err = safe_push(d)
        if not ok:
            return False, "push failed: " + (err.splitlines() or ["?"])[-1]
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
        ok, msg = autosync_once(d)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STATE_FILE, json.dumps({"at": now, "ok": ok, "result": msg, "repo": str(d)}, ensure_ascii=False) + "\n")
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
    try:
        session_store(strict=True)
        line(True, "hook state: readable (or not created yet)")
    except (OSError, ValueError):
        line(False, f"hook state unreadable: {STATE_DIR / 'nudged.json'} — preserved; inspect it before moving it aside")
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
    _r, _b, problem, verified = push_preflight(d)
    line(not problem, "push target: " + (verified or problem))
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
         + (f"; last run {stt.get('at')} {'ok' if stt.get('ok') else 'WARN'} {stt.get('result')}" if stt else ""),
         warn=not getattr(_a, "no_autosync", False))
    if stt and not stt.get("ok"):
        line(False, "last autosync did not finish: " + str(stt.get("result")))
    line(KNOWN_SECRETS.exists(), f"known-secrets list (optional, outside git): {KNOWN_SECRETS}", warn=False)
    stuck = [r["about"] for r in lesson_profile() if r["stuck"]]
    line(not stuck, "notes that keep failing: " + (", ".join(stuck[:5]) + " — `memory-use lessons`" if stuck else "none"),
         warn=False)
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


ABS_PATH = re.compile(r"(?<![\w.:/~])~?/[^\s`'\")]+")


def prompt_mentions(root: Path, prompt: str, limit: int = 3) -> list[str]:
    """Glossary concepts (by first alias) and section names that a user message mentions."""
    low = search_text(prompt)
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


@contextmanager
def local_file_lock(path: Path, timeout: float = 1.0):
    """Bounded, OS-owned lock. Keep the inode; process death releases the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        # Windows locks a byte range, including when the file was just created.
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + timeout
        if os.name == "nt":
            import msvcrt
            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        while True:
            try:
                acquire()
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for {path.name}") from e
                time.sleep(0.01)
        try:
            yield
        finally:
            release()


def atomic_write_text(path: Path, text: str) -> None:
    """Readers see a complete old or new file; exceptions leave the old file intact."""
    if path.is_symlink():
        raise OSError("refusing to replace a symlink state file")
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    if path.exists() and not os.access(path, os.W_OK):
        raise PermissionError(f"state file is read-only: {path.name}")
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            os.chmod(temp, mode)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def session_store(strict: bool = False) -> dict:
    """Per-session hook memory (STATE_DIR/nudged.json): {session_id: {t, concepts, fails}}; a day's worth."""
    try:
        seen = json.loads((STATE_DIR / "nudged.json").read_text())
    except FileNotFoundError:
        seen = {}
    except (OSError, ValueError):
        if strict:
            raise
        seen = {}
    if not isinstance(seen, dict):
        if strict:
            raise ValueError("hook state is not an object")
        return {}
    now = int(time.time())
    return {k: v for k, v in seen.items() if isinstance(v, dict)
            and isinstance(v.get("t"), (int, float)) and now - v["t"] < 86400}


@contextmanager
def session_update():
    """One transaction covers read, modification and atomic replacement, not only write."""
    with ExitStack() as stack:
        try:
            stack.enter_context(local_file_lock(STATE_DIR / "nudged.lock"))
            seen = session_store(strict=True)
        except (OSError, ValueError):
            # Hook state is best-effort. On contention or corruption, do not
            # overwrite anything or block the user's business command.
            yield None
            return
        original = json.dumps(seen, sort_keys=True)
        yield seen
        if json.dumps(seen, sort_keys=True) != original:
            try:
                atomic_write_text(STATE_DIR / "nudged.json", json.dumps(seen))
            except OSError:
                pass


def not_yet_nudged(sid: str, concepts: list[str]) -> list[str]:
    """The concepts not yet pointed at in this session, recorded as pointed at now.
    MemT stores at most one experience per episode: a repeated nudge is noise, and noise costs the agent."""
    if not sid:
        return concepts
    with session_update() as seen:
        if seen is None:
            return []
        s = seen.setdefault(sid, {})
        done = s.get("concepts", [])
        new = [c for c in concepts if c not in done]
        if new:
            s.update(t=int(time.time()), concepts=done + new)
        return new


def failure_count(sid: str, cmd: str) -> int:
    """How many times this exact command has failed in this session, counting this failure."""
    if not sid:
        return 1
    with session_update() as seen:
        if seen is None:
            return 1
        s = seen.setdefault(sid, {})
        fails = s.setdefault("fails", {})
        key = " ".join(cmd.split())[:300]
        fails[key] = fails.get(key, 0) + 1
        s["t"] = int(time.time())
        return fails[key]


NOISE_WORDS = {"error", "errors", "failed", "failure", "fatal", "warning", "the", "and", "for", "with", "not", "no",
               "cannot", "could", "can't", "unable", "such", "file", "directory", "command", "found", "exit", "code",
               "usage", "try", "help", "line", "from", "this", "that", "you", "are", "was", "has", "have", "sudo"}


# runners whose name says nothing about what failed (`git push`, `python3 x.py`); the repeat signal skips them
GENERIC_PROGRAMS = {"bash", "sh", "zsh", "cd", "python", "python3", "node", "npm", "npx", "pnpm", "bun", "uv", "git",
                    "make", "cat", "echo", "printf", "ls", "grep", "rg", "find", "sed", "awk", "cut", "head", "tail",
                    "tr", "sort", "wc", "pwd", "tee", "curl", "test", "true", "false"}

LOCAL_FAILURE = re.compile(
    r"no matches found|no such file or directory|command not found|syntax error|parse error|illegal byte sequence|"
    r"invalid (?:utf-?8|multibyte|encoding)|unicodedecodeerror|"
    r"(?:^|\n)\s*(?:[^:\n]*?/)?(?:zsh|bash|sh|\(eval\))[:)][^\n]*\bnot found\b", re.I)
DIAGNOSTIC = re.compile(
    r"\b(?:error|failed|failure|fatal|refused|denied|timeout|timed out|unreachable|closed|reset|authentication|"
    r"certificate|broken pipe|lost connection|could not|couldn't|unable)\b", re.I)


def failure_operation(command: str) -> tuple[str, list[str]]:
    """Conservative first-command targets; quoted payloads and file operands are not topics."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = []
        for token in lexer:
            if token and set(token) <= set(";&|()"):
                break
            tokens.append(token)
    except ValueError:
        return "", []
    while tokens and (re.match(r"[A-Za-z_][A-Za-z0-9_]*=", tokens[0]) or tokens[0] in ("sudo", "env", "time")):
        tokens.pop(0)
    if not tokens:
        return "", []
    program = Path(tokens[0]).name.lower()
    topics = [program] if program not in GENERIC_PROGRAMS else []
    options = {
        "ssh": {"-p", "-i", "-F", "-l", "-o", "-J", "-L", "-R", "-D", "-S", "-b", "-c", "-E", "-e", "-m", "-O", "-Q", "-W", "-w", "-B"},
        "scp": {"-P", "-i", "-F", "-o", "-J", "-S", "-c", "-l"},
        "sftp": {"-P", "-i", "-F", "-o", "-J", "-S", "-b", "-B", "-D", "-l", "-R"},
        "curl": {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--json", "-H", "--header",
                 "-F", "--form", "--form-string", "-o", "--output", "-T", "--upload-file", "-e", "--referer",
                 "--resolve", "--connect-to", "--proxy", "-x", "-u", "--user", "-X", "--request", "--connect-timeout", "--max-time"},
        "wget": {"-O", "--output-document", "-o", "--output-file", "--header", "--post-data", "--post-file", "--referer"},
        "ping": {"-c", "-i", "-W", "-t", "-s", "-I"},
    }
    args, index = [], 1
    while index < len(tokens):
        token = tokens[index]
        if token in options.get(program, set()):
            index += 2
            continue
        if not token.startswith("-"):
            args.append(token)
        index += 1
    if program in ("ssh", "sftp", "ping") and args:
        host = args[0].rsplit("@", 1)[-1]
        if host.startswith("[") and "]" in host:
            host = host[1:host.index("]")]
        elif program == "sftp" and host.count(":") == 1:
            host = host.split(":", 1)[0]
        topics.append(host)
    elif program == "scp":
        for arg in args:
            remote = re.match(r"^(?:[^@/]+@)?(\[[^\]]+\]|[^:/\\]+):", arg)
            if remote and not re.match(r"^[A-Za-z]:[/\\]", arg):
                topics.append(remote[1].strip("[]"))
    elif program in ("curl", "wget"):
        from urllib.parse import urlsplit
        for arg in args:
            try:
                parsed = urlsplit(arg)
                if parsed.scheme in ("http", "https", "ftp", "sftp") and parsed.hostname:
                    topics.append(parsed.hostname)
            except ValueError:
                continue
    return program, topics


def operation_mentions(root: Path, targets: list[str]) -> list[str]:
    """Endpoints must equal an alias, rather than contain a topic inside a path or address."""
    known = {search_text(target.rstrip(".")) for target in targets}
    found = [group[0] for group in load_glossary(root) if known.intersection(group)]
    found += [name for name, _ in sections(root) if search_text(name) in known and name not in found]
    return found[:3]


def failure_terms(cmd: str, err: str) -> list[str]:
    """The distinctive words of a failed command and the start of its error: program, host, error names."""
    words: list[str] = []
    for w in re.findall(r"[A-Za-z][\w.-]{2,}|[\u3400-\u9fff]{2,}", ABS_PATH.sub(" ", cmd + "\n" + err[:500])):
        w = w.lower().strip(".-")
        if len(w) >= 3 and w not in NOISE_WORDS and w not in words:
            words.append(w)
    return words[:8]


def tool_failure_nudge(d: Path, ev: dict) -> str | None:
    """Use diagnostic output or parsed operation targets, never arbitrary command text as a topic."""
    if ev.get("is_interrupt"):
        return None
    inp = ev.get("tool_input") or {}
    cmd = str(inp.get("command") or inp.get("file_path") or "")
    if re.match(r"\s*(memory-use|python3?\s+\S*memory_use\.py)\b", cmd):
        return None
    home = Path.home()
    names = {str(d)} | ({"~/" + d.relative_to(home).as_posix()} if d.is_relative_to(home) else set())
    if any(n in cmd for n in names):
        return None                # working on the notes themselves: their text is in the output, not the problem
    sid = str(ev.get("session_id") or "")
    err = re.sub(r"^Exit code \d+\s*", "", str(ev.get("error") or ""))[:2000]
    if LOCAL_FAILURE.search(err):
        return None
    prog, targets = failure_operation(cmd)
    diagnostic = "\n".join(line for line in err.splitlines() if DIAGNOSTIC.search(line))
    times = failure_count(sid, cmd)
    found = prompt_mentions(d, ABS_PATH.sub(" ", diagnostic)) or operation_mentions(d, targets)
    if found:
        note_lesson(sid, found[0])
        new = not_yet_nudged(sid, found)
        if new:
            return (f"memory-use: that command failed and the notes know about {', '.join(new)}. Before retrying, run "
                    f"`memory-use brief {' '.join(c.split()[0] for c in new)}` — the fix may already be written down.")
        return None
    if times < 3 or len(prog) < 3 or prog in GENERIC_PROGRAMS:
        return None
    terms = failure_terms(prog, diagnostic)
    res = [r for r in rank(d, terms, top=5) if r[1]["file"] != "glossary.md"]
    if not res:
        return None
    c = res[0][1]
    if c["matched"] < 2 or not hits(c["file"] + "\n" + c["heading"] + "\n" + c["text"], {Alias(prog, typed=True)}):
        return None                # a note that shares only generic error words with the failure is not about it
    note_lesson(sid, c["file"])
    if not not_yet_nudged(sid, ["file:" + c["file"]]):
        return None
    return (f"memory-use: that command has failed {times} times. The notes mention it in {c['file']}:{c['line']} "
            f"‹{c['path'] or c['file']}› — read it (`memory-use show {c['file']}`) before trying again.")


def note_lesson(sid: str, about: str) -> None:
    """Record a topic once per session; later failures must not re-arm an answered reminder."""
    if not sid:
        return
    with session_update() as seen:
        if seen is None:
            return
        s = seen.setdefault(sid, {})
        s["t"] = int(time.time())
        lessons = s.setdefault("lessons", [])
        if not any(x["about"] == about for x in lessons):
            lessons.append({"about": about, "t": s["t"]})
            log_lesson("failed", sid, about)


def stop_reminder(d: Path, ev: dict) -> str | None:
    """Ask at most once per topic/session, including when reading older duplicate lesson records."""
    sid = str(ev.get("session_id") or "")
    if not sid or ev.get("stop_hook_active"):
        return None
    with session_update() as seen:
        if seen is None:
            return None
        session = seen.get(sid, {})
        lessons = session.get("lessons", [])
        open_ = [x for x in lessons if not x.get("saved")]
        if not open_:
            return None
        # --since is inclusive and second-grained: a commit in the failure's own second predates the lesson
        since = min(x["t"] for x in open_) + 1
        commits = [int(c) for c in git("log", f"--since=@{since}", "--format=%ct", cwd=d, check=False).stdout.split()]
        for x in open_:
            if any(c > x["t"] for c in commits):
                x["saved"] = True
                log_lesson("saved", sid, x["about"])
        asked = set(session.get("stop_asked", [])) | {x["about"] for x in lessons if x.get("asked")}
        pending = []
        for x in open_:
            if x.get("saved") or x["about"] in asked:
                continue
            pending.append(x)
            asked.add(x["about"])
        for x in open_:
            if x["about"] not in asked:
                continue
            x["asked"] = True
        session["stop_asked"] = sorted(asked)
        for x in pending:
            log_lesson("asked", sid, x["about"])
    if not pending:
        return None
    about = ", ".join(dict.fromkeys(x["about"] for x in pending))
    return (f"memory-use: earlier in this session a command failed on {about} and you were pointed at the notes. "
            "If you got past it with something the notes do not say yet (a cause, fix, flag or gotcha), write it into "
            "the matching article now, with the symptom and how you verified it, and `memory-use save` it. "
            "If nothing new was learned, or you are still stuck, just finish.")


def log_lesson(event: str, sid: str, about: str) -> None:
    """Append to STATE_DIR/lessons.jsonl, which outlives the day-long session store; `lessons` reads it."""
    f = STATE_DIR / "lessons.jsonl"
    try:
        with local_file_lock(STATE_DIR / "lessons.lock"):
            with f.open("a") as out:
                out.write(json.dumps({"t": int(time.time()), "event": event, "session": sid, "about": about}) + "\n")
            if f.stat().st_size > 512_000:
                atomic_write_text(f, "\n".join(f.read_text().splitlines()[-2000:]) + "\n")
    except OSError:
        pass


def lesson_profile() -> list[dict]:
    """Per concept or article: the sessions where a command failed on it, and those where a fix was saved after.
    MemT profiles its backbone before training (each task tried 8 times: reliably / occasionally / never solved);
    a note that is pointed at session after session while nothing gets saved is not helping."""
    rows: dict[str, dict] = {}
    try:
        lines = (STATE_DIR / "lessons.jsonl").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        r = rows.setdefault(e["about"], {"about": e["about"], "failed": set(), "saved": set(), "last": 0})
        if e["event"] in ("failed", "saved"):
            r[e["event"]].add(e["session"])
        r["last"] = max(r["last"], e["t"])
    out = sorted(rows.values(), key=lambda r: (-len(r["failed"]), -r["last"]))
    for r in out:
        r["stuck"] = len(r["failed"]) >= 2 and not r["saved"]
    return out


def cmd_lessons(_a) -> int:
    rows = lesson_profile()
    if not rows:
        print("no failures on noted topics recorded yet (the PostToolUseFailure hook records them)")
        return 0
    print(f"{'topic / note':<34} {'failed in':>9} {'fix saved':>9}  last")
    for r in rows:
        last = dt.datetime.fromtimestamp(r["last"]).strftime("%Y-%m-%d")
        print(f"{r['about'][:34]:<34} {len(r['failed']):>9} {len(r['saved']):>9}  {last}"
              + ("  ← keeps failing, nothing saved: rewrite this note" if r["stuck"] else ""))
    return 0


TASK_NOTE = re.compile(r"<task-notification>.*?(?:</task-notification>|\Z)", re.S)


def cmd_hook(a) -> int:
    """Claude Code hooks. prompt: nudge to recall when the message names something the notes know.
    tool: the same nudge when a failed command touches something the notes know (PostToolUseFailure).
    stop: after such a failure, once, ask whether the way past it belongs in the notes (Stop).
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
        if a.event == "stop":
            if (d / ".git").exists() and (msg := stop_reminder(d, json.loads(raw, strict=False))):
                print(json.dumps({"decision": "block", "reason": msg}))
            return 0
        if a.event == "tool":
            # plain stdout of a PostToolUseFailure hook only reaches the debug log; additionalContext reaches the model
            if (d / ".git").exists() and (msg := tool_failure_nudge(d, json.loads(raw, strict=False))):
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUseFailure", "additionalContext": msg}}))
            return 0
        try:
            ev = json.loads(raw, strict=False) if raw.strip().startswith("{") else {"prompt": raw}
        except ValueError:
            ev = {"prompt": raw}
        # a background agent finishing arrives as a prompt too; its report is not something the user asked about
        prompt = TASK_NOTE.sub(" ", str(ev.get("prompt") or ""))
        if not prompt.strip() or not (d / ".git").exists():
            return 0
        # an absolute path pasted into the message (/Users/me/github.com/memory-use/…) is not a topic
        hits = not_yet_nudged(str(ev.get("session_id") or ""), prompt_mentions(d, ABS_PATH.sub(" ", prompt)))
        if hits:
            print(f"memory-use: the notes know about {', '.join(hits)}. Before acting, run "
                  f"`memory-use brief {' '.join(h.split()[0] for h in hits)}` and read the matching notes.")
    except Exception:
        pass
    return 0


def cmd_sync(_a) -> int:
    d = need_repo()
    ok, result = autosync_once(d)
    print(result)
    print(git("log", "--oneline", "-1", cwd=d).stdout.strip())
    return 0 if ok else 1


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
    print(f"# {target.relative_to(d).as_posix()}\n")
    text = target.read_text()
    warning = note_warning(text)
    if warning:
        print(f"WARN {warning}\n")
    print(text)
    return 0


def cmd_search(a) -> int:
    d = need_repo()
    res = rank(d, a.terms, a.all, a.limit)
    if not res:
        print("no match — try fewer words, or an alias (see glossary.md)")
        return 1
    for score, c, lines in res:
        via = " [symptom link]" if c.get("via_symptom") else " [glossary link]" if c.get("via_glossary") else ""
        print(f"[{score:4.1f}] {c['file']}:{c['line']}  ‹{c['path'] or c['file']}›{via}")
        if c.get("via_symptom"):
            route = c["via_symptom"]
            print(f"        trigger: {route['source']}:{route['line']} ‹{route['condition'][:120]}›")
        warning = note_warning((d / c["file"]).read_text(errors="ignore"))
        if warning:
            print(f"        WARN {warning}")
        for i, l in lines:
            print(f"        {i}: {l[:150]}")
    return 0


# Backticks delimit filenames containing commas; bare paths may contain spaces.
EVAL_TARGET = r"(?:`[^`\n]+\.md`|[^`,\n]+?\.md)"
EVAL_LINE = re.compile(rf"^\s*-\s+(.+?)\s*(?:→|->)\s*({EVAL_TARGET}(?:\s*,\s*{EVAL_TARGET})*)\s*$")


def eval_cases(root: Path) -> list[tuple[str, str, str, str]]:
    """(source, query, expected file, file to leave out of the ranking: the README a symptom was taken from). evals.md: `- query words → section/article.md`, written by hand from real
    questions. glossary: a concept whose description says 见/see an article. trouble: a symptom in a section README's
    troubleshooting table whose fix cell links an article (MemT's trigger context → experience). title: an H1."""
    cases: list[tuple[str, str, str, str]] = []
    f = root / "evals.md"
    if f.exists():
        cases += [("evals.md", m[1], m[2], "") for m in map(EVAL_LINE.match, f.read_text().splitlines()) if m]
    g = root / "glossary.md"
    if g.exists():
        for line in g.read_text().splitlines():
            m = SEE_ARTICLE.search(line)
            if line.startswith("- ") and m and (root / m[1]).exists():
                names = re.split(r"\s+—\s+", line[2:], maxsplit=1)[0]
                cases.append(("glossary", names.split(" / ")[0].strip().strip("`"), m[1], ""))   # every alias ranks alike
    cases += [("trouble", row["condition"], row["target"], row["source"]) for row in trouble_routes(root)]
    for p in md_files(root):
        rel = p.relative_to(root).as_posix()
        if "/" in rel and p.name not in ("README.md", "todo.md"):
            title = next((l[2:].strip() for l in p.read_text(errors="ignore").splitlines() if l.startswith("# ")), "")
            if title:
                cases.append(("title", title, rel, ""))
    return cases


def cmd_eval(a) -> int:
    """Measure file ranking and whether the actual budgeted brief retains the expected note."""
    d = need_repo()
    if a.max < 128:
        sys.exit("--max must be at least 128")
    if a.cases:
        path = Path(a.cases).expanduser().resolve()
        if d.resolve() in path.parents and path != (d / "evals.md").resolve():
            sys.exit("--cases must be outside the notes repo (or its excluded evals.md), so questions cannot answer themselves")
        try:
            lines = path.read_text().splitlines()
        except OSError as e:
            sys.exit(f"cannot read cases: {e}")
        cases = [("manual", m[1], m[2], "") for m in map(EVAL_LINE.match, lines) if m]
    else:
        cases = eval_cases(d)
    if not cases:
        message = "no eval cases — add `- <question words> → <section>/<article>.md` lines to evals.md"
        print(json.dumps({"error": message, "cases": [], "summary": {}}, ensure_ascii=False) if a.json else message)
        return 1
    report = evaluate_recall(d, cases, a.max)
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["summary"]["all"]["invalid"] else 0
    print(f"{'source':<10} {'cases':>5} {'hit@1':>6} {'hit@3':>6} {'MRR':>5} {'brief':>6} {'invalid':>7}")
    for source, s in report["summary"].items():
        print(f"{source:<10} {s['cases']:>5} {s['hit_at_1']:>6.0%} {s['hit_at_3']:>6.0%} "
              f"{s['mrr']:>5.2f} {s['brief_present']:>6.0%} {s['invalid']:>7}")
    if "multi_evidence" in report:
        m = report["multi_evidence"]
        print(f"\nmulti-evidence ({m['cases']} valid questions): recall@3 {m['recall_at_3']:.0%}; "
              f"brief evidence {m['brief_evidence_recall']:.0%}")
    print(f"\nbrief: expected note has a complete entry and an excerpt within {a.max} characters; not answer correctness")
    misses = [c for c in report["cases"] if c["rank"] != 1 or c["brief_status"] != "present"]
    if misses and a.misses:
        print(f"\nrecall gaps ({len(misses)}):")
        for c in misses[:a.misses]:
            print(f"  [{c['source']}] {c['query']!r} → {c['expected']} at {c['rank'] or 'miss'}; "
                  f"brief: {c['brief_status']}; top: {', '.join(c['top']) or '-'}")
    return 1 if report["summary"]["all"]["invalid"] else 0


BRIEF_ENTRY = re.compile(r"^- (.+\.md):\d+ ‹.*›(?: \[(?:glossary|symptom) link\])?$")


def brief_sources(text: str) -> dict[str, list[str]]:
    """Source excerpts only: routing conditions, warnings and labels are not answers."""
    sources: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        entry = BRIEF_ENTRY.match(line)
        if entry:
            current = entry[1]
        elif line.startswith("    ") and current:
            content = line.strip()
            if content and not content.startswith(("WARN ", "trigger: ", "要点/key points:")):
                sources.setdefault(current, []).append(content)
        elif line.strip():
            current = None
    return sources


def brief_files(text: str) -> set[str]:
    """Only count complete note entries with source text, not a path cut off by the budget."""
    return set(brief_sources(text))


def evaluate_recall(root: Path, cases: list[tuple[str, str, str, str]], max_chars: int = 6000) -> dict:
    """Use the production renderer; distinguish retrieval, selection, and budget losses."""
    records = []
    searchable = {p.relative_to(root).as_posix() for p in md_files(root)} - {"evals.md"}
    for source, query, want, skip in cases:
        # Multiple required files form one question, not multiple independent hits.
        wants = ([want] if source in {"title", "glossary", "trouble"} else
                 [item.strip().strip('`') for item in re.findall(r"`[^`]+`|[^`,]+", want) if item.strip()])
        if len(wants) > 1:
            # All evidence shares one ranking and one actual budgeted brief.
            files = []
            ranked = rank(root, query.split(), top=None)
            for _s, c, _l in ranked:
                if c["file"] not in files and c["file"] not in ("glossary.md", skip):
                    files.append(c["file"])
            selected = {c["file"] for _, c, _ in brief_matches(ranked[:48])}
            shown = brief_files(render_brief(root, query.split(), max_chars) or "")
            evidence = []
            seen_targets = set()
            for item in wants:
                target = (root / item).resolve()
                if target in seen_targets:
                    continue
                seen_targets.add(target)
                expected = target.relative_to(root.resolve()).as_posix() if root.resolve() in target.parents else item
                valid_target = expected in searchable and target.is_file() and root.resolve() in target.parents
                pos = files.index(expected) + 1 if valid_target and expected in files else 0
                status = ("invalid-target" if not valid_target else "not-retrieved" if not pos else
                          "filtered" if expected not in selected else
                          "present" if expected in shown else "budget-or-no-excerpt")
                evidence.append({"expected": item, "rank": pos, "brief_status": status, "valid": valid_target})
            valid = all(item["valid"] for item in evidence)
            ranks = [item["rank"] for item in evidence if item["rank"]]
            present = sum(item["brief_status"] == "present" for item in evidence)
            records.append({"source": source, "query": query, "expected": want,
                            "rank": min(ranks, default=0),
                            "brief_status": "invalid-target" if not valid else
                                ("present" if present == len(evidence) else "partial"),
                            "top": files[:3], "valid": valid,
                            "evidence": evidence,
                            "recall_at_3": sum(0 < item["rank"] <= 3 for item in evidence) / len(evidence),
                            "brief_evidence_recall": present / len(evidence)})
            continue
        want = wants[0]
        target = (root / want).resolve()
        expected = target.relative_to(root.resolve()).as_posix() if root.resolve() in target.parents else want
        valid = expected in searchable and target.is_file() and root.resolve() in target.parents
        files: list[str] = []
        # Rank all chunks, then dedupe files. Twenty chunks from one long note
        # must not turn the second matching file into a false miss.
        for _s, c, _l in rank(root, query.split(), top=None) if valid else []:
            if c["file"] not in files and c["file"] not in ("glossary.md", skip):
                files.append(c["file"])
        pos = files.index(expected) + 1 if expected in files else 0
        if not valid:
            status = "invalid-target"
        elif not pos:
            status = "not-retrieved"
        else:
            selected = brief_matches(rank(root, query.split(), top=48))
            if expected not in {c["file"] for _, c, _ in selected}:
                status = "filtered"
            else:
                text = render_brief(root, query.split(), max_chars)
                status = "present" if expected in brief_files(text or "") else "budget-or-no-excerpt"
        records.append({"source": source, "query": query, "expected": want, "rank": pos,
                        "brief_status": status, "top": files[:3], "valid": valid})
    summary = {}
    for source in dict.fromkeys([c["source"] for c in records] + ["all"]):
        rows = [c for c in records if source == "all" or c["source"] == source]
        valid = [c for c in rows if c["valid"]]
        n = len(valid) or 1
        summary[source] = {"cases": len(rows), "valid_cases": len(valid), "invalid": len(rows) - len(valid),
                           "hit_at_1": sum(c["rank"] == 1 for c in valid) / n,
                           "hit_at_3": sum(0 < c["rank"] <= 3 for c in valid) / n,
                           "mrr": sum(1 / c["rank"] for c in valid if c["rank"]) / n,
                           "brief_present": sum(c["brief_status"] == "present" for c in valid) / n}
    report = {"max_chars": max_chars, "summary": summary, "cases": records}
    multi = [c for c in records if "evidence" in c and c["valid"]]
    if multi:
        report["multi_evidence"] = {"cases": len(multi),
            "recall_at_3": sum(c["recall_at_3"] for c in multi) / len(multi),
            "brief_evidence_recall": sum(c["brief_evidence_recall"] for c in multi) / len(multi)}
    return report


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


def pack_brief(text: str, max_chars: int) -> str:
    """Pack usable source entries before background, preserving a hard character cap."""
    if len(text) <= max_chars:
        return text
    suffix = f"\n…(truncated at {max_chars} chars; use show/search for more)"
    limit = max_chars - len(suffix)
    lines = text.splitlines()
    try:
        start = lines.index("## best matching notes")
    except ValueError:
        # Section-only overviews have no source entries to reserve space for.
        prefix = text[:limit]
        return (prefix.rsplit("\n", 1)[0] if "\n" in prefix else prefix) + suffix
    head = lines[:start + 1]
    if len("\n".join(head)) > limit:
        head = [head[0], "", head[-1]]
    if len("\n".join(head)) > limit:
        room = max(1, limit - len(head[-1]) - 3)
        head[0] = head[0][:room - 1] + "…"
    out = "\n".join(head)
    entries, tail = [], []
    current = None
    for index, line in enumerate(lines[start + 1:], start + 1):
        if line.startswith("## "):
            if current is not None:
                entries.append(current)
                current = None
            tail = lines[index:]
            break
        if BRIEF_ENTRY.match(line):
            if current is not None:
                entries.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        entries.append(current)
    for entry in entries:
        sources = [line for line in entry[1:] if line.startswith("    ")
                   and line.strip() and not line.strip().startswith(("WARN ", "trigger: ", "要点/key points:"))]
        if not sources:
            continue
        warnings = [line for line in entry[1:] if line.strip().startswith("WARN ")]
        header = entry[0]
        if len(header) > 160:
            header = re.sub(r"‹(.*)›", lambda m: "‹" + m[1][:79] + "…›", header)
        source = sources[0]
        compact = [header, *warnings, source[:159] + "…" if len(source) > 160 else source]
        block = "\n".join(entry).rstrip()
        room = limit - len(out) - 1
        # One huge key point must not consume every later source's allocation.
        if len(block) > 320 or len(block) > room:
            block = "\n".join(compact)
        if len(block) > room:
            fixed = "\n".join(compact[:-1])
            excerpt_room = room - len(fixed) - 1
            if excerpt_room < 24:
                continue
            block = fixed + "\n" + source[:excerpt_room - 1] + "…"
        out += "\n" + block
    for line in tail:
        if len(out) + len(line) + 1 > limit:
            break
        out += "\n" + line
    return out + suffix


def cmd_brief(a) -> int:
    d = need_repo()
    if a.max < 128 or a.readme_lines < 1:
        sys.exit("--max must be at least 128 and --readme-lines must be positive")
    text = render_brief(d, a.topic, a.max, a.readme_lines)
    print(text if text is not None else "no match — try fewer words, or an alias (see glossary.md)")
    return 0 if text is not None else 1


def render_brief(d: Path, topic: list[str], max_chars: int = 6000, readme_lines: int = 25) -> str | None:
    """Context pack for a topic, most useful first: best matching notes (with their key points), open todos,
    trimmed section summaries, recent changes. Tight budgets reserve usable source entries before background."""
    secs = [n for n, _ in sections(d)]
    glossary = load_glossary(d)
    wanted = [search_text(t) for t in topic]
    groups = expand(topic, glossary) if topic else []
    res = brief_matches(rank(d, topic, top=48)) if topic else []
    if topic and not res:
        return None
    # A section name or its glossary alias alone asks for the whole section.
    broad_sections = [s for s in secs if len(groups) == 1 and any(x.text == s for x in groups[0])]
    broad = not topic or bool(broad_sections)
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
    if not topic:
        chosen = secs
    out: list[str] = []
    emit = out.append
    emit(f"# brief: {' '.join(topic) or '(all)'}  — {dt.date.today().isoformat()}, repo {git('log', '-1', '--format=%h %ad', '--date=short', cwd=d).stdout.strip()}")
    aliases = sorted({x.text for g in groups for x in g} - set(wanted))
    if aliases:
        emit(f"aliases: {', '.join(aliases[:12])}")
    if res:
        emit("\n## best matching notes")
        shown_points: set[str] = set()
        points: dict[str, list[str]] = {}
        for score, c, lines in res:
            via = " [symptom link]" if c.get("via_symptom") else " [glossary link]" if c.get("via_glossary") else ""
            emit(f"- {c['file']}:{c['line']} ‹{c['path'] or c['file']}›{via}")
            if c.get("via_symptom"):
                route = c["via_symptom"]
                emit(f"    trigger: {route['source']}:{route['line']} ‹{route['condition'][:120]}›")
            warning = note_warning((d / c["file"]).read_text(errors="ignore"))
            if warning:
                emit(f"    WARN {warning}")
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
            summary = readme_summary(readme.read_text(), readme_lines, None if broad else groups)
            if summary:
                emit(summary)
    history_paths = chosen if broad else sorted({c["file"] for _, c, _ in res})
    log = git("log", "-6" if broad else "-48", "--date=short", "--pretty=format:- %ad %s", "--", *history_paths, cwd=d, check=False).stdout.strip()
    if not broad:
        log = "\n".join([line for line in log.splitlines() if any(hits(line, g) for g in groups)][:6])
    if log:
        emit("\n## recent changes")
        emit(log)
    return pack_brief("\n".join(out), max_chars)


UPDATED = re.compile(r"(?:更新|Updated)[：:]\s*(\d{4}-\d{2}-\d{2})", re.I)
# per-article review interval: `· 复核：30d` / `· Review: 30d`, or 长期 / never for facts that do not drift.
# Like MemT's forget gates, which start every memory channel at its own half-life instead of one shared rate.
REVIEW = re.compile(r"(?:复核|Review)[：:]\s*(?:(\d+)\s*(?:d|天|days?)\b|(长期|不复核|never))", re.I)
STATUS = re.compile(r"(?:状态|Status)[：:]\s*([^·|\n]+)", re.I)


def review_warning(text: str) -> str | None:
    """Use the note's explicit update/review metadata, never its access frequency."""
    head = "\n".join(text.splitlines()[:8])
    status = STATUS.search(head)
    if status and re.match(r"已废弃|deprecated\b|obsolete\b", status[1], re.I):
        return "marked deprecated — verify a current replacement before using"
    updated = UPDATED.search(head)
    if not updated:
        return None
    try:
        age = (dt.date.today() - dt.date.fromisoformat(updated[1])).days
    except ValueError:
        return f"invalid update date {updated[1]} — fix the note's metadata"
    review = REVIEW.search(head)
    limit = None if review and review[2] else int(review[1]) if review else STALE_DAYS
    if limit is not None and age > limit:
        return f"last updated {age} days ago (review every {limit}) — re-verify before using"
    return None


CONFLICT_HEADER = re.compile(r"^>\s*(?:冲突|Conflicts)[：:]\s*(.+)$", re.I)


def conflict_warning(text: str) -> str | None:
    """Surface author-declared conflicts; never infer or resolve them."""
    targets = []
    fence = None
    for line in text.splitlines()[:8]:
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        match = CONFLICT_HEADER.match(line)
        if not match:
            continue
        for target in markdown_link_targets(match[1]):
            try:
                url = urlsplit(target)
            except ValueError:
                continue
            if not url.scheme and not url.netloc and url.path and target not in targets:
                targets.append(target)
    if not targets:
        return None
    return "declared conflict with " + ", ".join(targets[:3]) + (" and others" if len(targets) > 3 else "") + " — reconcile before relying on either"


def note_warning(text: str) -> str | None:
    warnings = [w for w in (review_warning(text), conflict_warning(text)) if w]
    return "; ".join(warnings) if warnings else None


# router-style LAN addresses (x.x.x.1 / .254) repeat at every site, so as a glossary alias they tie unrelated places together
GATEWAY_IP = re.compile(r"(10|192\.168|172\.(1[6-9]|2\d|3[01]))(\.\d{1,3}){1,2}\.(1|254)")


def gist_tokens(path: Path) -> set[str]:
    """Words of an article's title and key points; CJK runs as character bigrams so 群晖备份 ~ 备份群晖."""
    lines = path.read_text(errors="ignore").splitlines()
    title = next((l[2:] for l in lines if l.startswith("# ")), "")
    text = (title + "\n" + "\n".join(keypoints(path))).lower()
    toks = {w for w in re.findall(r"[a-z0-9][a-z0-9._-]+", text)}
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        toks.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return toks


def near_duplicates(root: Path, threshold: float = 0.6) -> list[tuple[str, str, float]]:
    """Article pairs in one section whose title + key points overlap (Jaccard) at least `threshold`.
    MemT clustered 3,678 induced experiences down to 1,200 before retrieval worked; two copies of a fact split
    the score and the stale one can win."""
    by_section: dict[str, list[tuple[str, set[str]]]] = {}
    for p in md_files(root):
        rel = p.relative_to(root).as_posix()
        if rel.count("/") >= 1 and p.name not in ("README.md", "todo.md"):
            toks = gist_tokens(p)
            if len(toks) >= 4:
                by_section.setdefault(section_of(rel), []).append((rel, toks))
    out = []
    for arts in by_section.values():
        for i, (a, ta) in enumerate(arts):
            for b, tb in arts[i + 1:]:
                j = len(ta & tb) / len(ta | tb)
                if j >= threshold:
                    out.append((a, b, j))
    return out


def markdown_link_targets(text: str):
    """Yield simple inline destinations outside fenced and inline code.

    This deliberately does not try to parse all Markdown; ambiguous destinations
    are skipped rather than reporting example code as a broken link.
    """
    fence = None
    prose = []
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if (marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence)
                    and not line[marker.end():].strip()):
                fence = None
            continue
        if marker:
            fence = marker.group(1)
            prose.append("")  # Code spans cannot cross a fenced block.
            continue
        prose.append(line)
    # Code spans may contain newlines, but cannot cross paragraph boundaries.
    for paragraph in re.split(r"\n\s*\n", "\n".join(prose)):
        paragraph = re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", "", paragraph, flags=re.S)
        for match in re.finditer(r"(?<!\\)\]\(\s*(?:<([^<>\n]+)>|([^\s()]+))(?:\s+['\"(][^\n]*?['\")])?\s*\)", paragraph):
            yield match.group(1) or match.group(2)


def cmd_lint(a) -> int:
    d = need_repo()
    warns: list[str] = []
    listed = {n for n, desc in sections(d) if "not listed" not in desc}
    for p in sorted(d.iterdir()):
        if p.is_dir() and p.name not in SKIP_DIRS and not p.name.startswith(".") and (p / "README.md").exists():
            if p.name not in listed:
                warns.append(f"{p.name}/: not in the root README table")
            if not (p / "todo.md").exists():
                warns.append(f"{p.name}/: no todo.md")
    for p in md_files(d):
        rel = p.relative_to(d).as_posix()
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
            warning = review_warning(text)
            if warning and "marked deprecated" not in warning:
                warns.append(f"{rel}: {warning}")
        if p.name == "todo.md":
            n_open = len(re.findall(r"^\s*- \[ \]", text, re.M))
            n_done = len(re.findall(r"^\s*- \[x\]", text, re.M | re.I))
            if n_open > TODO_MAX_OPEN:
                warns.append(f"{rel}: {n_open} open items — prioritise or split")
            if n_done > 5:
                warns.append(f"{rel}: {n_done} done items — delete them (git history keeps them)")
        conflict = conflict_warning(text)
        if conflict:
            warns.append(f"{rel}: {conflict}")
        for target in markdown_link_targets(text):
            try:
                url = urlsplit(target)
            except ValueError:  # Malformed URLs are outside this local-file check.
                continue
            if url.scheme or url.netloc or not url.path:
                continue
            if not (p.parent / unquote(url.path)).exists():
                warns.append(f"{rel}: broken link → {target}")
    for x, y, j in near_duplicates(d):
        warns.append(f"{x}: title and key points {j:.0%} like {y} — merge them, or archive the older one")
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
    try:
        with repository_operation_lock(d):
            return _save_locked(a, d)
    except TimeoutError:
        print("save skipped: another memory-use operation owns this checkout; retry later")
        return 1


def _save_locked(a, d: Path) -> int:
    if repository_operation_in_progress(d):
        print("save skipped: a rebase/merge is in progress; files and index left alone")
        return 1
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
        # A previous attempt may have committed successfully but failed to push.
        # Retrying save must check that pending history, even without a new edit.
        if a.no_push:
            return 0
        ok, result = _autosync_locked(d)
        print(result)
        return 0 if ok else 1
    msg = a.message + (f"\n\n{a.trailer}" if a.trailer else "")
    c = git("commit", "-q", "-m", msg, "--", *a.paths, cwd=d, check=False)   # only these paths, never others' staged work
    if c.returncode:
        print(c.stderr or c.stdout)
        return c.returncode
    if a.no_push:
        print("committed locally (not pushed): " + git("log", "--oneline", "-1", cwd=d).stdout.strip())
        return 0
    ok, result = _autosync_locked(d)
    if not ok:
        print("committed locally, not synced:\n" + result)
        return 1
    print(result + "\n" + git("log", "--oneline", "-1", cwd=d).stdout.strip())
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
    sub.add_parser("sync", help="pull latest and push local commits; never stash uncommitted edits").set_defaults(fn=cmd_sync)
    s = sub.add_parser("autosync", help="background pull/push on a timer (launchd / systemd / cron / Task Scheduler): on | off | status | run")
    s.add_argument("action", choices=["on", "off", "status", "run"]); s.add_argument("--every", type=int, default=15, help="minutes")
    s.set_defaults(fn=cmd_autosync)
    s = sub.add_parser("migrate", help="before leaving this computer: what is not on GitHub yet, and the one-liner for the new one")
    s.add_argument("--push", action="store_true", help="first push local commits (same as one autosync run)")
    s.set_defaults(fn=cmd_migrate)
    sub.add_parser("lessons", help="topics and notes that failed commands were pointed at, and whether a fix got saved").set_defaults(fn=cmd_lessons)
    s = sub.add_parser("eval", help="measure search: hit@1 / hit@3 / MRR over evals.md, glossary 见-links and article titles")
    s.add_argument("--misses", type=int, default=15, help="list up to N queries not ranked first (0: none)")
    s.add_argument("--cases", help="evaluate only a Markdown question set outside the notes repo")
    s.add_argument("--max", type=int, default=6000, help="brief character budget to evaluate (minimum 128)")
    s.add_argument("--json", action="store_true", help="structured metrics and per-question recall gaps")
    s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("hook", help="Claude Code hook entry points: prompt (UserPromptSubmit, JSON on stdin) | "
                                    "tool (PostToolUseFailure) | stop (Stop) | session (SessionStart)")
    s.add_argument("event", choices=["prompt", "tool", "stop", "session"]); s.set_defaults(fn=cmd_hook)
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
    s = sub.add_parser("save", help="secret-scan, commit given paths, pull --rebase, push (only to the verified notes repo)")
    s.add_argument("paths", nargs="+"); s.add_argument("-m", "--message", required=True); s.add_argument("--trailer", default=os.environ.get("MEMORY_USE_TRAILER", ""))
    s.add_argument("--no-push", action="store_true", help="commit locally only; autosync or `sync` pushes later")
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
