"""Replay stored job descriptions through a candidate JD prompt and diff it
against the extraction on record. READ-ONLY unless --apply is given.

The sibling of scripts/replay_classify.py for the JD extractor: a prompt or
model change is settled by replaying the real rows, never by reasoning about
it (invariant #5). What runs is exactly what the worker runs —
jd_extraction.extract with the same truncation, validation, quote check,
model and effort (config.JD_*) — only what is named here is changed.

  uv run python scripts/replay_jd.py --out v2.jsonl                 # live
  uv run python scripts/replay_jd.py --limit 20 --workers 4
  uv run python scripts/replay_jd.py --batch-submit                 # Batch API, half price
  uv run python scripts/replay_jd.py --batch-collect msgbatch_... --out v2.jsonl
  uv run python scripts/replay_jd.py --apply v2.jsonl               # writes those rows
  ... --skip v2.jsonl --out rest.jsonl   # resume: only postings not in it

It prints the visa_signal transition table (on record -> candidate), every
posting whose signal changed with the sentence each version quoted, and how
often the OTHER fields moved. A prompt edit aimed at one field must not quietly
move the rest — though technology lists move between two runs of the SAME
prompt on about half of postings under Haiku (25 Sep 2026), so read that line
against its own noise. --apply stores the saved decisions as new extraction
rows through jd_extraction.store — the rows that were reviewed, not a fresh
run that could differ from them.

Batch mode (a backfill, where nobody waits): the first call of every posting
goes through the Message Batches API at 50%, and collection feeds each answer
back through jd_extraction.extract via a client whose first call returns it,
so validation, the quote check and the repair retry run exactly as live; a
repair call is made live.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import anthropic  # noqa: E402

from pipeline import config, db, jd_extraction, llm  # noqa: E402
from pipeline.email_classifier import _load_prompt  # noqa: E402


def _latest(conn, limit: int):
    return conn.execute(
        """
        SELECT DISTINCT ON (p.id) p.id AS posting_id, p.user_id, p.title, p.jd_text,
               x.visa_signal, x.visa_notes, x.languages, x.technologies,
               x.seniority, x.work_mode, x.prompt_version
        FROM postings p
        LEFT JOIN extractions x ON x.posting_id = p.id
        WHERE COALESCE(p.jd_text, '') <> ''
        ORDER BY p.id, x.extracted_at DESC NULLS LAST
        """ + (f" LIMIT {int(limit)}" if limit else "")).fetchall()


def _rows(args):
    with db.connect() as conn:
        conn.execute("SET default_transaction_read_only = on")
        rows = _latest(conn, args.limit)
    if args.skip:
        # Resume a run that stopped part-way (the credit balance ran out 226
        # postings into the first full Sonnet replay, 25 Sep 2026) without
        # paying for the finished ones again.
        done = {json.loads(line)["posting_id"]
                for f in args.skip for line in open(f, encoding="utf-8") if line.strip()}
        rows = [r for r in rows if str(r["posting_id"]) not in done]
    return rows


def _norm(xs) -> set[str]:
    return {x.strip().lower() for x in xs or []}


def _extract(client, r, args):
    return jd_extraction.extract(client, r["jd_text"], r["title"], args.prompt,
                                 model=args.model, max_tokens=args.max_tokens,
                                 effort=args.effort)


def report(results, out_path) -> int:
    """results: [(row on record, JdExtraction or None, error or None)]."""
    moves = collections.Counter()
    drift = collections.Counter()
    changed, failed = [], []
    out = open(out_path, "w", encoding="utf-8") if out_path else None
    for r, x, err in results:
        if err is not None:
            failed.append((r["posting_id"], err))
            continue
        moves[(r["visa_signal"], x.visa_signal)] += 1
        if r["visa_signal"] != x.visa_signal:
            changed.append((r, x))
        if r["prompt_version"]:
            drift["languages"] += _norm(r["languages"]) != _norm(x.languages)
            drift["technologies"] += _norm(r["technologies"]) != _norm(x.technologies)
            drift["seniority"] += (r["seniority"] or "").lower() != (x.seniority or "").lower()
            drift["work_mode"] += r["work_mode"] != x.work_mode
        if out:
            out.write(json.dumps({
                "posting_id": str(r["posting_id"]), "user_id": str(r["user_id"]),
                "languages": x.languages, "technologies": x.technologies,
                "seniority": x.seniority, "salary_min": x.salary_min,
                "salary_max": x.salary_max, "currency": x.currency,
                "work_mode": x.work_mode, "visa_signal": x.visa_signal,
                "visa_notes": x.visa_notes, "model": x.model,
                "prompt_version": x.prompt_version}, ensure_ascii=False) + "\n")
    if out:
        out.close()

    print("\nvisa_signal, on record -> candidate:")
    for (a, b), n in sorted(moves.items(), key=lambda kv: (str(kv[0][0]), -kv[1])):
        print(f"  {str(a):12} -> {b:18} {n:4}{'' if a == b else '   *'}")
    print(f"\nother fields that moved (of {sum(moves.values())}): "
          + ", ".join(f"{k} {v}" for k, v in drift.items()))
    print(f"\nchanged ({len(changed)}):")
    for r, x in changed:
        print(f"  {r['visa_signal']} -> {x.visa_signal}")
        print(f"      was: {(r['visa_notes'] or '—')[:150]}")
        print(f"      now: {(x.visa_notes or '—')[:150]}")
    if failed:
        print(f"\nfailed ({len(failed)}):")
        for pid, err in failed:
            print(f"  {pid}: {str(err)[:120]}")
    return 1 if failed else 0


def replay(args) -> int:
    rows = _rows(args)
    client = llm.Client()
    print(f"replaying {len(rows)} job descriptions through {args.prompt} "
          f"on {args.model or config.JD_MODEL} ({args.workers} at a time)")

    def one(r):
        try:
            return r, _extract(client, r, args), None
        except Exception as err:            # noqa: BLE001 — counted, not fatal
            return r, None, err

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        return report(list(pool.map(one, rows)), args.out)


def batch_submit(args) -> int:
    rows = _rows(args)
    model = args.model or config.JD_MODEL
    effort = config.JD_EFFORT if args.effort == "config" else args.effort
    system = _load_prompt(args.prompt)
    reqs = [{"custom_id": str(r["posting_id"]),
             "params": {"model": model, "max_tokens": args.max_tokens or config.JD_MAX_TOKENS,
                        "system": system,
                        "messages": [{"role": "user",
                                      "content": jd_extraction.user_content(r["jd_text"], r["title"])}],
                        **({"output_config": {"effort": effort}} if effort else {})}}
            for r in rows]
    b = anthropic.Anthropic().messages.batches.create(requests=reqs)
    print(f"submitted {len(reqs)} postings through {args.prompt} on {model} "
          f"(effort {effort}) as batch {b.id}; collect with --batch-collect {b.id}")
    return 0


class _FirstFromBatch:
    """llm.Client's complete(): the first call returns the batch's answer, a
    repair retry calls live."""

    def __init__(self, text: str):
        self.first, self.live = text, llm.Client()

    def complete(self, **kw) -> str:
        if self.first is not None:
            text, self.first = self.first, None
            return text
        return self.live.complete(**kw)


def batch_collect(args) -> int:
    sdk = anthropic.Anthropic()
    b = sdk.messages.batches.retrieve(args.batch_collect)
    if b.processing_status != "ended":
        print(f"{b.id} is {b.processing_status}: {dict(b.request_counts)}")
        return 2
    by_id = {str(r["posting_id"]): r for r in _rows(args)}
    results = []
    for res in sdk.messages.batches.results(b.id):
        r = by_id.get(res.custom_id)
        if r is None:
            continue
        if res.result.type != "succeeded":
            results.append((r, None, RuntimeError(f"batch {res.result.type}")))
            continue
        text = "".join(c.text for c in res.result.message.content if c.type == "text")
        try:
            results.append((r, _extract(_FirstFromBatch(text), r, args), None))
        except Exception as err:            # noqa: BLE001 — counted, not fatal
            results.append((r, None, err))
    print(f"collected {len(results)} from {b.id}")
    return report(results, args.out)


def apply(path: str) -> int:
    decisions = [json.loads(line) for f in path.split(",")
                 for line in open(f, encoding="utf-8") if line.strip()]
    with db.connect() as conn, conn.transaction():
        for d in decisions:
            x = jd_extraction.JdExtraction(
                languages=d["languages"], technologies=d["technologies"],
                seniority=d["seniority"], salary_min=d["salary_min"],
                salary_max=d["salary_max"], currency=d["currency"],
                work_mode=d["work_mode"], visa_signal=d["visa_signal"],
                visa_notes=d["visa_notes"], model=d["model"],
                prompt_version=d["prompt_version"])
            jd_extraction.store(conn, d["user_id"], d["posting_id"], x)
    print(f"stored {len(decisions)} extraction rows from {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--prompt", default=jd_extraction.PROMPT_VERSION,
                    help="candidate prompt version (a prompts/<name>.txt)")
    ap.add_argument("--model", default=None, help="candidate model (default: config.JD_MODEL)")
    ap.add_argument("--effort", default="config",
                    help="effort for a Claude model that takes one (default: config.JD_EFFORT; "
                         "'' for none — Haiku 4.5 rejects it)")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="output cap (default: config.JD_MAX_TOKENS); on Sonnet 5 it covers "
                         "adaptive thinking too (.claude/rules/llm.md)")
    ap.add_argument("--limit", type=int, default=0, help="first N postings only (0 = all)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", help="write every decision here, one JSON object per line")
    ap.add_argument("--skip", action="append", default=[], metavar="JSONL",
                    help="leave out postings already decided in this file (repeatable)")
    ap.add_argument("--batch-submit", action="store_true",
                    help="send the first call of every posting through the Batch API")
    ap.add_argument("--batch-collect", metavar="BATCH_ID",
                    help="collect a submitted batch through extract, then report")
    ap.add_argument("--apply", metavar="JSONL[,JSONL]",
                    help="store the decisions in these files instead of replaying")
    args = ap.parse_args()
    if args.effort == "":
        args.effort = None
    if args.apply:
        return apply(args.apply)
    if args.batch_submit:
        return batch_submit(args)
    if args.batch_collect:
        return batch_collect(args)
    return replay(args)


if __name__ == "__main__":
    raise SystemExit(main())
