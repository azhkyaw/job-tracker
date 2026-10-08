"""Replay the invitation-detail stage over every stored interview-invite
email, grade it against the author's own labels, then backfill.

The stage is email_classifier.invite_detail (prompt invite_detail_v1): what
an invitation mail DOES on the thread (invitation / reschedule /
cancellation / reminder / scheduling), the kind of round it concerns
(analytics.ROUND_KINDS) and the day it names, resolved. Every call goes
through that REAL function — prompt, validation, repair retry — via a client
that records usage, so what is graded is what the worker would do.

The labels are the author's corrections of 8-9 Oct 2026 on real threads:
`round_kind` set by hand on an event (no `kind_source`), `round_is = none`
on a line that is not a round, and a `stated_date` the author left standing.

  uv run python scripts/replay_invites.py --price                  # count_tokens only, no spend
  uv run python scripts/replay_invites.py --run sonnet --trials 1 --out RUNS.jsonl
  uv run python scripts/replay_invites.py --report RUNS.jsonl      # offline, graded
  uv run python scripts/replay_invites.py --apply RUNS.jsonl --model claude-sonnet-5
  uv run python scripts/replay_invites.py --apply RUNS.jsonl --model claude-sonnet-5 \\
      --write --snapshot SNAP.json                                  # writes, after a snapshot

--run SPENDS on the key the live pipeline uses: price it first. The DB is
read-only for every mode but --apply --write. RUNS files quote real emails:
keep them outside the repository (the real-names rule).

--apply takes each email's answer from the chosen model's first trial and
writes, never over the author's word: emails.extraction.invite_detail where
the key is absent; and on the email's interview_invite events `invite_role`
where absent, `round_kind` + `kind_source: email` where the event carries no
kind, and `stated_date` from the stage's day where the event has none or
carries a different one (the "tomorrow" misread). A line the author marked
"not a round" keeps the mark; its role is still recorded.
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
    """Every email classified an interview invite that still has a body, with
    its interview_invite events and the author's labels on them."""
    with db.connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        return conn.execute("""
            SELECT m.id::text AS id, m.sender, m.subject, m.received_at, m.body_text,
                   m.extraction ? 'invite_detail' AS done,
                   coalesce(json_agg(json_build_object(
                       'id', e.id::text, 'round_is', e.payload->>'round_is',
                       'round_kind', e.payload->>'round_kind', 'kind_source', e.payload->>'kind_source',
                       'stated_date', e.payload->>'stated_date', 'invite_role', e.payload->>'invite_role'))
                     FILTER (WHERE e.id IS NOT NULL), '[]') AS events
              FROM emails m
              LEFT JOIN events e ON e.source_email_id = m.id AND e.type = 'interview_invite'
             WHERE m.classification = 'interview_invite' AND coalesce(m.body_text, '') <> ''
             GROUP BY m.id ORDER BY m.received_at""").fetchall()


def price(rows, names):
    sdk = anthropic.Anthropic()
    system = email_classifier._load_prompt(email_classifier.INVITE_PROMPT_VERSION)
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
        ans = email_classifier.invite_detail(client, r["sender"], r["subject"] or "",
                                             r["received_at"], r["body_text"], model=MODELS[n])
        return {"email_id": r["id"], "config": n, "trial": t, **ans, "calls": client.calls}

    with ThreadPoolExecutor(max_workers=4) as pool, out.open("a", encoding="utf-8") as f:
        for i, rec in enumerate(pool.map(one, jobs), 1):
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)}")
    print(f"wrote {len(jobs)} answers to {out}")


def _labels(r):
    """The author's word on this email's events: a hand-set kind, a 'not a
    round' mark, the stated day left standing."""
    kinds = {e["round_kind"] for e in r["events"] if e["round_kind"] and not e["kind_source"]}
    marked = any(e["round_is"] == "none" for e in r["events"])
    days = {e["stated_date"] for e in r["events"] if e["stated_date"]}
    return kinds, marked, days


