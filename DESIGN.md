# Links Garden — design

Personal tool, public repo, single user, localhost, self-hostable by others.

## Problem

Links land in Signal Note to Self faster than they can be reused. They mix four unrelated
intents: micro-influencers to contact, ad references to replicate, watch-later, and knowledge
to apply. Nothing is searchable. The Obsidian vault is a second disconnected pile.

Measured backlog on 2026-09-01: 685 messages, 500 unique URLs, from 2024-09-14 to 2026-09-01.
X holds 149 links, TikTok 168, YouTube 30, GitHub 25, `share.google` 13. Only 15% of
link-messages carry text of your own, so extraction is the only source of searchable content.

## Stack

| Layer | Choice |
| --- | --- |
| Store | One SQLite file, FTS5 plus vectors |
| Backend | Python, `uv`, FastAPI |
| Frontend | React, Vite, Tailwind, Motion |
| Embeddings | `bge-m3` via ollama, multilingual for a mixed French and English corpus |
| Extraction and classification | `qwen3:8b` via ollama, JSON Schema constrained output |
| Scheduling | cron |

## Sources

| Source | Contributes | On delete |
| --- | --- | --- |
| Signal Note to Self, Beeper at `BEEPER_API_URL`, chat `BEEPER_CHAT_ID` | Links plus any message text beside them | Tombstone |
| Obsidian vault, `~/Documents/VAULT`, excluding `wiki/` | Note text, and every URL inside a note is fetched and ingested | Index entry drops when the note disappears; tombstone survives re-sync |
| Dashboard field and CLI | Manual URL | Tombstone |
| MCP `ingest_url` | Agent-originated, logged as such | Tombstone |

Change detection for the vault is a hash walk. The vault is not a git repo. Nothing in this
system deletes or edits a note.

## Fetching

One `fetch(url) -> text` function with two backends chosen in `.env`. No adapter knows which
backend ran.

Firecrawl is the default backend and proxies every request it can, including the JSON APIs, using
`formats: ["rawHtml"]` so bodies arrive unmodified. This protects the home IP from being flagged.
If `FIRECRAWL_API_KEY` is missing, the app logs a warning at startup and uses the direct backend.

Measured on 2026-09-01: Firecrawl refuses `tiktok.com` and `vm.tiktok.com` outright, answering
"we do not support this site" for any URL on those domains. It serves `api.fxtwitter.com`,
`youtube.com/oembed`, `github.com` and ordinary articles without trouble. TikTok therefore cannot
use the Firecrawl backend, and TikTok is 168 of 500 links. See the TikTok entry under Adapters.

Adapters, in order of precedence:

| Order | Source | Endpoint | Backend | Yields |
| --- | --- | --- | --- | --- |
| 1 | `share.google` and other shorteners | resolve redirects, then re-dispatch | Firecrawl | the real URL |
| 2 | `vm.tiktok.com` | resolve redirect, then rule 3 | **direct** | the full TikTok URL |
| 3 | TikTok | `tiktok.com/oembed?url=` | **direct** | caption, `author_name`, `author_url` |
| 4 | X and Twitter | `api.fxtwitter.com` | Firecrawl | author handle, full text |
| 5 | YouTube | `youtube.com/oembed` | Firecrawl | title, channel |
| 6 | everything else | generic article extraction | Firecrawl | title, author, body |

TikTok is the one exception to the Firecrawl rule, and it is forced. Firecrawl refuses the domain,
so those two adapters call TikTok directly. To keep the home IP safe they carry their own limits:
one request every 2 seconds, and every response cached forever. TikTok oEmbed is TikTok's own
public embed API, designed for third-party callers, so this is a documented interface rather than
scraping.

Video and audio transcription is out of scope.

### Fetch budget

The Firecrawl plan allows 1000 fetches per month. That is a hard ceiling, and one development
run of step 2 would spend a quarter of it, because step 2 fetches the 219 URLs found inside
Obsidian notes. Two mechanisms keep the budget intact.

A content-addressed cache at `FETCH_CACHE_DIR` stores every raw response, keyed by a hash of the
URL. `fetch` consults it before spending a credit. The cache lives outside the database, so
wiping and rebuilding the database during development costs nothing.

