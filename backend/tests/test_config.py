"""Tests for the centralized configuration module."""

import os
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

# Import will load defaults since no config file exists during import
from config import (
    DatabaseSettings,
    EmbeddingSettings,
    LoggingSettings,
    McpSettings,
    Settings,
    StorageSettings,
    TasksSettings,
    TranscriptionSettings,
    _create_settings_from_yaml,
    get_settings,
    load_yaml_config,
)


class TestDefaultValues:
    """Test that default values are correctly set when no config exists."""

    def test_database_defaults(self):
        settings = DatabaseSettings()
        assert settings.url == 'postgresql://ytdl:ytdl@localhost:5432/ytdl_hoarder'

    def test_storage_defaults(self):
        settings = StorageSettings()
        assert settings.audio_path == '/mnt/audio'
        assert settings.video_path == '/mnt/video'

    def test_tasks_defaults(self):
        settings = TasksSettings()
        assert settings.purge_on_startup is False

    def test_transcription_defaults(self):
        settings = TranscriptionSettings()
        assert settings.whisper_model == 'tiny.en'
        assert settings.whisper_num_workers == 1

        # whisper_cpu_threads is derived from the host -- max(1, cpu_count - 1)
        # -- not a fixed literal, so assert the contract it promises instead of
        # a number. Pinning it to 4 only passed on a 5-core machine.
        cpu_count = os.cpu_count() or 2
        assert settings.whisper_cpu_threads >= 1
        if cpu_count > 1:
            assert settings.whisper_cpu_threads < cpu_count

    def test_embedding_defaults(self):
        settings = EmbeddingSettings()
        assert settings.model == 'all-MiniLM-L6-v2'

    def test_logging_defaults(self):
        settings = LoggingSettings()
        assert settings.level == 'INFO'


class TestYamlLoading:
    """Test loading configuration from YAML files."""

    def test_load_yaml_config_file_not_found(self, tmp_path):
        """When no config file exists, return empty dict."""
        assert load_yaml_config((tmp_path / 'config.yml',)) == {}

    def test_load_yaml_config_from_file(self, tmp_path):
        """Test loading from a temporary YAML file."""
        config_content = {
            'database': {'url': 'postgresql://test:test@testhost:5432/testdb'},
            'tasks': {'purge_on_startup': True},
        }
        config_path = tmp_path / 'config.yml'
        config_path.write_text(yaml.dump(config_content))

        assert load_yaml_config((config_path,)) == config_content

        settings = _create_settings_from_yaml(load_yaml_config((config_path,)))
        assert settings.database.url == 'postgresql://test:test@testhost:5432/testdb'
        assert settings.tasks.purge_on_startup is True
        # Defaults should still be used for unspecified values
        assert settings.storage.audio_path == '/mnt/audio'

    def test_first_readable_path_wins(self, tmp_path):
        first = tmp_path / 'first.yml'
        second = tmp_path / 'second.yml'
        first.write_text(yaml.dump({'logging': {'level': 'DEBUG'}}))
        second.write_text(yaml.dump({'logging': {'level': 'ERROR'}}))

        assert load_yaml_config((first, second)) == {'logging': {'level': 'DEBUG'}}

    def test_directory_at_config_path_is_skipped(self, tmp_path, capsys):
        """Docker materializes a missing bind-mount source as a directory.

        open() on it raises IsADirectoryError at import time, which crash-loops the
        container forever under `restart: unless-stopped`.
        """
        stray_dir = tmp_path / 'config.yml'
        stray_dir.mkdir()
        real = tmp_path / 'fallback.yml'
        real.write_text(yaml.dump({'logging': {'level': 'DEBUG'}}))

        assert load_yaml_config((stray_dir, real)) == {'logging': {'level': 'DEBUG'}}
        assert 'is not a file' in capsys.readouterr().err

    def test_directory_only_falls_back_to_defaults(self, tmp_path):
        stray_dir = tmp_path / 'config.yml'
        stray_dir.mkdir()

        assert load_yaml_config((stray_dir,)) == {}

    def test_empty_config_file_yields_empty_dict(self, tmp_path):
        """yaml.safe_load returns None for an empty document."""
        config_path = tmp_path / 'config.yml'
        config_path.write_text('')

        assert load_yaml_config((config_path,)) == {}

    def test_create_settings_from_yaml_partial_config(self):
        """Test that partial YAML config uses defaults for missing values."""
        partial_config = {
            'database': {'url': 'postgresql://custom:custom@db:5432/custom'},
            # Other sections not specified
        }

        settings = _create_settings_from_yaml(partial_config)

        assert settings.database.url == 'postgresql://custom:custom@db:5432/custom'
        # All other values should be defaults
        assert settings.tasks.purge_on_startup is False
        assert settings.logging.level == 'INFO'

    def test_create_settings_from_empty_config(self):
        """Test that empty YAML config uses all defaults."""
        settings = _create_settings_from_yaml({})

        assert settings.database.url == 'postgresql://ytdl:ytdl@localhost:5432/ytdl_hoarder'
        assert settings.tasks.purge_on_startup is False

    def test_tasks_block(self):
        """The tasks: block populates TasksSettings."""
        settings = _create_settings_from_yaml({'tasks': {'purge_on_startup': True}})
        assert settings.tasks.purge_on_startup is True

    def test_retired_tasks_keys_are_ignored(self):
        """Lane widths and the subscription cadence moved to app_settings. A config.yml
        left carrying them must stay inert rather than failing to load."""
        settings = _create_settings_from_yaml(
            {
                'tasks': {
                    'schedule_frequency_minutes': 3,
                    'default_concurrency': 4,
                    'purge_on_startup': True,
                }
            }
        )
        assert settings.tasks.purge_on_startup is True
        assert not hasattr(settings.tasks, 'schedule_frequency_minutes')
        assert not hasattr(settings.tasks, 'default_concurrency')

    def test_unknown_yaml_blocks_are_ignored(self):
        """Unrecognized top-level sections in config.yml must not break loading."""
        settings = _create_settings_from_yaml({'some_future_section': {'key': 'value'}})
        assert settings.database.url == 'postgresql://ytdl:ytdl@localhost:5432/ytdl_hoarder'
        assert settings.tasks.purge_on_startup is False


