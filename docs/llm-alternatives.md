# Other LLM APIs against the Claude stages

**Author:** AZ
**Status:** Research. One change applied, 3 Oct 2026: after §10's first
replay (§13), `CLASSIFY_MODEL` is `claude-sonnet-5-5`. JD extraction and the
rejection reason stay on Sonnet 5 (one reproducible miss each). Nothing else
changed.
**Date:** 3 October 2026
**Scope:** The five language-model stages (`pipeline/email_classifier.py`,
`jd_extraction.py`, `covers.py`) and the embedding stage, measured against
the Anthropic models they run on today. Covers Anthropic's own newer models,
OpenAI, Google Gemini, xAI, Mistral, Cohere, open-weight models through their
first-party APIs and through hosts (OpenRouter, Groq, Together, Fireworks,
DeepInfra, Cerebras), and embedding APIs. Self-hosting on a GPU is
`docs/vllm-lab.md`'s subject and appears here only as a shortlist (§4.3).

**How to read the evidence.** As in `docs/monetization-review.md`:

- **[P]** primary: the vendor's own pricing, model or policy page
- **[S]** secondary: a leaderboard, a blog, a forum thread
- **[M]** measured on this install (the dev database, `docs/worklog.md`,
  `.claude/rules/llm.md`, `docs/jd-extraction-models.md`)
- **[E]** our estimate or arithmetic on sourced numbers

**Limits of this pass.** Four research agents read vendor pages on
3 Oct 2026 through a summarising fetch; §14 marks the pages re-read at source
for this document. Several of the models are days old (GPT-6.1 Sol was
released 29 Sep, Sonnet 5.5 on 28 Sep), and prices move monthly. No model
was called: every quality statement below is a vendor's or a leaderboard's,
and none of them measures this project's task.

---

## 1. Summary

**Cost is not a reason to switch. At this volume the whole model bill is
about US$5.35 a month [E], and the cheapest credible alternatives would do
the same work for US$0.30–1.10.** Saving five dollars a month does not pay
for re-validating five prompts. Three other reasons might justify the work,
and none of them is urgent:

1. **An outage fallback.** The 4–8 Sep 2026 credit outage held the queue for
   four days [M]. A second provider behind the existing OpenAI-compatible
   backend is a configuration change, once three small code changes land
   (§8).
2. **Bring-your-own-provider for the open-source release.** A self-hoster
   without an Anthropic key needs a documented route. OpenRouter (§5) is the
   one route that puts many vendors behind a single base URL, which is all
   `llm.py` supports.
3. **The Anthropic side is moving.** Sonnet 5.5 shipped on 28 Sep at Sonnet
   5's price [P]. Haiku 4.5 is active, "not sooner than" 15 Oct 2026, with no
   deprecation notice, and Haiku 5.5 is announced but not priced [P].

The other findings:

- **The cheapest move to test is inside Anthropic.** Sonnet 5.5 costs the
  same per token as Sonnet 5, and Artificial Analysis scores it higher at
  medium effort (41) than Sonnet 5 at max (38) [S]. Replay it on classify and
  on the JD gold set before anything else. `claude-sonnet-5` is listed as
  active until at least 30 Jun 2027 [P], so there is no deadline.
- **On general benchmarks, much cheaper models now sit at Sonnet 5's level.**
  GPT-6 Luna, DeepSeek V4.1 Flash, Gemini 3.8 Flash, Qwen3.8 Flash and
  GLM-5.3-Flash score 38–42 on Artificial Analysis' index, against Sonnet 5's
  38, at between a third and a twentieth of its blended price [S]. Two
  caveats:
  - Those scores are at maximum reasoning effort. With reasoning off the
    same models are Haiku-tier (GPT-6 Luna: 22 at low).
  - The index does not measure quote-backed extraction from job email. This
    project has already seen a leaderboard point the wrong way: Vectara's
    hallucination leaderboard ranks Haiku 4.5 better than Sonnet 4.5 [S],
    yet on this data Haiku invented visa restrictions on 10 of 107 answers
    where Sonnet 5 invented none [M]. Replay, as always.
- **`llm.py`'s OpenAI-compatible backend reaches some providers today and
  breaks on others** (§5):
  - **Works unchanged** (with the right `TRACKER_LLM_EXTRA_BODY`): Mistral,
    Groq, DeepInfra, Fireworks, OpenRouter and Gemini's Flash-Lite models,
    and probably xAI.
  - **Needs a code change:**
    - GPT-6.1 Sol. `temperature: 0` is always sent after the extra body, and
      OpenAI says to remove it whenever reasoning is on [P], which on that
      model is always.
    - Gemini 3.8 Flash, where Google warns that low temperatures cause
      looping [P].
    - Possibly every OpenAI model, if `max_tokens` is refused there as it
      was on GPT-5.4 [S]. GPT-6 Luna itself takes temperature once
      reasoning is set to `none`.
  - **Treats running out of credit as the job's fault:** a 402 (DeepSeek,
    OpenRouter) or a 408 counts as a job failure and dead-letters it after
    five attempts. That is the 4 Sep failure again, on a new backend [M].