`MAX_FETCHES_PER_RUN` caps how many credits one run can spend, default 50. A run that reaches
the cap stops fetching, leaves the remaining items `pending`, and says so. The next run
continues.

`GET https://api.firecrawl.dev/v2/team/credit-usage` reports remaining credits and costs nothing.
Check it before a run and refuse to start when remaining credits fall below the run's needs.

## Index and retrieval

Chunks of about 1000 tokens with overlap. Every chunk gets a `bge-m3` embedding. FTS5 indexes
content plus an LLM-written summary and alias keyword list.

Search fuses the FTS5 and vector rankings with reciprocal rank fusion, matches chunks, and
returns parent documents with the best-matching chunk as the snippet.

Edges are nearest neighbors computed on demand. No stored edges.

A tombstoned document keeps its content in `documents_fts`. `tombstone` is an `UPDATE`, and the
FTS update trigger reinserts the same terms under the same rowid. Every search query must
filter `deleted_at IS NULL` or deleted items return as hits. `idx_documents_deleted` exists for
this filter.

## Sets

A set has a name, a natural-language description that drives classification, and a JSON Schema
for extraction. You author schemas in the admin as JSON with live validation and visual help.

`qwen3:8b` reads the source and the available set descriptions, assigns membership, and extracts
to each matching schema. Membership is many-to-many. Matching no set is normal, not an error.

Sets make the garden a typed backend. A `recipe` set with
`{name, duration_in_minutes, steps[]}` serves a recipe app. A `tiktok_influenceur` set with
`{username, mail?, follower_count, like_count, niche}` serves an outreach script.

Missing fields store as null and surface in the review queue. Reindex buttons cover one set,
failed items only, or the whole corpus.

## Interfaces

API collections are read-only. Three writes exist: ingest a URL, patch a record's extracted
fields, delete an item.

Auth is one static bearer token from `.env`, checked on every route including the dashboard,
bound to `127.0.0.1`.

MCP exposes five tools: `search_garden`, `get_document`, `list_set_records`, `find_related`,
`ingest_url`. Every MCP-originated ingest is logged with the caller distinguishable in the
dashboard.

Dashboard pages: search, per-set tables, review queue, set admin, and a graph showing one
item's neighbors two hops out.

## Operations

Cron. Per-item transactions. The ✅ reaction on the Signal message is written only after the
item commits. Retries cap at three, then the item lands in the review queue. Each run checks
ollama answers before starting, so a dead ollama skips one run instead of failing every item.

`.env` holds infrastructure and secrets: `BEEPER_ACCESS_TOKEN`, `BEEPER_API_URL`,
`BEEPER_CHAT_ID`, `OLLAMA_URL`, `EMBEDDING_MODEL`, `EXTRACTION_MODEL`, `VAULT_PATH`,
`VAULT_EXCLUDE`, `BACKFILL_START_DATE`, `FETCH_BACKEND`, `FIRECRAWL_API_KEY`,
`FETCH_CACHE_DIR`, `MAX_FETCHES_PER_RUN`, `API_TOKEN`, `DATABASE_PATH`. Sets live in the
database. Model names appear read-only in the admin so you can tell what produced a bad
extraction.

`BACKFILL_START_DATE=2026-08-25` during development, about 40 links, which exercises every
adapter path. Set the real date at deploy.

## Repo

Skills committed at `.claude/skills/`:

```
npx skills add emilkowalski/skills
npx skills add Leonxlnx/taste-skill
```

`ponytail` is installed globally.

CI runs one job on every push: `ruff format --check`, `ruff` with `C901` at 10, `complexipy` at
15 per function, `mypy --strict`, `pytest`, and on the frontend `eslint`, `tsc --noEmit`, and
complexity rules.

Tests cover pure functions only, with models and network mocked: chunking, rank fusion, schema
validation, URL extraction, redirect resolution, adapter dispatch, sync diffing, and tombstone
logic. No test calls ollama.

## Build order

1. Schema, SQLite store, config loading.
2. Obsidian sync, following in-note URLs. Needs no Beeper token and yields a real corpus.
3. Beeper ingestion with the ✅ reaction.
4. Embeddings, chunking, FTS5, rank fusion.
5. Sets, classification, schema extraction.
6. API, MCP, auth.
7. Dashboard, graph last.
