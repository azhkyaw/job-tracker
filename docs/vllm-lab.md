# vLLM lab: deployment and serving, hands-on

**Status: in progress, started 8 Sep 2026.** This is a log, not a design.
Each numbered stage records what was run, what was measured and what it
changed in the tracker, in the order the stages unblock each other. Read
`pipeline/llm.py` first: the tracker's side of all this is its
OpenAI-compatible backend, and stage 1 is the first time that backend meets a
real server. Fill in the **Result** blocks as you go; a stage without one has
not been done.

## 1. Why this project is the workload

A tutorial model answers "does vLLM run"; this workload answers "is it any
good at my job", and it had four things waiting on 8 Sep 2026:

- **A real drain — already taken.** 46 emails sat stored but unread from
  3 Sep, when the Anthropic account ran out of credit (`.claude/rules/llm.md`, the
  billing-error gotcha), until the top-up on 8 Sep drained them on Claude
  before this lab could start. The lab's real workload is therefore the
  replay (§4) plus whatever the next `sync` brings in; stage 1's finish line
  is a genuine email classified by vLLM, not a backlog.
- **A labelled set.** Every classified email carries the decision, the
  confidence and the model that made it (`emails.model`, invariant #5) —
  about 300 rows, the same data `scripts/replay_thresholds.py` uses for the
  matcher. Replaying them through an open-weight model gives an agreement
  rate on *your* mail, which is the only number a model swap here needs.
- **An unverified backend.** `pipeline/llm.py`'s OpenAI-compatible path is
  tested against a fake server only (`tests/test_llm.py`).
- **A feature never switched on.** Dedup has 0 embeddings on real data;
  vLLM serves embedding models too, and `vector(1024)` fixes which ones fit.

What each stage teaches, in vLLM's own terms:

| Stage | Concepts |
|---|---|
| 1 | Engine args, weight loading, KV-cache sizing, `--gpu-memory-utilization`, reasoning parsers, the API key, `/metrics` |
| 2 | Evaluating a swap by replay; where a small model disagrees with a large one on real mail |
| 3 | Prefix caching, continuous batching versus latency, quantisation, structured-output cost, `--max-model-len` |
| 4 | Cloud Run GPU (cold start, scale-to-zero, `enforce_eager`), GKE, tensor parallel |
| 5 | A second task type (`--task embed`) and the tracker's dedup path |

## 2. Ground truth, 8 Sep 2026

Read off this machine and the signed-in `gcloud` account, not assumed:

| Fact | State |
|---|---|
| `gcloud` | SDK 576, signed in, default region `asia-southeast1`, zone `-b` |
| G2 (L4) in Singapore | Offered |
| Active project | `ingenia-kp-dev` — not a lab, left untouched |
| Billing account | One, open, carrying 3 projects |
| L4 quota, `asia-southeast1` | 0 on every project inspected |
| `GPUS_ALL_REGIONS` | 0 |
| GPUs with any quota | K80/P100/P4/V100 at 1 each — pre-Volta except V100, useless for vLLM |

**What was done:** project `vllm-lab-2609` ("vLLM lab") created. Linking it
to the billing account was **refused**: `Cloud billing quota exceeded` — the
billing account's own limit on linked projects is used up by the three it
already carries. Nothing in a GCP project runs without billing, so the lab
cannot start there until one of:

1. a billing quota increase (the support form the error links to), which is
   a ticket, not a click;
2. unlinking a project the account no longer needs — and none of the three
   looks disposable: the auto-named one has BigQuery, Dataform, Datastore
   and the Gmail API enabled, i.e. it is doing something;
3. running the lab inside an already-linked project — namespaced by VM name,
   firewall rule and tag, so it collides with nothing; Compute Engine would
   be a new API there.

Once a project has billing, the GPU quota request (a preference, approved by
Google in hours to days; ask for 2 if §6's tensor-parallel exercise is
wanted):

```
gcloud services enable compute.googleapis.com cloudquotas.googleapis.com --project $P
gcloud beta quotas info list --service=compute.googleapis.com --project=$P \
    --format="value(quotaId)" | grep -iE "L4|GPUS_ALL"        # the exact ids
gcloud beta quotas preferences create --service=compute.googleapis.com --project=$P \
    --quota-id=<NVIDIA_L4_GPUS id> --preferred-value=1 --dimensions=region=asia-southeast1 \
    --justification="single-GPU vLLM inference lab"
gcloud beta quotas preferences create --service=compute.googleapis.com --project=$P \
    --quota-id=<GPUS_ALL_REGIONS id> --preferred-value=1 --justification="same"
```

Cost rails before the first VM: a budget alert on the billing account; Spot
provisioning (`scripts/gcp/vllm-vm.sh` does this); **stop the VM when a
session ends** — the L4 bills by the second whether or not anything is
being served. At roughly $0.71/hour on demand, the whole lab is a few tens of
dollars if the VM is stopped between sessions and hundreds if it is not.

**Result (8 Sep):** the lab lives in `vllm-lab-2609`; the billing limit is
being cleared by hand. **The author provisions everything in this lab by
hand, on purpose, to learn it** — `scripts/gcp/vllm-vm.sh` is the reference
for what each step is and the shortcut for the second time, not the path.
Sessions assisting here explain and check; they do not run `gcloud` create,
link or delete commands.

## 3. Stage 1: first serve, on Compute Engine

`scripts/gcp/vllm-vm.sh` is the whole procedure: `create` once, then
`serve`, `tunnel`, and `stop`. The choices it bakes in, and why:

- **Spot `g2-standard-4`**, one L4 (24 GB), Google's Deep Learning VM image
  (`common-cu129-ubuntu-2404-nvidia-580`: driver and Docker preinstalled), a
  200 GB disk so several models' weights fit. Preemption STOPS the VM and
  keeps the disk; `start` brings it back and the container restarts itself.
- **The official `vllm/vllm-openai` image**, not a pip install. The image
  pins CUDA, torch and vLLM together; a pip install on a VM is where people
  lose an afternoon to a driver mismatch.
- **Qwen3-8B in bf16**, about 16 GB of weights, `--max-model-len 16384`.
  Classify sends about 3,800 tokens, extract up to about 12,000 characters of
  body, so 16k covers every stage with KV cache to spare. Thinking OFF via
  `--reasoning-parser qwen3 --default-chat-template-kwargs
  '{"enable_thinking": false}'`: these are short extraction tasks, and
  `max_tokens` caps reasoning and answer together.
- **Nothing public.** The only firewall rule opening port 8000 admits Google's
  IAP range; `tunnel` (`gcloud compute start-iap-tunnel`, pure gcloud, works
  on Windows) puts the server on `localhost:8001`. The VM's own port is 8000
  because that is vLLM's default; 8001 locally because 8000 is the tracker.
- **An API key.** `VLLM_API_KEY` is the bearer the tracker sends as
  `TRACKER_LLM_API_KEY`. Over the tunnel it is belt-and-braces; on Cloud Run
  (§6) it is the only lock on the door.

What to read in `logs` on the first start, in order, and record:

| Log line | What it tells you |
|---|---|
| `Loading weights took N s` | Disk and HF download speed; the second start is disk only |
| `GPU KV cache size: N tokens` | What is left after weights at `--gpu-memory-utilization 0.90` — the real capacity number |
| `Maximum concurrency for N tokens per request: M` | How many of YOUR requests fit at once; the batching ceiling for §5 |
| CUDA graph capture time | What `--enforce-eager` would save at startup and cost per token (§6) |
| `Starting vLLM API server` | Ready |

Then, through the tunnel: `GET /v1/models` with the key, one chat completion
by hand, and `GET /metrics` once to see the counter names this vLLM version
uses (they have changed between releases; §5 reads them).

Then the tracker. In `.env` on this machine:

```
TRACKER_LLM_BASE_URL=http://127.0.0.1:8001/v1
TRACKER_LLM_MODEL=Qwen/Qwen3-8B
TRACKER_LLM_API_KEY=<VLLM_API_KEY>
```

then `sync` followed by `work --once`. Every stage now runs on the L4. What
to check: `SELECT model, count(*) FROM emails WHERE processed_at > now() -
interval '1 hour' GROUP BY 1` reads `Qwen/Qwen3-8B`; the header shows no
stall band; each newly filed event reads right on its application. Read them
with the suspicion you would give a new hire — a small model's mistakes on
real mail are stage 2's motivation, and the replay is where they get
counted rather than noticed.

Known frictions: the first `serve` downloads the weights (minutes; watch
`logs`); `gcloud compute ssh` on Windows may want PuTTY, which the SDK can
install — if it fights you, run `ssh`/`serve`/`logs` from WSL Ubuntu with
gcloud installed there, or from the Console's browser SSH, while `tunnel`
stays on Windows.

**PowerShell, which is where this lab is actually driven from**
(`scripts/gcp/vllm-vm.ps1` is the twin of the `.sh`). The commands above are
written bash-style; five things change on the laptop side, and nothing
changes on the VM side, which is Linux however you reached it:

- Line continuation is a backtick at the end of the line, not `\`.
- `curl` is an alias for `Invoke-WebRequest` and does not take `-H`. Use
  `curl.exe` (Windows ships it) or
  `Invoke-RestMethod -Headers @{Authorization="Bearer $key"} http://127.0.0.1:8001/v1/models`.
- `| grep -iE "L4|GPUS_ALL"` is `| Select-String -Pattern 'L4|GPUS_ALL'`.
- `&&` does not exist in Windows PowerShell 5.1; run the commands one after
  another, or `;`.
- `gcloud.ps1` prints progress to stderr, which Windows Terminal shows in
  red. It is not an error unless the exit code says so.

A random API key: `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`.
The tunnel blocks its tab; open a second Windows Terminal tab for it.

**Result:** _pending §2._

## 4. Stage 2: replay against the record

`scripts/replay_classify.py --model Qwen/Qwen3-8B --base-url
http://127.0.0.1:8001/v1 --api-key ...` runs `email_classifier.classify_email`
— the same prompt version, truncation and validation the worker uses — over
every stored classification and diffs. It writes nothing to the database.
Rows whose body was purged (a `not_job_related` verdict under `INGEST_ALL`)
are skipped and counted, since the model would be seeing a different email.

Read the confusion table, not the headline rate. The prompt's own rule 7 says
a missed rejection is worse than a false alarm, so `rejection -> other` and
`rejection -> not_job_related` are the rows that matter; `confirmation ->
status_update` is noise the matcher mostly absorbs. Compare against the case
CLAUDE.md records for Haiku (an ATS account-activation mail called a
`confirmation`, 3 of 3 runs): does the 8B make the same mistake?

Two runs are worth the extra minutes: `--workers 1` (the worker's serial
shape) and `--workers 8`, the same rows batched by the server. The emails/s
difference is §5's first measurement, free.

**Result:** _pending §3._

| Model | Rows | job_related agreement | type agreement | Worst confusion | median s | emails/s @1 / @8 |
|---|---|---|---|---|---|---|
| Qwen/Qwen3-8B | | | | | | |

## 5. Stage 3: serving internals

Each of these is one server restart plus one replay, so the replay's
`--limit 100` is the unit of measurement.

- **Prefix caching.** Every classify call shares one system prompt of a few
  thousand tokens. Run the replay twice with caching on (the default) and
  read the prefix-cache hit counters on `/metrics`; then once with
  `VLLM_EXTRA_ARGS="... --no-enable-prefix-caching"`. Record the median
  latency of all three: this is the cheapest speed-up vLLM has and it is
  invisible unless measured.
- **Batching.** `--workers 1, 4, 8, 16` against the same rows. Throughput
  rises until the "maximum concurrency" figure from §3's log line; latency
  per request rises with it. The tracker's worker is serial (one job, one
  transaction, §6.4 of the design doc), so this measures what a concurrent
  worker would buy — write that number down before deciding whether it is
  worth building.
