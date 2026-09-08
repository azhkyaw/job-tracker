---
paths:
  - "pipeline/llm.py"
  - "pipeline/email_classifier.py"
  - "pipeline/jd_extraction.py"
  - "pipeline/covers.py"
  - "pipeline/worker.py"
  - "pipeline/embeddings.py"
  - "prompts/**"
  - "tests/test_llm.py"
  - "scripts/replay_classify.py"
  - "docs/vllm-lab.md"
---

# LLM stages: models, thinking, cost, outages

Moved here VERBATIM from CLAUDE.md on 9 Sep 2026 so it loads when Claude reads a
matching file instead of in every session (it can also be Read directly).
Dates are the key to each case; the record itself is in the database. The
prompt/model versioning invariant (#5) and the worker invariant (#7) are still in CLAUDE.md.

## Gotchas learned the hard way

- **Omitting `thinking` means DIFFERENT things on Haiku and Sonnet 5, and this
  pipeline omits it everywhere.** All four Anthropic stages call
  `messages.create(model, max_tokens, system, messages)` and set no `thinking`,
  no `output_config.effort`, no sampling params. On Haiku 4.5 that means no
  thinking at all. On Sonnet 5 it means **adaptive thinking is ON at the
  default `high` effort**, and `max_tokens` caps thinking PLUS response text
  together — so moving a stage to Sonnet silently changes what its existing
  `max_tokens` is budgeting for. Measured on classify (4 Aug 2026, 10 random
  real emails): output 47 min / 66 mean / 199 max, a thinking block on 1 of 10,
  nothing truncated — but that one run had spent 66% of the old 300 cap on a
  ~60-token JSON answer. Caps raised to 1500 (classify) and 4000 (cover) for
  headroom; output is billed on what is generated, so an unreached ceiling is
  free. **Never reclaim headroom by disabling thinking on Sonnet 5** — it can
  leak `<thinking>` tags into the visible response, and `_strip_fences()`
  strips code fences ONLY, so a leaked tag fails `json.loads`, fails the repair
  retry identically, and dead-letters.
- **`effort` is not a cost lever on email classify — the workload is
  INPUT-dominated**, and lowering it is not monotonically safe. Measured at
  low/medium/high over 12 real emails: mean output 51/50/52 tokens, i.e. a
  2-token spread, against ~3,810 INPUT tokens per call. Output is ~7% of
  per-call cost, so tuning effort tunes the 7%. Worse, `medium` was the ONE
  level that got a known-answer case wrong (one employer's "Verify your candidate
  account" mail — `job_related/other` where low and high both correctly said
  not-job-related), so "medium as a safe middle" is a bad guess here. `low`
  matched `high` 12/12, but with no cost upside there is no reason to move off
  the default. If classify cost ever needs cutting, the lever is input —
  `STAGE1_BODY_CHARS`, or caching the system prompt — not effort.
- **A billing error is not the job's fault, and the queue charged every job
  for it anyway** (3-7 Sep 2026). The Anthropic account ran out of credit at
  4 Sep 00:25 SGT, mid-run. Sync kept storing mail; the worker retried every
  classify/extract job against `400 invalid_request_error: Your credit balance
  is too low…`, three of them to one attempt short of dead-lettering in the
  four minutes it ran, and no page said anything — the nav badge counts emails
  already IN triage, `sync` exits non-zero only on mailbox failures, and
  nothing read `job_queue` but the cover-letter panel. A rejection sat stored
  and unread until the user noticed it by absence. Reported as "job update
  emails not picked up"; the diagnosis was `emails.processed_at IS NULL`, then
  `job_queue.last_error` — check those two before suspecting the pre-filter or
  the matcher, since "stored but unprocessed" and "never fetched" are
  different bugs with the same symptom. Three things came out of it.
  `worker._outage()` classifies a failure as ENVIRONMENT (network,
  401/403/429/5xx, and the credit 400 — the one case the SDK's classes cannot
  express, told apart by its message) or JOB; an outage leaves the job
  untouched — attempts, state AND `run_after`, because deferring the oldest
  job would let a newer rejection process before an older confirmation, the
  Gmail-ordering bug all over again — and `run()` pauses instead.
  `db.queue_health()` is ONE definition of "stuck", read by the header band
  on every page (a Jinja global, `queue_alert()`, so the eleven
  context-building routes need not each remember it), by the Settings
  Pipeline section (dead jobs with their last line, a requeue button) and by
  `cli status`; stall = work older than `QUEUE_STALL_SECONDS` on the DATABASE
  clock (the dev DB is remote — a host-clock comparison would be a function of
  skew) or anything dead, so ordinary retry backoff never trips it. And the
  requeue is the general safety net for whatever `_outage` misclassifies: a
  job that died for a reason that was never its own is one click from running
  again. **Recovery verified the same night** (8 Sep 2026, ~01:00 SGT): the
  top-up and one `work --once` drained all 46, the rejection auto-matched
  at 0.895 and the application reads `rejected`, the band is gone, and the
  three extract jobs that had been at 4 of 5 attempts finished on their
  fifth — one more failed run under the old policy would have dead-lettered
  an interview invite.


## Known-untested surfaces (verify on first real contact)

- **Voyage embeddings live call** (`pipeline/embeddings.py`, ~25 lines) —
  never executed against the real API; verify model name/dimension (schema is
  `vector(1024)`) on first use.