class TestEnvironmentVariableOverrides:
    """Test that environment variables override YAML and defaults."""

    def test_database_env_override(self):
        """Environment variable should override default database URL."""
        with patch.dict(os.environ, {'DATABASE__URL': 'postgresql://env:env@envhost:5432/envdb'}):
            settings = DatabaseSettings()
            assert settings.url == 'postgresql://env:env@envhost:5432/envdb'

    def test_tasks_env_override(self):
        """Environment variables should override tasks settings."""
        with patch.dict(os.environ, {'TASKS__PURGE_ON_STARTUP': 'true'}):
            settings = TasksSettings()
            assert settings.purge_on_startup is True

    def test_transcription_env_override(self):
        """Environment variables should override transcription settings."""
        with patch.dict(
            os.environ,
            {
                'TRANSCRIPTION__WHISPER_MODEL': 'medium.en',
                'TRANSCRIPTION__WHISPER_CPU_THREADS': '8',
            },
        ):
            settings = TranscriptionSettings()
            assert settings.whisper_model == 'medium.en'
            assert settings.whisper_cpu_threads == 8

    def test_logging_env_override(self):
        """Environment variable should override logging level."""
        with patch.dict(os.environ, {'LOGGING__LEVEL': 'DEBUG'}):
            settings = LoggingSettings()
            assert settings.level == 'DEBUG'

    def test_yaml_values_take_precedence_over_env(self):
        """YAML config values passed to constructor take precedence over env vars.

        This is pydantic-settings behavior: explicit constructor values beat env vars.
        Env vars only apply when values are not passed to the constructor (i.e., defaults).
        """
        yaml_config = {
            'database': {'url': 'postgresql://yaml:yaml@yamlhost:5432/yamldb'},
        }

        with patch.dict(os.environ, {'DATABASE__URL': 'postgresql://env:env@envhost:5432/envdb'}):
            settings = _create_settings_from_yaml(yaml_config)
            # YAML value wins when passed to constructor (pydantic-settings behavior)
            assert settings.database.url == 'postgresql://yaml:yaml@yamlhost:5432/yamldb'


class TestSettingsCaching:
    """Test that settings are properly cached."""

    def test_get_settings_returns_settings_instance(self):
        """get_settings should return a Settings instance."""
        # Clear the cache first
        get_settings.cache_clear()

        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_get_settings_is_cached(self):
        """Multiple calls to get_settings should return the same instance."""
        get_settings.cache_clear()

        settings1 = get_settings()
        settings2 = get_settings()

        assert settings1 is settings2


class TestNestedSettingsStructure:
    """Test that nested settings are properly structured."""

    def test_settings_has_all_sections(self):
        """Settings should have all expected nested sections."""
        settings = Settings()

        assert hasattr(settings, 'database')
        assert hasattr(settings, 'storage')
        assert hasattr(settings, 'tasks')
        assert hasattr(settings, 'transcription')
        assert hasattr(settings, 'embedding')
        assert hasattr(settings, 'logging')
        assert hasattr(settings, 'mcp')

    def test_nested_settings_types(self):
        """Nested settings should be of correct types."""
        settings = Settings()

        assert isinstance(settings.database, DatabaseSettings)
        assert isinstance(settings.storage, StorageSettings)
        assert isinstance(settings.tasks, TasksSettings)
        assert isinstance(settings.transcription, TranscriptionSettings)
        assert isinstance(settings.embedding, EmbeddingSettings)
        assert isinstance(settings.logging, LoggingSettings)
        assert isinstance(settings.mcp, McpSettings)


class TestMcpSettings:
    def test_off_by_default(self):
        mcp = McpSettings()

        assert mcp.enabled is False
        assert mcp.public_url == ''

    def test_read_from_yaml(self):
        settings = _create_settings_from_yaml(
            {'mcp': {'enabled': True, 'public_url': 'https://hoarder.example.com/'}}
        )

        assert settings.mcp.enabled is True
        assert settings.mcp.public_url == 'https://hoarder.example.com'

    def test_env_fills_in_what_yaml_leaves_unset(self):
        with patch.dict(os.environ, {'MCP__ENABLED': 'true'}):
            assert McpSettings().enabled is True

    @pytest.mark.parametrize(
        'url', ['nas:8000', 'ftp://nas', 'https://nas/?x=1', 'https://nas/#frag', 'https://']
    )
    def test_public_url_must_be_a_plain_web_origin(self, url):
        with pytest.raises(ValidationError):
            McpSettings(public_url=url)

    def test_public_url_may_carry_a_path_prefix(self):
        assert McpSettings(public_url=' http://nas:8000/hoarder/ ').public_url == (
            'http://nas:8000/hoarder'
        )
