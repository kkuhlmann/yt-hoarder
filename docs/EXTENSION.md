# Browser extension

The YT-Hoarder extension sends the YouTube page you're on to your own server. You can download the
video, or the whole playlist it's playing in, as video, audio or transcript only. You can also
subscribe to the channel or playlist. The popup also lists your running tasks and how far along they
are. One extension covers Chrome and Firefox. It signs in with a **browser-extension access token**,
which can add downloads and subscriptions and read the progress of your tasks, and nothing else.

**Requirements:**
- YT-Hoarder **0.3.0** or later on the server, the first version that accepts extension tokens. An
  older server makes the connection test say so.
- Chrome, Edge, Brave or another Chromium browser.
- Or **Firefox 140** or later (142 on Android).

## Install

Each release is a GitHub Release tagged `extension-vX.Y.Z`. It carries `yt-hoarder-chrome-X.Y.Z.zip`
and `yt-hoarder-firefox-X.Y.Z.zip`.

- **Chrome and other Chromium browsers:**
  1. Unzip the Chrome zip.
  2. Open `chrome://extensions` and turn on **Developer mode**.
  3. Click **Load unpacked** and choose the unzipped folder.
- **Firefox:** release Firefox installs only add-ons signed by Mozilla. Until the extension is listed
  on addons.mozilla.org, load it as a temporary add-on:
  1. Open `about:debugging#/runtime/this-firefox`.
  2. Click **Load Temporary Add-on…** and pick the Firefox zip.

  Firefox removes it when it restarts. Firefox Developer Edition and Nightly can install the zip
  permanently once `xpinstall.signatures.required` is set to `false` in `about:config`.

To build it from source instead, see [`extension/README.md`](../extension/README.md).

## Set up

1. **Create a token.** In YT-Hoarder, open the account menu → **Access Tokens** and choose
   **Browser extension**. Name it after the browser it's for, and click **Create**. Copy the token
   and the **Server URL** the dialog shows. The token is shown only once.
2. **Enter them in the extension.** Click the extension's toolbar icon, then **Settings**. Paste both
   and click **Save & test**.
3. **Allow access to your server.** The browser asks permission for the extension to contact your
   server's address. It can't reach any site you haven't approved. It needs no access to YouTube
   itself: it reads the address of the current tab only when you click its icon.
4. The settings page then shows `Connected as <you> · token "<name>" · server v<version>`.

The server URL can be the address you open YT-Hoarder at (`https://hoarder.example.com`) or the API
address (`https://hoarder.example.com/api`, which is what the token dialog shows). The extension
finds the API under either one. On a dev-mode install the dialog shows the web app's
`http://<host>:3000`; the API's `http://<host>:8000` works too, and is where the extension finds the
API when given the first.

## Use

Click the extension's icon on a YouTube page.

| Page | Download | Subscribe |
|---|---|---|
| A video (`watch?v=`, Shorts, `youtu.be`, live) | ✓ | — |
| A video playing in a playlist | ✓, this video or the whole playlist | ✓, the playlist |
| A playlist | ✓, the whole playlist | ✓ |
| A channel (`/@name`, `/channel/…`, `/c/…`, `/user/…`, including tabs such as `/videos`) | — | ✓ |

**Subscriptions are offered only for addresses YT-Hoarder's subscription job accepts.** A channel
can't be read from a video page, so on a plain video the popup asks you to open the channel's page
first.

Every option the app's own forms have is in the popup, so each download can differ from the last.
When a page offers both actions (a playlist, or a video inside one), **Download** and **Subscribe**
are separate tabs, and the popup opens on the one you used last. The subscription filters sit in a
collapsed **Filters** group.

- **Both sections:**
  - **Video**, **Audio** or **Transcript only**. Transcript-only items are searchable but keep no
    media file, so they don't count toward a storage limit.
  - **Quality**: a resolution (Best down to 360p) for video, or a maximum bitrate (Best down to
    48 kbps) for audio. Transcript only has none.
  - **Also transcribe**, to transcribe a video or audio download.
- **Downloads also take Overwrite**, to download again what you already have. Subscriptions don't
  offer it: on a subscription it replaces every video of the channel you already have, on every
  check. Turn it on from the web app if that is what you want.
- **Subscriptions also take filters:**
  - a **title filter**, which keeps only uploads whose title contains that text;
  - **uploaded after**, which skips anything released on or before that date;
  - a **minimum and maximum length**, in minutes.
