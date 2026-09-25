# JD extraction: which model, version and effort

**Author:** AZ
**Status:** Applied 25 Sep 2026: `config.JD_MODEL` is `claude-sonnet-5` at
`JD_EFFORT = "medium"`, `jd_extraction.PROMPT_VERSION` is `jd_extract_v2`, and
all 264 stored JDs were re-extracted under it (§10).
**Date:** 25 September 2026
**Scope:** The job-description extractor (`pipeline/jd_extraction.py`) under
prompt `jd_extract_v2`, judged on the field v2 exists for, `visa_signal`.
Other stages (email classify, email extract, cover letters) are out of scope.

---

## 1. Summary

**Use Claude Sonnet 5 at `effort: "medium"`** for JD extraction.

- **Accuracy:** 98.1% of 108 labelled answers, with no false signal on a
  posting that states nothing about visas.
- **Stability:** the same label on every posting across two runs, and the most
  stable technology lists of any model tried.
- **Cost:** about $6.90 per 1,000 JDs, under $1 a month at this search's
  volume.

Haiku 4.5, the current model, is 2.6 times cheaper per JD but invents
restrictions: 10 of its answers put a restriction on a posting that states
none. Opus 5.5 at `low` was the only configuration with a perfect score. Its
one extra correct answer is a posting whose right label is a judgement call,
and it costs twice as much. Effort barely moves Sonnet on this task, because
the task barely needs thinking.

The sweep cost about $1.95 against a $5 budget.

## 2. The question

`jd_extract_v1` asked for one visa verdict: `sponsors`, `local_only` or
`unclear`. On 25 Sep 2026, replaying it over all 264 stored JDs showed three
problems.

- **It folded different restrictions into `local_only`.** Examples: "citizens
  or PRs only", "sponsorship is not available", "must already be based in
  Singapore", and "we will be prioritising applicants with a current right to
  work".
- **It let the model infer.** About 6 of 27 `local_only` verdicts came from
  the employer's sector, for example "a government agency, typically
  citizens". About 11 of 17 `sponsors` verdicts came from "hiring in two
  countries".
- **It leaked the prompt's framing into answers.** One verdict reasoned from
  the prompt's own line that the user was "applying from abroad".

`jd_extract_v2` changes three things:

- It records only what the JD says, in the distinctions the author reads by:
  `citizens_pr_only`, `no_sponsorship`, `in_country`, `locals_preferred`,
  `sponsors`, `unclear`. `in_country` means the candidate must already be in
  the country; a work pass may still be sponsored, but there is no hiring from
  abroad. That distinction was the author's own.
- A code-level check: `visa_notes` must be a verbatim quote of the JD, or the
  signal becomes `unclear` ("no quote, no signal").
- The replay then showed Haiku following v2 poorly. It kept inferring
  citizenship rules for government listings, now backed by real but
  irrelevant quotes, and it read "This role is based on-site in Singapore" as
  `in_country`, a sentence the prompt names as `unclear`.

That raised the question this document answers: which model, version and
effort level should run v2.

## 3. Method

**Gold set.** Every posting where any run gave a non-`unclear` signal (46) —
v1 on record, two Haiku v2 runs, one partial Sonnet v2 run — plus 20 random
postings where every run said `unclear` (seeded, `random.seed(2609)`). Each was
labelled by hand from the JD's own sentences under v2's rubric, reading the
surrounding text where a sentence was ambiguous:

| Label | Postings | Examples of the deciding sentence |
|---|---|---|
| `no_sponsorship` | 12 | "Authorized to work in Singapore" (a requirements bullet), "employer sponsorship (work pass) is not available" |
| `in_country` | 6 | "Applicants MUST BE currently based in Singapore", "remote-first role, open to candidates based in Australia or Singapore" |
| `sponsors` | 7 | "Relocation Eligible: Yes", "visa sponsorship and immigration legal support" |
| `locals_preferred` | 2 | "We will be prioritizing applicants who have a current right to work" |
| `citizens_pr_only` | 1 | "Must be a Singapore Citizen or Singapore Permanent Resident" |
| `unclear` | 38 | 18 earlier guesses (sector, dual-country hiring, "open to all nationalities", "someone on the ground in Singapore") + 20 controls |

