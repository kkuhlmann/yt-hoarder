import pytest

from config import settings


@pytest.fixture
def mcp_on(monkeypatch):
    monkeypatch.setattr(settings.mcp, 'enabled', True)
