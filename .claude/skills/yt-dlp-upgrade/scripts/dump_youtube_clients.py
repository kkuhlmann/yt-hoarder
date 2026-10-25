"""Print yt-dlp's YouTube client table and default client lists as stable JSON.

Run it once under each yt-dlp version and diff the two outputs:

    uv run --no-project --isolated --with 'yt-dlp[default]==2026.10.21' \\
        python -I .claude/skills/yt-dlp-upgrade/scripts/dump_youtube_clients.py \\
        --app-models backend/app/models.py > new.json

The table is read after yt-dlp's build_innertube_clients() has filled in every default, so a
client that inherits a policy reads the same as one that states it. Source paths for the
extractor files go to stderr, not into the JSON, so the two dumps diff cleanly.

--app-models adds an `app_check` section comparing the app's VALID_PLAYER_CLIENTS and default
lists against this version's table.
"""

import argparse
import ast
import importlib.metadata
import json
import os
import re
import sys

from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import _base, _video
from yt_dlp.version import __version__

REQUIREMENTS_OF_INTEREST = ('certifi', 'curl-cffi', 'deno', 'yt-dlp-ejs')
DEFAULT_LISTS = (
    '_DEFAULT_CLIENTS',
    '_DEFAULT_JSLESS_CLIENTS',
    '_DEFAULT_AUTHED_CLIENTS',
    '_DEFAULT_PREMIUM_CLIENTS',
)
APP_LISTS = ('VALID_PLAYER_CLIENTS', 'DEFAULT_PLAYER_CLIENTS', 'DEFAULT_COOKIES_PLAYER_CLIENTS')


def _required(policy) -> bool | None:
    return getattr(policy, 'required', None)


def _client(cfg: dict) -> dict:
    gvs = cfg.get('GVS_PO_TOKEN_POLICY') or {}
    return {
        'client_name': cfg['INNERTUBE_CONTEXT']['client'].get('clientName'),
        'gvs_pot_required': {proto.value: _required(policy) for proto, policy in gvs.items()},
        'player_pot_required': _required(cfg.get('PLAYER_PO_TOKEN_POLICY')),
        'subs_pot_required': _required(cfg.get('SUBS_PO_TOKEN_POLICY')),
        'require_js_player': cfg.get('REQUIRE_JS_PLAYER'),
        'supports_cookies': cfg.get('SUPPORTS_COOKIES'),
        'require_auth': cfg.get('REQUIRE_AUTH'),
    }


def _requirements() -> list[str]:
    lines = importlib.metadata.requires('yt-dlp') or []
    wanted = []
    for line in lines:
        name = re.match(r'[A-Za-z0-9_.-]+', line)
        if name and name.group(0).lower() in REQUIREMENTS_OF_INTEREST:
            wanted.append(line)
    return sorted(wanted)


def _app_lists(models_path: str) -> dict[str, list[str]]:
    with open(models_path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in APP_LISTS:
                found[target.id] = ast.literal_eval(node.value)
    return found


def _app_check(models_path: str, clients: dict) -> dict:
    app = _app_lists(models_path)
    valid = app.get('VALID_PLAYER_CLIENTS', [])
    anonymous = app.get('DEFAULT_PLAYER_CLIENTS', [])
    cookies = app.get('DEFAULT_COOKIES_PLAYER_CLIENTS', [])
    return {
        'missing_from_app': sorted(set(clients) - set(valid)),
        'gone_upstream': sorted(set(valid) - set(clients)),
        'defaults_not_valid': sorted(set(anonymous + cookies) - set(valid)),
        'cookie_defaults_without_cookie_support': [
            c for c in cookies if c in clients and not clients[c]['supports_cookies']
        ],
        'app_lists': app,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--app-models', help="path to the app's backend/app/models.py")
    args = parser.parse_args()

    clients = {
        name: _client(cfg) for name, cfg in _base.INNERTUBE_CLIENTS.items() if name[:1] != '_'
    }
    dump = {
        'yt_dlp_version': __version__,
        'defaults': {
            name: list(getattr(_video.YoutubeIE, name, None) or []) or None
            for name in DEFAULT_LISTS
        },
        'clients': clients,
        'requirements': _requirements(),
        'app_couplings': {
            'YoutubeDL._get_available_impersonate_targets': hasattr(
                YoutubeDL, '_get_available_impersonate_targets'
            ),
            'STREAMING_DATA_CLIENT_NAME': getattr(_video, 'STREAMING_DATA_CLIENT_NAME', None),
        },
    }
    if args.app_models:
        dump['app_check'] = _app_check(args.app_models, clients)

    json.dump(dump, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write('\n')

    pot_dir = os.path.join(os.path.dirname(_base.__file__), 'pot')
    ydl_file = sys.modules['yt_dlp.YoutubeDL'].__file__
    for label, path in (
        ('_base', _base.__file__),
        ('_video', _video.__file__),
        ('pot', pot_dir),
        ('YoutubeDL', ydl_file),
    ):
        sys.stderr.write(f'source {label}: {path}\n')


if __name__ == '__main__':
    main()
