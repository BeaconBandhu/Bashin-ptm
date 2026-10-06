"use client";

import { useEffect, useState } from "react";
import {
  type ChatbotAskRequest,
  type NodeRecord,
  type Ticket,
  listTickets,
  streamChatbotAsk,
} from "@/lib/api";
import { useLiveRun } from "@/lib/useLiveRun";

function newSessionId(): string {
  return `chat_${Math.random().toString(36).slice(2, 8)}${Date.now().toString(36)}`;
}

// Raw AST status (e.g. "SUCCEEDED") is the same whether a request was
// resolved, resolved-by-council, or just declined as out of scope — not
// useful to show in a ticket picker on its own. Derive what actually
// happened from which terminal node the trace reached instead.
function describeOutcome(t: Ticket): string {
  const reached = (id: string) => t.nodes.some((n) => n.template_slot_id === id && n.status !== "SKIPPED");

  const action = t.nodes.find((n) => n.template_slot_id === "take_protective_action" && n.status !== "SKIPPED");
  const actionTaken = (action?.outputs as { action_taken?: string } | undefined)?.action_taken;
  const actionSuffix = actionTaken && actionTaken !== "none" ? ` + ${actionTaken}` : "";

  if (reached("decline_out_of_scope")) return "Declined (out of scope)";

  if (reached("escalate_to_human")) {
    const escalate = t.nodes.find((n) => n.template_slot_id === "escalate_to_human");
    const agent = (escalate?.outputs as { agent_name?: string } | undefined)?.agent_name;
    return (agent ? `Escalated → ${agent}` : "Escalated to human") + actionSuffix;
  }

  const respond = t.nodes.find((n) => n.template_slot_id === "respond_to_user" && n.status !== "SKIPPED");
  if (respond) {
    const source = (respond.outputs as { source?: string } | undefined)?.source;
    if (source === "rag_direct") return "Resolved (RAG, $0)";
    if (source === "council_verifier") return `Resolved (Council, verified)${actionSuffix}`;
    if (source === "council_triage") return `Resolved (Council)${actionSuffix}`;
    return `Resolved${actionSuffix}`;
  }

  return t.status.replaceAll("_", " ");
}

const NODE_LABELS: Record<string, string> = {
  scope_gate: "Scope gate (harness)",
  decline_out_of_scope: "Declined — out of scope",
  retrieve_context: "Retrieve context (RAG)",
  rag_confidence_gate: "RAG confidence gate",
  create_support_ticket: "Create support ticket",
  lookup_transaction_context: "Lookup transaction context (fraud score)",
  council_triage: "Council — Triage",
  triage_route_gate: "Risk router",
  council_verifier: "Council — Verifier",
  resolution_decision: "Resolution decision",
  take_protective_action: "Take protective action",
  respond_to_user: "Resolved — respond to user",
  escalate_to_human: "Escalated to human",
};

type Status = "idle" | "running" | "pass" | "warn" | "fail" | "human";

function statusOf(record: NodeRecord | undefined, isRunning: boolean, everRun: boolean): Status {
  if (!record) return everRun && isRunning ? "running" : "idle";
  switch (record.status) {
    case "SUCCEEDED":
      return "pass";
    case "AWAITING_HUMAN":
      return "human";
    case "SKIPPED":
      return "idle";
    case "BLOCKED_BY_GUARDRAIL":
    case "FAILED":
      return "fail";
    default:
      return "warn";
  }
}

const STATUS_STYLE: Record<Status, string> = {
  idle: "border-black/10 bg-black/[.02] text-black/40 dark:border-white/10 dark:bg-white/[.02] dark:text-white/30",
  running: "animate-pulse border-blue-400 bg-blue-50 text-blue-900 dark:border-blue-600 dark:bg-blue-950 dark:text-blue-300",
  pass: "border-emerald-400 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  warn: "border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300",
  fail: "border-red-400 bg-red-50 text-red-900 dark:border-red-700 dark:bg-red-950 dark:text-red-300",
  human: "border-purple-400 bg-purple-50 text-purple-900 dark:border-purple-700 dark:bg-purple-950 dark:text-purple-300",
};

