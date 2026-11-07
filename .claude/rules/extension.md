---
paths:
  - "extension/**/*"
  - ".github/workflows/extension*.yml"
---

# Browser extension (`extension/`)

Root `AGENTS.md` has the overview. The server side (token scopes, the REST bearer path) is in
`mcp.md`. User docs are in `docs/EXTENSION.md`, dev commands in `extension/README.md`.

## Layout

| File | Role |
|---|---|
| `src/background.ts` + `src/handler.ts` | The only code that fetches. The handler takes its settings store, permission check and `fetch` as arguments, so tests run against fakes. |
| `src/api.ts` | Request bodies, `sendRequest`, finding the API base (`resolveConnection`), and turning every reply into one sentence. |
| `src/youtube.ts` | Page URL → `{video, playlist, channel}`. |
| `src/choices.ts` | The app forms' quality lists, and the conversions they apply (minutes → seconds, a date → that day's local midnight in ISO). |
| `src/popup/tasks.ts` | The popup's task list: the polling loop and the rows. Takes `listTasks` and `now` as arguments, like the rest. |
| `src/popup/view.ts`, `src/options/view.ts` | Page logic. `popup.ts` / `options.ts` are thin entry points, the only modules besides `background.ts` that touch the browser API (`browser.ts`). |
| `scripts/` | esbuild IIFE build, the per-browser manifest transform, zips. |

## Constraints

**Network and messaging**
- **Every request goes through the background script**, with `credentials: 'omit'` and
  `redirect: 'manual'`.
  - Background fetches under a granted host permission bypass CORS. That matters because prod
    registers no CORS middleware at all.
  - `omit` keeps the web app's session cookie off the request. Beside a token the server ignores it,
    but a request whose token went missing would quietly authenticate as whoever is signed in.
  - `manual`, because a redirect to another origin (http → https) drops `Authorization` and turns a
    wrong URL into a misleading 401. The reply is reported as a redirect instead.
- **Messages are answered with `sendResponse` + `return true`**, the form both browsers accept. The
  listener ignores messages whose `sender.id` isn't the extension's own.

**Permissions**
- **`permissions.request` must be called before any `await` in the click handler.** Firefox grants it
  only while the user-input handler is still running, so awaiting anything first rejects the request.
  `options/view.ts` issues it synchronously and awaits the promise later; `options/view.test.ts` pins
  the order.
- **Host permissions are optional and requested per server origin, without a port**
  (`originPattern`). Firefox rejects match patterns that carry a port, and a port-less pattern
  matches every port in both browsers. There is no static host permission: `activeTab` gives the
  popup the tab's URL when the user clicks the icon, so nothing on youtube.com is requested.

**URL rules**
- **`youtube.ts` mirrors `backend/app/ytdlp/urls.py` exactly**, quirks included: `replace` strips
  every `www.` and `m.`, and `parse_qs` drops blank values.
- **The popup offers Subscribe only for what `ADD_SUBSCRIPTION_JOB` accepts without its yt-dlp
  fallback**: a channel path, or any `list=`. A subscription that job rejects fails only in the
  server log.
- **Change both sides together.** `youtube.test.ts` reuses `tests/test_url_utils.py`'s examples.

**The task list (`popup/tasks.ts`)**
- **It polls; it can't stream.** The server's SSE endpoint is cookie-only and `EventSource` can't send
  a Bearer header. The background script can't hold a stream either: Chrome's MV3 service worker is
  killed when idle. The route is `GET /extension/tasks` (`mcp.md`).
- **The popup is the only thing that polls, and only while open.** There is no `alarms` or
  `notifications` permission and no toolbar badge: adding either changes the permission prompt
  (`manifest.test.ts` pins the list) and, on Firefox, the data-consent text. Closing the popup
  destroys its page, so no timer outlives it; `pagehide` and `visibilitychange` are handled anyway.
- **The fast cadence is only for work that moves soon** (resolving, queued, running). RETRY and
  NOT_READY are active too, but wait hours or days, so a list holding only those polls at the idle
  cadence.
- **One request in flight.** A `refresh()` that lands mid-request is remembered and answered right
  after, which is how a just-submitted download shows up without waiting for the next tick.
