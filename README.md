# Links Garden

A searchable garden for the links you save and the notes you write.

It reads links from Signal Note to Self through Beeper, reads your Obsidian vault, extracts what
each link actually contains, sorts items into sets you define, and indexes everything for search.
A dashboard shows it. Your agents reach it over MCP.

See [DESIGN.md](DESIGN.md) for the design and the reasoning behind it.

## Requirements

| | |
| --- | --- |
| Python 3.12+ with [uv](https://docs.astral.sh/uv/) | Always |
| Node 20+ with npm | For the dashboard |
| [ollama](https://ollama.com) | For search, summaries and set extraction |
| Beeper Desktop | Only for Signal ingestion |
| A [Firecrawl](https://firecrawl.dev) key | Only for fetching link contents |

Ollama, Beeper and Firecrawl are each optional. Without them you lose the feature that uses them,
not the app.

## Install with Docker

One image serves both the API and the built dashboard, so this is the whole install for a
container host — no Node, no Python, no `npm run dev`. Ollama and Beeper Desktop still run on
the host either way; see [Requirements](#requirements).

```sh
curl -O https://raw.githubusercontent.com/cestoliv/links-garden/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/cestoliv/links-garden/main/.env.example
# edit .env: at minimum API_TOKEN, see Configure below. VAULT_PATH, if set, is the host's
# absolute path to your vault; docker-compose.yml mounts it into the container read-only.

docker compose up -d
```

Open `http://127.0.0.1:8000` and sign in with `API_TOKEN`. Run a CLI command, such as a sync,
with `docker compose exec garden garden sync-vault`.

To update to the latest published image:

```sh
docker compose pull
docker compose up -d
```

## Install from source

```sh
git clone git@github.com:cestoliv/links-garden.git
cd links-garden

uv sync                                        # Python dependencies
ollama pull bge-m3 && ollama pull qwen3:8b     # about 6.4 GB
cd frontend && npm ci && cd ..                 # dashboard dependencies

cp .env.example .env                           # then edit it, see below
```

## Configure

Every setting has a working default except these four. Set only the ones whose feature you want.

| Setting | Needed for | How to get it |
| --- | --- | --- |
| `API_TOKEN` | The API, MCP and the dashboard | Invent one. Any long random string. The API refuses to start without it. |
| `VAULT_PATH` | Indexing Obsidian | The absolute path to your vault |
| `FIRECRAWL_API_KEY` | Fetching link contents | From firecrawl.dev. Without it the app fetches directly from your own IP and warns at startup. |
| `BEEPER_ACCESS_TOKEN` and `BEEPER_CHAT_ID` | Signal ingestion | See below |
| `BACKFILL_START_DATE` | Bounding the first Signal run | A date like `2026-08-25`. Without it, every run walks your entire chat history. |

### Finding your Beeper chat ID

Create an access token in Beeper Desktop's settings, put it in `.env`, then ask Beeper which chat
you want:

```sh
curl -s -H "Authorization: Bearer $BEEPER_ACCESS_TOKEN" \
  'http://127.0.0.1:23373/v1/chats?accountIDs=signal&limit=100' \
  | python3 -c 'import sys,json;[print(c["id"], "|", c.get("title")) for c in json.load(sys.stdin)["items"]]'
```

Copy the id of the chat you want into `BEEPER_CHAT_ID`. It looks like
`!sje8CuisVpV6iqz6aXDX:beeper.local`.

## Use it

Run these in order the first time. Each is safe to re-run: nothing is fetched, embedded or
enriched twice.

```sh
uv run garden sync-vault      # read the vault and follow the links inside notes
uv run garden sync-signal     # read Signal, mark captured messages with a checkmark
uv run garden index           # chunk and embed, about 50s per 100 documents
uv run garden enrich          # summaries, keywords and set membership, about 50s per document
uv run garden extract         # fill each matched set's schema
uv run garden search "your query"
```

Define a set before enriching, or nothing gets classified:

```sh
uv run garden sets add recipe \
  --description "Cooking recipes with ingredients and steps" \
  --schema recipe-schema.json
```

`uv run garden --help` lists everything.

### Costs and limits

- `enrich` is the slow one, roughly 50 seconds per document, run locally and free.
- Fetching costs Firecrawl credits. `MAX_FETCHES_PER_RUN` caps a single run at 50, and every
  response is cached, so re-running a sync costs nothing for links already seen.
- `uv run garden credits` shows what is left.

## The dashboard

This is the from-source dev flow, with the dashboard on its own dev server. The Docker image
serves the built dashboard from the API's own origin instead; see Install with Docker above.

```sh
uv run garden serve            # API on 127.0.0.1:8000
cd frontend && npm run dev     # dashboard on http://localhost:5174
```

Open `http://localhost:5174` and sign in with your `API_TOKEN`. The token is held in memory for the
session and is never written to storage.

If the API runs on another port, tell the dev server:

```sh
GARDEN_API_URL=http://127.0.0.1:9000 npm run dev
```

A port mismatch shows up as a rejected token, not a connection error, so check the port before
suspecting the token.

## Agents

```sh
uv run garden mcp              # MCP server on 127.0.0.1:8001
```

It speaks streamable HTTP and uses the same `API_TOKEN`. Five tools: `search_garden`,
`get_document`, `list_set_records`, `find_related`, `ingest_url`.

## Keeping it fresh

Put the syncs on a schedule. They are idempotent and skip what has not changed.

```cron
0 * * * *  cd /path/to/links-garden && uv run garden sync-vault && uv run garden sync-signal
30 * * * * cd /path/to/links-garden && uv run garden index && uv run garden enrich && uv run garden extract
```

## Checks

```sh
uv run ruff format --check src tests
uv run ruff check src tests
uv run complexipy src tests
uv run mypy
uv run pytest -q

cd frontend && npx tsc --noEmit && npx eslint . && npm test
```

No test reaches the network: `tests/conftest.py` blocks outbound sockets, so the suite can never
spend a Firecrawl credit or call a model.
