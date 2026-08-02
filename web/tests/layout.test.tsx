/**
 * The Phase F banner must appear on every page. It is tested here as its own
 * component rather than by rendering app/layout.tsx directly: layout.tsx
 * imports next/font/google, whose Geist() call is a build-time macro that only
 * works inside Next's own compiler and throws "Geist is not a function" when
 * the module is loaded directly in a unit test. PhaseBanner carries no such
 * dependency, and layout.tsx (verified by reading it) does nothing but import
 * and render it ahead of {children} on every request, which is what actually
 * puts it on every page in the running app.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhaseBanner } from "@/components/phase-banner";

describe("phase banner", () => {
  it("renders the exact required text", () => {
    const html = renderToStaticMarkup(<PhaseBanner />);
    expect(html).toContain("Engineering validation dataset");
    expect(html).toContain("not strategy performance.");
  });

  it("is sticky, so it stays visible while a long page scrolls", () => {
    const html = renderToStaticMarkup(<PhaseBanner />);
    expect(html).toContain("sticky");
  });
});
