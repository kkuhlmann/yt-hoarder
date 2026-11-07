# Releasing

Maintainer runbook. End users don't need this — see [README.md](../README.md) to install.

**A `v*` tag is the release.** Pushing one builds that exact commit, publishes the image under the
matching version tags, and opens a GitHub Release page for it. There are two ways to create that
tag, and they end in the same place.

## How the pieces fit

A **git tag** is a pointer to a commit. A **GitHub Release** is a page wrapped around a tag — title,
notes, a "Latest" badge.

`.github/workflows/release.yml` listens for `push: tags: ["v*"]`. Both routes produce that event:

- **Terminal** — `git tag v0.1.0 && git push origin v0.1.0`. The workflow builds, then creates the
  Release page with auto-generated notes.
- **Web UI** — publishing a Release creates the tag, which fires the same workflow. It builds, sees
  the release already exists, and leaves your hand-written notes alone.

## Cutting a release

**Pre-flight**

1. CI is green on `main`.
2. `backend/pyproject.toml` `version` and `frontend/package.json` `version` match the version you're
   about to tag, minus the `v`. Nothing enforces this — it's on you.

**From a terminal**

```bash
git checkout main && git pull
git tag v0.1.0            # keep the leading v; the workflow matches on it
git push origin v0.1.0
```

The **Actions** tab shows a `Release` run. It takes a while — the arm64 leg builds under emulation.
When it finishes, the image is on GHCR and `/releases` has a `v0.1.0` page with a changelog built
from the PRs merged since the previous tag.

To tag something other than the tip of `main`, name the commit: `git tag v0.1.0 <sha>`.

**From the web UI** (use this when you want to write the notes yourself)

1. Repo → **Releases** (right sidebar, or `/releases`) → **Draft a new release**.
2. **Choose a tag** → type the new tag, e.g. `v0.1.0` → click **+ Create new tag: v0.1.0 on
   publish**. Keep the leading `v`.
3. **Target**: `main` (or a specific commit).
4. Title: the version, e.g. `v0.1.0`.
5. **Generate release notes** for the same changelog the terminal route produces, then edit freely.
6. Leave **Set as a pre-release** unchecked. Keep **Set as the latest release** checked.
7. **Publish release**. The tag is created at this point, which is what starts the build.

## What gets published

Tag `v0.1.0` produces one multi-arch manifest (linux/amd64 + linux/arm64) under six references:

```
v0.1.0   0.1.0   v0.1   0.1   latest   sha-<short>
```

All six point at the same digest. The `v` and bare forms both exist because the docs and `setup.sh`
name the `v` form while Docker convention is the bare one.

### Two different "latest", and they are unrelated

| | Set by | Means |
|---|---|---|
| GitHub **Latest** badge | the checkbox in the release form; automatic by version/date for a tag-push release | which release the `/releases/latest` page shows |
| Docker tag **`latest`** | metadata-action's `latest=auto` flavor | what `docker pull …:latest` resolves to |

Neither influences the other. `latest=auto` is why `release.yml` lists no explicit `latest` rule —
see the comment there before changing it.

### The `latest` footgun

`latest=auto` tags any non-prerelease semver release as `latest` **without comparing it to existing
versions**. Releasing a hotfix on an older line — `v0.1.5` after `v0.2.0` already shipped — moves
the Docker `latest` tag *backwards* onto older code, and `latest` is what `setup.sh` installs by
default. If that situation comes up, publish the hotfix as a pre-release, or re-run the workflow
against the newest tag afterwards to put `latest` back.

### Pre-releases

A hyphen in the tag is semver's prerelease marker, so `v0.2.0-rc.1` is one automatically: the
workflow marks the Release page as a pre-release, and metadata-action emits only the full version
(`v0.2.0-rc.1`, `0.2.0-rc.1`) — no `latest`, and no floating `v0.2`/`0.2`. From the UI, tick **Set
as a pre-release** as well.

## Verifying a release

