"""Runtime configuration that never retains credential values."""

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from name_atlas.domain import RunMode

MODEL_ALIAS = "gpt-5.6"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class RuntimeConfig(BaseModel):
    """Validated, safe-to-display application configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: RunMode
    model: Literal["gpt-5.6"] = MODEL_ALIAS
    host: Literal["127.0.0.1"] = LOOPBACK_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65_535)
    api_key_configured: bool
    replay_record_configured: bool = False

    @classmethod
    def from_environment(
        cls,
        *,
        mode: RunMode,
        port: int = DEFAULT_PORT,
        environ: Mapping[str, str],
        replay_record_configured: bool = False,
    ) -> "RuntimeConfig":
        """Create safe runtime state without retaining the API-key value."""

        api_key_configured = bool(environ.get("OPENAI_API_KEY", "").strip())
        return cls(
            mode=mode,
            port=port,
            api_key_configured=api_key_configured,
            replay_record_configured=replay_record_configured,
        )

    @property
    def provider_status(self) -> str:
        """Return a truthful user-facing provider status."""

        if self.mode is RunMode.LIVE:
            if self.api_key_configured:
                return "Live GPT-5.6 provider ready for a user-requested call"
            return "Live GPT-5.6 provider blocked: OPENAI_API_KEY is not configured"
        if self.replay_record_configured:
            return "Recorded GPT-5.6 response"
        return "Replay provider configured; recorded response not yet captured"

    def safe_diagnostics(self) -> dict[str, str | int | bool]:
        """Return startup diagnostics that cannot expose a credential."""

        return {
            "mode": self.mode.value,
            "model": self.model,
            "host": self.host,
            "port": self.port,
            "api_key_configured": self.api_key_configured,
            "replay_record_configured": self.replay_record_configured,
            "provider_status": self.provider_status,
        }
