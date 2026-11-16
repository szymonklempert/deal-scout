"""The MCP server: an MCP client (Claude Desktop, Claude Code) judges listings, deal-scout remembers and scores them.

    deal-scout mcp            # serve over stdio (what `deal-scout-mcp` does too)

The server makes no model calls at all. It gives the client the owner's criteria and the domain's rules as resources,
read-only tools over the stored listings, and (with submit_review) the one way in: a review goes through
process.apply_review like the Telegram bot's analysis, and the score is computed here, in code.

This package is the only code that imports mcp (the optional extra: `uv sync --extra mcp`); the rest of deal_scout
works without it. A listing's text is untrusted in the client too, so the tools stay narrow and every argument is
validated. Over stdio, stdout is the wire: nothing here prints, and logs go to stderr.
"""

import logging
import sys
from collections.abc import Callable
from pathlib import Path

from mcp.server import MCPServer
from mcp_types import ToolAnnotations

from deal_scout import domain as domains
from deal_scout.config import Settings, load_settings
from deal_scout.domain import Domain
from deal_scout.mcp_server.guide import Guide
from deal_scout.mcp_server.reading import Reader
from deal_scout.mcp_server.writing import Reviewer

log = logging.getLogger("dealscout")

SERVER_NAME = "deal-scout"
INSTRUCTIONS = (
    "Reviews second-hand {item} listings against the owner's criteria. Read the criteria and domain resources, "
    "judge the listing, then call submit_review; the server computes the score."
)
URI_CRITERIA = "deal-scout://criteria"
URI_DOMAIN = "deal-scout://domain/{name}"
URI_LISTING = "deal-scout://listing/{number}"
URI_MARKET = "deal-scout://market"
MARKDOWN = "text/markdown"
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
# Adds files and rewrites a listing's review, but deletes nothing; calling it twice adds a note twice.
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def create_server(
    home: Path, settings: Settings, domain: Domain, *, push: Callable[..., None] | None = None
) -> MCPServer:
    """Build the server for one data home and domain (a factory, so tests use temporary homes).

    push replaces the Google Sheet copy (tests); by default rows go to the sheet only when GOOGLE_SHEET_URL is set.
    """
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS.format(item=domain.item))
    reader = Reader(home, settings, domain)
    reviewer = Reviewer(reader, push)
    guide = Guide(reader)
    server.resource(URI_CRITERIA, name="criteria", description="The owner's criteria.md.", mime_type=MARKDOWN)(
        reader.criteria
    )
    server.resource(
        URI_DOMAIN,
        name="domain_file",
        description="A domain file: instructions.md, checklist.md, card-template.md or a reference file.",
        mime_type=MARKDOWN,
    )(reader.domain_file)
    server.resource(
        URI_LISTING, name="listing_card", description="The card of an analysed listing.", mime_type=MARKDOWN
    )(reader.listing_card)
    server.resource(URI_MARKET, name="market", description="Asking prices of the owner's own listings.")(
        reader.market_page
    )
    for tool in (
        reader.list_listings,
        reader.top,
        reader.compare,
        reader.show_listing,
        reader.viewing_checklist,
        reader.market_prices,
        reader.review_format,
    ):
        server.add_tool(tool, annotations=READ_ONLY)
    server.add_tool(reviewer.submit_review, annotations=WRITES)
    server.add_tool(reviewer.record_seller_reply, annotations=WRITES)
    for prompt in (guide.review_listing, guide.draft_seller_message, guide.prepare_viewing):
        server.prompt()(prompt)
    return server


def setup_logging() -> None:
    """Log to stderr only: over stdio, stdout is the protocol. Never listing text."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main() -> None:
    """Serve the MCP server over stdio: the data home and settings as for the bot, the domain from the data home."""
    home, settings = load_settings()
    setup_logging()
    try:
        domain = domains.load(settings.domain, roots=[home])
    except domains.DomainError as exc:
        sys.exit(str(exc))
    log.info("MCP server: domain %s, data in %s", domain.name, home)
    create_server(home, settings, domain).run()
