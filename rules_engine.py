"""
rules_engine.py
----------------
Rule-based compliance engine for the Legal Metrology (Packaged Commodities)
Rules, 2011 (India).

This module is intentionally kept separate from OCR/Flask code so the legal
rule logic can be audited, unit-tested and updated independently as
notifications/amendments to the Rules are issued.

NOTE ON SCOPE (read this before treating output as legal advice):
This encodes the commonly-enforced declarations under Rule 6 (declarations
on every package) and the Second Schedule (minimum size of letters/numerals
for the net quantity declaration). It is a *decision-support* engine for
enforcement officers, not a substitute for the Rules themselves or for
officer judgement. Edge cases (multi-piece packages, wholesale packages,
commodities sold by number under Rule 2(l), exempted categories under
Rule 26, e-commerce specific disclosures under Rule 6(9)) are flagged as
"needs manual review" rather than auto-passed/failed.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"  # detected but needs a human to confirm (e.g. font size borderline)


@dataclass
class DeclarationResult:
    code: str
    label: str
    rule_ref: str
    status: Status
    detail: str
    matched_text: str = ""


@dataclass
class ComplianceReport:
    declarations: list = field(default_factory=list)
    overall_status: Status = Status.REVIEW
    score: float = 0.0  # % of declarations that PASS
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rule 6: mandatory declarations and their detection patterns
# ---------------------------------------------------------------------------

MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
          r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
          r"nov(?:ember)?|dec(?:ember)?")

PATTERNS = {
    "manufacturer_address": [
        r"\b(mfg\.?|mfd\.?|manufactured|marketed|marketer|packed|packer|importer|imported)[\s.:]*(by|for)\b",
        r"\b(mfg|mfd|mktd|pkd)[\s.:]*(by|for)\b",
    ],
    "generic_name": [
        # weak signal on its own; combined with a heuristic (first line / largest text)
        r"\b(contents?|ingredients?)\b",
    ],
    "net_quantity": [
        r"\bnet\s*(wt\.?|weight|qty\.?|quantity|contents?|volume|vol\.?)\b",
        r"\b\d+(\.\d+)?\s?(g|gm|gms|kg|ml|l|litre|liter|mg|pcs?|pieces|n|nos)\b",
    ],
    "mrp": [
        r"\bm\.?r\.?p\.?\b",
        r"\bmax(imum)?\s*retail\s*price\b",
        r"(₹|rs\.?|inr)\s*\d+",
    ],
    "mfg_date": [
        rf"\b(mfg|manufactur(ed|ing)|pkd|packed|packing)\.?\s*(date|dt|on)?\s*[:\-]?\s*(\d{{1,2}}[\/\-])?({MONTHS}|\d{{1,2}})[\/\-\s.]?\d{{2,4}}",
        r"\b(mfg|pkd)\.?\s*(date|dt)?\s*[:\-]?\s*\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}",
    ],
    "expiry_or_use_by": [
        r"\b(exp(iry)?|use\s*by|best\s*before)\.?\s*(date|dt)?\s*[:\-]?",
    ],
    "consumer_care": [
        r"\b(consumer|customer)\s*care\b",
        r"\b(toll\s*free|helpline)\b",
        r"[\w.+-]+@[\w-]+\.[a-z]{2,}",  # email
        r"\b(1800|1[- ]?800)[\d\- ]{6,}\b",  # toll-free style
    ],
    "country_of_origin": [
        r"\bcountry\s*of\s*origin\b",
        r"\bmade\s*in\s+\w+",
    ],
    "unit_sale_price": [
        r"\bunit\s*sale\s*price\b|\busp\b",
    ],
}

DECLARATION_META = {
    "manufacturer_address": ("Name & address of manufacturer/packer/importer", "Rule 6(1)(a)"),
    "generic_name": ("Common/generic name of the commodity", "Rule 6(1)(b)"),
    "net_quantity": ("Net quantity (standard units)", "Rule 6(1)(c) / Rule 8"),
    "mfg_date": ("Month & year of manufacture/pack/import", "Rule 6(1)(e)"),
    "mrp": ("Maximum Retail Price (inclusive of all taxes)", "Rule 6(1)(f)"),
    "consumer_care": ("Consumer care / customer complaint details", "Rule 6(1)(d)"),
    "country_of_origin": ("Country of origin (imported goods)", "Rule 6(8) / Legal Metrology (PC) 3rd Amdt."),
}

# Core declarations required on virtually every retail package.
CORE_DECLARATIONS = [
    "manufacturer_address",
    "net_quantity",
    "mfg_date",
    "mrp",
    "consumer_care",
]

# ---------------------------------------------------------------------------
# Second Schedule (Rule 6): minimum size of letters/numerals for the
# net quantity declaration, based on the area of the Principal Display
# Panel (PDP).
# ---------------------------------------------------------------------------

FONT_SIZE_TABLE = [
    # (max_area_cm2, min_height_mm, min_height_mm_if_more_than_25cm_wide... simplified)
    (100, 1.0),
    (500, 2.0),
    (2500, 4.0),
    (float("inf"), 6.0),
]


def required_font_height_mm(pdp_area_cm2: float) -> float:
    """Return the minimum letter/numeral height (mm) required for the net
    quantity declaration for a given Principal Display Panel area (Second
    Schedule, Rule 6)."""
    for max_area, height in FONT_SIZE_TABLE:
        if pdp_area_cm2 <= max_area:
            return height
    return FONT_SIZE_TABLE[-1][1]


def _search_any(patterns, text):
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def check_declarations(ocr_text: str) -> list:
    """Run every declaration's regex family against the OCR'd label text.
    Returns a list of DeclarationResult (REVIEW status pre-font-check)."""
    results = []
    for code in CORE_DECLARATIONS + ["country_of_origin"]:
        label, ref = DECLARATION_META[code]
        match = _search_any(PATTERNS.get(code, []), ocr_text)
        if match:
            status = Status.PASS
            detail = "Declaration detected on label."
        else:
            # country of origin is conditional (only imported goods) -> REVIEW not FAIL
            status = Status.REVIEW if code == "country_of_origin" else Status.FAIL
            detail = ("Not required unless goods are imported — confirm manually."
                       if code == "country_of_origin"
                       else "No matching text found on the label. Likely missing or unreadable.")
        results.append(DeclarationResult(
            code=code, label=label, rule_ref=ref,
            status=status, detail=detail, matched_text=match or ""
        ))
    return results


def check_font_size(net_qty_glyph_heights_mm: list, pdp_area_cm2: float):
    """Compare measured glyph heights (mm, from OCR bounding boxes calibrated
    against a known reference) for the net-quantity string against the
    Second Schedule minimum for the given PDP area."""
    required = required_font_height_mm(pdp_area_cm2)
    if not net_qty_glyph_heights_mm:
        return DeclarationResult(
            code="font_size", label="Net quantity font size", rule_ref="Rule 6 / Second Schedule",
            status=Status.REVIEW,
            detail=f"Could not measure glyph height automatically. Minimum required for a "
                    f"{pdp_area_cm2:.0f} cm\u00b2 panel is {required} mm — verify manually.",
        )
    measured = max(net_qty_glyph_heights_mm)  # OCR often under-segments; take the tallest run
    if measured + 0.15 >= required:  # small tolerance for OCR/DPI estimation error
        status = Status.PASS
        detail = f"Measured ~{measured:.2f} mm >= required {required} mm for a {pdp_area_cm2:.0f} cm\u00b2 panel."
    else:
        status = Status.FAIL
        detail = f"Measured ~{measured:.2f} mm < required {required} mm for a {pdp_area_cm2:.0f} cm\u00b2 panel."
    return DeclarationResult(
        code="font_size", label="Net quantity font size", rule_ref="Rule 6 / Second Schedule",
        status=status, detail=detail,
    )


def build_report(declaration_results: list) -> ComplianceReport:
    report = ComplianceReport(declarations=declaration_results)
    scored = [d for d in declaration_results if d.status != Status.REVIEW]
    passed = [d for d in scored if d.status == Status.PASS]
    report.score = round(100 * len(passed) / len(scored), 1) if scored else 0.0
    report.violations = [d for d in declaration_results if d.status == Status.FAIL]
    report.warnings = [d for d in declaration_results if d.status == Status.REVIEW]

    if any(d.status == Status.FAIL for d in declaration_results):
        report.overall_status = Status.FAIL
    elif any(d.status == Status.REVIEW for d in declaration_results):
        report.overall_status = Status.REVIEW
    else:
        report.overall_status = Status.PASS
    return report