One posting ("IMPORTANT NOTE! This role is open only in Singapore") accepts
`unclear` as well as `in_country`. To fit the budget, the sweep ran on the 46
plus the first 8 controls, 54 postings.

**Harness.** Each configuration ran the real `jd_extraction.extract`, so the
v2 prompt, validation, the quote check and the repair retry all ran as in
production. It ran behind a client that records usage and latency per call.
Only the model, `thinking`, `effort` and `max_tokens` varied.

- **Batch API.** The first call of each (config, trial, posting) went through
  the Batch API at half price, 486 requests. The answer was then fed back
  through `extract`, and any repair call ran live and was metered.
- **Free extra trials.** Two earlier full runs with the same prompt, Haiku and
  a partial Sonnet-high run, were scored as extra trials at no cost.
- **Costs** below are what production would pay, at standard prices and on
  measured tokens.

**Stability.** For each posting, whether its two runs gave the same label, and
the overlap (Jaccard) of the technology lists the two runs extracted. The
technology list feeds `/analytics`' technology comparison.

**Configurations.**

| Config | Model | Settings |
|---|---|---|
| haiku | Haiku 4.5 | no thinking (as in production) |
| sonnet_nothink | Sonnet 5 | `thinking: disabled` |
| sonnet_low / _medium / _high | Sonnet 5 | adaptive thinking, `effort` low / medium / high (default) |
| opus55_low | Opus 5.5 | `effort: low` (thinking cannot be turned off) |

Left out to stay under budget: Haiku with a thinking budget, Opus 5.5 at
`medium`, and Fable 5.1, which would cost about $4.40 for a single pass.

## 4. Results

| Config | Answers | Correct | Correct on restricted | False signals | Same label, run to run | Tech overlap | Input tok | Output tok | $ / 1,000 JDs |
|---|---|---|---|---|---|---|---|---|---|
| haiku | 107 | 88.8% | 96.4% | **10** | 92% | 0.83 | 1,832 | 154 | 2.60 |
| sonnet_nothink | 108 | 98.1% | 96.4% | 0 | 98% | **0.94** | 2,695 | 154 | 6.93 |
| sonnet_low | 108 | 98.1% | 96.4% | 0 | 98% | 0.89 | 2,695 | 147 | 6.86 |
| **sonnet_medium** | 108 | **98.1%** | 96.4% | **0** | **100%** | 0.93 | 2,695 | 148 | **6.87** |
| sonnet_high | 100 | 98.0% | 96.2% | 0 | 96% | 0.85 | 2,695 | 317 | 8.56 |
| opus55_low | 54 | 100% | 100% | 0 | one run | one run | 2,697 | 166 | 14.11 |

"Correct on restricted" is accuracy on the postings whose gold label is not
`unclear`. "False signals" are answers giving a restriction or `sponsors` to
a posting whose gold label is `unclear`.

**Haiku's misses are reproducible, not noise:**

- citizenship inferred for a government agency and its subsidiary (2 postings,
  both runs);
- "open to all nationalities" read as `sponsors` (both runs);
- "based in Singapore, or open to relocating (relocation support provided)"
  read as `in_country` (both runs);
- plus one-off reads of a company's own location, and of hiring in two
  countries, as signals.

**Every Sonnet configuration misses the same one posting:** "This is a
remote-first role, open to candidates based in Australia or Singapore." Sonnet
reads it as a work arrangement (`unclear`); the gold label, and Opus 5.5, read
it as a restriction on where the candidate is (`in_country`). If that posting
is treated as ambiguous, Sonnet scores 100% too.

## 5. What the numbers say