- **The popup remembers** the type, quality and transcribe setting you last used in each section, and
  the last tab.
  Overwrite and the subscription filters start empty every time: they belong to one video or
  channel, and a remembered Overwrite would quietly replace files on later downloads.
- **What happens next:**
  - A download shows up in the popup's task list at once, and in the app's **Tasks** tab.
  - A subscription is added in the background and appears under **Subscriptions** once YT-Hoarder
    has looked it up.
  - The server's own messages appear as they are: a download that already exists, or a cancelled
    download still holding its place in the queue.

## Task progress

The popup's **Tasks** section lists everything you have running, queued or retrying, plus whatever
finished or failed in the last hour. It covers downloads and transcripts started anywhere: from the
extension, the web app or a subscription.

- **Each row** shows the title, what it's doing and how far along: a progress bar with the percentage
  and time left for a download, "Queued", "Processing…", "Starts in 45s" while a download waits out
  the rate limit, or "Retrying in 3m" after a failure.
- **It updates every 3 seconds** while something is running or queued, and every 10 seconds
  otherwise, including while a task only waits to retry or for a premiere. It updates only while the
  popup is open: the extension keeps nothing running in the background, so it has no toolbar badge
  or notifications.
- **It shows up to 25 tasks:** what is running and queued, in the order it will run, then what
  finished most recently. Up to 10 of those places are kept for finished tasks, so a long queue
  can't hide how your last download went. The **Tasks** tab in the app has the rest.
- **Only your own tasks appear,** even if you're an admin.
- **If the server can't be reached** the list keeps what it had and tries again, waiting longer after
  each failure. If the token is rejected it stops asking and says why.

## What an extension token can do

- **Add a download, add a subscription, list your task progress, and check that it works. Nothing
  else:**
  - It can't list your library, read transcripts, or change or delete anything, with one exception:
    a download with **Overwrite** replaces the copy you already have. It can't add a subscription
    with overwrite on. The task list holds titles, channels and progress, and no files or links.
  - It can't use the MCP endpoint, and an MCP token doesn't work in the extension.
  - Extension tokens can be created while the MCP server is turned off.
- **It acts as you,** including your storage limit, and its downloads and subscriptions are yours.
- **It stops working when:**
  - it reaches its optional expiry;
  - you revoke it;
  - your password changes;
  - an admin revokes all your tokens;
  - your account is waiting for approval or has to change its password.

  The token list shows which of these applies.
- **It's stored in the extension's local storage** in your browser profile, and never synced to your
  browser account. Revoke it if that profile is shared or lost.
- **Limits:** 60 requests a minute per token for adding and checking, and another 60 for the task
  list, so keeping the popup open never uses up the allowance for starting downloads. Repeated failed
  sign-ins from one address are locked out for a few minutes.

## Troubleshooting

- **"The server rejected the token."** Possible causes:
  - it was revoked or has expired;
  - your password changed;
  - it's an MCP token rather than a browser-extension token;
  - your account is blocked.

  Check its status under **Access Tokens**, and create a new one if needed.
- **"…doesn't accept browser-extension tokens. Update it to 0.3.0 or later."** The server predates
  extension tokens. Update it.
- **"The server didn't answer like YT-Hoarder."** Usually a sign-in proxy in front of the app, such
  as Authelia, Authentik or basic auth, answering instead of YT-Hoarder. The proxy has to let these
  paths through to the app, since the token authenticates them:
  - `/api/ytdl/`
  - `/api/subscriptions`
  - `/api/extension/`

  It also has to pass the `Authorization` header through unchanged. A proxy that itself uses
  `Authorization` (basic auth) can't share those paths with the token.
- **"The server redirected the request."** Enter the address it redirects to, usually `https://`
  rather than `http://`. A redirect would drop the token, so the extension doesn't follow it.
- **"The extension isn't allowed to contact …"** Open the settings, click **Save & test** again, and
  accept the permission prompt.
- **A warning about plain HTTP.** Over `http://` the token crosses the network unencrypted. Serve the
  app over HTTPS when the browser reaches it across a network you don't trust; see
  [OFFLINE.md](OFFLINE.md#1-https-from-any-trusted-certificate).
- **A subscription never appears.** The subscription job failed after accepting the request. The
  reason is in the server's logs.