- **Ruled out for personal job mail:**
  - **Gemini's free tier.** Google marks its prompts "Used to improve our
    products: Yes" [P].
  - **DeepSeek's first-party API.** Its privacy policy says data is stored in
    mainland China and used for training unless you opt out [P]. The same
    open weights run on US hosts with no retention.
  - **Cohere.** It costs about as much as Sonnet and gives nothing in return.
- **No vendor processes the current top models in Singapore** on a standard
  API. Anthropic, OpenAI, Google and xAI all route globally or offer only
  US or EU regions [P]. The exceptions are Azure's APAC Data Zone (GPT-5.6,
  GA 9 Jul 2026) and Alibaba Model Studio's Singapore region (Qwen).

## 2. The workload, measured

Volumes are the search from 16 Jul to 30 Sep 2026, scaled to a month (×0.395)
[M, `docs/monetization-review.md` §8.1]. Tokens per call are Claude tokens
[M, `.claude/rules/llm.md`, `docs/jd-extraction-models.md` §4] or estimated
from the measured cost per call [E].

| Stage | Model now | Calls/month | Input/call | Output/call | $/month [E] | Share |
|---|---|---|---|---|---|---|
| Classify | Sonnet 5 | ~488 | 3,810 | ~66 | 4.04 | 75% |
| Extract | Haiku 4.5 | ~221 | ~1,400 | ~120 | 0.44 | 8% |
| JD extraction | Sonnet 5, effort medium | ~118 | 2,695 | 148 | 0.81 | 15% |
| Rejection reason | Sonnet 5 | ~20 | ~1,000 | ~70 | 0.05 | 1% |
| Cover letter | Sonnet 5 | rare | ~5,000 | ~500 | — | — |
| **Total** | | | **~2.51M** | **~78k** | **~5.35** | |

**Correction, the same day (§13).** Counting tokens on the 182 classified
emails that still keep a body gave a classify mean of **1,404–1,470 input
tokens**, not the 3,810 above, which came from 12 emails on 4 Aug and could
not be reproduced. The mail that is not about jobs (more than half of the
calls) has had its body deleted and cannot be measured, so the true mean is
unknown. Read classify's row, the total and §7 as an upper bound: at 1,470
tokens classify is about US$1.76 a month and the total about US$3.10. The
conclusion, that cost is not a reason to switch, only gets stronger.

What this table decides:

- **The bill is classify's.** Three quarters of it is one stage, and more
  than half of classify's calls (676 of 1,235) were mail that turned out not
  to be about jobs, the price of `TRACKER_INGEST_ALL` [M]. Turning the
  pre-filter back on would halve classify's cost before any model change.
- **It is input-dominated.** Output is about 15% of spend. Output-side
  levers (effort, shorter answers) tune that 15%. The project measured
  effort on classify and found a two-token spread [M].
- **Prompt caching does not help here.** Classify's system prompt is 2,868
  characters, roughly 700–950 tokens [E]. That is below Sonnet 5's minimum
  cacheable length of 1,024 tokens [P], and the email is different on every
  call. Traffic is also sparse (about 16 classify calls a day), so even
  Sonnet 5.5's 512-token minimum would rarely see a hit inside a 5-minute
  cache. Caching would matter only during a backfill.

What the stages need from any model:

- One JSON object in a closed vocabulary, validated in code, with one repair
  turn.
- A verbatim quote for the two claims (the rejection reason and the JD's
  visa signal), checked by `quotes.quoted_in`.
- No inference beyond the text. This is where Haiku 4.5 failed on JDs.
- Personal data handled under a no-training, short-retention policy.
- No latency requirement: the worker is asynchronous, so a slow reasoning
  model is acceptable.
- One OpenAI-compatible base URL at a time, plus Anthropic (§5).

## 3. Anthropic's own options

Prices are US$ per million tokens [P, pricing page, re-read 3 Oct].

| Model | ID | Input | Cache read | Output | Batch in/out | Retirement, not sooner than |
|---|---|---|---|---|---|---|
| Sonnet 5.5 (28 Sep 2026) | `claude-sonnet-5-5` | 2 | 0.20 | 10 | 1 / 5 | 28 Sep 2027 |
| Sonnet 5 (now) | `claude-sonnet-5` | 2 | 0.20 | 10 | 1 / 5 | 30 Jun 2027 |
| Haiku 4.5 (now) | `claude-haiku-4-5-20251001` | 1 | 0.10 | 5 | 0.50 / 2.50 | 15 Oct 2026 |
| Opus 5.5 | `claude-opus-5-5` | 4 | 0.20 | 20 | 2 / 10 | 22 Sep 2027 |

- **Sonnet 5's US$2/US$10 is now permanent.** The increase to US$3/US$15
  planned for 1 Sep "will not occur" [P].
- **Haiku 4.5's date is a floor.** Anthropic promises at least 60 days'
  notice before retiring a model [P], and none has been given. A notice
  today would mean early December at the soonest [E].
