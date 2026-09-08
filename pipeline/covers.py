"""Cover letter generation (§6.4/§9): larger model, on-demand only, grounded
in the maintained resume profile — never re-derived, never invented."""

from __future__ import annotations

from . import config, llm
from .email_classifier import _load_prompt

PROMPT_VERSION = "cover_letter_v1"


class ProfileMissing(RuntimeError):
    """No resume profile for this user — nothing to ground a letter in."""


# There is ONE resume profile and it lives on the user row.
#
# There used to be a file fallback here (config.RESUME_PROFILE, default
# `profile.md`), read whenever users.resume_profile was empty, and it was wrong
# in two directions at once. It was a release blocker: the file was the only
# route named in the error message and the only one the README described, while
# `users.resume_profile` — the one with a UI, on the Settings page — was never
# mentioned, so a self-hoster from a clean checkout hit
# `resume profile not found at profile.md` with no way to learn what actually
# feeds the generator. (This author hit it too, on a real cover-letter job.)
#
# And it was a tenancy hole. One file, no user_id: a second account that had not
# filled in Settings would silently be handed the FIRST account's profile and
# have their cover letter written from someone else's career. Every other
# per-user secret on this system is scoped by RLS (invariant #6); this one
# quietly was not, because a path on disk cannot be.
#
# Both are fixed by deleting it. A missing profile is now an error that names
# the page which fixes it.
def load_profile(resume_profile: str | None) -> str:
    profile = (resume_profile or "").strip()
    if not profile:
        raise ProfileMissing(
            "no resume profile saved — add one under Settings -> Resume profile "
            "(markdown, your real background, in your words). Cover letters are "
            "grounded in it and the prompt forbids inventing beyond it, so there "
            "is nothing to generate from until it exists.")
    return profile


def generate(client: llm.Client, profile_md: str, company: str | None,
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
    return client.complete(
        model=config.COVER_MODEL, max_tokens=4000,
        system=_load_prompt(PROMPT_VERSION),
        messages=[{"role": "user", "content": user_content}],
    ).strip()
