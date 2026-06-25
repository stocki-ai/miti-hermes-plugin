"""Tests for multimodal inbound parsing."""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adapter_inbound as adapter_inbound_mod
from adapter_inbound import (
    DEFAULT_IMAGE_PROMPT,
    DEFAULT_IMAGES_PROMPT,
    parse_inbound_message,
)


class _Msg:
    def __init__(self, msg_type: str, content: dict):
        self.msg_type = msg_type
        self.content = content


def test_multimodal_text_and_one_image():
    msg = _Msg(
        "multimodal",
        {
            "text": "这是什么？",
            "images": [{"url": "https://cdn/a.jpg", "mime_type": "image/jpeg"}],
        },
    )
    payload = parse_inbound_message(msg)
    assert payload is not None
    assert payload.text == "这是什么？"
    assert payload.image_urls == ["https://cdn/a.jpg"]
    assert payload.has_images is True


def test_image_only_gets_default_prompt():
    msg = _Msg(
        "image",
        {"images": [{"url": "https://cdn/a.jpg"}]},
    )
    payload = parse_inbound_message(msg)
    assert payload.text == DEFAULT_IMAGE_PROMPT


def test_image_multiple_gets_default_prompt():
    msg = _Msg(
        "image",
        {
            "images": [
                {"url": "https://cdn/a.jpg"},
                {"url": "https://cdn/b.jpg"},
            ]
        },
    )
    payload = parse_inbound_message(msg)
    assert payload.text == DEFAULT_IMAGES_PROMPT
    assert len(payload.image_urls) == 2


def test_text_only_still_works():
    msg = _Msg("text", {"text": "hello"})
    payload = parse_inbound_message(msg)
    assert payload.text == "hello"
    assert not payload.has_images


def test_download_images_uses_direct_await():
    """cache_image_from_url is async; must not be wrapped in asyncio.to_thread."""
    src = inspect.getsource(adapter_inbound_mod.download_images_to_cache)
    assert "await cache_image_from_url" in src
    assert "to_thread" not in src


def test_trusted_miti_image_url():
    assert adapter_inbound_mod._is_trusted_miti_image_url(
        "https://t1.miti.chat/api/object/u1/pic.jpg"
    )
    assert adapter_inbound_mod._is_trusted_miti_image_url(
        "https://www.miti.chat/api/object/u1/pic.jpg"
    )
    assert not adapter_inbound_mod._is_trusted_miti_image_url(
        "https://evil.example.com/pic.jpg"
    )
