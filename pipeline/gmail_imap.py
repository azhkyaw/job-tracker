"""Gmail IMAP provider — app-password ingest (docs/email-ingest.md), the
default email path. No Google Cloud project, no OAuth consent screen, no
7-day refresh-token expiry (docs/open-source.md §2). The shared
candidate-filter / storage / query-building orchestration lives in
pipeline/mailbox.py — this module only connects, searches, and returns the
normalised message dict; never reimplement mailbox.py's logic here.

Two verified stdlib traps this module works around (see docs/email-ingest.md
§0 and CLAUDE.md's gotchas):

  1. IMAP4._command() does no quoting — every argument is concatenated onto
     the wire verbatim. A mailbox name or an X-GM-RAW query containing
     spaces or quotes must be escaped by the caller (_quote below), or the
     server returns BAD/a truncated command.
  2. imaplib.Internaldate2tuple() returns time.localtime(utc) — a NAIVE
     struct in the *host's* timezone. Using it for received_at silently
     shifts every stored email by the host's UTC offset. This module parses
     imaplib.InternalDate's regex groups directly instead and builds a
     tz-aware UTC datetime.
"""

from __future__ import annotations

import email
import imaplib
import re
import socket
import ssl
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header

from . import mailbox
from .mailbox import MailboxAuthError, MailboxError

HOST = "imap.gmail.com"
PORT = 993
TIMEOUT = 30

# Module-level seam: tests reassign this attribute (the same convention as
# email_classifier.classify_email = lambda ...), so the provider's own code
# never needs to know it is talking to a fake.
IMAP4_SSL = imaplib.IMAP4_SSL

_ALL_MAIL_FALLBACKS = ("[Gmail]/All Mail", "INBOX")

_MSGID_RE = re.compile(rb"X-GM-MSGID\s+(\d+)")
_ALL_FLAG_RE = re.compile(rb"\\All\b")
_MAILBOX_NAME_RE = re.compile(rb'"([^"]*)"\s*$')


# --------------------------------------------------------------------------- quoting

