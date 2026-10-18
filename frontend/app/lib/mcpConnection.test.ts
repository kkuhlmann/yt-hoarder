import { describe, expect, it } from "vitest"

import { claudeCodeCommand, mcpServerUrl, mcpServersJson } from "./mcpConnection"

describe("mcpServerUrl", () => {
  it("resolves production's relative API path against the page origin", () => {
    expect(mcpServerUrl("/api/mcp", "https://hoarder.example.com")).toBe(
      "https://hoarder.example.com/api/mcp"
    )
  })

  it("keeps dev's absolute API URL, which is on another port than the page", () => {
    expect(mcpServerUrl("http://nas:8000/mcp", "http://nas:3000")).toBe("http://nas:8000/mcp")
  })
})

describe("claudeCodeCommand", () => {
  it("registers the server for every project with the token as a header", () => {
    expect(claudeCodeCommand("http://nas:8000/api/mcp", "ytdlh_abc")).toBe(
      'claude mcp add --scope user --transport http yt-hoarder http://nas:8000/api/mcp ' +
        '--header "Authorization: Bearer ytdlh_abc"'
    )
  })
})

describe("mcpServersJson", () => {
  it("is valid JSON in the mcpServers shape", () => {
    expect(JSON.parse(mcpServersJson("http://nas:8000/api/mcp", "ytdlh_abc"))).toEqual({
      mcpServers: {
        "yt-hoarder": {
          url: "http://nas:8000/api/mcp",
          headers: { Authorization: "Bearer ytdlh_abc" },
        },
      },
    })
  })
})
