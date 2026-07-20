"""Pure provider-limit contracts shared without importing API or budget code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from name_atlas.folder_refactor.planner_contracts import MAX_OUTPUT_TOKENS


@dataclass(frozen=True, slots=True)
class LivePlannerPolicy:
    """Fixed provider limits; SDK retries remain disabled."""

    timeout_seconds: float = 120.0
    max_output_tokens: int = MAX_OUTPUT_TOKENS
    sdk_max_retries: Literal[0] = 0
    reasoning_effort: Literal["medium"] = "medium"


DEFAULT_LIVE_PLANNER_POLICY = LivePlannerPolicy()
DEFAULT_LIVE_REVISION_POLICY = LivePlannerPolicy(max_output_tokens=8192)