- **Quantisation.** A 14B in AWQ or FP8 fits the same L4 where its bf16 does
  not. Serve one (`VLLM_MODEL=<an AWQ Qwen3-14B> VLLM_EXTRA_ARGS="...
  --quantization awq"`) and rerun §4: does agreement move more than latency?
- **Structured outputs.** The JSON stages send `response_format:
  json_object`; vLLM enforces it with a grammar backend, which costs decode
  time on the first request of each shape. Compare the replay with and
  without (`TRACKER_LLM_EXTRA_BODY` cannot turn it off — temporarily edit
  `llm.py` for this one measurement, or replay a free-text stage).
- **`--max-model-len`.** Halve it, watch `GPU KV cache size` and "maximum
  concurrency" change, and see which stage breaks first (the JD stage sends
  up to 24,000 characters).

**Result:** _pending §4._

## 6. Stage 4: deployment shapes

**Cloud Run with an L4.** The shape that matches this workload's traffic:
nothing for hours, then a drain. Two ways to supply weights — bake them into
the image at build time (`FROM vllm/vllm-openai` plus an HF download layer;
large image, fast start) or mount a Cloud Storage bucket as a volume
(`--add-volume type=cloud-storage`; small image, slower start). Deploy with
`--gpu=1 --gpu-type=nvidia-l4`, at least 4 CPU and 16 GiB, `--max-instances=1`,
and `--no-gpu-zonal-redundancy` for the cheaper rate. Measure cold start
twice, with and without `--enforce-eager` (skips CUDA-graph capture: faster
start, slower tokens). Auth: the tracker sends a fixed bearer, so allow
unauthenticated invocation and let `VLLM_API_KEY` over Cloud Run's HTTPS be
the lock — Cloud Run IAM wants an identity token that expires hourly. Record:
cold start, cost of one drain, and whether the 15-minute sync cadence makes
scale-to-zero a win or a cold start every time.

