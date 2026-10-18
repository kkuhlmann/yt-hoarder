---
paths:
  - "frontend/**/*"
---

# Frontend (`frontend/app/`)

## Toolchain — upgraded July 2026; the non-obvious parts
- **Turbopack is the builder** for both `next dev` and `next build`. `--turbopack` is the default in 16, not a flag.
- **Lint is flat config only**: `eslint.config.mjs` + `"lint": "eslint ."`. `next lint` was removed in Next 16 and `.eslintrc.json` is gone — don't reintroduce either.
- **`noUnusedLocals` + `noUnusedParameters` are on**, and `next build` type-checks, so an unused import is a *build failure*, not editor noise.
- **`tsconfig.json` pins `"target": "ES2022"` with an explicit `"useDefineForClassFields": false`.** The second flag looks redundant and is not: Next passes `Boolean(compilerOptions.useDefineForClassFields)` straight to SWC and never derives it from `target`, so a bare `ES2022` would leave tsc assuming define-semantics the emitter doesn't produce. Leave it.
- **Node 24** is the pinned build version in both `frontend/Dockerfile` and `Dockerfile.prod`'s frontend-builder.
- **TypeScript is capped at 6.x, and that is a ceiling rather than a stale pin.** 6.0 is the last release with a JavaScript compiler API; 7.0 is the Go port, and its package exports only `{ version, versionMajorMinor }` — `lib/typescript.js` is gone. `typescript-eslint` (transitively, via `eslint-config-next`) reads `versionMajorMinor` at module load and throws on major ≥ 7, so `npm run lint` cannot start. `tsc --noEmit`, Vitest and `next build` all pass on 7.0 — lint alone is the blocker, which is why the failure looks narrower than it is. `.github/dependabot.yml` therefore ignores typescript majors. The documented `@typescript/typescript6` shim does work, but it costs `experimental.useTypeScriptCli: false` in `next.config.js` (Next 16 defaults that to true and needs a real `typescript/bin/tsc`; the shim ships `bin/tsc6`), and the build then type-checks through the TS 6 API anyway — so it buys a faster `tsc` step and nothing else. Neither 7.0 nor the shim ships `lib/tsserver.js`, so both break an editor's "Use Workspace Version".
- **Next 16 enforces `allowedDevOrigins`** (advisory in 15): cross-origin requests to `/_next/*` and HMR are blocked unless the host is listed. Same-origin page loads send no `Origin` and are never blocked, so in practice this governs **hot reload**. `next.config.js` ships `["**.*"]`, the widest pattern Next's matcher accepts — every IPv4 literal and every dotted name. **A bare `"*"` or `"**"` matches nothing**: `matchWildcardDomain` (`next/dist/server/app-render/csrf-protection`) rejects a single-segment wildcard outright, so "simplifying" the pattern silently blocks everything. Nothing can match a single-label host (`http://nas:3000`) or an IPv6 literal — that is what `ALLOWED_DEV_ORIGINS` (comma-separated) remains for. `localhost` is always allowed by Next itself. Prod is a static export and unaffected.
- **Tailwind is v4 and CSS-first — there is no `tailwind.config.ts`.** The theme lives in an `@theme inline` block at the top of `app/globals.css`; `components.json` carries `"config": ""` to say so. Four things there are load-bearing and easy to undo by accident:
  - **`inline` is mandatory.** It makes each utility emit the *runtime* variable (`.bg-matrix` → `background-color: var(--matrix-green)`) instead of a copy resolved once at `:root`, which is the only reason the 92 `[data-theme]` blocks can still repaint the app. Note `inline` does **not** suppress the `:root` copy of a theme variable — it only changes what utilities reference.
  - **Theme keys must not share a name with a runtime var.** Identical names emit `--x: var(--x)` at `:root`. The theme blocks' shadow vars are called `--glow*` purely so the `--shadow-glow*` keys have something distinct to point at. No `--font-sans`/`--font-mono` keys exist for the same reason — v4 defines both by default and the unlayered theme blocks already override them.
  - **The radius scale is deliberately non-monotonic** (`--radius-xs` > `--radius-sm`). v4 shifted every rounding class one step down (`rounded` → `rounded-sm`), so the values shift one step up to cancel it out and keep the v3 pixels. Don't "fix" it without re-checking 58 call sites across all 92 themes.
  - **v4 emits real `@layer` at-rules; v3 did not.** Unlayered CSS now outranks every Tailwind utility regardless of specificity. The theme blocks, the `.rdp-*`/`.slider-matrix` overrides and the mobile-zoom rule at the bottom of `globals.css` all depend on staying unlayered — moving any of them into a `@layer` silently breaks it. This is also why that mobile rule no longer needs `!important`.
