# Offline mode

Media can be downloaded onto a phone and played with no connection at all — including the app
itself, which normally could not load without reaching the server.

## What offline mode requires

**Two things are required, and the offline controls stay hidden until both are true.** They hide
themselves deliberately: downloading media that could never play back would fail at the only moment
that matters. Neither condition has a flag that overrides it.

### 1. HTTPS, from any trusted certificate

Offline mode is built on a **service worker**, and browsers refuse to register one outside a *secure
context* — real HTTPS, or `localhost`. An install reached at `http://192.168.1.50:8000` does not
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

`task prod` or `task published`. **Development mode (`task dev`) registers no service worker and
shows no offline controls**, which is deliberate on two counts. A worker in front of `next dev`
caches the dev server's output and breaks hot reload. And there is no stable shell to cache in the
first place: production is a static export served same-origin with the API, while dev compiles on
demand through Turbopack — chunk URLs change on every edit — and serves the UI and API on separate
origins.

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

## Using it

With both requirements met:

1. Open the app and use your browser's **Add to Home Screen**. This matters on iOS: a plain Safari
   tab has its storage cleared after about a week of not being opened, while an installed web app
   keeps it and is allowed far more space.
2. Save something with the **download arrow** on any item, or the **refresh icon** on a playlist or
   subscription to keep its newest items downloaded automatically.
3. **Offline Storage** in the account menu (on a phone, the menu at the top right) opens storage
   management — how much is stored, the budget, and what to remove. Items you saved by hand are
   kept; playlist and subscription downloads are removed least-recently-played-first to stay inside
   the budget.
4. Switch on offline mode with the **signal icon** in the top bar, or **Offline mode** in the menu on
   a phone. The library then shows only what is on the device, and playback positions are saved
   locally and sent to the server the next time you switch back.

## What works offline

**Offline mode is for playing what you have downloaded, and nothing else.** The Downloads tab shows
the saved items with search, tag and rating filters and sorting; the Playlists tab shows every
playlist that contains at least one downloaded item, with just those items, in playlist order, ready
to play or shuffle. Everything that needs the server waits until you switch back: new downloads,
clips, subscriptions, tasks, stats, settings, sharing, editing tags or ratings, adding to or
reordering playlists, deleting — those buttons and tabs are simply absent rather than failing. The
playlist list is captured whenever the app is online and again at the moment you switch offline, so
a playlist you just filled is there when you go.

Offline mode is a manual switch, never automatic — a phone reports itself online while attached to a
hotel captive portal, so guessing would swap the library out at the worst moment.

## Storage on the device

Downloads live in the browser's IndexedDB, which is per-device, per-browser, and never synced. The
app asks for persistent storage the first time you download something; browsers grant or refuse that
on their own heuristics. Two consequences on iOS: install the app to the home screen (a plain Safari
tab has its storage cleared after roughly a week unused), and expect a system prompt the first time a
large download crosses the browser's quota threshold.

The **storage budget** (default 4 GB, changed from the storage dialog) governs only what the app
itself will keep. Items downloaded by hand are pinned and never removed automatically; playlist and
subscription downloads form the pool that is reclaimed least-recently-played-first when the budget is
reached.

## Troubleshooting

Offline controls missing, or an empty library with offline mode on: see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).
