"""Gmail IMAP ingest test (docs/email-ingest.md). Self-contained: creates no
users, seeds only emails/job_queue rows it can identify by their own
gmail_message_id (the hex of a fixture msgid it chooses), so it can run
anywhere in suite order without depending on or polluting other suites'
fixtures.

FakeIMAP swaps over gmail_imap.IMAP4_SSL (the same module-attribute
stubbing convention as email_classifier.classify_email = lambda ...) and
records every command it receives, so protocol invariants — readonly
select, BODY.PEEK, quoted/escaped X-GM-RAW, ascending UID order — are
assertable data, not just hoped for.

Covers: pure query/credential helpers; protocol invariants over the wire;
backfill (hex-identity dedup with the API path, oldest-first ordering, the
local candidate filter re-checking a server-side search hit, idempotent
re-sync); incremental (UID n:* returning a stale UID and the client-side
filter dropping it, a UIDVALIDITY change forcing a full re-search, a
bare-integer legacy cursor treated as absent); INTERNALDATE timezone
parsing; RFC 2047 subject decoding before the candidate filter runs; auth
failure surfacing as MailboxAuthError; credential-kind dispatch and
disconnect.

Run:  TRACKER_DATABASE_URL=postgresql:///tracker_test python3 tests/test_email_ingest.py
"""

import base64
import json
import os
import sys
from email.header import Header
from email.message import EmailMessage
from pathlib import Path