def report(rows, runs: Path):
    by_id = {r["id"]: r for r in rows}
    recs = [json.loads(x) for x in runs.open(encoding="utf-8") if x.strip()]
    per = defaultdict(lambda: defaultdict(dict))            # config -> email -> trial -> answer
    spent = Counter()
    for x in recs:
        per[x["config"]][x["email_id"]][x["trial"]] = x
        pin, pout = PRICE[MODELS[x["config"]]]
        spent[x["config"]] += sum(c["in"] * pin + c["out"] * pout for c in x["calls"]) / 1e6
    for n, answers in per.items():
        first = {e: t[0] for e, t in answers.items() if 0 in t}
        stable = sum(len({(a["role"], a["kind"], a["day"]) for a in t.values()}) == 1 for t in answers.values())
        errors = sum(1 for a in first.values() if a.get("error"))
        roles = Counter(a["role"] for a in first.values())
        kinds = Counter(a["kind"] for a in first.values())
        print(f"\n== {n}: {len(answers)} emails, same answer every trial on {stable}, errors {errors}, "
              f"spent ${spent[n]:.3f}")
        print("   roles:", dict(roles))
        print("   kinds:", dict(kinds))
        # Graded against the author's labels.
        k_hit = k_miss = m_hit = m_miss = d_hit = d_miss = 0
        k_rows, m_rows, d_rows = [], [], []
        for e, a in first.items():
            r = by_id.get(e)
            if not r:
                continue
            kinds_l, marked, days = _labels(r)
            if kinds_l:
                if a["kind"] in kinds_l:
                    k_hit += 1
                else:
                    k_miss += 1
                    k_rows.append((e, sorted(kinds_l), a["kind"], r["subject"]))
            if marked:
                if a["role"] != "invitation":
                    m_hit += 1
                else:
                    m_miss += 1
                    m_rows.append((e, a["role"], a["day"], r["subject"]))
            if days and a["day"]:
                if a["day"] in days:
                    d_hit += 1
                else:
                    d_miss += 1
                    d_rows.append((e, sorted(days), a["day"], r["subject"]))
        print(f"   kind vs the author's {k_hit + k_miss} labels: {k_hit} agree, {k_miss} differ")
        for e, want, got, subj in k_rows:
            print(f"      {e[:8]} author {want} model {got!r:14} {subj[:60]!r}")
        print(f"   'not a round' marks ({m_hit + m_miss}): role not an invitation on {m_hit}, "
              f"still an invitation on {m_miss}")
        for e, role, day, subj in m_rows:
            print(f"      {e[:8]} role {role} day {day} {subj[:60]!r}")
        print(f"   day vs the stated day on record ({d_hit + d_miss}): {d_hit} same, {d_miss} differ")
        for e, want, got, subj in d_rows:
            print(f"      {e[:8]} record {want} model {got} {subj[:60]!r}")
    ids = sorted({e for a in per.values() for e in a}, key=lambda e: str(by_id.get(e, {}).get("received_at")))
    print("\n== every email, first trial (config: role / kind / day)")
    for e in ids:
        r = by_id.get(e, {})
        print(f"\n  {e[:8]} {str(r.get('received_at'))[:10]} {(r.get('subject') or '')[:70]!r}")
        for n in per:
            a = per[n][e].get(0)
            if a:
                print(f"     {n:7} {a['role']:13} {str(a['kind']):14} {a['day']}")


def apply(rows, runs: Path, model: str, write: bool, snapshot: Path | None):
    by_id = {r["id"]: r for r in rows}
    config = next((n for n, m in MODELS.items() if m == model), None)
    chosen = {}
    for x in (json.loads(y) for y in runs.open(encoding="utf-8") if y.strip()):
        if x["config"] == config and x["trial"] == 0:
            chosen[x["email_id"]] = {k: x.get(k) for k in ("role", "kind", "day", "model", "prompt_version")}
    todo = [(e, a) for e, a in chosen.items() if e in by_id and not by_id[e]["done"]]
    print(f"{len(chosen)} answers from {model}; {len(todo)} emails not yet through the stage")
    for e, a in todo:
        evs = by_id[e]["events"]
        print(f"  {e[:8]} {a['role']:13} {str(a['kind']):14} {a['day']}  events {len(evs)}"
              f"{'  (author: ' + ', '.join(sorted({x['round_kind'] for x in evs if x['round_kind']})) + ')' if any(x['round_kind'] for x in evs) else ''}")
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
                                   "WHERE type = 'interview_invite' AND source_email_id::text = ANY(%s)",
                                   (ids,)).fetchall(),
        }
        snapshot.write_text(json.dumps(snap, default=str, indent=1), encoding="utf-8")
        n_email = n_role = n_kind = n_day = 0
        for e, a in todo:
            n_email += conn.execute(
                """UPDATE emails SET extraction = coalesce(extraction, '{}'::jsonb) || %s::jsonb
                    WHERE id::text = %s AND NOT coalesce(extraction ? 'invite_detail', false)""",
                (Json({"invite_detail": a}), e)).rowcount
            n_role += conn.execute(
                """UPDATE events SET payload = payload || %s::jsonb
                    WHERE source_email_id::text = %s AND type = 'interview_invite'
                      AND NOT payload ? 'invite_role'""",
                (Json({"invite_role": a["role"]}), e)).rowcount
            if a["kind"]:
                n_kind += conn.execute(
                    """UPDATE events SET payload = payload || %s::jsonb
                        WHERE source_email_id::text = %s AND type = 'interview_invite'
                          AND NOT payload ? 'round_kind'""",
                    (Json({"round_kind": a["kind"], "kind_source": "email"}), e)).rowcount
            if a["day"]:
                n_day += conn.execute(
                    """UPDATE events SET payload = payload || %s::jsonb
                        WHERE source_email_id::text = %s AND type = 'interview_invite'
                          AND coalesce(payload->>'stated_date', '') <> %s""",
                    (Json({"stated_date": a["day"]}), e, a["day"])).rowcount
    print(f"\nwrote: {n_email} emails; events: {n_role} roles, {n_kind} kinds, {n_day} days; "
          f"snapshot {snapshot}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--price", action="store_true")
    ap.add_argument("--run", metavar="CONFIGS", help="comma list of " + ",".join(MODELS))
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--report", type=Path, metavar="RUNS")
    ap.add_argument("--apply", type=Path, metavar="RUNS")
    ap.add_argument("--model", default=email_classifier.INVITE_MODEL)
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
