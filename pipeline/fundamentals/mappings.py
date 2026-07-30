"""Priority-ordered concept mapping, version 1.

Issuers tag the same economic quantity with different XBRL concepts. Revenue
alone appears as Revenues, RevenueFromContractWithCustomerExcludingAssessedTax
or SalesRevenueNet depending on the issuer and the year ASC 606 was adopted.

Each input below lists candidate concepts in priority order. The first concept
that has a usable fact for the period wins, and the winning concept and its
accession are recorded on the derived row. Nothing is averaged or blended: one
tag produces the number, and that tag is named.

Bumping MAPPING_VERSION creates a new generation of derived rows rather than
silently changing existing ones.
"""

from __future__ import annotations

MAPPING_VERSION = "1"

# input_name -> [(taxonomy, concept, priority, notes)]
CONCEPT_MAP: dict[str, list[tuple[str, str, int, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", 1,
         "ASC 606 standard tag, used by most issuers from FY2018"),
        ("us-gaap", "Revenues", 2, "Generic pre-606 and continuing usage"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax", 3,
         "Includes sales taxes collected; less comparable, so lower priority"),
        ("us-gaap", "SalesRevenueNet", 4, "Pre-606 tag, retired but present in history"),
        ("us-gaap", "SalesRevenueGoodsNet", 5, "Goods-only variant"),
    ],
    "cost_of_revenue": [
        ("us-gaap", "CostOfRevenue", 1, "Total cost of revenue"),
        ("us-gaap", "CostOfGoodsAndServicesSold", 2, "Common combined tag"),
        ("us-gaap", "CostOfGoodsSold", 3, "Goods only"),
        ("us-gaap", "CostOfServices", 4, "Services only"),
    ],
    "gross_profit": [
        ("us-gaap", "GrossProfit", 1, "Reported directly when the issuer discloses it"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss", 1, "Net income attributable to the parent"),
        ("us-gaap", "ProfitLoss", 2, "Includes noncontrolling interests"),
        ("us-gaap", "NetIncomeLossAvailableToCommonStockholdersBasic", 3,
         "After preferred dividends"),
    ],
    "pretax_income": [
        ("us-gaap",
         "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
         1, "Standard pretax income tag"),
        ("us-gaap",
         "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
         2, "Variant including equity method results"),
    ],
    "income_tax": [
        ("us-gaap", "IncomeTaxExpenseBenefit", 1, "Total income tax expense"),
    ],
    "eps_diluted": [
        ("us-gaap", "EarningsPerShareDiluted", 1, "Diluted EPS as reported"),
        ("us-gaap", "EarningsPerShareBasicAndDiluted", 2, "Combined when identical"),
    ],
    "stockholders_equity": [
        ("us-gaap", "StockholdersEquity", 1, "Parent-only equity, the book value for P/B"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", 2,
         "Includes NCI; less precise for per-share book value"),
    ],
    "assets": [("us-gaap", "Assets", 1, "Total assets")],
    "liabilities": [("us-gaap", "Liabilities", 1, "Total liabilities")],
    "current_assets": [("us-gaap", "AssetsCurrent", 1, "Total current assets")],
    "current_liabilities": [("us-gaap", "LiabilitiesCurrent", 1, "Total current liabilities")],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", 1, "Cash and equivalents"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", 2,
         "Includes restricted cash; overstates free cash slightly"),
    ],
    "short_term_debt": [
        ("us-gaap", "ShortTermBorrowings", 1, "Short-term borrowings"),
        ("us-gaap", "DebtCurrent", 2, "Current portion of total debt"),
        ("us-gaap", "LongTermDebtCurrent", 3, "Current maturities of long-term debt"),
    ],
    "long_term_debt": [
        ("us-gaap", "LongTermDebtNoncurrent", 1, "Long-term debt excluding current portion"),
        ("us-gaap", "LongTermDebt", 2, "Total long-term debt including current portion"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss", 1, "Operating income, the EBIT base"),
    ],
    "depreciation_amortization": [
        ("us-gaap", "DepreciationDepletionAndAmortization", 1, "Cash-flow statement D&A"),
        ("us-gaap", "DepreciationAmortizationAndAccretionNet", 2, "Includes accretion"),
        ("us-gaap", "DepreciationAndAmortization", 3, "Income-statement variant"),
    ],
    "interest_expense": [
        ("us-gaap", "InterestExpense", 1, "Total interest expense"),
        ("us-gaap", "InterestExpenseDebt", 2, "Interest on debt only"),
        ("us-gaap", "InterestExpenseNonoperating", 3, "Nonoperating interest expense"),
    ],
    "cfo": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", 1, "Operating cash flow"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations", 2,
         "Continuing operations only"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", 1, "Capital expenditure"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets", 2, "Broader productive assets"),
    ],
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding", 1,
         "Cover-page share count, closest to a point-in-time figure"),
        ("us-gaap", "CommonStockSharesOutstanding", 2, "Balance-sheet share count"),
        ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", 3,
         "Period average, not point-in-time; last resort"),
    ],
}

# Which inputs are period totals (duration) versus balances (instant).
DURATION_INPUTS = {
    "revenue", "cost_of_revenue", "gross_profit", "net_income", "pretax_income",
    "income_tax", "eps_diluted", "operating_income", "depreciation_amortization",
    "interest_expense", "cfo", "capex",
}
INSTANT_INPUTS = {
    "stockholders_equity", "assets", "liabilities", "current_assets",
    "current_liabilities", "cash", "short_term_debt", "long_term_debt",
    "shares_outstanding",
}


def seed_concept_mappings(conn, mapping_version: str = MAPPING_VERSION) -> int:
    """Write the mapping into concept_mappings. Idempotent."""
    written = 0
    for metric_name, candidates in CONCEPT_MAP.items():
        for taxonomy, concept, priority, notes in candidates:
            conn.execute(
                """
                INSERT INTO concept_mappings
                    (metric_name, taxonomy, concept, priority, mapping_version,
                     valid_from, valid_to, notes)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT (metric_name, taxonomy, concept, mapping_version)
                DO UPDATE SET priority = excluded.priority, notes = excluded.notes
                """,
                (metric_name, taxonomy, concept, priority, mapping_version, notes),
            )
            written += 1
    return written


def load_concept_mappings(conn, mapping_version: str = MAPPING_VERSION) -> dict[str, list[tuple[str, str]]]:
    """input_name -> [(taxonomy, concept)] in priority order, read from the table."""
    result: dict[str, list[tuple[str, str]]] = {}
    for row in conn.execute(
        "SELECT metric_name, taxonomy, concept FROM concept_mappings "
        "WHERE mapping_version = ? ORDER BY metric_name, priority",
        (mapping_version,),
    ):
        result.setdefault(row["metric_name"], []).append((row["taxonomy"], row["concept"]))
    return result