- **Haiku 5.5 is announced, not priced.** It will join "in the coming
  weeks", stated on 22 and 28 Sep [P, via agent]. When it lands it is the
  natural candidate for extract, and for classify if it matches Sonnet on
  the ATS account-mail case (`.claude/rules/llm.md`).
- **Tokenizer.** Claude 4.7 and later models count about 30% more tokens
  for the same text than Haiku 4.5 does [P]. Per-token prices are therefore
  not directly comparable across that line.
- **Region.** The Claude API offers only `inference_geo: "global"` or `"us"`.
  Bedrock in `ap-southeast-1` has a global endpoint only, and Vertex has
  `us` and `eu` multi-regions only [P, via agent]. None of the three can
  pin processing to Singapore.
- **Retention.** Anthropic does not train on API data without permission.
  Its policy deletes inputs and outputs within 30 days, and longer for
  flagged content [P, via agent].

## 4. The other vendors, by tier

Artificial Analysis Intelligence Index v4.3.2 is quoted with the effort level
it was measured at [S]. It is built from agentic and knowledge evaluations,
not extraction. For calibration: Sonnet 5 (max) 38, Haiku 4.5 (no reasoning,
estimated) 15.

### 4.1 Haiku-tier: extract, and a cheaper classify

| Model | Vendor | $ in / out per MTok | Index | Notes |
|---|---|---|---|---|
| `gpt-6-luna`, reasoning `none` | OpenAI | 0.10 / 0.50 [P] | 22 (low) | Cached input 0.01. Released 22 Sep 2026 |
| `gemini-2.5-flash-lite` | Google | 0.10 / 0.40 [P] | — | Thinking off by default; no shutdown date |
| `gemini-3.1-flash-lite` | Google | 0.25 / 1.50 [P] | — | Earliest shutdown 7 May 2027 |
| `gemini-3.5-flash-lite` | Google | 0.30 / 2.50 [P] | 22 | Minimal thinking by default |
| `mistral-small-2603` (Small 4) | Mistral | 0.15 / 0.60 [P, via agent] | 11 | Reasoning off by default; EU hosting |
| `gpt-oss-120b` | Groq (open weights) | 0.15 / 0.60 [P, via agent] | 12 | Reasoning cannot be switched off; batch 50% |
| `qwen3.7-plus` | Alibaba, Singapore | 0.40 / 1.60 [P, via agent] | 25 | Data stored in Singapore |

### 4.2 Sonnet-tier: classify, JD extraction, the rejection reason

| Model | Vendor | $ in / out per MTok | Index | Notes |
|---|---|---|---|---|
| `claude-sonnet-5-5` | Anthropic | 2 / 10 [P] | 41 (medium), 56 (max) | Same price as now |
| `gpt-6.1-sol` | OpenAI | 2 / 10 [P, via agent] | 52 (max) | No `none` effort, so `temperature` is always refused |
| `gemini-3.8-flash` | Google | 0.75 / 3.75 until 31 Dec 2026, then 1.50 / 7.50 [P] | 41 (high) | Thinking cannot go below `low`; temperature 0 discouraged |
| `deepseek-flash` (V4.1 Flash) | DeepSeek, or US hosts | 0.30 / 1.20 [P]; same on Together and Fireworks | 39 (max) | MIT weights; thinking on by default |
| Qwen3.8 Flash | Alibaba | 0.15 / 0.47 [P, via agent] | 40 | 6B active parameters |
| GLM-5.3-Flash | Z.ai, Together, Fireworks | 0.15 / 0.50 [P, via agent] | 42 | MIT weights |
| `gpt-6-luna`, reasoning `max` | OpenAI | 0.10 / 0.50 [P] | 38 | Reasoning billed as output; 110–280 s to first token at max [S] |
| `grok-4.7` | xAI | 2 / 6 [P, via agent] | — | No Asia region |
| `mistral-medium-3-5` | Mistral | 1.50 / 7.50 [P, via agent] | — | Reasoning off by default |

What the two tables share: **every cheap model that reaches Sonnet 5's score
does it by reasoning.** That has three consequences here:

- The reasoning tokens are billed as output, which turns this
  input-dominated workload into one where output matters.
- The reasoning counts inside `max_tokens`. Caps sized for Claude's answers
  (500 for extract) would be spent on thinking, leaving the JSON empty.
- Each provider names its reasoning switch differently.

### 4.3 Self-host shortlist, one 24 GB GPU (pointer only)

For `docs/vllm-lab.md`. Weight sizes are our arithmetic [E]:

| Model | Weights on an L4 | Status |
|---|---|---|
| Gemma 4 12B, FP8 | ~12 GB | Fits |
| Qwen3.5-9B | 9–18 GB | Fits. Together and DeepInfra host it, so it can be replayed before a VM exists |
| Qwen3.6-35B-A3B, Qwen's official Int4 | 18–20 GB | Tight fit |
| Qwen3.8-27B, community 4-bit | 15–17 GB | The best quality on this list. Groq hosts it, so it can be replayed there first |
| gpt-oss-20b | ~13 GB | vLLM's support for the L4's GPU generation is still in progress [P, via agent] |

## 5. Will it plug in? `llm.py` as it stands

