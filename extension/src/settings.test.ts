import { describe, expect, it } from "vitest"
import { createSettingsStore, DEFAULT_PREFS } from "./settings"
import { fakeStorageArea, TOKEN } from "./testing"

describe("settings store", () => {
  it("is unconfigured until a connection test has found the API", async () => {
    const area = fakeStorageArea()
    const store = createSettingsStore(area)
    expect(await store.load()).toEqual({ serverUrl: "", apiBase: "", token: "" })
    expect(await store.isConfigured()).toBe(false)

    await store.save({ serverUrl: "https://h", token: TOKEN, apiBase: "" })
    expect(await store.isConfigured()).toBe(false)

    await store.save({ apiBase: "https://h/api" })
    expect(await store.isConfigured()).toBe(true)
    expect(await store.load()).toEqual({ serverUrl: "https://h", apiBase: "https://h/api", token: TOKEN })
  })

  it("methods work detached from the store", async () => {
    const { load, isConfigured } = createSettingsStore(fakeStorageArea({ apiBase: "a", token: TOKEN }))
    expect((await load()).token).toBe(TOKEN)
    expect(await isConfigured()).toBe(true)
  })

  it("prefs default, round-trip, and survive corrupt values", async () => {
    const area = fakeStorageArea()
    const store = createSettingsStore(area)
    expect(await store.loadPrefs()).toEqual(DEFAULT_PREFS)

    const prefs = {
      download: { mediaType: "AUDIO", transcribe: true, videoQuality: "720P", audioQuality: "96K" },
      subscribe: { mediaType: "TRANSCRIPT", transcribe: false, videoQuality: "BEST", audioQuality: "48K" },
      lastTab: "subscribe",
    } as const
    await store.savePrefs(prefs)
    expect(await store.loadPrefs()).toEqual(prefs)

    area.data.set("prefs", {
      download: { mediaType: "MP3", transcribe: "yes", videoQuality: "4K", audioQuality: 128 },
      subscribe: null,
      lastTab: "settings",
    })
    expect(await store.loadPrefs()).toEqual(DEFAULT_PREFS)
  })

  it("prefs saved before quality was remembered keep their choices and start at Best", async () => {
    const area = fakeStorageArea({ prefs: { download: { mediaType: "AUDIO", transcribe: true } } })

    const { download } = await createSettingsStore(area).loadPrefs()

    expect(download).toEqual({ mediaType: "AUDIO", transcribe: true, videoQuality: "BEST", audioQuality: "BEST" })
  })
})
