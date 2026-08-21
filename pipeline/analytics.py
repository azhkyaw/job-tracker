"""Funnel analytics (design doc §6.6). Pure SQL over the event log — this is
the payoff of status-as-events: response rates and time-to-response fall out
of timestamps.

The per-dimension splits are `by_platform` and `by_resume`. A `by_focus` used
to sit beside them, reading `applications.focused` with a COALESCE onto
artifact existence so an untagged-but-prepped application still counted as
focused. Both are gone (21 Aug 2026): the column never split — 175 `false`,
22 null and zero `true` across every real application — so the dimension had
one value and `_rate`'s own `len(rows) > 1` guard meant the panel never
rendered. `by_resume` is the split that question actually wanted, and it is
read off the apply form rather than asked for afterwards."""

from __future__ import annotations

from datetime import timedelta

from . import config

_RESPONSE_TYPES = "('viewed','engaged','interview_invite','rejected','offer')"

_APPS_CTE = f"""
WITH apps AS (
    SELECT a.id,
           a.job_id,
           COALESCE(p.platform, 'unknown') AS platform,
           a.resume_file,
           (SELECT min(occurred_at) FROM events e
             WHERE e.application_id = a.id AND e.type = 'applied')      AS applied_at,
           (SELECT min(occurred_at) FROM events e
             WHERE e.application_id = a.id
               AND e.type IN {_RESPONSE_TYPES})                          AS first_resp,
           EXISTS (SELECT 1 FROM events e
                   WHERE e.application_id = a.id
                     AND e.type IN ('interview_invite','offer'))         AS interviewed,
           EXISTS (SELECT 1 FROM events e
                   WHERE e.application_id = a.id AND e.type = 'offer')   AS offered
    FROM applications a
    LEFT JOIN postings p ON p.id = a.applied_via_posting_id
    WHERE a.user_id = %(user_id)s
)
"""

_GROUPED = """
SELECT {dim} AS dim,
       count(*) FILTER (WHERE applied_at IS NOT NULL)               AS applied,
       count(*) FILTER (WHERE first_resp IS NOT NULL)               AS responded,
       count(*) FILTER (WHERE interviewed)                          AS interviews,
       round(avg(EXTRACT(epoch FROM first_resp - applied_at) / 86400.0)::numeric, 1)
                                                                    AS avg_days_to_resp
FROM apps
WHERE applied_at IS NOT NULL
GROUP BY {dim}
ORDER BY applied DESC, dim
"""


# Below this many applications a per-dimension response rate is noise wearing a
# percent sign — 0% on n=3 says nothing about the technology, only about the
# sample. The raw counts stay visible; only the derived rate is withheld.
MIN_RATE_N = 5


def _rate(rows, min_n: int = MIN_RATE_N):
    for r in rows:
        r["response_rate"] = (round(100.0 * r["responded"] / r["applied"])
                              if r["applied"] >= min_n else None)
        r["thin"] = r["applied"] < min_n
    return rows


def summary(conn, user_id) -> dict:
    row = conn.execute(_APPS_CTE + """
        SELECT count(*) FILTER (WHERE applied_at IS NOT NULL) AS applied,
               count(*) FILTER (WHERE applied_at IS NULL)     AS interested,
               count(*) FILTER (WHERE first_resp IS NOT NULL) AS responded,
               count(*) FILTER (WHERE interviewed)            AS interviews,
               count(*) FILTER (WHERE offered)                AS offers,
               round(avg(EXTRACT(epoch FROM first_resp - applied_at) / 86400.0)::numeric, 1)
                                                              AS avg_days_to_resp
        FROM apps
    """, {"user_id": user_id}).fetchone()
    row["response_rate"] = (round(100.0 * row["responded"] / row["applied"])
                            if row["applied"] else None)
    return row


def by_platform(conn, user_id):
    return _rate(conn.execute(_APPS_CTE + _GROUPED.format(dim="platform"),
                              {"user_id": user_id}).fetchall())


def by_resume(conn, user_id):
    """Response rate per resume actually sent (migration 014).

    The dimension `focused` was meant to answer this shape of question and
    couldn't — it came out a constant. This one is a genuine split, because it
    is read off the form rather than asked for afterwards. Applications with no
    picker (external ATS, manual entry) group under a null dim, which the
    template drops: 'unknown' is not a resume, and padding the table with it
    would invite comparing a real resume against the absence of data.
    """
    rows = _rate(conn.execute(
        _APPS_CTE + _GROUPED.format(dim="resume_file"),
        {"user_id": user_id}).fetchall())
    return [r for r in rows if r["dim"]]