1. **Effort is not the lever here; the model is.** At thinking off, `low` and
   `medium`, Sonnet writes about 150 output tokens: the JSON and little or no
   thinking. Adaptive thinking judges the task easy. `high` doubles output to
   317 tokens, costs 25% more, and gains nothing. At the lower levels the
   workload is input-dominated: 2,695 input tokens against about 150 output,
   so about 78% of the cost is input. The Anthropic effort guidance expects
   flat curves on work like this. This is the same lesson as email classify
   (memory: never assume a middle setting is safe, and measure whether input
   or output dominates first). Here the middle setting was measured, not
   assumed, and came out the most stable.
2. **The failure that matters is the false signal.** A wrong `unclear` hides a
   restriction the author can still read in the JD. A wrong restriction puts a
   tag on the list that the JD does not support, and the tag is what the list
   shows. Haiku made 10 such claims in 107 answers; Sonnet made none in 424.
3. **Tokenizer and price.** Sonnet 5 and Opus 5.5 use the newer tokenizer:
   2,695 input tokens where Haiku counts 1,832 for the same JDs, 47% more on
   this text, above the documented ~30%. Per JD, Sonnet costs 2.6 times
   Haiku, not the 2 times the price list suggests.
4. **Stability is a side benefit worth having.** Technology lists overlap 0.93
   between Sonnet-medium runs against 0.83 for Haiku. An earlier probe found
   Haiku's own v1 moving the technology list on 5 of 10 postings between runs.
   Steadier lists mean steadier technology rows on `/analytics`.
5. **The sweep found a defect in our own check, not only in the models.** Some
   captured JDs have stray spaces inside words ("Singapor e", "nee dsYou").
   A model that quoted the sentence correctly ("…in Singapore.") failed the
   verbatim check, was sent back for repair, and was downgraded to `unclear`.
   It happened on every Sonnet run of that posting, and first looked like
   Sonnet's worst miss. `jd_extraction.quoted_in` now ignores all whitespace;
   the 10 affected answers were re-run with the fix.

## 6. Recommendation

| Choice | Decision | Why |
|---|---|---|
| Model | **Claude Sonnet 5** (`claude-sonnet-5`) | Zero false signals where Haiku made 10. Half the cost of Opus 5.5 low, whose only extra correct answer is a judgement call. |
| Effort | **`medium`**, set explicitly | Accuracy equal to `low` and `high`, the only level with identical labels on both runs, and among the most stable technology lists. Same cost as `low`. |
| Thinking | Adaptive (the default), not disabled | `thinking: disabled` scored the same, but `.claude/rules/llm.md` records why this pipeline does not disable thinking on Sonnet 5: it can leak `<thinking>` tags into the visible answer, and `_strip_fences` would then dead-letter the job. Nothing is saved. |
| `max_tokens` | **4000** | On Sonnet 5 it covers thinking and answer together. The largest measured output was far below it, and an unused cap costs nothing. |
| Prompt | `jd_extract_v2` | As replayed, with the whitespace-insensitive quote check. |

**What applying it takes:**

