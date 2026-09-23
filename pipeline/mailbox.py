"""Provider-agnostic email ingest orchestrator (docs/email-ingest.md).

Owns everything a mail provider should never reimplement: candidate
filtering, body storage, enqueueing, query building, and cursor
persistence. A provider (gmail_sync.GmailApiProvider, gmail_imap.ImapProvider)
only connects, searches, and returns the normalised message dict below.
Never copy is_candidate / store_message / backfill_query into a provider —
that is exactly how two ingest paths silently diverge (CLAUDE.md invariant).

Provider protocol (duck-typed, no ABC):
    kind: str                                    "gmail_api" | "imap"
    search(query: str) -> list[handle]            oldest first
    fetch(handle) -> dict | None                  normalised message, or None
                                                   if the message vanished
    incremental_handles(state: dict | None) -> list[handle]
                                                   provider-specific cursor
                                                   logic; returns handles only,
                                                   never stores
    cursor() -> str                               value to persist after a
                                                   successful run
    close() -> None

Handles are provider-opaque (API = message-id string, IMAP = int UID).
Normalised message: {"id", "sender", "subject", "body_text", "received_at"}
— "id" is gmail_message_id as lowercase hex, identical on both paths;
"received_at" is a tz-aware UTC datetime.

This module imports neither `google*` nor `imaplib`, so it — and anything
that only needs credential dispatch (web.py, cli.py) — stays importable on
an install with no Google libraries.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser

from . import config, db


class MailboxError(RuntimeError):
    """Transient or configuration failure: network, mailbox, server."""


class MailboxAuthError(MailboxError):
    """The stored credential is bad — the user must reconnect."""


# --------------------------------------------------------------------------- credentials

def credential_kind(payload: dict) -> str:
    """"imap" or "oauth". Absence of "kind" means the legacy bare OAuth blob
    (Credentials.to_json() has no top-level "kind" key)."""
    k = payload.get("kind")
    if k is None:
        return "oauth"
    if k == "imap":
        return "imap"
    raise MailboxError(f"unrecognised stored credential kind {k!r} — "
                        f"reconnect Gmail in Settings")


def describe_credential(raw: str | None) -> tuple[str | None, str | None]:
    """(kind, address) for display. ("unreadable", None) if raw exists but
    can't be decrypted; (None, None) if there is nothing stored."""
    from . import auth  # local import: avoid cycle at module load
    if not raw:
        return None, None
    decrypted = auth.decrypt(raw)
    if decrypted is None:
        return "unreadable", None
    payload = json.loads(decrypted)
    kind = credential_kind(payload)
    address = payload.get("address") if kind == "imap" else None
    return kind, address


def provider_for_user(conn, user):
    """The single dispatch point for credential kind — the IMAP analogue of
    invariant #3's "ONLY place" discipline. Returns None when the user has
    no Gmail connected. Local imports keep google* and imaplib optional for
    callers that only need one kind."""
    from . import auth  # local import: avoid cycle at module load
    raw = user.get("gmail_credentials") if isinstance(user, dict) else user["gmail_credentials"]
    if raw:
        decrypted = auth.decrypt(raw)
        if decrypted is None:
            raise MailboxAuthError(
                "stored Gmail credentials cannot be decrypted — "
                "TRACKER_SECRET_KEY changed? Reconnect Gmail in Settings")
        payload = json.loads(decrypted)
        kind = credential_kind(payload)
        if kind == "imap":
            from . import gmail_imap
            return gmail_imap.ImapProvider(payload["address"], payload["app_password"])
        from . import gmail_sync
        return gmail_sync.GmailApiProvider.from_stored(conn, user, payload)
    if config.GMAIL_TOKEN_FILE.exists():
        from . import gmail_sync
        return gmail_sync.GmailApiProvider(gmail_sync.get_service())
    return None


def disconnect(conn, user_id) -> None:
    """Kind-agnostic: clears whichever credential is stored, and always
    drops the sync cursor. A stale cursor left behind for a different
    mailbox/account would make the next connect skip real mail."""
    conn.execute(
        "UPDATE users SET gmail_credentials = NULL, oauth_state = NULL WHERE id = %s",
        (user_id,))
    conn.execute("DELETE FROM gmail_sync_state WHERE user_id = %s", (user_id,))


# --------------------------------------------------------------------------- filtering

