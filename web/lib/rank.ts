/**
 * Rankability rules. Mirrors pipeline/universe/classify.py — if you change one,
 * change the other, and update the tests on both sides.
 *
 * Only common stock is ranked. Preferred shares, warrants, units, ETFs and ETNs
 * are excluded for what they ARE, not because their classification failed.
 */

export const RANKABLE_SECURITY_TYPES = ["common_stock"] as const;
export const RANKABLE_CONFIDENCES = ["high", "medium"] as const;

export function rankExclusionReason(
  securityType: string,
  confidence: string,
): string | null {
  if (securityType === "unknown") {
    return "classification is unknown; unknown securities are never ranked";
  }
  if (!RANKABLE_SECURITY_TYPES.includes(securityType as "common_stock")) {
    return `security_type '${securityType}' is not common stock; only common stock is ranked`;
  }
  if (!RANKABLE_CONFIDENCES.includes(confidence as "high" | "medium")) {
    return `classification confidence '${confidence}' is too low to rank`;
  }
  return null;
}

export function isRankable(securityType: string, confidence: string): boolean {
  return rankExclusionReason(securityType, confidence) === null;
}
