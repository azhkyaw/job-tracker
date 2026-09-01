"""Two-stage email pipeline (Phase 1): classification -> field extraction.

Stage 1 (classify_email) runs on every allowlist-filtered email with a cheap
model. Stage 2 (extract_email) runs only on mail stage 1 marked job-related.

Design-doc invariants implemented here (docs/design.md §9):
  * JSON-only outputs validated against an explicit schema, with exactly one
    repair retry that feeds the parse/validation error back to the model.
  * Prompt files are versioned on disk; the version string travels with every
    result so rows can be re-processed when prompts change.
  * Bodies are truncated for stage 1 (classification rarely needs more than
    the opening); stage 2 sees more since dates/names sit deeper in the mail.

Usage:
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    c = classify_email(client, sender, subject, received_at, body)
    if c.job_related:
        x = extract_email(client, sender, subject, received_at, body, c.type)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import anthropic

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
CLASSIFY_PROMPT_VERSION = "email_classify_v1"
EXTRACT_PROMPT_VERSION = "email_extract_v1"

# Classification moved to Sonnet 5 on 4 Aug 2026; extraction stays on Haiku.
#
# Haiku got a Workday "Verify your candidate account" mail WRONG, and
# reproducibly so — 3/3 runs called it a `confirmation` while calling a
# near-identical one from a second employer `not_job_related` 3/3. It is an
# account-activation link, not an application receipt. Sonnet 5 answered
# correctly 3/3 on the SAME
# v1 prompt, and was MORE confident on the true positive it shares with Haiku
# (0.98 vs 0.95), so this buys the fix without a prompt change.
#
# The prompt is genuinely underspecified here and that is worth fixing too:
# rule 2 exempts "platform account mail" and lists only LinkedIn/JobStreet/
# Indeed examples, so ATS account mail falls in a gap, while rule 7 ("a missed
# rejection is worse than a false alarm") pushes toward flagging it. Sonnet
# infers the intended answer; Haiku follows the text as written. An
# email_classify_v2 rule would pin the definition for ANY model — still worth
# writing, just no longer urgent.
#
# Cost, measured rather than assumed (count_tokens against both models on 12
# real emails, since Sonnet 5's tokenizer runs ~21% higher on this content):
# at ~141 classify calls/month this is ~$0.49 -> ~$1.74/month. The near-miss it
# prevents was a 0.65-scoring duplicate `confirmation` on a live application.
#
# Extraction deliberately stays on Haiku: every extraction inspected so far has
# been correct, including the Wingtip one that produced exactly the right company
# and title while the MATCHER failed around it. Change what there is evidence
# for. Both remain env-overridable, and emails.model records the model per row,
# so old rows stay attributable and a selective re-run stays possible — the
# same property invariant #5 buys for prompts.
CLASSIFY_MODEL = os.environ.get("TRACKER_CLASSIFY_MODEL", "claude-sonnet-5")
EXTRACT_MODEL = os.environ.get("TRACKER_EXTRACT_MODEL", "claude-haiku-4-5-20251001")

STAGE1_BODY_CHARS = 4_000
STAGE2_BODY_CHARS = 12_000

CLASSIFY_TYPES = {
    "confirmation", "rejection", "interview_invite",
    "recruiter_outreach", "status_update", "other",
}
STATUS_DETAILS = {"viewed", "in_review", "shortlisted", "on_hold", "other"}
PLATFORMS = {"linkedin", "jobstreet", "indeed", "ats", "direct", "unknown"}


# --------------------------------------------------------------------------- results

@dataclass
class Classification:
    job_related: bool
    type: str | None            # one of CLASSIFY_TYPES, or None when not job_related
    confidence: float
    reason: str
    model: str = CLASSIFY_MODEL
    prompt_version: str = CLASSIFY_PROMPT_VERSION


@dataclass
class Extraction:
    company: str | None
    role_title: str | None
    platform: str               # one of PLATFORMS
    ats: str | None
    event_date: str | None      # YYYY-MM-DD, or None -> caller uses received_at
    status_detail: str | None
    recruiter: dict | None      # {"name": ..., "email": ...} or None
    notes: str | None
    raw: dict = field(repr=False, default_factory=dict)
    model: str = EXTRACT_MODEL
    prompt_version: str = EXTRACT_PROMPT_VERSION


# --------------------------------------------------------------------------- helpers

def _load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip())


def _email_block(sender: str, subject: str, received_at: datetime, body: str,
                 limit: int) -> str:
    body = (body or "")[:limit]
    return (
        "<email>\n"
        f"<sender>{sender}</sender>\n"
        f"<subject>{subject or '(no subject)'}</subject>\n"
        f"<received>{received_at.date().isoformat()}</received>\n"
        f"<body>\n{body}\n</body>\n"
        "</email>"
    )


def _call_json(
    client: anthropic.Anthropic,
    *,
    model: str,
    system: str,
    user_content: str,
    validate: Callable[[dict], None],
    max_tokens: int,
) -> dict:
    """One model call with a single repair retry on parse/validation failure."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    last_err: Exception | None = None
    for attempt in range(2):
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system, messages=messages,
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = json.loads(_strip_fences(text))
            if not isinstance(data, dict):
                raise ValueError("top-level JSON must be an object")
            validate(data)
            return data
        except (json.JSONDecodeError, ValueError) as err:
            last_err = err
            if attempt == 0:
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content":
                        f"That reply was invalid: {err}. "
                        "Reply again with ONLY the corrected JSON object."},
                ]
    raise ValueError(f"model returned invalid JSON after retry: {last_err}")


