/**
 * The Phase F banner, required on every page: "Engineering validation dataset
 * — not strategy performance."
 *
 * Lives in its own component, separate from app/layout.tsx, for one reason:
 * layout.tsx imports next/font/google, whose Geist() call is a Next.js
 * build-time macro that only works inside Next's own compiler. Rendering
 * layout.tsx directly in a unit test (outside `next build`/`next dev`) throws
 * "Geist is not a function", so this component holds the banner's actual
 * markup where it CAN be unit tested, and layout.tsx just renders it.
 */
export function PhaseBanner() {
  return (
    <div className="sticky top-0 z-50 border-b border-amber-400/40 bg-amber-950/95 px-4 py-2 text-center text-sm font-semibold text-amber-100 backdrop-blur">
      Engineering validation dataset — not strategy performance.
    </div>
  );
}
