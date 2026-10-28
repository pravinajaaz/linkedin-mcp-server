"""Remote entry point: streamable HTTP behind Google OAuth for one account.

Runs the normal CLI startup (config, browser environment, profile claim) and
serves over streamable HTTP, but attaches a Google OAuth provider to the
server so a remote MCP client such as a claude.ai custom connector can sign
in. Only the Google accounts listed in ``ALLOWED_EMAILS`` get a valid token;
any other account can complete Google's sign-in but every MCP call is refused.

Environment:
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET  OAuth client (type: Web application)
    BASE_URL         Public origin, e.g. https://linkedin-mcp.up.railway.app
    ALLOWED_EMAILS   Comma-separated Google accounts allowed to call tools
    JWT_SIGNING_KEY  Stable secret so issued tokens survive restarts
    PORT             Listen port (Railway sets it)
    FASTMCP_HOME     Where OAuth client registrations are stored; put it on
                     the persistent volume so connectors stay signed in
"""

from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlparse

import fastmcp
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider

from linkedin_mcp_server import cli_main

logger = logging.getLogger(__name__)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"remote_main: {name} must be set")
    return value


def allowed_emails_from(value: str) -> frozenset[str]:
    """Parse a comma-separated email list into a normalized set."""
    return frozenset(e.strip().lower() for e in value.split(",") if e.strip())


class AllowlistGoogleProvider(GoogleProvider):
    """Google OAuth proxy that only accepts tokens for allowlisted emails."""

    def __init__(self, *, allowed_emails: frozenset[str], **kwargs):
        super().__init__(**kwargs)
        self._allowed_emails = allowed_emails

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        access = await super().load_access_token(token)
        if access is None:
            return None
        email = str(access.claims.get("email") or "").lower()
        if email not in self._allowed_emails or not access.claims.get("email_verified"):
            logger.warning("Refused MCP token for non-allowlisted account %r", email)
            return None
        return access


def build_auth() -> AllowlistGoogleProvider:
    """Build the OAuth provider from the environment."""
    allowed = allowed_emails_from(_required("ALLOWED_EMAILS"))
    if not allowed:
        sys.exit("remote_main: ALLOWED_EMAILS has no addresses")
    return AllowlistGoogleProvider(
        allowed_emails=allowed,
        client_id=_required("GOOGLE_CLIENT_ID"),
        client_secret=_required("GOOGLE_CLIENT_SECRET"),
        base_url=_required("BASE_URL").rstrip("/"),
        required_scopes=["openid", "email"],
        jwt_signing_key=_required("JWT_SIGNING_KEY"),
    )


def main() -> None:
    auth = build_auth()
    base_host = urlparse(os.environ["BASE_URL"]).hostname or ""
    # The CLI turns on Host validation for HTTP; the public name must be listed
    # or every request through Railway's edge is answered 421. FastMCP reads its
    # settings at import, so set the loaded value rather than the environment.
    if not fastmcp.settings.http_allowed_hosts:
        fastmcp.settings.http_allowed_hosts = [base_host]

    create = cli_main.create_mcp_server

    def create_with_auth(*args, **kwargs):
        mcp = create(*args, **kwargs)
        mcp.auth = auth
        return mcp

    cli_main.create_mcp_server = create_with_auth
    cli_main.main(
        [
            "--transport",
            "streamable-http",
            "--host",
            "0.0.0.0",
            "--port",
            os.environ.get("PORT", "8000"),
            *sys.argv[1:],
        ]
    )


if __name__ == "__main__":
    main()
