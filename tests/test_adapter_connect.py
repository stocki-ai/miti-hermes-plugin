"""Tests for MitiAdapter.connect() Hermes 0.17+ compatibility."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def adapter_module():
    if "adapter" in sys.modules:
        return importlib.reload(sys.modules["adapter"])
    return importlib.import_module("adapter")


@pytest.fixture
def miti_adapter(adapter_module):
    cfg = MagicMock()
    cfg.extra = {}
    with patch.dict(
        "os.environ",
        {"MITI_APP_ID": "app_test", "MITI_APP_SECRET": "secret_test"},
        clear=False,
    ):
        return adapter_module.MitiAdapter(cfg)


@pytest.mark.asyncio
async def test_connect_accepts_is_reconnect_kwarg(miti_adapter, adapter_module):
    mock_agent = MagicMock()
    mock_agent.on_message = MagicMock()
    mock_agent.on_group_at = MagicMock()
    mock_agent.run_async = AsyncMock()

    with (
        patch.object(adapter_module, "_import_sdk", return_value=lambda **kw: mock_agent),
        patch.object(miti_adapter, "_resolve_group_auth_user_id", return_value=""),
    ):
        assert await miti_adapter.connect(is_reconnect=False) is True
        assert miti_adapter.is_connected is True

        await miti_adapter.disconnect()
        assert miti_adapter.is_connected is False

        assert await miti_adapter.connect(is_reconnect=True) is True
        assert miti_adapter.is_connected is True


@pytest.mark.asyncio
async def test_connect_signature_has_is_reconnect(miti_adapter):
    sig = inspect.signature(miti_adapter.connect)
    assert "is_reconnect" in sig.parameters
    assert sig.parameters["is_reconnect"].default is False


@pytest.mark.asyncio
async def test_gateway_task_crash_sets_fatal_error(miti_adapter, adapter_module):
    mock_agent = MagicMock()
    mock_agent.on_message = MagicMock()
    mock_agent.on_group_at = MagicMock()

    async def boom(**kwargs):
        raise RuntimeError("ws dropped")

    mock_agent.run_async = boom

    with (
        patch.object(adapter_module, "_import_sdk", return_value=lambda **kw: mock_agent),
        patch.object(miti_adapter, "_resolve_group_auth_user_id", return_value=""),
    ):
        assert await miti_adapter.connect() is True
        await asyncio.wait_for(miti_adapter._gateway_task, timeout=2.0)

    assert miti_adapter._fatal_error_code == "gateway_crashed"
    assert miti_adapter._fatal_error_retryable is True