`OpenAICompatible.complete` sends this body (`pipeline/llm.py:94-109`):

```
{"model", "messages": [system, user], "max_tokens": N, **TRACKER_LLM_EXTRA_BODY,
 "response_format": {"type": "json_object"}, "temperature": 0}
```

The last two keys go only on the JSON stages, and they are set *after* the
extra body, so configuration cannot override or remove them. Status codes
401, 403, 404, 429 and 5xx count as an outage; every other 4xx counts as the
job's fault and dead-letters it after five attempts. All six JSON prompts
already contain the word "JSON", which DeepSeek, Alibaba and OpenAI each
require in JSON mode [M].

| Provider | Works today? | What breaks | Setting or fix |
|---|---|---|---|
| Mistral | Yes | Nothing found | None. Reasoning is off by default |
| Groq | Yes | gpt-oss reasoning cannot be turned off | `{"reasoning_effort": "low"}` plus larger caps; Qwen3.8-27B takes `"none"` |
| DeepInfra | Yes | — | — |
| Fireworks | Yes | Without a JSON instruction the model can emit whitespace to the cap; ours have one | — |
| OpenRouter | Yes | 402 (balance), 408 and a 200 carrying only an error object are filed as the job's fault | `{"provider": {"require_parameters": true, "zdr": true, "data_collection": "deny", "ignore": ["deepseek"]}, "reasoning": {"enabled": false}}` |
| Gemini 2.5 Flash-Lite, 3.x Flash-Lite | Probably | Thinking (minimal) counts inside the cap; Google's page does not document `max_tokens` or `json_object`, though Vertex's copy does | Larger caps; one probe call |
| Gemini 3.8 Flash | No | `minimal` effort is an error; temperature 0 "can cause looping" on Gemini 3 [P, via agent] and cannot be overridden | `{"reasoning_effort": "low"}`, plus code change 2 |
| Vertex (Gemini) | No | Authenticates with a one-hour OAuth token, not a fixed key | Code change: token refresh |
| xAI `grok-4.3`, `grok-4.7` | Probably | `max_tokens` deprecated but documented; temperature 0 on reasoning not confirmed | One probe call |
| OpenAI `gpt-6-luna`, `gpt-6-sol` | No | `temperature` must be removed unless effort is `none` [P]; `max_tokens` may be refused for `max_completion_tokens` [S, GPT-5.4] | `{"reasoning_effort": "none"}` handles temperature; code change 3 if `max_tokens` is refused |
| OpenAI `gpt-6.1-sol` | No | No `none` effort, so `temperature` is always refused | Code change 2 |
| DeepSeek, first party | Yes, technically | 402 on an empty balance; thinking on by default | `{"thinking": {"type": "disabled"}}`. Excluded on data grounds (§6) |
| Alibaba, Singapore | Yes | A prompt without the word "json" is a 400 (ours have it) | `{"enable_thinking": false}` |
| Together | Unknown | `json_object` is not documented | One probe call |

**One base URL.** Every non-`claude-` model goes to the same
`TRACKER_LLM_BASE_URL`. Mixing two non-Claude vendors (say Gemini for classify
and OpenAI for JDs) is impossible without a code change, or without OpenRouter,
which puts both behind one URL for a 5.5% fee on card top-ups and no markup
on tokens [P, via agent].

## 6. Where the data goes

| Provider | Trains on API data? | Retention | Processing region | Verdict |
|---|---|---|---|---|
| Anthropic | No, without permission | 30 days; longer if flagged | Global or US | Current |
| OpenAI | No, unless you opt in | 30 days of abuse logs; ZDR by sales approval | US or EU; Singapore stores at rest only, by approval | Acceptable |
| Google Gemini, paid | No ("Used to improve our products: No") [P] | 55 days of abuse monitoring, which staff may review | Not selectable on the Developer API | Acceptable |
| **Google Gemini, free** | **Yes** [P] | Human review possible | — | **Excluded** |
| xAI | No, without permission | 30 days; ZDR is a self-serve team setting | Global or US | Acceptable |
| Mistral | Opt-out toggle; the pay-as-you-go default is not stated | 30 days | EU | Acceptable after setting the toggle |
| **DeepSeek, first party** | **By default, with an opt-out** [P] | — | **Mainland China** [P] | **Excluded** |
| Alibaba, Singapore | No, per its FAQ | — | Data stored in Singapore | Acceptable; Chinese parent company |
| Groq, Fireworks, DeepInfra | No | Nothing kept by default | US, mostly | Acceptable |
| OpenRouter | No logging unless you opt in (do not: it trades prompts for a 1% discount) | Per request: `zdr`, `data_collection: deny` | Depends on the host routed to | Acceptable with the settings in §5 |

For a hosted product, Singapore's PDPA adds transfer obligations for data
sent to a foreign LLM API (`docs/monetization-review.md` §8.4). For the
author's own install it is a preference, not a rule.

## 7. Cost at this volume

Monthly cost on the token totals of §2 [E]. Other vendors' tokenizers count
differently, so read these as ±30%. Reasoning tokens are excluded unless
stated.

