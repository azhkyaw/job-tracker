"""Parsing the salary line a job platform prints beside an ad (migration 011).

One implementation, in Python, never in SQL and never in the extension — same
rule as `norm_company` (invariant #4) and `norm_question`. The extension sends
the string exactly as displayed and nothing else; every caller that wants
numbers gets them from here, so two callers can't disagree about what
"$10,000 – $11,000 per month" means.

Three things make this less trivial than it looks:

 1. **The period carries the meaning.** SEA ads quote MONTHLY where most of the
    world quotes annual. A figure without its period is not comparable to
    anything, so an unrecognised period is stored as NULL rather than guessed.
 2. **Thousands separators differ by market.** Singapore/Malaysia/Australia
    write 10,000; Indonesia writes 10.000.000. Treating that dot as a decimal
    point turns twenty million rupiah into twenty.
 3. **Most of the currency is implied.** SEEK prints a bare "$" on the
    Singapore site and on the Australian one, meaning two different currencies.
    The symbol alone can't settle it, so the posting's own host does.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

PERIODS = ("hourly", "daily", "weekly", "monthly", "annual")

# Bare "$" is deliberately absent — it is ambiguous across SEEK's markets and is
# resolved from the URL host instead. Longest symbols first so "A$"/"S$" win
# over "$" and "RM" isn't shadowed.
_SYMBOLS = (
    ("RM", "MYR"), ("Rp", "IDR"), ("A$", "AUD"), ("S$", "SGD"),
    ("HK$", "HKD"), ("NZ$", "NZD"), ("₱", "PHP"), ("฿", "THB"), ("₹", "INR"),
)
_CODES = ("SGD", "MYR", "IDR", "AUD", "PHP", "THB", "HKD", "NZD", "USD", "INR")

# Which currency a bare "$" means, by the site it was printed on.
_HOST_CURRENCY = (
    ("jobstreet.com.sg", "SGD"), ("sg.jobstreet.com", "SGD"),
    ("jobstreet.com.my", "MYR"), ("my.jobstreet.com", "MYR"),
    ("jobstreet.co.id", "IDR"),  ("id.jobstreet.com", "IDR"),
    ("jobstreet.com.ph", "PHP"), ("ph.jobstreet.com", "PHP"),
    ("th.jobstreet.com", "THB"),
    ("seek.com.au", "AUD"), ("seek.co.nz", "NZD"),
)

_PERIOD_PATTERNS = (
    (re.compile(r"per\s*hour|/\s*h(ou)?r\b|hourly", re.I), "hourly"),
    (re.compile(r"per\s*day|daily\b", re.I), "daily"),
    (re.compile(r"per\s*week|weekly\b", re.I), "weekly"),
    (re.compile(r"per\s*month|/\s*mth\b|monthly\b|p\.?m\.?\b", re.I), "monthly"),
    (re.compile(r"per\s*(year|annum)|annually|yearly|p\.?a\.?\b", re.I), "annual"),
)

# A number as any market writes it: 10,000 · 10.000.000 · 8500 · 9,500.50
_NUMBER = re.compile(r"\d[\d.,]*")
_GROUPED = re.compile(r"^\d{1,3}([.,]\d{3})+$")
_DECIMAL = re.compile(r"^\d+[.,]\d{1,2}$")

# "up to 10,000" caps without a floor; "from 8,000" floors without a cap.
_MAX_ONLY = re.compile(r"\b(up\s*to|max(imum)?|below|under)\b", re.I)
_MIN_ONLY = re.compile(r"\b(from|at\s*least|min(imum)?|above|starting)\b", re.I)


def _to_number(tok: str):
    """One numeric token to a Decimal, honouring either separator convention."""
    tok = tok.strip(".,")
    if not tok:
        return None
    if _GROUPED.match(tok):              # 10,000 / 10.000.000 — all separators
        tok = tok.replace(".", "").replace(",", "")
    elif _DECIMAL.match(tok):            # 9,500.50 handled below; 9500,50 here
        tok = tok.replace(",", ".")
    else:
        # Mixed, e.g. 1,234,567.89 — the LAST separator is the decimal point.
        cut = max(tok.rfind("."), tok.rfind(","))
        if cut != -1 and len(tok) - cut - 1 <= 2:
            tok = tok[:cut].replace(".", "").replace(",", "") + "." + tok[cut + 1:]
        else:
            tok = tok.replace(".", "").replace(",", "")
    try:
        return Decimal(tok)
    except InvalidOperation:
        return None


def currency_for(raw: str, url: str | None) -> str | None:
    """Explicit code, then explicit symbol, then the site it was printed on."""
    upper = (raw or "").upper()
    for code in _CODES:
        if re.search(rf"\b{code}\b", upper):
            return code
    for sym, code in _SYMBOLS:
        if sym.upper() in upper:
            return code
    host = (url or "").lower()
    for frag, code in _HOST_CURRENCY:
        if frag in host:
            return code
    return None


def period_for(raw: str) -> str | None:
    for pattern, name in _PERIOD_PATTERNS:
        if pattern.search(raw or ""):
            return name
    return None


def parse(raw: str | None, url: str | None = None) -> dict:
    """Normalise one displayed salary line.

    Always returns the five columns migration 011 defines. `salary_raw` is
    preserved verbatim whatever happens — "Competitive salary" parses to no
    numbers at all, and that is a normal ad, not a failure.
    """
    raw = (raw or "").strip()
    out = {"salary_raw": raw or None, "salary_min": None, "salary_max": None,
           "salary_currency": None, "salary_period": None}
    if not raw:
        return out

    out["salary_currency"] = currency_for(raw, url)
    out["salary_period"] = period_for(raw)

    nums = [n for n in (_to_number(t) for t in _NUMBER.findall(raw)) if n is not None]
    # Drop tokens too small to be pay — "per 1 month", a stray "2026". The floor
    # has to follow the period or it eats real rates: $45 per hour is a salary,
    # 45 per month is not.
    floor = {"hourly": 1, "daily": 10, "weekly": 50}.get(out["salary_period"], 100)
    nums = [n for n in nums if n >= floor]
    if not nums:
        return out

    if len(nums) == 1:
        only = nums[0]
        if _MAX_ONLY.search(raw):
            out["salary_max"] = only
        elif _MIN_ONLY.search(raw):
            out["salary_min"] = only
        else:
            out["salary_min"] = out["salary_max"] = only
    else:
        lo, hi = min(nums), max(nums)
        out["salary_min"], out["salary_max"] = lo, hi
    return out
