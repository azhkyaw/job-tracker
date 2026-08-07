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

_TAG_RE = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = _HTML_RE.sub(" ", text)
    return re.sub(r"[ \t]+", " ", unescape(text)).strip()


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
