// Local-dev API client for apps/council-engine. Points at
// NEXT_PUBLIC_COUNCIL_ENGINE_URL (default http://localhost:8000) because
// `next dev` and `uvicorn` run as two separate localhost ports until this
// app runs behind `vercel dev`'s Services binding (see vercel.json at the
// repo root) — at that point server-side code should switch to the
// COUNCIL_ENGINE_INTERNAL_URL binding instead of this public base URL.

const BASE_URL =
  process.env.NEXT_PUBLIC_COUNCIL_ENGINE_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export type Account = {
  account_id: string;
  customer_id: string;
  label: string;
  balance: string;
  currency: string;
};

export type Payee = {
  payee_id: string;
  label: string;
  verified: boolean;
};

export type AccountsResponse = {
  accounts: Account[];
  payees: Payee[];
};

export type GuardrailVerdict = {
  node: string;
  name: string;
  category: string;
  phase: "pre" | "post";
  verdict: "pass" | "warn" | "fail";
  reason: string | null;
  latency_ms: number;
  on_fail: string;
};

export type NodeRecord = {
  node_id: string;
  template_slot_id: string;
  status: string;
  timestamp: string;
  prev_hash: string;
  hash: string;
  inputs_hash?: string;
  outputs_hash?: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  guardrail_verdicts: GuardrailVerdict[];
};

export type PaymentAuthorizationResult = {
  session_id: string;
  last_status: string;
  halted: boolean;
  halt_reason: string | null;
  outputs: Record<string, unknown>;
  node_records: NodeRecord[];
};

export type PaymentAuthorizationRequest = {
  session_id: string;
  customer_id: string;
  account_id: string;
  payee_id: string;
  amount: number;
  currency?: string;
  payee_verified: boolean;
  upi_pin_verified: boolean;
};

export type CouncilTriageResult = {
  resolvable: boolean;
  diagnosis: string;
  resolution_steps: string | null;
  escalate_reason: string | null;
  confidence: number;
  risk_class: "routine" | "high_stakes";
  provider: string;
  model: string;
  actual_cost_usd: string;
  input_tokens: number;
  output_tokens: number;
};

export type CouncilVerifierResult = {
  approved: boolean;
  final_resolution_steps: string | null;
  veto_reason: string | null;
  confidence: number;
  provider: string;
  model: string;
  actual_cost_usd: string;
  input_tokens: number;
  output_tokens: number;
};

export type Ticket = {
  session_id: string;
  tenant_id: string;
  customer_id: string;
  created_at: string;
  type: string;
  ticket_id?: string | null;
  query: { account_id: string; payee_id: string; amount: string; currency: string } | { text: string };
  status: string;
  halted: boolean;
  halt_reason: string | null;
  governance: GuardrailVerdict[];
  council:
    | { triage: CouncilTriageResult | null; verifier: CouncilVerifierResult | null }
    | null;
  nodes: NodeRecord[];
  chain_valid: boolean;
};

export function getAccounts(): Promise<AccountsResponse> {
  return apiFetch<AccountsResponse>("/v1/accounts");
}

export function authorizePayment(
  req: PaymentAuthorizationRequest,
): Promise<PaymentAuthorizationResult> {
  return apiFetch<PaymentAuthorizationResult>("/v1/domains/payment/authorize", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

export function listTickets(): Promise<{ tickets: Ticket[]; count: number }> {
  return apiFetch("/v1/tickets");
}

export function getTicket(sessionId: string): Promise<Ticket> {
  return apiFetch(`/v1/tickets/${sessionId}`);
}

// ---- static AST skeleton (for /graph, rendered before any run streams) ----

export type PlanGuardrailSpec = {
  name: string;
  category: string;
  phase: "pre" | "post";
  on_fail: string;
};

export type PlanTemplateNode = {
  template_slot_id: string;
  node_type: string;
  risk_tier: string;
  reversible: boolean;
  side_effecting: boolean;
  guardrails: PlanGuardrailSpec[];
};

export type PlanTemplate = {
  domain: string;
  nodes: PlanTemplateNode[];
  edges: [string, string][];
};

export function getPlanTemplate(): Promise<PlanTemplate> {
  return apiFetch<PlanTemplate>("/v1/domains/payment/plan-template");
}

export function getChatbotPlanTemplate(): Promise<PlanTemplate> {
  return apiFetch<PlanTemplate>("/v1/chatbot/plan-template");
}

// ---- chatbot (RAG -> Council triage/verifier -> human escalation) ---------

export type ChatbotAskRequest = {
  session_id: string;
  customer_id: string;
  query: string;
};

export type ChatbotAskResult = {
  session_id: string;
  ticket_id: string | null;
  last_status: string;
  halted: boolean;
  halt_reason: string | null;
  outputs: Record<string, unknown>;
  node_records: NodeRecord[];
};

export function askChatbot(req: ChatbotAskRequest): Promise<ChatbotAskResult> {
  return apiFetch<ChatbotAskResult>("/v1/chatbot/ask", {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// ---- live streaming (NDJSON) -----------------------------------------------

export type LiveEvent =
  | { event: "session_started"; session_id: string; plan: string[] }
  | { event: "node_completed"; node: string; record: NodeRecord }
  | {
      event: "session_completed";
      session_id: string;
      last_status: string;
      halted: boolean;
      halt_reason: string | null;
      chain_valid: boolean;
    }
  | { event: "error"; detail: unknown };

// A POST + ReadableStream (not EventSource, which is GET-only) reading
// newline-delimited JSON — one line per AST node transition, emitted by
// apps/council-engine as the LangGraph-backed graph actually executes (see
// app/api/main.py's streaming handlers). Nothing here is simulated
// client-side; this generator only parses what the server sent.
async function* streamNdjson<TReq>(path: string, req: TReq): AsyncGenerator<LiveEvent> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!res.ok || !res.body) {
    throw new Error(`stream POST ${path} -> ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex: number;
    while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);
      if (line) yield JSON.parse(line) as LiveEvent;
    }
  }
  const trailing = buffer.trim();
  if (trailing) yield JSON.parse(trailing) as LiveEvent;
}

export function streamPaymentAuthorization(
  req: PaymentAuthorizationRequest,
): AsyncGenerator<LiveEvent> {
  return streamNdjson("/v1/domains/payment/authorize/stream", req);
}

export function streamChatbotAsk(req: ChatbotAskRequest): AsyncGenerator<LiveEvent> {
  return streamNdjson("/v1/chatbot/ask/stream", req);
}

// ---- post-escalation verification call ("controlled local calling agent") --

export type VerificationStartResponse = {
  call_id: string;
  question: string;
};

export type VerificationAnswerResponse = {
  status: "next_question" | "escalated" | "resolved";
  question?: string | null;
  reason?: string | null;
  resolution?: {
    type: string;
    action?: string;
    summary?: string;
    detail?: string | null;
    reason?: string | null;
  } | null;
};

export function startVerificationCall(sessionId: string): Promise<VerificationStartResponse> {
  return apiFetch("/v1/domains/support/verification/start", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
}

export function answerVerificationCall(
  callId: string,
  answer: string,
): Promise<VerificationAnswerResponse> {
  return apiFetch(`/v1/domains/support/verification/${callId}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}
