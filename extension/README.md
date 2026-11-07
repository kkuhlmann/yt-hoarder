# YT-Hoarder browser extension

Sends the YouTube video, playlist or channel in the current tab to a YT-Hoarder server, as a
download or a subscription, and lists the progress of your tasks in the popup. One codebase builds for Chrome and Firefox (Manifest V3). Installing and
setting it up as a user is covered in [`docs/EXTENSION.md`](../docs/EXTENSION.md); this file is for
working on it.

Requires a YT-Hoarder server 0.3.0 or later, which introduced browser-extension tokens.

## Develop

Node 24 or later.

```sh
npm ci
npm run build        # dist/chrome and dist/firefox
npm test             # Vitest
npm run typecheck
npm run lint
npm run lint:firefox # web-ext lint on dist/firefox
```

Both builds hold the same JavaScript, HTML and icons. Only `manifest.json` differs: the Chrome
copy drops `background.scripts` and `browser_specific_settings`, and the Firefox copy drops
`background.service_worker` (`scripts/manifest.mjs`).

### Load it unpacked

- **Chrome:** `chrome://extensions` → enable *Developer mode* → *Load unpacked* → `dist/chrome`.
- **Firefox:** `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on…* → pick
  `dist/firefox/manifest.json`. It stays loaded until Firefox restarts.

Rebuild after a change, then reload the extension from the same page.

Against a dev stack (`task dev`), set the server URL to `http://localhost:3000` (the extension finds
the API on `:8000` of the same host, as the web app does) and create a
*Browser extension* token from the web app's account menu → *Access tokens*.

## Package

```sh
npm run package         # artifacts/yt-hoarder-{chrome,firefox}-<version>.zip
npm run package:source  # artifacts/yt-hoarder-source-<version>.zip, for AMO's source review
```

The version comes from `package.json`. Releases are tagged `extension-vX.Y.Z`, separately from the
app's `vX.Y.Z` tags.

### Rebuilding from the source zip (AMO reviewers)

Unzip, then run `npm ci && npm run build` with Node 24 or later. The output in `dist/firefox`
matches the submitted add-on; the bundles are not minified.
