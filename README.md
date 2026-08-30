# ORD Bot — AI Customer Support Resolution Engine

ORD Bot is an AI-powered agent that resolves e-commerce customer support
issues end-to-end (order tracking, returns, refunds, cancellations) instead
of just answering questions about them. It looks up real order data,
grounds every policy claim in retrieval over actual policy documents, takes
permitted actions directly, and automatically hands off to a human agent —
with full context — whenever a case is high-risk, high-value, or uncertain.

## Why this design

Most "AI support chatbot" demos only do Q&A. The three things that make
this closer to production-grade:

1. **Tool-using agent, not a text generator.** The LLM (Claude) can only
   affect the world through a fixed set of auditable functions
   (`track_order`, `initiate_return`, `process_refund`, `cancel_order`,
   `search_policy`, `escalate_to_human`). It cannot promise a refund that
   never actually happened — the tool result is the source of truth.
2. **Policy-grounded, not memorized.** Return/refund/shipping/cancellation
   rules live in markdown docs and are retrieved via TF-IDF search
   (`policy_search.py`), so the agent cites the actual current policy
   instead of hallucinating one.
3. **Deterministic escalation backstop.** The agent is prompted to escalate
   on its own judgment, but the hard financial/fraud rules (refunds ≥
   ₹5,000, refunds with no return, repeated refund requests, possibly-lost
   shipments) are *also* enforced in code (`escalation.py`), so a prompting
   mistake can't cause an unapproved high-risk action to go through.

## Architecture

```
frontend/index.html   -- chat UI + live "agent trace" panel showing every tool call
        |
        v  HTTP (fetch)
backend/app/main.py    -- FastAPI: /api/start, /api/chat
        |
        v
backend/app/agent.py   -- tool-use loop against Claude (Anthropic Messages API)
        |
        v
backend/app/tools.py   -- track_order, initiate_return, process_refund,
                           cancel_order, search_policy, escalate_to_human
        |               \
        v                v
backend/app/database.py  backend/app/policy_search.py
  (SQLite mock orders)     (TF-IDF retrieval over backend/policies/*.md)
```

**Escalation flow:** when a tool result comes back `pending_human_approval`
(e.g. a ₹9,000 refund) or the model calls `escalate_to_human` directly
(customer asks for a human, possible fraud, possibly-lost shipment, low
confidence), the conversation is flagged, logged with a full summary in
the `escalations` table, and the UI shows an "ESCALATED TO HUMAN AGENT"
badge — this is the "complete context and investigation" handoff described
in the problem statement.

## Setup

Requires Python 3.10+ and an Anthropic API key.

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # then paste your ANTHROPIC_API_KEY into .env

python3 app/database.py           # one-time: creates + seeds the SQLite DB
uvicorn app.main:app --reload --port 8000
```

Then open `frontend/index.html` directly in your browser (no build step
needed — it's a static file that calls `http://localhost:8000`).

## Demo script (things to try)

Pick a customer in the dropdown, then try:

| Message | What happens |
|---|---|
| "Where is my order ORD5002?" | `track_order` is called; note it also demonstrates the "possibly lost in transit" flag if tracking is stale |
| "I want to return the smartwatch from ORD5003, it's defective" | `check_return_eligibility` → `initiate_return` → likely `process_refund` (auto-approved, under ₹5,000) |
| "Refund me ₹9000 for ORD5005, I never opened the box" | `process_refund` returns `pending_human_approval` → agent calls `escalate_to_human` |
| "Cancel my order ORD5006" | `cancel_order` succeeds instantly (order still "processing") |
| "I want to speak to a real person" | Immediate `escalate_to_human`, regardless of the underlying issue |

Watch the **Agent Trace** panel on the right — it shows exactly which
tools were called, with what inputs, and what came back, so you can see
the reasoning isn't just prose.

## Project structure

```
resolveai/
├── backend/
│   ├── app/
│   │   ├── main.py            FastAPI app
│   │   ├── agent.py           Claude tool-use loop
│   │   ├── tools.py           tool implementations + schemas
│   │   ├── escalation.py      deterministic escalation rules
│   │   ├── policy_search.py   TF-IDF retrieval over policy docs
│   │   └── database.py        SQLite mock order/customer store
│   ├── data/seed_data.json    mock customers + orders
│   ├── policies/*.md          return/refund/shipping/cancellation/escalation policy docs
│   ├── requirements.txt
│   └── .env.example
└── frontend/index.html        chat UI with live tool-trace panel
```

## Ideas to extend (good for interview talking points)

- Swap TF-IDF retrieval for real embeddings + a vector DB.
- Add conversation persistence (Postgres/Redis) instead of in-memory sessions.
- Add an eval harness: a fixed set of scripted conversations with expected
  tool calls / expected escalation outcomes, run on every change (this is
  the natural next step and a strong resume line — "built an eval suite
  for agent correctness").
- Add authentication so `customer_id` comes from a real session instead of
  a dropdown.
- Add streaming responses (Anthropic supports streaming tool use).
- Add a sentiment/frustration classifier feeding into the escalation rules.

## Suggested resume bullet points

- Built ORD Bot, an LLM agent that autonomously resolves e-commerce
  support tickets (order tracking, returns, refunds, cancellations) by
  orchestrating tool calls against order/policy systems, with a
  rules-based escalation layer for high-risk or low-confidence cases.
- Implemented a retrieval layer (TF-IDF) grounding all policy statements
  in source documents to reduce hallucinated policy claims.
- Designed a human-in-the-loop handoff that auto-escalates refunds above
  a configurable threshold, refunds without an associated return, and
  suspected fraud patterns, passing full conversation context to the
  human agent.
- Built a full-stack demo (FastAPI + SQLite backend, vanilla JS frontend)
  with a live tool-trace view for observability into agent decisions.
