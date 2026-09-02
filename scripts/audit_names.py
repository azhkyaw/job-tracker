"""Find real names from the dev database inside the repository.

    uv run python scripts/audit_names.py              # tracked files (the tip)
    uv run python scripts/audit_names.py --history    # every blob + commit
                                                      # message in git history
    uv run python scripts/audit_names.py --list       # print the name set

Why this is derived from the database rather than kept as a list: the 26 Jul
2026 release audit scrubbed real employers, an agency and a named recruiter out
of the tracked files and rewrote history to match — and by 2 Sep 2026 the same
class of name had been written back into comments, tests, a migration header
and CLAUDE.md, dozens of times, by the ordinary habit of naming the real case
that motivated a change. A hand-kept denylist only knows the names somebody
already noticed. The database knows every employer ever applied to, every
contact ever captured, every recruiter the extractor ever named, and the
author's own resume filename, so the check cannot go stale as the search goes
on. Run it against the DEV database (the one with the real rows) — against the
throwaway test DB it finds nothing and says so.

Two blind spots, both seen on the first run (2 Sep 2026): a job that was
browsed but never applied to leaves no posting, and an employer whose mail
classified not_job_related leaves no extraction — neither reaches the name set,
though both had been named in comments. A shortened form of a stored name
("Bellows & Munson" for "Bellows & Munson Asia") can slip past too when its
tokens are short, and so can an employer's mail DOMAIN, which is stored only
inside the sender header. Read the comments around every hit; the names next to a real one are
usually real as well.

What it does NOT do is write the rewrite rules. The July rewrite needed
case-sensitive literal rules with surrounding context because of substring
traps (a selector containing a recruiter's first name, a job id sharing a
fragment with a company) — that judgement stays manual. This script is the
zero-survivors check that verifies the rules afterwards, run with --history.

Strength of a hit:
  strong  a multi-word name in any casing, a single-word name in its exact
          database casing, or an author identifier — read every one.
  weak    a single-word name in some other casing, a distinctive token of a
          longer name, or an email sender's display name — mostly common
          words; summarised per name, scan for the real ones.
Exit 1 when any strong hit exists, so this can gate a release script.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter, defaultdict
from email.utils import parseaddr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pipeline import db  # noqa: E402
from pipeline.email_classifier import norm_company  # noqa: E402

SKIP_FILES = {".env"}
PLACEHOLDER_NAMES = {"unknown company", "unknown role"}
MIN_LEN = 4


# --------------------------------------------------------------------------- names

def _git(*args: str, binary: bool = False):
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8", errors="replace")


def names_from_db(conn) -> dict[str, set[str]]:
    """kind -> raw strings, straight from the rows."""
    # db.connect() rows are dicts (dict_row), so take the single value by position.
    q = lambda sql: [next(iter(r.values())) for r in conn.execute(sql).fetchall()]  # noqa: E731
    companies = set(q("SELECT DISTINCT company_raw FROM postings WHERE company_raw IS NOT NULL"))
    companies |= set(q("SELECT DISTINCT extraction->>'company' FROM emails "
                       "WHERE extraction->>'company' IS NOT NULL"))
    norms = set(q("SELECT DISTINCT company_norm FROM jobs"))
    people = set(q("SELECT DISTINCT name FROM contacts"))
    people |= set(q("SELECT DISTINCT extraction->'recruiter'->>'name' FROM emails "
                    "WHERE extraction->'recruiter'->>'name' IS NOT NULL"))
    senders = {parseaddr(s)[0] for s in q("SELECT DISTINCT sender FROM emails")}
    author = set(q("SELECT email FROM users"))
    author |= {e.split("@", 1)[0] for e in author}
    author |= set(q("SELECT DISTINCT resume_file FROM applications WHERE resume_file IS NOT NULL"))
    clean = lambda xs: {x.strip() for x in xs if x and len(x.strip()) >= MIN_LEN  # noqa: E731
                        and x.strip().lower() not in PLACEHOLDER_NAMES}
    return {"company": clean(companies), "norm": clean(norms), "person": clean(people),
            "sender": clean(senders), "author": clean(author)}


class Pattern:
    __slots__ = ("label", "kind", "strong", "rx")

    def __init__(self, label: str, kind: str, strong: bool, rx: re.Pattern):
        self.label, self.kind, self.strong, self.rx = label, kind, strong, rx


def build_patterns(names: dict[str, set[str]], extra: list[str]) -> list[Pattern]:
    pats: list[Pattern] = []
    seen: set[tuple[str, int]] = set()

    def add(label: str, kind: str, strong: bool, literal: str, ci: bool):
        flags = re.IGNORECASE if ci else 0
        key = (literal.lower() if ci else literal, flags)
        if key in seen or len(literal) < 3:
            return
        seen.add(key)
        pats.append(Pattern(label, kind, strong, re.compile(
            r"(?<![\w])" + re.escape(literal) + r"(?![\w])", flags)))

    # Tokens shared by several employers are corporate vocabulary ("Consulting",
    # "Group", "Singapore"); a token unique to one or two names is the name.
    # Derived from frequency, so no hand list of generic words is needed.
    token_freq = Counter()
    for n in names["company"]:
        for t in set(re.findall(r"[A-Za-z][A-Za-z0-9]+", n)):
            token_freq[t.lower()] += 1

    for n in sorted(names["company"]):
        multi = " " in n
        add(n, "company", True, n, ci=multi)          # exact display form
        if not multi:
            add(n, "company", False, n, ci=True)      # other casings: weak
        nc = norm_company(n)
        if nc and nc != n.lower():
            add(n, "company", " " in nc, nc, ci=True)  # normalised form
        for t in set(re.findall(r"[A-Za-z][A-Za-z0-9]+", n)):
            if token_freq[t.lower()] > 2:
                continue
            if len(t) >= 6 and t.lower() != n.lower():
                add(f"{n} [token {t}]", "company", False, t, ci=True)
                if t.endswith("s"):
                    add(f"{n} [token {t[:-1]}]", "company", False, t[:-1], ci=True)
            elif len(t) >= 3 and t.isupper():
                add(f"{n} [token {t}]", "company", False, t, ci=False)
    for n in sorted(names["norm"]):
        if " " in n:
            add(n, "norm", True, n, ci=True)
        else:
            add(n, "norm", False, n, ci=True)
    for n in sorted(names["person"]):
        add(n, "person", True, n, ci=True)
        first = n.split()[0]
        if len(first) >= 4:
            add(f"{n} [first name]", "person", False, first, ci=False)
    for n in sorted(names["sender"]):
        add(n, "sender", False, n, ci=True)
    for n in sorted(names["author"]):
        add(n, "author", True, n, ci=True)
        stem = n.rsplit(".", 1)[0] if "." in n else None
        for part in filter(None, [stem]):
            add(f"{n} [stem]", "author", True, part, ci=True)
    for n in extra:
        add(n, "extra", True, n, ci=False)
    return pats


# --------------------------------------------------------------------------- scanning

def scan_text(text: str, pats: list[Pattern]):
    """Yield (lineno, pattern, line) for every hit."""
    for lineno, line in enumerate(text.splitlines(), 1):
        for p in pats:
            if p.rx.search(line):
                yield lineno, p, line


def tracked_files() -> list[str]:
    return [f for f in _git("ls-files", "-z").split("\0") if f and f not in SKIP_FILES]


def scan_tree(pats: list[Pattern]):
    hits = []
    for rel in tracked_files():
        path = ROOT / rel
        try:
            text = path.read_bytes().decode("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, p, line in scan_text(text, pats):
            hits.append((rel, lineno, p, line.strip()))
    return hits


def scan_history(pats: list[Pattern]):
    """Every blob reachable from any ref, plus every commit message."""
    objects = _git("rev-list", "--all", "--objects")
    paths_by_sha: dict[str, set[str]] = defaultdict(set)
    for line in objects.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            paths_by_sha[sha].add(path)
    shas = list(paths_by_sha)
    proc = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, check=True,
                          input="\n".join(shas).encode(), capture_output=True)
    out, i = proc.stdout, 0
    # Keyed by pattern INDEX, not label: a single-word name yields two patterns
    # with the same label (exact casing = strong, other casings = weak), and
    # keying by label reported every `chrome.runtime` as a strong hit on an
    # employer whose name is that same common word.
    blob_hits: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    while i < len(out):
        nl = out.index(b"\n", i)
        header = out[i:nl].decode()
        sha, kind, size = header.split()[:3]
        size = int(size)
        body = out[nl + 1:nl + 1 + size]
        i = nl + 1 + size + 1
        if kind != "blob":
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for i, p in enumerate(pats):
            if p.rx.search(text):
                for path in paths_by_sha[sha]:
                    blob_hits[i][path].add(sha)
    msg_hits: dict[int, set[str]] = defaultdict(set)
    log = _git("log", "--all", "--format=%H%x1e%B%x1f")
    for entry in log.split("\x1f"):
        if "\x1e" not in entry:
            continue
        sha, _, msg = entry.partition("\x1e")
        for i, p in enumerate(pats):
            if p.rx.search(msg):
                msg_hits[i].add(sha.strip())
    return blob_hits, msg_hits


# --------------------------------------------------------------------------- report

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--history", action="store_true",
                    help="scan every blob and commit message in git history")
    ap.add_argument("--list", action="store_true", help="print the loaded name set")
    ap.add_argument("--also", action="append", default=[], metavar="TEXT",
                    help="an extra literal to treat as a strong name (repeatable), "
                         "e.g. a Windows username that never reaches the database")
    ap.add_argument("--weak", action="store_true",
                    help="print every weak hit in full instead of a per-name summary")
    args = ap.parse_args()

    with db.connect() as conn:
        names = names_from_db(conn)
    total = sum(len(v) for v in names.values())
    if total == 0:
        print("no names in this database — point TRACKER_DATABASE_URL at the dev DB")
        return 2
    print("names loaded: " + ", ".join(f"{k} {len(v)}" for k, v in names.items()))
    if args.list:
        for kind, vals in names.items():
            for v in sorted(vals):
                print(f"  {kind:8} {v}")
    pats = build_patterns(names, args.also)

    if args.history:
        blob_hits, msg_hits = scan_history(pats)
        strong = 0
        print("\n== history: names present in any blob ever committed")
        for i in sorted(blob_hits, key=lambda i: (not pats[i].strong, pats[i].label)):
            p = pats[i]
            n_blobs = sum(len(s) for s in blob_hits[i].values())
            strong += p.strong
            print(f"  [{'strong' if p.strong else 'weak  '}] {p.label}: {n_blobs} blob(s) in "
                  + ", ".join(sorted(blob_hits[i])))
        print("\n== history: names present in commit messages")
        for i in sorted(msg_hits, key=lambda i: (not pats[i].strong, pats[i].label)):
            p = pats[i]
            strong += p.strong
            print(f"  [{'strong' if p.strong else 'weak  '}] {p.label}: "
                  + ", ".join(s[:10] for s in sorted(msg_hits[i])))
        if not blob_hits and not msg_hits:
            print("  (none)")
        print(f"\nhistory: {strong} strong name(s) present — "
              + ("a rewrite is needed before publishing" if strong else "clean"))
        return 1 if strong else 0

    hits = scan_tree(pats)
    strong_hits = [h for h in hits if h[2].strong]
    weak_hits = [h for h in hits if not h[2].strong]
    by_file: dict[str, list] = defaultdict(list)
    for h in strong_hits:
        by_file[h[0]].append(h)
    print(f"\n== strong hits: {len(strong_hits)} in {len(by_file)} file(s)")
    for rel in sorted(by_file):
        print(f"\n{rel}  ({len(by_file[rel])})")
        for _, lineno, p, line in by_file[rel]:
            print(f"  {lineno:>5}  {p.label}  |  {line[:110]}")
    print(f"\n== weak hits: {len(weak_hits)}"
          + ("" if args.weak else " (per name; --weak prints each line)"))
    if args.weak:
        for rel, lineno, p, line in weak_hits:
            print(f"  {rel}:{lineno}  {p.label}  |  {line[:100]}")
    else:
        per = defaultdict(lambda: defaultdict(int))
        for rel, _, p, _ in weak_hits:
            per[p.label][rel] += 1
        for label in sorted(per, key=lambda l: -sum(per[l].values())):
            files = ", ".join(f"{f} x{n}" for f, n in sorted(per[label].items(), key=lambda kv: -kv[1]))
            print(f"  {label}: {files}")
    print(f"\ntree: {len(strong_hits)} strong hit(s) — "
          + ("scrub before publishing" if strong_hits else "clean"))
    return 1 if strong_hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
