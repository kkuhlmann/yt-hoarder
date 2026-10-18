/**
 * Copy-paste setup for connecting an AI agent to this server's MCP endpoint.
 *
 * Pure so the exact strings users paste into a terminal or a config file are unit-tested:
 * a quoting slip here surfaces as a confusing error in someone else's tool.
 */

export const MCP_SERVER_NAME = "yt-hoarder"

/**
 * The MCP endpoint as an absolute URL that an agent on another machine can reach.
 *
 * `apiPath` is `apiUrl("/mcp")`: relative `/api/mcp` in production, already absolute
 * (`http://host:8000/mcp`) in dev, so resolving against the page origin handles both.
 */
export function mcpServerUrl(apiPath: string, origin: string): string {
  return new URL(apiPath, origin).href
}

/** Registers the server for every project (`--scope user`), not just the current directory. */
export function claudeCodeCommand(url: string, token: string): string {
  return (
    `claude mcp add --scope user --transport http ${MCP_SERVER_NAME} ${url} ` +
    `--header "Authorization: Bearer ${token}"`
  )
}

/** The `mcpServers` shape LM Studio's mcp.json and Cursor read, among others. */
export function mcpServersJson(url: string, token: string): string {
  return JSON.stringify(
    { mcpServers: { [MCP_SERVER_NAME]: { url, headers: { Authorization: `Bearer ${token}` } } } },
    null,
    2
  )
}