| Configuration | $/month [E] | Saving |
|---|---|---|
| Now: Sonnet 5 for classify, JD and reason; Haiku 4.5 for extract | 5.35 | — |
| Sonnet 5.5 in place of Sonnet 5 | 5.35 | 0 |
| Classify and extract on `gpt-6-luna` (none); JD and reason stay on Sonnet | 1.10 | 4.25 |
| Everything on `gpt-6-luna` (none) | 0.29 | 5.06 |
| Everything on `gemini-2.5-flash-lite` | 0.28 | 5.07 |
| Everything on `mistral-small-2603` | 0.42 | 4.93 |
| Everything on `deepseek-flash`, thinking off, via a US host | 0.85 | 4.50 |
| Everything on `gemini-3.8-flash` at `low`, until 31 Dec | 2.17 + thinking | ~3 |
| The same from 1 Jan 2027 | 4.35 + thinking | ~1 |
| Pre-filter back on (`TRACKER_INGEST_ALL` off), models unchanged | ~3.1 | ~2.2 |

The cheapest option on this list involves no new vendor: turning the
pre-filter back on saves about as much as a second-tier model swap. It also
costs job mail the filter misses, which is why `INGEST_ALL` was turned on.

## 8. What `llm.py` would need

None of these changes was made. Each needs a case in `tests/test_llm.py`'s
fake server, and change 2 rewrites a check that pins today's behaviour
("json=True asks for a JSON object at temperature 0").

1. **Count 402 and 408 as outages, and read a 200 that carries only an error
   object by its code.** This matters for any OpenAI-compatible backend and
   is independent of the vendor choice. It is the 4 Sep lesson
   (`.claude/rules/llm.md`) applied to the second backend.
2. **Let configuration override or remove `temperature` and
   `response_format`.** Set them before the extra body is merged, and drop
   any key whose value is `null`. OpenAI's reasoning models (no
   temperature) and Gemini 3 (temperature 1) then need settings, not code.
3. **Make the token-cap field configurable** (`max_tokens` or
   `max_completion_tokens`). It cannot live in the extra body, because the
   cap differs per stage (500, 1,500, 4,000).
4. **Give thinking models headroom.** The caps were sized for Claude's
   answers. A configurable multiplier on the OpenAI-compatible backend
   would stop extract's 500 from being spent on reasoning.
5. **Optional: a base URL per stage.** Only if two non-Claude vendors must
   run at once without OpenRouter.

## 9. Embeddings

Embeddings have never been called in production. Re-embedding all 298 JDs
would cost about two cents on any of these [E]. Every option below can
output 1,024 dimensions, which fits the `vector(1024)` column.

| Model | $/MTok | Notes |
|---|---|---|
| `voyage-3.5-lite` (configured) | 0.02 | Moved to Voyage's "older models"; no deprecation notice [P, via agent] |
| `voyage-4-lite` | 0.02, first 200M tokens free | Probably a separate vector space from 3.5, so switching means re-embedding |
| OpenAI `text-embedding-3-small` | 0.02 | `dimensions: 1024`; batch 50% |
| Qwen3-Embedding-8B on DeepInfra | 0.01 | Open weights |
| Gemini Embedding 2 | 0.20 | Ten times the others for this use |

If dedup is ever switched on, change `TRACKER_EMBED_MODEL` to `voyage-4-lite`
before the first call, while there is nothing to re-embed.

## 10. Recommendation

1. **Change nothing now.** Sonnet 5 is active until at least 30 Jun 2027.
   Watch for a Haiku 4.5 deprecation notice; it comes with at least 60 days'
   warning.
2. **Replay Sonnet 5.5 first,** on a trimmed classify set and on the JD gold
   set (`job-tracker-snapshots/jd-eval-2026-09-25/`, whose `--report`
   re-scores the earlier paid runs offline). Same price, so it needs only to
   match. If it does, switch following invariant #5: change the default, put
   the reason beside the constant, and leave existing rows on Sonnet 5.
   - **Cost:** about US$0.80 batched for 200 classify emails, plus about
     US$0.40 for two JD trials [E].
   - **Before any spend:** price it with `count_tokens` and ask.
3. **Make §8's changes 1–3 only if a fallback or a bring-your-own route is
   wanted.** Then replay, on separate vendor keys, which cannot drain the
   Anthropic balance the live pipeline uses:
   - two cheap candidates on classify: `gpt-6-luna` at `none`, and
     `mistral-small-2603` or `gemini-3.5-flash-lite`;
   - one Sonnet-tier cheap model on the JD gold set: `deepseek-flash` via a
     US host, or `gemini-3.8-flash`.

   `scripts/replay_classify.py` already takes `--base-url` and `--model`.
   About 700 stored emails keep a body, which is about 2.7M input tokens, or
   US$0.27–0.80 per cheap candidate [E].
4. **For the open-source release,** document OpenRouter with §5's privacy
   settings as the one-URL route for a self-hoster without an Anthropic key.
5. **Leave out** Gemini's free tier, DeepSeek's first-party API and Cohere.

