"use client";

import { useEffect, useState } from "react";
import {
  type Ticket,
  type VerificationAnswerResponse,
  answerVerificationCall,
  listTickets,
  startVerificationCall,
} from "@/lib/api";

type TranscriptTurn = { question: string; answer: string };

type CallOutcome =
  | { kind: "in_progress" }
  | { kind: "resolved"; resolution: NonNullable<VerificationAnswerResponse["resolution"]> }
  | { kind: "escalated"; reason: string | null };

export function VerificationCallConsole() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [selected, setSelected] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);

  const [callId, setCallId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptTurn[]>([]);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [answerDraft, setAnswerDraft] = useState("");
  const [outcome, setOutcome] = useState<CallOutcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshTickets = () => {
    listTickets()
      .then((res) =>
        setTickets(res.tickets.filter((t) => t.type === "customer_support" && t.status === "AWAITING_HUMAN")),
      )
      .catch((err) => setLoadError(String(err)));
  };

  useEffect(() => {
    refreshTickets();
  }, []);

  function reset() {
    setCallId(null);
    setTranscript([]);
    setPendingQuestion(null);
    setAnswerDraft("");
    setOutcome(null);
    setError(null);
  }

  async function handleStart() {
    if (!selected || busy) return;
    reset();
    setBusy(true);
    try {
      const res = await startVerificationCall(selected);
      setCallId(res.call_id);
      setPendingQuestion(res.question);
      setOutcome({ kind: "in_progress" });
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmitAnswer() {
    if (!callId || !pendingQuestion || !answerDraft.trim() || busy) return;
    setBusy(true);
    const turn: TranscriptTurn = { question: pendingQuestion, answer: answerDraft.trim() };
    try {
      const res = await answerVerificationCall(callId, answerDraft.trim());
      setTranscript((prev) => [...prev, turn]);
      setAnswerDraft("");
      if (res.status === "next_question") {
        setPendingQuestion(res.question ?? null);
      } else if (res.status === "resolved" && res.resolution) {
        setPendingQuestion(null);
        setOutcome({ kind: "resolved", resolution: res.resolution });
      } else {
        setPendingQuestion(null);
        setOutcome({ kind: "escalated", reason: res.reason ?? null });
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
      refreshTickets();
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 rounded-xl border border-black/10 bg-black/[.02] p-4 text-sm dark:border-white/10 dark:bg-white/[.03]">
        <p className="text-black/70 dark:text-white/70">
          A locally-simulated verification call: asks the customer targeted follow-up questions,
          cross-checks each answer for real internal contradictions (against the original
          complaint and every prior answer), and only resolves the ticket if the answers are both
          consistent AND concrete enough to justify a specific action. Any real discrepancy, or
          any answer set that's still too thin, leaves the ticket exactly as escalated as it
          already was — this is deliberately biased toward escalating, not resolving.
        </p>
      </div>

      <div className="flex flex-col gap-3 rounded-xl border border-black/10 bg-white p-4 dark:border-white/10 dark:bg-white/5">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-black/60 dark:text-white/60">
            Escalated ticket to verify ({tickets.length} awaiting a human)
          </span>
          <div className="flex gap-2">
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="flex-1 rounded-lg border border-black/15 bg-white px-2 py-2 text-sm text-black dark:border-white/20"
            >
              <option value="" className="text-black">
                Select an escalated ticket…
              </option>
              {tickets.map((t) => (
                <option key={t.session_id} value={t.session_id} className="text-black">
                  {t.ticket_id ? `#${t.ticket_id}` : t.session_id} ·{" "}
                  {"text" in t.query ? t.query.text.slice(0, 60) : ""}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleStart}
              disabled={busy || !selected}
              className="shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 hover:bg-blue-700"
            >
              {busy && !callId ? "Starting…" : "Start call"}
            </button>
          </div>
        </label>
        {loadError && <p className="text-xs text-red-700 dark:text-red-400">{loadError}</p>}
        {error && <p className="text-xs text-red-700 dark:text-red-400">{error}</p>}
      </div>

      {callId && (
        <div className="flex flex-col gap-4 rounded-xl border border-black/10 bg-white p-4 dark:border-white/10 dark:bg-white/5">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-black/50 dark:text-white/50">
            Call {callId}
          </h2>

          {transcript.length > 0 && (
            <ol className="flex flex-col gap-3">
              {transcript.map((t, i) => (
                <li key={i} className="text-sm">
                  <p className="font-medium text-black/70 dark:text-white/70">Q{i + 1}: {t.question}</p>
                  <p className="mt-1 text-black/60 dark:text-white/60">A{i + 1}: {t.answer}</p>
                </li>
              ))}
            </ol>
          )}

          {pendingQuestion && (
            <div className="flex flex-col gap-2 border-t border-black/5 pt-3 dark:border-white/10">
              <p className="text-sm font-medium">{pendingQuestion}</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={answerDraft}
                  onChange={(e) => setAnswerDraft(e.target.value)}
                  placeholder="Type the customer's answer…"
                  className="flex-1 rounded-lg border border-black/15 bg-white px-3 py-2 text-sm text-black dark:border-white/20"
                  onKeyDown={(e) => e.key === "Enter" && handleSubmitAnswer()}
                />
                <button
                  type="button"
                  onClick={handleSubmitAnswer}
                  disabled={busy || !answerDraft.trim()}
                  className="shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 hover:bg-blue-700"
                >
                  {busy ? "…" : "Answer"}
                </button>
              </div>
            </div>
          )}

          {outcome?.kind === "resolved" && (
            <div className="rounded-xl border-2 border-emerald-400 bg-emerald-50 p-4 text-sm text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
              <p className="font-semibold uppercase tracking-wide">Resolved without a human</p>
              <p className="mt-1">
                action: <strong>{outcome.resolution.action}</strong>
              </p>
              {outcome.resolution.summary && <p className="mt-1">{outcome.resolution.summary}</p>}
              {outcome.resolution.detail && <p className="mt-1 opacity-80">{outcome.resolution.detail}</p>}
            </div>
          )}

          {outcome?.kind === "escalated" && (
            <div className="rounded-xl border-2 border-purple-400 bg-purple-50 p-4 text-sm text-purple-900 dark:border-purple-700 dark:bg-purple-950 dark:text-purple-300">
              <p className="font-semibold uppercase tracking-wide">Still needs a human</p>
              <p className="mt-1 opacity-90">
                {outcome.reason ?? "The call didn't reach a resolvable conclusion."}
              </p>
              <p className="mt-2 text-xs opacity-70">
                Nothing was lost — the ticket is exactly as escalated as it already was.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
