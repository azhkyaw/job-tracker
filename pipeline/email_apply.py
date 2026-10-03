"""Does a job description ask for the application BY EMAIL? (4 Oct 2026)

Some listings say "please send your CV in Word format to <address>" instead
of, or beside, the platform's own apply button, and nothing showed it: of 333
stored JDs on the day, 22 carried such a sentence, and not one of those
applications had any mail of the user's filed on it. Recruitment agencies
posting on LinkedIn, almost all of them.

The rule, measured on those JDs before it was written: ONE SENTENCE holding
an email address and a CV word (cv, resume, résumé). A sentence, because the
address and the request must belong together: an agency's licence line, a
privacy contact, "email us for a confidential discussion" and an
accessibility address all carry an address and no CV word, and were all of
the 19 address sentences the rule left out. "application" is NOT a CV word:
it admitted exactly one sentence, an employer's accessibility contact ("to
complete your application, please send an e-mail with your request to …").

A sentence that ALSO offers the platform's button ("please apply online or
email your CV to …", "click 'apply now' … or email …") asks for nothing
extra, since the application already went in that way, so it does not count:
7 of the 22. What is left is an application OWED by email (15 on the day).

One rule in Python and SQL, like answers.declares_sponsorship: the patterns
use only what Python's `re` and Postgres' regex agree on (no \\b, no
lookaround), so asks_by_email_sql() and instruction() cannot drift, and
tests/email_apply.json holds the two together over every real wording
(placeholder names; the structure of each sentence is kept).
"""

from __future__ import annotations

import re
from urllib.parse import quote

ADDRESS = r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+"
CV_WORD = r"(^|[^a-z])(cv|cvs|resume|resumes|résumé|résumés)([^a-z]|$)"
ONLINE = r"(^|[^a-z])apply (online|now|here|through|via)([^a-z]|$)"
# A full stop inside an address (jane.doe@contoso.com) is followed by a
# letter, never by a space, so it never ends a sentence here.
SENTENCE_BREAK = r"[.!?]+\s+|\n+"
# The same break with its punctuation captured, so a quoted sentence keeps its
# full stop. Only the display needs it: no pattern above depends on a
# sentence's last character, so the SQL's split decides the same.
_BREAK_KEEP = r"([.!?]+)\s+|\n+"
assert _BREAK_KEEP.replace("(", "").replace(")", "") == SENTENCE_BREAK

for _p in (ADDRESS, CV_WORD, ONLINE, SENTENCE_BREAK):
    assert "'" not in _p and "%" not in _p, _p


def _sentences(jd: str):
    parts = re.split(_BREAK_KEEP, jd)
    # [text, punctuation-or-None, text, …]: each sentence takes back its own.
    for i in range(0, len(parts), 2):
        s = (parts[i] + (parts[i + 1] or "" if i + 1 < len(parts) else "")).strip()
        if s:
            yield s


def instruction(jd_text: str | None) -> dict | None:
    """The first sentence of a JD that asks for a CV by email and does not
    also offer to apply online: {"to": [its addresses, in order],
    "sentence": the sentence, verbatim}. None when there is none."""
    for s in _sentences(jd_text or ""):
        if (re.search(ADDRESS, s) and re.search(CV_WORD, s, re.I)
                and not re.search(ONLINE, s, re.I)):
            to = []
            for m in re.finditer(ADDRESS, s):
                if m.group(0) not in to:
                    to.append(m.group(0))
            return {"to": to, "sentence": s}
    return None


def asks_by_email_sql(jd: str) -> str:
    """instruction(jd) is not None, as a SQL boolean over a text column. The
    patterns hold no quote or percent sign, so they inline safely into a
    query that also takes psycopg parameters."""
    return (f"EXISTS (SELECT 1 FROM regexp_split_to_table({jd}, '{SENTENCE_BREAK}') AS ea(s) "
            f"WHERE ea.s ~ '{ADDRESS}' AND ea.s ~* '{CV_WORD}' AND ea.s !~* '{ONLINE}')")


def mailto(found: dict, title: str | None) -> str:
    """A `mailto:` link to every address the sentence names, the subject
    naming the role. A plain link, so the page needs no script for it."""
    to = ",".join(found["to"])
    return f"mailto:{to}?subject={quote(f'Application: {title}' if title else 'Application')}"
