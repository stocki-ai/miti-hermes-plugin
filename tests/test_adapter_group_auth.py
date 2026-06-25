"""Tests for group @ owner auth mapping."""

import json
from pathlib import Path

from adapter_group_auth import group_session_user_ids, resolve_group_auth_user_id


def test_group_session_user_ids_owner_passes_other_sender():
    gateway_id, alt = group_session_user_ids("7380897508", "4170171283")
    assert gateway_id == "4170171283"
    assert alt == "7380897508"


def test_group_session_user_ids_owner_is_sender():
    gateway_id, alt = group_session_user_ids("4170171283", "4170171283")
    assert gateway_id == "4170171283"
    assert alt is None


def test_group_session_user_ids_no_owner():
    gateway_id, alt = group_session_user_ids("7380897508", "")
    assert gateway_id == "7380897508"
    assert alt is None


def test_resolve_group_auth_user_id_from_env():
    uid, source = resolve_group_auth_user_id("4170171283")
    assert uid == "4170171283"
    assert source == "env"


def test_resolve_group_auth_user_id_from_single_pairing(tmp_path: Path):
    pairing = tmp_path / "miti-approved.json"
    pairing.write_text(json.dumps({"4170171283": {"approved_at": 1}}), encoding="utf-8")
    uid, source = resolve_group_auth_user_id("", pairing_path=pairing)
    assert uid == "4170171283"
    assert source == "pairing"


def test_resolve_group_auth_user_id_ambiguous_pairing(tmp_path: Path):
    pairing = tmp_path / "miti-approved.json"
    pairing.write_text(
        json.dumps(
            {
                "4170171283": {"approved_at": 1},
                "7380897508": {"approved_at": 2},
            }
        ),
        encoding="utf-8",
    )
    uid, source = resolve_group_auth_user_id("", pairing_path=pairing)
    assert uid == ""
    assert source == ""