- **A fatal reply stops polling for good** (a refused token, a 403, a 404, a redirect, a page that
  isn't JSON). That is load-bearing: every refused token counts against the server's per-address
  limit on failed sign-ins (`EXTENSION_AUTH_FAIL_LIMITER`), so a loop would lock out everyone behind
  that address. Only 429 and 5xx, and network failures, are retried, with backoff
  `max(Retry-After, min(60 s, 3 s · 2^n))`.
- **`describeTasks` treats the reply as untrusted.** Unknown statuses and task types are dropped,
  numbers are clamped, strings are capped and token-redacted, and rows are built with `textContent`
  only. A server newer than the extension may add statuses; they vanish rather than break the list.
- **The handler's reply type depends on the message** (`Handler` overloads): `listTasks` answers a
  `TaskFeed`, everything else an `Outcome`. The background's last-resort failure reply is shaped
  to match, and `isTaskFeed` guards the popup against any other shape.

**The popup's options**
- **They mirror the web app's download and subscription forms** (`DownloadButton.tsx`,
  `AddSubscriptionButton.tsx`), down to the encodings. `date_filter` is the chosen day's *local*
  midnight via `toISOString()`, which is what `DateSelection` sends. Durations are minutes ×60,
  rounded. Each type shows only the quality it uses. A new option in those forms belongs here too.
- **Overwrite is offered on Download only**, so it lives on `DownloadRequest`, not `MediaOptions`.
  The server 403s it on a subscription from a token (`mcp.md`).
- **Only type, transcribe and the two qualities are remembered** (`SectionPrefs`). Overwrite and
  the subscription filters reset on every open, because a remembered Overwrite would quietly replace
  files on later downloads.
- **Download and Subscribe are tabs only when the page offers both** (`bindTabs` in `popup/view.ts`);
  a single-action page shows its panel with no tab bar. Availability is decided first by un-hiding
  the panel, and the tab logic then hides the unselected one, so tests assert `hidden` after that.
  `Prefs.lastTab` is the one remembered value outside `SectionPrefs`. The subscription filters live
  in a collapsed `<details>`; a validation error opens it so the message isn't about hidden fields.
- **Length boxes are `type="number"`**, and a browser reports typed garbage there as an empty
  value. `lengthSeconds` therefore checks `validity.badInput`, or a mistyped length would go out as
  no filter.

**Finding the API base**
- **Prod serves the API under `/api`, dev at the root**, and users paste either the app's URL or the
  API base. `resolveConnection` probes `{url}/api/extension/me`, then `{url}/extension/me` (the other
  way round when the URL already ends in `/api`).
- **A probe must get back a JSON identity body**, not just a 200: the SPA catch-all answers any
  non-`/api` path with the HTML shell and a 200.
- **A `:3000` address also tries `:8000` of the same host**, last. In dev mode that address is the
  Next dev server, which serves no API and 308s every path to add a trailing slash
  (`trailingSlash: true`). The web app itself reaches the API on `:8000` (`frontend/app/lib/api.ts`),
  and the port-less permission pattern already covers it. Keep `DEV_API_PORT` in step with
  `api.ts`'s `DEV_API_PORT`.
- **Both probes 404ing as JSON means a server that predates extension tokens.** The unauthenticated
  `/ytdl/version` then names its version for the error message. Raise `MIN_SERVER_VERSION` only when
  the API contract the extension relies on changes.

**The token**
- **It lives in `storage.local` only.** `storage.sync` would copy it into the user's browser account.
- **It never reaches a message, an error or the console:**
  - ESLint's `no-console` allows only `warn` and `error`.
  - Server text passes through `redactTokens` before display.
  - `api.test.ts` asserts that no output contains the token.

**Builds and releases**
- **One manifest source, two builds** (`scripts/manifest.mjs`). JS, HTML and icons are
  byte-identical across them.
  - The Chrome build drops `background.scripts` and `browser_specific_settings`.
  - The Firefox build drops `background.service_worker`; Firefox has no MV3 background service
    worker.
  - `web-ext lint` runs on the Firefox build only. On the Chrome build it flags the
    `service_worker` key by design.
- **The gecko settings are deliberate:**
  - `strict_min_version` is 140 (142 on Android). `optional_host_permissions` needs 128, and the
    built-in data-consent UI that `data_collection_permissions` relies on needs 140.
  - `data_collection_permissions` is `required: ["browsingActivity"]`: page URLs leave the browser,
    and AMO lists no exemption for a server the user hosts.
  - The gecko `id` is permanent once a version is on AMO.
- **TypeScript is capped at 6.x**, for the same typescript-eslint reason as the frontend
  (`frontend.md`). Dependabot ignores its majors.
- **Bundles aren't minified, and every release carries a source zip** (`npm run package:source`).
  AMO requires source for any bundled or transpiled add-on; `extension/README.md` holds the
  reviewer's build steps.
- **Releases are `extension-v*` tags** (`extension-release.yml`):
  - The tag is checked against `package.json`'s version.
  - The release is created with `--latest=false`, so the app keeps the "Latest" badge.
  - Its notes start at the previous `extension-v*` release.

  `extension.yml` is path-filtered, so it must never become a required status check.

## Testing

- **Vitest** runs with `environment: "node"`. DOM tests opt in with `// @vitest-environment jsdom`
  on their first line, as in the frontend.
- **No `chrome` global is mocked.** Modules take their browser APIs as arguments, and `src/testing.ts`
  holds the shared fakes: canned replies and the test token.
