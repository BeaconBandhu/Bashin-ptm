"use client";

import { useEffect, useState } from "react";
import { type Account, type Payee, type PaymentAuthorizationRequest, getAccounts } from "@/lib/api";

function newSessionId(): string {
  return `live_${Math.random().toString(36).slice(2, 8)}${Date.now().toString(36)}`;
}

export function LiveRunControls({
  running,
  onRun,
}: {
  running: boolean;
  onRun: (req: PaymentAuthorizationRequest) => void;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [payees, setPayees] = useState<Payee[]>([]);
  const [payeeId, setPayeeId] = useState("");
  const [amount, setAmount] = useState("900");
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getAccounts()
      .then((res) => {
        setAccounts(res.accounts);
        setPayees(res.payees);
        setPayeeId(res.payees[0]?.payee_id ?? "");
      })
      .catch((err) => setLoadError(String(err)));
  }, []);

  const account = accounts[0];
  const selectedPayee = payees.find((p) => p.payee_id === payeeId);
  const canRun = !!account && !!selectedPayee && Number(amount) > 0 && !running;

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-xl border border-black/10 bg-white p-4 dark:border-white/10 dark:bg-white/5">
      {loadError && (
        <p className="w-full text-sm text-red-700 dark:text-red-400">
          Couldn&apos;t reach the council-engine API. {loadError}
        </p>
      )}
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-black/60 dark:text-white/60">Pay to</span>
        <select
          className="rounded-lg border border-black/15 bg-white px-2 py-1.5 text-sm text-black dark:border-white/20"
          value={payeeId}
          onChange={(e) => setPayeeId(e.target.value)}
        >
          {payees.map((p) => (
            <option key={p.payee_id} value={p.payee_id} className="text-black">
              {p.label} {p.verified ? "" : "(unverified)"}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-xs">
        <span className="font-medium text-black/60 dark:text-white/60">Amount</span>
        <input
          type="text"
          inputMode="decimal"
          className="w-28 rounded-lg border border-black/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/20"
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
        />
      </label>
      <button
        type="button"
        disabled={!canRun}
        onClick={() =>
          account &&
          selectedPayee &&
          onRun({
            session_id: newSessionId(),
            customer_id: account.customer_id,
            account_id: account.account_id,
            payee_id: selectedPayee.payee_id,
            amount: Number(amount),
            currency: account.currency,
            payee_verified: selectedPayee.verified,
            upi_pin_verified: true,
          })
        }
        className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40 hover:bg-blue-700"
      >
        {running ? "Running…" : "Run live"}
      </button>
      {selectedPayee && !selectedPayee.verified && (
        <span className="text-xs text-amber-700 dark:text-amber-400">
          Unverified payee — will escalate to human approval.
        </span>
      )}
    </div>
  );
}