# --------------------------------------------------------------------------- validation

def _validate_classification(d: dict) -> None:
    if not isinstance(d.get("job_related"), bool):
        raise ValueError("job_related must be a boolean")
    conf = d.get("confidence")
    if not isinstance(conf, (int, float)) or not 0.0 <= conf <= 1.0:
        raise ValueError("confidence must be a number in [0, 1]")
    if d["job_related"]:
        if d.get("type") not in CLASSIFY_TYPES:
            raise ValueError(f"type must be one of {sorted(CLASSIFY_TYPES)}")
    elif d.get("type") not in (None,):
        raise ValueError("type must be null when job_related is false")
    if not isinstance(d.get("reason"), str):
        raise ValueError("reason must be a string")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_extraction(d: dict) -> None:
    for key in ("company", "role_title", "ats", "event_date", "status_detail", "notes"):
        if d.get(key) is not None and not isinstance(d[key], str):
            raise ValueError(f"{key} must be a string or null")
    if d.get("platform") not in PLATFORMS:
        raise ValueError(f"platform must be one of {sorted(PLATFORMS)}")
    if d.get("event_date") and not _DATE_RE.match(d["event_date"]):
        raise ValueError("event_date must be YYYY-MM-DD or null")
    if d.get("status_detail") is not None and d["status_detail"] not in STATUS_DETAILS:
        raise ValueError(f"status_detail must be one of {sorted(STATUS_DETAILS)} or null")
    rec = d.get("recruiter")
    if rec is not None:
        if not isinstance(rec, dict):
            raise ValueError("recruiter must be an object or null")
        for key in ("name", "email"):
            if rec.get(key) is not None and not isinstance(rec[key], str):
                raise ValueError(f"recruiter.{key} must be a string or null")


# --------------------------------------------------------------------------- stages

