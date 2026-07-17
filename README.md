# Reversible Name Atlas

**Refactor the collection. Preserve every identity.**

Reversible Name Atlas is a local-first migration workbench for previewing and
proving canonical renames across linked digital collections. Product
implementation is in progress for OpenAI Build Week.

## Foundation commands

Requires Python 3.11 and [`uv`](https://docs.astral.sh/uv/).

```text
uv sync --frozen
uv run name-atlas demo --mode replay
```

The application binds only to `127.0.0.1`. Replay mode currently opens the
foundation shell and truthfully reports that no validated GPT-5.6 recording has
yet been captured.

Live mode requires `OPENAI_API_KEY` to be configured in the launching process.
Configure it locally; never paste it into chat or commit it:

```text
uv run name-atlas demo --mode live
```

Without a configured key, live mode exits before starting a server and never
silently substitutes replay or another model.

## Verification

```text
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The supported product contract, claims, and current build state live in
[`docs/build/BUILD_SPEC.md`](docs/build/BUILD_SPEC.md) and
[`docs/build/STATE.md`](docs/build/STATE.md).
