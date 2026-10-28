"""
LinkedIn post/content search tool, plus comment and like actions on one post.

Performs LinkedIn's global content search (the "Posts" results tab) using
innerText extraction, so informal "we're hiring" / "Buscamos ..." posts can
be found before a formal job listing is published. Mirrors search_people:
build a /search/results/content/ URL, scroll to load results, and return the
raw innerText for the LLM to parse, plus post-permalink references.
"""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping.contracts import FilterValidationError
from linkedin_mcp_server.scraping.post_actions import InvalidPostReferenceError

logger = logging.getLogger(__name__)


def register_post_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register post/content-search tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"post", "search"},
        exclude_args=["extractor"],
    )
    async def search_posts(
        keywords: str,
        ctx: Context,
        date_posted: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=10)] = 3,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Search LinkedIn posts/content globally by keyword (the "Posts" tab).

        Use this to catch informal hiring posts ("we're hiring", "Buscamos
        ...", "estamos contratando", "join our team") that often appear before
        a formal job listing exists. This is global content search, distinct
        from get_feed (your own home feed) and get_company_posts (one
        company's page).

        Args:
            keywords: Search keywords (e.g., "Buscamos Unity", "AI automation hiring")
            ctx: FastMCP context for progress reporting
            date_posted: Optional recency filter. One of "past-24h",
                "past-week", "past-month"; the "past_24_hours" / "past_week" /
                "past_month" spellings used by search_jobs are accepted too.
                Omit for any time.
            max_pages: Scroll depth as result "pages" of ~5 scrolls each
                (1-10, default 3). Content search is an infinite scroll, so
                this caps how far the page is scrolled rather than fetching
                discrete pages.

        Returns:
            Dict with url, sections (search_results -> raw text), and optional
            references (post authors, companies, linked jobs) and
            section_errors. The results page carries no per-post permalinks,
            so reach a post through its author. The LLM should parse the raw
            text to extract each post's author, headline/role, company, body,
            posted date, and reaction/comment counts.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_posts"
            )
            logger.info(
                "Searching posts: keywords='%s', date_posted='%s', max_pages=%d",
                keywords,
                date_posted,
                max_pages,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Starting post search"
            )

            try:
                result = await extractor.search_posts(
                    keywords,
                    date_posted=date_posted,
                    max_pages=max_pages,
                )
            except FilterValidationError as e:
                # Validation messages carry actionable detail; surface them as
                # ToolError so mask_error_details doesn't reduce them to a
                # generic "Error calling tool 'search_posts'".
                raise ToolError(str(e)) from e

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except ToolError:
            # Already a properly formatted client-facing error; do not log it
            # as "Unexpected error" via raise_tool_error.
            raise
        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_posts")
        except Exception as e:
            raise_tool_error(e, "search_posts")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Comment on Post",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"post", "actions"},
        exclude_args=["extractor"],
    )
    async def comment_on_post(
        post: str,
        text: str,
        ctx: Context,
        confirm: bool = False,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Comment on a LinkedIn post as the signed-in user.

        Always call first with confirm=False: it opens the post and returns a
        preview (post_excerpt + comment) without posting. Show that preview to
        the user and only call again with confirm=True once they approve.

        Args:
            post: Post URL (/feed/update/urn:li:activity:... or
                /posts/...-activity-...), post URN, or numeric activity ID
            text: Comment text, up to 1250 characters. Em dashes are refused.
            ctx: FastMCP context for progress reporting
            confirm: Must be True to actually post the comment

        Returns:
            Dict with url, status (refused, preview, posted, unverified,
            no_box, no_button), post_excerpt, comment, sent and retry_safe.
            When retry_safe is False the comment may already be posted, so do
            not call again without checking the post.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="comment_on_post"
            )
            logger.info("Commenting on post %s (confirm=%s)", post, confirm)
            await ctx.report_progress(progress=0, total=100, message="Opening post")
            try:
                result = await extractor.comment_on_post(post, text, confirm=confirm)
            except InvalidPostReferenceError as e:
                raise ToolError(str(e)) from e
            await ctx.report_progress(progress=100, total=100, message="Complete")
            return result

        except ToolError:
            raise
        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "comment_on_post")
        except Exception as e:
            raise_tool_error(e, "comment_on_post")  # NoReturn

    @mcp.tool(
        timeout=tool_timeout,
        title="Like Post",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"post", "actions"},
        exclude_args=["extractor"],
    )
    async def react_to_post(
        post: str,
        ctx: Context,
        confirm: bool = False,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Like a LinkedIn post as the signed-in user.

        Call with confirm=False to preview the post first; confirm=True likes it.

        Args:
            post: Post URL, post URN, or numeric activity ID
            ctx: FastMCP context for progress reporting
            confirm: Must be True to actually like the post

        Returns:
            Dict with url, status (preview, liked, already_liked,
            like_unavailable, unverified), post_excerpt and liked.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="react_to_post"
            )
            logger.info("Liking post %s (confirm=%s)", post, confirm)
            await ctx.report_progress(progress=0, total=100, message="Opening post")
            try:
                result = await extractor.react_to_post(post, confirm=confirm)
            except InvalidPostReferenceError as e:
                raise ToolError(str(e)) from e
            await ctx.report_progress(progress=100, total=100, message="Complete")
            return result

        except ToolError:
            raise
        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "react_to_post")
        except Exception as e:
            raise_tool_error(e, "react_to_post")  # NoReturn
