"""Cover letter generation (§6.4/§9): larger model, on-demand only, grounded
in the maintained resume profile — never re-derived, never invented."""

from __future__ import annotations

import anthropic

from . import config
from .email_classifier import _load_prompt

PROMPT_VERSION = "cover_letter_v1"


def load_profile() -> str:
    if not config.RESUME_PROFILE.exists():
        raise RuntimeError(
            f"resume profile not found at {config.RESUME_PROFILE} — create it "
            "(markdown, your real background) or set TRACKER_RESUME_PROFILE")
    return config.RESUME_PROFILE.read_text(encoding="utf-8")


def generate(client: anthropic.Anthropic, profile_md: str, company: str | None,
             title: str | None, jd_text: str | None) -> str:
    user_content = (
        f"<candidate_profile>\n{profile_md}\n</candidate_profile>\n"
        "<job>\n"
        f"<company>{company or '(unknown)'}</company>\n"
        f"<title>{title or '(unknown)'}</title>\n"
        f"<description>\n{(jd_text or '(no JD captured — write from title and company only)')[:20000]}\n</description>\n"
        "</job>"
    )
    # 4000, not 900, because COVER_MODEL is a Sonnet 5 and this call omits
    # `thinking` — which on that model family means ADAPTIVE THINKING IS ON,
    # and max_tokens caps thinking plus response text TOGETHER. The three
    # letters on record (23 Jul 2026) ran on claude-sonnet-4-6, where omitting
    # `thinking` meant none at all, and they came to 430-465 output tokens; add
    # thinking at the default `high` effort and 900 stops being comfortable.
    #
    # Truncation here is worse than anywhere else in the pipeline: unlike the
    # three JSON stages this does NOT go through _call_json, so there is no
    # validation and no repair retry — a cut-off letter is returned as a string
    # and written straight to `artifacts`. It fails silently and looks finished.
    #
    # Raising the ceiling is close to free: output is billed on what is actually
    # generated, so a bigger cap costs nothing unless it is used. Do not "fix"
    # this instead by disabling thinking — see the note in email_classifier.
    resp = client.messages.create(
        model=config.COVER_MODEL, max_tokens=4000,
        system=_load_prompt(PROMPT_VERSION),
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
