# Links Garden

A searchable garden for links you save and notes you write.

It reads links from Signal Note to Self through Beeper, reads your Obsidian vault,
extracts what each link actually contains, sorts items into sets you define, and
indexes everything for search. Agents reach it over MCP.

See [DESIGN.md](DESIGN.md) for the full design.

## Requirements

- Python 3.12 or later, with [uv](https://docs.astral.sh/uv/)
- Node 20 or later
- [ollama](https://ollama.com), with `bge-m3` and `qwen3:8b` pulled
- Beeper Desktop, for Signal ingestion

## Setup

1. Copy the example environment file and fill it in.

   ```sh
   cp .env.example .env
   ```

2. Pull the models.

   ```sh
   ollama pull bge-m3 && ollama pull qwen3:8b
   ```

3. Install dependencies.

   ```sh
   uv sync
   ```

## Checks

```sh
uv run ruff format --check src tests
uv run ruff check src tests
uv run complexipy src tests
uv run mypy .
uv run pytest
```
