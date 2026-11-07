// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"

const get = vi.fn()
const post = vi.fn()
const del = vi.fn()

vi.mock("axios", () => {
  const axios = {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
    delete: (...args: unknown[]) => del(...args),
    defaults: { withCredentials: false },
    interceptors: { response: { use: () => {} } },
    isCancel: () => false,
    isAxiosError: () => false,
  }
  return { default: axios, isCancel: axios.isCancel, isAxiosError: axios.isAxiosError }
})
vi.mock("@/app/lib/clipboard", () => ({ copyToClipboard: vi.fn(async () => true) }))
vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const { AccessTokensDialog } = await import("./AccessTokensDialog")

const TOKEN = {
  id: 1,
  name: "laptop",
  scope: "mcp" as const,
  token_prefix: "ytdlh_abcdef",
  created_at: "2026-10-01T12:00:00",
  last_used_at: null,
  expires_at: null,
  status: "active" as const,
}

beforeEach(() => {
  get.mockResolvedValue({ data: [TOKEN] })
})

// RTL's automatic cleanup never registers with globals off — see .claude/rules/frontend.md.
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("AccessTokensDialog", () => {
  it("lists the user's tokens with their status", async () => {
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)

    expect(await screen.findByText("laptop")).toBeDefined()
    expect(screen.getByText("active")).toBeDefined()
    expect(screen.getByText(/ytdlh_abcdef… · created .+ · used never/)).toBeDefined()
  })

  it("shows a new token once, with copy-paste setup, then returns to the list", async () => {
    post.mockResolvedValue({ data: { ...TOKEN, id: 2, name: "agent", token: "ytdlh_secret" } })
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("laptop")

    fireEvent.change(screen.getByPlaceholderText(/Name/), { target: { value: "agent" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    expect(await screen.findByText("ytdlh_secret")).toBeDefined()
    expect(post).toHaveBeenCalledWith(expect.stringMatching(/\/auth\/tokens$/), {
      name: "agent",
      expires_in_days: null,
      scope: "mcp",
    })
    expect(screen.getByText(/claude mcp add --scope user --transport http yt-hoarder/)).toBeDefined()

    fireEvent.click(screen.getByRole("button", { name: "Done" }))
    expect(screen.queryByText("ytdlh_secret")).toBeNull()
    expect(screen.getByText("laptop")).toBeDefined()
  })

  it("never shows the token again after the dialog is closed", async () => {
    post.mockResolvedValue({ data: { ...TOKEN, id: 2, name: "agent", token: "ytdlh_secret" } })
    const onOpenChange = vi.fn()
    const { rerender } = render(<AccessTokensDialog open onOpenChange={onOpenChange} mcpEnabled />)
    await screen.findByText("laptop")
    fireEvent.change(screen.getByPlaceholderText(/Name/), { target: { value: "agent" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))
    await screen.findByText("ytdlh_secret")

    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" })
    expect(onOpenChange).toHaveBeenCalledWith(false)
    rerender(<AccessTokensDialog open={false} onOpenChange={onOpenChange} mcpEnabled />)
    rerender(<AccessTokensDialog open onOpenChange={onOpenChange} mcpEnabled />)

    await screen.findByText("laptop")
    expect(screen.queryByText("ytdlh_secret")).toBeNull()
  })

  it("sends the chosen expiry", async () => {
    post.mockResolvedValue({ data: { ...TOKEN, id: 2, token: "ytdlh_secret" } })
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("laptop")

    fireEvent.change(screen.getByPlaceholderText(/Name/), { target: { value: "short" } })
    fireEvent.click(screen.getByRole("button", { name: "90 days" }))
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(expect.any(String), { name: "short", expires_in_days: 90, scope: "mcp" })
    )
  })

  it("cannot create a token without a name", async () => {
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("laptop")

    expect((screen.getByRole("button", { name: "Create" }) as HTMLButtonElement).disabled).toBe(true)
  })

  it("revokes a token after confirmation", async () => {
    del.mockResolvedValue({})
    vi.spyOn(window, "confirm").mockReturnValue(true)
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("laptop")

    fireEvent.click(screen.getByTitle("Revoke laptop"))

    await waitFor(() => expect(del).toHaveBeenCalledWith(expect.stringMatching(/\/auth\/tokens\/1$/)))
  })

  it("labels each token with its scope", async () => {
    get.mockResolvedValue({ data: [TOKEN, { ...TOKEN, id: 2, name: "firefox", scope: "extension" }] })
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("firefox")

    expect(screen.getByText("MCP")).toBeDefined()
    expect(screen.getByText("extension")).toBeDefined()
  })

  it("creates a browser-extension token and shows only what the extension needs", async () => {
    post.mockResolvedValue({
      data: { ...TOKEN, id: 2, name: "firefox", scope: "extension", token: "ytdlh_secret" },
    })
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("laptop")

    fireEvent.click(screen.getByRole("radio", { name: "Browser extension" }))
    fireEvent.change(screen.getByPlaceholderText(/Name/), { target: { value: "firefox" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    expect(await screen.findByText("ytdlh_secret")).toBeDefined()
    expect(post).toHaveBeenCalledWith(expect.any(String), {
      name: "firefox",
      expires_in_days: null,
      scope: "extension",
    })
    expect(screen.getByText("Server URL")).toBeDefined()
    expect(screen.queryByText(/claude mcp add/)).toBeNull()
  })

  it("offers only extension tokens while MCP is disabled", async () => {
    post.mockResolvedValue({ data: { ...TOKEN, id: 2, scope: "extension", token: "ytdlh_secret" } })
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled={false} />)
    await screen.findByText("laptop")

    expect(screen.queryByRole("radiogroup", { name: "Token type" })).toBeNull()
    fireEvent.change(screen.getByPlaceholderText(/Name/), { target: { value: "firefox" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(expect.any(String), {
        name: "firefox",
        expires_in_days: null,
        scope: "extension",
      })
    )
  })

  it("does not revoke when the confirmation is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false)
    render(<AccessTokensDialog open onOpenChange={vi.fn()} mcpEnabled />)
    await screen.findByText("laptop")

    fireEvent.click(screen.getByTitle("Revoke laptop"))

    expect(del).not.toHaveBeenCalled()
  })
})