def _quote(s: str) -> str:
    """IMAP quoted-string literal. imaplib does zero quoting of its own
    (see module docstring point 1) — every mailbox name and every X-GM-RAW
    query passed to this provider must go through here."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --------------------------------------------------------------------------- error mapping

def _is_auth_failure(err: "imaplib.IMAP4.error") -> bool:
    msg = str(err).lower()
    return any(s in msg for s in
               ("authenticationfailed", "invalid credentials", "web login required",
                "application-specific password required"))


def _auth_error(address: str, app_password: str, err: Exception) -> MailboxAuthError:
    hint = ""
    if len(app_password) != 16:
        hint = (" (app passwords are 16 characters — this looks like your "
                "Google account password, not an app password)")
    return MailboxAuthError(
        f"Gmail rejected that app password for {address}{hint}. Generate a new "
        f"one at myaccount.google.com/apppasswords, or use OAuth instead if "
        f"this is a Workspace / Advanced Protection account. ({err})")


# --------------------------------------------------------------------------- mailbox discovery

def _find_all_mail(conn) -> str:
    """The All Mail folder, found by its \\All SPECIAL-USE flag rather than
    a hard-coded name — the literal "[Gmail]/All Mail" is localised per
    account language. All Mail (not INBOX) is the parity choice: the Gmail
    API's messages.list searches all mail excluding Spam/Trash, and INBOX
    alone would silently miss archived and filter-routed job mail, which is
    most accounts with job alerts."""
    try:
        typ, data = conn.list()
    except imaplib.IMAP4.error:
        return _ALL_MAIL_FALLBACKS[0]
    if typ == "OK":
        for line in data:
            if line and _ALL_FLAG_RE.search(line):
                m = _MAILBOX_NAME_RE.search(line)
                if m:
                    return m.group(1).decode("utf-8", errors="replace")
    return _ALL_MAIL_FALLBACKS[0]


def _response_int(conn, name: str) -> int | None:
    values = conn.untagged_responses.get(name)
    if not values:
        return None
    return int(values[-1])


# --------------------------------------------------------------------------- INTERNALDATE parsing

_MON2NUM = imaplib.Mon2num  # {b"Jan": 1, ..., b"Dec": 12}


def _parse_internaldate(metadata: bytes) -> datetime | None:
    """Never imaplib.Internaldate2tuple() — see module docstring point 2."""
    m = imaplib.InternalDate.match(metadata)
    if m is None:
        return None
    offset_minutes = int(m.group("zoneh")) * 60 + int(m.group("zonem"))
    if m.group("zonen") == b"-":
        offset_minutes = -offset_minutes
    tz = timezone(timedelta(minutes=offset_minutes))
    dt = datetime(int(m.group("year")), _MON2NUM[m.group("mon")], int(m.group("day")),
                  int(m.group("hour")), int(m.group("min")), int(m.group("sec")), tzinfo=tz)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- message normalisation

def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (UnicodeDecodeError, LookupError):
        return raw


def _extract_body(msg: "email.message.Message") -> str:
    plain, html = None, None
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        charset = part.get_content_charset() or "utf-8"
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(charset, errors="replace")
        if part.get_content_type() == "text/plain" and plain is None:
            plain = text
        elif part.get_content_type() == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain.strip()
    if html is not None:
        return mailbox.html_to_text(html)
    return ""


def _normalise(metadata: bytes, raw: bytes) -> dict:
    m = _MSGID_RE.search(metadata)
    if m is None:
        raise MailboxError(f"FETCH response missing X-GM-MSGID: {metadata!r}")
    gmail_message_id = format(int(m.group(1)), "x")

    msg = email.message_from_bytes(raw)
    received_at = (_parse_internaldate(metadata)
                   or _fallback_date(msg)
                   or datetime.now(timezone.utc))
    return {
        "id": gmail_message_id,
        "sender": _decode_header_value(msg.get("From", "")),
        "subject": _decode_header_value(msg.get("Subject", "")),
        "body_text": _extract_body(msg),
        "received_at": received_at,
    }


def _fallback_date(msg: "email.message.Message") -> datetime | None:
    from email.utils import parsedate_to_datetime
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- verify (CLI / Settings)

def verify(address: str, app_password: str) -> str | None:
    """Connect, log in, EXAMINE the mailbox, log out. Returns None on
    success or a human-readable message on failure — never raises. Used to
    validate a credential before it is ever stored."""
    try:
        provider = ImapProvider(address, app_password)
    except MailboxError as err:
        return str(err)
    provider.close()
    return None


# --------------------------------------------------------------------------- provider

class ImapProvider:
    """Gmail IMAP + app password. Connects eagerly in __init__ (matching
    GmailApiProvider.from_stored's eager refresh) so a bad credential fails
    fast, before a backfill or sync run is half done."""

    kind = "imap"

    def __init__(self, address: str, app_password: str):
        self.address = address
        self._app_password = app_password
        self._conn = None
        self.uidvalidity: int | None = None
        self.uidnext: int | None = None
        self.selected_mailbox: str | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            conn = IMAP4_SSL(HOST, PORT, timeout=TIMEOUT)
        except (OSError, ssl.SSLError, socket.timeout) as err:
            raise MailboxError(f"could not reach {HOST}: {err}") from err
        try:
            conn.login(self.address, self._app_password)
        except imaplib.IMAP4.error as err:
            try:
                conn.logout()
            except Exception:
                pass
            if _is_auth_failure(err):
                raise _auth_error(self.address, self._app_password, err) from err
            raise MailboxError(f"Gmail IMAP login failed for {self.address}: {err}") from err

        mailbox_name = _find_all_mail(conn)
        typ, data = conn.select(_quote(mailbox_name), readonly=True)
        if typ != "OK":
            try:
                conn.logout()
            except Exception:
                pass
            raise MailboxError(f"could not open mailbox {mailbox_name!r}: {data}")

        self._conn = conn
        self.selected_mailbox = mailbox_name
        self.uidvalidity = _response_int(conn, "UIDVALIDITY")
        self.uidnext = _response_int(conn, "UIDNEXT")

    def _reconnect(self) -> None:
        """One retry on a dropped connection (a -m 12 backfill can outlive
        Gmail's idle timeout). Connect-work-disconnect otherwise; never
        IDLE — Gmail allows ~15 simultaneous connections per account and
        the operator's own mail clients compete for them."""
        prev_validity = self.uidvalidity
        try:
            self._conn.logout()
        except Exception:
            pass
        self._connect()
        if prev_validity is not None and self.uidvalidity != prev_validity:
            raise MailboxError(
                "Gmail mailbox UIDVALIDITY changed mid-run — the UIDs already "
                "fetched in this run are no longer meaningful; re-run sync/backfill")

    def _uid_command(self, *args: str):
        try:
            typ, data = self._conn.uid(*args)
        except (imaplib.IMAP4.abort, OSError):
            self._reconnect()
            try:
                typ, data = self._conn.uid(*args)
            except (imaplib.IMAP4.abort, OSError) as err2:
                raise MailboxError(
                    f"Gmail IMAP connection failed twice: {err2}") from err2
        if typ != "OK":
            raise MailboxError(f"IMAP command failed: UID {' '.join(args)}: {data}")
        return data

    def _search_uids(self, *criteria: str) -> list[int]:
        data = self._uid_command("SEARCH", *criteria)
        raw = data[0] if data else b""
        if not raw:
            return []
        return [int(x) for x in raw.split()]

    # ---------------------------------------------------------- provider protocol

    def search(self, query: str) -> list[int]:
        uids = self._search_uids("X-GM-RAW", _quote(query))
        # Gmail returns UID SEARCH results ascending. Assert rather than
        # silently sort/reverse — a server that ever violated this would
        # otherwise quietly revive the newest-first ordering bug documented
        # in CLAUDE.md (gmail_sync._list_message_ids's long comment).
        if uids != sorted(uids):
            raise MailboxError("Gmail IMAP returned UID SEARCH results out of "
                                "ascending order — ingest order guarantees violated")
        return uids

    def fetch(self, uid: int) -> dict | None:
        data = self._uid_command("FETCH", str(uid), "(BODY.PEEK[] INTERNALDATE X-GM-MSGID)")
        item = next((d for d in data if isinstance(d, tuple) and len(d) == 2), None)
        if item is None:  # message vanished between search and fetch
            return None
        metadata, raw = item
        return _normalise(metadata, raw)

    def incremental_handles(self, state: dict | None) -> list[int]:
        cursor = (state or {}).get("history_id") or ""
        if ":" in cursor:
            validity_s, last_uid_s = cursor.split(":", 1)
            if validity_s == str(self.uidvalidity):
                last_uid = int(last_uid_s)
                # UID SEARCH UID n:* returns the highest existing UID even
                # when n exceeds it (RFC 3501 range semantics) — without
                # this filter every run would re-fetch the newest matching
                # message forever: invisible (the insert is a no-op) except
                # as a permanent bandwidth leak.
                # Under INGEST_ALL there is no predicate: send the UID range
                # alone. Passing the empty string as an X-GM-RAW argument would
                # go on the wire as a valid-but-empty quoted string and match
                # nothing, silently stalling every incremental sync.
                criteria = ["UID", f"{last_uid + 1}:*"]
                predicate = mailbox.filter_query()
                if predicate:
                    criteria += ["X-GM-RAW", _quote(predicate)]
                uids = self._search_uids(*criteria)
                return [u for u in uids if u > last_uid]
            # UIDVALIDITY changed — every previously-recorded UID is
            # meaningless. Fall through to a full filtered re-search.
        since = _fallback_since(state)
        return self.search(mailbox.query_since(since))

    def cursor(self) -> str:
        # uidnext was captured at EXAMINE, before the search — the exact
        # "everything up to here has been considered" watermark. Using
        # max(matched UID) instead would leave every non-matching message
        # in the incremental window forever; capturing uidnext after the
        # search would risk skipping a message that arrived mid-run.
        watermark = (self.uidnext - 1) if self.uidnext else 0
        return f"{self.uidvalidity}:{watermark}"

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None


def _fallback_since(state: dict | None) -> date:
    last_synced_at = (state or {}).get("last_synced_at")
    since = last_synced_at or datetime.now(timezone.utc) - timedelta(days=7)
    return since.date() if hasattr(since, "date") else since
