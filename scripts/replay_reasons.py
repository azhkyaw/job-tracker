"""Replay the rejection-reason stage over every stored rejection email, grade
the candidate models, then backfill the reviewed decisions.

The stage is email_classifier.rejection_reason (prompt rejection_reason_v1):
the reason a rejection email itself STATES, with the sentence that states
it, quoted verbatim or dropped. Every call below goes through that REAL
function — prompt, quote check, repair retry — via a client that records
usage, so what is graded is what the worker would do.

  uv run python scripts/replay_reasons.py --price                  # count_tokens only, no spend
  uv run python scripts/replay_reasons.py --run haiku,sonnet --trials 2 --out RUNS.jsonl
  uv run python scripts/replay_reasons.py --report RUNS.jsonl      # offline
  uv run python scripts/replay_reasons.py --apply RUNS.jsonl --model claude-sonnet-5
  uv run python scripts/replay_reasons.py --apply RUNS.jsonl --model claude-sonnet-5 \\
      --write --snapshot SNAP.json                                  # writes, after a snapshot

--run SPENDS on the key the live pipeline uses: price it first. The DB is
read-only for every mode but --apply --write. RUNS files quote real emails:
keep them outside the repository (the real-names rule).

--apply takes each email's answer from the chosen model's first trial and
writes two things, never overwriting: emails.extraction.rejection_reason
(the stage's answer, so the email reads as having been through it) where the
key is absent, and — when there is a reason — reason / reason_source /
reason_quote on that email's rejected events that carry no reason yet. A
reason the user set by hand always wins.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import anthropic  # noqa: E402
from psycopg.types.json import Json  # noqa: E402

from pipeline import db, email_classifier  # noqa: E402

MODELS = {"haiku": "claude-haiku-4-5-20251001", "sonnet": "claude-sonnet-5"}
# $/MTok, platform.claude.com pricing page read 25 Sep 2026.
PRICE = {"claude-haiku-4-5-20251001": (1.0, 5.0), "claude-sonnet-5": (2.0, 10.0)}
ASSUMED_OUT = {"claude-haiku-4-5-20251001": 60, "claude-sonnet-5": 250}  # thinking included


class Metered:
    """llm.Client's complete() surface over the SDK, sending what
    llm.AnthropicBackend sends for a stage with no effort, and recording usage."""

    def __init__(self, sdk):
        self.sdk, self.calls = sdk, []

    def complete(self, *, model, system, messages, max_tokens, json=False):
        r = self.sdk.messages.create(model=model, max_tokens=max_tokens, system=system,
                                     messages=messages)
        self.calls.append({"in": r.usage.input_tokens, "out": r.usage.output_tokens,
                           "stop": r.stop_reason})
        return "".join(b.text for b in r.content if b.type == "text")


def load() -> list[dict]:
    """Every email classified a rejection that still has a body, with the ids
    of the rejected events it filed (none when it waits in triage)."""
    with db.connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        return conn.execute("""
            SELECT m.id::text AS id, m.sender, m.subject, m.received_at, m.body_text,
                   m.extraction ? 'rejection_reason' AS done,
                   coalesce(array_agg(e.id::text) FILTER (WHERE e.id IS NOT NULL), '{}') AS events
              FROM emails m
              LEFT JOIN events e ON e.source_email_id = m.id AND e.type = 'rejected'
             WHERE m.classification = 'rejection' AND coalesce(m.body_text, '') <> ''
             GROUP BY m.id ORDER BY m.received_at""").fetchall()


def price(rows, names):
    sdk = anthropic.Anthropic()
    system = email_classifier._load_prompt(email_classifier.REASON_PROMPT_VERSION)
    for n in names:
        model = MODELS[n]
        tin = sum(sdk.messages.count_tokens(model=model, system=system, messages=[{
            "role": "user", "content": email_classifier._email_block(
                r["sender"], r["subject"], r["received_at"], r["body_text"],
                email_classifier.STAGE2_BODY_CHARS)}]).input_tokens for r in rows)
        pin, pout = PRICE[model]
        cost = (tin * pin + len(rows) * ASSUMED_OUT[model] * pout) / 1e6
        print(f"{n:7} {len(rows)} emails, {tin:,} input tokens: ~${cost:.3f} per pass "
              f"(assuming ~{ASSUMED_OUT[model]} output tokens each)")


def run(rows, names, trials, out: Path):
    sdk = anthropic.Anthropic(max_retries=4)
    jobs = [(n, t, r) for n in names for t in range(trials) for r in rows]

    def one(job):
        n, t, r = job
        client = Metered(sdk)
        ans = email_classifier.rejection_reason(client, r["sender"], r["subject"] or "",
                                                r["received_at"], r["body_text"], model=MODELS[n])
        return {"email_id": r["id"], "config": n, "trial": t, **ans, "calls": client.calls}

    with ThreadPoolExecutor(max_workers=4) as pool, out.open("a", encoding="utf-8") as f:
        for i, rec in enumerate(pool.map(one, jobs), 1):
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)}")
    print(f"wrote {len(jobs)} answers to {out}")


def report(rows, runs: Path):
    by_id = {r["id"]: r for r in rows}
    recs = [json.loads(x) for x in runs.open(encoding="utf-8") if x.strip()]
    per = defaultdict(lambda: defaultdict(dict))            # config -> email -> trial -> reason
    quotes, spent = {}, Counter()
    for x in recs:
        per[x["config"]][x["email_id"]][x["trial"]] = x["reason"]
        if x["reason"]:
            quotes[(x["config"], x["email_id"])] = x["quote"]
        model = MODELS[x["config"]]
        pin, pout = PRICE[model]
        spent[x["config"]] += sum(c["in"] * pin + c["out"] * pout for c in x["calls"]) / 1e6
    for n, answers in per.items():
        stable = sum(len(set(t.values())) == 1 for t in answers.values())
        said = Counter(t.get(0) for t in answers.values())
        errors = sum(1 for x in recs if x["config"] == n and x.get("error"))
        print(f"\n== {n}: {len(answers)} emails, same answer every trial on {stable}, "
              f"errors {errors}, spent ${spent[n]:.3f}")
        print("   first-trial reasons:", dict(said))
    ids = sorted({e for a in per.values() for e in a}, key=lambda e: str(by_id.get(e, {}).get("received_at")))
    print("\n== every email any config gave a reason (config: trials)")
    for e in ids:
        got = {n: [per[n][e].get(t) for t in sorted(per[n][e])] for n in per if e in per[n]}
        if any(any(v) for v in got.values()):
            r = by_id.get(e, {})
            print(f"\n  {e[:8]} {str(r.get('received_at'))[:10]} {r.get('subject', '')[:70]!r}")
            for n, v in got.items():
                print(f"     {n:7} {v}  {quotes.get((n, e), '')[:150]!r}")


def apply(rows, runs: Path, model: str, write: bool, snapshot: Path | None):
    by_id = {r["id"]: r for r in rows}
    config = next((n for n, m in MODELS.items() if m == model), None)
    chosen = {}
    for x in (json.loads(y) for y in runs.open(encoding="utf-8") if y.strip()):
        if x["config"] == config and x["trial"] == 0:
            chosen[x["email_id"]] = {k: x.get(k) for k in ("reason", "quote", "model", "prompt_version")}
    todo = [(e, rr) for e, rr in chosen.items() if e in by_id and not by_id[e]["done"]]
    tagged = [(e, rr) for e, rr in todo if rr["reason"]]
    print(f"{len(chosen)} answers from {model}; {len(todo)} emails not yet through the stage; "
          f"{len(tagged)} state a reason:")
    for e, rr in tagged:
        print(f"  {e[:8]} {rr['reason']:<11} events {len(by_id[e]['events'])}  {rr['quote'][:110]!r}")
    if not write:
        print("\ndry run: nothing written (add --write --snapshot PATH)")
        return
    if snapshot is None:
        raise SystemExit("--write needs --snapshot PATH: the rows are copied there first")
    ids = [e for e, _ in todo]
    with db.connect() as conn, conn.transaction():
        snap = {
            "emails": conn.execute("SELECT id::text, extraction FROM emails WHERE id::text = ANY(%s)",
                                   (ids,)).fetchall(),
            "events": conn.execute("SELECT id::text, source_email_id::text, payload FROM events "
                                   "WHERE type = 'rejected' AND source_email_id::text = ANY(%s)",
                                   (ids,)).fetchall(),
        }
        snapshot.write_text(json.dumps(snap, default=str, indent=1), encoding="utf-8")
        n_email = n_event = 0
        for e, rr in todo:
            n_email += conn.execute(
                """UPDATE emails SET extraction = coalesce(extraction, '{}'::jsonb) || %s::jsonb
                    WHERE id::text = %s AND NOT coalesce(extraction ? 'rejection_reason', false)""",
                (Json({"rejection_reason": rr}), e)).rowcount
            if rr["reason"]:
                n_event += conn.execute(
                    """UPDATE events SET payload = payload || %s::jsonb
                        WHERE source_email_id::text = %s AND type = 'rejected'
                          AND NOT payload ? 'reason'""",
                    (Json({"reason": rr["reason"], "reason_source": "email",
                           "reason_quote": rr["quote"]}), e)).rowcount
    print(f"\nwrote: {n_email} emails, {n_event} rejected events; snapshot {snapshot}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price", action="store_true")
    ap.add_argument("--run", metavar="CONFIGS", help="comma list of " + ",".join(MODELS))
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--report", type=Path, metavar="RUNS")
    ap.add_argument("--apply", type=Path, metavar="RUNS")
    ap.add_argument("--model", default=email_classifier.REASON_MODEL)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--snapshot", type=Path)
    a = ap.parse_args()
    rows = load()
    if a.price:
        price(rows, list(MODELS))
    elif a.run:
        if not a.out:
            raise SystemExit("--run needs --out RUNS.jsonl (outside the repository)")
        run(rows, a.run.split(","), a.trials, a.out)
    elif a.report:
        report(rows, a.report)
    elif a.apply:
        apply(rows, a.apply, a.model, a.write, a.snapshot)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