**GKE.** Google's own tutorial (serving Gemma on GKE with vLLM) is the
template; Autopilot with an L4 node pool, the same container, a Service in
front. This is the shape teams run, and its value here is seeing what the
VM script hand-rolled (health checks, restarts, the model as a Deployment)
expressed as manifests. `vllm-project/production-stack` is the next rung.

**Tensor parallel.** `g2-standard-24` has two L4s; `--tensor-parallel-size 2`
splits a model across them. Needs L4 quota 2. Serve the 14B in bf16 that did
not fit one card, and compare its agreement with §5's quantised one — the
honest answer to "quantise or add a GPU".

**Result:** _pending §5._

## 7. Stage 5: embeddings, back to the tracker

`vllm serve BAAI/bge-large-en-v1.5 --task embed` serves a 1024-dimension
model, which is what `postings.jd_embedding vector(1024)` was declared for
(migration 001, sized for Voyage). `pipeline/embeddings.py` speaks Voyage
only; teaching it `/v1/embeddings` on the same OpenAI-compatible backend is
a small change (one more method next to `complete`), after which `python -m
pipeline.cli scan` enqueues the 210 jobs' embeddings and `/triage` shows its
duplicate band for the first time on real data. Check `DEDUP_AUTO_COS` /
`DEDUP_PENDING_COS` against what a different embedding model's cosine
distribution looks like before trusting an auto-merge — invariant #3, a
wrong merge has no inverse.

