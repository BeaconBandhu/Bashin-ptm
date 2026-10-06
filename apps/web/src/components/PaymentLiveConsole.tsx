"use client";

import { useMemo } from "react";
import { LiveRunControls } from "@/components/LiveRunControls";
import { useLiveRun } from "@/lib/useLiveRun";
import { streamPaymentAuthorization } from "@/lib/api";
import type { GuardrailVerdict, NodeRecord } from "@/lib/api";

type Verdict = "idle" | "pass" | "warn" | "fail";

const GOVERNANCE_GROUPS: { label: string; categories: string[] }[] = [
  { label: "Identity + Scope", categories: ["authorization_entitlement"] },
  { label: "Policy Decision", categories: ["business_rule", "input_validation"] },
  { label: "Budget + Rate", categories: ["rate_spend_limit"] },
  {
    label: "Taint + Effect",
    categories: ["pii_data_boundary", "action_irreversibility", "output_hallucination_check", "idempotency_replay"],
  },
];

const WORST: Record<Verdict, number> = { idle: 0, pass: 1, warn: 2, fail: 3 };

function worst(a: Verdict, b: Verdict): Verdict {
  return WORST[b] > WORST[a] ? b : a;
}

function allVerdicts(nodes: Record<string, NodeRecord>): GuardrailVerdict[] {
  return Object.values(nodes).flatMap((n) => n.guardrail_verdicts);
}

const BOX_COLORS: Record<Verdict, string> = {
  idle: "border-black/10 bg-black/[.02] text-black/40 dark:border-white/10 dark:bg-white/[.02] dark:text-white/30",
  pass: "border-emerald-400 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  warn: "border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300",
  fail: "border-red-400 bg-red-50 text-red-900 dark:border-red-700 dark:bg-red-950 dark:text-red-300",
};

function StageBox({
  title,
  subtitle,
  status,
  pulsing,
}: {
  title: string;
  subtitle: string;
  status: Verdict;
  pulsing?: boolean;
}) {
  return (
    <div
      className={`flex min-w-[140px] flex-1 flex-col gap-1 rounded-xl border-2 px-3 py-3 transition-all duration-300 ${BOX_COLORS[status]} ${
        pulsing ? "animate-pulse" : ""
      }`}
    >
      <span className="text-sm font-semibold uppercase tracking-wide">{title}</span>
      <span className="text-xs opacity-80">{subtitle}</span>
    </div>
  );
}

function Arrow() {
  return <span className="hidden self-center px-1 text-black/20 sm:block dark:text-white/20">→</span>;
}

const NODE_LABELS: Record<string, string> = {
  verify_balance: "Verify balance",
  balance_threshold_decision: "Check sufficiency",
  payment_authorization_gate: "Authorization gate",
  execute_payment: "Execute payment",
  notify_user: "Notify",
};

