/**
 * O3's mandatory footer, required on every page: not financial advice, not a
 * licensed advisor, and the price-data licensing boundary. A plain
 * synchronous component (no DB read) since the text is fixed, matching
 * phase-banner.tsx's reason for being its own component rather than inlined
 * in layout.tsx -- layout.tsx must stay renderable outside next build/dev
 * for its own unit tests (see tests/layout.test.tsx), so every mandatory
 * site-wide notice lives in a component layout.tsx imports and renders,
 * verified by reading it rather than rendering it directly in tests.
 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border px-4 py-6 text-center text-xs text-muted-foreground">
      Personal research tool. Not financial advice. Not a licensed financial
      advisor. Private, non-commercial use only - the price data source is not
      licensed for redistribution or commercial use.
    </footer>
  );
}
