"use client";

import { useCallback, useState } from "react";
import type { LiveEvent, NodeRecord, Ticket } from "@/lib/api";

export type LiveRunState = {
  running: boolean;
  mode: "idle" | "live" | "replay";
  plan: string[];
  completedNodes: Record<string, NodeRecord>;
  order: string[];
  sessionId: string | null;
  finalStatus: string | null;
  chainValid: boolean | null;
  error: string | null;
};

const INITIAL: LiveRunState = {
  running: false,
  mode: "idle",
  plan: [],
  completedNodes: {},
  order: [],
  sessionId: null,
  finalStatus: null,
  chainValid: null,
  error: null,
};

/** Drives a live NDJSON run (and, separately, a client-side replay of a
 * previously recorded ticket) and exposes both as the same plain React
 * state shape — shared by /live's pipeline console, its AST graph, and the
 * chatbot console so every view visualizes the exact same real stream (or
 * the exact same recorded trace) instead of duplicating fetch/parse logic.
 * `streamFn` is injected per domain (payment vs chatbot) rather than
 * hardcoded. */
export function useLiveRun<TReq extends { session_id: string }>(
  streamFn: (req: TReq) => AsyncGenerator<LiveEvent>,
) {
  const [state, setState] = useState<LiveRunState>(INITIAL);

  const run = useCallback(
    async (req: TReq) => {
      setState({ ...INITIAL, running: true, mode: "live", sessionId: req.session_id });
      try {
        for await (const evt of streamFn(req)) {
          applyEvent(evt, setState);
        }
      } catch (err) {
        setState((prev) => ({ ...prev, running: false, error: String(err) }));
      }
    },
    [streamFn],
  );

  /** Replays an already-recorded ticket's node trace, paced for legibility.
   * Zero network/LLM cost — every event comes from data already returned
   * by GET /v1/tickets/{session_id}; nothing is re-executed or re-billed. */
  const replay = useCallback(async (ticket: Ticket, stepDelayMs = 450) => {
    setState({
      ...INITIAL,
      running: true,
      mode: "replay",
      sessionId: ticket.session_id,
      plan: ticket.nodes.map((n) => n.template_slot_id),
    });
    for (const record of ticket.nodes) {
      await new Promise((resolve) => setTimeout(resolve, stepDelayMs));
      setState((prev) => ({
        ...prev,
        completedNodes: { ...prev.completedNodes, [record.template_slot_id]: record },
        order: [...prev.order, record.template_slot_id],
      }));
    }
    setState((prev) => ({
      ...prev,
      running: false,
      finalStatus: ticket.status,
      chainValid: ticket.chain_valid,
    }));
  }, []);

  const reset = useCallback(() => setState(INITIAL), []);

  return { ...state, run, replay, reset };
}

function applyEvent(evt: LiveEvent, setState: (fn: (prev: LiveRunState) => LiveRunState) => void) {
  setState((prev) => {
    switch (evt.event) {
      case "session_started":
        return { ...prev, plan: evt.plan };
      case "node_completed":
        return {
          ...prev,
          completedNodes: { ...prev.completedNodes, [evt.node]: evt.record },
          order: [...prev.order, evt.node],
        };
      case "session_completed":
        return {
          ...prev,
          running: false,
          finalStatus: evt.last_status,
          chainValid: evt.chain_valid,
        };
      case "error":
        return { ...prev, running: false, error: JSON.stringify(evt.detail) };
      default:
        return prev;
    }
  });
}
