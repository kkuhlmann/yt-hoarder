import { cp, readFile, rm, writeFile } from "node:fs/promises"
import { basename, join } from "node:path"
import { fileURLToPath } from "node:url"
import { build } from "esbuild"
import { TARGETS, targetManifest } from "./manifest.mjs"
import { listFiles, writeZip } from "./zip.mjs"

const root = fileURLToPath(new URL("..", import.meta.url))
const src = join(root, "src")
const dist = join(root, "dist")

const pkg = JSON.parse(await readFile(join(root, "package.json"), "utf8"))
const sourceManifest = JSON.parse(await readFile(join(src, "manifest.json"), "utf8"))

const ENTRY_POINTS = {
  background: join(src, "background.ts"),
  popup: join(src, "popup", "popup.ts"),
  options: join(src, "options", "options.ts"),
}
const STATIC_FILES = ["popup/popup.html", "options/options.html", "ui.css"]

await rm(dist, { recursive: true, force: true })

for (const target of TARGETS) {
  const out = join(dist, target)
  // Unminified so AMO reviewers can read the shipped code against the source zip.
  await build({
    entryPoints: ENTRY_POINTS,
    outdir: out,
    bundle: true,
    format: "iife",
    target: ["chrome120", "firefox140"],
    logLevel: "warning",
  })
  for (const file of STATIC_FILES) await cp(join(src, file), join(out, basename(file)))
  await cp(join(src, "icons"), join(out, "icons"), { recursive: true })
  const manifest = targetManifest(sourceManifest, target, pkg.version)
  await writeFile(join(out, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`)
}

if (process.argv.includes("--package")) {
  for (const target of TARGETS) {
    const out = join(dist, target)
    const zipPath = join(root, "artifacts", `yt-hoarder-${target}-${pkg.version}.zip`)
    await writeZip(zipPath, out, await listFiles(out))
    console.log(`Wrote ${zipPath}`)
  }
}
