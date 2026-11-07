import { ext } from "../browser"
import { isTaskFeed } from "../messages"
import { createSettingsStore } from "../settings"
import { initPopup } from "./view"

const store = createSettingsStore(ext.storage.local)

void initPopup(document, {
  async activeTabUrl() {
    const [tab] = await ext.tabs.query({ active: true, currentWindow: true })
    return tab?.url
  },
  isConfigured: store.isConfigured,
  loadPrefs: store.loadPrefs,
  savePrefs: store.savePrefs,
  send: (message) => ext.runtime.sendMessage(message),
  async listTasks() {
    const reply: unknown = await ext.runtime.sendMessage({ type: "listTasks" })
    return isTaskFeed(reply) ? reply : undefined
  },
  openOptions() {
    void ext.runtime.openOptionsPage()
    window.close()
  },
})
