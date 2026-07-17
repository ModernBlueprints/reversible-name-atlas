"""Runtime configuration tests."""

import pytest
from pydantic import ValidationError

from name_atlas.config import RuntimeConfig
from name_atlas.domain import RunMode


def test_replay_mode_does_not_require_api_key() -> None:
    config = RuntimeConfig.from_environment(mode=RunMode.REPLAY, environ={})

    assert config.api_key_configured is False
    assert "recorded response not yet captured" in config.provider_status
    assert "OPENAI_API_KEY" not in str(config.safe_diagnostics())


def test_live_mode_reports_key_presence_without_retaining_value() -> None:
    config = RuntimeConfig.from_environment(
        mode=RunMode.LIVE,
        environ={"OPENAI_API_KEY": "configured-only-for-this-test"},
    )

    assert config.api_key_configured is True
    assert config.model == "gpt-5.6"
    assert "configured-only-for-this-test" not in str(config)


def test_runtime_rejects_non_loopback_host() -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(
            mode=RunMode.REPLAY,
            host="0.0.0.0",
            api_key_configured=False,
        )
