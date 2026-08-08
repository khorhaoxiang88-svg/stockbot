/**
 * O3's permanent scope disclosure must appear on /performance and
 * /candidates (this project's screener). Tested as its own component,
 * same reasoning as site-footer.tsx.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ScopeDisclosure } from "@/components/scope-disclosure";

describe("scope disclosure", () => {
  it("renders the exact required text", () => {
    const html = renderToStaticMarkup(<ScopeDisclosure />);
    expect(html).toContain(
      "This system covers primarily profitable, mid-and-large-cap US operating",
    );
    expect(html).toContain("companies.");
    expect(html).toContain(
      "It excludes financial companies, REITs, ADRs, unresolvable",
    );
    expect(html).toContain("multi-class issuers, and companies without positive earnings.");
    expect(html).toContain("Any result generalises only to that population.");
  });
});
