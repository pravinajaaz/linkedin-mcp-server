"""Tests for the Google-OAuth remote entry point's account allowlist."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider

from linkedin_mcp_server.remote_main import AllowlistGoogleProvider, allowed_emails_from


def _provider() -> AllowlistGoogleProvider:
    return AllowlistGoogleProvider(
        allowed_emails=allowed_emails_from(" Owner@Example.com ,second@example.com"),
        client_id="client",
        client_secret="secret",
        base_url="https://mcp.example.com",
        jwt_signing_key="test-signing-key",
    )


def _token(email: str, verified: bool = True) -> AccessToken:
    return AccessToken(
        token="t",
        client_id="client",
        scopes=["openid"],
        claims={"email": email, "email_verified": verified},
    )


def test_allowed_emails_are_normalized():
    assert allowed_emails_from(" A@x.com, ,b@X.com ") == {"a@x.com", "b@x.com"}


async def test_allowlisted_verified_email_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    provider = _provider()
    with patch.object(
        GoogleProvider,
        "load_access_token",
        AsyncMock(return_value=_token("owner@example.com")),
    ):
        assert await provider.load_access_token("jwt") is not None


async def test_other_or_unverified_email_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    provider = _provider()
    for token in (_token("intruder@example.com"), _token("owner@example.com", False)):
        with patch.object(
            GoogleProvider, "load_access_token", AsyncMock(return_value=token)
        ):
            assert await provider.load_access_token("jwt") is None