def _sender_domain(from_header: str) -> str:
    addr = parseaddr(from_header or "")[1]
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def is_candidate(from_header: str, subject: str) -> bool:
    if config.INGEST_ALL:
        return True
    domain = _sender_domain(from_header)
    if any(domain == d or domain.endswith("." + d) for d in config.ALLOWLIST_DOMAINS):
        return True
    subject_l = (subject or "").lower()
    return any(kw in subject_l for kw in config.SUBJECT_KEYWORDS)


# --------------------------------------------------------------------------- body extraction
#
# Which MIME part IS the body is decided here, once, for both providers. The
# part WALK is provider-specific by nature (an API payload dict against
# email.message.Message.walk()), but the CHOICE between what the walk found
# must not be — two copies of that rule is exactly the divergence invariant
# #10 exists to prevent.

def body_from_parts(plain: str | None, html: str | None) -> str:
    """The text to store as emails.body_text, given a message's first
    text/plain and first text/html parts (either may be absent).

    The HTML alternative wins whenever it renders to anything. RFC 2046
    §5.1.4 orders multipart/alternative parts from plainest to richest and
    tells a reader to show the LAST one it can — which is why every mail
    client renders the HTML, and why senders let the text/plain part rot
    unnoticed. Measured on the author's real mail, 23 Sep 2026: three
    senders ship a plain part that is not what a human sees. LinkedIn's
    "Your application was viewed by X" is footer-only (40 of 40 stored; the
    role title, "Applied on <date>" and the poster's name exist ONLY in the
    HTML, so the extractor never saw a title and the matcher had company
    alone to go on). Workable's plain part starts at its own divider. An
    employer's referral mail ships its plain part as raw HTML with the
    template variables unfilled ("[[JOB_REQ_TITLE]]") while the HTML part
    is rendered. In the same 40-message sample no sender had the reverse
    problem, and the HTML text was shorter for the classifier every time
    (link targets stay in the plain part as bare URLs; HTML keeps only the
    link text). Preferring text/plain was a convenience that assumed the two
    alternatives carry the same content. They don't.
    """
    if html:
        text = html_to_text(html)
        if text:
            return text
    return (plain or "").strip()


# Rendered to nothing: a mail client shows none of these.
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "template"})
# Rendered as a line break, so their text never runs into a neighbour's.
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "hr", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "ul", "ol", "blockquote", "pre", "section", "article", "header",
    "footer", "address", "dl", "dt", "dd", "form", "fieldset", "center", "body",
})
# Occupies no space on screen but survives tag stripping: the combining
# grapheme joiner LinkedIn pads its preheader with (U+034F, hundreds per
# mail), zero-width spaces/joiners and marks, the word joiner and the
# invisible operators, the BOM some templates repeat as filler, and the soft
# hyphen. Each one was seen in a real message's rendered text.
_INVISIBLE_RE = re.compile(
    "[͏​‌‍‎‏⁠⁡⁢⁣⁤﻿­]")
_INLINE_WS_RE = re.compile(r"[ \t\r\f\v ]+")
_BLANK_RUN_RE = re.compile(r"\n{3,}")

# Fallback only: the pre-23-Sep-2026 tag-strip, kept for a document the
# parser cannot get through (see html_to_text). It leaks <title> text,
# comment bodies and Outlook's conditional-comment residue.
_TAG_RE = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")


class _TextExtractor(HTMLParser):
    """Collects what a mail client would put on screen, with the document's
    block structure kept as line breaks. Comments (including Outlook's
    `<!--[if mso]> ... <![endif]-->` blocks, whose `<o:PixelsPerInch>96`
    leaked into stored bodies as a bare "96") and declarations are dropped by
    HTMLParser's own defaults; entities arrive already decoded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skipping: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "body":
            # A <head> that is never closed would otherwise swallow the mail.
            self._skipping.clear()
        if tag in _SKIP_TAGS:
            self._skipping.append(tag)
            return
        self.out.append("\n" if tag in _BLOCK_TAGS else " ")

    def handle_endtag(self, tag) -> None:
        if tag in _SKIP_TAGS:
            while self._skipping:
                if self._skipping.pop() == tag:
                    break
            return
        self.out.append("\n" if tag in _BLOCK_TAGS else " ")

    def handle_startendtag(self, tag, attrs) -> None:
        # A void element (<br/>, <hr/>, <img/>) breaks once, not twice — the
        # default would run both handlers and give <br/> a blank line that
        # <br> does not get.
        self.handle_starttag(tag, attrs)

    def handle_data(self, data) -> None:
        if not self._skipping:
            self.out.append(data)


def html_to_text(html: str) -> str:
    """What a mail client shows, as text: one line per block, cells of a
    row on one line, no hidden chrome. Whitespace is normalised per line and
    runs of blank lines collapse to one, so a nested-table newsletter reads
    as paragraphs rather than as twenty empty lines between them."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
        text = "".join(parser.out)
    except Exception:  # a document the parser cannot get through — rare, never seen
        text = unescape(_HTML_RE.sub(" ", _TAG_RE.sub(" ", html)))
    text = _INVISIBLE_RE.sub("", text)
    lines = (_INLINE_WS_RE.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(lines)).strip()


