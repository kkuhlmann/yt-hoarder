<!-- Screenshots & GIFs live in docs/images/. -->

<div align="center">

<img src="docs/images/logo-banner.png" alt="YT-Hoarder logo" width="65%" />

# YT-Hoarder

**Self-hosted YouTube download manager with automatic subscriptions, local AI transcription, and search across the spoken content of your library.**

![Self-hosted](https://img.shields.io/badge/self--hosted-000000?style=flat-square)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)
![Next.js 16](https://img.shields.io/badge/Next.js-16-000000?style=flat-square&logo=nextdotjs&logoColor=white)
![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?style=flat-square&logo=postgresql&logoColor=white)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg?style=flat-square)](LICENSE)

</div>

Subscribe to channels and playlists and YT-Hoarder keeps the library current on a schedule.
Downloads can be transcribed on-device with Whisper, which enables semantic search across the
collection, navigation to any spoken moment, clip extraction, playlist curation, and sharing with
other users on the instance. A channel can also be indexed *transcript-only*: searchable by what
is said in it, without keeping any of its media. It runs entirely on your own hardware under Docker, with no external
accounts and no cloud dependency.

<div align="center">

<img src="docs/images/hero.gif" alt="A tour of YT-Hoarder: searching transcripts, playing and clipping media, subscriptions, playlists, and themes" width="100%" />

</div>

## Features

