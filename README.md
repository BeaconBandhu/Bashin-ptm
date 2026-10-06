# Bashin-ptm — AI Teammate (Council + AST + Guardrails)

An AI teammate that handles customer-facing workflows end to end: it decides,
acts on reversible actions, and hands off to a human only when it can't close
the case safely. It is built as a small **AST execution engine** where every
decision is a typed, guarded node, plus a **council** of LLMs that reasons over
verified facts instead of guessing.

The project is a working prototype with simulated (mock) integrations. It is
not connected to any real bank, payment rail, or telephony provider.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Core ideas](#core-ideas)
3. [Architecture](#architecture)
4. [Domains](#domains)
5. [The council (LLM tier)](#the-council-llm-tier)
6. [Post-escalation verification calls (n8n)](#post-escalation-verification-calls-n8n)
7. [Repository layout](#repository-layout)
8. [Getting started](#getting-started)
9. [Configuration](#configuration)
10. [API reference](#api-reference)
11. [Frontend pages](#frontend-pages)
12. [Testing](#testing)
13. [Security and cost controls](#security-and-cost-controls)
14. [Status, limitations, and roadmap](#status-limitations-and-roadmap)

---

## What it does

- **Customer support chatbot.** Answers common questions from a curated FAQ and
  policy corpus (TF-IDF retrieval, no paid calls). Anything it can't answer
  confidently becomes an 8-digit ticket and goes to the LLM council. Off-topic
  or out-of-scope requests are declined before any retrieval or model call.
- **Fraud-aware handling of escalations.** For reports of unauthorized activity,
  a deterministic fraud score (velocity, new payee, amount deviation, report
  recency) decides whether to freeze the account, and a dispute with a reference
  number can be filed. Both actions are reversible and never move money.
- **Payment authorization.** A guarded plan that checks balance, freeze status,
  payee verification, and daily limits before executing a payment against the
  mock rail. Each step has its own guardrails.
- **Post-escalation verification calls.** A locally simulated follow-up "call"
  that asks targeted questions, checks answers for real contradictions, and
  closes a ticket only on consistent, concrete evidence. It is orchestrated by a
  self-hosted, free n8n instance.
- **Live visibility.** A console streams each node's execution, guardrail
  verdicts, and council deliberation as they happen, and tickets can be replayed
  from the recorded trace at zero cost.

## Core ideas

**AST nodes are the execution graph.** Each step is a registered node type with
input and output schemas, a risk tier (`deterministic`, `single_model`, or
`council`), and attached guardrails. A compiled plan is a LangGraph
`StateGraph`, so there is one execution engine, not two.

**Guardrails are pure checks around every node.** Pre-guardrails decide whether
a node may run. Post-guardrails check the real output before a result is
trusted. A failing check blocks the node instead of letting it proceed. The
design goal is that a model's *claim* is never enough: an action needs evidence
the system can verify.

**The LLM reasons over verified facts, it doesn't invent them.** Fraud scoring,
amount extraction, and transaction context are computed deterministically. The
council receives those facts and decides what to do with them.

**Protective actions are a closed, reversible vocabulary.** The council can
request only `freeze_card`, `file_dispute`, or `none`. Neither moves money nor
decides that fraud occurred.

**Tamper-evident audit trail.** Every node event is written to a single-writer
SHA-256 hash chain, so retroactive edits are detectable by recomputing the
chain.

**Failure defaults to escalation.** Missing evidence, a spend cap reached, or a
model that can't ground its claim all route to a human, not to a guess.

## Architecture

```
            ┌────────────────────────── apps/web (Next.js 16, React 19) ─────────────────────────┐
            │  /live  /flow  /graph  /transfer  /verification-call                               │
            └──────────────┬───────────────────────────────────────────┬─────────────────────────┘
                           │ HTTP / NDJSON stream                       │ HTTP
                           ▼                                            ▼
┌──────────── apps/council-engine (FastAPI) ─────────────┐   ┌──── apps/n8n (self-hosted n8n) ────┐
│ API layer  →  plan compiler + validator                 │   │ webhook: verification-call/start   │
│   ↓                                                     │◀──┤ webhook: verification-call/answer  │
│ LangGraph StateGraph (one node per AST step)            │   └────────────────────────────────────┘
│   guardrails (pre/post) · node registry · audit chain   │
│   ├─ support domain: RAG · council · protective action  │
│   ├─ payment domain: balance · threshold · execute      │
│   └─ verification calls: question / check / resolve     │
│ Integrations (behind interfaces): bank, payment rail,   │
│   CRM, human agents — mocks today                       │
│ Stores: MongoDB (optional) or in-memory fallback        │
└─────────────────────────────────────────────────────────┘
```

Deployment intent (see `vercel.json`): `apps/web` is the public service and
`apps/council-engine` is reachable only through a private service binding, so
provider keys and mock backends never sit behind the public front end. Locally,
the two services run on separate ports with CORS opened for the web origin.

## Domains

The engine is domain-agnostic. A domain is a plan template, a set of node types,
and their wiring.

| Domain | Status | What it does |
|---|---|---|
| **Payment authorization** | Implemented | `verify_balance` → `balance_threshold_decision` → `payment_authorization_gate` → `execute_payment` → `notify_user`. Checks freeze status, payee verification, balance, and daily limits. |
| **Customer support** | Implemented | `scope_gate` → `retrieve_context` → `rag_confidence_gate` → `create_support_ticket` → `lookup_transaction_context` → `council_triage` → `triage_route_gate` → `council_verifier` (high-stakes only) → `resolution_decision` → `take_protective_action` → `respond_to_user` or `escalate_to_human`. |
| **Sales** | Planned | Package scaffolded (`app/domains/sales/`), no nodes yet. |

The support plan is a branching graph. A `follow_up` value on the resolution
decision determines whether a protective action leads to a customer answer or a
human handoff.

## The council (LLM tier)

Two roles, deliberately split to balance accuracy and cost:

- **Triage** runs on Groq (`openai/gpt-oss-20b` by default). It diagnoses the
  issue, classifies risk as `routine` or `high_stakes`, and requests a protective
  action if one is warranted.
- **Verifier** runs on OpenAI (`gpt-5-nano` by default) and is called only for
  `high_stakes` tickets. It can approve, veto, or downgrade Triage's proposal.

If a preferred provider isn't configured, each role falls back to whichever
provider is available. With no keys at all, the engine runs with a $0 null
client so the whole pipeline still executes end to end. Every paid call is
checked against a per-provider spend cap first.

## Post-escalation verification calls (n8n)

A second pass over tickets that already reached `escalate_to_human`:

1. `start` picks the first question from the ticket's context.
2. Each answer is checked for an amount mismatch (deterministic), then for a
   contradiction. A contradiction claim must quote two verbatim phrases from the
   transcript, which the system verifies before trusting it.
3. After at most four questions, a resolution step may close the ticket **only**
   if it can point to a specific action and supporting evidence. "No
   discrepancy" alone never closes a ticket.
4. Any discrepancy, or any answer set that is still too thin, leaves the ticket
   exactly as escalated as it was.

n8n is a thin orchestrator in front of these endpoints, so a real telephony
integration could later call the same webhooks with no backend changes. See
[`apps/n8n/README.md`](apps/n8n/README.md) for setup and a known limitation of the
discrepancy checker.

## Repository layout

```
.
├── apps/
│   ├── council-engine/        FastAPI + LangGraph execution engine (Python)
│   │   ├── app/
│   │   │   ├── api/           HTTP routes (main.py) and streaming
│   │   │   ├── graph/         plan model and LangGraph builder
│   │   │   ├── nodes/         node-type registry and base types
│   │   │   ├── guardrails/    guardrail middleware
│   │   │   ├── domains/       payment/, support/, sales/ (planned)
│   │   │   ├── llm/           Groq, OpenAI, Anthropic clients and pricing
│   │   │   ├── rag/           TF-IDF retriever and FAQ/policy corpus
│   │   │   ├── audit/         hash-chained audit log
│   │   │   ├── planning/      static plan validator
│   │   │   ├── mocks/         bank, payment rail, CRM, human agents
│   │   │   ├── spend/         spend tracking and caps
│   │   │   └── tickets/       ticket store (MongoDB or in-memory)
│   │   └── tests/             pytest suite
│   ├── web/                   Next.js 16 console (TypeScript, Tailwind)
│   └── n8n/                   n8n workflows and local setup notes
├── vercel.json                service layout for deployment
└── .gitignore
```

Some packages are placeholders for planned work and currently contain only
`__init__.py`: `app/memory/`, `app/self_improvement/`, `app/eval/`, and
`app/domains/sales/`.

## Getting started

### Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer (tested with Node 24)
- Optional: an API key for Groq and/or OpenAI. Without keys, everything still
  runs on the $0 null client.

### 1. Backend (council engine)

```bash
cd apps/council-engine
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # then fill in keys you have; never commit .env
uvicorn app.api.main:app --port 8000
```

Use `--reload` only while developing. Without it, the process is more
predictable, and a stale reloader process can otherwise hold the port.

Check it's up at <http://localhost:8000/healthz>. Interactive API docs are at
<http://localhost:8000/docs>.

### 2. Frontend (web console)

```bash
cd apps/web
npm install
npm run dev                        # http://localhost:3000
```

The console reads the engine URL from `NEXT_PUBLIC_COUNCIL_ENGINE_URL` and
defaults to `http://localhost:8000`.

### 3. n8n (optional, for verification calls)

```bash
cd apps/n8n
export N8N_USER_FOLDER="$(pwd)/data" N8N_HOST=127.0.0.1 N8N_PORT=5678 \
       N8N_LISTEN_ADDRESS=127.0.0.1 N8N_DIAGNOSTICS_ENABLED=false \
       N8N_SECURE_COOKIE=false
npx --yes n8n start
```

Then import and publish the two workflows in `apps/n8n/workflows/` as described in
[`apps/n8n/README.md`](apps/n8n/README.md). n8n runs locally, and its data
folder is gitignored.

## Configuration

All settings are read from `apps/council-engine/.env`. Start from
[`.env.example`](apps/council-engine/.env.example). Every value is optional:
unset values fall back to in-memory stores and mocks.

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY`, `GROQ_MODEL`, `MAX_GROQ_SPEND_USD` | Triage provider and its spend cap |
| `OPENAI_API_KEY`, `MAX_OPENAI_SPEND_USD` | Verifier provider and its spend cap |
| `ANTHROPIC_API_KEY`, `MAX_ANTHROPIC_SPEND_USD` | Optional fallback provider and cap |
| `MONGODB_URI`, `MONGODB_DB_NAME` | Ticket store (in-memory if unset) |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | Optional LangSmith tracing |
| `INTERNAL_JWT_SECRET` | Service-to-service secret (placeholder only; replace before deploying) |
| `DATABASE_URL`, `REDIS_URL`, `AI_GATEWAY_API_KEY` | Reserved for planned Postgres, Redis, and gateway work; not yet used |

Spend caps are enforced before each paid call. Exceeding a cap escalates to a
human instead of calling the provider.

## API reference

Base URL `http://localhost:8000`.

| Method and path | Purpose |
|---|---|
| `GET /healthz` | Liveness and which providers are active |
| `GET /v1/accounts` | Demo accounts and payees |
| `GET /v1/domains/payment/plan-template` | Static payment AST, nodes, and guardrails |
| `POST /v1/domains/payment/authorize` | Run a payment authorization |
| `POST /v1/domains/payment/authorize/stream` | Same, streamed as NDJSON events |
| `GET /v1/session/{session_id}/audit-chain` | Audit chain for a session, with verification |
| `GET /v1/domains/support/plan-template` | Static support AST (alias `/v1/chatbot/plan-template`) |
| `POST /v1/chatbot/ask` | Ask the support chatbot (alias `/v1/domains/support/ask`) |
| `POST /v1/chatbot/ask/stream` | Same, streamed |
| `GET /v1/tickets`, `GET /v1/tickets/{session_id}` | Ticket list and detail |
| `POST /v1/domains/support/verification/start` | Begin a verification call for an escalated ticket |
| `POST /v1/domains/support/verification/{call_id}/answer` | Submit an answer; returns the next question or the outcome |
| `GET /v1/domains/support/verification/{call_id}` | Read a call's transcript and state |

## Frontend pages

| Route | What it shows |
|---|---|
| `/` | Landing page |
| `/live` | Live execution console: chatbot run plus payment run, with replay |
| `/flow` | Ticket list and per-ticket detail |
| `/graph` | Payment AST with guardrails |
| `/transfer` | Payment authorization form |
| `/verification-call` | Walk through a verification call for an escalated ticket |

## Testing

```bash
cd apps/council-engine
pytest
ruff check app tests
```

The suite covers the hash chain, payment and support flows end to end, guardrail
behavior, the plan validator, and the verification-call logic. It runs entirely
on mocks and stubbed models, so it makes no paid calls. Type-check the frontend
with `npx tsc --noEmit` in `apps/web`.

`ruff check app tests` currently reports three known findings: one intentional
broad `except` in the guardrail runner (a guardrail that throws must fail
closed), and two `dict()` style nits in `tests/test_payment_guardrails.py`.

## Security and cost controls

- **Secrets stay out of git.** `.env` files, n8n's data folder, virtualenvs, and
  `node_modules` are gitignored. `.env.example` contains placeholders only.
- **Least privilege by design.** Protective actions are limited to two reversible
  operations. The council cannot move money, issue refunds, or adjudicate fraud.
- **Spend caps.** Each provider has a hard cap checked before every paid call.
- **Evidence before action.** Dispute filing requires an amount or a payee;
  an empty request is blocked, not guessed at.
- **Audit integrity.** The audit trail is hash-chained and can be verified on
  demand.

## Status, limitations, and roadmap

**Working today**

- Payment authorization and customer-support plans, both guarded and audited
- Deterministic fraud scoring and freeze/dispute actions, with the freeze enforced
  by the payment domain's own guardrails
- Groq triage and OpenAI verification, with spend caps
- Verification calls driven through n8n and the web console
- 64 automated tests passing

**Known limitations**

- **Integrations are mocks.** The bank, payment rail, CRM, and human-agent queue
  are simulated. Nothing dials phones or moves real money.
- **Discrepancy checking is imperfect.** On the small `gpt-5-nano` model, the
  checker sometimes misreads consistent statements as contradictions. Verbatim
  quote verification removes outright hallucinated claims, but not every
  wrong inference. The system errs toward escalation as a result. See
  [`apps/n8n/README.md`](apps/n8n/README.md).
- **A stale policy entry conflicts with a design decision.** The corpus entry
  `policy-escalation-criteria` says to escalate whenever the customer asks for a
  human. The current design attempts AI resolution first. Until the entry is
  revised, Triage follows the corpus text.
- **The scope gate is strict.** Genuine fraud complaints that don't mention an
  account, payment, or Paytm can be declined as out of scope.
- **RAG corpus is small.** About 33 hand-written FAQ and policy entries, retrieved
  with TF-IDF rather than embeddings.
- **Not yet wired:** Postgres persistence, Redis session buffer, pgvector memory,
  authentication (Clerk), Vercel Workflow durability, the sales domain, and the
  self-improvement loop. The `.env.example` entries for these are reserved.
- **Deployment is unverified.** `vercel.json` describes the intended layout, but
  this project has been run locally only.

**Roadmap**

1. Revise the escalation-criteria policy and tune the discrepancy checker, or
   move that check to a stronger model.
2. Persist tickets and audit records in Postgres, and move session state to Redis.
3. Add authentication and role-based access to the console.
4. Implement the sales domain on the same engine, without engine changes.
5. Build the self-improvement loop: outcome labeling, a proposer that can only
   tighten guardrails, shadow evaluation, and human approval before promotion.
6. Replace the mock integrations with sandbox providers behind the existing
   interfaces.

## License

No license has been chosen yet. Until one is added, the default copyright rules
apply and others may not reuse the code.