# --------------------------------------------------------------------------- query builders

def filter_query() -> str:
    """The allowlist/subject-keyword predicate, without any date bound —
    shared by backfill (which adds an after: window) and incremental
    (which ANDs it with a UID range instead).

    Empty string under config.INGEST_ALL: there is no predicate to send, and
    every caller must drop the criterion rather than pass "" to the server.
    Callers: query_since below, and gmail_imap.incremental_handles."""
    if config.INGEST_ALL:
        return ""
    froms = " OR ".join(f"from:{d}" for d in sorted(config.ALLOWLIST_DOMAINS))
    subjects = " OR ".join(f'subject:"{kw}"' for kw in config.SUBJECT_KEYWORDS)
    return f"(({froms}) OR ({subjects}))"


def query_since(day) -> str:
    after = f"after:{day.strftime('%Y/%m/%d')}"
    predicate = filter_query()
    # Concatenation stays byte-identical to the pre-INGEST_ALL formula when a
    # predicate exists — backfill_query's wire format is pinned by a test.
    return f"{after} {predicate}" if predicate else after


def backfill_query(months: int) -> str:
    """Byte-identical to the pre-refactor gmail_sync._backfill_query — pinned
    by a test, since X-GM-RAW reuses this string verbatim on the wire."""
    since = (datetime.now(timezone.utc) - timedelta(days=months * 31)).date()
    return query_since(since)


# --------------------------------------------------------------------------- storing

def store_message(conn, user_id, msg: dict) -> bool:
    """Insert one normalised message if it's a candidate. Idempotent:
    re-syncing the same gmail_message_id is a no-op. Returns True when
    newly stored. `msg` is the provider-normalised dict, not a raw payload —
    both providers funnel through here so they cannot silently diverge."""
    if not is_candidate(msg["sender"], msg["subject"]):
        return False
    row = conn.execute(
        """
        INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text, received_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, gmail_message_id) DO NOTHING
        RETURNING id
        """,
        (user_id, msg["id"], msg["sender"], msg["subject"], msg["body_text"], msg["received_at"]),
    ).fetchone()
    if row is None:
        return False
    db.enqueue(conn, user_id, "classify_email", {"email_id": str(row["id"])})
    return True


def _fetch_and_store(conn, provider, user_id, handles) -> int:
    stored = 0
    for handle in handles:
        msg = provider.fetch(handle)
        if msg is None:  # vanished between search and fetch
            continue
        with conn.transaction():
            if store_message(conn, user_id, msg):
                stored += 1
    return stored


# --------------------------------------------------------------------------- backfill / incremental

def backfill(conn, provider, user_id, months: int = config.BACKFILL_MONTHS_DEFAULT) -> int:
    """First-run reconstruction of application history from the inbox."""
    handles = provider.search(backfill_query(months))
    total = _fetch_and_store(conn, provider, user_id, handles)
    _save_cursor(conn, provider, user_id)
    return total


def incremental(conn, provider, user_id) -> int:
    state = conn.execute(
        "SELECT history_id, last_synced_at FROM gmail_sync_state WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    handles = provider.incremental_handles(state)
    total = _fetch_and_store(conn, provider, user_id, handles)
    _save_cursor(conn, provider, user_id)
    return total


def _save_cursor(conn, provider, user_id) -> None:
    conn.execute(
        """
        INSERT INTO gmail_sync_state (user_id, history_id, last_synced_at)
        VALUES (%s, %s, now())
        ON CONFLICT (user_id) DO UPDATE
            SET history_id = EXCLUDED.history_id, last_synced_at = now()
        """,
        (user_id, provider.cursor()),
    )
    conn.commit()
