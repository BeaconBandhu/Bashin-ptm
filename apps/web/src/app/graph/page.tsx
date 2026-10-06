"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LiveRunControls } from "@/components/LiveRunControls";
import { useLiveRun } from "@/lib/useLiveRun";
import { streamPaymentAuthorization } from "@/lib/api";
import { type PlanTemplate, getPlanTemplate } from "@/lib/api";

const NODE_LABELS: Record<string, string> = {
  verify_balance: "Verify balance",
  balance_threshold_decision: "Check sufficiency",
  payment_authorization_gate: "Authorization gate",
  execute_payment: "Execute payment",
  notify_user: "Notify",
};

const NODE_BORDER: Record<string, string> = {
  SUCCEEDED: "border-emerald-400 dark:border-emerald-700",
  BLOCKED_BY_GUARDRAIL: "border-red-400 dark:border-red-700",
  AWAITING_HUMAN: "border-amber-400 dark:border-amber-700",
  FAILED: "border-red-400 dark:border-red-700",
  SKIPPED: "border-black/10 opacity-50 dark:border-white/10",
};

const CHIP_STYLES: Record<string, string> = {
  idle: "border-black/15 text-black/40 dark:border-white/20 dark:text-white/40",
  pass: "border-emerald-400 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  warn: "border-amber-400 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300",
  fail: "border-red-400 bg-red-50 text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-300",
};

const RISK_TIER_STYLES: Record<string, string> = {
  deterministic: "bg-black/5 text-black/50 dark:bg-white/10 dark:text-white/50",
  single_model: "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  council: "bg-purple-100 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
};

export default function GraphPage() {
  const [template, setTemplate] = useState<PlanTemplate | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const live = useLiveRun(streamPaymentAuthorization);

  useEffect(() => {
    getPlanTemplate()
      .then(setTemplate)
      .catch((err) => setLoadError(String(err)));
  }, []);

  if (loadError) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-10">
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          Couldn&apos;t load the AST template. {loadError}
        </p>
      </main>
    );
  }

  if (!template) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-10 text-sm text-black/60 dark:text-white/60">
        Loading AST…
      </main>
    );
  }

  const nodes = template.nodes;

  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-4 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Live AST graph</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            The actual compiled plan for <code className="font-mono">{template.domain}</code> —
            nodes as typed steps, guardrails attached directly to the node they gate.
          </p>
        </div>
        <nav className="flex gap-4 text-sm text-blue-600 dark:text-blue-400">
          <Link href="/live" className="hover:underline">
            Pipeline console →
          </Link>
          <Link href="/flow" className="hover:underline">
            Tickets
          </Link>
        </nav>
      </div>

      <LiveRunControls running={live.running} onRun={live.run} />

      {live.error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {live.error}
        </p>
      )}

      <div className="flex flex-wrap items-stretch gap-0 overflow-x-auto pb-2">
        {nodes.map((node, i) => {
          const record = live.completedNodes[node.template_slot_id];
          const isCompleted = !!record;
          const isCurrent =
            live.running &&
            !isCompleted &&
            (i === 0 || !!live.completedNodes[nodes[i - 1].template_slot_id]);

          return (
            <div key={node.template_slot_id} className="flex items-stretch">
              <div
                className={`flex w-56 shrink-0 flex-col gap-2 rounded-xl border-2 bg-white p-3 transition-all duration-300 dark:bg-white/5 ${
                  isCompleted
                    ? (NODE_BORDER[record.status] ?? "border-black/10 dark:border-white/10")
                    : isCurrent
                      ? "animate-pulse border-blue-400 dark:border-blue-600"
                      : "border-black/10 dark:border-white/10"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{NODE_LABELS[node.template_slot_id] ?? node.template_slot_id}</span>
                  {isCompleted && (
                    <span className="text-[10px] text-black/50 dark:text-white/50">{record.status}</span>
                  )}
                </div>
                <div className="flex flex-wrap gap-1">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${RISK_TIER_STYLES[node.risk_tier]}`}
                  >
                    {node.risk_tier}
                  </span>
                  {node.side_effecting && !node.reversible && (
                    <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-medium text-red-700 dark:bg-red-950 dark:text-red-300">
                      irreversible
                    </span>
                  )}
                  {!node.side_effecting && (
                    <span className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] text-black/50 dark:bg-white/10 dark:text-white/50">
                      guardrail-only
                    </span>
                  )}
                </div>

                {node.guardrails.length > 0 && (
                  <div className="flex flex-col gap-1 border-t border-black/5 pt-2 dark:border-white/10">
                    <span className="text-[10px] font-semibold uppercase tracking-wide text-black/40 dark:text-white/40">
                      Governance
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {node.guardrails.map((g) => {
                        const verdict = record?.guardrail_verdicts.find((v) => v.name === g.name);
                        const style = CHIP_STYLES[verdict?.verdict ?? "idle"];
                        return (
                          <span
                            key={g.name}
                            title={`${g.category} · ${g.phase} · on_fail=${g.on_fail}${verdict?.reason ? ` · ${verdict.reason}` : ""}`}
                            className={`rounded-full border px-1.5 py-0.5 text-[10px] transition-colors duration-300 ${style}`}
                          >
                            {g.category}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>

              {i < nodes.length - 1 && (
                <div className="relative flex w-10 shrink-0 items-center justify-center">
                  <div className="h-px w-full bg-black/15 dark:bg-white/15" />
                  <span className="absolute text-black/25 dark:text-white/25">▶</span>
                  {live.running && live.completedNodes[node.template_slot_id] && !live.completedNodes[nodes[i + 1]?.template_slot_id] && (
                    <span className="absolute h-2 w-2 animate-ping rounded-full bg-blue-500" />
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {live.finalStatus && (
        <div className="rounded-xl border border-black/10 bg-white p-4 text-sm dark:border-white/10 dark:bg-white/5">
          Run finished: <span className="font-semibold">{live.finalStatus}</span>{" "}
          {live.chainValid !== null && (
            <span className={live.chainValid ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}>
              · audit chain {live.chainValid ? "verified ✓" : "BROKEN ✗"}
            </span>
          )}
        </div>
      )}

      <p className="text-xs text-black/40 dark:text-white/40">
        This AST is currently a fixed linear template (Phase 0). The layout and per-node guardrail
        attachment shown here is the real compiled plan, not a mockup — it will render branches
        once a domain template introduces conditional edges.
      </p>
    </main>
  );
}
