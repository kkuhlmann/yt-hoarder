<!-- Screenshots & GIFs live in docs/images/. -->

<div align="center">

<img src="docs/images/logo-banner.png" alt="YT-Hoarder logo" width="65%" />

# YT-Hoarder

**Self-hosted YouTube download manager with automatic subscriptions, local AI transcription, and search across the spoken content of your library — by you, or by an AI agent you connect.**

![Self-hosted](https://img.shields.io/badge/self--hosted-000000?style=flat-square)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white)
[![MCP](https://img.shields.io/badge/MCP-000000?style=flat-square&logo=modelcontextprotocol&logoColor=white)](#research-with-an-ai-agent)
![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)
![Next.js 16](https://img.shields.io/badge/Next.js-16-000000?style=flat-square&logo=nextdotjs&logoColor=white)
![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?style=flat-square&logo=postgresql&logoColor=white)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg?style=flat-square)](LICENSE)

</div>

Subscribe to channels and playlists, and YT-Hoarder downloads new uploads on a schedule. Then:

- **Watch and listen** in the built-in player, on desktop or phone, even offline.
- **Search what was said**, using transcripts made on your own machine, and jump to that moment.
- **Clip, collect and share** with clips, playlists, and other users on your instance.
- **Research with AI**: connect your own AI agent to dig through everything your library says, with
  answers cited to the second.

Everything runs on your own hardware under Docker, with no external accounts and no cloud dependency.

<div align="center">

<img src="docs/images/hero.gif" alt="A tour of YT-Hoarder: searching transcripts, playing and clipping media, subscriptions, playlists, and themes" width="100%" />

</div>

**Try it** with Docker installed:

```bash
wget https://raw.githubusercontent.com/kkuhlmann/yt-hoarder/main/setup.sh
bash setup.sh -y
```

Then open <http://localhost:8000> and register; the first account is the admin. See
[Installation](#installation) for the options.

## Contents

<div align="center">

**[Features](#features)** · **[On mobile](#on-mobile)** · **[Research with AI](#research-with-an-ai-agent)** · **[Installation](#installation)** · **[Usage](#usage)** · **[Configuration](#configuration)** · **[Troubleshooting](#troubleshooting)** · **[Development](#development)** · **[Support](#support)** · **[License](#license)**

[Download options](#download-options) · [Subscription options](#subscription-options) · [Searching](#searching) · [Transcripts](#transcripts) · [Offline mode](#offline-mode)

</div>

## Features

| Watch | |
|---|---|
| **Built-in player** | Video and audio resume where you left off, keep playing in a bar along the bottom while you browse, and preview thumbnails as you scrub. |
| **Mobile** | Built for phones as well as desktop, with touch drag-and-drop and native fullscreen and picture-in-picture on iOS. |
| **Offline mode** | Save audio and video to your phone and play it with no connection ([needs HTTPS](#offline-mode)). |
| **Clipping** | Cut frame-accurate clips on an interactive waveform, or straight from YouTube for items you never downloaded. |
| **70+ themes** | From retro terminal to modern light and dark, with an optional audio visualizer on desktop that follows them. |

| Search and research | |
|---|---|
| **Transcript search** | Search by what was said and jump to that moment, using YouTube's captions or Whisper run on your own machine. |
| **Transcript-only indexing** | Make a video, playlist or channel searchable without keeping its media ([Transcripts](#transcripts)). |
| **AI research (MCP)** | Your own AI agent answers questions about your library with every claim cited to the second ([Research with an AI agent](#research-with-an-ai-agent)). |

| Organize | |
|---|---|
| **Library** | Table or grid view, folders by channel, tag or date, your own ratings and tags, and a search box that combines terms with `&&` and `\|\|` ([Searching](#searching)). |
| **Playlists** | Drag-and-drop ordering, played through with next-up, shuffle and autoplay. |
| **Statistics** | Storage, download trends, transcription coverage and engagement metrics at a glance. |

| Run it | |
|---|---|
| **Automatic subscriptions** | Channels and playlists download new uploads on a schedule, as audio or video, filtered by title, upload date or duration. |
| **Multi-user and sharing** | Admin-approved accounts with storage limits share media, subscriptions, playlists and clips, and a video one user already has is never downloaded twice. |
| **Live progress** | Every download and transcription reports its progress as it runs. |

## On mobile

The same interface on a phone — the list and grid layouts, with the player docked at the bottom.
Media can also be saved to the phone and played with no connection; see [Offline mode](#offline-mode).

<div align="center">

<img src="docs/images/mobile.png" alt="The mobile interface, in list and grid layouts" width="85%" />

</div>

## Research with an AI agent

A transcribed library is a searchable record of everything its channels have said. Turn on the
optional MCP endpoint and an AI agent you run yourself — Claude
Code, LM Studio, Cursor, or any other MCP client — can research it for you:

> *What has this podcast said about interest rates since 2024, and has its view changed?*
>
> *Find the episode where they interviewed the author of this book, and quote what they said about it.*
>
> *Compare how these three channels covered the launch.*

The agent resolves the channels and dates you mention against the library, runs several searches by
meaning and by exact name, and reads around each passage before quoting it. The answer is a summary —
chronological when the question spans time — followed by an evidence table of upload date, channel,
video, quote and timestamp. Every timestamp links to the exact second, both on YouTube and in
YT-Hoarder's own player, which keeps working for audio downloads and for videos since taken down.
Every claim carries its citation, so checking the answer takes a click.

- **Index more than you keep.** Subscribe to channels as [Transcript Only](#transcripts) and their
  whole back catalogue becomes researchable without storing any of the media.
- **Nothing leaves the server on its own.** YT-Hoarder never calls an AI service; transcripts go only
  to the agent you connect. With a local model in LM Studio, the whole loop stays on your hardware.
- **Each user researches only their own library.** A personal access token sees exactly what its
  user sees in the app, the tools are read-only, and a password change revokes every token.
- **A ready-made research prompt** asks for the summary-plus-evidence answer; Claude Code lists it as
  `/yt-hoarder:research`.

**Getting started:** a new install has it on already (an older one needs `mcp.enabled: true` in
`config.yml` and a restart). Create a token from the account menu → **MCP Access Tokens**; the
dialog shows the exact
`claude mcp add` command, or the `mcp.json` snippet for LM Studio and Cursor, with your server's
address and token filled in. Full details in
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md#ai-agents-mcp).

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

   [AI agent access](#research-with-an-ai-agent) is on by default; add `--no-mcp` to leave it off.

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
6. **Research it (optional):** turn on MCP and ask an AI agent what your channels have said — see
   [Research with an AI agent](#research-with-an-ai-agent).

| Tab | Purpose |
|-----|---------|
| **Downloads** | Browse the library, play media, search transcripts, create clips, rate and tag; switch to grid view and group into folders by channel, tag, or date. The **Transcripts** view lists everything with a transcript, including transcript-only items |
| **Subscriptions** | Add and edit channel and playlist subscriptions and their filters |
| **Clips** | Manage clips created from your media, including those cut straight from YouTube for transcript-only items |
| **Playlists** | Create and order playlists, play them through, and create clips from any track |
| **Tasks** | Monitor active downloads and transcription jobs. A submitted URL appears as **Resolving**, then **Queued** with the real title. Unreleased videos (live streams, upcoming premieres, post-live processing) appear as **Not Released**; subscriptions retry them automatically, one-off downloads must be re-added once the video airs. A task awaiting an automatic retry shows what failed and when it will retry (`HTTP 403 (3/20): Retries in 4m`) |
| **Stats** | Storage on disk and transcripts' space in the database (text vs embeddings, by channel), download trends, transcription coverage, and engagement metrics |
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
| **Enabled** | The switch on each row in the Subscriptions table. Pauses scheduled checking without deleting the subscription or anything it downloaded. |

Filtered-out videos are recorded as skipped rather than re-examined every cycle. Loosening a filter
later re-evaluates them, so widening a duration range or clearing a title filter picks up the videos
it previously passed over. How often subscriptions are checked is set in **Settings**, not here.

To check one subscription right away instead of waiting for the schedule, press the bolt icon on its
row (**Check for new videos now**). It works on a subscription that is switched off too, so a disabled
subscription can serve as a manual-only one, and the videos it finds are queued ahead of scheduled
subscription downloads. Only the subscription's owner, or an admin, sees the button.

### Searching

The Downloads tab has two search boxes.

- **Search downloads** matches channel and title. Combine terms with `&&` (both) and `||` (either):
  `lofi && mix` needs both words, `cats || dogs` either one. `&&` binds tighter, so `a && b || c`
  reads as `(a && b) || c`. A single `&` or `|` is matched as text, so `Q&A` finds "Q&A".
- **Transcript search** finds passages by what was said, each linked to its moment. A slider sets the
  balance between exact words (**Keyword**) and meaning (**Semantic**). Results are narrowed by the
  downloads search, the open folder, the tag chips and the minimum rating.

### Transcripts

- **Watch with the transcript.** The **Transcript** button on any media page shows it beside the
  player (below it on a phone). The playing line is highlighted, clicking a line jumps there, and the
  search box filters to matching lines. Media without one offers **Generate transcript**.
- **The Transcripts view**, picked at the top of the Downloads tab, lists everything you can search:
  transcript-only items, library media with a transcript, and deleted media whose transcript you
  kept. It has the Library's grid, folders and bulk actions.
- **Transcript-only items** keep just the transcript and a thumbnail. The text comes from YouTube's
  English captions, or from Whisper run on a temporary audio download when there are none. They play
  through YouTube's privacy-enhanced embed (`youtube-nocookie.com`) and remember your position, but
  don't appear in the Library or playlists.
- **Clip without a file.** **Create Clip** under the embedded player saves a section as MP4 or MP3;
  the server fetches just that part from YouTube.

Good to know:

- Downloading a video in full replaces its transcript-only entry, so search never shows a passage
  twice.
- Deleting a transcript-only item deletes its transcript. Deleting library media offers to keep the
  transcript, which then shows as *Deleted* in the Transcripts view.
- Captions and Whisper are English-only. Whisper is CPU-heavy and runs one job at a time, so a large
  backlog of videos without captions takes a while.

### Offline mode

Audio and video can be saved to a phone and played with no connection, including the app itself
once it is installed to the home screen. Use the **download arrow** on an item, or the **refresh
icon** on a playlist or subscription to keep its newest items saved, then switch to offline mode
(the **signal icon** in the top bar, or **Offline mode** in the menu on a phone). While it is on,
the app only plays what is on the device.

The offline controls stay hidden until two things are true: the app is reached over **HTTPS** with a
trusted certificate (`tailscale serve --bg 8000` is the one-command option), and it is a
**production install** (`task prod` or `task published`, not dev mode). Setup, storage limits and
what works offline are in [`docs/OFFLINE.md`](docs/OFFLINE.md).

## Configuration

`setup.sh` configures the essentials. To change them later, edit `config.yml` and restart:

- **Storage paths** — where audio and video are saved on the host (`.env`: `AUDIO_ONLY_PATH`,
  `VIDEO_PATH`)
- **Whisper model** — `tiny.en` (fast, ~1 GB) up to `large` (best, ~10 GB)
  (`transcription.whisper_model`)
- **Release version** — `.env`: `YT_HOARDER_TAG`, `latest` by default; pin it to hold a version
  through updates
- **AI agent access (MCP)** — on in a new install (`mcp.enabled`); `setup.sh --no-mcp` leaves it off.
  Each user creates a token from the account menu → **MCP Access Tokens**, which shows the exact command to
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
