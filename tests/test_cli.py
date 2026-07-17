"""Judge-facing CLI tests."""

from typing import Any

from name_atlas import cli


def test_live_mode_fails_clearly_without_api_key(capsys: Any) -> None:
    exit_code = cli.run(["demo", "--mode", "live"], environ={})

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "configure OPENAI_API_KEY locally" in captured.err


def test_replay_mode_runs_on_loopback(monkeypatch: Any) -> None:
    called: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        called["app"] = app
        called.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    exit_code = cli.run(
        ["demo", "--mode", "replay", "--port", "8123"],
        environ={},
    )

    assert exit_code == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 8123
    assert called["app"].title == "Reversible Name Atlas"
