"""Application-form Q&A: the one place screening answers are normalised and
written (`application_answers`, migration 009).

Why a module rather than inline SQL in web.py: `question_norm` is a grouping
key, and a grouping key that two callers compute differently silently splits
the answer bank in half. Same argument as `norm_company` (invariant #4) — one
implementation, in Python, never in SQL.

The write path is deliberately last-write-wins per (application, question):
re-capturing a job you re-applied to should show what you told them THIS time,
not accumulate a pile of near-identical rows nobody can read.
"""

from __future__ import annotations

import re

# Caps applied here, at the boundary, rather than rejecting the capture: an
# over-long answer is a formatting surprise, not a reason to lose the whole
# application record the user just submitted. Generous enough for a pasted
# cover letter in a textarea, small enough that a runaway DOM scrape can't
# dump a page into the DB.
MAX_QUESTION = 300
MAX_ANSWER = 4000
MAX_ITEMS = 60

_NOISE = re.compile(r"[^a-z0-9]+")
# Trailing required-markers the platforms append to the visible label; they're
# chrome, not part of the question, and they come and go between layouts.
_REQUIRED = re.compile(r"\s*(\*|\(required\)|required)\s*$", re.I)


def norm_question(q: str) -> str:
    """Grouping key for 'the same question, asked again'.

    Punctuation, case, and whitespace differ freely between one employer's
    form and the next ("Years of experience with Python?" vs "Years of
    experience with Python*"), while the question is plainly the same one.
    Strip all of it and keep the words.
    """
    return _NOISE.sub(" ", _REQUIRED.sub("", q or "").lower()).strip()


def clean(items) -> list[dict]:
    """Normalise a capture's raw answer list: trim, cap, drop the unusable,
    and collapse duplicate questions (last one wins, matching the DB's
    ON CONFLICT). Returns items ready to insert, in form order."""
    out: dict[str, dict] = {}
    for i, raw in enumerate(items or []):
        question = (raw.get("question") or "").strip()
        answer = (raw.get("answer") or "").strip()
        norm = norm_question(question)
        if not norm or not answer:
            continue
        out[norm] = {
            "question": question[:MAX_QUESTION],
            "question_norm": norm[:MAX_QUESTION],
            "answer": answer[:MAX_ANSWER],
            "field_type": (raw.get("type") or None),
            # First sighting keeps its place in the form; a later correction of
            # the same question shouldn't jump to the end of the list.
            "ordinal": out[norm]["ordinal"] if norm in out else i,
        }
    return sorted(out.values(), key=lambda r: r["ordinal"])[:MAX_ITEMS]


def store(conn, user_id, application_id, posting_id, items) -> int:
    """Upsert one capture's answers onto an application. Returns rows written."""
    rows = clean(items)
    for r in rows:
        conn.execute(
            """
            INSERT INTO application_answers
                (user_id, application_id, posting_id, question, question_norm,
                 answer, field_type, ordinal)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (application_id, question_norm) DO UPDATE SET
                question    = EXCLUDED.question,
                answer      = EXCLUDED.answer,
                field_type  = COALESCE(EXCLUDED.field_type, application_answers.field_type),
                posting_id  = COALESCE(EXCLUDED.posting_id, application_answers.posting_id),
                ordinal     = COALESCE(EXCLUDED.ordinal, application_answers.ordinal),
                captured_at = now()
            """,
            (user_id, application_id, posting_id, r["question"], r["question_norm"],
             r["answer"], r["field_type"], r["ordinal"]))
    return len(rows)


# One row per distinct question the user has ever been asked, newest answer
# first. `variants` is what makes this worth reading: a question you've always
# answered the same way needs no thought, one you've answered three different
# ways is a decision you keep re-making.
BANK_SQL = """
SELECT question_norm,
       (array_agg(question ORDER BY captured_at DESC))[1]   AS question,
       (array_agg(answer   ORDER BY captured_at DESC))[1]   AS answer,
       (array_agg(field_type ORDER BY captured_at DESC))[1] AS field_type,
       count(*)                        AS times_asked,
       count(DISTINCT lower(answer))   AS variants,
       max(captured_at)                AS last_asked
FROM application_answers
GROUP BY question_norm
ORDER BY max(captured_at) DESC
"""

# The other answers given to one question, for the expandable detail under a
# bank row — which employer, what was said, when.
BANK_DETAIL_SQL = """
SELECT aa.question_norm, aa.answer, aa.captured_at, aa.application_id,
       j.title_canonical,
       COALESCE(
         (SELECT p.company_raw FROM postings p
           WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
           ORDER BY p.captured_at DESC LIMIT 1),
         j.company_norm) AS company_display
FROM application_answers aa
JOIN applications a ON a.id = aa.application_id
JOIN jobs j         ON j.id = a.job_id
WHERE aa.question_norm = ANY(%s)
ORDER BY aa.question_norm, aa.captured_at DESC
"""
