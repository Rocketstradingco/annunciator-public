"""The optional memory router: typed routing advice for AI agents, plus write leases.

The router asks a configurable model provider where a durable fact belongs and
whether it should be stored at all. It never writes memory itself; agents do,
after acquiring a cooperative lease. ``mcp.py`` exposes it to any MCP client.
"""