- `config.JD_MODEL` becomes `claude-sonnet-5`, with the measurement in a
  comment beside the constant (invariant #5).
- `jd_extraction.PROMPT_VERSION` becomes `jd_extract_v2`.
- `extract` passes `max_tokens=4000`.
- `llm.AnthropicBackend.complete` learns an opt-in `effort`. Its comment
  explains why it sends no `output_config` today: the other stages' calls were
  measured without it, so they must keep sending none.

**The one-time backfill** of 264 JDs costs about $1.80 at standard prices, or
$0.90 through the Batch API.

## 7. Cost at this search's volume

About 30 new postings a week are extracted:

| Config | Per 1,000 JDs | Per month (~130 JDs) |
|---|---|---|
| Haiku 4.5 | $2.60 | $0.34 |
| Sonnet 5 medium | $6.87 | $0.89 |
| Opus 5.5 low | $14.11 | $1.83 |

At this volume none of these is a cost decision. It becomes one only for a
self-hoster extracting thousands of JDs, who should read §4 before choosing
Haiku.

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The gold set was labelled by one annotator | A mislabel moves a configuration's score by ~1 point | The ambiguous cases are named in §3–4; the author can overrule any label and re-score without new calls (`jd_eval.py --report`) |
| 54 postings, 1–2 trials | Differences under ~2 points are noise | Only the claims the numbers support are made: Haiku's reproducible false signals; the Sonnet levels are indistinguishable |
| Opus 5.5 low ran once | Its 100% may not hold | It is not recommended; only its cost and the one disputed posting are relied on |
| Fable 5.1 untested | The ceiling is unknown | Not needed: Sonnet's only disagreement is a judgement call |
| A future model or prompt change | The curve moves | Re-run the sweep (the harness and gold labels are kept, §9) after any change to `jd_extract_*` or `JD_MODEL` |
| Captured JDs with broken whitespace | Other verbatim checks may trip the same way | The check now ignores whitespace; the capture artefact itself is an extension issue, not fixed here |

## 9. Open questions

1. **Should the Australia-or-Singapore posting be `in_country`?** For an
   applicant already in Singapore it changes nothing. The label decides only
   whether a grey tag shows.
2. **Should "Authorized to work in Singapore" count as `no_sponsorship`?** It
   is a requirements bullet on many listings (12 in the gold set). A pass
   holder may already satisfy it. The rubric says `no_sponsorship`, and the
   author's reading decides.
3. **Where the harness lives.** The sweep scripts (`jd_eval.py`,
   `jd_batch.py`) and `gold.json` were built in the session's scratch
   directory. `gold.json` names real postings by id, so it stays outside the
   repository. If the sweep is to be re-run after a model change, the scripts
   belong in `scripts/` beside `replay_jd.py`.

## 10. Applied, and what the backfill showed

Applied the same day. The model switch, v2, migration 017 widening the
`visa_signal` CHECK, and the list's grey visa tag. The backfill of all 264
stored JDs ran as one batch (`scripts/replay_jd.py --batch-submit` /
`--batch-collect`, about $0.90) and was written with `--apply`. The v2 rows sit
beside the 263 v1 rows, which remain.

- **Against the gold labels:** 65 of 66 agree. The one disagreement is the
  Australia-or-Singapore posting of §9.1, the same reading on every Sonnet run.
- **Where the signals landed:** 238 `unclear`, 12 `no_sponsorship`, 7
  `sponsors`, 4 `in_country`, 2 `locals_preferred`, 1 `citizens_pr_only`.
  Every non-`unclear` answer is on a gold posting. No new signal appeared
  outside the set the evaluation had read.
- **Against LinkedIn's screens:** of the 12 rejections LinkedIn's
  sponsorship screen closed, only 4 were on JDs that said `no_sponsorship`;
  8 said nothing. The knockout mostly lives in the apply form, not the JD.
  So the form answer (`answers.declares_sponsorship`) is the stronger
  predictor, and the JD's tag the earlier, weaker one.

## 11. Sources

- Anthropic, [Pricing](https://platform.claude.com/docs/en/about-claude/pricing),
  read 25 Sep 2026. Per-MTok prices: Haiku 4.5 $1/$5, Sonnet 5 $2/$10 (now
  standard; the planned 1 Sep rise to $3/$15 was cancelled), Opus 5.5 $4/$20,
  Fable 5.1 $10/$50. Batch API 50% off. The newer tokenizer on 4.7+ models
  counts ~30% more tokens.
- Anthropic, [Effort](https://platform.claude.com/docs/en/build-with-claude/effort),
  read 25 Sep 2026. `high` is the default except on Opus 5.5 (`medium`). For
  Sonnet 5, `medium` is "comparable to Claude Sonnet 4.6 at high effort", and
  `low` is for "high-volume or latency-sensitive workloads".
- Anthropic, the claude-api reference (cost optimisation, § effort and model
  selection): price per completed task on your own traffic, sweep effort
  before changing model, include the larger model at lower effort.
- `docs/worklog.md` tasks 31–33; `.claude/rules/llm.md` (thinking on Sonnet 5,
  input- vs output-dominated workloads).
