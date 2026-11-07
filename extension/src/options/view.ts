import {
  isPlainHttp,
  normalizeServerUrl,
  originPattern,
  TOKEN_PREFIX,
  type Outcome,
} from "../api"
import type { ExtensionRequest } from "../messages"
import type { Settings } from "../settings"

export type OptionsDeps = {
  load(): Promise<Settings>
  save(update: Partial<Settings>): Promise<void>
  requestPermission(pattern: string): Promise<boolean>
  removePermission(pattern: string): Promise<unknown>
  send(message: ExtensionRequest): Promise<Outcome | undefined>
}

const NO_REPLY: Outcome = {
  tone: "error",
  message: "The extension's background script didn't answer. Try again.",
}

function byId<T extends HTMLElement = HTMLElement>(doc: Document, id: string): T {
  const element = doc.getElementById(id)
  if (!element) throw new Error(`Missing #${id}`)
  return element as T
}

export async function initOptions(doc: Document, deps: OptionsDeps): Promise<void> {
  const form = byId<HTMLFormElement>(doc, "settings-form")
  const serverInput = byId<HTMLInputElement>(doc, "server-url")
  const tokenInput = byId<HTMLInputElement>(doc, "token")
  const toggle = byId<HTMLButtonElement>(doc, "toggle-token")
  const warning = byId(doc, "http-warning")
  const saveButton = byId<HTMLButtonElement>(doc, "save")
  const status = byId(doc, "status")

  const show = (outcome: Outcome) => {
    status.textContent = outcome.message
    status.dataset.tone = outcome.tone
  }

  const saved = await deps.load()
  serverInput.value = saved.serverUrl
  tokenInput.value = saved.token

  const updateWarning = () => {
    warning.hidden = !isPlainHttp(serverInput.value)
  }
  updateWarning()
  serverInput.addEventListener("input", updateWarning)

  toggle.addEventListener("click", () => {
    const revealing = tokenInput.type === "password"
    tokenInput.type = revealing ? "text" : "password"
    toggle.textContent = revealing ? "Hide" : "Show"
    toggle.setAttribute("aria-pressed", String(revealing))
  })

  async function finishSave(granted: Promise<boolean>, serverUrl: string, token: string) {
    saveButton.disabled = true
    show({ tone: "info", message: "Testing the connection…" })
    try {
      if (!(await granted)) {
        show({
          tone: "error",
          message: `Permission to contact ${new URL(serverUrl).origin} wasn't granted, so the extension can't reach your server.`,
        })
        return
      }
      const previous = saved.serverUrl
      await deps.save({ serverUrl, token, apiBase: "" })
      saved.serverUrl = serverUrl
      serverInput.value = serverUrl
      if (previous && originPattern(previous) !== originPattern(serverUrl)) {
        await deps.removePermission(originPattern(previous)).catch(() => undefined)
      }
      show((await deps.send({ type: "testConnection" })) ?? NO_REPLY)
    } catch {
      show(NO_REPLY)
    } finally {
      saveButton.disabled = false
    }
  }

  function save() {
    const server = normalizeServerUrl(serverInput.value)
    if (!server.ok) {
      show({ tone: "error", message: server.message })
      return
    }
    const token = tokenInput.value.trim()
    if (!token.startsWith(TOKEN_PREFIX)) {
      show({
        tone: "error",
        message: `That isn't a YT-Hoarder access token: those start with ${TOKEN_PREFIX}.`,
      })
      return
    }
    // Firefox only grants a permission request made while the click is still being handled, so
    // the request has to be issued before anything here awaits.
    const granted = deps.requestPermission(originPattern(server.url))
    void finishSave(granted, server.url, token)
  }

  form.addEventListener("submit", (event) => event.preventDefault())
  saveButton.addEventListener("click", (event) => {
    event.preventDefault()
    save()
  })
}
