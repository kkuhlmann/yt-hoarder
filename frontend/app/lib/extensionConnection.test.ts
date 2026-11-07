import { describe, expect, it } from "vitest"

import { extensionServerUrl } from "./extensionConnection"

describe("extensionServerUrl", () => {
  it("resolves production's relative API base against the page origin", () => {
    expect(extensionServerUrl("/api", "https://hoarder.example.com")).toBe(
      "https://hoarder.example.com/api"
    )
  })

  it("offers the address dev is opened at rather than its :8000 API", () => {
    expect(extensionServerUrl("http://192.168.1.50:8000", "http://192.168.1.50:3000")).toBe(
      "http://192.168.1.50:3000"
    )
  })

  it("keeps a configured API base that is not :8000 of the page's host", () => {
    expect(extensionServerUrl("https://api.example.com", "http://192.168.1.50:3000")).toBe(
      "https://api.example.com"
    )
  })

  it("keeps :8000 when the page is not on dev's web port", () => {
    expect(extensionServerUrl("http://192.168.1.50:8000", "http://192.168.1.50:3001")).toBe(
      "http://192.168.1.50:8000"
    )
  })

  it("keeps /api when production is itself served on :8000", () => {
    expect(extensionServerUrl("/api", "http://192.168.1.50:8000")).toBe(
      "http://192.168.1.50:8000/api"
    )
  })

  it("falls back to the origin when the API shares it with no prefix", () => {
    expect(extensionServerUrl("", "https://hoarder.example.com")).toBe(
      "https://hoarder.example.com"
    )
  })
})