## 11. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Prices and models change monthly; GPT-6 models are days old; Gemini 3.8 Flash's price doubles on 1 Jan 2027 | §7 goes stale | Every price here is dated; re-read before acting |
| General benchmarks do not measure this task | The wrong model gets picked | Replay stored rows and the gold set; a leaderboard has already disagreed with this data once |
| Thinking models spend the token cap on reasoning | Empty JSON, a failed repair, a dead letter | Disable or lower reasoning; change 4; watch the replay for empty answers |
| Temperature 0 on Gemini 3 | Looping or degraded output | Change 2; replay at temperature 1 |
| 402 and 408 filed as job failures | One balance exhaustion dead-letters the queue | Change 1 |
| Tokenizers differ by ±30% | Cost estimates off | Count tokens per vendor during the replay |
| A vendor changes its data terms | Personal mail exposed | Prefer no-training and zero-retention routes; re-read terms when renewing a key |
| More vendor keys | A larger secret surface | `.env` only, under the same rule as `ANTHROPIC_API_KEY` |
| Artificial Analysis labels Sonnet 5 "deprecated" [S] | A misleading reading | Anthropic's deprecations page says active until at least 30 Jun 2027 [P] and is the authority |

## 12. Open questions

1. **Is a fallback provider worth a second key?** The answer turns on how
   much a four-day pause (once, in September) cost the search.
2. **Should the open-source release default to a non-Anthropic route?** Or
   stay Claude-first with OpenRouter documented as the alternative.
3. **Does the author mind processing in the US?** No standard route keeps
   the current top models in Singapore; Azure's APAC Data Zone and Alibaba's
   Singapore region are the exceptions.
4. **What will Haiku 5.5 cost?** When it lands, re-run the extract and
   classify comparisons; it may make the cross-vendor question moot for the
   cheap tier.
5. **What is Mistral's pay-as-you-go training default?** The help page says
   only that you may opt out. Set the toggle before any call.

## 13. Sonnet 5.5, replayed (3 Oct 2026)

§10's first step, run the same day on the author's go-ahead. All three
stages went through the production functions (`classify_email`,
`jd_extraction.extract`, `rejection_reason`) with only the model changed:
`claude-sonnet-5-5`, and effort `medium` on the JD stage as in production.
It cost about **US$1.25** [M], against the US$1.35 quoted.

- **Classify:** 182 emails at full price (live), about US$0.66.
- **JD set:** 108 requests through the Batch API, about US$0.27.
- **Rejection reasons:** 53 emails live, US$0.15.
- **Re-checks:** about US$0.18.

The run files quote real emails, so they sit outside the repository in
`job-tracker-snapshots/llm-alt-2026-10-03/`. The JD runs are appended to
`job-tracker-snapshots/jd-eval-2026-09-25/eval_runs.jsonl`, whose
`jd_eval.py --report` now scores Sonnet 5.5 beside the earlier configurations.

### 13.1 Classify: better

**The sample.** Every classified email since 10 Sep that still keeps a body:
182, all decided by Sonnet 5. Mail ruled not job-related has its body deleted
(`TRACKER_INGEST_ALL`), so the sample is job-related mail only. The check is
therefore one-sided: it can catch Sonnet 5.5 dropping job mail, but not
Sonnet 5.5 flagging non-job mail.

**The headline.** 175 of 182 agree on job-related (96.2%). Where both say
job-related, 170 of 175 agree on the type.

**Each of the 12 disagreements was run twice more on both models.** Neither
model's errors would be visible from one run:

| Disagreements | Stored (Sonnet 5) | Sonnet 5 again | Sonnet 5.5, 3 runs | Who is right |
|---|---|---|---|---|
| 5 ATS "verify your account" or verification-code emails | `other` | `other`, flipping to `not_job_related` on 2 | `not_job_related`, 3 of 3 on all five | **Sonnet 5.5.** This is the mail class behind the 4 Aug Haiku-to-Sonnet move, and the known answer recorded in `.claude/rules/llm.md` is not job-related |
| 4 emails received before the 23 Sep body rewrite (2 LinkedIn connection invitations; a recruiter asking for a call; a cancelled interview) | `recruiter_outreach` or `status_update` | Sonnet 5.5's answer, 2 of 2 | the same, 3 of 3 | No model difference: the stored answer was decided on the old body |
| 1 employee referral to a role not applied to | `other` | `other` | `recruiter_outreach`, 3 of 3 | Probably Sonnet 5.5: it routes to triage's inbound lane, where referrals have gone before |
| 2: a reminder to finish an incomplete application, and LinkedIn saying an application failed to deliver | `other` | `other` | `status_update`, 3 of 3 | A judgment call. Both types file a note (`matcher.EVENT_TYPE`), so nothing downstream changes |

Sonnet 5.5 gave the same label on all 3 runs of every disputed email. Sonnet 5
flipped on 2 of the 12. Latency was a 1.6 s median, and each call averaged
1,470 input tokens, the figure §2's correction rests on.

### 13.2 JD extraction: one reproducible miss

Against the 66-label gold set, on the 54 postings the sweep uses, two trials
(`jd_eval.py --report`):

