import { ext } from "./browser"
import { createHandler } from "./handler"
import { isExtensionRequest } from "./messages"
import { createSettingsStore } from "./settings"

const handle = createHandler({
  settings: createSettingsStore(ext.storage.local),
  hasPermission: (pattern) => ext.permissions.contains({ origins: [pattern] }),
  fetch: (input, init) => fetch(input, init),
})

ext.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== ext.runtime.id || !isExtensionRequest(message)) return false
  const failure = "Something went wrong in the extension. Try again."
  handle(message).then(sendResponse, () =>
    sendResponse(
      message.type === "listTasks"
        ? { ok: false, message: failure, fatal: false, retryAfterSeconds: null }
        : { tone: "error", message: failure }
    )
  )
  // Keeps the channel open for the async reply; both browsers support this form.
  return true
})
