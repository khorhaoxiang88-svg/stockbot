/**
 * O3's mandatory footer must appear on every page. Tested as its own
 * component for the same reason phase-banner.tsx is (see layout.test.tsx):
 * layout.tsx can't be rendered directly in a unit test (next/font/google),
 * so layout.tsx's inclusion of SiteFooter ahead of </body> is verified by
 * reading it, and the text itself is verified here.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SiteFooter } from "@/components/site-footer";

describe("site footer", () => {
  it("renders the exact required disclosure text", () => {
    const html = renderToStaticMarkup(<SiteFooter />);
    expect(html).toContain("Personal research tool.");
    expect(html).toContain("Not financial advice.");
    expect(html).toContain("Not a licensed financial advisor.");
    expect(html).toContain("Private, non-commercial use only");
    expect(html).toContain(
      "the price data source is not licensed for redistribution or commercial use.",
    );
  });

  it("renders as a footer element", () => {
    const html = renderToStaticMarkup(<SiteFooter />);
    expect(html).toMatch(/^<footer/);
  });
});
