/** @typedef {"chrome" | "firefox"} Target */

/** @type {readonly Target[]} */
export const TARGETS = ["chrome", "firefox"]

/**
 * The manifest one browser's build ships: the source manifest with the version stamped in and
 * the keys only the other browser reads removed, so neither store nor browser warns about them.
 *
 * @param {Record<string, any>} source
 * @param {Target} target
 * @param {string} version
 * @returns {Record<string, any>}
 */
export function targetManifest(source, target, version) {
  const { manifest_version, name, ...rest } = structuredClone(source)
  /** @type {Record<string, any>} */
  const manifest = { manifest_version, name, version, ...rest }
  if (target === "chrome") {
    delete manifest.browser_specific_settings
    delete manifest.background.scripts
  } else {
    delete manifest.background.service_worker
  }
  return manifest
}
