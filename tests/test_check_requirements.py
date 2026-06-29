"""Tests for SDK / pip bootstrap in adapter.check_requirements."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

adapter = importlib.import_module("adapter")


@pytest.fixture(autouse=True)
def _clear_sdk_modules():
    for name in list(sys.modules):
        if name == "miti_agent_sdk" or name.startswith("miti_agent_sdk."):
            del sys.modules[name]
    yield


def test_bootstrap_pip_skips_when_pip_already_available():
    with patch.object(adapter, "_pip_is_available", return_value=True) as probe:
        assert adapter._bootstrap_pip() is True
    probe.assert_called_once()


def test_bootstrap_pip_runs_ensurepip_when_pip_missing():
    with (
        patch.object(adapter, "_pip_is_available", side_effect=[False, True]),
        patch.object(adapter.subprocess, "run") as run,
    ):
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert adapter._bootstrap_pip() is True

    ensurepip_cmd = run.call_args_list[0].args[0]
    assert ensurepip_cmd[-2:] == ["--upgrade", "--default-pip"]
    assert "ensurepip" in ensurepip_cmd


def test_check_requirements_bootstraps_pip_before_sdk_install():
    with (
        patch.object(adapter, "_bootstrap_pip", return_value=True) as bootstrap,
        patch.object(adapter.subprocess, "run") as run,
        patch.dict(sys.modules, {"miti_agent_sdk": MagicMock()}),
    ):
        # First import fails, second succeeds after pip install.
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name == "miti_agent_sdk":
                raise ImportError("missing")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            # Simulate successful import after pip install.
            import builtins

            calls = {"n": 0}

            def import_after_install(name, *args, **kwargs):
                if name == "miti_agent_sdk":
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise ImportError("missing")
                    return MagicMock()
                return real_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=import_after_install):
                assert adapter.check_requirements() is True

    bootstrap.assert_called_once()
    pip_cmd = run.call_args.args[0]
    assert pip_cmd[1:4] == ["-m", "pip", "install"]


def test_check_requirements_fails_when_ensurepip_fails():
    import builtins

    real_import = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name == "miti_agent_sdk":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    with (
        patch.object(builtins, "__import__", side_effect=no_sdk),
        patch.object(adapter, "_bootstrap_pip", return_value=False),
    ):
        assert adapter.check_requirements() is False
