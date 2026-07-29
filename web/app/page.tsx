import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-6xl px-8 py-24">
      <p className="mb-4 text-base uppercase tracking-[0.2em] text-muted-foreground">
        stockbot
      </p>
      <h1 className="mb-6">US stock research system</h1>
      <p className="mb-12 max-w-3xl text-muted-foreground">
        Phase 1: project skeleton, versioned migrations, frozen configuration.
        No market data yet.
      </p>
      <Link
        href="/health"
        className="inline-block rounded-lg border border-border px-8 py-4 hover:bg-muted"
      >
        Open system health
      </Link>
    </main>
  );
}
