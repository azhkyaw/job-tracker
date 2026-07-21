"""Duplicate detection and job merging (design doc §10).

scan_posting() runs after a posting gains an embedding. Blocking finds
candidate pairs by company match OR high embedding similarity (the
agency-repost case where the company name differs); refinement applies the
thresholds; auto-links merge at the job level; the pending band waits for a
human in the triage queue.

merge_jobs() is the only place records combine, and it is deliberately
conservative: events, artifacts, emails, contacts, and postings all re-point
to the surviving job/application; nothing is deleted except the emptied
loser rows.
"""

from __future__ import annotations

from . import config

_PAIR_SQL = """
SELECT p2.id AS other_id,
       1 - (p.jd_embedding <=> p2.jd_embedding)                       AS cos,
       similarity(coalesce(p.title, ''), coalesce(p2.title, ''))     AS tsim
FROM postings p
JOIN postings p2
  ON p2.user_id = p.user_id
 AND p2.id <> p.id
 AND p2.jd_embedding IS NOT NULL
WHERE p.id = %(posting_id)s
  AND p.jd_embedding IS NOT NULL
  AND (p2.company_norm = p.company_norm
       OR similarity(coalesce(p2.company_norm,''), coalesce(p.company_norm,'')) >= %(cmin)s
       OR (p.jd_embedding <=> p2.jd_embedding) <= %(block_dist)s)
"""


def _job_of(conn, posting_id) -> dict | None:
    return conn.execute(
        "SELECT p.job_id, j.created_at FROM postings p JOIN jobs j ON j.id = p.job_id "
        "WHERE p.id = %s", (posting_id,)).fetchone()


def _record_pair(conn, user_id, a, b, tsim, cos, state) -> None:
    conn.execute(
        """
        INSERT INTO duplicate_candidates
            (user_id, posting_a, posting_b, title_sim, cosine_sim, state)
        VALUES (%s, LEAST(%s::uuid, %s::uuid), GREATEST(%s::uuid, %s::uuid),
                %s, %s, %s)
        ON CONFLICT (posting_a, posting_b) DO NOTHING
        """, (user_id, a, b, a, b, tsim, cos, state))


def _pending_pair_exists(conn, job_a, job_b) -> bool:
    """A pending candidate between ANY postings of these two jobs already
    covers the human decision — one triage card per job pair."""
    return conn.execute(
        """
        SELECT 1 FROM duplicate_candidates d
        JOIN postings x ON x.id = d.posting_a
        JOIN postings y ON y.id = d.posting_b
        WHERE d.state = 'pending'
          AND ((x.job_id = %(a)s AND y.job_id = %(b)s)
            OR (x.job_id = %(b)s AND y.job_id = %(a)s))
        """, {"a": job_a, "b": job_b}).fetchone() is not None


def _sweep_moot_pendings(conn, user_id) -> None:
    """After a merge, pending pairs whose postings now share a job are moot."""
    conn.execute(
        """
        UPDATE duplicate_candidates d SET state = 'confirmed'
        FROM postings x, postings y
        WHERE d.user_id = %s AND d.state = 'pending'
          AND d.posting_a = x.id AND d.posting_b = y.id
          AND x.job_id = y.job_id
        """, (user_id,))


def merge_jobs(conn, user_id, keep_job, drop_job) -> None:
    """Fold drop_job into keep_job. Applications are unique per (user, job):
    when both sides have one, the loser's history moves to the winner before
    the loser row goes."""
    if str(keep_job) == str(drop_job):
        return
    wa = conn.execute("SELECT id FROM applications WHERE user_id = %s AND job_id = %s",
                      (user_id, keep_job)).fetchone()
    la = conn.execute("SELECT id FROM applications WHERE user_id = %s AND job_id = %s",
                      (user_id, drop_job)).fetchone()
    if la and wa:
        for sql in (
            "UPDATE events SET application_id = %(w)s WHERE application_id = %(l)s",
            "UPDATE artifacts SET application_id = %(w)s WHERE application_id = %(l)s",
            "UPDATE emails SET matched_application_id = %(w)s WHERE matched_application_id = %(l)s",
        ):
            conn.execute(sql, {"w": wa["id"], "l": la["id"]})
        conn.execute("DELETE FROM applications WHERE id = %s", (la["id"],))
    elif la and not wa:
        conn.execute("UPDATE applications SET job_id = %s WHERE id = %s",
                     (keep_job, la["id"]))
    conn.execute("UPDATE postings SET job_id = %s WHERE job_id = %s", (keep_job, drop_job))
    conn.execute("UPDATE contacts SET job_id = %s WHERE job_id = %s", (keep_job, drop_job))
    conn.execute("DELETE FROM jobs WHERE id = %s", (drop_job,))
    _sweep_moot_pendings(conn, user_id)


def _merge_postings(conn, user_id, posting_a, posting_b) -> None:
    """Merge the jobs of two postings; the older job survives (its history is
    longer). Safe to call when they already share a job."""
    ja, jb = _job_of(conn, posting_a), _job_of(conn, posting_b)
    if not ja or not jb or ja["job_id"] == jb["job_id"]:
        return
    keep, drop = (ja, jb) if ja["created_at"] <= jb["created_at"] else (jb, ja)
    merge_jobs(conn, user_id, keep["job_id"], drop["job_id"])


def scan_posting(conn, user_id, posting_id) -> dict:
    """§10 steps 1–3 for one posting. Returns counts for logging/tests."""
    rows = conn.execute(_PAIR_SQL, {
        "posting_id": posting_id,
        "cmin": config.COMPANY_TRGM_MIN,
        "block_dist": round(1 - config.DEDUP_PENDING_COS, 6),
    }).fetchall()
    out = {"auto": 0, "pending": 0, "skipped": 0}
    for r in rows:
        cos, tsim = float(r["cos"]), float(r["tsim"])
        # Same canonical job already (earlier merge in this scan) -> nothing to do.
        ja, jb = _job_of(conn, posting_id), _job_of(conn, r["other_id"])
        if ja and jb and ja["job_id"] == jb["job_id"]:
            out["skipped"] += 1
            continue
        if cos >= config.DEDUP_AUTO_COS and tsim >= config.DEDUP_AUTO_TITLE:
            _record_pair(conn, user_id, posting_id, r["other_id"], tsim, cos, "auto")
            _merge_postings(conn, user_id, posting_id, r["other_id"])
            out["auto"] += 1
        elif cos >= config.DEDUP_PENDING_COS:
            if _pending_pair_exists(conn, ja["job_id"], jb["job_id"]):
                out["skipped"] += 1
                continue
            _record_pair(conn, user_id, posting_id, r["other_id"], tsim, cos, "pending")
            out["pending"] += 1
        else:
            out["skipped"] += 1
    return out


def resolve_candidate(conn, cand_id, confirm: bool) -> None:
    """Triage resolution of a pending pair."""
    cand = conn.execute(
        "SELECT * FROM duplicate_candidates WHERE id = %s::uuid AND state = 'pending'",
        (cand_id,)).fetchone()
    if cand is None:
        raise ValueError("pending duplicate candidate not found")
    if confirm:
        _merge_postings(conn, cand["user_id"], cand["posting_a"], cand["posting_b"])
    conn.execute("UPDATE duplicate_candidates SET state = %s WHERE id = %s",
                 ("confirmed" if confirm else "rejected", cand["id"]))
