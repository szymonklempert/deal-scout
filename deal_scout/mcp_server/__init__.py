"""The MCP server package: `create_server` builds it, `main` serves it over stdio (`deal-scout mcp`)."""

from deal_scout.mcp_server.server import create_server, main

__all__ = ["create_server", "main"]
