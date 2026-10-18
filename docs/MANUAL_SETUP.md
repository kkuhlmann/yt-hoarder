# Manual setup

`setup.sh` performs these steps automatically. Follow them only to configure an install by hand.

These steps assume a checkout. Without one, download
[`docker-compose.published.yml`](../docker-compose.published.yml),
[`docker-compose.common.yml`](../docker-compose.common.yml),
[`.env.sample`](../.env.sample) and [`config.sample.yml`](../config.sample.yml) into an empty
directory first — the published file `extends` the common one and will not start without it, and
step 1 copies the two samples.

```bash
# 1. Copy the samples
cp .env.sample .env
cp config.sample.yml config.yml

# 2. Set your media storage paths in .env
#    AUDIO_ONLY_PATH=/path/to/your/audio/library
#    VIDEO_PATH=/path/to/your/video/library

# 3. Generate a JWT secret and paste it into config.yml (auth.secret_key)
#    Required — the backend refuses to start while this is the sample value.
python -c "import secrets; print(secrets.token_hex(32))"

# 4. Start it — pick one
docker compose -f docker-compose.published.yml up -d      # published release, no build (recommended)
docker compose -f docker-compose.prod.yml up -d --build   # build prod from source: single container :8000
docker compose -f docker-compose.dev.yml up -d            # build dev from source: :3000 + :8000, hot reload
# With Task installed: `task published`, `task prod`, or `task up` (dev)

# Later, to update the published release:
docker compose -f docker-compose.published.yml pull && docker compose -f docker-compose.published.yml up -d
# With Task installed: `task update`
```

To install a specific release rather than the newest, set `YT_HOARDER_TAG` in `.env` (for example
`v0.1.0`) before starting. See [CONFIGURATION.md](CONFIGURATION.md#release-pinning) for the caveats
around pinning backwards.
