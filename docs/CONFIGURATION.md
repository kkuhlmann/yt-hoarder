# Configuration reference

[`config.sample.yml`](../config.sample.yml) is the authoritative annotated reference for every
application setting. This document covers the settings that need more explanation than a comment
allows: live runtime settings, the throttling knobs, AI agent access (MCP), and dev-mode networking.

## Where settings live

`config.yml` is the single source of application settings. `.env` is Docker Compose's variable file
and is never read by the application; it holds only values consumed outside the backend process:

| Variable | Consumed by | Purpose |
|---|---|---|
| `AUDIO_ONLY_PATH`, `VIDEO_PATH` | Compose | Host media directories, resolved before the container exists |
| `YT_HOARDER_IMAGE`, `YT_HOARDER_TAG` | Compose | Which image and release to run |
| `NEXT_PUBLIC_BACKEND_API` | Next.js dev server | Overrides the API address the dev UI calls (dev mode only; normally empty) |
| `ALLOWED_DEV_ORIGINS` | Next.js dev server | Extra hosts permitted to reach dev resources |
| `FORWARDED_ALLOW_IPS` | uvicorn | Proxy addresses whose `X-Forwarded-For` is trusted |

Settings can also be supplied as environment variables in double-underscore notation (for example
`TRANSCRIPTION__WHISPER_MODEL=small.en`), but they apply only to keys `config.yml` leaves unset — a
key present in `config.yml` always wins. Under Docker they must additionally be added to the compose
file's `environment:` block to reach the container at all.

## Live settings (Settings tab)

These are stored in the database rather than `config.yml`, are edited from the **Settings** tab, and
take effect on the next task without a restart.

**Subscription Check** is the interval in minutes between subscription scans, from 1 minute to a full
day. Slots are clock-aligned from midnight, so 15 fires at :00/:15/:30/:45 and 120 fires on every
even hour.

**Task concurrency** applies immediately. Raising a lane starts queued work right away; lowering one
lets the jobs already running finish rather than cancelling them. Raising the *Downloads* lane above
1 means the download delay no longer paces the application as a whole, increasing the risk of YouTube
rate limiting. Raising the *ML* lane runs several transcriptions at once, each loading its own
Whisper model.

Also editable live: temp-file cleanup age, yt-dlp player-client order, transcript chunking, and table
page sizes.

## Throttling

Three settings exist specifically to avoid being flagged by YouTube. All three are off or mild by
default.

- **Download Sleep** — seconds to wait between downloads. Applies to subscription and playlist
  downloads only, not one-off submissions. Default 60.
- **Speed Limit** — caps each download's speed in KB/s; `0` means unlimited. It applies *per
  download*, so with the Downloads lane above 1 the total is that many times the limit.
- **Request Sleep** — seconds to wait between the HTTP requests yt-dlp makes while reading metadata;
  `0` disables it. This is usually the more effective of the two against bot detection because it
  paces the request *pattern* rather than the bytes, but it also slows channel and playlist scanning.
  Start at 1–2 seconds.

Adjust these only after ruling out the cheaper causes in
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#downloads). An out-of-date image is a far more common culprit
than throttling.

## Release pinning

`YT_HOARDER_TAG` in `.env` defaults to `latest`. Pin it (for example `v0.1.0`) to hold a version
through updates. Pinning *backwards* to an older release after a newer one has run its database
migrations may fail to start, since migrations are not guaranteed reversible — back up with
`task db:backup` first. The variable is read only by `docker-compose.published.yml`; the
build-from-source modes ignore it.

## What offline mode requires

Offline mode is gated on **two** conditions, and the UI hides every offline control until both hold
rather than offering something that would fail later. Neither has a flag that overrides it.

### 1. A secure context (HTTPS)

Offline mode is built on a **service worker**, and browsers refuse to register one outside a *secure
context* — real HTTPS, or `localhost`. A LAN address like `http://192.168.1.50:8000` does not
qualify.

Nothing about this is specific to any one tool: the app serves plain HTTP on port 8000 and something
in front of it terminates TLS. Any of these work identically.

