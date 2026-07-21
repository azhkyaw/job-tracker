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
    resp = client.messages.create(
        model=config.COVER_MODEL, max_tokens=900,
        system=_load_prompt(PROMPT_VERSION),
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