```bash
docker buildx imagetools inspect ghcr.io/kkuhlmann/yt-hoarder:v0.1.0
```

Expect a manifest list with `linux/amd64` and `linux/arm64` children, plus `unknown/unknown` entries
(those are the provenance attestations, not a problem). `:0.1.0` and `:latest` must report the same
digest.

Then exercise the documented pin path in a scratch directory: set `YT_HOARDER_TAG=v0.1.0` in
`.env` and run `docker compose -f docker-compose.published.yml pull`.

Both commands must work while logged out, since `setup.sh`'s default published-install path pulls
anonymously. A `denied` means the package visibility has reverted to private (GitHub → Packages →
`yt-hoarder` → Package settings → Change visibility) — that switch is separate from the
repository's own.

## Fixing a botched release

Delete the Release *and* its tag, fix the problem, then re-tag the same version. GHCR overwrites an
existing image tag without complaint, and the workflow only skips release creation when a release
still exists — so deleting both is what makes the retry behave like a first attempt.

```bash
gh release delete v0.1.0 --cleanup-tag    # or delete both in the UI
git push origin :refs/tags/v0.1.0         # if the tag survived
git tag -d v0.1.0
```

**Do not** clean up GHCR with `actions/delete-package-versions` and `delete-only-untagged-versions`.
In a manifest list the per-arch children are themselves untagged package versions — only the index
carries the tag — so that sweep deletes the amd64/arm64 images out from under a working tag and
pulls start failing with `manifest unknown`. The retention note at the bottom of `release.yml` has
the detail.

## `workflow_dispatch`

Actions → Release → **Run workflow** builds from a branch. It pushes `<branch>` and `sha-<short>`
only — never `latest`, since there's no semver tag involved. Useful as a dry run of the build, or
for a one-off image, but it is not a release.

## Releasing the browser extension

The extension under `extension/` is versioned and released on its own, with **`extension-v*`
tags**. `release.yml` matches only `v*`, so neither kind of tag triggers the other's workflow.

1. Bump `version` in `extension/package.json` and run `npm install` there so the lockfile follows.
   The build copies that version into both manifests.
2. On `main`, after CI is green: `git tag extension-v0.1.0 && git push origin extension-v0.1.0`.
3. `.github/workflows/extension-release.yml` first checks that the tag matches `package.json`, then
   runs the extension's checks. It then opens a GitHub Release titled `Browser extension v0.1.0`
   carrying three zips: `yt-hoarder-chrome-0.1.0.zip`, `yt-hoarder-firefox-0.1.0.zip` and
   `yt-hoarder-source-0.1.0.zip`. A second hyphen (`extension-v0.2.0-rc.1`) makes it a pre-release.

Two things keep the two release streams apart on the Releases page:
- **The "Latest" badge.** The extension's releases are created with `--latest=false`, so the badge
  stays on the app release, which is what `/releases/latest` and the install docs point at.
- **The notes.** Each workflow passes `--notes-start-tag` for the previous release of its own kind,
  so app notes never start from an extension tag, and the reverse. The changelog still lists every
  PR merged in that range, whichever part of the repo it touched.

**The stores are uploaded by hand** from that release's zips:
- **Chrome Web Store:** upload the Chrome zip in the developer dashboard.
- **addons.mozilla.org:** upload the Firefox zip. When AMO asks for source code, upload the source
  zip, because the add-on is bundled with esbuild. The reviewer's build steps are in
  `extension/README.md`.
- **The gecko id** in `extension/src/manifest.json` (`yt-hoarder@kkuhlmann.github.io`) is permanent
  once the first version is on AMO.

## If the repo moves

`release.yml` derives the image name from `${{ github.repository }}` and follows a move on its own.
`ghcr.io/kkuhlmann/yt-hoarder` is hardcoded elsewhere and does not: `setup.sh`,
`docker-compose.published.yml`, `.env.sample`, and several files under `docs/`. Sweep for the
string before the first release from a new repo.
