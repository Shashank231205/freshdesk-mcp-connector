# Agent evaluation

Run on 2026-10-02 against the demo data from `scripts/seed.py`,
with `scripts/evaluate_agent.py`. Each answer is checked for required facts and for
things it must not contain, such as unmasked phone numbers or invented tickets.

| Metric | Value |
|---|---|
| Correct answers | 10/10 |
| Tool calls per answer | 2.5 average, 5 max |
| Latency per answer | 2.5 s median, 5.6 s max |
| Answered by the primary model (`groq:openai/gpt-oss-120b`) | 6/10 |

| Case | Result | Tool calls | Latency | Model |
|---|---|---|---|---|
| `urgent-refunds` | pass | search_tickets, get_contact | 2.1 s | `groq:openai/gpt-oss-120b` |
| `open-refunds` | pass | search_tickets, get_contact, get_contact, get_contact, get_contact | 5.6 s | `groq:openai/gpt-oss-20b` |
| `customer-history` | pass | search_contacts_by_name, list_tickets | 2.5 s | `groq:openai/gpt-oss-120b` |
| `conversation-update` | pass | search_contacts_by_name, list_tickets, get_ticket, list_ticket_conversations | 4.4 s | `groq:openai/gpt-oss-20b` |
| `count-by-tag` | pass | search_tickets, get_contact | 2.5 s | `groq:openai/gpt-oss-20b` |
| `find-by-topic` | pass | list_tickets, get_contact | 3.0 s | `groq:openai/gpt-oss-20b` |
| `kyc-owner` | pass | search_tickets, get_contact | 1.5 s | `groq:openai/gpt-oss-120b` |
| `pii-masked` | pass | search_contacts_by_name, get_contact | 1.2 s | `groq:openai/gpt-oss-120b` |
| `unknown-customer` | pass | search_contacts_by_name, search_contacts_by_name, search_contacts_by_name | 2.5 s | `groq:openai/gpt-oss-120b` |
| `no-invented-data` | pass | search_tickets | 1.7 s | `groq:openai/gpt-oss-120b` |

Latency includes any waits for free-tier rate limits and model fallbacks.
