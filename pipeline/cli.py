"""CLI. Run as: python -m pipeline.cli <command>

  auth               one-time Gmail OAuth (prints a URL; token cached on disk)
  backfill [-m N] [-d N]  first-run inbox reconstruction, default 12 months
                          (-d overrides -m, e.g. -d 1 for the last day)
  sync               incremental pull — this is the 15-minute cron entry
  scan               enqueue JD extraction/embedding for the backlog\n  work [--once]      run the queue worker (loop, or drain-and-exit)\n  serve              start the web UI (default http://127.0.0.1:8000)
  status             quick counts for a terminal sanity check
"""

from __future__ import annotations

import argparse

from . import config, db, gmail_sync, worker


def cmd_auth(_args) -> None:
    gmail_sync.get_service()
    print(f"OK — token cached at {config.GMAIL_TOKEN_FILE}")


def cmd_backfill(args) -> None:
    with db.connect() as conn:
        if args.email:
            user = conn.execute("SELECT * FROM users WHERE email = %s",
                                (args.email,)).fetchone()
            if user is None:
                raise SystemExit(f"no user {args.email}")
        else:
            user = conn.execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()
        service = gmail_sync.service_for_user(conn, user)
        if service is None:
            raise SystemExit("no Gmail connected for this user — Settings page "
                             "(web OAuth) or `auth` (single-user desktop flow)")
        months = args.days / 31 if args.days else args.months
        n = gmail_sync.backfill(conn, service, user["id"], months=months)
    print(f"backfill: {n} candidate emails stored and enqueued "
          f"(run 'work --once' to process)")


def cmd_sync(_args) -> None:
    with db.connect() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        for user in users:
            service = gmail_sync.service_for_user(conn, user)
            if service is None:
                continue
            n = gmail_sync.incremental(conn, service, user["id"])
            print(f"sync {user['email']}: {n} new candidate emails")


def cmd_work(args) -> None:
    worker.run(once=args.once)


def cmd_passwd(args) -> None:
    """Set/reset a user's password and mint a fresh API token — the upgrade
    path for pre-Phase-4 users and an offline recovery tool."""
    import getpass
    from . import auth
    with db.connect() as conn, conn.transaction():
        user = conn.execute("SELECT id, email FROM users WHERE email = %s",
                            (args.email,)).fetchone()
        if user is None:
            raise SystemExit(f"no user {args.email} — sign up in the web UI instead")
        pw = getpass.getpass("new password: ")
        if len(pw) < 8:
            raise SystemExit("password must be at least 8 characters")
        token, token_hash = auth.new_api_token()
        conn.execute("UPDATE users SET password_hash = %s, api_token_hash = %s "
                     "WHERE id = %s", (auth.hash_password(pw), token_hash, user["id"]))
    print(f"password set for {args.email}")
    print(f"new API token (shown once, update the extension): {token}")


def cmd_scan(_args) -> None:
    """Enqueue Phase 3 work for the backlog: extraction for postings with a JD
    but no extractions row; embedding (when VOYAGE_API_KEY is set) for postings
    with a JD but no vector."""
    from . import config, embeddings
    with db.connect() as conn, conn.transaction():
        user_id = db.single_user_id(conn)
        need_x = conn.execute(
            "SELECT id FROM postings WHERE user_id = %s AND jd_text IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM extractions x WHERE x.posting_id = postings.id)",
            (user_id,)).fetchall()
        for r in need_x:
            db.enqueue(conn, user_id, "extract_jd", {"posting_id": str(r["id"])})
        n_e = 0
        if embeddings.available():
            need_e = conn.execute(
                "SELECT id FROM postings WHERE user_id = %s AND jd_text IS NOT NULL "
                "AND jd_embedding IS NULL", (user_id,)).fetchall()
            for r in need_e:
                db.enqueue(conn, user_id, "embed_jd", {"posting_id": str(r["id"])})
            n_e = len(need_e)
        print(f"scan: enqueued {len(need_x)} extractions, {n_e} embeddings"
              + ("" if embeddings.available() else " (no VOYAGE_API_KEY — embeddings skipped)"))


def cmd_serve(args) -> None:
    import uvicorn
    uvicorn.run("pipeline.web:app", host=args.host, port=args.port, reload=True)


def cmd_status(_args) -> None:
    with db.connect() as conn:
        for label, sql in [
            ("emails by triage_state",
             "SELECT triage_state, count(*) FROM emails GROUP BY 1 ORDER BY 1"),
            ("queue by state",
             "SELECT state, count(*) FROM job_queue GROUP BY 1 ORDER BY 1"),
            ("applications by status",
             "SELECT status, count(*) FROM application_status GROUP BY 1 ORDER BY 1"),
        ]:
            print(f"\n{label}:")
            for row in conn.execute(sql).fetchall():
                print(f"  {list(row.values())[0]:<16} {list(row.values())[1]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth").set_defaults(fn=cmd_auth)
    p = sub.add_parser("backfill")
    p.add_argument("-m", "--months", type=int, default=config.BACKFILL_MONTHS_DEFAULT)
    p.add_argument("-d", "--days", type=int,
                    help="backfill only the last N days (overrides --months)")
    p.add_argument("--email", help="which account's Gmail to backfill")
    p.set_defaults(fn=cmd_backfill)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("scan").set_defaults(fn=cmd_scan)
    p = sub.add_parser("passwd")
    p.add_argument("email")
    p.set_defaults(fn=cmd_passwd)
    p = sub.add_parser("work")
    p.add_argument("--once", action="store_true", help="drain the queue and exit")
    p.set_defaults(fn=cmd_work)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