| Option | Notes |
|---|---|
| **Caddy** | Automatic Let's Encrypt. Least configuration if you have a public domain. |
| **nginx** / **Traefik** | With certbot or their own ACME support. |
| **Cloudflare Tunnel** | No inbound port needed; the certificate is Cloudflare's. |
| **Tailscale** | `tailscale serve --bg 8000` issues a real certificate for the machine's `*.ts.net` name. A single command if you already run Tailscale, and no public exposure. |
| **`localhost`** | Already a secure context, so offline mode works on the server itself with no TLS. Useful for testing, useless for a phone. |

**A self-signed certificate is a trap unless you do it properly.** The CA must be installed into the
*device's* trust store (a configuration profile on iOS, the user certificate store on Android).
Clicking through the browser's "your connection is not private" interstitial does **not** produce a
secure context: the page loads, the service worker silently refuses to register, and the offline
controls stay hidden with nothing on screen explaining why.

`FORWARDED_ALLOW_IPS` needs no change for `tailscale serve`: it proxies from `127.0.0.1`, which
uvicorn already trusts. A reverse proxy on another host does need it — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### 2. A production install

`task prod` or `task published`. **Development mode registers no service worker and shows no offline
controls**, which is deliberate on two counts. A worker in front of `next dev` caches the dev
server's output and breaks hot reload. And there is no stable shell to cache in the first place:
production is a static export served same-origin with the API, while dev compiles on demand through
Turbopack — chunk URLs change on every edit — and serves the UI and API on separate origins.

### Recommended alongside TLS: `cookie_secure`

Once HTTPS is in front, set:

```yaml
auth:
  cookie_secure: true
```

`setup.sh --cookie-secure` writes this at install time, for a machine that already has TLS in front
of it.

This is a hardening step, **not** an offline-mode prerequisite — it sets the `Secure` flag on the
auth cookie and nothing more, and offline mode works either way. It is worth doing because it stops
the cookie from ever being sent in the clear.

**It has a cost worth knowing before you make it.** The auth cookie stops being sent over plain
`http://`, so signing in at `http://192.168.1.50:8000` no longer works and everyone has to use the
HTTPS address. `http://localhost:8000` on the server itself keeps working, because browsers already
treat localhost as trustworthy. `cookie_secure` defaults to `false` precisely so a plain-HTTP install
works out of the box, so this is opt-in rather than something to set speculatively.

### Storage on the device

Downloads live in the browser's IndexedDB, which is per-device, per-browser, and never synced. The
app asks for persistent storage the first time you download something; browsers grant or refuse that
on their own heuristics. Two consequences on iOS: install the app to the home screen (a plain Safari
tab has its storage cleared after roughly a week unused), and expect a system prompt the first time a
large download crosses the browser's quota threshold.

The **storage budget** (default 4 GB, changed from the storage dialog) governs only what the app
itself will keep. Items downloaded by hand are pinned and never removed automatically; playlist and
subscription downloads form the pool that is reclaimed least-recently-played-first when the budget is
reached.

## AI agents (MCP)

