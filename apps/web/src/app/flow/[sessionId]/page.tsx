"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type Ticket, getTicket } from "@/lib/api";

const VERDICT_STYLES: Record<string, string> = {
  pass: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  fail: "text-red-700 dark:text-red-400",
};

const STATUS_STYLES: Record<string, string> = {
  SUCCEEDED: "border-emerald-300 dark:border-emerald-800",
  SKIPPED: "border-black/10 dark:border-white/10 opacity-50",
  AWAITING_HUMAN: "border-amber-300 dark:border-amber-800",
  BLOCKED_BY_GUARDRAIL: "border-red-300 dark:border-red-800",
  FAILED: "border-red-300 dark:border-red-800",
};

const NODE_LABELS: Record<string, string> = {
  verify_balance: "Verify balance",
  balance_threshold_decision: "Check sufficiency",
  payment_authorization_gate: "Authorization gate",
  execute_payment: "Execute payment",
  notify_user: "Notify",
};

export default function FlowDetailPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    getTicket(sessionId)
      .then(setTicket)
      .catch((err) => setError(String(err)));
  }, [sessionId]);

  if (error) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-10">
        <Link href="/flow" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← All tickets
        </Link>
        <p className="mt-4 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      </main>
    );
  }

  if (!ticket) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-10 text-sm text-black/60 dark:text-white/60">
        Loading…
      </main>
    );
  }

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-10">
      <Link href="/flow" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
        ← All tickets
      </Link>

      <div className="flex flex-col gap-2 rounded-2xl border border-black/10 bg-white p-5 dark:border-white/10 dark:bg-white/5">
        <div className="flex items-center justify-between">
          <h1 className="font-mono text-sm text-black/60 dark:text-white/60">{ticket.session_id}</h1>
          <span
            className={`rounded-full px-2 py-0.5 text-xs ${
              ticket.chain_valid
                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                : "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
            }`}
          >
            {ticket.chain_valid ? "Audit chain verified ✓" : "Audit chain BROKEN ✗"}
          </span>
        </div>
        <p className="text-lg font-semibold">
          {"account_id" in ticket.query
            ? `${ticket.query.account_id} → ${ticket.query.payee_id}: ₹${ticket.query.amount} ${ticket.query.currency}`
            : ticket.query.text}
        </p>
        <p className="text-sm text-black/60 dark:text-white/60">
          {new Date(ticket.created_at).toLocaleString()} · {ticket.type}
          {ticket.ticket_id ? ` · #${ticket.ticket_id}` : ""} · {ticket.status.replaceAll("_", " ")}
          {ticket.halt_reason ? ` — ${ticket.halt_reason}` : ""}
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
          AST execution trail
        </h2>
        {ticket.nodes.map((node) => (
          <div
            key={node.node_id}
            className={`rounded-xl border p-4 ${STATUS_STYLES[node.status] ?? "border-black/10 dark:border-white/10"}`}
          >
            <div className="flex items-center justify-between">
              <span className="font-medium">
                {NODE_LABELS[node.template_slot_id] ?? node.template_slot_id}
              </span>
              <span className="text-xs text-black/50 dark:text-white/50">{node.status}</span>
            </div>
            {node.guardrail_verdicts.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1 border-t border-black/5 pt-2 text-xs dark:border-white/10">
                {node.guardrail_verdicts.map((v, i) => (
                  <li key={i} className="flex items-start justify-between gap-3">
                    <span className="text-black/70 dark:text-white/70">
                      {v.name}{" "}
                      <span className="text-black/40 dark:text-white/40">({v.category})</span>
                    </span>
                    <span className={`shrink-0 font-medium ${VERDICT_STYLES[v.verdict]}`}>
                      {v.verdict}
                      {v.reason ? `: ${v.reason}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </section>

      <section className="flex flex-col gap-2 rounded-xl border border-dashed border-black/15 p-4 dark:border-white/15">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
          Council deliberation
        </h2>
        {ticket.council === null ? (
          <p className="text-sm text-black/50 dark:text-white/50">
            No council was invoked for this request — every node here is deterministic or
            single-model (Phase 0/1). Multi-model council deliberation (parallel responses, peer
            ranking, chairman synthesis) appears here starting Phase 2, and only for high-stakes
            or ambiguous decisions.
          </p>
        ) : (
          <pre className="overflow-x-auto text-xs">{JSON.stringify(ticket.council, null, 2)}</pre>
        )}
      </section>
    </main>
  );
}
