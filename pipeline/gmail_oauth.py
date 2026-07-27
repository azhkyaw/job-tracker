"""Per-user Gmail connect via the web OAuth flow (Phase 4, §6.2/§11).

Distinct from the CLI's Desktop-app flow: this uses a Web-application OAuth
client and a redirect back to /oauth/gmail/callback. Tokens are stored
Fernet-encrypted on the user row (bare Credentials.to_json(), same slot the
IMAP path uses with a "kind" discriminator — see pipeline/mailbox.py);
state is stored per user for CSRF checking. Disconnecting either kind of
credential is kind-agnostic and lives in mailbox.disconnect(), not here.
"""

from __future__ import annotations

import secrets

from google_auth_oauthlib.flow import Flow

from . import auth, config

_REDIRECT_PATH = "/oauth/gmail/callback"


def configured() -> bool:
    return config.GMAIL_WEB_CREDENTIALS.exists()


def _flow(state: str | None = None) -> Flow:
    return Flow.from_client_secrets_file(
        str(config.GMAIL_WEB_CREDENTIALS), scopes=config.GMAIL_SCOPES, state=state,
        redirect_uri=config.BASE_URL + _REDIRECT_PATH,
    )


def start(conn, user_id) -> str:
    """Returns the Google authorization URL; stores CSRF state on the user."""
    state = secrets.token_urlsafe(24)
    url, _ = _flow(state).authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true")
    conn.execute("UPDATE users SET oauth_state = %s WHERE id = %s", (state, user_id))
    return url


def finish(conn, user, code: str, state: str) -> None:
    """Exchanges the code and stores encrypted credentials. Raises ValueError
    on state mismatch."""
    if not state or state != user["oauth_state"]:
        raise ValueError("OAuth state mismatch — start the connect flow again")
    flow = _flow(state)
    flow.fetch_token(code=code)
    conn.execute(
        "UPDATE users SET gmail_credentials = %s, oauth_state = NULL WHERE id = %s",
        (auth.encrypt(flow.credentials.to_json()), user["id"]))
