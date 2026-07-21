"""Gmail ingestion (design doc §6.2).

Two entry points:
  backfill(conn, service, user_id, months)  — first run; walks history back N
      months via messages.list with a server-side query, reconstructing the
      application record from confirmations/rejections already in the inbox.
  incremental(conn, service, user_id)       — the 15-minute cron; walks the
      History API from the stored cursor, falling back to a time-window query
      when the cursor has expired (Gmail keeps history ~1 week).

Both funnel every message through the same path: candidate filter ->
store into emails (idempotent on gmail_message_id) -> enqueue classify job.
Non-candidate mail is never stored and never reaches an LLM (§11).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from html import unescape

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config, db

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def service_for_user(conn, user) -> "object | None":
    """Multi-tenant path: build a Gmail service from the user's stored
    (encrypted) credentials, refreshing and re-persisting when needed.
    Falls back to the legacy single-user token file when the DB has nothing.
    Returns None when the user has no Gmail connected."""
    from . import auth  # local import: avoid cycle at module load
    import json as _json
    raw = user.get("gmail_credentials") if isinstance(user, dict) else user["gmail_credentials"]
    if raw:
        decrypted = auth.decrypt(raw)
        if decrypted is None:
            raise RuntimeError("stored Gmail credentials cannot be decrypted — "
                               "TRACKER_SECRET_KEY changed? Reconnect Gmail in Settings")
        creds = Credentials.from_authorized_user_info(_json.loads(decrypted), SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            conn.execute("UPDATE users SET gmail_credentials = %s WHERE id = %s",
                         (auth.encrypt(creds.to_json()), user["id"]))
            conn.commit()
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    if config.GMAIL_TOKEN_FILE.exists():
        return get_service()
    return None


# --------------------------------------------------------------------------- auth

def get_service():
    """OAuth dance on first run (prints a URL — works under WSL where the
    browser can't be launched), silent token refresh afterwards."""
    creds = None
    if config.GMAIL_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.GMAIL_TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.GMAIL_CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=False)
        config.GMAIL_TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# --------------------------------------------------------------------------- filtering

def _sender_domain(from_header: str) -> str:
    addr = parseaddr(from_header or "")[1]
    return addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""


def is_candidate(from_header: str, subject: str) -> bool:
    domain = _sender_domain(from_header)
    if any(domain == d or domain.endswith("." + d) for d in config.ALLOWLIST_DOMAINS):
        return True
    subject_l = (subject or "").lower()
    return any(kw in subject_l for kw in config.SUBJECT_KEYWORDS)


# --------------------------------------------------------------------------- body extraction

_TAG_RE = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _walk_parts(payload: dict, out: dict) -> None:
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if data and mime == "text/plain" and "plain" not in out:
        out["plain"] = _decode(data)
    elif data and mime == "text/html" and "html" not in out:
        out["html"] = _decode(data)
    for part in payload.get("parts", []) or []:
        _walk_parts(part, out)


def extract_body(payload: dict) -> str:
    """Prefer text/plain; fall back to tag-stripped text/html."""
    found: dict = {}
    _walk_parts(payload or {}, found)
    if "plain" in found:
        return found["plain"].strip()
    if "html" in found:
        text = _TAG_RE.sub(" ", found["html"])
        text = _HTML_RE.sub(" ", text)
        return re.sub(r"[ \t]+", " ", unescape(text)).strip()
    return ""


# --------------------------------------------------------------------------- storing

def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])}


def store_message(conn, user_id, msg: dict) -> bool:
    """Insert one fetched message if it's a candidate. Idempotent: re-syncing
    the same gmail_message_id is a no-op. Returns True when newly stored."""
    headers = _headers(msg)
    sender = headers.get("from", "")
    subject = headers.get("subject", "")
    if not is_candidate(sender, subject):
        return False
    received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
    row = conn.execute(
        """
        INSERT INTO emails (user_id, gmail_message_id, sender, subject, body_text, received_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, gmail_message_id) DO NOTHING
        RETURNING id
        """,
        (user_id, msg["id"], sender, subject, extract_body(msg.get("payload")), received_at),
    ).fetchone()
    if row is None:
        return False
    db.enqueue(conn, user_id, "classify_email", {"email_id": str(row["id"])})
    return True


def _fetch_and_store(conn, service, user_id, message_ids) -> int:
    stored = 0
    for mid in message_ids:
        msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        with conn.transaction():
            if store_message(conn, user_id, msg):
                stored += 1
    return stored


# --------------------------------------------------------------------------- backfill

def _backfill_query(months: int) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=months * 31)).strftime("%Y/%m/%d")
    froms = " OR ".join(f"from:{d}" for d in sorted(config.ALLOWLIST_DOMAINS))
    subjects = " OR ".join(f'subject:"{kw}"' for kw in config.SUBJECT_KEYWORDS)
    return f"after:{since} (({froms}) OR ({subjects}))"


def backfill(conn, service, user_id, months: int = config.BACKFILL_MONTHS_DEFAULT) -> int:
    """First-run reconstruction of application history from the inbox."""
    query, page_token, total = _backfill_query(months), None, 0
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token
        ).execute()
        ids = [m["id"] for m in resp.get("messages", [])]
        total += _fetch_and_store(conn, service, user_id, ids)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    _save_cursor(conn, service, user_id)
    return total


# --------------------------------------------------------------------------- incremental

def _save_cursor(conn, service, user_id) -> None:
    profile = service.users().getProfile(userId="me").execute()
    conn.execute(
        """
        INSERT INTO gmail_sync_state (user_id, history_id, last_synced_at)
        VALUES (%s, %s, now())
        ON CONFLICT (user_id) DO UPDATE
            SET history_id = EXCLUDED.history_id, last_synced_at = now()
        """,
        (user_id, str(profile["historyId"])),
    )
    conn.commit()


def _window_fallback(conn, service, user_id, since: datetime) -> int:
    """Used when there is no cursor or the cursor expired (History API 404)."""
    query = f"after:{since.strftime('%Y/%m/%d')}"
    page_token, total = None, 0
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token
        ).execute()
        total += _fetch_and_store(conn, service, user_id,
                                  [m["id"] for m in resp.get("messages", [])])
        page_token = resp.get("nextPageToken")
        if not page_token:
            return total


def incremental(conn, service, user_id) -> int:
    state = conn.execute(
        "SELECT history_id, last_synced_at FROM gmail_sync_state WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    total = 0
    if state is None or not state["history_id"]:
        since = (state or {}).get("last_synced_at") or datetime.now(timezone.utc) - timedelta(days=1)
        total = _window_fallback(conn, service, user_id, since)
    else:
        try:
            page_token, message_ids = None, []
            while True:
                resp = service.users().history().list(
                    userId="me", startHistoryId=state["history_id"],
                    historyTypes=["messageAdded"], pageToken=page_token,
                ).execute()
                for h in resp.get("history", []):
                    message_ids += [m["message"]["id"] for m in h.get("messagesAdded", [])]
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            total = _fetch_and_store(conn, service, user_id, dict.fromkeys(message_ids))
        except HttpError as err:
            if err.resp.status == 404:  # cursor expired — Gmail keeps ~1 week
                since = state["last_synced_at"] or datetime.now(timezone.utc) - timedelta(days=7)
                total = _window_fallback(conn, service, user_id, since)
            else:
                raise
    _save_cursor(conn, service, user_id)
    return total
