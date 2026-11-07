import { mkdir, readdir, readFile, writeFile } from "node:fs/promises"
import { dirname, join, relative, sep } from "node:path"
import { zipSync } from "fflate"

// A fixed timestamp keeps an archive byte-identical across rebuilds of the same source, so AMO's
// reviewers can rebuild from the source zip and compare.
const FIXED_MTIME = new Date("2026-01-01T00:00:00Z")

/**
 * Every file under `dir`, sorted, skipping any file or directory whose name is in `excluded`.
 *
 * @param {string} dir
 * @param {ReadonlySet<string>} [excluded]
 * @returns {Promise<string[]>}
 */
export async function listFiles(dir, excluded = new Set()) {
  const entries = await readdir(dir, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    if (excluded.has(entry.name)) continue
    const path = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...(await listFiles(path, excluded)))
    else if (entry.isFile()) files.push(path)
  }
  return files.sort()
}

/**
 * @param {string} zipPath
 * @param {string} rootDir
 * @param {string[]} files
 */
export async function writeZip(zipPath, rootDir, files) {
  /** @type {Record<string, [Uint8Array, { mtime: Date }]>} */
  const entries = {}
  for (const file of files) {
    const name = relative(rootDir, file).split(sep).join("/")
    entries[name] = [await readFile(file), { mtime: FIXED_MTIME }]
  }
  await mkdir(dirname(zipPath), { recursive: true })
  await writeFile(zipPath, zipSync(entries, { level: 9 }))
}
