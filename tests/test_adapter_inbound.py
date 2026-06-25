"""Tests for multimodal inbound parsing."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