- **`@source` paths in `globals.css` are relative to that file**, i.e. to `frontend/app/` — so it is `../components`, not `../../components`. Combined with `source(none)` they replace v4's automatic detection, which would otherwise walk up to the repo root and scan `backend/`. Get one wrong and those files' classes are silently dropped from the build.

## Components with constraints worth knowing (the rest are named for what they do)
- `AuthGuard.tsx` gates on `must_change_password` → `ForcePasswordChange.tsx`, mirroring the server-side gate. `LoginPage` swaps its card body between sign-in and the two recovery panels in `auth/`
- `auth/ChangePasswordDialog.tsx` is opened from the `KeyIcon` in `NavigationBar`, **not** Settings — that tab is `adminOnly` and every `/settings` endpoint requires `get_admin_user_id`, so a non-admin would never reach it. Shares `auth/changePassword.ts` with `ForcePasswordChange`
- `auth/AccessTokensDialog.tsx` (MCP personal access tokens) lives in the account menu for the same reason, and only when `/auth/me` reports `mcp_enabled`. `MobileNavMenu` takes its handler as an *optional* prop and renders the entry only when it is passed — the safe form of the `onClip` trap below. The plaintext token is cleared on close so reopening can never show it again
- **Deep links `/?media=<id>&t=<s>`** (what MCP results hand to agents) are parsed once in `page.tsx`'s `useState` initializer, stripped from the address bar on mount, and played by `useDeepLinkPlayback` inside `DownloadsCard`, because the inline `VideoPlayer` only exists there. A query on `/` rather than a path because the static export has no router; it survives sign-in because `AuthGuard` never navigates. An aborted run (StrictMode's double mount) must not mark the link handled, or the second mount has nothing to play
- `media/MediaClipEditor.tsx` is the single surface behind every scissors action (`media/actions.tsx`), dispatching on `media_type`. Note `MediaListView`'s `onClip` is optional and the scissors button renders regardless, so a new list surface that forgets to pass it gets a **silently dead button**
- `TagMixView.tsx` plays through `MediaPlayerContext` under the sentinel `TAG_MIX_PLAYLIST_ID`, and renders through the same `MediaListView`/`media/columns.tsx` as the library, so mix rows get the library's actions for free

## Hooks

`useFetchEffect` is the project's data-fetching effect, registered in `eslint.config.mjs`
`additionalHooks` so exhaustive-deps checks it. **There are two hook directories**:
`hooks/useTaskProgress.ts` (SSE, auto-reconnect with backoff) lives in `app/hooks/`, everything else
in `app/_hooks/`.

**`useFetchEffect`'s `pollMs` runs are quiet: they never raise `isLoading`.** A poll tick refreshes
data already on screen, so announcing it blanks a populated list every interval — and on a slow
connection every fetch outlives `useDelayedFlag`'s 500ms grace, so the spinner is up more than it is
down. Mount, a `deps` change and `refetch()` all stay loud, because `refetch` is only ever called
from a user action (the Refresh button, or the reload after a delete/tag/retry). That is why no list
surface needs its own `rows.length === 0` guard around the spinner — `isLoading` already means "the
user asked for this".

`useAudioAnalyser.ts` - Feeds the visualizer's `AnalyserNode` by routing the real `<audio>` element through `ctx.createMediaElementSource(el)` (per-element WeakMap-cached graph, since that call may run only ONCE per element; a parallel strong `liveGraphs` list closes contexts whose element has left the DOM, bounding live AudioContexts over a long session). **Desktop-only by design, and that gate IS the iOS protection:** `createMediaElementSource` reroutes the element's native output into the AudioContext, and on iOS the browser suspends that context on screen-lock → **silences lock-screen/background audio**, with no way to un-route the element. So `getOrCreateGraph` calls `isDesktop()` (UA-based iOS/iPadOS detection + `(pointer: coarse)`) and returns null on any iOS or touch device — no AudioContext is ever created there, the `<audio>` tag stays plain and untouched, and the visualizer simply stays inert. Tapping the element via `captureStream()` → `createMediaStreamSource` looks like a way to avoid the reroute and keep the graph everywhere; it is not, because WebKit doesn't implement `captureStream` — that route is desktop-only too, minus the explicit gate. **CORS:** the real `<audio>` still needs `crossOrigin="use-credentials"` (`MediaPlayer.tsx`) or a tainted cross-origin element yields zeroed frequency data. `ensureStarted()` (called from the visualizer toggle click) creates/resumes the graph inside a user gesture — outside one the context stays suspended and the audio routes into a silent graph; a `visibilitychange` + `pointerdown`/`touchend` effect resumes it after the browser auto-suspends on tab background. `enabled`/`isPlaying` are mirrored into refs from a **commit-phase effect**, not during render, so the rAF-driven `getBars`/`isActive` read fresh values without re-creating the memoized handle.

