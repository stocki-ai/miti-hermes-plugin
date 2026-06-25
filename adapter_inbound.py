"""Inbound message parsing for Miti → Hermes (text, image, multimodal)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Used when the user sends image(s) without accompanying text.
DEFAULT_IMAGE_PROMPT = "请描述这张图片的内容。"
DEFAULT_IMAGES_PROMPT = "请描述这些图片的内容。"

# Pure image vs text+image(s); both carry images[] in content.
_IMAGE_MSG_TYPES = frozenset({"image", "multimodal"})


def _extract_image_specs(content: dict) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    for img in content.get("images") or []:
        if not isinstance(img, dict):
            continue
        url = (img.get("url") or "").strip()
        if not url:
            continue
        mime = (img.get("mime_type") or "image/jpeg").strip()
        specs.append((url, mime))
    return specs


@dataclass
class InboundPayload:
    text: str
    image_urls: list[str]
    mime_types: list[str]
    has_images: bool


def parse_inbound_message(message) -> Optional[InboundPayload]:
    """Parse EventMessage into text + image URLs for Hermes."""
    msg_type: str = getattr(message, "msg_type", "") or ""
    content = getattr(message, "content", {}) or {}
    if not isinstance(content, dict):
        return None

    text = ""
    image_specs: list[tuple[str, str]] = []

    if msg_type in _IMAGE_MSG_TYPES:
        text = (content.get("text") or "").strip()
        image_specs = _extract_image_specs(content)
    elif msg_type == "text":
        text = (content.get("text") or "").strip()
    elif msg_type == "at_text":
        raw = content.get("text") or ""
        text = re.sub(r"^(@\S+\s*)+", "", raw).strip()
    else:
        return None

    if image_specs and not text:
        text = (
            DEFAULT_IMAGES_PROMPT
            if len(image_specs) > 1
            else DEFAULT_IMAGE_PROMPT
        )

    if not text and not image_specs:
        return None

    urls = [u for u, _ in image_specs]
    mimes = [m for _, m in image_specs]
    return InboundPayload(
        text=text,
        image_urls=urls,
        mime_types=mimes,
        has_images=bool(image_specs),
    )


async def download_images_to_cache(
    urls: list[str],
    mime_types: list[str],
) -> tuple[list[str], list[str]]:
    """Download remote image URLs into Hermes local image cache."""
    from gateway.platforms.base import cache_image_from_bytes, cache_image_from_url

    paths: list[str] = []
    types: list[str] = []
    for i, url in enumerate(urls):
        mime = mime_types[i] if i < len(mime_types) else "image/jpeg"
        ext = _mime_to_ext(mime)
        try:
            if _is_trusted_miti_image_url(url):
                # Miti CDN/API hosts often resolve to 198.18.x (Clash fake-ip);
                # Hermes SSRF blocks those via cache_image_from_url.
                path = await _download_trusted_miti_image(url, ext, cache_image_from_bytes)
            else:
                path = await cache_image_from_url(url, ext)
            paths.append(path)
            types.append(mime)
            logger.debug("miti-platform: cached image %s -> %s", url[:80], path)
        except Exception as exc:
            logger.error(
                "miti-platform: failed to cache image %s: %s", url[:80], exc
            )
    return paths, types


def _miti_api_host() -> str:
    base = (os.getenv("MITI_API_BASE_URL") or "").strip()
    if not base:
        return ""
    return (urlparse(base).hostname or "").lower()


def _is_trusted_miti_image_url(url: str) -> bool:
    """True for Miti object/CDN URLs we intentionally fetch for vision."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host == "miti.chat" or host.endswith(".miti.chat"):
        return True
    api_host = _miti_api_host()
    return bool(api_host and host == api_host)


async def _download_trusted_miti_image(url: str, ext: str, cache_fn) -> str:
    import httpx

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0; MitiPlatform)",
                "Accept": "image/*,*/*;q=0.8",
            },
        )
        response.raise_for_status()
        return cache_fn(response.content, ext)


def _mime_to_ext(mime: str) -> str:
    mime = (mime or "").lower().split(";")[0].strip()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }
    if mime in mapping:
        return mapping[mime]
    if "/" in mime:
        return "." + mime.split("/")[-1]
    return ".jpg"