def classify_email(
    client: anthropic.Anthropic,
    sender: str,
    subject: str,
    received_at: datetime,
    body: str,
) -> Classification:
    data = _call_json(
        client,
        model=CLASSIFY_MODEL,
        system=_load_prompt(CLASSIFY_PROMPT_VERSION),
        user_content=_email_block(sender, subject, received_at, body, STAGE1_BODY_CHARS),
        validate=_validate_classification,
        # 1500, not the 300 this used under Haiku. CLASSIFY_MODEL is a Sonnet 5
        # and this call omits `thinking`, which on that family means ADAPTIVE
        # THINKING IS ON at the default `high` effort — and max_tokens caps
        # thinking plus response text together. Measured over 10 random real
        # emails: output tokens 47 min / 66 mean / 199 max, with a thinking
        # block on 1 of 10. Nothing truncated, but that one run had spent 66%
        # of a 300 budget on a task whose JSON answer is ~60 tokens.
        #
        # DO NOT reclaim the headroom by disabling thinking. On Sonnet 5 a
        # thinking-disabled call can leak `<thinking>` tags into the visible
        # response, and _strip_fences() removes code fences ONLY — a leaked tag
        # goes straight into json.loads and fails the call, then fails the
        # repair retry the same way, then dead-letters. Output is billed on what
        # is generated, so a ceiling that is never reached costs nothing;
        # `effort` is the lever for spending less, not max_tokens.
        max_tokens=1500,
    )
    return Classification(
        job_related=data["job_related"],
        type=data.get("type"),
        confidence=float(data["confidence"]),
        reason=data["reason"],
    )


def extract_email(
    client: anthropic.Anthropic,
    sender: str,
    subject: str,
    received_at: datetime,
    body: str,
    classification_type: str,
) -> Extraction:
    if classification_type not in CLASSIFY_TYPES:
        raise ValueError(f"unknown classification type: {classification_type}")
    user_content = (
        _email_block(sender, subject, received_at, body, STAGE2_BODY_CHARS)
        + f"\n<classification>{classification_type}</classification>"
    )
    data = _call_json(
        client,
        model=EXTRACT_MODEL,
        system=_load_prompt(EXTRACT_PROMPT_VERSION),
        user_content=user_content,
        validate=_validate_extraction,
        max_tokens=500,
    )
    return Extraction(
        company=data.get("company"),
        role_title=data.get("role_title"),
        platform=data["platform"],
        ats=data.get("ats"),
        event_date=data.get("event_date"),
        status_detail=data.get("status_detail"),
        recruiter=data.get("recruiter"),
        notes=data.get("notes"),
        raw=data,
    )


# --------------------------------------------------------------------------- normalization

# Legal suffixes across SG / TH / ID / AU / US corporate forms. Longest first
# so "pte. ltd." wins over "ltd".
_SUFFIXES = [
    "private limited", "pte. ltd.", "pte ltd", "co., ltd.", "co. ltd.", "co ltd",
    "company limited", "sdn. bhd.", "sdn bhd", "pty ltd", "pty. ltd.",
    "public company limited", "pcl", "limited", "ltd.", "ltd", "inc.", "inc",
    "llc", "l.l.c.", "plc", "corp.", "corp", "corporation", "gmbh", "tbk",
]
_SUFFIX_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(s) for s in _SUFFIXES) + r")\s*$",
    re.IGNORECASE,
)
# Indonesian corporate forms lead the name: "PT GoTo ... Tbk", "CV Maju Jaya".
_PREFIX_RE = re.compile(r"^(?:pt|cv)\.?\s+", re.IGNORECASE)


def norm_company(raw: str) -> str:
    """Single source of truth for company_norm across the whole system.

    lowercase -> strip legal suffixes (repeatedly, for 'X Co., Ltd. (Thailand)'
    style chains) -> strip punctuation -> collapse whitespace.
    """
    s = (raw or "").lower().strip()
    s = re.sub(r"\([^)]*\)", " ", s)          # drop parentheticals: "(thailand)"
    s = _PREFIX_RE.sub("", s).strip()
    prev = None
    while prev != s:
        prev = s
        s = _SUFFIX_RE.sub("", s).strip(" ,.")
    s = re.sub(r"[^\w\s&-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