| Configuration | Correct | On restricted postings | Invented signals | Same label both runs | Tech-list overlap | $ per 1,000 JDs |
|---|---|---|---|---|---|---|
| Sonnet 5, medium (production) | 98.1% | 96.4% | 0 | 100% | 0.93 | 6.87 |
| **Sonnet 5.5, medium** | **97.2%** | **94.6%** | **0** | **98%** | **0.97** | **7.04** |

- **The new miss.** A posting whose only visa line is "APPLICABLE FOR WORK
  VISA" (gold: `sponsors`). Sonnet 5 found it in both trials; Sonnet 5.5
  answered `unclear` in both.
- **The known judgment posting** (`docs/jd-extraction-models.md` §9.1) went
  the other way. Sonnet 5 misses it every time, and Sonnet 5.5 got it right
  in 1 of 2.
- **Net:** one answer fewer out of 108, still no invented restriction, and
  steadier technology lists. The JD doc's own risk table calls differences
  under about 2 points noise. This one is small, but it repeated, so it is
  real.

### 13.3 Rejection reasons: one reproducible miss

All 53 stored rejection emails, one trial, scored against Sonnet 5's two
trials from 25 Sep and against the live stage's stored answers for the seven
emails since.

- **Agreed** on 4 of the 5 stated reasons found on 25 Sep.
- **Found the newest one,** a 28 Sep form letter that says the role was
  filled, which the live Sonnet 5 stage had also found.
- **Missed** the LinkedIn letter that names the screening answer the
  employer rejected. Sonnet 5 called it `skills` in two trials and live;
  Sonnet 5.5 said "no reason" in three. That letter's rejection is also read
  as a `form_screen` by the 72-hour timer (`.claude/rules/web-ui.md` rule
  13), so the miss costs a tag, not the row's "how it ended".
- **Cost less:** US$0.15 for 53 emails, where Sonnet 5's two-trial run cost
  US$0.25 for 46 the same way. Sonnet 5.5 writes fewer output tokens here.

### 13.4 What it means

Not applied. The decision is the author's, and invariant #5 says how to make
it: change the default, put this measurement beside the constant, and leave
existing rows on their model.

- **Classify:** switching `CLASSIFY_MODEL` to `claude-sonnet-5-5` is
  supported by the evidence. It fixes the one mail class this project has a
  recorded answer for, which Sonnet 5 gets wrong, and nothing in the sample
  went the other way.
- **JD extraction and the rejection reason:** these have nothing to gain
  that the replay showed, and each now has one reproducible miss. Sonnet 5
  is active until at least 30 Jun 2027, so they can stay on it. Splitting
  stages by model is what the per-stage constants exist for.

## 14. Sources

All read 3 Oct 2026. **Re-read at source for this document** marks the pages
whose facts were checked after the agents reported; everything else is as an
agent reported it from the page named.

**Anthropic**
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing) [P],
  re-read at source for this document. Sonnet 5.5 and Sonnet 5 at $2/$10;
  Sonnet 5's price made standard; Haiku 4.5 at $1/$5; batch 50%; the
  ~30% tokenizer note.
- [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)
  [P], re-read at source for this document. Every retirement date in §3;
  the 60-day notice.
