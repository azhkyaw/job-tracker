"""Identity numbers out of stored mail (10 Oct 2026).

answers.is_sensitive() has withheld identity numbers from screening answers
since 24 Sep 2026, on the ground that the value never helps and is the worst
thing a leaked tracker could hold. Mail bodies had no such rule: that night's
wider data audit found the author's FIN number in a stored body, a reply they
had SENT a recruiter ("FIN Number: …"), and every mail body also goes to the
classifier. A body has no question to judge it by, so the rule here reads
the VALUES:

  - a Singapore NRIC or FIN anywhere, a prefix letter, seven digits and a
    check letter, when the check letter is the one ICA's published weighted
    sum gives (S/T/F/G), so a requisition shaped like one is left alone. The
    M series (2022) is matched by shape only: its check table is not
    verified here;
  - a Malaysian MyKad number (YYMMDD-PB-####) anywhere;
  - any value after an identity label and a colon ("FIN Number:",
    "Passport No:", "Date of birth:"), when it holds a digit, whatever its
    format, which is what catches a passport.

Each is replaced by answers.REDACTED. mailbox.store_message applies it to
every message both providers store, so neither the database nor the model
ever holds the value; stored_mail() is the same rule over rows stored
before it (cli redact-mail, dry run by default; there is no undo in the
database, though Gmail still holds the message)."""
from __future__ import annotations

import re

from .answers import REDACTED

_SG_ID = re.compile(r"\b([STFGM])(\d{7})([A-Z])\b")
_WEIGHTS = (2, 7, 6, 5, 4, 3, 2)
_CHECK = {"ST": "JZIHGFEDCBA", "FG": "XWUTRQPNMLK"}


def _sg_valid(prefix: str, digits: str, check: str) -> bool:
    if prefix == "M":
        return True
    total = sum(int(d) * w for d, w in zip(digits, _WEIGHTS)) + (4 if prefix in "TG" else 0)
    table = _CHECK["ST"] if prefix in "ST" else _CHECK["FG"]
    return table[total % 11] == check


_MYKAD = re.compile(r"\b\d{6}-\d{2}-\d{4}\b")
_LABEL = re.compile(
    r"(?im)\b(?:(?:nric|fin)(?:\s*/\s*(?:nric|fin))?(?:\s*(?:no\.?|number|#))?"
    r"|ic\s*(?:no\.?|number)|mykad(?:\s*(?:no\.?|number))?"
    r"|passport(?:\s*(?:no\.?|number|#))?"
    r"|national\s+(?:id|identity|identification|registration)(?:\s*(?:card|no\.?|number))?"
    r"|identity\s+card(?:\s*(?:no\.?|number))?"
    r"|date\s+of\s+birth|d\.?o\.?b\.?)"
    r"\s*[:：]\s*(?P<value>[^\n,;|]{1,40})")


def identity_numbers(text: str | None) -> tuple[str | None, int]:
    """`text` with every identity number replaced, and how many were."""
    if not text:
        return text, 0
    n = 0

    def label(m: re.Match) -> str:
        nonlocal n
        value = m.group("value").rstrip()
        if not re.search(r"\d", value) or value == REDACTED:
            return m.group(0)
        n += 1
        start = m.start("value") - m.start()
        return m.group(0)[:start] + REDACTED + m.group(0)[start + len(value):]

    def sg(m: re.Match) -> str:
        nonlocal n
        if not _sg_valid(*m.groups()):
            return m.group(0)
        n += 1
        return REDACTED

    def mykad(m: re.Match) -> str:
        nonlocal n
        n += 1
        return REDACTED

    text = _LABEL.sub(label, text)
    text = _SG_ID.sub(sg, text)
    text = _MYKAD.sub(mykad, text)
    return text, n


def stored_mail(conn, apply: bool = False) -> list[dict]:
    """identity_numbers() over every stored mail's subject and body. Returns
    the rows that would change (id, date, subject's start, count: never a
    value); writes only when `apply`, each UPDATE guarded on the text still
    being what was read. Admin connection, every user's rows."""
    rows = conn.execute("SELECT id, received_at, subject, body_text FROM emails").fetchall()
    plan = []
    for r in rows:
        subject, ns = identity_numbers(r["subject"])
        body, nb = identity_numbers(r["body_text"])
        if ns or nb:
            plan.append({"id": r["id"], "received_at": r["received_at"],
                         "subject": (r["subject"] or "")[:60] if not ns else "(subject withheld)",
                         "count": ns + nb, "_new": (subject, body), "_old": (r["subject"], r["body_text"])})
    if apply and plan:
        with conn.transaction():
            for p in plan:
                n = conn.execute(
                    "UPDATE emails SET subject = %s, body_text = %s WHERE id = %s "
                    "AND subject IS NOT DISTINCT FROM %s AND body_text IS NOT DISTINCT FROM %s",
                    (*p["_new"], p["id"], *p["_old"])).rowcount
                assert n == 1, p["id"]
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in plan]