export function PaymentLiveConsole() {
  const live = useLiveRun(streamPaymentAuthorization);

  const governanceStatus: Verdict = useMemo(() => {
    const verdicts = allVerdicts(live.completedNodes);
    return verdicts.reduce<Verdict>((acc, v) => worst(acc, (v.verdict as Verdict) ?? "idle"), "idle");
  }, [live.completedNodes]);

  const groupStatuses = useMemo(() => {
    const verdicts = allVerdicts(live.completedNodes);
    return GOVERNANCE_GROUPS.map((group) => {
      const matches = verdicts.filter((v) => group.categories.includes(v.category));
      const status = matches.reduce<Verdict>((acc, v) => worst(acc, v.verdict as Verdict), "idle");
      return { ...group, status, count: matches.length };
    });
  }, [live.completedNodes]);

  const intakeStatus: Verdict = live.plan.length > 0 ? "pass" : "idle";
  const executeRecord = live.completedNodes["execute_payment"];
  const executeStatus: Verdict = executeRecord
    ? executeRecord.status === "SUCCEEDED"
      ? "pass"
      : executeRecord.status === "SKIPPED"
        ? "idle"
        : "fail"
    : "idle";
  const auditCount = live.order.length;

  return (
    <div className="flex flex-col gap-6">
      <LiveRunControls running={live.running} onRun={live.run} />

      {live.error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {live.error}
        </p>
      )}

      {/* Top pipeline strip, styled after the reference architecture diagram */}
      <div className="flex flex-col gap-2 rounded-2xl border border-black/10 bg-white p-4 dark:border-white/10 dark:bg-white/5">
        <div className="flex flex-wrap items-stretch gap-1">
          <StageBox
            title="Intake & Plan"
            subtitle="Typed envelope, statically validated"
            status={intakeStatus}
            pulsing={live.running && intakeStatus === "idle"}
          />
          <Arrow />
          <StageBox
            title="Council"
            subtitle="Proposers ×3 · Reviewer · Chairman — not active (Phase 2)"
            status="idle"
          />
          <Arrow />
          <StageBox
            title="Governance layer"
            subtitle={`${allVerdicts(live.completedNodes).length} checks run`}
            status={governanceStatus}
            pulsing={live.running && governanceStatus !== "fail"}
          />
          <Arrow />
          <StageBox
            title="Execute"
            subtitle="The only live write"
            status={executeStatus}
            pulsing={live.running && live.order.length > 0 && !executeRecord}
          />
          <Arrow />
          <StageBox
            title="Audit chain"
            subtitle={`${auditCount} hash-linked events${live.chainValid === true ? " · verified ✓" : live.chainValid === false ? " · BROKEN ✗" : ""}`}
            status={live.chainValid === false ? "fail" : auditCount > 0 ? "pass" : "idle"}
          />
        </div>
        <div className="flex flex-wrap gap-1">
          <StageBox title="Replay + Gym" subtitle="Self-improvement loop — not active (Phase 4)" status="idle" />
        </div>

        {/* Governance sub-groups, mirroring the reference diagram's row */}
        <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
          {groupStatuses.map((g) => (
            <div
              key={g.label}
              className={`rounded-lg border px-2 py-1.5 text-center text-xs font-medium transition-colors duration-300 ${BOX_COLORS[g.status]}`}
            >
              {g.label}
              {g.count > 0 && <span className="block text-[10px] opacity-70">{g.count} checks</span>}
            </div>
          ))}
        </div>
      </div>

      {/* Live log */}
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
          Live log
        </h2>
        <ol className="flex flex-col gap-2">
          {live.order.map((nodeId, i) => {
            const record = live.completedNodes[nodeId];
            return (
              <li
                key={`${nodeId}-${i}`}
                className="rounded-lg border border-black/10 bg-white p-3 text-sm dark:border-white/10 dark:bg-white/5"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium">{NODE_LABELS[nodeId] ?? nodeId}</span>
                  <span className="text-xs text-black/50 dark:text-white/50">{record.status}</span>
                </div>
                {record.guardrail_verdicts.length > 0 && (
                  <ul className="mt-1 flex flex-col gap-0.5 text-xs text-black/60 dark:text-white/60">
                    {record.guardrail_verdicts.map((v, j) => (
                      <li key={j}>
                        {v.name}:{" "}
                        <span
                          className={
                            v.verdict === "pass"
                              ? "text-emerald-700 dark:text-emerald-400"
                              : v.verdict === "warn"
                                ? "text-amber-700 dark:text-amber-400"
                                : "text-red-700 dark:text-red-400"
                          }
                        >
                          {v.verdict}
                        </span>
                        {v.reason ? ` — ${v.reason}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-1 truncate font-mono text-[10px] text-black/30 dark:text-white/30">
                  hash {record.hash.slice(0, 16)}…
                </p>
              </li>
            );
          })}
        </ol>
        {live.order.length === 0 && !live.running && (
          <p className="text-sm text-black/50 dark:text-white/50">
            Run a live transfer above to watch it flow through the AST.
          </p>
        )}
      </section>

      <footer className="mt-4 flex flex-wrap gap-2 border-t border-black/10 pt-4 text-xs text-black/50 dark:border-white/10 dark:text-white/50">
        <span className="rounded-full border border-current/30 px-2 py-1">Typed envelopes, not prose</span>
        <span className="rounded-full border border-current/30 px-2 py-1">No agent holds credentials</span>
        <span className="rounded-full border border-current/30 px-2 py-1">
          Permissions only narrow, never widen
        </span>
      </footer>
    </div>
  );
}
