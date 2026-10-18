"""
Centralized configuration module using pydantic-settings.

Configuration priority (highest to lowest):
1. config.yml
2. Environment variables, using double underscore as nested delimiter
   (DATABASE__URL, TASKS__PURGE_ON_STARTUP)
3. Field defaults defined below

Env vars fill in only what config.yml leaves unset: _create_settings_from_yaml
passes YAML values as constructor kwargs, which pydantic-settings ranks above
its environment source, so a key present in config.yml always wins.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value shipped in config.sample.yml. Compared against at startup to refuse
# boot, so it is a sentinel rather than an embedded credential.
DEFAULT_INSECURE_SECRET_KEY = 'change-me-in-production'  # noqa: S105

DEFAULT_CONFIG_SEARCH_PATHS = (
    Path('/etc/app/config.yml'),  # Docker container mount point
    Path('./config.yml'),
    Path('../config.yml'),  # Project root when running from backend/app
    Path('../../config.yml'),  # When running tests from backend/tests
)


def load_yaml_config(search_paths: tuple[Path, ...] | None = None) -> dict[str, Any]:
    """
    Load configuration from the first readable config.yml in the search paths.

    Args:
        search_paths: Override the locations to search, first found wins.
            Defaults to DEFAULT_CONFIG_SEARCH_PATHS.

    Returns:
        The parsed YAML mapping, or an empty dict if no config file was found.
    """
    for path in search_paths if search_paths is not None else DEFAULT_CONFIG_SEARCH_PATHS:
        if path.is_file():
            with open(path) as f:
                return yaml.safe_load(f) or {}
        if path.exists():
            # Compose materializes a missing bind-mount source as a directory. Skipping
            # it keeps this module importable — open() on a directory raises
            # IsADirectoryError at import time, which crash-loops the container forever
            # under `restart: unless-stopped` with nothing but a traceback to go on.
            sys.stderr.write(
                f'[config] {path} exists but is not a file, so it was skipped. '
                'Docker creates a directory here when the bind-mount source is missing; '
                f'remove it with `rmdir {path.name}` and create a real config file.\n'
            )

    sys.stderr.write(
        '[config] No config.yml found — using environment variables and defaults. '
        'If you are running under Docker without DATABASE__URL set, the default '
        'database URL points at localhost and will not reach the postgres service. '
        'Create a config with `cp config.sample.yml config.yml`.\n'
    )
    return {}


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='DATABASE__',
        extra='ignore',
    )

    url: str = Field(
        default='postgresql://ytdl:ytdl@localhost:5432/ytdl_hoarder',
        description='PostgreSQL connection URL',
    )


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='STORAGE__',
        extra='ignore',
    )

    audio_path: str = Field(default='/mnt/audio', description='Path to audio files storage')
    video_path: str = Field(default='/mnt/video', description='Path to video files storage')


class TasksSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='TASKS__',
        extra='ignore',
    )

    purge_on_startup: bool = Field(
        default=False,
        description='Cancel pending tasks on startup instead of resuming them (True for dev)',
    )


class TranscriptionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='TRANSCRIPTION__',
        extra='ignore',
    )

    whisper_model: str = Field(
        default='tiny.en',
        description='Whisper model size (tiny.en, small.en, medium.en, large)',
    )
    whisper_cpu_threads: int = Field(
        default_factory=lambda: max(1, (os.cpu_count() or 2) - 1),
        description='CPU threads for Whisper inference (defaults to available CPUs minus 1)',
    )
    whisper_num_workers: int = Field(default=1, description='Number of Whisper worker processes')


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='EMBEDDING__',
        extra='ignore',
    )

    model: str = Field(
        default='all-MiniLM-L6-v2',
        description='Embedding model for semantic search (only all-MiniLM-L6-v2 is supported)',
    )


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='AUTH__',
        extra='ignore',
    )

    secret_key: str = Field(
        default=DEFAULT_INSECURE_SECRET_KEY,
        description='Secret key for JWT signing. Set auth.secret_key in config.yml.',
    )
    jwt_expiry_days: int = Field(default=30, description='JWT token expiry in days')
    algorithm: str = Field(default='HS256', description='JWT signing algorithm')
    cookie_secure: bool = Field(
        default=False,
        description=(
            'Set the Secure flag on the auth cookie. Defaults to False so plain-HTTP '
            'self-hosts keep working; enable when serving over HTTPS.'
        ),
    )
    allowed_origins: list[str] = Field(
        default=[],
        description=(
            'Origins allowed to make credentialed cross-origin API calls, dev mode only. '
            'Empty allows any origin, so the dev UI works from any address without being '
            'configured for it; list origins to enforce a strict allowlist instead. The '
            'production image serves the frontend same-origin and ignores this.'
        ),
    )


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='LOGGING__',
        extra='ignore',
    )

    level: str = Field(default='INFO', description='Log level (DEBUG, INFO, WARNING, ERROR)')


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='MCP__',
        extra='ignore',
    )

    enabled: bool = Field(
        default=False,
        description=(
            'Serve the MCP endpoint that lets AI agents search transcripts with a '
            'personal access token. Off by default: while disabled the endpoint 404s '
            'and no token can be created.'
        ),
    )
    public_url: str = Field(
        default='',
        description=(
            'The address you open ytdl-hoarder at (e.g. https://hoarder.example.com), '
            'used to build the in-app links in search results. Empty derives it from '
            'the address the agent connected to, which is wrong behind a proxy that '
            'rewrites the Host header.'
        ),
    )

    @field_validator('public_url')
    @classmethod
    def _validate_public_url(cls, value: str) -> str:
        value = value.strip().rstrip('/')
        if not value:
            return ''
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            msg = 'mcp.public_url must be an http:// or https:// URL'
            raise ValueError(msg)
        if parsed.query or parsed.fragment:
            msg = 'mcp.public_url must not carry a query string or fragment'
            raise ValueError(msg)
        return value


class Settings(BaseSettings):
    """
    Configuration priority (highest to lowest):
    1. config.yml file
    2. Environment variables (DATABASE__URL, TASKS__PURGE_ON_STARTUP, etc.),
       which apply only to keys config.yml does not set
    3. Default values defined in this module
    """

    model_config = SettingsConfigDict(
        extra='ignore',
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    tasks: TasksSettings = Field(default_factory=TasksSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    mcp: McpSettings = Field(default_factory=McpSettings)


def _create_settings_from_yaml(yaml_config: dict[str, Any]) -> Settings:
    """Build each nested settings section from its YAML key, so pydantic-settings
    still applies per-section environment variable overrides on top."""
    database = DatabaseSettings(**(yaml_config.get('database') or {}))
    storage = StorageSettings(**(yaml_config.get('storage') or {}))
    transcription = TranscriptionSettings(**(yaml_config.get('transcription') or {}))
    embedding = EmbeddingSettings(**(yaml_config.get('embedding') or {}))
    auth = AuthSettings(**(yaml_config.get('auth') or {}))
    logging = LoggingSettings(**(yaml_config.get('logging') or {}))
    mcp = McpSettings(**(yaml_config.get('mcp') or {}))

    tasks = TasksSettings(**(yaml_config.get('tasks') or {}))

    return Settings(
        database=database,
        storage=storage,
        tasks=tasks,
        transcription=transcription,
        embedding=embedding,
        auth=auth,
        logging=logging,
        mcp=mcp,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    yaml_config = load_yaml_config()
    return _create_settings_from_yaml(yaml_config)


settings = get_settings()
