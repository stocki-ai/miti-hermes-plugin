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
  MITI_APP_ID        Agent App ID (create at Miti 我的 / 设置 → 智能体管理 → 连接智能体)
  MITI_APP_SECRET    Agent App Secret (view on agent detail page at any time)

Optional:
  MITI_API_BASE_URL    API base URL (default: https://www.miti.chat/chat)
  MITI_OWNER_USER_ID   Optional override for group @ Gateway auth (auto from pairing if unset)
  MITI_ALLOWED_USERS   Comma-separated Miti user IDs; empty = allow all (plugin layer)
  MITI_ALLOW_ALL_USERS "true" to skip Hermes pairing for all Miti traffic (dev)
  MITI_HOME_CHANNEL    Default user ID for cron / notification delivery
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Minimum miti-agent-sdk version required by this plugin.
# Bump when the plugin depends on a newly added SDK feature.
_SDK_REQUIRE = "miti-agent-sdk>=0.1.0"


def _pip_is_available() -> bool:
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
        return probe.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _bootstrap_pip() -> bool:
    """Ensure ``python -m pip`` works in the active Hermes venv.

    Some Hermes installs (notably Windows / uv-created venvs) ship without
    pip. Mirrors the ensurepip ladder in Hermes core (``lazy_deps``).
    """
    if _pip_is_available():
        return True

    logger.info("miti-platform: pip not found in venv, bootstrapping via ensurepip…")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ensurepip", "--upgrade", "--default-pip"],
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        logger.error("miti-platform: ensurepip timed out")
        return False

    if result.returncode != 0:
        logger.error(
            "miti-platform: ensurepip failed:\n%s",
            result.stderr or result.stdout,
        )
        return False

    if not _pip_is_available():
        logger.error("miti-platform: pip still unavailable after ensurepip")
        return False

    logger.info("miti-platform: pip bootstrapped successfully")
    return True


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
    if not _bootstrap_pip():
        return False

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


def _parse_inbound_message(message) -> Optional[tuple[str, bool, list[str], list[str]]]:
    """Return (text, has_images, image_urls, mime_types) or None if unsupported."""
    from .adapter_inbound import parse_inbound_message

    payload = parse_inbound_message(message)
    if payload is None:
        return None
    return payload.text, payload.has_images, payload.image_urls, payload.mime_types


async def _cache_remote_images(
    urls: list[str], mime_types: list[str]
) -> tuple[list[str], list[str]]:
    from .adapter_inbound import download_images_to_cache

    return await download_images_to_cache(urls, mime_types)


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

        self._owner_user_id_config: str = (
            os.getenv("MITI_OWNER_USER_ID") or extra.get("owner_user_id", "")
        ).strip()
        self._group_auth_user_id: str = ""
        self._group_auth_source: str = ""

        # Runtime state
        self._miti_agent: Any = None
        self._gateway_task: Optional[asyncio.Task] = None
        # chat_id → last inbound message_id (for stream_full ask_msg_id)
        self._ask_msg_by_chat: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "Miti"

    # ── Authorization ─────────────────────────────────────────────────────

    def _is_authorized(self, user_id: str) -> bool:
        """Return True when user_id is allowed to interact with the bot."""
        if self._allow_all or not self._allowed_users:
            return True
        return user_id in self._allowed_users

    def _resolve_group_auth_user_id(self) -> str:
        """Owner/paired user for group @ Gateway auth (env or auto from pairing file)."""
        if self._group_auth_user_id:
            return self._group_auth_user_id
        from .adapter_group_auth import resolve_group_auth_user_id

        uid, source = resolve_group_auth_user_id(self._owner_user_id_config)
        self._group_auth_user_id = uid
        self._group_auth_source = source
        return uid

    # ── Connection lifecycle ───────────────────────────────────────────────

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        """Validate config, create MitiAgent, and start the WebSocket task.

        Hermes 0.17+ passes ``is_reconnect`` on gateway watcher retries.
        Miti has no server-side update queue — the flag is accepted and ignored.
        """
        if self._gateway_task is not None:
            await self.disconnect()

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
        group_auth = self._resolve_group_auth_user_id()
        if group_auth:
            logger.info(
                "miti-platform: group @ auth user=%s (from %s)",
                group_auth,
                self._group_auth_source,
            )
        elif not self._allow_all:
            logger.warning(
                "miti-platform: no group @ auth user (set MITI_OWNER_USER_ID, "
                "pair exactly one miti user, or MITI_ALLOW_ALL_USERS=true)"
            )
        self._mark_connected()
        if is_reconnect:
            logger.info("miti-platform: reconnected")
        return True

    async def _run_agent(self) -> None:
        """Run the MitiAgent until cancelled or a fatal error occurs."""
        try:
            await self._miti_agent.run_async(register_signals=False)
        except asyncio.CancelledError:
            logger.info("miti-platform: gateway task cancelled")
        except Exception as exc:
            logger.error("miti-platform: gateway task crashed: %s", exc, exc_info=True)
            self._set_fatal_error("gateway_crashed", str(exc), retryable=True)
            await self._notify_fatal_error()

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
        self._gateway_task = None
        self._miti_agent = None
        self._mark_disconnected()
        logger.info("miti-platform: disconnected")

    # ── Inbound event handlers (Miti → Hermes) ────────────────────────────

    async def _dispatch_inbound(
        self,
        event,
        *,
        chat_id: str,
        sender_id: str,
        chat_type: str,
        log_label: str,
    ) -> None:
        """Parse inbound message, download images, forward to Hermes."""
        parsed = _parse_inbound_message(event.event.message)
        if not parsed:
            logger.info(
                "miti-platform: skipping unsupported %s message (msg_type=%s)",
                log_label,
                getattr(event.event.message, "msg_type", "?"),
            )
            return

        text, has_images, image_urls, mime_types = parsed
        msg_id = getattr(event.event.message, "message_id", "") or ""
        if msg_id:
            self._ask_msg_by_chat[chat_id] = msg_id

        media_urls: list[str] = []
        media_types: list[str] = []
        if has_images:
            media_urls, media_types = await _cache_remote_images(
                image_urls, mime_types
            )
            if not media_urls:
                logger.error(
                    "miti-platform: all image downloads failed for %s from %s",
                    log_label,
                    chat_id,
                )
                await self.send(
                    chat_id,
                    "Sorry, I couldn't download the image. Please try again or resend it.",
                    reply_to=msg_id or None,
                )
                return

        gateway_user_id = sender_id
        user_id_alt: Optional[str] = None
        if chat_type == "group":
            group_auth = self._resolve_group_auth_user_id()
            if group_auth:
                from .adapter_group_auth import group_session_user_ids

                gateway_user_id, user_id_alt = group_session_user_ids(
                    sender_id, group_auth
                )

        source = self.build_source(
            user_id=gateway_user_id,
            user_id_alt=user_id_alt,
            chat_id=chat_id,
            chat_type=chat_type,
        )
        hermes_type = MessageType.PHOTO if has_images else MessageType.TEXT
        hermes_event = HermesMessageEvent(
            source=source,
            text=text,
            message_type=hermes_type,
            media_urls=media_urls,
            media_types=media_types,
            message_id=msg_id or None,
        )
        log_text = text[:80] if text else ""
        if has_images:
            logger.info(
                "miti-platform: %s from %s: text=%r images=%d",
                log_label,
                chat_id,
                log_text,
                len(media_urls),
            )
        else:
            logger.info(
                "miti-platform: %s from %s: %r",
                log_label,
                chat_id,
                log_text,
            )
        if chat_type == "group" and user_id_alt:
            logger.debug(
                "miti-platform: group @ auth via %s, session user %s",
                gateway_user_id,
                user_id_alt,
            )
        await self.handle_message(hermes_event)

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

        await self._dispatch_inbound(
            event,
            chat_id=sender_id,
            sender_id=sender_id,
            chat_type="dm",
            log_label="single-chat message",
        )

    async def _on_group_at(self, event) -> None:
        """Handle im.message.group_at (group @mention of bot)."""
        sender_id: str = event.event.sender.user_id
        group_id: str = event.event.group_id

        if not sender_id or not group_id:
            logger.warning(
                "miti-platform: group_at event missing sender or group_id"
            )
            return

        # Group @: skip plugin allowlist when a paired auth user is available.
        group_auth = self._resolve_group_auth_user_id()
        if not group_auth and not self._is_authorized(sender_id):
            logger.info(
                "miti-platform: ignoring group_at from unauthorized user %s", sender_id
            )
            return

        await self._dispatch_inbound(
            event,
            chat_id=f"{_GROUP_PREFIX}{group_id}",
            sender_id=sender_id,
            chat_type="group",
            log_label="group_at message",
        )

    # ── Outbound (Hermes → Miti) ──────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a Markdown reply back to Miti as stream_full (contentType 125)."""
        if not self._miti_agent:
            logger.error("miti-platform: send() called before connect()")
            return SendResult(success=False)
        if self._gateway_task and self._gateway_task.done():
            logger.error("miti-platform: send() called but gateway task has exited")
            return SendResult(success=False)

        try:
            from miti_agent_sdk.stream import build_stream_full_markdown

            ask_msg_id = (
                (metadata or {}).get("ask_msg_id")
                or reply_to
                or self._ask_msg_by_chat.get(chat_id, "")
            )
            payload = build_stream_full_markdown(content, ask_msg_id)

            if chat_id.startswith(_GROUP_PREFIX):
                group_id = chat_id[len(_GROUP_PREFIX):]
                await self._miti_agent.send_message(
                    to_group_id=group_id,
                    msg_type="stream_full",
                    content=payload,
                )
                logger.debug(
                    "miti-platform: sent stream_full to group %s: %r",
                    group_id,
                    content[:80],
                )
            else:
                await self._miti_agent.send_message(
                    to_user_id=chat_id,
                    msg_type="stream_full",
                    content=payload,
                )
                logger.debug(
                    "miti-platform: sent stream_full to user %s: %r",
                    chat_id,
                    content[:80],
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
# Helpers (legacy — prefer adapter_inbound.parse_inbound_message)
# ---------------------------------------------------------------------------

def interactive_setup() -> None:
    """Called by ``hermes gateway setup`` when the user selects Miti."""
    from hermes_cli.config import get_env_value, save_env_value

    print("\n  Miti Agent Setup")
    print("  ─────────────────────────────────────────────────────────")
    print("  1. Open the Miti app → My → Settings → Agent Management")
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
# Configuration status
# ---------------------------------------------------------------------------

def is_connected(config) -> bool:
    """Return True when Miti credentials are configured.

    This is intentionally separate from ``check_requirements()`` so
    ``hermes gateway setup`` can show "configured" based on saved credentials
    even when the SDK has not been installed into the Hermes venv yet.
    """
    extra = getattr(config, "extra", {}) or {}
    app_id = (os.getenv("MITI_APP_ID") or extra.get("app_id", "")).strip()
    app_secret = (os.getenv("MITI_APP_SECRET") or extra.get("app_secret", "")).strip()
    return bool(app_id and app_secret)


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
        is_connected=is_connected,
        required_env=["MITI_APP_ID", "MITI_APP_SECRET"],
        allowed_users_env="MITI_ALLOWED_USERS",
        allow_all_env="MITI_ALLOW_ALL_USERS",
        cron_deliver_env_var="MITI_HOME_CHANNEL",
        install_hint="pip install miti-agent-sdk",
        setup_fn=interactive_setup,
        emoji="💬",
        platform_hint=(
            "You are chatting via Miti IM. Use Markdown for formatting — "
            "replies are rendered in the App. Users may send images; you "
            "can see them via vision. In direct messages, reply directly to "
            "the user. In group chats, you were @mentioned; reply to the "
            "group. Keep responses concise and natural."
        ),
    )
