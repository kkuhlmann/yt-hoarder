// @vitest-environment jsdom
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Settings } from "../settings"
import { TOKEN } from "../testing"
import { initOptions, type OptionsDeps } from "./view"

const HTML = readFileSync(join(import.meta.dirname, "options.html"), "utf8")

function render() {
  const parsed = new DOMParser().parseFromString(HTML, "text/html")
  document.body.innerHTML = parsed.body.innerHTML
}

function deps(stored: Partial<Settings> = {}, overrides: Partial<OptionsDeps> = {}) {
  const settings: Settings = { serverUrl: "", apiBase: "", token: "", ...stored }
  const value = {
    load: vi.fn(async () => ({ ...settings })),
    save: vi.fn(async (update: Partial<Settings>) => {
      Object.assign(settings, update)
    }),
    requestPermission: vi.fn(async () => true),
    removePermission: vi.fn(async () => true),
    send: vi.fn(async () => ({ tone: "success" as const, message: "Connected as kim" })),
    ...overrides,
  }
  return { deps: value, settings }
}

const input = (id: string) => document.getElementById(id) as HTMLInputElement
const el = (id: string) => document.getElementById(id) as HTMLElement

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

function fill(server: string, token: string) {
  input("server-url").value = server
  input("server-url").dispatchEvent(new Event("input"))
  input("token").value = token
}

beforeEach(render)
afterEach(() => {
  document.body.innerHTML = ""
  vi.restoreAllMocks()
})

describe("saving", () => {
  it("requests the host permission synchronously inside the click", async () => {
    const { deps: d } = deps()
    await initOptions(document, d)
    fill("https://hoarder.example.com/", TOKEN)

    el("save").click()
    // Nothing has been awaited since the click: the request must already have been made.
    expect(d.requestPermission).toHaveBeenCalledWith("https://hoarder.example.com/*")
    expect(d.save).not.toHaveBeenCalled()

    await settle()
    expect(d.save).toHaveBeenCalledWith({ serverUrl: "https://hoarder.example.com", token: TOKEN, apiBase: "" })
    expect(d.send).toHaveBeenCalledWith({ type: "testConnection" })
    expect(el("status").textContent).toBe("Connected as kim")
    expect(el("status").dataset.tone).toBe("success")
    expect(input("server-url").value).toBe("https://hoarder.example.com")
  })

  it("asks for the host without its port", async () => {
    const { deps: d } = deps()
    await initOptions(document, d)
    fill("http://localhost:8000", TOKEN)
    el("save").click()
    expect(d.requestPermission).toHaveBeenCalledWith("http://localhost/*")
  })

  it("rejects an address without a scheme before asking for anything", async () => {
    const { deps: d } = deps()
    await initOptions(document, d)
    fill("hoarder.local", TOKEN)
    el("save").click()
    await settle()
    expect(d.requestPermission).not.toHaveBeenCalled()
    expect(el("status").dataset.tone).toBe("error")
  })

  it("rejects something that isn't a token", async () => {
    const { deps: d } = deps()
    await initOptions(document, d)
    fill("https://h", "hunter2")
    el("save").click()
    await settle()
    expect(d.requestPermission).not.toHaveBeenCalled()
    expect(el("status").textContent).toBe("That isn't a YT-Hoarder access token: those start with ytdlh_.")
  })

  it("stops when the permission is refused", async () => {
    const { deps: d } = deps({}, { requestPermission: vi.fn(async () => false) })
    await initOptions(document, d)
    fill("https://h", TOKEN)
    el("save").click()
    await settle()
    expect(d.save).not.toHaveBeenCalled()
    expect(el("status").textContent).toMatch(/wasn't granted/)
  })

  it("gives back the old server's permission after moving to a new one", async () => {
    const { deps: d } = deps({ serverUrl: "https://old.example.com", token: TOKEN })
    await initOptions(document, d)
    fill("https://new.example.com", TOKEN)
    el("save").click()
    await settle()
    expect(d.removePermission).toHaveBeenCalledWith("https://old.example.com/*")
  })

  it("keeps the permission when only the path changes", async () => {
    const { deps: d } = deps({ serverUrl: "https://h", token: TOKEN })
    await initOptions(document, d)
    fill("https://h/api", TOKEN)
    el("save").click()
    await settle()
    expect(d.removePermission).not.toHaveBeenCalled()
  })
})

describe("the form", () => {
  it("prefills what was saved and keeps the token masked", async () => {
    await initOptions(document, deps({ serverUrl: "https://h", token: TOKEN }).deps)
    expect(input("server-url").value).toBe("https://h")
    expect(input("token").value).toBe(TOKEN)
    expect(input("token").type).toBe("password")

    el("toggle-token").click()
    expect(input("token").type).toBe("text")
    expect(el("toggle-token").getAttribute("aria-pressed")).toBe("true")
    el("toggle-token").click()
    expect(input("token").type).toBe("password")
  })

  it("warns about plain HTTP", async () => {
    await initOptions(document, deps().deps)
    fill("http://nas:8000", TOKEN)
    expect(el("http-warning").hidden).toBe(false)
    fill("https://nas", TOKEN)
    expect(el("http-warning").hidden).toBe(true)
  })
})