**Result:** _pending §3._

## 8. Cost ledger

| Date | What | GPU-hours | Credits |
|---|---|---|---|
| | | | |

## 9. Sources

- vLLM installation requirements (Linux only; WSL2 or forks on Windows):
  https://docs.vllm.ai/en/stable/getting_started/installation/gpu
- vLLM OpenAI-compatible server, `--api-key`, extra parameters:
  https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server
- vLLM reasoning outputs, `--reasoning-parser`, disabling thinking per model:
  https://docs.vllm.ai/en/stable/features/reasoning_outputs
- vLLM structured outputs: https://docs.vllm.ai/en/stable/features/structured_outputs
- vLLM Docker image: https://docs.vllm.ai/en/stable/deployment/docker
- Cloud Run GPU services: https://docs.cloud.google.com/run/docs/configuring/services/gpu
- Cloud Run AI cold starts: https://cloud.google.com/blog/topics/developers-practitioners/a-guide-to-ai-cold-starts-on-cloud-run
- Serve Gemma on GKE with vLLM: https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/serve-gemma-gpu-vllm
- Compute Engine allocation quotas: https://docs.cloud.google.com/compute/resource-usage
- G2 / L4 regions: https://cloud.google.com/blog/products/compute/introducing-g2-vms-with-nvidia-l4-gpus
- g2-standard-4 pricing (third party): https://instances.vantage.sh/gcp/g2-standard-4
