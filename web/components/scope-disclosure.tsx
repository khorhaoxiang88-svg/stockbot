/**
 * O3's permanent scope disclosure, required on /performance and /candidates
 * (the brief's "/screener" -- this project's screener is the /candidates
 * page, see README/S6). Exact wording, not paraphrased: any result only
 * generalises to the population actually screened, and that population is
 * named here rather than left implicit.
 */
export function ScopeDisclosure() {
  return (
    <div className="mb-6 rounded-xl border border-sky-400/25 bg-sky-400/8 px-4 py-3 text-sm text-muted-foreground">
      <strong className="mb-1 block text-sky-200">What this bot covers</strong>
      This system covers primarily profitable, mid-and-large-cap US operating
      companies. It excludes financial companies, REITs, ADRs, unresolvable
      multi-class issuers, and companies without positive earnings. Any
      result generalises only to that population.
    </div>
  );
}
