"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { type Ticket, listTickets } from "@/lib/api";

const STATUS_DOT: Record<string, string> = {
  SUCCEEDED: "bg-emerald-500",
  AWAITING_HUMAN: "bg-amber-500",
  BLOCKED_BY_GUARDRAIL: "bg-red-500",
  FAILED: "bg-red-500",
};

function querySummary(t: Ticket): { title: string; amount: string | null } {
  if ("account_id" in t.query) {
    return { title: `${t.query.account_id} → ${t.query.payee_id}`, amount: `₹${t.query.amount} ${t.query.currency}` };
  }
  return { title: t.query.text, amount: null };
}

export default function FlowListPage() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listTickets()
      .then((res) => setTickets(res.tickets))
      .catch((err) => setError(String(err)));
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Money flow &amp; governance</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            Every request the teammate has handled — the query, the decision, and the full
            guardrail (governance layer) trail behind it.
          </p>
        </div>
        <Link href="/transfer" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← New transfer
        </Link>
      </div>

      {error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          Couldn&apos;t reach the council-engine API. {error}
        </p>
      )}

      {tickets && tickets.length === 0 && (
        <p className="text-sm text-black/60 dark:text-white/60">
          No tickets yet — make a transfer to see it appear here.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {tickets?.map((t) => {
          const summary = querySummary(t);
          return (
            <li key={t.session_id}>
              <Link
                href={`/flow/${t.session_id}`}
                className="flex items-center justify-between gap-4 rounded-xl border border-black/10 bg-white p-4 hover:border-blue-400 dark:border-white/10 dark:bg-white/5"
              >
                <div className="flex items-center gap-3">
                  <span
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${STATUS_DOT[t.status] ?? "bg-gray-400"}`}
                  />
                  <div className="flex flex-col">
                    <span className="max-w-md truncate text-sm font-medium">{summary.title}</span>
                    <span className="text-xs text-black/50 dark:text-white/50">
                      {new Date(t.created_at).toLocaleString()} · {t.type}
                      {t.ticket_id ? ` · #${t.ticket_id}` : ""}
                    </span>
                  </div>
                </div>
                <div className="flex flex-col items-end">
                  {summary.amount && <span className="font-mono text-sm">{summary.amount}</span>}
                  <span className="text-xs text-black/50 dark:text-white/50">
                    {t.status.replaceAll("_", " ")}
                  </span>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </main>
  );
}
