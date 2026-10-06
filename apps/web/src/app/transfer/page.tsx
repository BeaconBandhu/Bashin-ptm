"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  type Account,
  type Payee,
  type PaymentAuthorizationResult,
  authorizePayment,
  getAccounts,
} from "@/lib/api";

function newSessionId(): string {
  return `txn_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;
}

const STATUS_STYLES: Record<string, string> = {
  SUCCEEDED: "bg-emerald-50 border-emerald-300 text-emerald-800 dark:bg-emerald-950 dark:border-emerald-800 dark:text-emerald-300",
  AWAITING_HUMAN: "bg-amber-50 border-amber-300 text-amber-800 dark:bg-amber-950 dark:border-amber-800 dark:text-amber-300",
  BLOCKED_BY_GUARDRAIL: "bg-red-50 border-red-300 text-red-800 dark:bg-red-950 dark:border-red-800 dark:text-red-300",
  FAILED: "bg-red-50 border-red-300 text-red-800 dark:bg-red-950 dark:border-red-800 dark:text-red-300",
};

const NODE_LABELS: Record<string, string> = {
  verify_balance: "Verify balance",
  balance_threshold_decision: "Check sufficiency",
  payment_authorization_gate: "Authorization gate",
  execute_payment: "Execute payment",
  notify_user: "Notify",
};

export default function TransferPage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [payees, setPayees] = useState<Payee[]>([]);
  const [accountId, setAccountId] = useState("");
  const [payeeId, setPayeeId] = useState("");
  const [amount, setAmount] = useState("");
  const [pin, setPin] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PaymentAuthorizationResult | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getAccounts()
      .then((res) => {
        setAccounts(res.accounts);
        setPayees(res.payees);
        setAccountId(res.accounts[0]?.account_id ?? "");
        setPayeeId(res.payees[0]?.payee_id ?? "");
      })
      .catch((err) => setLoadError(String(err)));
  }, []);

  const selectedAccount = accounts.find((a) => a.account_id === accountId);
  const selectedPayee = payees.find((p) => p.payee_id === payeeId);
  const canSubmit =
    accountId && payeeId && /^\d+(\.\d{1,2})?$/.test(amount) && Number(amount) > 0 && /^\d{4,6}$/.test(pin);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !selectedAccount || !selectedPayee) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await authorizePayment({
        session_id: newSessionId(),
        customer_id: selectedAccount.customer_id,
        account_id: selectedAccount.account_id,
        payee_id: selectedPayee.payee_id,
        amount: Number(amount),
        currency: selectedAccount.currency,
        payee_verified: selectedPayee.verified,
        // Phase-0 simplification, documented in the backend: entering a
        // plausible PIN sets this trust signal. A real UPI PIN entry flow
        // (and server-side verification) lands with Phase 1+ auth work.
        upi_pin_verified: true,
      });
      setResult(res);
      setPin("");
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col gap-6 px-4 py-10">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Send money</h1>
        <Link href="/flow" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          View money flow →
        </Link>
      </div>

      {loadError && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          Couldn&apos;t reach the council-engine API at{" "}
          {process.env.NEXT_PUBLIC_COUNCIL_ENGINE_URL ?? "http://localhost:8000"}. Is it running?
        </p>
      )}

      <form
        onSubmit={handleSubmit}
        className="flex flex-col gap-4 rounded-2xl border border-black/10 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/5"
      >
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">From account</span>
          <select
            className="rounded-lg border border-black/15 bg-white px-3 py-2 text-black dark:border-white/20"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
          >
            {accounts.map((a) => (
              <option key={a.account_id} value={a.account_id} className="text-black">
                {a.label} — ₹{a.balance} {a.currency}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Pay to</span>
          <select
            className="rounded-lg border border-black/15 bg-white px-3 py-2 text-black dark:border-white/20"
            value={payeeId}
            onChange={(e) => setPayeeId(e.target.value)}
          >
            {payees.map((p) => (
              <option key={p.payee_id} value={p.payee_id} className="text-black">
                {p.label} {p.verified ? "" : "(unverified)"}
              </option>
            ))}
          </select>
          {selectedPayee && !selectedPayee.verified && (
            <span className="text-xs text-amber-700 dark:text-amber-400">
              Unverified payee — this will require human approval regardless of amount.
            </span>
          )}
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Amount ({selectedAccount?.currency ?? "INR"})</span>
          <input
            type="text"
            inputMode="decimal"
            placeholder="0.00"
            className="rounded-lg border border-black/15 bg-transparent px-3 py-2 text-lg dark:border-white/20"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">UPI PIN</span>
          <input
            type="password"
            inputMode="numeric"
            maxLength={6}
            placeholder="••••"
            className="rounded-lg border border-black/15 bg-transparent px-3 py-2 tracking-widest dark:border-white/20"
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
          />
        </label>

        <button
          type="submit"
          disabled={!canSubmit || loading}
          className="mt-2 rounded-lg bg-blue-600 px-4 py-2.5 font-medium text-white disabled:opacity-40 hover:bg-blue-700"
        >
          {loading ? "Processing…" : "Pay"}
        </button>
      </form>

      {error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          {error}
        </p>
      )}

      {result && (
        <div
          className={`flex flex-col gap-3 rounded-2xl border p-4 ${
            STATUS_STYLES[result.last_status] ?? "border-black/10 bg-black/5"
          }`}
        >
          <div className="flex items-center justify-between">
            <span className="font-semibold">{result.last_status.replaceAll("_", " ")}</span>
            <Link
              href={`/flow/${result.session_id}`}
              className="text-xs underline underline-offset-2"
            >
              Full trail →
            </Link>
          </div>
          {result.halt_reason && <p className="text-sm">{result.halt_reason}</p>}
          <ol className="flex flex-wrap gap-2 text-xs">
            {result.node_records.map((n) => (
              <li
                key={n.node_id}
                className="rounded-full border border-current/30 px-2 py-1"
                title={n.status}
              >
                {NODE_LABELS[n.template_slot_id] ?? n.template_slot_id}: {n.status}
              </li>
            ))}
          </ol>
        </div>
      )}
    </main>
  );
}
