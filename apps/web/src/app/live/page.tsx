"use client";

import Link from "next/link";
import { useState } from "react";
import { ChatbotConsole } from "@/components/ChatbotConsole";
import { PaymentLiveConsole } from "@/components/PaymentLiveConsole";

type Tab = "chatbot" | "payment";

export default function LivePage() {
  const [tab, setTab] = useState<Tab>("chatbot");

  return (
    <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-6 px-4 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Live execution console</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            Every box below reflects a real AST run streaming from council-engine — nothing here
            is simulated.
          </p>
        </div>
        <nav className="flex gap-4 text-sm text-blue-600 dark:text-blue-400">
          {tab === "payment" && (
            <Link href="/graph" className="hover:underline">
              AST graph view →
            </Link>
          )}
          <Link href="/flow" className="hover:underline">
            Tickets
          </Link>
          <Link href="/verification-call" className="hover:underline">
            Verification call →
          </Link>
        </nav>
      </div>

      <div className="flex gap-1 rounded-lg border border-black/10 bg-black/[.02] p-1 text-sm dark:border-white/10 dark:bg-white/[.03]">
        <button
          type="button"
          onClick={() => setTab("chatbot")}
          className={`flex-1 rounded-md px-3 py-1.5 font-medium transition-colors ${
            tab === "chatbot"
              ? "bg-white shadow-sm dark:bg-white/10"
              : "text-black/50 hover:text-black/80 dark:text-white/50 dark:hover:text-white/80"
          }`}
        >
          Chatbot (RAG + Council)
        </button>
        <button
          type="button"
          onClick={() => setTab("payment")}
          className={`flex-1 rounded-md px-3 py-1.5 font-medium transition-colors ${
            tab === "payment"
              ? "bg-white shadow-sm dark:bg-white/10"
              : "text-black/50 hover:text-black/80 dark:text-white/50 dark:hover:text-white/80"
          }`}
        >
          Payment authorization
        </button>
      </div>

      {tab === "chatbot" ? <ChatbotConsole /> : <PaymentLiveConsole />}
    </main>
  );
}
