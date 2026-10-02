# freshdesk-mcp-connector

A read-only [MCP](https://modelcontextprotocol.io) connector that lets an AI agent list,
get and search a merchant's Freshdesk tickets, ticket conversations and contacts.

It authenticates with a Freshdesk API key, stays inside the account's rate limit, caches
repeat reads, and returns compact, masked records sized for an agent's context window.

- What the agent can and cannot do: [docs/CAPABILITIES.md](docs/CAPABILITIES.md)
- MCP tool specification, generated from the code: [docs/tools.json](docs/tools.json)
- How accurately an LLM agent answers through it: [docs/evaluation.md](docs/evaluation.md)

## Tools

| Tool | Freshdesk endpoint | Purpose |
|---|---|---|
| `list_tickets` | `GET /tickets` | Tickets, most recently updated first |
| `get_ticket` | `GET /tickets/{id}` | One ticket with description and custom fields |
| `search_tickets` | `GET /search/tickets` | Filter by status, priority, tag, type, agent, group, dates |
| `list_ticket_conversations` | `GET /tickets/{id}/conversations` | Replies and internal notes |
| `get_contact` | `GET /contacts/{id}` | One customer |
| `search_contacts` | `GET /search/contacts` | Customers by email, phone, mobile, company or tag |
| `search_contacts_by_name` | `GET /contacts/autocomplete` | Customers by name (ids and names only) |

Every tool is marked read-only in its MCP annotations and has an input and output schema.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and a Freshdesk account. A free 14-day
trial works.

```bash
git clone https://github.com/Shashank231205/freshdesk-mcp-connector.git
cd freshdesk-mcp-connector
uv sync
cp .env.example .env
```

Fill in two values in `.env`:

- `FRESHDESK_DOMAIN`: your subdomain, e.g. `acme` for `acme.freshdesk.com`
- `FRESHDESK_API_KEY`: in Freshdesk, click your profile picture, then
  **Profile settings → View API key**. New accounts ship with API access turned off.
  If the key is hidden, enable it under **Admin → Agents → (your agent) → Security and
  permission → API Key access**.

Optionally load fictional demo data (18 tickets, 6 customers, replies and notes):

```bash
uv run python scripts/seed.py
```

## Run

The server speaks MCP over stdio, so an agent platform launches it as a subprocess:

```json
{
  "mcpServers": {
    "freshdesk": {
      "command": "uv",
      "args": ["--directory", "/path/to/freshdesk-mcp-connector", "run", "freshdesk-mcp"]
    }
  }
}
```

On start it calls `GET /agents/me` once. A wrong domain or key stops it with a one-line
message instead of failing later inside a tool call.

To try the tools by hand, use the MCP Inspector (needs Node.js):

```bash
npx @modelcontextprotocol/inspector uv run freshdesk-mcp
```

On Windows PowerShell, use `npx.cmd` if script execution is disabled.

## Agent example

`scripts/support_agent.py` is a working LLM agent that uses this connector the way an
Agent Studio agent would. It launches the connector over MCP, hands the tool schemas to
the model, and lets the model decide what to call.

```bash
uv run python scripts/support_agent.py
uv run python scripts/support_agent.py "Has Priya Sharma raised any other tickets?"
```

```
Q: Which urgent refund tickets are open, and who raised them?

  -> search_tickets({"filters":{"priority":["urgent"],"status":["open"],"tag":"refund"}})
  -> get_contact({"contact_id":1130009701347})

A (groq:openai/gpt-oss-120b):
- Ticket #4 - urgent, open, tagged refund - raised by Aarav Mehta.
```

It needs `GROQ_API_KEY` and/or `GEMINI_API_KEY` in `.env`; both have free tiers.
`AGENT_MODELS` sets an ordered chain of models across providers. A model that is
rate-limited or overloaded hands over to the next one. The conversation so far is passed
on as plain text, because tool-call history from one model is not always accepted by
another (Gemini 3, for example, requires signatures it generated itself). A model that
fails permanently, such as an unknown model name, is skipped for the rest of the session.

## Agent evaluation

`scripts/evaluate_agent.py` asks the agent ten questions with known answers in the demo
data. Each answer is checked for the facts it must contain and for things it must not,
such as unmasked phone numbers or invented ticket ids.

```bash
uv run python scripts/evaluate_agent.py --save
```

Latest run, in [docs/evaluation.md](docs/evaluation.md): 10/10 correct, 2.5 tool calls per
answer, 2.5 s median latency.

Results vary between runs, because the model that answers depends on free-tier rate
limits. In an earlier run the smaller fallback model counted the open delivery tickets
correctly but did not say whose they were, so that run scored 9/10. Building the
evaluation also exposed a grader bug: models write names with narrow no-break spaces and
Markdown emphasis, so plain text matching marked correct answers as wrong until the
grader normalised both.

## Test

```bash
uv run pytest                        # unit and MCP protocol tests, no network
uv run python scripts/smoke.py       # live check against your Freshdesk account
```

The unit tests mock Freshdesk's HTTP API. The protocol tests drive the real MCP server
through an in-process MCP client. `smoke.py` launches the connector over stdio exactly as
an agent platform would, calls every tool against the live account, and checks error
handling and caching.

CI runs formatting, lint, strict type checks, the tests, a check that `docs/tools.json`
matches the code, and a gitleaks secret scan. The same secret scan runs as a pre-commit
hook (`uvx pre-commit install`).

## How it works

```
agent ──MCP/stdio──▶ server.py    tools, session budget, errors as JSON
                        │
                     service.py   cache, query building, record shaping
                        │
                     client.py    auth, rate limiter, retries, timeouts (GET only)
                        │
                     Freshdesk API v2 (HTTPS)
```

Each layer calls only the one below it.

**Authentication.** HTTP Basic auth with the API key, as Freshdesk requires. The key is
held as a `SecretStr`, never logged, and sent only to `https://<domain>.freshdesk.com`.
The domain is validated as a plain subdomain and redirects are not followed, so the key
cannot be sent to another host. TLS certificates are verified against the operating
system's trust store, so the connector works behind corporate proxies and antivirus
HTTPS scanning without turning verification off.

**Rate limits.** Freshdesk limits API calls per minute across the whole account (50 on
a trial). A token bucket spaces requests, and after every response it is reconciled with
Freshdesk's `X-RateLimit-Total`, `X-RateLimit-Remaining` and
`X-RateLimit-Used-CurrentRequest` headers, so calls made by other integrations count too.
On HTTP 429 the client waits for `Retry-After` and retries. If the wait is longer than
`FRESHDESK_MAX_RETRY_WAIT_SECONDS`, it returns a `rate_limited` error with the wait time
instead of blocking the agent.

**Retries.** Timeouts, connection errors and 5xx responses are retried with exponential
backoff and jitter. Other 4xx responses are never retried.

**Caching.** Successful reads are cached in memory for 60 seconds with LRU eviction.
Concurrent identical requests share one API call. Errors are never cached.

**Context size.** Records carry only the fields an agent needs. Status, priority and
source codes become words. Long text is cut at `FRESHDESK_MAX_TEXT_CHARS` and flagged.
Pages report `has_more` so the agent fetches more only when it needs to.

**Accuracy.** Search takes structured filters, and the connector writes the Freshdesk
query. The agent cannot produce invalid syntax, and quotes are rejected so it cannot
inject clauses. Every Freshdesk response is validated into a typed model, and an
unexpected payload becomes an `upstream_unavailable` error rather than a crash.

**Session limits.** Each session has a tool-call budget, so a looping agent cannot drain
the merchant's API quota.

**Observability.** One JSON log line per Freshdesk request and per tool call (path,
status, attempt, latency, remaining quota), written to stderr. Query parameters are not
logged because they can contain customer emails.

## Configuration

All settings are environment variables, read from `.env` if present.

| Variable | Default | Meaning |
|---|---|---|
| `FRESHDESK_DOMAIN` | required | Helpdesk subdomain |
| `FRESHDESK_API_KEY` | required | API key of the agent the connector acts as |
| `FRESHDESK_TIMEOUT_SECONDS` | `10` | Per-request timeout |
| `FRESHDESK_MAX_RETRIES` | `3` | Retries for 429, 5xx and network errors |
| `FRESHDESK_RETRY_BACKOFF_SECONDS` | `0.5` | Base delay for exponential backoff |
| `FRESHDESK_MAX_RETRY_WAIT_SECONDS` | `30` | Longest wait before returning `rate_limited` instead |
| `FRESHDESK_RATE_LIMIT_PER_MINUTE` | `50` | Starting quota; corrected from response headers |
| `FRESHDESK_MAX_CONCURRENCY` | `4` | Parallel requests to Freshdesk |
| `FRESHDESK_CACHE_TTL_SECONDS` | `60` | Cache lifetime; `0` turns caching off |
| `FRESHDESK_CACHE_MAX_ENTRIES` | `1024` | Cache size |
| `FRESHDESK_SESSION_CALL_BUDGET` | `200` | Tool calls allowed per session |
| `FRESHDESK_DEFAULT_PAGE_SIZE` | `30` | Page size when the agent does not set one |
| `FRESHDESK_MAX_TEXT_CHARS` | `2000` | Longest description or message returned |
| `FRESHDESK_MASK_PII` | `true` | Mask emails and phone numbers |
| `FRESHDESK_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Project layout

```
src/freshdesk_connector/
  config.py     settings and validation
  errors.py     typed errors with agent-facing hints
  models.py     record schemas and the search query builder
  cache.py      TTL + LRU cache with single-flight loading
  client.py     HTTP client, rate limiter, retries
  service.py    read operations and record shaping
  server.py     MCP tools, session budget, logging, entry point
scripts/
  seed.py            loads fictional demo data into a trial account
  seed_data.json     the demo tickets, customers, replies and notes
  smoke.py           live end-to-end check over stdio
  support_agent.py   LLM agent answering questions through the connector
  evaluate_agent.py  measures the agent's accuracy on eval_cases.json
  eval_cases.json    questions with known answers in the demo data
  export_tools.py    writes docs/tools.json from the code
tests/               one test module per source module
```

## Assumptions

- One server process serves one merchant and one agent session (MCP over stdio).
- The connector acts as one Freshdesk agent. Freshdesk's role permissions for that agent
  decide what it can see. In production, give it a dedicated agent with the narrowest
  role and ticket scope the use case needs, not an administrator account.
- Freshdesk's REST API is authenticated with per-agent API keys and offers no OAuth flow,
  so this connector uses the API-key option from the brief.
- The agent consuming the tools is untrusted with respect to ticket text: customer-written
  content is treated as data, not instructions.

## Limitations

- Read-only by design. No ticket updates, replies or assignments.
- Search is limited by Freshdesk: exact-match fields only, 30 results per page, 300 in
  total, and new or changed tickets take a few minutes to become searchable.
- `list_tickets` covers only the last 30 days unless `updated_since` is given.
- Cached reads can be up to 60 seconds old.
- The cache, rate limiter and session budget live in process memory. Several processes
  using the same Freshdesk account share its quota. They reconcile through the response
  headers, but they can still receive 429s, which are retried.
- Masking covers contact emails and phone numbers, conversation senders and ticket CC
  lists. Ticket text and custom fields are returned as written, so personal data the
  merchant stores there is not redacted.
- Attachments, and names for agents, groups and companies, are not returned.

## Long-term fixes

- **Hosted, multi-tenant deployment.** Serve over MCP streamable HTTP with
  authentication on the MCP endpoint. Load per-merchant credentials from a secret
  manager, and key session budgets by MCP session id.
- **Shared state.** Move the cache and the token bucket to Redis so replicas share one
  view of the quota.
- **Fresher data.** Use Freshdesk automation webhooks to invalidate cached tickets on
  change, instead of relying on a fixed TTL.
- **Free-text search.** Sync tickets into a search index, since Freshdesk's API matches
  only exact fields.
- **More tools.** A Zoho Inventory or WooCommerce connector would add a new client and
  service pair behind the same server pattern. Zoho would use an OAuth 2.0 refresh-token
  flow.
