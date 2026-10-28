"""Write actions on a single LinkedIn post: comment and react.

Both actions open the post's permalink (``/feed/update/<urn>/``) so the post
under action is the only one on the page, rather than acting on a feed item
whose position can shift while the page loads. Each action is gated: without
``confirm`` it only opens the post and returns a preview of what would be
written, so a caller can show the post and the text before anything is sent.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from linkedin_mcp_server.scraping.navigation import PageNavigator
from linkedin_mcp_server.scraping.session import ScrapingSession

logger = logging.getLogger(__name__)

# LinkedIn rejects comments over this length in its own editor.
MAX_COMMENT_LENGTH = 1250

# Characters the account owner never wants in anything posted under their name.
FORBIDDEN_COMMENT_CHARACTERS = {"—": "em dash"}

_POST_URN_RE = re.compile(r"urn:li:(activity|ugcPost|share):(\d{10,})")
_ACTIVITY_ID_RE = re.compile(r"activity[-:](\d{10,})")
_BARE_ID_RE = re.compile(r"^\d{10,}$")

_COMMENT_BOX_SELECTOR = "div[role='textbox'][contenteditable='true']"

# The post body on a permalink page: the first feed update in <main>.
_POST_EXCERPT_JS = """
() => {
    const main = document.querySelector('main') || document.body;
    const post = main.querySelector('[data-urn], [data-id], article') || main;
    return (post.innerText || '').trim().slice(0, 600);
}
"""

# Clicks the "Comment" submit button that belongs to the focused editor. The
# button carries no stable class, so walk up from the editor and take the
# first button whose own text is exactly "Comment" (the social-bar button that
# opens the box reads the same, but sits outside the editor's form).
_SUBMIT_COMMENT_JS = """
(selector) => {
    const box = document.activeElement && document.activeElement.matches(selector)
        ? document.activeElement
        : document.querySelector(selector);
    if (!box) return 'no_box';
    let node = box.parentElement;
    for (let depth = 0; node && depth < 12; depth++, node = node.parentElement) {
        const button = [...node.querySelectorAll('button')].find(
            (b) => (b.innerText || '').trim() === 'Comment' && !b.disabled
        );
        if (button) { button.click(); return 'clicked'; }
    }
    return 'no_button';
}
"""

# True once the comment text appears on the page outside any editor.
_COMMENT_POSTED_JS = """
(snippet) => {
    const editors = [...document.querySelectorAll("[contenteditable='true']")];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const parent = node.parentElement;
        if (!parent || editors.some((e) => e.contains(parent))) continue;
        if ((parent.innerText || node.textContent || '').includes(snippet)) return true;
    }
    return false;
}
"""

# State of the post's own Like button: the first reaction button in <main>.
_LIKE_STATE_JS = """
(click) => {
    const main = document.querySelector('main') || document.body;
    const button = [...main.querySelectorAll('button[aria-label]')].find((b) =>
        /^(React Like|Like|Unlike|Reacted)/i.test(b.getAttribute('aria-label'))
    );
    if (!button) return 'missing';
    const pressed = button.getAttribute('aria-pressed') === 'true';
    if (click && !pressed) button.click();
    return pressed ? 'pressed' : (click ? 'clicked' : 'unpressed');
}
"""


class InvalidPostReferenceError(ValueError):
    """The post reference names no LinkedIn post this tool can open."""


def post_permalink(post: str) -> str:
    """Normalize a post URL, URN or numeric activity ID to its permalink."""
    value = post.strip()
    if match := _POST_URN_RE.search(value):
        kind, post_id = match.groups()
        return f"https://www.linkedin.com/feed/update/urn:li:{kind}:{post_id}/"
    if match := _ACTIVITY_ID_RE.search(value):
        return f"https://www.linkedin.com/feed/update/urn:li:activity:{match.group(1)}/"
    if _BARE_ID_RE.match(value):
        return f"https://www.linkedin.com/feed/update/urn:li:activity:{value}/"
    raise InvalidPostReferenceError(
        "Pass a LinkedIn post URL (/feed/update/urn:li:activity:... or "
        "/posts/...-activity-...), a post URN, or a numeric activity ID."
    )


def refuse_an_invalid_comment(text: str) -> str | None:
    """Return why *text* cannot be posted, or None when it can."""
    if not text.strip():
        return "Comment text is empty."
    if len(text) > MAX_COMMENT_LENGTH:
        return (
            f"Comment is {len(text)} characters; LinkedIn allows {MAX_COMMENT_LENGTH}."
        )
    for char, name in FORBIDDEN_COMMENT_CHARACTERS.items():
        if char in text:
            return f"Comment contains an {name}; rewrite it with commas or periods."
    return None


class PostActions:
    """Own the write actions whose subject is one LinkedIn post."""

    def __init__(self, session: ScrapingSession, navigator: PageNavigator):
        self._session = session
        self._navigator = navigator

    async def _open_post(self, url: str) -> str:
        await self._navigator._navigate_to_page(url)
        await self._session.check_rate_limit()
        await self._session.dismiss_modal()
        return str(await self._session.page.evaluate(_POST_EXCERPT_JS))

    async def comment_on_post(
        self, post: str, text: str, *, confirm: bool
    ) -> dict[str, Any]:
        """Comment on a post; without *confirm*, preview the post and text only."""
        url = post_permalink(post)
        refusal = refuse_an_invalid_comment(text)
        if refusal is not None:
            return {
                "url": url,
                "status": "refused",
                "message": refusal,
                "sent": False,
                "retry_safe": True,
            }

        excerpt = await self._open_post(url)
        if not confirm:
            return {
                "url": url,
                "status": "preview",
                "post_excerpt": excerpt,
                "comment": text,
                "sent": False,
                "retry_safe": True,
                "message": "Not sent. Call again with confirm=True to post.",
            }

        page = self._session.page
        box = page.locator(_COMMENT_BOX_SELECTOR).first
        if await page.locator(_COMMENT_BOX_SELECTOR).count() == 0:
            # The editor only mounts after the social-bar Comment button is used.
            await page.locator("main button[aria-label*='Comment']").first.click()
            await page.wait_for_selector(_COMMENT_BOX_SELECTOR, timeout=8000)
        await box.click()
        await page.keyboard.type(text, delay=15)
        await self._session.delay(0.5)

        clicked = await page.evaluate(_SUBMIT_COMMENT_JS, _COMMENT_BOX_SELECTOR)
        if clicked != "clicked":
            return {
                "url": url,
                "status": clicked,
                "post_excerpt": excerpt,
                "message": "Typed the comment but found no Comment button; nothing was submitted.",
                "sent": False,
                "retry_safe": True,
            }

        snippet = text.strip().splitlines()[0][:60]
        try:
            await page.wait_for_function(_COMMENT_POSTED_JS, arg=snippet, timeout=10000)
            posted = True
        except Exception:
            logger.warning("Comment submitted but not seen on the page: %s", url)
            posted = False
        return {
            "url": url,
            "status": "posted" if posted else "unverified",
            "post_excerpt": excerpt,
            "comment": text,
            "sent": posted,
            "retry_safe": False,
        }

    async def react_to_post(self, post: str, *, confirm: bool) -> dict[str, Any]:
        """Like a post; without *confirm*, preview the post only."""
        url = post_permalink(post)
        excerpt = await self._open_post(url)
        page = self._session.page
        state = await page.evaluate(_LIKE_STATE_JS, False)
        if state == "missing":
            return {
                "url": url,
                "status": "like_unavailable",
                "post_excerpt": excerpt,
                "liked": False,
            }
        if state == "pressed":
            return {
                "url": url,
                "status": "already_liked",
                "post_excerpt": excerpt,
                "liked": True,
            }
        if not confirm:
            return {
                "url": url,
                "status": "preview",
                "post_excerpt": excerpt,
                "liked": False,
                "message": "Not liked. Call again with confirm=True to like.",
            }
        await page.evaluate(_LIKE_STATE_JS, True)
        await self._session.delay(1.5)
        liked = await page.evaluate(_LIKE_STATE_JS, False) == "pressed"
        return {
            "url": url,
            "status": "liked" if liked else "unverified",
            "post_excerpt": excerpt,
            "liked": liked,
        }
