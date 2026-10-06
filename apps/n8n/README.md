# Verification-call n8n orchestrator

Self-hosted, free/open-source n8n — no cloud account, no subscription. Sits
in front of `apps/council-engine`'s post-escalation verification-call API
(`app/domains/support/verification.py` + `verification_routes.py`) as a
thin, swappable orchestration layer: today it's driven by curl or the new
`/verification-call` page in `apps/web`; later, a real telephony/voice
integration (Twilio, Vapi, etc.) can call the exact same two webhooks
without any backend changes.

## What this is (and isn't)

- **Simulated, locally-controlled call**, not a real phone call — per the
  explicit scope decision, nothing here dials a real number. It's a
  question-by-question Q&A against a ticket that already reached
  `escalate_to_human`.
- It never rewrites AST history — `escalate_to_human`'s own run and audit
  trail are untouched. This is a second pass that either closes the ticket
  (`status` becomes `RESOLVED_BY_VERIFICATION_CALL`) or leaves it exactly
  as escalated as it already was.
- Deliberately biased toward escalating, not resolving — see
  `ATTEMPT_RESOLUTION_SYSTEM_PROMPT` in `verification.py`. "No discrepancy
  found" is necessary but never sufficient to close a ticket on its own.

## Starting it

```bash
cd apps/n8n
export N8N_USER_FOLDER="$(pwd)/data"
export N8N_DIAGNOSTICS_ENABLED=false
export N8N_VERSION_NOTIFICATIONS_ENABLED=false
export N8N_TEMPLATES_ENABLED=false
export N8N_SECURE_COOKIE=false
export N8N_HOST=127.0.0.1
export N8N_PORT=5678
export N8N_LISTEN_ADDRESS=127.0.0.1
npx --yes n8n start
```

Requires `apps/council-engine`'s backend running on `http://127.0.0.1:8000`
(the workflows' HTTP Request nodes point there directly).

Editor UI: http://127.0.0.1:5678 (first visit will ask you to create a local
owner account — that's n8n's own instance setup, not a cloud sign-in;
nothing leaves your machine).

**If port 5678/5679 is already in use** on restart (an orphaned process
from a previous run, same class of issue as `uvicorn --reload`): find the
real owner and kill that PID specifically, don't just retry.

```powershell
Get-NetTCPConnection -LocalPort 5678,5679 -State Listen | Select LocalPort, OwningProcess
Stop-Process -Id <pid> -Force
```

## The two workflows

Both live as JSON in `apps/n8n/workflows/` (version-controlled — the
source of truth) and are loaded into n8n's local SQLite DB
(`apps/n8n/data/`, gitignored) via the CLI, not hand-built in the UI:

```bash
npx --yes n8n import:workflow --input=workflows/verification-call-start.json
npx --yes n8n publish:workflow --id=vcallstart0000001
npx --yes n8n import:workflow --input=workflows/verification-call-answer.json
npx --yes n8n publish:workflow --id=vcallanswer000001
# then restart n8n — publish only takes effect on the next start
```

- **`POST /webhook/verification-call/start`** — body `{"session_id": "..."}`.
  Proxies to the backend's `/v1/domains/support/verification/start`.
  Returns `{"call_id", "question"}`.
- **`POST /webhook/verification-call/answer`** — body
  `{"call_id": "...", "answer": "..."}`. Proxies to the backend's
  `/v1/domains/support/verification/{call_id}/answer`. Returns
  `{"status": "next_question"|"resolved"|"escalated", ...}`.

Call `/start` once, then `/answer` repeatedly (up to 4 turns —
`MAX_QUESTIONS` in `verification.py`) until `status` is no longer
`"next_question"`.

## Quick manual test

```bash
# 1. Get a real escalated ticket's session_id (needs status AWAITING_HUMAN)
curl -s http://localhost:8000/v1/tickets | python -m json.tool

# 2. Start the call through n8n
curl -s -X POST http://127.0.0.1:5678/webhook/verification-call/start \
  -H "Content-Type: application/json" -d '{"session_id":"<session_id>"}'

# 3. Answer with the returned call_id, repeat until status != "next_question"
curl -s -X POST http://127.0.0.1:5678/webhook/verification-call/answer \
  -H "Content-Type: application/json" \
  -d '{"call_id":"<call_id>","answer":"..."}'
```

Or just use the UI: `apps/web`'s `/verification-call` page (talks to the
backend directly, not through n8n — n8n is the integration point for a
future real telephony layer, not a required hop for the local UI).

## Known limitation (found and partially fixed during build/test)

The discrepancy-checking prompt (`CHECK_DISCREPANCY_SYSTEM_PROMPT`) runs on
whichever provider `verification_routes._select_provider(..., "careful")`
resolves to — currently OpenAI's `gpt-5-nano` (same model as the main
Council's Verifier role, chosen there for budget reasons). Live testing
during this build found it real-contradiction-hunting too aggressively on
a small/cheap model: it repeatedly claimed a contradiction between "the
merchant name shown was X" and "I don't recognize X" (these are actually
consistent — that combination is the definition of an unrecognized
charge), and once between two answers that were just natural elaboration
across turns (adding a date in a later answer that a first answer hadn't
mentioned yet).

Fixed the worst of it: `check_discrepancy` now requires the model to quote
two literal, verbatim phrases from the transcript as evidence
(`quote_a`/`quote_b`) and verifies both actually appear in what was said
before trusting the verdict (`_quote_is_grounded` in `verification.py`) —
this catches purely hallucinated/paraphrased "contradictions" reliably
(covered by `test_check_discrepancy_rejects_an_ungrounded_llm_claim`).
It does **not** fully catch a subtler case: the model correctly quoting
real text but drawing a wrong inference from it (e.g. conflating "the
UPI ID I *intended* to pay, which I know" with "the UPI ID it actually
went to by typo, which I don't recognize" — two different entities, both
real quotes). That's a genuine small-model reasoning limit, not a bug an
easy prompt tweak fixes. Net effect: the system currently leans toward
escalating on borderline cases rather than falsely resolving them — which
matches "don't resolve in a positive pattern," but if you want fewer
borderline escalations, the lever is upgrading the "careful" role to a
stronger (costlier) model in `_select_provider`, not further prompt
tuning.