## Testing

Frontend tests run via **Vitest** — `task frontend:test`, or `npm test` from `frontend/`. Tests
are **co-located** next to their source as `*.test.ts` / `*.test.tsx`.

- **`environment: "node"` is the default** (`vitest.config.ts`); a file that touches the DOM opts
  in per-file with a `// @vitest-environment jsdom` docblock as its first line.
  `environmentMatchGlobs` was removed in Vitest 4, and the current file count doesn't justify a
  `projects` config.
- **Globals are off** — every test imports `describe`/`it`/`expect`/`vi`/etc. explicitly from
  `"vitest"`. That is what keeps `tsconfig.json` untouched (no `"types": ["vitest/globals"]`
  needed). Test files still sit under `tsconfig.json`'s `**/*.ts(x)` include glob like every other
  file, so they're checked by the same `next build` type-check as app code — a type error in a
  test fails the build exactly like one anywhere else.
- **RTL's automatic `cleanup()` never registers with globals off** — its `afterEach` guard probes
  for a *global* `afterEach`, which doesn't exist here. Every jsdom test file must call
  `cleanup()` from its own explicit `afterEach`, or DOM/mounted state leaks into the next test in
  that file.
- **Never assert exact output of locale-dependent formatting** (anything through
  `toLocaleString`) — CI's ICU/locale/TZ differs from a dev machine's. Assert shape with a regex
  instead, e.g. `/^in 4m \(.+\)$/`.
- **The anonymous `node_modules` volume trap:** `docker-compose.dev.yml` mounts `./frontend:/app`
  plus an anonymous `/app/node_modules` volume that masks the host directory, so after editing
  `package.json` the container keeps the old `node_modules` (`vitest: not found`) until `task
  frontend:install` runs `npm install` into the volume — the lockfile writes back through the
  bind mount, `node_modules` doesn't.

## Offline mode and the service worker

Offline playback is a service worker (`sw/sw.ts`) plus an IndexedDB store
(`app/lib/offlineDb.ts`). The parts that are load-bearing:

- **`sw/` is excluded from the root `tsconfig.json` and type-checked by its own
  `sw/tsconfig.json`** (`npm run typecheck:sw`, chained off `build`). That config uses
  `lib: ["ES2022", "WebWorker"]` — WebWorker *replaces* DOM rather than joining it, since the two
  redeclare the same globals and including both is a wall of duplicate-identifier errors. The
  consequence is a real constraint: **everything `sw.ts` imports must be free of
  `window`/`document`/`localStorage`.** `offlineRange.ts`, `offlineDb.ts` and `offlineRoutes.ts` are
  written that way, and the tsconfig's `include` is an explicit list rather than a glob so a new
  page-side module can't be dragged in by accident. Adding a `document` reference to any of the three
  fails `typecheck:sw` with a clear error — the guard is not decorative.
- **Offline support is gated three ways** (`app/_hooks/useOfflineSupported.ts`): a production build,
  service-worker support, and a secure context. Every offline control consults it and renders nothing
  when it is false, so the affordance is never offered where it would fail later. The production
  clause is the non-obvious one — without it the controls appear on `http://localhost:3000`, which
  *is* a secure context, while `ServiceWorkerRegistrar` has deliberately skipped registering there.
  Downloads would fill IndexedDB and never play back.
- **`out/sw.js` is generated by `scripts/build-sw.mjs`, wired as npm's `postbuild`.** It esbuild-bundles
  the worker and injects the precache list plus a build hash. Emitting into `out/` rather than
  `public/` is what keeps a generated file out of the source tree *and* keeps the worker away from
  `next dev`, where it would cache the dev server's output and break HMR. Because it hangs off
  `postbuild`, `Dockerfile.prod`'s `RUN npm run build` picks it up with no Dockerfile change.
- **`cache.addAll()` is atomic: one unfetchable entry rejects the whole install and the worker never
  activates**, silently. If offline support stops working after a build change, check that every
  injected precache URL is actually served before looking anywhere else.
- **Media cannot go through the Cache API.** `cache.put()` rejects a non-200, and
  `media.py`'s `_build_stream_response` answers every ranged request with 206 — which is every
  request a `<video>` makes. Hence chunked blobs in IndexedDB, assembled and re-served as synthesized
  206s. **Safari probes a media source with `bytes=0-1` and abandons the element if the reply is
  malformed**, which presents as "it downloaded but won't play" rather than as a range error, so
  `offlineRange.ts` is worth reading before touching that shape.
- **`contentType` is captured from the origin's own response**, not guessed from the file extension,
  so the worker replays exactly what the server would have sent — including its deliberate
  `video/mp4` for every video container.
