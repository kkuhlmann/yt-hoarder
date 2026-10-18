"""MCP server: read-only transcript search tools for AI agents, behind personal access tokens.

Importing this package must stay cheap. The SDK is imported only by `server`, which
the lifespan loads when `mcp.enabled` is set, so a disabled install never loads it.
"""
