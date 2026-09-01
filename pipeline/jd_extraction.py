"""JD -> structured extraction (design doc §6.4/§9). Mirrors the email
pipeline's discipline: JSON-only, schema validation, one repair retry,
prompt version recorded on every row."""

from __future__ import annotations

from dataclasses import dataclass, field

import anthropic

from . import config
from .email_classifier import _call_json, _load_prompt

PROMPT_VERSION = "jd_extract_v1"
WORK_MODES = {"onsite", "hybrid", "remote"}
VISA_SIGNALS = {"sponsors", "local_only", "unclear"}
JD_MAX_CHARS = 24_000


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


def _validate(d: dict) -> None:
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
    if d.get("visa_signal") not in VISA_SIGNALS:
        raise ValueError(f"visa_signal must be one of {sorted(VISA_SIGNALS)}")


def extract(client: anthropic.Anthropic, jd_text: str, title: str | None) -> JdExtraction:
    user_content = (
        "<job>\n"
        f"<title>{title or '(unknown title)'}</title>\n"
        f"<description>\n{(jd_text or '')[:JD_MAX_CHARS]}\n</description>\n"
        "</job>"
    )
    data = _call_json(
        client, model=config.JD_MODEL, system=_load_prompt(PROMPT_VERSION),
        user_content=user_content, validate=_validate, max_tokens=700,
    )
    return JdExtraction(
        languages=data["languages"], technologies=data["technologies"],
        seniority=data.get("seniority"),
        salary_min=data.get("salary_min"), salary_max=data.get("salary_max"),
        currency=data.get("currency"), work_mode=data.get("work_mode"),
        visa_signal=data["visa_signal"], visa_notes=data.get("visa_notes"),
        raw=data,
    )
