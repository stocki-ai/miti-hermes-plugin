"""
Miti Platform Adapter for Hermes Agent.

Connects Hermes to the Miti IM platform via miti-agent-sdk's outbound
WebSocket long-connection.  No public webhook endpoint is needed.

Supported message flows
-----------------------
- Single-chat (direct message):  Miti user → bot → Hermes → reply to user
- Group @mention:                 Miti group member @bot → Hermes → reply to group

chat_id convention
------------------
- Direct message : ``<miti_user_id>``          e.g. ``"u_abc123"``
- Group @mention : ``"group:<miti_group_id>"`` e.g. ``"group:sg_xyz789"``

Configuration (via hermes gateway setup or environment variables)
-----------------------------------------------------------------
Required:
  MITI_APP_ID        Agent App ID (create at Miti Discovery page → 连接智能体)
  MITI_APP_SECRET    Agent App Secret (view on agent detail page at any time)

Optional:
  MITI_API_BASE_URL    API base URL (default: https://www.miti.chat/chat)
  MITI_ALLOWED_USERS   Comma-separated Miti user IDs; empty = allow all
  MITI_ALLOW_ALL_USERS "true" to disable allowlist (dev only)
  MITI_HOME_CHANNEL    Default user ID for cron / notification delivery
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Minimum miti-agent-sdk version required by this plugin.
# Bump when the plugin depends on a newly added SDK feature.
_SDK_REQUIRE = "miti-agent-sdk>=0.1.0"


def check_requirements() -> bool:
    """Return True when a compatible miti-agent-sdk is importable.

    If not installed, attempts ``pip install _SDK_REQUIRE`` into the active
    Hermes venv (same lazy-install pattern as built-in platform adapters).

    Local development — install the SDK in editable mode (changes take effect
    immediately without reinstalling this plugin)::

        pip install -e /path/to/pai/miti-agent-sdk
    """
    try:
        import miti_agent_sdk  # noqa: F401
        return True
    except ImportError:
        pass

    logger.info("miti-platform: miti-agent-sdk not found, attempting pip install…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", _SDK_REQUIRE],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(
            "miti-platform: pip install %s failed:\n%s",
            _SDK_REQUIRE,
            result.stderr or result.stdout,
        )
        return False
    try:
        import miti_agent_sdk  # noqa: F401
        logger.info("miti-platform: %s installed successfully", _SDK_REQUIRE)
        return True
    except ImportError as exc:
        logger.error("miti-platform: import failed after pip install: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Lazy imports: only loaded after check_requirements() confirms availability.
# ---------------------------------------------------------------------------
def _import_sdk():
    from miti_agent_sdk import MitiAgent
    return MitiAgent


# ---------------------------------------------------------------------------
# Hermes base imports (always available inside the gateway process)
# ---------------------------------------------------------------------------
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent as HermesMessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig
from gateway.session import SessionSource

_GROUP_PREFIX = "group:"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class MitiAdapter(BasePlatformAdapter):
    """Hermes platform adapter for Miti IM.

    Internally wraps ``MitiAgent`` from miti-agent-sdk, running it as an
    asyncio background task so it does not block Hermes' event loop.
    """

    def __init__(self, config: PlatformConfig, **kwargs):
        platform = Platform("miti")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        self.app_id: str = os.getenv("MITI_APP_ID") or extra.get("app_id", "")
        self.app_secret: str = os.getenv("MITI_APP_SECRET") or extra.get("app_secret", "")
        self.api_base_url: str = (
            os.getenv("MITI_API_BASE_URL")
            or extra.get("api_base_url", "")
            or ""  # SDK picks its own default when empty
        )

        # Allowlist: comma-separated user IDs
        raw_allowed = os.getenv("MITI_ALLOWED_USERS") or extra.get("allowed_users", "")
        self._allowed_users: set[str] = (
            {u.strip() for u in raw_allowed.split(",") if u.strip()}
            if raw_allowed
            else set()
        )
        allow_all_raw = os.getenv("MITI_ALLOW_ALL_USERS", "").lower()
        self._allow_all: bool = allow_all_raw in {"1", "true", "yes"}

        # Runtime state
        self._miti_agent: Any = None
        self._gateway_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Miti"

    # ── Authorization ─────────────────────────────────────────────────────

    def _is_authorized(self, user_id: str) -> bool:
        """Return True when user_id is allowed to interact with the bot."""
        if self._allow_all or not self._allowed_users:
            return True
        return user_id in self._allowed_users

    # ── Connection lifecycle ───────────────────────────────────────────────

    async def connect(self) -> bool:
        """Validate config, create MitiAgent, and start the WebSocket task."""
        if not self.app_id or not self.app_secret:
            logger.error(
                "miti-platform: MITI_APP_ID and MITI_APP_SECRET must be set"
            )
            self._set_fatal_error(
                "config_missing",
                "MITI_APP_ID and MITI_APP_SECRET must be configured",
                retryable=False,
            )
            return False

        MitiAgent = _import_sdk()

        kwargs: dict[str, Any] = {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }
        if self.api_base_url:
            kwargs["base_url"] = self.api_base_url

        try:
            self._miti_agent = MitiAgent(**kwargs)
        except ValueError as exc:
            logger.error("miti-platform: %s", exc)
            self._set_fatal_error("tls_required", str(exc), retryable=False)
            return False

        # Register inbound event handlers
        self._miti_agent.on_message(self._on_single_chat)
        self._miti_agent.on_group_at(self._on_group_at)

        # Launch SDK as a background task — does not block Hermes
        self._gateway_task = asyncio.create_task(
            self._run_agent(), name="miti-gateway"
        )

        logger.info(
            "miti-platform: connecting (app_id=%s, base_url=%s)",
            self.app_id,
            self.api_base_url or "<sdk-default>",
        )
        return True

    async def _run_agent(self) -> None:
        """Run the MitiAgent until cancelled or a fatal error occurs."""
        try:
            await self._miti_agent.run_async(register_signals=False)
        except asyncio.CancelledError:
            logger.info("miti-platform: gateway task cancelled")
        except Exception as exc:
            logger.error("miti-platform: gateway task crashed: %s", exc, exc_info=True)

    async def disconnect(self) -> None:
        """Stop the WebSocket task and clean up SDK resources.

        Cancelling the gateway task triggers ``run_async``'s finally block,
        which calls ``MitiAgent.close()`` internally — no need to call it
        again here.  We only call ``close()`` explicitly when the task
        already finished on its own (e.g. fatal error) but ``disconnect``
        was not yet called.
        """
        if self._gateway_task and not self._gateway_task.done():
            self._gateway_task.cancel()
            try:
                await asyncio.wait_for(self._gateway_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        elif self._miti_agent:
            try:
                await self._miti_agent.close()
            except Exception as exc:
                logger.warning("miti-platform: error during agent close: %s", exc)
        logger.info("miti-platform: disconnected")

    # ── Inbound event handlers (Miti → Hermes) ────────────────────────────

    async def _on_single_chat(self, event) -> None:
        """Handle im.message.receive (direct message to bot)."""
        sender_id: str = event.event.sender.user_id
        if not sender_id:
            logger.warning("miti-platform: single-chat event missing sender user_id")
            return

        if not self._is_authorized(sender_id):
            logger.info(
                "miti-platform: ignoring message from unauthorized user %s", sender_id
            )
            return

        text = _extract_text(event.event.message)
        if not text:
            logger.debug(
                "miti-platform: skipping non-text single-chat message (msg_type=%s)",
                event.event.message.msg_type,
            )
            return

        chat_id = sender_id
        source = self.build_source(
            user_id=sender_id,
            chat_id=chat_id,
            chat_type="dm",
        )
        hermes_event = HermesMessageEvent(
            source=source,
            text=text,
            message_type=MessageType.TEXT,
        )
        logger.info(
            "miti-platform: single-chat message from %s: %r",
            sender_id,
            text[:80],
        )
        self.handle_message(hermes_event)

    async def _on_group_at(self, event) -> None:
        """Handle im.message.group_at (group @mention of bot)."""
        sender_id: str = event.event.sender.user_id
        group_id: str = event.event.group_id

        if not sender_id or not group_id:
            logger.warning(
                "miti-platform: group_at event missing sender or group_id"
            )
            return

        if not self._is_authorized(sender_id):
            logger.info(
                "miti-platform: ignoring group_at from unauthorized user %s", sender_id
            )
            return

        text = _extract_text(event.event.message)
        if not text:
            logger.debug(
                "miti-platform: skipping non-text group_at message (msg_type=%s)",
                event.event.message.msg_type,
            )
            return

        chat_id = f"{_GROUP_PREFIX}{group_id}"
        source = self.build_source(
            user_id=sender_id,
            chat_id=chat_id,
            chat_type="group",
        )
        hermes_event = HermesMessageEvent(
            source=source,
            text=text,
            message_type=MessageType.TEXT,
        )
        logger.info(
            "miti-platform: group_at in %s from %s: %r",
            group_id,
            sender_id,
            text[:80],
        )
        self.handle_message(hermes_event)

    # ── Outbound (Hermes → Miti) ──────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        text: str,
        *,
        source: Optional[SessionSource] = None,
        **kwargs,
    ) -> SendResult:
        """Send a text reply back to Miti."""
        if not self._miti_agent:
            logger.error("miti-platform: send() called before connect()")
            return SendResult(success=False)
        if self._gateway_task and self._gateway_task.done():
            logger.error("miti-platform: send() called but gateway task has exited")
            return SendResult(success=False)

        try:
            if chat_id.startswith(_GROUP_PREFIX):
                group_id = chat_id[len(_GROUP_PREFIX):]
                await self._miti_agent.send_message(
                    to_group_id=group_id,
                    msg_type="text",
                    content={"text": text},
                )
                logger.debug(
                    "miti-platform: sent to group %s: %r", group_id, text[:80]
                )
            else:
                await self._miti_agent.send_message(
                    to_user_id=chat_id,
                    msg_type="text",
                    content={"text": text},
                )
                logger.debug(
                    "miti-platform: sent to user %s: %r", chat_id, text[:80]
                )
            return SendResult(success=True)
        except Exception as exc:
            logger.error(
                "miti-platform: send failed (chat_id=%s): %s", chat_id, exc
            )
            return SendResult(success=False)

    async def send_typing(self, chat_id: str, **kwargs) -> None:
        """Miti does not have a typing indicator — no-op."""

    async def get_chat_info(self, chat_id: str) -> dict:
        """Return minimal chat metadata for Hermes session management."""
        if chat_id.startswith(_GROUP_PREFIX):
            group_id = chat_id[len(_GROUP_PREFIX):]
            return {
                "name": f"Group {group_id}",
                "type": "group",
                "chat_id": chat_id,
            }
        return {
            "name": chat_id,
            "type": "dm",
            "chat_id": chat_id,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(message) -> str:
    """Extract plain text from a Miti EventMessage.

    For ``text`` messages: ``content["text"]``.
    For ``at_text`` messages: strip the @bot mention prefix and return the rest.
    Other types (image, audio, …) are not yet supported — returns empty string.
    """
    msg_type: str = getattr(message, "msg_type", "")
    content: dict = getattr(message, "content", {}) or {}

    if msg_type == "text":
        return content.get("text", "").strip()

    if msg_type == "at_text":
        # at_text content: {"text": "@BotName actual message", "atUserList": [...]}
        raw = content.get("text", "")
        text = re.sub(r"^(@\S+\s*)+", "", raw).strip()
        return text

    return ""


# ---------------------------------------------------------------------------
# Interactive setup wizard
# ---------------------------------------------------------------------------

def interactive_setup() -> None:
    """Called by ``hermes gateway setup`` when the user selects Miti."""
    from hermes_cli.config import get_env_value, save_env_value

    print("\n  Miti Agent Setup")
    print("  ─────────────────────────────────────────────────────────")
    print("  1. Open the Miti app → Discovery page → top-left menu")
    print('  2. Tap "连接智能体" (Connect Agent) to create a new agent app')
    print("  3. Copy the App ID and App Secret shown after creation")
    print("     (Secret can be viewed again anytime on the detail page)")
    print()

    for var, prompt, secret in [
        ("MITI_APP_ID",     "App ID",     False),
        ("MITI_APP_SECRET", "App Secret", True),
    ]:
        existing = get_env_value(var)
        if existing:
            print(f"  {var} is already set — skipping")
            continue
        try:
            if secret:
                import getpass
                value = getpass.getpass(f"  {prompt}: ").strip()
            else:
                value = input(f"  {prompt}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Setup cancelled.")
            return
        if value:
            save_env_value(var, value)
            print(f"  ✓ {var} saved")

    print()
    print("  Setup complete. Run `hermes gateway run` to start.")
    print()


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Called by the Hermes plugin system during gateway initialisation."""
    ctx.register_platform(
        name="miti",
        label="Miti",
        adapter_factory=lambda cfg: MitiAdapter(cfg),
        check_fn=check_requirements,
        required_env=["MITI_APP_ID", "MITI_APP_SECRET"],
        allowed_users_env="MITI_ALLOWED_USERS",
        allow_all_env="MITI_ALLOW_ALL_USERS",
        cron_deliver_env_var="MITI_HOME_CHANNEL",
        install_hint="pip install miti-agent-sdk",
        setup_fn=interactive_setup,
        emoji="💬",
        platform_hint=(
            "You are chatting via Miti IM. Use plain text — Miti does not render "
            "markdown. In direct messages, reply directly to the user. "
            "In group chats, you were @mentioned; reply to the group. "
            "Keep responses concise and natural."
        ),
    )