| Feature | Description |
|---|---|
| **Automatic subscriptions** | Subscribe to channels and playlists; new uploads download on a schedule, filtered by title keyword, upload date, or duration, as audio-only or full video. |
| **Transcript search** | Items are transcribed from their YouTube captions, or locally with faster-whisper, embedded with onnxruntime, and stored in pgvector. Searches return the exact spoken passages and link to their timestamps, narrowed to whatever the library is currently filtered to — the search box, the open group folder, tag chips and minimum rating. While watching, the transcript can sit beside the player, following along and searchable on its own. |
| **Transcript-only indexing** | Download or subscribe to a video, playlist or channel as **Transcript Only**: only its transcript is kept, so it is searchable (in the app and over MCP) without taking up the library. See [Transcripts](#transcripts). |
| **Mobile-optimized interface** | The web UI is built for phones and tablets as well as desktop: card layouts in place of tables, touch drag-and-drop, mobile chart forms, and native video fullscreen and picture-in-picture on iOS. |
| **Waveform clipping** | Scrub an interactive waveform, drag region handles to frame-accurate start and end points, zoom for precision, and export a clip. |
| **Real-time progress** | Downloads and transcriptions stream progress over Server-Sent Events, reporting phase (video vs. audio), and percent complete.
| **Library organization** | Switch between table and grid views, group media into folders by channel, tag, or date, and rate and tag items. Hovering the progress bar shows sprite-based thumbnail previews. |
| **Playlists** | Build custom playlists with drag-and-drop ordering and play through them with next-up, shuffle, and autoplay controls. |
| **Statistics** | Storage breakdown, download trends over time, transcription coverage, and filterable engagement metrics. |
| **Multi-user and sharing** | Accounts with admin approval, per-user storage limits, and sharing of media, subscriptions, playlists, and clips. Media already downloaded by one user is granted to the next rather than downloaded twice. |
| **AI research (MCP)** | Optional MCP endpoint that lets an AI agent you run yourself — Claude Code, LM Studio, Cursor or any other MCP client — search your transcripts and answer with citations, upload dates and links to the exact moment, both on YouTube and in the app's own player. Each user connects with a personal access token that sees only their library. Off by default; see [Configuration](#configuration). |
| **Boolean search** | The Downloads search box matches channel and title and supports `&&` (AND) and `\|\|` (OR); `&&` binds tighter. Single `&` and `\|` are matched literally. The operators narrow transcript search results too. |
| **Offline mode** | Save audio and video to a phone and play it with no connection — the app itself keeps working offline, installed to the home screen. Downloads can be pinned per item or kept in sync with a playlist or subscription, inside a storage budget; playlists with downloaded items play offline too. Playback-only while switched on. Needs HTTPS and a production install; see [On mobile](#on-mobile). |
| **70+ themes** | Built-in themes from retro terminal to modern light and dark palettes, switchable instantly. |
| **Audio visualizer** | Optional reactive visualizer behind the player bar for audio-only tracks, following the active theme's colors. Desktop only, to protect lock-screen playback on iOS. |

## On mobile

The same interface on a phone — the list and grid layouts, with the player docked at the bottom.

<div align="center">

<img src="docs/images/mobile.png" alt="The mobile interface, in list and grid layouts" width="85%" />

</div>

### Offline mode

Media can be downloaded onto the phone and played with no connection at all — including the app
itself, which normally could not load without reaching the server.

**Two things are required, and the offline controls stay hidden until both are true.** They hide
themselves deliberately: downloading media that could never play back would fail at the only moment
that matters.

**1. HTTPS, from any trusted certificate.** The browser feature that makes a site work offline (a
service worker) is refused on a plain `http://` address, so an install reached at
`http://192.168.1.50:8000` cannot do any of this. Anything that puts a genuinely trusted certificate
in front of port 8000 works — Caddy (automatic Let's Encrypt), nginx with certbot, Traefik,
Cloudflare Tunnel, or Tailscale, which is a single command if you already use it:

```bash
tailscale serve --bg 8000
```

A self-signed certificate works **only** if you install its CA into the device's trust store.
Clicking through the browser's "not private" warning does not count — the origin stays untrusted,
the service worker still refuses to register, and the offline controls stay hidden with no visible
reason why.

**2. A production install** — `task prod` or `task published`. Development mode (`task dev`) shows
no offline controls at all. That is by design rather than an oversight: a service worker sitting in
front of the dev server caches its output and breaks hot reload, and dev serves the UI and the API
on two different origins besides.

Recommended once you are on HTTPS, though not required for offline mode: set
`auth.cookie_secure: true` in `config.yml` and restart (or pass `--cookie-secure` to `setup.sh` at
install time), so the auth cookie is never sent in the clear. Be aware this stops sign-in from
working over plain `http://` on the LAN — use the HTTPS address from then on
(`http://localhost:8000` on the server itself keeps working).

With both requirements met:

1. Open the app and use your browser's **Add to Home Screen**. This matters on iOS: a plain Safari
   tab has its storage cleared after about a week of not being opened, while an installed web app
   keeps it and is allowed far more space.
2. Save something with the **download arrow** on any item, or the **refresh icon** on a playlist or
   subscription to keep its newest items downloaded automatically.
3. The **database icon** in the top-left opens storage management — how much is stored, the budget,
   and what to remove. Items you saved by hand are kept; playlist and subscription downloads are
   removed least-recently-played-first to stay inside the budget.
4. Tap the **signal icon** to switch on offline mode. The library then shows only what is on the
   device, and playback positions are saved locally and sent to the server the next time you switch
   back.

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

## Installation

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) with Docker Compose v2, `openssl`,
either `curl` or `wget`, and disk space for your media. The default install pulls a prebuilt image,
so there is no build toolchain and nothing to compile. [Task](https://taskfile.dev/installation/) is
optional and adds shortcuts when working from a source checkout.

The published image is a multi-arch manifest covering x86-64 and ARM64, including Apple Silicon,
Raspberry Pi 5, and Graviton/Ampere servers. Transcription is CPU-bound and slower on ARM; consider a
smaller `transcription.whisper_model` there.

1. **Run the setup script:**

   ```bash
   wget https://raw.githubusercontent.com/kkuhlmann/yt-hoarder/main/setup.sh
   bash setup.sh
   ```

   There is no repository to clone. The script creates `./yt-hoarder/`, downloads the two Compose
   files it needs, prompts for media storage paths and the Whisper model and CPU threads, generates a
   JWT secret, writes `.env` and `config.yml`, then pulls the release image and starts the app.

   Every prompt has a corresponding flag for scripted setup (`--install-dir`, `--audio-path`,
   `--whisper-model`, `--image-tag`, and others; run `bash setup.sh --help` for the full list).
   `-y`/`--yes` accepts every default and performs a complete install in one command:

   ```bash
   bash setup.sh -y
   ```

2. **Open the UI and create an account:**
   - Published or prod mode: <http://localhost:8000> · Dev mode: <http://localhost:3000>
   - The first user to register becomes admin automatically. Subsequent users require admin approval.
   - API documentation (dev mode only): <http://localhost:8000/docs>

3. **Update:** from the install directory, run
   `docker compose -f docker-compose.published.yml pull && docker compose -f docker-compose.published.yml up -d`
   (or `task update` from a checkout). Updates are opt-in — a running container is never replaced
   until you pull — and this is also how new yt-dlp versions are picked up. All three modes share one
   Docker project, so the database and downloaded media survive switching between them.

<details>
<summary>Building from source</summary>

The published image covers both x86-64 and ARM64, so building is only needed to run modified code or
to contribute. It is the same script with two extra launch modes. Expect 20–45 minutes and several GB
of disk:

```bash
git clone https://github.com/kkuhlmann/yt-hoarder.git
cd yt-hoarder
bash setup.sh --launch build-prod   # or build-dev for :3000 + :8000 with hot reload
```

The `task` shortcuts (`task update`, `task db:backup`, `task admin:reset-password`) require the
checkout — they are unavailable in a `wget` install, which has no `Taskfile.yml`.

</details>

To configure the Compose files by hand instead of running `setup.sh`, see
[`docs/MANUAL_SETUP.md`](docs/MANUAL_SETUP.md).

## Usage

1. **Register** the first account, which becomes the administrator.
2. **Add content:** create a subscription for a channel or playlist, or paste a single video or
   playlist URL to download on demand.
3. **Monitor the Tasks tab** as downloads and optional transcription run. A pasted URL appears
   immediately as **Resolving** while its details are fetched.
4. **Open a download** in the built-in player. Playback position is saved per user, and transcribed
   items show their transcript beside the player, to follow along, search, and jump to any line.
5. **Clip, collect, and share:** extract a clip on the waveform, add media to a playlist, and share
   any of it with other users on the instance.

| Tab | Purpose |
|-----|---------|
| **Downloads** | Browse the library, play media, search transcripts, create clips, rate and tag; switch to grid view and group into folders by channel, tag, or date. The **Transcripts** view lists everything with a transcript, including transcript-only items |
| **Subscriptions** | Add and edit channel and playlist subscriptions and their filters |
| **Clips** | Manage clips created from your media |
| **Playlists** | Create and order playlists, play them through, and create clips from any track |
| **Tasks** | Monitor active downloads and transcription jobs. A submitted URL appears as **Resolving**, then **Queued** with the real title. Unreleased videos (live streams, upcoming premieres, post-live processing) appear as **Not Released**; subscriptions retry them automatically, one-off downloads must be re-added once the video airs. A task awaiting an automatic retry shows what failed and when it will retry (`HTTP 403 (3/20): Retries in 4m`) |
| **Stats** | Storage, download trends, and engagement metrics |
| **Settings** | Runtime configuration (admin only) |

### Download options

The toggles beside the URL box on the **Downloads** tab. They apply to that submission only.

| Option | What it does |
|---|---|
| **Transcript Only** | Keeps only the transcript, for searching: from YouTube's captions, or if a video has none, from Whisper run on a temporary audio download that is deleted afterwards. Nothing goes in your library or counts toward your storage limit. Hides the options below that it makes irrelevant. See [Transcripts](#transcripts). |
| **Audio Only** | Downloads audio instead of video, saved as m4a to the audio path. Also switches the quality selector to bitrate tiers. |
| **Playlist** | Treats the URL as a playlist and expands it into one download per video. Off, a `watch?v=…&list=…` URL downloads only the single video it points at. |
| **Overwrite** | Re-downloads a URL already in your library, replacing the existing copy and its transcript. Off, an already-downloaded URL is skipped. |
| **Transcript** | Makes the item searchable by spoken content. Uses the video's English YouTube captions (manual or auto-generated) when they can be fetched, and transcribes it with Whisper after it downloads otherwise — a caption fetch that fails never fails the download. |
| **Quality** | Caps the resolution — **Best** takes the highest available, otherwise the best format at or below the height you pick (1440p–360p). |
| **Quality** (audio-only) | Caps the bitrate. **Best** and **128 kbps** copy YouTube's native AAC stream untouched; 96/64/48 kbps re-encode down to hit the target. |

### Subscription options

Set per subscription when you add it, and editable afterwards. Subscriptions carry the same
**Transcript Only**, **Audio Only**, **Overwrite**, **Transcript**, and **Quality** options as above,
applied to every video they download, plus filters deciding *which* videos qualify. Transcript Only
is fixed once the subscription exists — to switch a channel between indexing and downloading, add a
second subscription.

| Option | What it does |
|---|---|
| **Date filter** | Ignores anything released on or before this date, so a new subscription doesn't pull in a channel's entire back catalogue. |
| **Title filter** | Only downloads videos whose title contains this as a whole word, case-insensitive — `mix` matches "Mix" but not "remix". |
| **Min / Max duration** | Skips videos shorter or longer than the given number of **minutes**. Useful for excluding Shorts, or multi-hour streams. |
| **Enabled** | The switch on each row in the Subscriptions table. Pauses checking without deleting the subscription or anything it downloaded. |

Filtered-out videos are recorded as skipped rather than re-examined every cycle. Loosening a filter
later re-evaluates them, so widening a duration range or clearing a title filter picks up the videos
it previously passed over. How often subscriptions are checked is set in **Settings**, not here.

### Transcripts

**Transcript-only items** are indexed for search but never kept as media. They don't appear in the
Library or in playlists, only in transcript search, MCP, and the **Transcripts** view. Each one
uses YouTube's English captions when it has them; a video without captions is downloaded as the
smallest English audio stream (never one of YouTube's auto-dubbed tracks while an English one
exists), transcribed with Whisper, and the audio deleted. Whisper is CPU-heavy and
runs one job at a time, so a large caption-less backlog takes a while; caption fetches are paced by
the same **Download Sleep** setting as downloads, since both talk to YouTube.

**The Transcripts view** (the scope picker at the top of the Downloads tab) lists everything you can
search: transcript-only items, library media with a transcript (*In library*), and deleted media
whose transcript you kept (*Deleted*). Like the Library it has a **Grid** view, and it groups into
folders by channel, tag or date; an open folder narrows transcript search to it. With **Resume** on,
opening a row picks up where you left off.

Transcript-only items keep a small thumbnail (YouTube's 480px image, about 20 KB) so the grid has
pictures. A thumbnail lasts as long as the media file or the transcript does: deleting library media
but keeping its transcript keeps its picture, and deleting that transcript later removes it.

**Watching with the transcript.** Every media page has a **Transcript** button that shows the
transcript beside the player (below it on a phone): the playing line is highlighted, a click jumps
there, and its search box narrows it to the lines containing every word you type. Media without a
transcript shows **Generate transcript** instead, for its owner or an admin. Audio keeps playing in
the bottom bar; its **Transcript** button opens the same page, following the bar.

Items with no file to play — transcript-only, or deleted with the transcript kept — play through
YouTube's privacy-enhanced (`youtube-nocookie.com`) embed, which sets no cookies until you press play
and keeps these views out of your YouTube recommendations. Your position is still saved, so they
resume like everything else.

A few rules follow from all this:

- **Downloading a video in full replaces its transcript-only entry** once the full copy has a
  transcript, so search never shows the same passage twice. Asking for transcript-only on something
  whose transcript you already have is refused.
- **Deleting a transcript-only item deletes its transcript.** Deleting library media still offers
  to keep its transcript, which then shows in the Transcripts view as *Deleted*, visible to you only.
- **Captions and Whisper are English-only**, as for the **Transcript** option.

## Configuration

`setup.sh` configures the essentials. To change them later, edit `config.yml` and restart:

- **Storage paths** — where audio and video are saved on the host (`.env`: `AUDIO_ONLY_PATH`,
  `VIDEO_PATH`)
- **Whisper model** — `tiny.en` (fast, ~1 GB) up to `large` (best, ~10 GB)
  (`transcription.whisper_model`)
- **Release version** — `.env`: `YT_HOARDER_TAG`, `latest` by default; pin it to hold a version
  through updates
- **AI agent access (MCP)** — off by default (`mcp.enabled`); `setup.sh --mcp` turns it on. Each user
  then creates a token from the account menu → **MCP Access Tokens**, which shows the exact command to
  connect Claude Code or the `mcp.json` snippet for LM Studio and Cursor

`config.yml` is the single source of application settings and is documented in full in
[`config.sample.yml`](config.sample.yml). `.env` is Docker Compose's variable file and is never read
by the application. Download throttling, yt-dlp player-client order, subscription check interval,
task lane concurrency, and several other settings are editable live from the **Settings** tab and
take effect on the next task without a restart.

See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for the throttling knobs, live settings
semantics, release pinning, connecting AI agents over MCP, and dev-mode networking.

## Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for the full list, including password
recovery. The two most common issues:

- **Downloads fail or return empty files.** Update the image to get a newer yt-dlp (`task update`, or
  `docker compose -f docker-compose.published.yml pull && docker compose -f docker-compose.published.yml up -d`).
  This resolves the problem more often than any setting change.
- **Locked out of the only admin account.** Use *Admin account recovery* on the sign-in page, which
  writes a single-use code to `data/admin-recovery.txt` on the server, or run
  `task admin:reset-password -- <username>` with shell access.

## Development

[`CONTRIBUTING.md`](CONTRIBUTING.md) covers the dev setup — run `bash script/bootstrap.sh` (or
`task setup:dev`) to install the optional host tooling (uv and dependencies, node dependencies, task,
deno). Architecture, task orchestration, and conventions are documented in [`AGENTS.md`](AGENTS.md).

```bash
# Backend (Python 3.14, managed with uv)
cd backend
uv sync              # install dependencies
uv run pytest        # run tests
uv run ruff format . # format
uv run ruff check .  # lint

# Frontend (Next.js 16 / React 19)
cd frontend
npm install
npm run dev          # dev server
npm run build        # production build (Turbopack)
npm run lint         # eslint flat config; `next lint` was removed in Next 16
```

With [Task](https://taskfile.dev/) installed, `task published` / `task up` / `task down` /
`task logs` run the stack, `task update` pulls a newer release, `task db:shell` opens psql, and
`task tasks:runtime` shows the orchestrator's queued and running jobs. Run `task --list` for the
full set.

**Stack:** Python 3.14 · FastAPI · SQLModel · PostgreSQL + pgvector · yt-dlp · faster-whisper ·
onnxruntime on the backend; Next.js 16 · React 19 · TypeScript · Tailwind CSS on the frontend; Docker
and Alembic for infrastructure. An in-process task orchestrator runs four lanes (default,
subscriptions, downloads, ml) inside the backend, with no separate broker or worker processes.

## Support

YT-Hoarder is free, open source, and built in my spare time. If it's useful to you, a ⭐ on GitHub
helps more than you'd think. And if you'd like to buy me a coffee, it's genuinely appreciated —
never expected.

<div align="center">

<a href="https://buymeacoffee.com/yt.hoarder"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" alt="Buy me a coffee" /></a>

</div>

## License

[GNU AGPL v3.0](LICENSE) — running a modified version of YT-Hoarder as a network service requires
making the source of your modifications available to its users.

The published Docker images include GPL-licensed `ffmpeg`/`ffprobe` binaries from
[yt-dlp/FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds); their source and licenses are
available at that repository. The download helper is adapted from
[tubearchivist](https://github.com/tubearchivist/tubearchivist) (GPL-3.0).