def by_technology(conn, user_id, limit: int = 12):
    return _rate(conn.execute(_APPS_CTE + """
        , tech AS (
            SELECT DISTINCT ap.id, initcap(lower(t.tech)) AS tech,
                   ap.applied_at, ap.first_resp, ap.interviewed
            FROM apps ap
            JOIN postings p ON p.job_id = ap.job_id
            JOIN extractions x ON x.posting_id = p.id
            CROSS JOIN LATERAL unnest(x.languages || x.technologies) AS t(tech)
            WHERE ap.applied_at IS NOT NULL
        )
        SELECT tech AS dim,
               count(*)                                        AS applied,
               count(*) FILTER (WHERE first_resp IS NOT NULL)  AS responded,
               count(*) FILTER (WHERE interviewed)             AS interviews,
               NULL::numeric                                   AS avg_days_to_resp
        FROM tech
        GROUP BY tech
        ORDER BY applied DESC, tech
        LIMIT %(limit)s
    """, {"user_id": user_id, "limit": limit}).fetchall())


def weekly(conn, user_id, weeks: int = 14):
    """Applications sent and replies received, per calendar week.

    Two measures an order of magnitude apart, so the page renders them as two
    separate panels rather than one dual-axis chart — the bar heights are
    percentages of each panel's OWN peak, and each panel direct-labels that
    peak so the scale is never implied.
    """
    rows = conn.execute(f"""
        SELECT date_trunc('week', e.occurred_at)::date          AS wk,
               count(*) FILTER (WHERE e.type = 'applied')       AS applied,
               count(*) FILTER (WHERE e.type IN {_RESPONSE_TYPES}) AS replied
        FROM events e
        JOIN applications a ON a.id = e.application_id
        WHERE a.user_id = %(user_id)s
          AND e.occurred_at >= date_trunc('week', now())
                             - make_interval(weeks => %(weeks)s - 1)
        GROUP BY 1 ORDER BY 1
    """, {"user_id": user_id, "weeks": weeks}).fetchall()
    if not rows:
        return []

    # Fill the gaps: a week with no activity is a real, meaningful zero, and
    # dropping it would compress the x axis into a lie about pacing.
    by_week = {r["wk"]: r for r in rows}
    cur, last = rows[0]["wk"], rows[-1]["wk"]
    out = []
    while cur <= last:
        r = by_week.get(cur, {"wk": cur, "applied": 0, "replied": 0})
        out.append(dict(r))
        cur += timedelta(days=7)

    for key in ("applied", "replied"):
        peak = max((r[key] for r in out), default=0)
        for r in out:
            r[f"{key}_h"] = f"{(100.0 * r[key] / peak) if peak else 0:.1f}%"
            r[f"{key}_peak"] = peak
    for r in out:
        r["label"] = f'{r["wk"]:%d %b}'.lstrip("0")
    return out


def reminders(conn, user_id):
    """Applied > REMINDER_DAYS ago, no response, no follow-up, not withdrawn."""
    return conn.execute(f"""
        SELECT a.id, j.title_canonical,
               COALESCE(
                 (SELECT p.company_raw FROM postings p
                   WHERE p.job_id = a.job_id AND p.company_raw IS NOT NULL
                   ORDER BY p.captured_at DESC LIMIT 1),
                 j.company_norm) AS company_display,
               (SELECT min(occurred_at) FROM events e
                 WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at,
               -- How long they've been sitting on it. The list can't reuse its
               -- own silent_days here: this runs as its own query, and the
               -- number is the whole reason a row is in this block.
               (SELECT date_part('day', now() - min(occurred_at))::int FROM events e
                 WHERE e.application_id = a.id AND e.type = 'applied') AS days_waiting
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.user_id = %(user_id)s
          AND EXISTS (SELECT 1 FROM events e
                      WHERE e.application_id = a.id AND e.type = 'applied'
                        AND e.occurred_at < now() - make_interval(days => %(days)s))
          AND NOT EXISTS (SELECT 1 FROM events e
                          WHERE e.application_id = a.id
                            AND e.type IN ('viewed','engaged','interview_invite','rejected',
                                           'offer','withdrawn','follow_up_sent'))
        ORDER BY applied_at
    """, {"user_id": user_id, "days": config.REMINDER_DAYS}).fetchall()
