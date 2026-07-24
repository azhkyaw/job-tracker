"""Funnel analytics (design doc §6.6). Pure SQL over the event log — this is
the payoff of status-as-events: response rates and time-to-response fall out
of timestamps, and 'focused' falls back to artifact existence (§7) via
COALESCE so untagged-but-prepped applications count correctly."""

from __future__ import annotations

from . import config

_RESPONSE_TYPES = "('viewed','interview_invite','rejected','offer')"

_APPS_CTE = f"""
WITH apps AS (
    SELECT a.id,
           a.job_id,
           COALESCE(p.platform, 'unknown') AS platform,
           COALESCE(a.focused, EXISTS (SELECT 1 FROM artifacts ar
                                       WHERE ar.application_id = a.id)) AS focused_eff,
           (SELECT min(occurred_at) FROM events e
             WHERE e.application_id = a.id AND e.type = 'applied')      AS applied_at,
           (SELECT min(occurred_at) FROM events e
             WHERE e.application_id = a.id
               AND e.type IN {_RESPONSE_TYPES})                          AS first_resp,
           EXISTS (SELECT 1 FROM events e
                   WHERE e.application_id = a.id
                     AND e.type IN ('interview_invite','offer'))         AS interviewed
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


def _rate(rows):
    for r in rows:
        r["response_rate"] = (round(100.0 * r["responded"] / r["applied"])
                              if r["applied"] else None)
    return rows


def summary(conn, user_id) -> dict:
    row = conn.execute(_APPS_CTE + """
        SELECT count(*) FILTER (WHERE applied_at IS NOT NULL) AS applied,
               count(*) FILTER (WHERE applied_at IS NULL)     AS interested,
               count(*) FILTER (WHERE first_resp IS NOT NULL) AS responded,
               count(*) FILTER (WHERE interviewed)            AS interviews,
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


def by_focus(conn, user_id):
    rows = _rate(conn.execute(
        _APPS_CTE + _GROUPED.format(
            dim="CASE WHEN focused_eff THEN 'focused' ELSE 'generic' END"),
        {"user_id": user_id}).fetchall())
    return rows


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
                 WHERE e.application_id = a.id AND e.type = 'applied') AS applied_at
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        WHERE a.user_id = %(user_id)s
          AND EXISTS (SELECT 1 FROM events e
                      WHERE e.application_id = a.id AND e.type = 'applied'
                        AND e.occurred_at < now() - make_interval(days => %(days)s))
          AND NOT EXISTS (SELECT 1 FROM events e
                          WHERE e.application_id = a.id
                            AND e.type IN ('viewed','interview_invite','rejected',
                                           'offer','withdrawn','follow_up_sent'))
        ORDER BY applied_at
    """, {"user_id": user_id, "days": config.REMINDER_DAYS}).fetchall()
