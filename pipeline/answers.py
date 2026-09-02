"""Application-form Q&A: the one place screening answers are normalised and
written (`application_answers`, migration 009).

Why a module rather than inline SQL in web.py: `question_norm` is a grouping
key, and a grouping key that two callers compute differently silently splits
the answer bank in half. Same argument as `norm_company` (invariant #4) — one
implementation, in Python, never in SQL.

The write path is deliberately last-write-wins per (application, question,
occurrence): re-capturing a job you re-applied to should show what you told
them THIS time, not accumulate a pile of near-identical rows nobody can read.

`occurrence` is what lets one form ask the same question twice — a work-history
repeater asks "Industry" once per employer (migration 010). It is assigned HERE
from the order the capture arrived in, not taken from the client: the extension
already sends the form's controls in DOM order, and deriving the index server-
side means the two callers can't disagree about it, same argument as
question_norm itself.
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

# The apply form's own CONTROLS, which the DOM sweep cannot tell apart from a
# screening question — both are just labelled inputs. They are not questions an
# employer asked, and /answers exists to show "the same question, asked by many
# employers", so each one lands there as a group nobody was ever asked.
#
# The resume picker is a radio group whose selected member is labelled with the
# action available on it — "Deselect resume <file>.pdf" — so the label and the
# recorded value came out as the SAME string, 24 times over two groups. That
# one is not dropped but PROMOTED: which resume you sent is real, useful, and
# already captured (migration 014).
_RESUME_RE = re.compile(r"^\s*(?:de)?select\s+resume\s+(.+?)\s*$", re.I)
# The rebuilt Easy Apply (Aug 2026) stopped saying so. Its resume cards are
# <div role="radio" aria-label="<file>.pdf"> under a heading that reads
# "Resume*", so the pair arrives as question "Resume…" / answer "<file>.pdf":
# the filename is the ANSWER, and nothing in the label says "picker".
# Recognised by the shape of the pair instead — a radio whose question opens
# with the word and whose answer is a BARE document filename. Bare on purpose:
# a genuine "Resume link" question answered with a URL is a question.
_RESUME_HEAD = re.compile(r"^resume\b")
_RESUME_FILE = re.compile(r"^[^/\\:]+\.(?:pdf|docx?)$", re.I)

# The rest are LinkedIn UI toggles, answered by ticking a box rather than by
# saying anything. "Follow <employer>" is the worst of them: the employer's
# name is IN the label, so it never groups — one dead singleton row per company
# you apply to. Matched as anchored patterns against the NORMALISED text, not
# as loose prefixes: a bare "follow" prefix would also swallow a real question
# that happens to open with the word.
_CONTROL_NORM_RES = (
    re.compile(r"^mark job as a top choice\b"),
    re.compile(r"^follow .+ to stay up to date\b"),
)


def _is_resume_pick(question: str, answer: str | None, field_type: str | None) -> bool:
    """Either layout's resume picker — see _RESUME_RE and _RESUME_HEAD."""
    if _RESUME_RE.match(question or ""):
        return True
    return (field_type in (None, "radio")
            and bool(_RESUME_HEAD.match(norm_question(question)))
            and bool(_RESUME_FILE.match((answer or "").strip())))


def _control_kind(question: str, answer: str | None = None,
                  field_type: str | None = None) -> str | None:
    """'resume', 'drop', or None for a genuine question."""
    if _is_resume_pick(question, answer, field_type):
        return "resume"
    norm = norm_question(question)
    if any(rx.match(norm) for rx in _CONTROL_NORM_RES):
        return "drop"
    return None


def resume_file(items) -> str | None:
    """Which resume the form had selected, from a capture's raw answer list.

    LAST one wins. The extension accumulates across wizard steps in first-seen
    order, so switching from A to B arrives as [A, B] and B is the live choice.
    The one case this gets wrong is switching A -> B -> back to A: A keeps its
    original position, so B still reads as last. Nothing in the arrival order
    can distinguish that, and a single switch is the shape actually seen in the
    data (6 applications), so it is left as a known edge rather than guessed at.
    """
    found = None
    for raw in items or []:
        question = raw.get("question") or ""
        m = _RESUME_RE.match(question)
        if m:
            found = m.group(1).strip()
        elif _is_resume_pick(question, raw.get("answer"), raw.get("type")):
            found = (raw.get("answer") or "").strip()
    return found[:MAX_QUESTION] if found else None


def norm_question(q: str) -> str:
    """Grouping key for 'the same question, asked again'.

    Punctuation, case, and whitespace differ freely between one employer's
    form and the next ("Years of experience with Python?" vs "Years of
    experience with Python*"), while the question is plainly the same one.
    Strip all of it and keep the words.
    """
    return _NOISE.sub(" ", _REQUIRED.sub("", q or "").lower()).strip()


def clean(items) -> list[dict]:
    """Normalise a capture's raw answer list: trim, cap, drop the unusable, and
    number repeats. Returns items ready to insert, in form order.

    Repeats are KEPT, each with its own `occurrence` (0, 1, 2 … in form order)
    — a repeater section asking "City" once per employer is N answers, not one
    answer corrected N times. Before migration 010 this collapsed them and only
    the last survived.
    """
    out: list[dict] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(items or []):
        question = (raw.get("question") or "").strip()
        answer = (raw.get("answer") or "").strip()
        norm = norm_question(question)
        if not norm or not answer:
            continue
        # Form chrome, not a question. The resume picker is read out of the
        # same list by resume_file() and stored as a column instead.
        if _control_kind(question, answer, raw.get("type")):
            continue
        norm = norm[:MAX_QUESTION]
        occurrence = seen.get(norm, 0)
        seen[norm] = occurrence + 1
        out.append({
            "question": question[:MAX_QUESTION],
            "question_norm": norm,
            "answer": answer[:MAX_ANSWER],
            "field_type": (raw.get("type") or None),
            "ordinal": i,
            "occurrence": occurrence,
        })
    return out[:MAX_ITEMS]


def store(conn, user_id, application_id, posting_id, items) -> int:
    """Upsert one capture's answers onto an application. Returns rows written."""
    rows = clean(items)
    for r in rows:
        conn.execute(
            """
            INSERT INTO application_answers
                (user_id, application_id, posting_id, question, question_norm,
                 answer, field_type, ordinal, occurrence)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (application_id, question_norm, occurrence) DO UPDATE SET
                question    = EXCLUDED.question,
                answer      = EXCLUDED.answer,
                field_type  = COALESCE(EXCLUDED.field_type, application_answers.field_type),
                posting_id  = COALESCE(EXCLUDED.posting_id, application_answers.posting_id),
                ordinal     = COALESCE(EXCLUDED.ordinal, application_answers.ordinal),
                captured_at = now()
            """,
            (user_id, application_id, posting_id, r["question"], r["question_norm"],
             r["answer"], r["field_type"], r["ordinal"], r["occurrence"]))

    # Re-capturing a form filled with FEWER repeat entries than last time (you
    # deleted an employer from your work history) would otherwise leave the
    # extra occurrences behind forever — nothing overwrites them, and they'd
    # read as answers the user never gave. Scoped to questions this capture
    # actually asked, so a capture that simply couldn't see part of the form
    # can't delete the part an earlier one did see.
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["question_norm"]] = counts.get(r["question_norm"], 0) + 1
    for norm, n in counts.items():
        conn.execute(
            "DELETE FROM application_answers "
            "WHERE application_id = %s AND question_norm = %s AND occurrence >= %s",
            (application_id, norm, n))
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
ORDER BY aa.question_norm, aa.captured_at DESC, aa.occurrence
"""
