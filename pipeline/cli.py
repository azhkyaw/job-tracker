"""CLI. Run as: python -m pipeline.cli <command>

  auth [--oauth] [--email x]  connect Gmail: IMAP app password by default
                          (prompts for address + password, verifies before
                          storing); --oauth for the legacy single-user
                          desktop OAuth flow (prints a URL; token cached on
                          disk, not tied to a tracker account)
  backfill [-m N] [-d N]  first-run inbox reconstruction, default 12 months
                          (-d overrides -m, e.g. -d 1 for the last day)
  sync               incremental pull — this is the 15-minute cron entry
  scan               enqueue JD extraction/embedding for the backlog\n  work [--once]      run the queue worker (loop, or drain-and-exit)\n  serve              start the web UI (default http://127.0.0.1:8000)
  status             quick counts for a terminal sanity check
"""

from __future__ import annotations

import argparse

from . import config, db, gmail_sync, mailbox, worker


def _resolve_user(conn, email: str | None) -> dict:
    if email:
        user = conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
        if user is None:
            raise SystemExit(f"no user {email}")
        return user
    user = conn.execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()
    if user is None:
        raise SystemExit("no user row — sign up in the web UI first, or see README setup")
    return user


def cmd_auth(args) -> None:
    if args.oauth:
        gmail_sync.get_service()
        print(f"OK — token cached at {config.GMAIL_TOKEN_FILE}")
        return

    import getpass
    import json
    from datetime import datetime, timezone

    from . import auth, gmail_imap

    with db.connect() as conn:
        user = _resolve_user(conn, args.email)
        address = input(f"Gmail address [{user['email']}]: ").strip() or user["email"]
        # Never as an argv flag — an app password in shell history is a
        # full-mailbox credential (read, modify, delete, and send).
        app_password = "".join(getpass.getpass(
            "app password (myaccount.google.com/apppasswords): ").split())
        err = gmail_imap.verify(address, app_password)
        if err:
            raise SystemExit(err)
        payload = json.dumps({
            "kind": "imap", "provider": "gmail", "address": address,
            "app_password": app_password,
            "connected_at": datetime.now(timezone.utc).isoformat(),
        })
        with conn.transaction():
            conn.execute("UPDATE users SET gmail_credentials = %s WHERE id = %s",
                         (auth.encrypt(payload), user["id"]))
    print(f"OK — Gmail connected over IMAP as {address}. "
          f"Next: python -m pipeline.cli backfill -m 12")


def cmd_backfill(args) -> None:
    with db.connect() as conn:
        user = _resolve_user(conn, args.email)
        try:
            provider = mailbox.provider_for_user(conn, user)
        except mailbox.MailboxError as err:
            raise SystemExit(str(err)) from None
        if provider is None:
            raise SystemExit(
                "no Gmail connected for this user — run `python -m pipeline.cli auth` "
                "(IMAP app password), or connect from the Settings page")
        try:
            months = args.days / 31 if args.days else args.months
            n = mailbox.backfill(conn, provider, user["id"], months=months)
        except mailbox.MailboxError as err:
            raise SystemExit(str(err)) from None
        finally:
            provider.close()
    print(f"backfill: {n} candidate emails stored and enqueued "
          f"(run 'work --once' to process)")


def cmd_sync(_args) -> None:
    with db.connect() as conn:
        users = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        failed = 0
        for user in users:
            try:
                provider = mailbox.provider_for_user(conn, user)
                if provider is None:
                    continue
                try:
                    n = mailbox.incremental(conn, provider, user["id"])
                finally:
                    provider.close()
                print(f"sync {user['email']} [{provider.kind}]: {n} new candidate emails")
            except mailbox.MailboxError as err:
                failed += 1
                print(f"sync {user['email']}: FAILED — {err}")
    if failed:
        raise SystemExit(1)


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
    p = sub.add_parser("auth")
    p.add_argument("--oauth", action="store_true",
                    help="legacy single-user desktop OAuth flow instead of IMAP")
    p.add_argument("--email", help="which tracker account to connect (default: first created)")
    p.set_defaults(fn=cmd_auth)
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