os.environ.setdefault("ANTHROPIC_API_KEY", "test-dummy-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imaplib

from pipeline import auth, config, db, gmail_imap, gmail_sync, mailbox

# config.py auto-loads the repo's gitignored .env, so a developer who has turned
# INGEST_ALL on for their own mailbox would otherwise silently change what this
# suite asserts — the pre-filter tests below would all pass vacuously, and
# backfill_query's pinned wire format would fail. Pin the filter ON as the
# baseline; the INGEST_ALL section flips it explicitly and restores it.
config.INGEST_ALL = False

TEST_ADDRESS = "test.imap.fixture@gmail.com"
GOOD_PASSWORD = "abcdabcdabcdabcd"  # 16 chars, matches a real app password's length


def check(label, cond, detail=""):
    if not cond:
        raise SystemExit(f"FAIL {label}: {detail}")
    print(f"  ok  {label}")


# --------------------------------------------------------------------------- fake IMAP server

class _FakeMsg:
    def __init__(self, msgid_decimal: int, internaldate: str, raw: bytes, matches: bool = True):
        self.msgid_decimal = msgid_decimal
        self.internaldate = internaldate
        self.raw = raw
        self.matches = matches  # whether Gmail's server-side X-GM-RAW would hit this


class FakeServer:
    """Mutable 'server state', shared across FakeIMAP connections within one
    scenario — mirrors a real IMAP server persisting between logins. Tests
    set FakeIMAP.server to one of these before constructing a provider,
    since ImapProvider constructs IMAP4_SSL(HOST, PORT, ...) itself with no
    way to inject test config through the constructor."""

    def __init__(self, uidvalidity: int):
        self.uidvalidity = uidvalidity
        self.messages: dict[int, _FakeMsg] = {}
        self.list_response = rb'(\HasNoChildren \All) "/" "[Gmail]/Tous les messages"'
        self.login_calls = 0

    def add(self, uid: int, msgid_decimal: int, internaldate: str, raw: bytes, matches: bool = True):
        self.messages[uid] = _FakeMsg(msgid_decimal, internaldate, raw, matches)


class FakeIMAP:
    """Records every command received. Raises the real imaplib.IMAP4.error
    on bad login so the provider's real except clauses are exercised."""

    server: "FakeServer | None" = None

    def __init__(self, host, port, timeout=None):
        assert FakeIMAP.server is not None, "test must set FakeIMAP.server before connecting"
        self.host, self.port, self.timeout = host, port, timeout
        self.commands: list[tuple] = []
        self.untagged_responses: dict = {}
        self._srv = FakeIMAP.server

    def login(self, user, password):
        self.commands.append(("LOGIN", user, password))
        self._srv.login_calls += 1
        if password != GOOD_PASSWORD:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return ("OK", [b"success"])

    def list(self):
        self.commands.append(("LIST",))
        return ("OK", [self._srv.list_response])

    def select(self, mailbox_name, readonly=False):
        self.commands.append(("SELECT", mailbox_name, readonly))
        if not readonly:
            raise AssertionError(
                "select() called without readonly=True — would EXAMINE become "
                "SELECT and mark a year of the operator's mail as read")
        uids = sorted(self._srv.messages)
        uidnext = (uids[-1] + 1) if uids else 1
        self.untagged_responses = {
            "UIDVALIDITY": [str(self._srv.uidvalidity).encode()],
            "UIDNEXT": [str(uidnext).encode()],
        }
        return ("OK", [str(len(uids)).encode()])

    def uid(self, command, *args):
        self.commands.append(("UID", command) + args)
        if command == "SEARCH":
            return self._search(args)
        if command == "FETCH":
            return self._fetch(args)
        raise NotImplementedError(command)

    def _search(self, args):
        uid_range = None
        rest = args
        if args and args[0] == "UID":
            lo_s, hi_s = args[1].split(":")
            lo = int(lo_s)
            hi = None if hi_s == "*" else int(hi_s)
            uid_range = (lo, hi)
            rest = args[2:]
        assert rest and rest[0] == "X-GM-RAW", f"expected X-GM-RAW criteria, got {rest!r}"
        all_uids = sorted(self._srv.messages)
        if uid_range is not None:
            lo, hi = uid_range
            existing_max = max(all_uids) if all_uids else 0
            if hi is None:
                if lo > existing_max:
                    # RFC 3501: "n:*" with n beyond the highest UID still
                    # matches the highest UID — the quirk gmail_imap.py's
                    # client-side `> last_uid` filter exists to defeat.
                    candidate = [existing_max] if existing_max else []
                else:
                    candidate = [u for u in all_uids if u >= lo]
            else:
                candidate = [u for u in all_uids if lo <= u <= hi]
        else:
            candidate = all_uids
        matched = sorted(u for u in candidate if self._srv.messages[u].matches)
        return ("OK", [(" ".join(str(u) for u in matched)).encode()])

    def _fetch(self, args):
        uid = int(args[0])
        spec = args[1]
        assert "BODY.PEEK[" in spec, f"fetch must use BODY.PEEK[], got: {spec}"
        assert "BODY[" not in spec.replace("BODY.PEEK[", ""), f"bare BODY[ in fetch spec: {spec}"
        msg = self._srv.messages.get(uid)
        if msg is None:
            return ("OK", [b")"])
        metadata = (f'{uid} (X-GM-MSGID {msg.msgid_decimal} INTERNALDATE "{msg.internaldate}" '
                    f'BODY[] {{{len(msg.raw)}}}').encode()
        return ("OK", [(metadata, msg.raw), b")"])

    def logout(self):
        self.commands.append(("LOGOUT",))
        return ("BYE", [b"LOGOUT"])


gmail_imap.IMAP4_SSL = FakeIMAP


def _mk_msg(sender: str, subject: str, body: str) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["Subject"] = subject
    m.set_content(body)
    return m.as_bytes()


def _mk_msg_rfc2047(sender: str, subject: str, body: str) -> bytes:
    """Like _mk_msg, but forces the ENTIRE subject into one RFC 2047
    encoded-word (Header(...).encode(), not EmailMessage's own per-word
    auto-encoding, which leaves ASCII runs as literal text). Q-encoding
    replaces spaces with "_", so the raw wire subject contains no space —
    a keyword substring match against the undecoded header is structurally
    impossible, proving is_candidate only ever sees the decoded text."""
    encoded_subject = Header(subject, "utf-8").encode()
    raw = (f"From: {sender}\r\n"
          f"Subject: {encoded_subject}\r\n"
          f"Content-Type: text/plain; charset=utf-8\r\n"
          f"MIME-Version: 1.0\r\n\r\n"
          f"{body}\r\n")
    return raw.encode("utf-8")


def _quoted_search_arg(commands, cmd="SEARCH"):
    for c in commands:
        if c[:2] == ("UID", cmd):
            return c[-1]
    return None


# --------------------------------------------------------------------------- A: pure functions

print("pure functions: query builders")


def _reference_backfill_query(months: int) -> str:
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    since = (_dt.now(_tz.utc) - _td(days=months * 31)).strftime("%Y/%m/%d")
    froms = " OR ".join(f"from:{d}" for d in sorted(config.ALLOWLIST_DOMAINS))
    subjects = " OR ".join(f'subject:"{kw}"' for kw in config.SUBJECT_KEYWORDS)
    return f"after:{since} (({froms}) OR ({subjects}))"


check("backfill_query(12) matches the pre-refactor formula byte-for-byte",
      mailbox.backfill_query(12) == _reference_backfill_query(12),
      (mailbox.backfill_query(12), _reference_backfill_query(12)))

print("pure functions: INGEST_ALL")

# The email that motivated the flag (Tailspin Consulting rejection, 7 Aug
# 2026): an employer's own domain, and a subject whose only application-ish
# phrase is split by the role title so no SUBJECT_KEYWORD matches. The
# "not moving forward" wording that WOULD match is in the body, which
# is_candidate never reads. Pinned in both directions so a future keyword
# addition can't quietly make the off-case pass and hide the regression.
_TAILSPIN_SENDER = "Humans of Tailspin <humans@tailspin-consulting.com>"
_TAILSPIN_SUBJECT = ("[Be@Tailspin] Thank you for your Full-Stack Developer "
                      "application to Tailspin Consulting")

check("INGEST_ALL off: the real missed rejection is NOT a candidate (the bug)",
      not mailbox.is_candidate(_TAILSPIN_SENDER, _TAILSPIN_SUBJECT))

_saved_ingest_all = config.INGEST_ALL
try:
    config.INGEST_ALL = True
    check("INGEST_ALL on: that same email IS a candidate",
          mailbox.is_candidate(_TAILSPIN_SENDER, _TAILSPIN_SUBJECT))
    check("INGEST_ALL on: even a sender and subject matching nothing at all",
          mailbox.is_candidate("A Friend <friend@example.invalid>", "lunch?"))
    check("INGEST_ALL on: filter_query() is empty, not a vacuous predicate",
          mailbox.filter_query() == "", repr(mailbox.filter_query()))
    from datetime import date as _date
    _q = mailbox.query_since(_date(2026, 8, 6))
    check("INGEST_ALL on: query_since is the bare date bound, no trailing space",
          _q == "after:2026/08/06", repr(_q))
    # gmail_imap builds its incremental UID search from filter_query() directly.
    # An empty X-GM-RAW argument goes on the wire as a valid-but-empty quoted
    # string and matches nothing, which would stall every incremental sync
    # silently — the criterion has to be dropped, not blanked.
    check("INGEST_ALL on: _quote() of the empty predicate is the trap being avoided",
          gmail_imap._quote(mailbox.filter_query()) == '""',
          gmail_imap._quote(mailbox.filter_query()))
finally:
    config.INGEST_ALL = _saved_ingest_all

check("INGEST_ALL restored to the module default after the block",
      config.INGEST_ALL == _saved_ingest_all)
check("INGEST_ALL off again: backfill_query is byte-identical once more",
      mailbox.backfill_query(12) == _reference_backfill_query(12))

print("pure functions: credential_kind")
check("credential_kind: imap", mailbox.credential_kind({"kind": "imap"}) == "imap")
check("credential_kind: legacy bare OAuth blob (no kind key)",
      mailbox.credential_kind({"token": "x", "refresh_token": "y"}) == "oauth")
try:
    mailbox.credential_kind({"kind": "bogus"})
    check("credential_kind: unknown kind raises", False, "did not raise")
except mailbox.MailboxError as e:
    check("credential_kind: unknown kind raises MailboxError", True, str(e))

print("pure functions: html_to_text matches gmail_sync.extract_body's HTML branch")
html_sample = "<style>.x{color:red}</style><p>Hello &amp; welcome</p><script>evil()</script>"
direct = mailbox.html_to_text(html_sample)
api_payload = {"mimeType": "text/html",
              "body": {"data": base64.urlsafe_b64encode(html_sample.encode()).decode()}}
via_api = gmail_sync.extract_body(api_payload)
check("mailbox.html_to_text() == gmail_sync.extract_body() on the same HTML",
      direct == via_api and direct == "Hello & welcome", (direct, via_api))

print("pure functions: X-GM-MSGID hex identity")
for dec in (1000000000001101, 1837402910584999, 1, 18446744073709551615):
    hexid = format(dec, "x")
    check(f"format({dec}, 'x') round-trips as int(hexid, 16)",
          int(hexid, 16) == dec and not hexid.startswith("0x"), hexid)


# --------------------------------------------------------------------------- fixtures

MSGID_1, MSGID_2, MSGID_3, MSGID_MAX = (
    1837402910583201, 1837402910583455, 1837402910583600, 1837402910584999)

server = FakeServer(uidvalidity=1000)
server.add(101, MSGID_1, "10-Jul-2026 09:00:00 +0000",
          _mk_msg("no-reply@greenhouse.io", "Thanks for applying",
                  "We received your application for Senior AI Engineer."))
server.add(205, MSGID_2, "15-Jul-2026 02:30:00 +0800",
          _mk_msg_rfc2047("hr@randomcorp.example", "Application Update café",
                          "There has been an update to your application."))
server.add(310, MSGID_3, "18-Jul-2026 11:00:00 +0000",
          _mk_msg("person@nowhere.example", "Weekly newsletter roundup",
                  "Nothing to do with a job search."),
          matches=True)  # server-side hit; must still fail the LOCAL candidate filter
server.add(999, MSGID_MAX, "20-Jul-2026 12:00:00 +0000",
          _mk_msg("careers@lever.co", "Interview scheduled",
                  "Let's schedule your interview."))

MY_HEX_IDS = [format(d, "x") for d in (MSGID_1, MSGID_2, MSGID_MAX)]  # MSGID_3 never stored


# --------------------------------------------------------------------------- B/C: backfill

print("backfill: connect, protocol invariants, hex identity, ordering, candidate filter")

with db.connect() as conn:
    user_id = db.single_user_id(conn)

    FakeIMAP.server = server
    provider1 = gmail_imap.ImapProvider(TEST_ADDRESS, GOOD_PASSWORD)
    check("provider selected the localised All Mail folder via the \\All flag",
          provider1.selected_mailbox == "[Gmail]/Tous les messages", provider1.selected_mailbox)

    select_cmd = next(c for c in provider1._conn.commands if c[0] == "SELECT")
    check("select() called with readonly=True", select_cmd[2] is True, select_cmd)
    check("select() called with a quoted mailbox name",
          select_cmd[1].startswith('"') and select_cmd[1].endswith('"'), select_cmd)

    stored = mailbox.backfill(conn, provider1, user_id, months=1)
    check("backfill stored exactly the 3 local candidates (1 server hit rejected locally)",
          stored == 3, stored)

    wire_query = _quoted_search_arg(provider1._conn.commands)
    check("search() sent the query quoted (X-GM-RAW arg is a valid IMAP literal)",
          wire_query is not None and wire_query.startswith('"') and wire_query.endswith('"'),
          wire_query)
    check('search() escaped the subject:"..." inner quotes for the wire',
          '\\"' in wire_query, wire_query)
    check("search() sent backfill_query(1) reused verbatim, only wrapped/escaped by _quote()",
          wire_query == gmail_imap._quote(mailbox.backfill_query(1)), wire_query)

    fetch_cmds = [c for c in provider1._conn.commands if c[:2] == ("UID", "FETCH")]
    check("every FETCH used BODY.PEEK[ (asserted live by the fake; this re-confirms count)",
          len(fetch_cmds) == 4, fetch_cmds)  # 3 stored + the 1 rejected-by-local-filter

    rows = conn.execute(
        "SELECT gmail_message_id, subject, received_at FROM emails "
        "WHERE user_id = %s AND gmail_message_id = ANY(%s) ORDER BY received_at",
        (user_id, MY_HEX_IDS)).fetchall()
    check("all 3 candidates stored with hex ids identical to the API path's would-be ids",
          {r["gmail_message_id"] for r in rows} == set(MY_HEX_IDS),
          [r["gmail_message_id"] for r in rows])

    rejected = conn.execute(
        "SELECT 1 FROM emails WHERE user_id = %s AND gmail_message_id = %s",
        (user_id, format(MSGID_3, "x"))).fetchone()
    check("the server-side search hit that fails the LOCAL candidate filter was not stored",
          rejected is None, rejected)

    u2_row = next(r for r in rows if r["gmail_message_id"] == format(MSGID_2, "x"))
    check("INTERNALDATE +0800 stored as the correct UTC instant, not host-shifted",
          u2_row["received_at"].hour == 18 and u2_row["received_at"].day == 14,
          u2_row["received_at"])
    check("RFC 2047 subject decoded to readable text, not left as =?utf-8?q?...?= wire syntax "
          "(this message's sender is NOT allowlisted, so it can only have been stored by "
          "matching the SUBJECT_KEYWORDS 'application update' — which the raw undecoded "
          "wire form (Q-encoding replaces spaces with '_') cannot contain as a substring)",
          u2_row["subject"] == "Application Update café", u2_row["subject"])

    job_rows = conn.execute(
        """SELECT e.gmail_message_id FROM job_queue q
           JOIN emails e ON e.id = (q.payload->>'email_id')::uuid
           WHERE q.type = 'classify_email' AND e.user_id = %s
             AND e.gmail_message_id = ANY(%s)
           ORDER BY q.id""",
        (user_id, MY_HEX_IDS)).fetchall()
    check("classify_email jobs enqueued in oldest-first (received_at) order",
          [r["gmail_message_id"] for r in job_rows] ==
          [format(MSGID_1, "x"), format(MSGID_2, "x"), format(MSGID_MAX, "x")],
          job_rows)

    cursor_row = conn.execute(
        "SELECT history_id FROM gmail_sync_state WHERE user_id = %s", (user_id,)).fetchone()
    check("cursor is uidvalidity:uidnext-1, captured at EXAMINE time",
          cursor_row["history_id"] == "1000:999", cursor_row)

    print("backfill: idempotent re-sync")
    FakeIMAP.server = server
    provider2 = gmail_imap.ImapProvider(TEST_ADDRESS, GOOD_PASSWORD)
    stored_again = mailbox.backfill(conn, provider2, user_id, months=1)
    check("re-running backfill against the same mailbox stores zero new rows",
          stored_again == 0, stored_again)
    job_count_after = conn.execute(
        """SELECT count(*) AS n FROM job_queue q
           JOIN emails e ON e.id = (q.payload->>'email_id')::uuid
           WHERE q.type = 'classify_email' AND e.user_id = %s
             AND e.gmail_message_id = ANY(%s)""",
        (user_id, MY_HEX_IDS)).fetchone()["n"]
    check("re-running backfill enqueues no duplicate classify_email jobs",
          job_count_after == 3, job_count_after)
    provider2.close()

    # ----------------------------------------------------------------- incremental

    print("incremental: UID n:* returns the highest UID even past it — client filters it out")
    FakeIMAP.server = server
    provider3 = gmail_imap.ImapProvider(TEST_ADDRESS, GOOD_PASSWORD)
    n3 = mailbox.incremental(conn, provider3, user_id)
    check("no new mail since backfill -> zero stored", n3 == 0, n3)
    stale_fetch = [c for c in provider3._conn.commands if c[:2] == ("UID", "FETCH")]
    check("the stale UID 999 (== last_uid, not > last_uid) was never fetched",
          stale_fetch == [], stale_fetch)
    search_cmd3 = next(c for c in provider3._conn.commands if c[:2] == ("UID", "SEARCH"))
    check("incremental search used a UID range (cursor validity matched)",
          search_cmd3[2] == "UID", search_cmd3)
    provider3.close()

    print("incremental: UIDVALIDITY change forces a full re-search, still idempotent")
    server_v2 = FakeServer(uidvalidity=2000)
    server_v2.messages = server.messages  # same underlying mail, "server" was rebuilt
    server_v2.list_response = server.list_response
    FakeIMAP.server = server_v2
    provider4 = gmail_imap.ImapProvider(TEST_ADDRESS, GOOD_PASSWORD)
    n4 = mailbox.incremental(conn, provider4, user_id)
    check("UIDVALIDITY-triggered full re-search stores zero new rows "
          "(hex-identity dedup holds even across a full re-search)", n4 == 0, n4)
    search_cmd4 = next(c for c in provider4._conn.commands if c[:2] == ("UID", "SEARCH"))
    check("the re-search after a UIDVALIDITY change is NOT a UID-range search",
          search_cmd4[2] != "UID", search_cmd4)
    cursor_row4 = conn.execute(
        "SELECT history_id FROM gmail_sync_state WHERE user_id = %s", (user_id,)).fetchone()
    check("cursor now carries the new UIDVALIDITY",
          cursor_row4["history_id"].startswith("2000:"), cursor_row4)
    provider4.close()

    print("incremental: a bare-integer legacy cursor (leftover OAuth historyId) is treated as absent")
    conn.execute("UPDATE gmail_sync_state SET history_id = '987654321' WHERE user_id = %s",
                (user_id,))
    conn.commit()
    FakeIMAP.server = server
    provider5 = gmail_imap.ImapProvider(TEST_ADDRESS, GOOD_PASSWORD)
    try:
        n5 = mailbox.incremental(conn, provider5, user_id)
        ok5, detail5 = True, n5
    except Exception as e:
        ok5, detail5 = False, f"{type(e).__name__}: {e}"
    check("bare-integer cursor does not crash int()/split(':') parsing", ok5, detail5)
    search_cmd5 = next(c for c in provider5._conn.commands if c[:2] == ("UID", "SEARCH"))
    check("bare-integer cursor triggers a full (non-UID-range) re-search",
          search_cmd5[2] != "UID", search_cmd5)
    provider5.close()

    # ----------------------------------------------------------------- auth failure

    print("auth failure: wrong app password")
    FakeIMAP.server = server
    try:
        gmail_imap.ImapProvider(TEST_ADDRESS, "wrong-password-12")
        ok6, detail6 = False, "did not raise"
    except mailbox.MailboxAuthError as e:
        ok6, detail6 = True, str(e)
    except Exception as e:
        ok6, detail6 = False, f"wrong exception type: {type(e).__name__}: {e}"
    check("bad app password raises MailboxAuthError, not raw imaplib.IMAP4.error", ok6, detail6)

    bad_msg = gmail_imap.verify(TEST_ADDRESS, "wrong-password-12")
    check("verify() returns a human message (not None) on bad password, mentioning app password",
          bad_msg is not None and "app password" in bad_msg.lower(), bad_msg)
    good_msg = gmail_imap.verify(TEST_ADDRESS, GOOD_PASSWORD)
    check("verify() returns None on a good password", good_msg is None, good_msg)

    # ----------------------------------------------------------------- credential dispatch

    print("credential dispatch: provider_for_user + describe_credential")
    stored_payload = json.dumps({
        "kind": "imap", "provider": "gmail", "address": TEST_ADDRESS,
        "app_password": GOOD_PASSWORD, "connected_at": "2026-07-27T00:00:00+00:00"})
    encrypted = auth.encrypt(stored_payload)

    kind, address = mailbox.describe_credential(encrypted)
    check("describe_credential decrypts and identifies an IMAP credential",
          kind == "imap" and address == TEST_ADDRESS, (kind, address))
    kind_none, addr_none = mailbox.describe_credential(None)
    check("describe_credential(None) is (None, None)", (kind_none, addr_none) == (None, None),
          (kind_none, addr_none))
    kind_bad, addr_bad = mailbox.describe_credential("not-a-valid-fernet-token")
    check("describe_credential on undecryptable ciphertext is 'unreadable', not a crash",
          kind_bad == "unreadable" and addr_bad is None, (kind_bad, addr_bad))

    FakeIMAP.server = server
    fake_user_row = {"id": user_id, "email": "fixture@example.com",
                     "gmail_credentials": encrypted}
    dispatched = mailbox.provider_for_user(conn, fake_user_row)
    check("provider_for_user dispatches an IMAP credential to ImapProvider",
          isinstance(dispatched, gmail_imap.ImapProvider) and dispatched.kind == "imap",
          type(dispatched))
    dispatched.close()

    # ----------------------------------------------------------------- disconnect (unit level)

    print("disconnect: kind-agnostic, clears the cursor too")
    conn.execute("UPDATE users SET gmail_credentials = %s WHERE id = %s", (encrypted, user_id))
    conn.commit()
    mailbox.disconnect(conn, user_id)
    conn.commit()
    after = conn.execute(
        "SELECT gmail_credentials, oauth_state FROM users WHERE id = %s", (user_id,)).fetchone()
    check("disconnect clears gmail_credentials", after["gmail_credentials"] is None, after)
    check("disconnect clears oauth_state", after["oauth_state"] is None, after)
    sync_row = conn.execute(
        "SELECT 1 FROM gmail_sync_state WHERE user_id = %s", (user_id,)).fetchone()
    check("disconnect deletes the gmail_sync_state cursor row "
          "(a stale UIDVALIDITY:uid left behind would make the next connect skip real mail)",
          sync_row is None, sync_row)

    # ----------------------------------------------------------------- web: settings form

    print("web: /settings/gmail/imap connect form")
    from fastapi.testclient import TestClient
    from pipeline.web import app as _web_app

    web_client = TestClient(_web_app, follow_redirects=False)

    def _bootstrap_session():
        with db.connect() as sconn, sconn.transaction():
            u = sconn.execute(
                "SELECT id, password_hash FROM users ORDER BY created_at LIMIT 1").fetchone()
            if not u["password_hash"]:
                sconn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                             (auth.hash_password("testpass123"), u["id"]))
            sid = auth.create_session(sconn, u["id"])
        web_client.cookies.set("session", sid)

    _bootstrap_session()

    r_blank = web_client.post("/settings/gmail/imap",
                              data={"gmail_address": "", "app_password": ""})
    check("blank address/password -> 400 with a friendly message",
          r_blank.status_code == 400 and "app password" in r_blank.text.lower(),
          r_blank.status_code)
    row_blank = conn.execute(
        "SELECT gmail_credentials FROM users WHERE id = %s", (user_id,)).fetchone()
    check("blank submission wrote nothing", row_blank["gmail_credentials"] is None, row_blank)

    FakeIMAP.server = server
    r_bad = web_client.post("/settings/gmail/imap",
                            data={"gmail_address": TEST_ADDRESS, "app_password": "wrong-one-12"})
    check("bad password -> 400", r_bad.status_code == 400, r_bad.status_code)
    check("bad password is never echoed back into the response",
          "wrong-one-12" not in r_bad.text, "password leaked into response body")
    row_bad = conn.execute(
        "SELECT gmail_credentials FROM users WHERE id = %s", (user_id,)).fetchone()
    check("bad password wrote nothing", row_bad["gmail_credentials"] is None, row_bad)

    FakeIMAP.server = server
    r_good = web_client.post("/settings/gmail/imap",
                             data={"gmail_address": TEST_ADDRESS, "app_password": GOOD_PASSWORD})
    check("good password -> 200, settings page re-rendered", r_good.status_code == 200,
          r_good.status_code)
    check("good password never appears in the response body",
          GOOD_PASSWORD not in r_good.text, "password leaked into response body")
    check("settings page shows the IMAP-connected state",
          "Connected over IMAP" in r_good.text and TEST_ADDRESS in r_good.text, r_good.text[:2000])
    row_good = conn.execute(
        "SELECT gmail_credentials FROM users WHERE id = %s", (user_id,)).fetchone()
    stored_kind, stored_addr = mailbox.describe_credential(row_good["gmail_credentials"])
    check("stored credential decrypts to kind=imap with the right address",
          stored_kind == "imap" and stored_addr == TEST_ADDRESS, (stored_kind, stored_addr))

    print("web: unreadable credential state renders without a traceback")
    conn.execute("UPDATE users SET gmail_credentials = 'not-a-valid-fernet-token' WHERE id = %s",
                (user_id,))
    conn.commit()
    r_unreadable = web_client.get("/settings")
    check("settings page renders 200 even when the stored credential can't be decrypted",
          r_unreadable.status_code == 200 and "reconnect" in r_unreadable.text.lower(),
          r_unreadable.status_code)

    print("web: disconnect route")
    conn.execute("UPDATE users SET gmail_credentials = %s WHERE id = %s", (encrypted, user_id))
    conn.commit()
    r_disc = web_client.post("/settings/gmail/disconnect")
    check("disconnect route redirects", r_disc.status_code == 303, r_disc.status_code)
    row_disc = conn.execute(
        "SELECT gmail_credentials FROM users WHERE id = %s", (user_id,)).fetchone()
    check("disconnect route cleared the credential", row_disc["gmail_credentials"] is None, row_disc)
    sync_row2 = conn.execute(
        "SELECT 1 FROM gmail_sync_state WHERE user_id = %s", (user_id,)).fetchone()
    check("disconnect route also deleted the sync cursor", sync_row2 is None, sync_row2)

    # ----------------------------------------------------------------- cross-suite hygiene
    #
    # This suite is the first to leave live classify_email jobs in job_queue
    # (test_integration drains its own with stubs; test_web/test_phase3 seed
    # emails without enqueueing). test_phase4 runs `while worker.process_one`
    # to drain the queue for its own cover-letter check — undrained, it would
    # claim these jobs and attempt a real Anthropic call with the dummy test
    # key. Mark only OUR rows done, matched by email id, so nothing from any
    # other suite is touched.
    my_email_ids = [str(r["id"]) for r in conn.execute(
        "SELECT id FROM emails WHERE user_id = %s AND gmail_message_id = ANY(%s)",
        (user_id, MY_HEX_IDS)).fetchall()]
    conn.execute(
        """UPDATE job_queue SET state = 'done'
           WHERE type = 'classify_email' AND state = 'pending'
             AND payload->>'email_id' = ANY(%s)""",
        (my_email_ids,))
    conn.commit()

print("\nALL EMAIL-INGEST CHECKS PASS")
