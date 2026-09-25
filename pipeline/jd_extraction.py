"""JD -> structured extraction (design doc §6.4/§9). Mirrors the email
pipeline's discipline: JSON-only, schema validation, one repair retry,
prompt version recorded on every row."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config, llm
from .email_classifier import _call_json, _load_prompt

PROMPT_VERSION = "jd_extract_v2"
WORK_MODES = {"onsite", "hybrid", "remote"}
JD_MAX_CHARS = 24_000

# What each prompt version may answer for visa_signal. v1 folded every
# restriction into `local_only` and let the model infer a signal from the
# sector or the office ("government agency, no sponsorship mentioned"). On
# 25 Sep 2026 about 6 of its 27 `local_only` and 11 of its 17 `sponsors` were
# such guesses, and "must be based in Singapore" (a location, not a visa
# rule) was among the `local_only`. v2 records only what the JD says, in the
# distinctions the author reads it by: citizens/PR only, no sponsorship,
# must already be in the country (a pass may still be sponsored, just not
# a hire from abroad), locals preferred, sponsors. Rows keep the version that
# wrote them, so v1's values stay valid on v1 rows (migration 017).
VISA_SIGNALS_BY_VERSION = {
    "jd_extract_v1": {"sponsors", "local_only", "unclear"},
    "jd_extract_v2": {"citizens_pr_only", "no_sponsorship", "in_country",
                      "locals_preferred", "sponsors", "unclear"},
}
VISA_SIGNALS = VISA_SIGNALS_BY_VERSION[PROMPT_VERSION]

# What each signal is called on a page — the list's grey tag, the detail
# page's select, /analytics' comparison — in order from most to least
# restrictive. `unclear` has no tag: the JD said nothing, and a tag would say
# something. v1's `local_only` keeps a word for the v1 rows still on record.
VISA_LABELS = {
    "citizens_pr_only": "citizens/PR only",
    "no_sponsorship":   "no sponsorship",
    "in_country":       "in-country only",
    "locals_preferred": "locals preferred",
    "sponsors":         "sponsors",
    "local_only":       "local only",
}

# The JD's signal coarsened for a glance: the columns of /analytics' "Visa, at
# a glance" and the list's `visa` filter (25 Sep 2026). Restrictive signals of
# either prompt version group together; no extraction counts as saying nothing.
# Each key: (column head on /analytics, the sentence the list's filter note uses).
VISA_GROUPS = {
    "restricts": ("JD restricts", "The JD restricts who may apply"),
    "sponsors":  ("JD offers sponsorship", "The JD offers sponsorship"),
    "nothing":   ("JD says nothing", "The JD says nothing"),
}
_RESTRICTS = ("citizens_pr_only", "no_sponsorship", "in_country", "locals_preferred", "local_only")


def visa_group_sql(col: str) -> str:
    """The VISA_GROUPS key for a visa_signal column — one expression for the
    list's filter and the matrix's counts."""
    restricts = ",".join(f"'{s}'" for s in _RESTRICTS)
    return (f"(CASE WHEN {col} IN ({restricts}) THEN 'restricts' "
            f"WHEN {col} = 'sponsors' THEN 'sponsors' ELSE 'nothing' END)")

# Versions whose visa_notes must be a verbatim quote of the JD, checked here
# rather than trusted: "no quote, no signal". Told in words not to infer, v2's
# first replay (25 Sep 2026) still wrote "government employment in Singapore
# is restricted to citizens" for four agency and exchange listings whose text
# says nothing of the kind — the model's knowledge, not the JD. A note the JD
# does not contain now fails validation and the repair retry is told why.
VERBATIM_NOTES = {"jd_extract_v2"}
_SPACE = re.compile(r"\s+")
_PIECES = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


def _flat(text: str) -> str:
    """Case, whitespace and typographic quotes folded, for a quote check."""
    t = (text or "").lower().replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    return _SPACE.sub(" ", t).strip()


def quoted_in(notes: str, jd_text: str) -> bool:
    """Whether every piece of `notes` (split at an ellipsis) appears in the JD,
    ignoring case, ALL whitespace, surrounding quotes and a trailing full stop.
    Whitespace goes entirely, not just runs of it: some captured JDs carry
    stray spaces inside words ("Singapor e", "nee dsYou", measured 25 Sep
    2026), and a model quoting that sentence correctly writes "Singapore".
    The quote is right and the capture is wrong, so the check must pass it —
    on the eval it was failing a correct `in_country` on every Sonnet run."""
    jd = _SPACE.sub("", _flat(jd_text))
    pieces = [_SPACE.sub("", _flat(x).strip(' "\'').rstrip("."))
              for x in _PIECES.split(notes or "")]
    pieces = [x for x in pieces if x]
    return bool(pieces) and all(x in jd for x in pieces)


@dataclass
class JdExtraction:
    languages: list[str]
    technologies: list[str]
    seniority: str | None
    salary_min: float | None
    salary_max: float | None
    currency: str | None
    work_mode: str | None
    visa_signal: str
    visa_notes: str | None
    raw: dict = field(repr=False, default_factory=dict)
    model: str = config.JD_MODEL
    prompt_version: str = PROMPT_VERSION


def _validate(d: dict, signals: set[str] = VISA_SIGNALS) -> None:
    for key in ("languages", "technologies"):
        v = d.get(key)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"{key} must be a list of strings")
    for key in ("seniority", "currency", "visa_notes"):
        if d.get(key) is not None and not isinstance(d[key], str):
            raise ValueError(f"{key} must be a string or null")
    for key in ("salary_min", "salary_max"):
        if d.get(key) is not None and not isinstance(d[key], (int, float)):
            raise ValueError(f"{key} must be a number or null")
    if d.get("work_mode") is not None and d["work_mode"] not in WORK_MODES:
        raise ValueError(f"work_mode must be one of {sorted(WORK_MODES)} or null")
    if d.get("visa_signal") not in signals:
        raise ValueError(f"visa_signal must be one of {sorted(signals)}")


def _quote_check(jd_text: str):
    """A validator step for VERBATIM_NOTES versions: the first unquoted
    signal is sent back through _call_json's repair retry with the reason; a
    second is not an error but an answer — no quote, no signal — so the
    signal becomes "unclear" and the rest of the extraction (the stack, the
    seniority) is kept rather than failing the job over one field. Measured on
    the second v2 replay: 1 of 264 postings failed both attempts."""
    asked = {"repair": False}

    def check(d: dict) -> None:
        if d["visa_signal"] == "unclear":
            return
        if d.get("visa_notes") and quoted_in(d["visa_notes"], jd_text):
            return
        if not asked["repair"]:
            asked["repair"] = True
            raise ValueError(
                "visa_notes must be a sentence copied verbatim from the description; "
                "if the description does not say it, visa_signal must be \"unclear\"")
        d["visa_signal"], d["visa_notes"] = "unclear", None
    return check


def user_content(jd_text: str | None, title: str | None) -> str:
    """The user turn of an extraction request — one builder, so a batched
    backfill (scripts/replay_jd.py --batch) sends what the worker sends."""
    return ("<job>\n"
            f"<title>{title or '(unknown title)'}</title>\n"
            f"<description>\n{(jd_text or '')[:JD_MAX_CHARS]}\n</description>\n"
            "</job>")


def extract(client: llm.Client, jd_text: str, title: str | None,
            prompt_version: str = PROMPT_VERSION, model: str | None = None,
            max_tokens: int | None = None, effort: str | None = "config") -> JdExtraction:
    """One extraction. `prompt_version` is the worker's current one unless a
    replay (scripts/replay_jd.py) asks for a candidate; validation follows
    the version asked for, so a candidate's vocabulary is checked as its own."""
    signals = VISA_SIGNALS_BY_VERSION[prompt_version]
    jd = (jd_text or "")[:JD_MAX_CHARS]
    quote = _quote_check(jd) if prompt_version in VERBATIM_NOTES else (lambda d: None)

    def validate(d: dict) -> None:
        _validate(d, signals)
        quote(d)
    data = _call_json(
        client, model=model or config.JD_MODEL, system=_load_prompt(prompt_version),
        user_content=user_content(jd_text, title), validate=validate,
        max_tokens=max_tokens or config.JD_MAX_TOKENS,
        effort=config.JD_EFFORT if effort == "config" else effort,
    )
    return JdExtraction(
        languages=data["languages"], technologies=data["technologies"],
        seniority=data.get("seniority"),
        salary_min=data.get("salary_min"), salary_max=data.get("salary_max"),
        currency=data.get("currency"), work_mode=data.get("work_mode"),
        visa_signal=data["visa_signal"], visa_notes=data.get("visa_notes"),
        raw=data, prompt_version=prompt_version, model=model or config.JD_MODEL,
    )


def store(conn, user_id, posting_id, x: JdExtraction) -> None:
    """The one INSERT of an extraction row — the worker's, and a replay's
    --apply, which writes exactly the rows that were reviewed."""
    conn.execute(
        """
        INSERT INTO extractions (user_id, posting_id, languages, technologies,
                                 seniority, salary_min, salary_max, currency,
                                 work_mode, visa_signal, visa_notes,
                                 model, prompt_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, posting_id, x.languages, x.technologies, x.seniority,
         x.salary_min, x.salary_max, x.currency, x.work_mode, x.visa_signal,
         x.visa_notes, x.model, x.prompt_version))
