import { describe, expect, it } from "vitest"
import source from "../src/manifest.json"
import { TARGETS, targetManifest } from "./manifest.mjs"

describe("targetManifest", () => {
  it("stamps the version and keeps the source untouched", () => {
    const before = structuredClone(source)
    for (const target of TARGETS) {
      const manifest = targetManifest(source, target, "1.2.3")
      expect(manifest.version).toBe("1.2.3")
      expect(Object.keys(manifest).slice(0, 3)).toEqual(["manifest_version", "name", "version"])
    }
    expect(source).toEqual(before)
  })

  it("Chrome gets the service worker and no Firefox settings", () => {
    const manifest = targetManifest(source, "chrome", "1.0.0")
    expect(manifest.background).toEqual({ service_worker: "background.js" })
    expect(manifest).not.toHaveProperty("browser_specific_settings")
  })

  it("Firefox gets background scripts and its add-on settings", () => {
    const manifest = targetManifest(source, "firefox", "1.0.0")
    expect(manifest.background).toEqual({ scripts: ["background.js"] })
    expect(manifest.browser_specific_settings.gecko.id).toBe("yt-hoarder@kkuhlmann.github.io")
  })

  it("both targets share everything else", () => {
    const strip = (manifest: Record<string, unknown>) => {
      const rest = { ...manifest }
      delete rest.background
      delete rest.browser_specific_settings
      return rest
    }
    expect(strip(targetManifest(source, "chrome", "1.0.0"))).toEqual(
      strip(targetManifest(source, "firefox", "1.0.0"))
    )
  })

  it("asks only for what the popup needs up front, and server access at runtime", () => {
    expect(source.permissions).toEqual(["storage", "activeTab"])
    expect(source).not.toHaveProperty("host_permissions")
    expect(source.optional_host_permissions).toEqual(["http://*/*", "https://*/*"])
  })
})
