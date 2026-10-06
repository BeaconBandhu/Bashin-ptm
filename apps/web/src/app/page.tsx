import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center gap-8 px-4 text-center">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">AI Teammate — Payment Authorization</h1>
        <p className="text-black/60 dark:text-white/60">
          A guardrailed, audit-traced AST execution engine — Phase 0 vertical slice.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-4">
        <Link
          href="/transfer"
          className="rounded-lg bg-blue-600 px-6 py-3 font-medium text-white hover:bg-blue-700"
        >
          Send money
        </Link>
        <Link
          href="/flow"
          className="rounded-lg border border-black/15 px-6 py-3 font-medium hover:border-blue-400 dark:border-white/20"
        >
          Money flow &amp; governance
        </Link>
        <Link
          href="/live"
          className="rounded-lg border border-black/15 px-6 py-3 font-medium hover:border-blue-400 dark:border-white/20"
        >
          Live execution console
        </Link>
        <Link
          href="/graph"
          className="rounded-lg border border-black/15 px-6 py-3 font-medium hover:border-blue-400 dark:border-white/20"
        >
          Live AST graph
        </Link>
      </div>
    </main>
  );
}
