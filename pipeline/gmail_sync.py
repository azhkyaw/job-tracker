"""Gmail API provider — OAuth ingest (docs/email-ingest.md §8: kept as the
alternative for Workspace / Advanced Protection accounts, which cannot use
an IMAP app password). The default path is now pipeline/gmail_imap.py; the
shared candidate-filter / storage / query-building orchestration both
providers funnel through lives in pipeline/mailbox.py — never reimplement
it here (see mailbox.py's module docstring).

Two responsibilities:
  get_service()       — CLI single-user desktop OAuth dance (prints a URL —
                         works under WSL where the browser can't be
                         launched), silent token refresh afterwards.
  GmailApiProvider     — implements the mailbox provider protocol: search()
                         (oldest-first, walking messages.list), fetch()
                         (normalises one message), incremental_handles()
                         (the History API from the stored cursor, falling
                         back to a time-window query when the cursor has
                         expired — Gmail keeps history ~1 week), cursor()
                         (the current historyId).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config
from .mailbox import MailboxAuthError

SCOPES = config.GMAIL_SCOPES


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
            # host="127.0.0.1", not the default "localhost" — on Windows the
            # browser's IPv6-first resolution of "localhost" races a stray
            # connection against the real OAuth redirect for this server's
            # single handle_request() call, losing the code and surfacing as
            # a misleading WSGITimeoutError instead of a real timeout.
            creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=False)
        config.GMAIL_TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# --------------------------------------------------------------------------- body extraction

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
    from .mailbox import html_to_text

    found: dict = {}
    _walk_parts(payload or {}, found)
    if "plain" in found:
        return found["plain"].strip()
    if "html" in found:
        return html_to_text(found["html"])
    return ""


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])}


# --------------------------------------------------------------------------- listing

def _list_message_ids(service, query: str) -> list[str]:
    """All message ids matching `query`, oldest first.

    messages.list returns newest-first, page by page (page 1 = newest 100,
    last page = oldest) — collecting every page before reversing undoes that
    ordering globally, not just within one page. This matters because
    downstream processing is order-sensitive: matcher.dispatch() can only
    match a status email (rejection/interview_invite/etc.) against an
    application that a chronologically-earlier confirmation email created.
    Enqueueing/processing newest-first (the un-reversed order) intermittently
    sent a status email to triage for no real reason — the application
    existed, it just hadn't been created yet when that email was matched.
    """
    page_token, ids = None, []
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=100, pageToken=page_token
        ).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return list(reversed(ids))


# --------------------------------------------------------------------------- provider

class GmailApiProvider:
    """Gmail API + OAuth. `kind` is for display only — mailbox.py never
    branches on it; the provider protocol is what both providers share."""

    kind = "gmail_api"

    def __init__(self, service):
        self._service = service

    @classmethod
    def from_stored(cls, conn, user, payload: dict) -> "GmailApiProvider":
        """Builds a service from the user's stored OAuth credentials JSON
        (already decrypted and parsed by mailbox.provider_for_user),
        refreshing and re-persisting when needed."""
        from . import auth  # local import: avoid cycle at module load

        creds = Credentials.from_authorized_user_info(payload, SCOPES)
        if not creds.valid and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as err:
                # Tokens die on Google-password change, ~6 months of disuse,
                # user revocation, or (until docs/open-source.md §2 is
                # resolved) the 7-day Testing-status expiry. Surface this the
                # same way an IMAP login failure is surfaced — never a
                # traceback from deep inside a cron run.
                raise MailboxAuthError(
                    f"Gmail authorization expired for {user.get('email', '')} — "
                    f"reconnect Gmail in Settings ({err})"
                ) from err
            conn.execute("UPDATE users SET gmail_credentials = %s WHERE id = %s",
                         (auth.encrypt(creds.to_json()), user["id"]))
            conn.commit()
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False))

    def search(self, query: str) -> list[str]:
        return _list_message_ids(self._service, query)

    def fetch(self, message_id: str) -> dict | None:
        try:
            msg = self._service.users().messages().get(
                userId="me", id=message_id, format="full").execute()
        except HttpError as err:
            if err.resp.status == 404:  # vanished between search and fetch
                return None
            raise
        headers = _headers(msg)
        return {
            "id": msg["id"],
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "body_text": extract_body(msg.get("payload")),
            "received_at": datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc),
        }

    def incremental_handles(self, state) -> list[str]:
        """Used when there is no cursor or the cursor expired (History API
        404): a bare after: query, deliberately NOT ANDed with the
        allowlist/subject filter group. Gmail's subject:"…" phrase matching
        and is_candidate's case-insensitive substring matching are not
        equivalent, so adding server-side filtering here would narrow what
        this path stores relative to its historical behaviour. The IMAP
        provider has no equivalent expired-cursor fallback, so this
        asymmetry never causes the two paths to disagree on what a full
        backfill would have found."""
        if state is None or not state["history_id"]:
            since = (state or {}).get("last_synced_at") or datetime.now(timezone.utc) - timedelta(days=1)
            return _list_message_ids(self._service, f"after:{since.strftime('%Y/%m/%d')}")
        try:
            page_token, message_ids = None, []
            while True:
                resp = self._service.users().history().list(
                    userId="me", startHistoryId=state["history_id"],
                    historyTypes=["messageAdded"], pageToken=page_token,
                ).execute()
                for h in resp.get("history", []):
                    message_ids += [m["message"]["id"] for m in h.get("messagesAdded", [])]
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            return list(dict.fromkeys(message_ids))
        except HttpError as err:
            if err.resp.status == 404:  # cursor expired — Gmail keeps ~1 week
                since = state["last_synced_at"] or datetime.now(timezone.utc) - timedelta(days=7)
                return _list_message_ids(self._service, f"after:{since.strftime('%Y/%m/%d')}")
            raise

    def cursor(self) -> str:
        profile = self._service.users().getProfile(userId="me").execute()
        return str(profile["historyId"])

    def close(self) -> None:
        pass
