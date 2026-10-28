"""Tests for the comment and like actions on a single post."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.scraping.post_actions import (
    InvalidPostReferenceError,
    PostActions,
    post_permalink,
    refuse_an_invalid_comment,
)

URN_URL = "https://www.linkedin.com/feed/update/urn:li:activity:7389012345678901234/"


@pytest.mark.parametrize(
    "reference",
    [
        URN_URL,
        "urn:li:activity:7389012345678901234",
        "7389012345678901234",
        "https://www.linkedin.com/posts/jane-doe_law-activity-7389012345678901234-AbCd",
    ],
)
def test_post_permalink_normalizes_every_reference_form(reference):
    assert post_permalink(reference) == URN_URL


def test_post_permalink_keeps_ugc_post_urns():
    assert post_permalink("urn:li:ugcPost:7389012345678901234") == (
        "https://www.linkedin.com/feed/update/urn:li:ugcPost:7389012345678901234/"
    )


def test_post_permalink_rejects_a_profile_url():
    with pytest.raises(InvalidPostReferenceError):
        post_permalink("https://www.linkedin.com/in/jane-doe/")


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("   ", "empty"),
        ("x" * 1251, "1250"),
        ("Great point — agreed", "em dash"),
    ],
)
def test_invalid_comments_are_refused(text, reason):
    refusal = refuse_an_invalid_comment(text)
    assert refusal is not None and reason in refusal


def test_a_plain_comment_is_accepted():
    assert refuse_an_invalid_comment("Great point, agreed.") is None


def _actions(
    excerpt: str = "Jane Doe\nPost body",
) -> tuple[PostActions, MagicMock, AsyncMock]:
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=excerpt)
    session = MagicMock(
        page=page, check_rate_limit=AsyncMock(), dismiss_modal=AsyncMock()
    )
    navigator = MagicMock(_navigate_to_page=AsyncMock())
    return PostActions(session, navigator), page, navigator._navigate_to_page


async def test_comment_without_confirm_previews_and_types_nothing():
    actions, page, navigate = _actions()
    result = await actions.comment_on_post(
        "7389012345678901234", "Nice.", confirm=False
    )

    navigate.assert_awaited_once_with(URN_URL)
    assert result["status"] == "preview"
    assert result["sent"] is False and result["retry_safe"] is True
    assert result["post_excerpt"] == "Jane Doe\nPost body"
    page.keyboard.type.assert_not_called()


async def test_refused_comment_never_opens_the_post():
    actions, _page, navigate = _actions()
    result = await actions.comment_on_post(URN_URL, "a — b", confirm=True)

    assert result["status"] == "refused"
    navigate.assert_not_awaited()


async def test_like_preview_does_not_click():
    actions, page, _navigate = _actions()
    page.evaluate = AsyncMock(side_effect=["excerpt", "unpressed"])
    result = await actions.react_to_post(URN_URL, confirm=False)

    assert result["status"] == "preview"
    assert page.evaluate.await_count == 2
    assert page.evaluate.await_args_list[1].args[1] is False


async def test_like_reports_an_existing_like():
    actions, page, _navigate = _actions()
    page.evaluate = AsyncMock(side_effect=["excerpt", "pressed"])
    result = await actions.react_to_post(URN_URL, confirm=True)

    assert result == {
        "url": URN_URL,
        "status": "already_liked",
        "post_excerpt": "excerpt",
        "liked": True,
    }
