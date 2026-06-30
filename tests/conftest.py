"""Pytest config: gateway stubs + path setup for top-level adapter imports."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_gateway_stubs() -> None:
    """Minimal gateway stubs so adapter.py can import without Hermes installed."""
    if "gateway.platforms.base" in sys.modules:
        return

    gateway = types.ModuleType("gateway")
    config_mod = types.ModuleType("gateway.config")

    class Platform:
        def __init__(self, value: str):
            self.value = value

    class PlatformConfig:
        pass

    config_mod.Platform = Platform
    config_mod.PlatformConfig = PlatformConfig
    gateway.config = config_mod

    session_mod = types.ModuleType("gateway.session")

    class SessionSource:
        pass

    session_mod.SessionSource = SessionSource
    gateway.session = session_mod

    base_mod = types.ModuleType("gateway.platforms.base")

    class MessageType:
        TEXT = "text"
        PHOTO = "photo"

    class SendResult:
        def __init__(self, success: bool, **kwargs):
            self.success = success

    class MessageEvent:
        pass

    class BasePlatformAdapter:
        def __init__(self, config, platform):
            self.config = config
            self.platform = platform
            self._running = False
            self._fatal_error_code = None
            self._fatal_error_message = None
            self._fatal_error_retryable = True
            self._fatal_error_handler = None

        def _mark_connected(self) -> None:
            self._running = True

        def _mark_disconnected(self) -> None:
            self._running = False

        @property
        def is_connected(self) -> bool:
            return self._running

        def _set_fatal_error(self, code, message, *, retryable: bool) -> None:
            self._fatal_error_code = code
            self._fatal_error_message = message
            self._fatal_error_retryable = retryable
            self._running = False

        async def _notify_fatal_error(self) -> None:
            handler = self._fatal_error_handler
            if handler:
                result = handler(self)
                if asyncio.iscoroutine(result):
                    await result

        def build_source(self, **kwargs):
            return kwargs

        async def handle_message(self, event):
            pass

    base_mod.BasePlatformAdapter = BasePlatformAdapter
    base_mod.MessageEvent = MessageEvent
    base_mod.MessageType = MessageType
    base_mod.SendResult = SendResult

    platforms_mod = types.ModuleType("gateway.platforms")
    platforms_mod.base = base_mod
    gateway.platforms = platforms_mod

    sys.modules["gateway"] = gateway
    sys.modules["gateway.config"] = config_mod
    sys.modules["gateway.session"] = session_mod
    sys.modules["gateway.platforms"] = platforms_mod
    sys.modules["gateway.platforms.base"] = base_mod


_install_gateway_stubs()
