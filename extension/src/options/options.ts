import { ext } from "../browser"
import { createSettingsStore } from "../settings"
import { initOptions } from "./view"

const store = createSettingsStore(ext.storage.local)

void initOptions(document, {
  load: store.load,
  save: store.save,
  requestPermission: (pattern) => ext.permissions.request({ origins: [pattern] }),
  removePermission: (pattern) => ext.permissions.remove({ origins: [pattern] }),
  send: (message) => ext.runtime.sendMessage(message),
})
