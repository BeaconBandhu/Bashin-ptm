"use client";

import Link from "next/link";
import { VerificationCallConsole } from "@/components/VerificationCallConsole";

export default function VerificationCallPage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Verification call</h1>
          <p className="text-sm text-black/60 dark:text-white/60">
            A locally-controlled follow-up pass on tickets that already reached a human — see if a
            short Q&amp;A can close them without one.
          </p>
        </div>
        <Link href="/live" className="text-sm text-blue-600 hover:underline dark:text-blue-400">
          ← Live console
        </Link>
      </div>
      <VerificationCallConsole />
    </main>
  );
}