- [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview),
  [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching),
  [Data residency](https://platform.claude.com/docs/en/manage-claude/data-residency),
  [API and data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention),
  [Opus 5.5](https://www.anthropic.com/claude-opus-5-5) (22 Sep 2026),
  [Sonnet 5.5](https://www.anthropic.com/claude-sonnet-5-5) (28 Sep 2026) [P].

**OpenAI**
- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) [P],
  re-read at source for this document. $0.10 / $0.01 cached / $0.50; effort
  values `none` to `max`, default `medium`.
- [Reasoning guide](https://developers.openai.com/api/docs/guides/reasoning.md)
  [P], re-read at source for this document. Reasoning tokens are billed as
  output; GPT-6.1 Sol supports neither `none` nor `minimal`.
- [Latest-model guide](https://developers.openai.com/api/docs/guides/latest-model.md)
  [P], re-read at source for this document. "When reasoning effort is not
  `none`, remove `temperature`, `top_p`, and `top_logprobs`." Says nothing
  on `max_tokens`.
- [Pricing](https://developers.openai.com/api/docs/pricing),
  [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching.md),
  [Flex processing](https://developers.openai.com/api/docs/guides/flex-processing.md),
  [Your data](https://developers.openai.com/api/docs/guides/your-data) [P].
- [Forum: GPT-5.4 and `max_completion_tokens`](https://community.openai.com/t/gpt-5-4-ignores-reasoning-effort-none-when-max-completion-tokens-is-used/1378362)
  [S], Apr 2026. The only evidence for the `max_tokens` refusal, and it
  predates GPT-6.

**Google**
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) [P],
  updated 1 Oct 2026, re-read at source for this document. All Gemini
  prices in §4; output includes thinking tokens; free tier "Used to improve
  our products: Yes", paid tier "No".
- [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai),
  [Thinking](https://ai.google.dev/gemini-api/docs/thinking),
  [Gemini 3 guide](https://ai.google.dev/gemini-api/docs/gemini-3) (the
  temperature warning), [Terms](https://ai.google.dev/gemini-api/terms)
  (28 Apr 2026), [Usage policies](https://ai.google.dev/gemini-api/docs/usage-policies)
  (9 Jun 2026), [Deprecations](https://ai.google.dev/gemini-api/docs/deprecations) [P].
- Vertex AI [OpenAI endpoint auth](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/migrate/openai/auth-and-credentials.md.txt)
  and [locations](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations.md.txt)
  [P, stale copies].

**xAI**
- [Models](https://docs.x.ai/docs/models), [Pricing](https://docs.x.ai/developers/pricing),
  [Chat completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions),
  [Regions](https://docs.x.ai/developers/advanced-api-usage/regions),
  [Security FAQ](https://docs.x.ai/developers/faq/security),
  [15 May retirement](https://docs.x.ai/developers/migration/may-15-retirement) [P].

**Mistral, Cohere**
- [Mistral pricing](https://mistral.ai/pricing) [P], re-read at source for
  this document: it shows only Large 3's $0.5/$1.5; the Small 4 and
  Medium 3.5 prices come from their model pages,
  [Small 4](https://docs.mistral.ai/models/mistral-small-4-0-26-03) and
  [Medium 3.5](https://docs.mistral.ai/models/mistral-medium-3-5-26-04) [P].
- [Mistral API](https://docs.mistral.ai/api/), [Reasoning](https://docs.mistral.ai/capabilities/reasoning),
  [Training opt-out](https://help.mistral.ai/en/articles/347617) (31 Aug 2026) [P].
- [Cohere compatibility API](https://docs.cohere.com/docs/compatibility-api),
  [Data usage](https://cohere.com/data-usage-policy) [P].

**Open weights and hosts**
- [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing) [P],
  re-read at source for this document. `deepseek-flash` $0.30/$1.20 peak,
  half off-peak; peak is 01:00–04:00 and 06:00–10:00 UTC on weekdays.
- [DeepSeek privacy policy](https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html)
  [P], 10 Feb 2026, re-read at source for this document. "we directly
  collect, process and store your Personal Data in People's Republic of
  China"; training opt-out. The policy excludes data that developers' own
  applications collect from their end users.
- [DeepSeek JSON mode](https://api-docs.deepseek.com/guides/json_mode),
  [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode),
  [Error codes](https://api-docs.deepseek.com/quick_start/error_codes) [P].
- Alibaba Model Studio [pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing),
  [regions](https://www.alibabacloud.com/help/en/model-studio/regions/),
  [JSON mode](https://www.alibabacloud.com/help/en/model-studio/json-mode) [P].
- OpenRouter [provider routing](https://openrouter.ai/docs/features/provider-routing),
  [ZDR](https://openrouter.ai/docs/features/zdr),
  [data collection](https://openrouter.ai/docs/guides/privacy/data-collection),
  [reasoning tokens](https://openrouter.ai/docs/use-cases/reasoning-tokens),
  [errors](https://openrouter.ai/docs/api/reference/errors-and-debugging),
  [FAQ](https://openrouter.ai/docs/faq) [P].
- Groq [models](https://console.groq.com/docs/models),
  [structured outputs](https://console.groq.com/docs/structured-outputs),
  [your data](https://console.groq.com/docs/your-data); Together
  [pricing](https://www.together.ai/pricing), [JSON mode](https://docs.together.ai/docs/json-mode);
  Fireworks [pricing](https://docs.fireworks.ai/serverless/pricing),
  [data handling](https://docs.fireworks.ai/guides/security_compliance/data_handling);
  DeepInfra [pricing](https://deepinfra.com/pricing),
  [privacy](https://docs.deepinfra.com/account/data-privacy) [P].
- vLLM [GPT-OSS recipe](https://docs.vllm.ai/projects/recipes/en/latest/OpenAI/GPT-OSS.html) [P].

**Independent comparisons**
- [Artificial Analysis leaderboard](https://artificialanalysis.ai/leaderboards/models),
  Intelligence Index v4.3.2, and its model pages [S]. Undated; read
  3 Oct 2026.
- [Vectara hallucination leaderboard](https://github.com/vectara/hallucination-leaderboard)
  [S], updated 22 Sep 2026. No rows for Sonnet 5 or any 5.x model.

**Embeddings**
- [Voyage pricing](https://docs.voyageai.com/docs/pricing),
  [OpenAI embeddings](https://developers.openai.com/api/docs/guides/embeddings),
  [Gemini embeddings](https://ai.google.dev/gemini-api/docs/embeddings),
  [Qwen3-Embedding-8B on DeepInfra](https://deepinfra.com/Qwen/Qwen3-Embedding-8B) [P].

**This project**
- `docs/monetization-review.md` §8 (volumes, cost per call, 30 Sep 2026);
  `docs/jd-extraction-models.md` (the gold set and its sweep);
  `.claude/rules/llm.md` (thinking on Sonnet 5, effort on classify, the
  4–8 Sep outage); `pipeline/llm.py` (the request body and status
  handling, read 3 Oct 2026).