yt-hoarder can serve an [MCP](https://modelcontextprotocol.io) endpoint so an AI agent you run
yourself can research your transcript library: *"What did this channel say about X in 2025?"*,
answered with a summary, citations, upload dates and links to the exact second. The app never calls
an AI service; connect whichever MCP client you like, such as Claude Code, LM Studio or Cursor.

**Turning it on.** Run `setup.sh --mcp`, answer yes to its prompt, or set it in `config.yml` and
restart the backend:

```yaml
mcp:
  enabled: true
  public_url: ""   # optional, see below
```

While it is off, the endpoint answers 404 and the token menu is hidden.

**Tokens.** Every approved user creates their own tokens from the account menu → **MCP Access
Tokens**. A token is shown once, acts as its user, and sees exactly what that user sees in the app:
their own media and media shared with them. It stops working when:

- it reaches its optional expiry;
- the user revokes it;
- the user's password changes, the same rule that signs out their other sessions;
- an admin revokes all of a user's tokens from **Settings → User Management**.

Tokens authenticate the MCP endpoint only, never the rest of the API.

**Connecting a client.** Any MCP client that can send a custom header works. It needs two things:

- **URL:** the endpoint address, described below;
- **Header:** `Authorization: Bearer <token>`.

The token dialog shows both with your server's address and token filled in, plus ready-to-paste
setup: a `claude mcp add` command and an `mcp.json` snippet (an `mcpServers` entry with the URL and
header) of the kind LM Studio and Cursor read. With Claude Code, for example:

```bash
claude mcp add --scope user --transport http yt-hoarder https://<your-host>/api/mcp \
  --header "Authorization: Bearer <token>"
```

Use a model that supports tool calling. The server also offers a `research` prompt for a
summary-plus-evidence-table answer; Claude Code lists it as `/yt-hoarder:research`.

**The endpoint address** is `/api/mcp` on a production or published install (`http://<host>:8000/api/mcp`
by default) and `http://<host>:8000/mcp` in dev mode. Use the address another machine reaches the
server by, not `localhost`, when the agent runs elsewhere. Tokens travel in a request header, so serve
the app over HTTPS (set up as in [A secure context](#1-a-secure-context-https)) when agents reach it
across an untrusted network.

**The tools** are read-only:

| Tool | What it does |
|---|---|
| `library_overview` | Channels, upload-date span and tags, so the agent can pick real channel names and dates |
| `search_transcripts` | Timestamped passages, best first, filtered by channel, tag, rating and upload-date range. Each result reports exactly what was searched: the semantic phrase, keywords, channels, dates and embedding model |
| `get_transcript` | Reads a video's transcript around a moment, or page by page |

**`public_url` and in-app links.** Every result links both to the source (`youtube.com/…&t=754`) and
into yt-hoarder's own player (`/?media=123&t=754`), which also works for audio and for videos since
removed from YouTube. With `public_url` empty, in-app links use the address the agent connected to,
which is right when your browser and the agent reach the server by the same name. Set it to the
address you open the app at when they differ, or when a reverse proxy rewrites the `Host` header.
Behind a TLS-terminating proxy, also set `FORWARDED_ALLOW_IPS` so links come out as `https`.

**Limits.** 120 requests a minute per token. Repeated failed authentications from one address are
locked out for a few minutes.

## Dev-mode networking

**Dev mode needs no configuration to be reached from another device.** Open
`http://<the-docker-host>:3000` from a phone, a laptop, a LAN IP, a Tailscale name or a domain and it
works — nothing to declare up front, and no rebuild. Prod and published were always like this, since
they call the API through a same-origin `/api` path; dev now matches them.

Three things make that true, and each has an escape hatch if your setup is unusual.

**The API address follows your browser.** Dev serves the UI on port 3000 and the API on port 8000, so
the frontend has to name a host. It derives one from the address you actually browsed to, so the same
dev server answers correctly on every address the machine has. Set `NEXT_PUBLIC_BACKEND_API` in `.env`
only when the API is *not* on port 8000 of that host — behind a reverse proxy, or with TLS terminated
in front. It is read when the dev server starts, so a change takes effect on:

```bash
docker compose -f docker-compose.dev.yml up -d frontend
```

**Credentialed cross-origin calls are allowed from anywhere.** Because every dev API call is
cross-origin, `auth.allowed_origins` in `config.yml` defaults to empty, which means any origin. The
auth cookie is `SameSite=lax`, so a foreign website still cannot make a credentialed request to your
API — that, rather than this list, is what keeps other sites out. To enforce a strict allowlist
anyway, list full origins including the port (`http://192.168.1.50:3000`) and restart the backend.
The production image serves the frontend same-origin and ignores the setting entirely.

**Hot reload works from any dotted host.** Next.js 16 refuses cross-origin requests to dev-server
resources unless the host is allowed, which otherwise leaves the page loading but never updating on
edit. Every dotted host is allowed — LAN IPs, `.local` names, Tailscale MagicDNS, domains — as is
`localhost`. Two shapes cannot be covered by any wildcard Next accepts: a **single-label hostname**
like `http://nas:3000`, and an **IPv6 literal**. Add those to `ALLOWED_DEV_ORIGINS` in `.env`
(comma-separated, hostnames only — no scheme or port), then restart the frontend service.