function FlowNode({
  id,
  caption,
  record,
  running,
  everRun,
}: {
  id: string;
  caption?: string;
  record: NodeRecord | undefined;
  running: boolean;
  everRun: boolean;
}) {
  const status = statusOf(record, running, everRun);
  return (
    <div className={`w-full rounded-xl border-2 px-3 py-2 transition-colors duration-300 ${STATUS_STYLE[status]}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{NODE_LABELS[id] ?? id}</span>
        {record && <span className="text-[10px] opacity-70">{record.status}</span>}
      </div>
      {caption && <p className="mt-0.5 text-[11px] opacity-70">{caption}</p>}
      <NodeDetail id={id} record={record} />
    </div>
  );
}

function NodeDetail({ id, record }: { id: string; record: NodeRecord | undefined }) {
  if (!record) return null;
  const out = record.outputs as Record<string, unknown>;

  if (id === "retrieve_context") {
    const matches = (out.matches as { question: string; kind: string; score: number }[]) ?? [];
    return (
      <div className="mt-2 flex flex-col gap-1 border-t border-current/10 pt-2 text-[11px]">
        <span className="font-medium uppercase tracking-wide opacity-60">Retrieved chunks</span>
        {matches.length === 0 && <span className="opacity-60">No matches in the corpus.</span>}
        {matches.map((m, i) => (
          <div key={i} className="flex items-center justify-between gap-2">
            <span className="truncate">
              [{m.kind}] {m.question}
            </span>
            <span className="shrink-0 font-mono opacity-70">{m.score.toFixed(3)}</span>
          </div>
        ))}
      </div>
    );
  }

  if (id === "lookup_transaction_context") {
    const reasons = (out.fraud_reasons as string[]) ?? [];
    return (
      <div className="mt-2 flex flex-col gap-0.5 border-t border-current/10 pt-2 text-[11px]">
        <span>
          fraud_score: <strong>{Number(out.fraud_score).toFixed(2)}</strong>
          {out.should_freeze ? " · should_freeze" : ""}
          {out.is_within_golden_hour ? " · golden_hour" : ""}
          {out.is_repeat_complaint ? " · repeat_complaint" : ""}
          {out.extracted_amount != null ? ` · amount ₹${String(out.extracted_amount)}` : ""}
        </span>
        {reasons.length > 0 && <span className="opacity-80">{reasons.join("; ")}</span>}
      </div>
    );
  }

  if (id === "take_protective_action") {
    const actionTaken = String(out.action_taken ?? "none");
    if (actionTaken === "none") return null;
    return (
      <div className="mt-2 flex flex-col gap-0.5 border-t border-current/10 pt-2 text-[11px]">
        <span>
          action: <strong>{actionTaken}</strong>
          {out.reference_number ? ` · ref ${String(out.reference_number)}` : ""}
        </span>
        {out.details ? <span className="opacity-80">{String(out.details)}</span> : null}
      </div>
    );
  }

  if (id === "council_triage") {
    return (
      <div className="mt-2 flex flex-col gap-0.5 border-t border-current/10 pt-2 text-[11px]">
        <span>
          risk_class: <strong>{String(out.risk_class)}</strong> · resolvable:{" "}
          <strong>{String(out.resolvable)}</strong> · confidence: {Number(out.confidence).toFixed(2)}
        </span>
        <span className="opacity-80">{String(out.diagnosis ?? "")}</span>
        <span className="opacity-60">
          {String(out.provider)}/{String(out.model)} · ${Number(out.actual_cost_usd).toFixed(6)}
        </span>
      </div>
    );
  }

  if (id === "council_verifier") {
    return (
      <div className="mt-2 flex flex-col gap-0.5 border-t border-current/10 pt-2 text-[11px]">
        <span>
          approved: <strong>{String(out.approved)}</strong> · confidence: {Number(out.confidence).toFixed(2)}
        </span>
        <span className="opacity-80">
          {String(out.approved ? out.final_resolution_steps : out.veto_reason) || "—"}
        </span>
        <span className="opacity-60">
          {String(out.provider)}/{String(out.model)} · ${Number(out.actual_cost_usd).toFixed(6)}
        </span>
      </div>
    );
  }

  if (id === "escalate_to_human" && out.agent_name) {
    return (
      <div className="mt-2 border-t border-current/10 pt-2 text-[11px]">
        Assigned to <strong>{String(out.agent_name)}</strong>
      </div>
    );
  }

  if ((id === "respond_to_user" || id === "decline_out_of_scope") && out.message) {
    return (
      <div className="mt-2 border-t border-current/10 pt-2 text-[11px] opacity-80">{String(out.message)}</div>
    );
  }

  if (record.guardrail_verdicts.length > 0) {
    return (
      <div className="mt-2 flex flex-col gap-0.5 border-t border-current/10 pt-2 text-[11px]">
        {record.guardrail_verdicts.map((v, i) => (
          <span key={i}>
            {v.name}: <strong>{v.verdict}</strong>
            {v.reason ? ` — ${v.reason}` : ""}
          </span>
        ))}
      </div>
    );
  }

  return null;
}

export function ChatbotConsole() {
  const live = useLiveRun<ChatbotAskRequest>(streamChatbotAsk);
  const [query, setQuery] = useState("I made a payment of 60000 rupees but it is not showing up");
  const [customerId] = useState("demo-customer-1");
  const [recentTickets, setRecentTickets] = useState<Ticket[]>([]);
  const [selectedTicket, setSelectedTicket] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshTickets = () => {
    listTickets()
      .then((res) => setRecentTickets(res.tickets.filter((t) => t.type === "customer_support")))
      .catch((err) => setLoadError(String(err)));
  };

  useEffect(() => {
    refreshTickets();
  }, []);

  async function handleAsk() {
    if (!query.trim() || live.running) return;
    await live.run({ session_id: newSessionId(), customer_id: customerId, query });
    refreshTickets();
  }

  function handleReplay() {
    const ticket = recentTickets.find((t) => t.session_id === selectedTicket);
    if (ticket) live.replay(ticket);
  }

  const running = live.running;
  const everRun = live.order.length > 0 || running;

  const outcomeRecord =
    live.completedNodes["respond_to_user"] ??
    live.completedNodes["escalate_to_human"] ??
    live.completedNodes["decline_out_of_scope"];
  const outcomeKind = live.completedNodes["decline_out_of_scope"]
    ? "declined"
    : live.completedNodes["escalate_to_human"]
      ? "escalated"
      : live.completedNodes["respond_to_user"]
        ? "resolved"
        : null;

  return (
    <div className="flex flex-col gap-6">
      {/* Chat input */}
      <div className="flex flex-col gap-3 rounded-xl border border-black/10 bg-white p-4 dark:border-white/10 dark:bg-white/5">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-black/60 dark:text-white/60">Ask the chatbot</span>
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. how do I add a beneficiary for NEFT?"
              className="flex-1 rounded-lg border border-black/15 bg-transparent px-3 py-2 text-sm dark:border-white/20"
              onKeyDown={(e) => e.key === "Enter" && handleAsk()}
            />
            <button
              type="button"
              onClick={handleAsk}
              disabled={running || !query.trim()}
              className="shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 hover:bg-blue-700"
            >
              {running && live.mode === "live" ? "Thinking…" : "Ask (live)"}
            </button>
          </div>
        </label>

        <div className="flex flex-wrap items-center gap-2 border-t border-black/5 pt-3 dark:border-white/10">
          <span className="text-xs font-medium text-black/60 dark:text-white/60">Replay a past ticket:</span>
          <select
            value={selectedTicket}
            onChange={(e) => setSelectedTicket(e.target.value)}
            className="rounded-lg border border-black/15 bg-white px-2 py-1 text-xs text-black dark:border-white/20"
          >
            <option value="" className="text-black">
              Select a ticket…
            </option>
            {recentTickets.map((t) => (
              <option key={t.session_id} value={t.session_id} className="text-black">
                {t.ticket_id ? `#${t.ticket_id}` : "—"} · {describeOutcome(t)} ·{" "}
                {"text" in t.query ? t.query.text.slice(0, 40) : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleReplay}
            disabled={running || !selectedTicket}
            className="rounded-lg border border-black/15 px-3 py-1 text-xs font-medium disabled:opacity-40 hover:border-blue-400 dark:border-white/20"
          >
            {running && live.mode === "replay" ? "Replaying…" : "▶ Replay"}
          </button>
          <span className="text-[11px] text-black/40 dark:text-white/40">
            (replays the recorded trace — $0, no new AI calls)
          </span>
        </div>
        {loadError && <p className="text-xs text-red-700 dark:text-red-400">{loadError}</p>}
      </div>

      {live.error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {live.error}
        </p>
      )}

      {/* Combined live execution console + AST graph: one flow, top to bottom */}
      <div className="flex flex-col gap-3 rounded-2xl border border-black/10 bg-white p-4 dark:border-white/10 dark:bg-white/5">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
          Live execution + AST graph
        </h2>

        <FlowNode
          id="scope_gate"
          caption="Harness/guardrail: off-topic or 'earn money' requests exit here → Declined"
          record={live.completedNodes["scope_gate"]}
          running={running}
          everRun={everRun}
        />
        <Center>↓ in scope</Center>
        <FlowNode
          id="retrieve_context"
          caption="TF-IDF over the Paytm FAQ/policy corpus — $0, no LLM call"
          record={live.completedNodes["retrieve_context"]}
          running={running}
          everRun={everRun}
        />
        <FlowNode
          id="rag_confidence_gate"
          caption="Confident match → Resolved directly. Otherwise ↓ escalate to Council."
          record={live.completedNodes["rag_confidence_gate"]}
          running={running}
          everRun={everRun}
        />
        <Center>↓ not confident enough</Center>
        <FlowNode
          id="create_support_ticket"
          caption="Assigns the 8-digit ticket number"
          record={live.completedNodes["create_support_ticket"]}
          running={running}
          everRun={everRun}
        />
        <FlowNode
          id="lookup_transaction_context"
          caption="Deterministic $0 fraud scoring + repeat-complaint check — the Council reasons over this, never guesses it"
          record={live.completedNodes["lookup_transaction_context"]}
          running={running}
          everRun={everRun}
        />
        <FlowNode
          id="council_triage"
          caption="Council member 1 — diagnoses the issue, classifies risk (routine vs high-stakes)"
          record={live.completedNodes["council_triage"]}
          running={running}
          everRun={everRun}
        />
        <FlowNode
          id="triage_route_gate"
          caption="Routine → skip straight to Resolution (saves a call). High-stakes → get a second opinion ↓"
          record={live.completedNodes["triage_route_gate"]}
          running={running}
          everRun={everRun}
        />
        <FlowNode
          id="council_verifier"
          caption="Council member 2 — independently reviews Triage's proposal; can approve or veto it"
          record={live.completedNodes["council_verifier"]}
          running={running}
          everRun={everRun}
        />
        <FlowNode
          id="resolution_decision"
          caption="Resolvable → Resolved. Not resolvable (or nothing upstream could run) → Escalated."
          record={live.completedNodes["resolution_decision"]}
          running={running}
          everRun={everRun}
        />
        <Center>↓ if the Council requested one</Center>
        <FlowNode
          id="take_protective_action"
          caption="freeze_card / file_dispute — reversible, never moves money. Blocked without evidence → escalates instead."
          record={live.completedNodes["take_protective_action"]}
          running={running}
          everRun={everRun}
        />
        <Center>↓</Center>

        {/* Outcomes: exactly one of these lights up per run */}
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <FlowNode
            id="decline_out_of_scope"
            record={live.completedNodes["decline_out_of_scope"]}
            running={running}
            everRun={everRun}
          />
          <FlowNode
            id="respond_to_user"
            record={live.completedNodes["respond_to_user"]}
            running={running}
            everRun={everRun}
          />
          <FlowNode
            id="escalate_to_human"
            record={live.completedNodes["escalate_to_human"]}
            running={running}
            everRun={everRun}
          />
        </div>
      </div>

      {outcomeRecord && outcomeKind && (
        <div
          className={`rounded-xl border-2 p-4 text-sm ${
            outcomeKind === "resolved"
              ? STATUS_STYLE.pass
              : outcomeKind === "escalated"
                ? STATUS_STYLE.human
                : STATUS_STYLE.idle
          }`}
        >
          <span className="font-semibold uppercase tracking-wide">
            {outcomeKind === "resolved" ? "Resolved" : outcomeKind === "escalated" ? "Escalated to human" : "Declined"}
          </span>
          {live.chainValid !== null && (
            <span className="ml-2 text-xs opacity-70">
              audit chain {live.chainValid ? "verified ✓" : "BROKEN ✗"}
            </span>
          )}
        </div>
      )}

      {/* Chronological live log */}
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
          Live log ({live.mode === "replay" ? "replayed from ticket" : "live"})
        </h2>
        <ol className="flex flex-col gap-1">
          {live.order.map((nodeId, i) => (
            <li key={`${nodeId}-${i}`} className="flex items-center gap-2 text-xs">
              <span className="w-5 shrink-0 text-black/30 dark:text-white/30">{i + 1}.</span>
              <span className="font-medium">{NODE_LABELS[nodeId] ?? nodeId}</span>
              <span className="text-black/40 dark:text-white/40">{live.completedNodes[nodeId]?.status}</span>
            </li>
          ))}
        </ol>
        {live.order.length === 0 && !running && (
          <p className="text-sm text-black/50 dark:text-white/50">
            Ask a question above, or replay a past ticket, to watch it flow through the AST.
          </p>
        )}
      </section>
    </div>
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <div className="text-center text-xs text-black/40 dark:text-white/40">{children}</div>;
}
