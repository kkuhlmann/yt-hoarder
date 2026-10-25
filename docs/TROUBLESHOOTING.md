# Troubleshooting

Common problems and their fixes. For configuration reference see
[CONFIGURATION.md](CONFIGURATION.md) and [`config.sample.yml`](../config.sample.yml).

## Installation and updates

**Pulling the image fails with `denied` or `manifest unknown`.** Either that release isn't published
yet, the tag in `YT_HOARDER_TAG` doesn't exist, or the machine can't reach `ghcr.io` (offline,
proxy, DNS). Check the [releases page](https://github.com/kkuhlmann/yt-hoarder/releases) for a tag
that exists, or build from source with `docker compose -f docker-compose.prod.yml up -d --build`.

**The app doesn't update itself.** This is deliberate. `latest` is resolved when you pull, not on
every restart, so a reboot never swaps the running version. Run `task update` to move to a newer
release. The consequence is that an install left alone for months runs months-old yt-dlp, which is
the usual cause of downloads breaking.

**Stopping takes several minutes.** `docker compose down` waits up to 6 minutes for the backend so
an in-flight download can finish rather than being truncated. It exits as soon as the work is done;
only a mid-download stop takes the full wait.

**The backend won't start and the log says `auth.secret_key is still the sample value`.** The sample
key is published in this repository, so leaving it in place would allow anyone to forge an
administrator token. Set a real value in `config.yml` under `auth.secret_key` and restart. Generate
one with `python -c "import secrets; print(secrets.token_hex(32))"`. `setup.sh` does this
automatically.

## Downloads

**YouTube rate limiting or empty downloads.** If files come back empty, update the image to get a
newer yt-dlp. This resolves the problem far more often than any setting change. On the published
release run `task update` (or `docker compose -f docker-compose.published.yml pull &&
docker compose -f docker-compose.published.yml up -d`); if you build from source, rebuild with
`task prod`. If downloads are being blocked rather than returning empty, raise **Request Sleep** to
1–2 seconds and, if that is insufficient, increase **Download Sleep** or set a **Speed Limit** in the
Settings tab. Adjusting the yt-dlp player-client order is a last resort, not a first step.

**Repeated HTTP 403 errors on YouTube downloads.** Check the cookie path first. The fastest test is
to set **Cookies mode** to `NEVER` in Settings and retry. If the 403s stop, the cookie session is the
problem rather than yt-dlp or the player clients:

- Re-export cookies from a private/incognito window, then close it *without logging out*. Cookies
  exported from a window you keep using are rotated out from under the app.
- Check the player clients under Settings. If the list contains `web_creator` (YouTube Studio),
  `ios`, or `android_vr` (YouTube has rejected every format it returns since August 2026), remove
  them — none is appropriate for this workload. "Reset to defaults" clears them.

**"The page needs to be reloaded" (`tv_downgraded player response playability status:
UNPLAYABLE`).** Since autumn 2026 YouTube serves some sessions a TV player that `tv_downgraded`
cannot use. Remove `tv_downgraded` from both player-client lists under Settings; `tv_samsung` is
its replacement and is already in the defaults. "Reset to defaults" does both.

**Stuck or orphaned tasks.** Task state lives entirely in PostgreSQL, so queued or interrupted tasks
resume automatically after a restart. For a clean slate, cancel pending tasks from the Tasks tab, set
`tasks.purge_on_startup: true` in `config.yml`, or run `task clean` for a full reset (deletes the
database and restarts fresh).

**"A cancelled download for this video still holds its place in the queue."** Cancelling a download
keeps that video from being re-queued, so subscriptions do not resurrect dismissed work. Submitting
the URL again therefore starts nothing. Retry the cancelled task from the Tasks tab, or delete it
there and submit again.

**Memory pressure during transcription.** Use a smaller Whisper model in `config.yml`
(`transcription.whisper_model: tiny.en`). Downloads and transcriptions each run one at a time by
default, and transcription runs in a short-lived child process whose memory is fully reclaimed after
each job.

## Accounts and access

**"Too many attempts. Please try again later." on the sign-in page.** Sign-in, registration and
password recovery are rate limited per client address to protect an exposed instance from
brute-force attempts. Normal use will not reach the limit — sign-in allows 30 attempts every 5
minutes, registration and recovery 10 per hour — and the limit clears as the window passes; the
`Retry-After` header on the response states when. If every user is blocked at once, the instance is
almost certainly behind a reverse proxy: uvicorn only trusts `X-Forwarded-For` from `127.0.0.1` by
default, so every visitor appears to be the proxy and shares one budget. Set `FORWARDED_ALLOW_IPS`
in `.env` to the proxy's address and restart.

**Locked out of the only admin account.** Use *Admin account recovery* on the sign-in page, which
writes a single-use code to `data/admin-recovery.txt` on the server, or run
`task admin:reset-password -- <username>` with shell access. See [Password recovery](#password-recovery)
below.

**Can't reach PostgreSQL from another machine.** The database port is published on `127.0.0.1` only,
since it ships with hardcoded credentials. Host tools on the Docker machine work normally; to reach
it from elsewhere, tunnel over SSH rather than republishing the port.

## Offline mode

**I don't see the offline controls.** They hide themselves until offline support can actually work,
so the download arrow, the signal icon and the database icon are all absent. Check, in this order:

1. **Are you on an `https://` address?** A LAN IP or a bare Tailscale IP over plain `http://` is not
   a secure context and cannot register a service worker. Put any trusted certificate in front of
   port 8000 — Caddy, nginx, Traefik, Cloudflare Tunnel, or `tailscale serve --bg 8000`. A
   self-signed certificate only counts if its CA is installed in the device's trust store; clicking
   through the browser warning does not.
2. **Are you running a production install?** `task dev` never shows offline controls — see
   [CONFIGURATION.md](CONFIGURATION.md#what-offline-mode-requires). Use `task prod` or
   `task published`.
3. **Is your image new enough to include the feature?** An install pinned to an older
   `YT_HOARDER_TAG` will not have it.

**Offline mode is on but the library is empty.** The toggle filters the library to what is stored on
this device; it does not fetch anything. Download items first — the arrow on a media row, or the
refresh icon on a playlist or subscription — then switch the toggle on.

**Downloads disappeared.** Two causes, and they look identical. On iOS, a plain Safari tab has its
storage cleared after roughly a week of not being opened; an app added to the Home Screen keeps its
storage and is allowed far more of it, so install it rather than browsing to it. Otherwise the
storage budget reclaimed them: playlist and subscription downloads are removed
least-recently-played-first once the budget is reached, while items downloaded by hand are pinned and
are never removed automatically. Raise the budget from the storage dialog, or pin the item by
downloading it by hand with the arrow on its media row — doing that to a synced item promotes it to
pinned and takes it out of the eviction pool.

**A download stops partway.** Downloads only progress while the app is in the foreground, so locking
the phone interrupts them. Nothing is lost — progress is saved after each chunk, and *Resume
interrupted* in the storage dialog picks up where it stopped.

**Buttons are missing, or a tab says it isn't available in offline mode.** By design. Offline mode
is playback-only: anything that needs the server — new downloads, clips, subscriptions, tasks,
stats, settings, sharing, tags and ratings, playlist editing, deleting, signing out — is hidden
rather than left to fail. Switch the toggle off and it all comes back.

**A playlist doesn't show up offline.** Only playlists with at least one downloaded item are listed,
and each shows just its downloaded items. The playlist list itself is captured while the app is
online and again at the moment you switch offline — so if you filled a playlist on another device,
open the app online once (or toggle offline mode off and on) before leaving.

## Dev mode networking

These apply only to dev mode (`docker-compose.dev.yml`). The published and prod modes serve the UI
and API from the same origin and are unaffected.

Opening the dev UI from another device needs no configuration — the API address follows the address
you browsed to. The failures below are what remains when something about the setup is unusual.

**Nothing loads and the browser console shows failed API calls, behind a reverse proxy or TLS.** The
frontend assumes the API is on port 8000 of the host you browsed to. When it isn't, set
`NEXT_PUBLIC_BACKEND_API` in `.env` to the address the browser should use and restart the frontend
(`docker compose -f docker-compose.dev.yml up -d frontend` — no rebuild needed).

**Requests fail with a CORS error.** `auth.allowed_origins` in `config.yml` is empty by default,
which allows any origin; a CORS failure means it has been filled in. Either add the origin you browse
to, including the port — `http://192.168.1.50:3000` — or empty the list again, then restart the
backend.

**Hot reload stops working when the UI is opened on a single-label hostname or an IPv6 literal.**
Next.js 16 blocks cross-origin requests to dev resources and HMR unless the host is allowed, so the
page loads normally and simply never picks up edits. Every dotted host is already allowed (LAN IPs,
domains, Tailscale names); a bare name like `http://nas:3000` and an IPv6 literal are the two shapes
no wildcard can match. Add them to `ALLOWED_DEV_ORIGINS` in `.env` (comma-separated, hostnames only)
and restart the frontend service.

## Password recovery

There is no email server involved, so recovery is trust-based or filesystem-based.

### A regular user forgot their password

The user clicks *Forgot your password?* on the sign-in page and enters their username. The request
appears for admins under **Settings → User Management**, where *Reset* generates a temporary
password. It is displayed once, with a copy button; deliver it to the user through whatever channel
you normally use. The user is required to choose their own password the first time they sign in with
it, so an admin never ends up holding a working credential for another user's account.

Any user can change their own password at any time with the key icon in the top right of the
navigation bar. Changing or resetting a password signs that account out on every other device.

### An admin forgot their password

Recovery requires access to the machine YT-Hoarder runs on; that requirement stands in for an email
inbox. Click *Admin account recovery* on the sign-in page and enter the admin username. A single-use
code is written to `data/admin-recovery.txt` inside the install directory:

```
  user:    admin
  code:    K7QM-3XR9-P2WD
  expires: 2026-07-26 19:14:22 UTC
```

Read that file off the server, enter the code, and choose a new password. The code expires in 15
minutes, works once, and the file is deleted as soon as it is used. Nothing about the account changes
until the code is submitted, so triggering recovery cannot lock out the admin.

If it reports a permission error writing the file, the `data/` directory is not writable by the
container. On Linux: `sudo chown 1000:1000 data/`.

With shell access the file step can be skipped entirely:

```bash
task admin:reset-password -- <username>
```
