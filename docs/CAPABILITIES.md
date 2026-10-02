# What the agent can and cannot do

This connector gives an agent read-only access to one merchant's Freshdesk helpdesk. It
acts as a single Freshdesk agent (the owner of the API key), so it sees exactly what that
agent's role allows.

## The agent can

| Task | Tool |
|---|---|
| List tickets, most recently updated first, filtered by requester, company or update time | `list_tickets` |
| Read one ticket: subject, plain-text description, status, priority, tags, dates, custom fields | `get_ticket` |
| Find tickets by status, priority, tag, type, assigned agent or group, and created/updated date range | `search_tickets` |
| Read a ticket's thread: customer messages, agent replies and internal notes, oldest first | `list_ticket_conversations` |
| Look up the customer behind a ticket (`requester_id`) | `get_contact` |
| Find a customer by exact email, phone, mobile, company or tag | `search_contacts` |

Example questions it can answer:

- "Which urgent tickets are still open?" uses `search_tickets` with `status: ["open"]` and `priority: ["urgent"]`.
- "What has happened on ticket 4512 so far?" uses `get_ticket`, then `list_ticket_conversations`.
- "Has this customer raised other tickets this month?" uses `search_contacts` by email, then `list_tickets` with `requester_id` and `updated_since`.

## The agent cannot

- **Change anything.** It cannot create, update, reply to, assign, merge or delete tickets
  or contacts. The HTTP client only issues GET requests.
- **Search free text.** Freshdesk's search API matches fields exactly. There is no keyword
  search over subjects or descriptions.
- **Go past Freshdesk's search cap.** Search returns at most 300 results (10 pages of 30).
  Narrow the filters instead.
- **List old tickets without a date.** Without `updated_since`, `list_tickets` covers only
  tickets created in the last 30 days.
- **Read attachments.** Ticket attachments are not returned.
- **Resolve names for agents, groups or companies.** It returns their ids only.
- **See unmasked personal data** by default. Emails show as `p***@example.com` and phones
  as `***3210`. The merchant can turn this off with `FRESHDESK_MASK_PII=false`.
- **See data in real time.** Reads are cached for up to `FRESHDESK_CACHE_TTL_SECONDS`
  (60s by default), and Freshdesk's search index can lag a few minutes behind changes.

## Guardrails

| Risk | Guardrail |
|---|---|
| Agent changes or deletes merchant data | No write tools; the client has no write method |
| Prompt injection inside ticket text | Text fields are labelled as customer-written data in the schema and server instructions |
| Internal notes reaching customers | Notes are marked `private: true`; a customer-facing agent must not repeat them |
| Malformed or injected search queries | Agent passes structured filters; the connector builds the query and rejects quotes |
| Agent stuck in a loop drains the API quota | Per-session call budget (`FRESHDESK_SESSION_CALL_BUDGET`, default 200) |
| Too much text filling the context window | Compact records, text cut at `FRESHDESK_MAX_TEXT_CHARS` with a `*_truncated` flag |
| Credentials leaking | Key held as a secret type, never logged; `.env` is git-ignored; gitleaks in pre-commit and CI |

## Errors the agent may see

Every error is JSON with `error`, `message`, `hint` and `retryable`.

| `error` | Meaning | What the agent should do |
|---|---|---|
| `not_found` | No record with that id | Use a list or search tool to find a valid id |
| `invalid_request` | Freshdesk rejected the arguments | Fix the arguments |
| `rate_limited` | Freshdesk quota used up | Wait `retry_after_seconds`, then retry |
| `upstream_unavailable` | Freshdesk is down or slow | Retry later |
| `permission_denied` | The key's agent cannot see this record | Do not retry |
| `auth_failed` | Key or domain is wrong | Stop; the merchant must fix configuration |
| `session_budget_exceeded` | Session used its call budget | Summarise what it has; start a new session |