- **The worker's whole routing decision is `classifyRequest` in `offlineRoutes.ts`**, kept pure so the
  rules about what *not* to claim are testable: the SSE stream must never be intercepted
  (`useTaskProgress` would wait forever), and no navigation fallback may apply under `/api/`.
- **`useMediaElement` needs no offline branch.** It points `src` at `/api/media/{id}` and the worker
  answers from storage, so video, audio, seeking and Media Session lock-screen controls all work
  offline unchanged. (The clip *editor* would play too, but clipping is a server job, so its entry
  points are hidden offline — next bullet.)
- **Offline mode is playback-only, and the UI enforces it by hiding, not disabling.** Nothing that
  writes to the server or reads live server state is rendered while the mode is on: no download
  form, clip/tag/share/delete/transcript actions, ratings clicks, bulk bar, scope selector,
  semantic search, group-by, playlist create/reorder/remove/delete/share/sync, Sign out, Change
  Password, admin view. A new server-touching control must join one of the gates: `buildMediaActions`
  takes `offlineMode` and returns only the offline button (that single choke point covers the
  library, playlist detail and tag mix via `MediaListView`, which also drops `onRate` so
  `StarRating` renders read-only); `page.tsx` shows `OfflineUnavailable` for Subscriptions, Clips,
  Tasks, Stats and Settings and pins the library scope to `COMPLETE`; `DownloadsCard`,
  `PlaylistsCard`, `PlaylistDetailView`, `NavigationBar` and `MobileNavMenu` each read `useOffline()`
  and skip their own controls. Reads that can be answered locally are swapped rather than hidden
  (`fetchOfflineStats`, `offlineTags`, the playlist helpers below); reads that can't are simply
  not issued (`MediaPlayerContext`'s per-track detail GET, the storage poll).
- **Playlists work offline from a membership snapshot, not from the items store.** A downloaded
  record carries no playlist ids and `OfflineItem.source` names at most one collection (overwritten
  to `manual` on a hand download), so `offlinePlaylists.ts` keeps its own `playlists` store: every
  playlist's ordered `media_ids`, fetched in one call from `GET /playlists?include_media_ids=true`
  by `snapshotPlaylists()`. `OfflineProvider` refreshes it on **both** edges of the switch — online
  launches keep it fresh, and switching offline is usually done while still connected, so it is the
  last-chance refresh. Which playlists to *show* is decided at read time (≥ 1 downloaded member),
  with counts and durations describing what is on the device. Adding the store was a `DB_VERSION`
  bump: every store in `openOfflineDb` is created under an `if (!contains)` guard, so bumping the
  version is the whole migration. Offline playback of a playlist goes through `playMediaQueue` with
  the stored tracks — a real playlist id is fine there, and `setQueueResume` skips its refetch
  offline as it already does for virtual ids.
- **Offline capability is read per-component, not passed down.** `OfflineDownloadButton` and
  `CollectionOfflineButton` call their own hooks rather than taking handlers through
  `buildMediaActions`, deliberately avoiding the `onClip` trap above — an optional prop whose button
  renders regardless and does nothing where a surface forgot to wire it.
- **The library swap is one line in `page.tsx`.** `fetchDownloadJobs` returns `fetchOfflineLibrary(…)`
  when offline mode is on. That is the only path by which the list is loaded, and its filter and sort
  semantics deliberately mirror `repositories/media_details.py` (`&&` binding tighter than `||`, tags
  as "any of", `NULLS LAST`) so a query that works online doesn't quietly return nothing offline.
- **Offline mode is a manual switch and never inferred from `navigator.onLine`** — a phone reports
  itself online on a captive portal. It is also what `AuthContext` consults to skip its 30×2s retry
  loop, which otherwise stalls an offline cold start on "Loading…" for a full minute.
- **Installed-app safe areas.** `layout.tsx` ships `viewportFit: "cover"` with
  `appleWebApp.statusBarStyle: "black-translucent"`, so in iOS standalone mode the page extends
  under the status bar *and* the home indicator, and nothing reserves either space. Every
  viewport-pinned surface pads itself with `env(safe-area-inset-*)`: `NavigationBar`'s
  `.status-bar-inset` strip (top — painted `#0a0a0a` on the light-theme list at the tail of
  `globals.css`, because iOS draws the clock white over whatever is beneath it), `StatsCard`'s
  sticky filter bar and `StatsPanel`'s `scroll-mt` (both hang off the nav's 3.5rem and must add
  the same inset), and the audio bar's inner element in `page.tsx` plus the `Toaster` (bottom).
  A new `fixed`/`sticky` element at either edge needs the same treatment. jsdom can't evaluate
  `env()`, so this is verified on a device, not in Vitest.
