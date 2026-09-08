"""Replay stored, already-classified emails through a candidate model and diff
its decisions against the ones on record. READ-ONLY against the database.

The emails table is a labelled dataset this project has been building since
July 2026: every classified row carries the decision, the confidence and the
model that made it (invariant #5). Replaying those rows through another
backend — an open-weight model on vLLM, a cheaper Claude — answers the only
question a model swap here has to answer, "does it make the same calls on MY
mail", the same way scripts/replay_thresholds.py answers it for the matcher.
Never reason about a swap; replay it (docs/vllm-lab.md §4).

What it runs is exactly what the worker runs: email_classifier.classify_email
with the same prompt version, truncation and validation, only the model
chosen here. Rows whose body has been purged (INGEST_ALL drops the body of a
not_job_related message once it is ruled out) are skipped and counted — the
model would be seeing a different email from the one that was classified.

  uv run python scripts/replay_classify.py --model Qwen/Qwen3-8B \
      --base-url http://127.0.0.1:8001/v1 --api-key "$VLLM_API_KEY"
  uv run python scripts/replay_classify.py --model claude-haiku-4-5-20251001 --limit 50
  ... --workers 8          # concurrency: the same rows, batched by the server
  ... --out replay.jsonl   # every decision, one JSON object per line
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pipeline import config, db, email_classifier, llm  # noqa: E402


def stored_decision(classification: str) -> tuple[bool, str | None]:
    """emails.classification -> the (job_related, type) pair classify_email returns."""
    if classification == "not_job_related":
        return False, None
    return True, classification


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="model name to replay with")
    ap.add_argument("--base-url", default=None,
                    help="OpenAI-compatible server (default: TRACKER_LLM_BASE_URL)")
    ap.add_argument("--api-key", default=None,
                    help="bearer for that server (default: TRACKER_LLM_API_KEY)")
    ap.add_argument("--limit", type=int, default=0, help="first N rows only (0 = all)")
    ap.add_argument("--since", type=date.fromisoformat,
                    help="only emails received on or after YYYY-MM-DD")
    ap.add_argument("--workers", type=int, default=1,
                    help="concurrent requests (1 = the worker's own serial shape)")
    ap.add_argument("--out", type=Path, help="write every decision here as JSON lines")
    args = ap.parse_args()

    client = llm.Client(base_url=args.base_url, api_key=args.api_key)
    where = ["classification IS NOT NULL"]
    params: list = []
    if args.since:
        where.append("received_at >= %s")
        params.append(args.since)
    sql = ("SELECT id, sender, subject, received_at, body_text, classification, "
           "classify_confidence, model FROM emails WHERE " + " AND ".join(where)
           + " ORDER BY received_at" + (f" LIMIT {int(args.limit)}" if args.limit else ""))
    with db.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    purged = [r for r in rows if not (r["body_text"] or "").strip()]
    rows = [r for r in rows if (r["body_text"] or "").strip()]
    if not rows:
        print("nothing to replay", file=sys.stderr)
        return 1
    where_str = args.base_url or config.LLM_BASE_URL or "Anthropic"
    print(f"replaying {len(rows)} emails against {args.model} via {where_str} "
          f"({len(purged)} skipped: body purged) with {args.workers} worker(s)")

    def one(row):
        t0 = time.perf_counter()
        try:
            c = email_classifier.classify_email(
                client, row["sender"], row["subject"] or "", row["received_at"],
                row["body_text"] or "", model=args.model)
            got = (c.job_related, c.type)
            conf, reason, err = c.confidence, c.reason, None
        except llm.Unavailable:
            raise
        except Exception as e:  # a bad JSON after retry, a Refused — record, move on
            got, conf, reason, err = (None, None), None, None, f"{type(e).__name__}: {e}"
        return {
            "id": str(row["id"]), "received_at": row["received_at"].isoformat(),
            "subject": row["subject"], "stored": stored_decision(row["classification"]),
            "stored_model": row["model"], "stored_confidence": row["classify_confidence"],
            "replay": got, "confidence": conf, "reason": reason, "error": err,
            "seconds": round(time.perf_counter() - t0, 2),
        }

    t_start = time.perf_counter()
    results = []
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            for i, r in enumerate(pool.map(one, rows), 1):
                results.append(r)
                if i % 25 == 0 or i == len(rows):
                    print(f"  {i}/{len(rows)}", file=sys.stderr)
    except llm.Unavailable as e:
        print(f"\nSTOPPED — the model is unavailable: {e}", file=sys.stderr)
        return 2
    total = time.perf_counter() - t_start

    if args.out:
        with args.out.open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ok = [r for r in results if not r["error"]]
    errors = [r for r in results if r["error"]]
    jr_agree = sum(1 for r in ok if r["replay"][0] == r["stored"][0])
    both_job = [r for r in ok if r["replay"][0] and r["stored"][0]]
    type_agree = sum(1 for r in both_job if r["replay"][1] == r["stored"][1])
    confusion = collections.Counter(
        (r["stored"][1] or "not_job_related", r["replay"][1] or "not_job_related")
        for r in ok if r["replay"] != r["stored"])
    lat = sorted(r["seconds"] for r in results)

    def pct(a, b):
        return f"{a}/{b} ({100 * a / b:.1f}%)" if b else "n/a"

    print()
    print(f"job_related agreement: {pct(jr_agree, len(ok))}")
    print(f"type agreement, where both say job-related: {pct(type_agree, len(both_job))}")
    if confusion:
        print("disagreements, stored -> replay:")
        for (s, g), n in confusion.most_common():
            print(f"  {s:20} -> {g:20} {n}")
    if errors:
        print(f"errors: {len(errors)} (first: {errors[0]['error'][:120]})")
    if lat:
        p90 = lat[min(len(lat) - 1, int(0.9 * len(lat)))]
        print(f"latency: median {statistics.median(lat):.1f}s  p90 {p90:.1f}s  "
              f"total {total / 60:.1f} min  ({len(results) / total:.2f} emails/s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
