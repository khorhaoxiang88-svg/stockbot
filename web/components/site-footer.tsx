/**
 * O3's mandatory footer, required on every page: not financial advice, not a
 * licensed advisor, and the price-data licensing boundary. A plain
 * synchronous component (no DB read) since the text is fixed. layout.tsx
 * can't be rendered directly in a unit test (next/font/google), so every
 * mandatory site-wide notice lives in its own component that layout.tsx
 * imports and renders, verified by reading layout.tsx rather than rendering
 * it (see tests/site-footer.test.tsx, tests/scope-disclosure.test.tsx).
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
