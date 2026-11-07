import { readFile } from "node:fs/promises"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { listFiles, writeZip } from "./zip.mjs"

const root = fileURLToPath(new URL("..", import.meta.url))
const pkg = JSON.parse(await readFile(join(root, "package.json"), "utf8"))

const EXCLUDED = new Set(["node_modules", "dist", "artifacts", ".DS_Store"])
const zipPath = join(root, "artifacts", `yt-hoarder-source-${pkg.version}.zip`)
await writeZip(zipPath, root, await listFiles(root, EXCLUDED))
console.log(`Wrote ${zipPath}`)
