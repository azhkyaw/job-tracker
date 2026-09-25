"""Is a model's quote really in the text it read? "No quote, no signal".

A stage that must justify an answer with a sentence copied from its input
checks the copy here rather than trusting it: told in words not to infer, a
model still writes plausible sentences of its own (jd_extract_v2's first
replay, 25 Sep 2026: "government employment in Singapore is restricted to
citizens" for listings that say nothing of the kind). Shared by the JD
extractor's visa signal and the rejection-reason stage, so both hold a quote
to the same test. Pure; no model, no database."""

from __future__ import annotations

import re

_SPACE = re.compile(r"\s+")
_PIECES = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")


def _flat(text: str) -> str:
    """Case, whitespace and typographic quotes folded, for a quote check."""
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    return _SPACE.sub(" ", t).strip()


def quoted_in(quote: str, text: str) -> bool:
    """Whether every piece of `quote` (split at an ellipsis) appears in `text`,
    ignoring case, ALL whitespace, surrounding quotes and a trailing full stop.
    Whitespace goes entirely, not just runs of it: some captured JDs carry
    stray spaces inside words ("Singapor e", "nee dsYou", measured 25 Sep
    2026), and a model quoting that sentence correctly writes "Singapore".
    The quote is right and the capture is wrong, so the check must pass it —
    on the JD eval it was failing a correct `in_country` on every Sonnet run."""
    flat = _SPACE.sub("", _flat(text))
    pieces = [_SPACE.sub("", _flat(x).strip(' "\'').rstrip("."))
              for x in _PIECES.split(quote or "")]
    pieces = [x for x in pieces if x]
    return bool(pieces) and all(x in flat for x in pieces)
