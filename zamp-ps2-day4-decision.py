# VendorGate — Day 4: cross-field consistency + risk scoring + decision (Zamp PS-2)
#
# Day 4 (Tue 10 Nov 2026) per zamp-ps2-day2-spec.md: checks 3, 4, 5.
#   Check 3 — cross-field consistency: submission name vs bank-account name
#              after normalisation (case, punctuation, legal suffixes are the
#              same; a genuinely different entity is not), tax ID vs claimed
#              country, GSTIN vs PAN consistency, document expiry vs
#              submission date.
#   Check 4 — duplicate + fraud signals: same vendor + tax ID seen before
#              with the same bank details (dedupe, link to original) or with
#              different bank details (reject, flag for human review).
#   Check 5 — risk score + decision, rules as versioned data (RISK_RULES
#              below, not prompt text). Approve needs every check to pass.
#              One missing/wrong item is pending with a named fix. A fraud
#              signal or multiple failures is reject.
#
# Reuses checks 1+2 from zamp-ps2-day3-validation.py (single source of
# truth). Stdlib only. Run from this folder:
#   python3 zamp-ps2-day4-decision.py            # per-vendor decision log
#   python3 zamp-ps2-day4-decision.py --eval     # summary vs golden set
#
# Decision rules (versioned data):
#   - Fraud signal (name mismatch after normalisation, or duplicate with
#     different bank details) -> rejected, whatever else passes.
#   - Otherwise each distinct failure cause scores 10 points; a document
#     expired 12+ months before submission adds 15 more. Score >= 30
#     (multiple independent failures) -> rejected; 1-29 -> pending with a
#     named fix; 0 -> approved.
#   - A duplicate with identical bank details scores 0 but is pending:
#     link to the original case, do not create a second vendor.
#   - Root-cause merge: when the tax ID is for another country than the
#     one claimed, the missing/wrong documents for the claimed country are
#     the same single mistake (an India-format submission filed as USA/UK),
#     so they count as one cause, not several. Without this merge V025/V026
#     would wrongly score as multi-failure rejects; the golden set says
#     pending.
# Precision-first: ambiguous is pending, never auto-approved. The KPI that
# matters most: false-approve rate on the golden set must be zero.

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

# --- Reuse Day 3 (checks 1+2, models, loaders) ------------------------------
_spec = importlib.util.spec_from_file_location(
    "zamp_day3", HERE / "zamp-ps2-day3-validation.py"
)
day3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(day3)

# --- Risk rules: versioned data, not prompt text ----------------------------
# cause -> points. Fraud causes bypass scoring (auto-reject).
RISK_POINTS = {
    "tax_format": 10,            # tax ID format invalid for claimed country
    "tax_country": 10,           # tax ID type does not match claimed country
    "bank_details": 10,          # IFSC/SWIFT mismatch or bad account number
    "required_fields": 10,       # a required submission field is missing
    "contact_email": 10,         # contact email is not a valid address
    "document_missing": 10,      # a required document is missing
    "document_wrong_type": 10,   # wrong document attached (invoice, etc.)
    "document_expired": 10,      # a required document expired before submission
    "document_expired_long": 15,  # ... and expired 12+ months before submission
}
REJECT_AT_SCORE = 30
LONG_EXPIRY_MONTHS = 12

# Legal-suffix tokens stripped (from the end) when comparing names.
LEGAL_SUFFIX_TOKENS = {
    "pvt", "ltd", "private", "limited", "llc", "pte", "inc", "corp",
    "co", "company", "llp",
}


def normalize_name(name: str) -> str:
    """Lowercase, drop punctuation, drop trailing legal-suffix tokens.

    'Ananya Textiles Pvt. Ltd.' -> 'ananya textiles'; 'Imran Trading
    Company' -> 'imran trading'. A genuinely different entity keeps
    different tokens, so 'Meera Pharma Distributors' never matches
    'Sanjay Agro Products'.
    """
    text = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    tokens = [t for t in text.split() if t]
    while tokens and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def check_name_match(v) -> day3.Check:
    """Check 3a: submission name vs bank-account name, normalised."""
    a, b = normalize_name(v.legal_name), normalize_name(v.bank_account_name)
    if a and a == b:
        return day3.Check(
            "name_match", True,
            f"Bank account name matches after normalisation ({v.bank_account_name!r}).",
        )
    return day3.Check(
        "name_match", False,
        f"FRAUD SIGNAL: bank account belongs to a different legal entity: "
        f"submission is {v.legal_name!r} but the bank account is "
        f"{v.bank_account_name!r}. Flag for human review.",
    )


def check_gstin_pan(v) -> day3.Check:
    """Check 3b: GSTIN vs PAN consistency (embedded PAN must be valid)."""
    if v.tax_id_type != "GSTIN":
        return day3.Check(
            "gstin_pan", True,
            f"No GSTIN to cross-check (tax ID type is {v.tax_id_type}).",
        )
    if len(v.tax_id or "") == 15 and day3.PAN_RE.match(v.tax_id[2:12]):
        return day3.Check(
            "gstin_pan", True,
            f"GSTIN embeds a valid PAN ({v.tax_id[2:12]}).",
        )
    return day3.Check(
        "gstin_pan", False,
        f"GSTIN {v.tax_id!r} does not embed a valid PAN in positions 3-12.",
    )


def check_tax_country(v) -> day3.Check:
    """Check 3c: tax ID vs claimed country (cross-field restatement)."""
    expected = day3.COUNTRY_TAX_ID.get(v.country)
    if expected is None:
        return day3.Check("tax_country", False, f"Unknown country {v.country!r}")
    if v.tax_id_type != expected:
        article = "an" if expected == "EIN" else "a"
        return day3.Check(
            "tax_country", False,
            f"Tax ID is {v.tax_id_type} format but vendor claims {v.country}, "
            f"which uses {expected}. Ask vendor to provide {article} {expected}.",
        )
    return day3.Check(
        "tax_country", True,
        f"Tax ID type {v.tax_id_type} matches claimed country {v.country}.",
    )


def check_expiry_vs_submission(v, checks_1_2) -> day3.Check:
    """Check 3d: document expiry vs submission date (restates check 2)."""
    expired = [c for c in checks_1_2
               if c.name == "document_expiry" and not c.passed]
    if not expired:
        return day3.Check(
            "expiry_vs_submission", True,
            "No required document expired before the submission date.",
        )
    worst = max(expired, key=lambda c: c.reason)
    return day3.Check("expiry_vs_submission", False, worst.reason)


def run_check_3(v, checks_1_2):
    return [
        check_name_match(v),
        check_gstin_pan(v),
        check_tax_country(v),
        check_expiry_vs_submission(v, checks_1_2),
    ]


def find_duplicate(v, registry):
    """Check 4: same vendor (normalised name + tax ID) seen before?

    registry maps (normalised name, tax_id) -> original vendor, and only
    holds vendors that passed checks 1+2, so a duplicate of a failed
    submission is not treated as a duplicate of a real vendor. Returns
    (original, same_bank: bool) or (None, None).
    """
    key = (normalize_name(v.legal_name), v.tax_id)
    orig = registry.get(key)
    if orig is None:
        return None, None
    same_bank = (
        orig.bank_name == v.bank_name
        and orig.ifsc_or_swift == v.ifsc_or_swift
        and str(orig.account_number) == str(v.account_number)
    )
    return orig, same_bank


def cause_of(check: day3.Check, v) -> str | None:
    """Map a failed check to its single risk cause (or None if it passed)."""
    if check.passed:
        return None
    if check.name in ("tax_id_format", "gstin_pan"):
        return "tax_format"
    if check.name in ("tax_id_country", "tax_country"):
        return "tax_country"
    if check.name in ("account_number", "ifsc_format", "ifsc_bank", "swift_format"):
        return "bank_details"
    if check.name == "required_fields":
        return "required_fields"
    if check.name == "contact_email":
        return "contact_email"
    if check.name == "document_present":
        return "document_missing"
    if check.name == "document_type":
        return "document_wrong_type"
    if check.name == "document_expiry":
        return "document_expired"
    if check.name == "expiry_vs_submission":
        return "document_expired"  # same cause as check 2's expiry row
    if check.name == "name_match":
        return "name_mismatch"
    return "other"


def expiry_months(v) -> int:
    """Months between the earliest expired document and submission (0 if none)."""
    worst = 0
    try:
        submitted = date.fromisoformat(v.submitted_on)
    except ValueError:
        return 0
    for d in v.documents:
        if d.expiry:
            try:
                exp = date.fromisoformat(d.expiry)
            except ValueError:
                continue
            if exp < submitted:
                months = (submitted.year - exp.year) * 12 + (submitted.month - exp.month)
                worst = max(worst, months)
    return worst


@dataclass
class Decision:
    vendor_id: str
    status: str  # approved | pending | rejected
    risk_score: int
    causes: dict
    reasons: list
    duplicate_of: str | None = None
    fraud: bool = False


def decide(v, checks_1_2, checks_3, duplicate):
    """Check 5: risk score + decision from the full check log."""
    orig, same_bank = duplicate
    all_checks = checks_1_2 + checks_3
    causes = {}
    for c in all_checks:
        cause = cause_of(c, v)
        if cause is None or cause == "other":
            continue
        causes[cause] = RISK_POINTS.get(cause, 0)
    # Root-cause merge: a wrong-country tax ID explains the wrong-country
    # document set, so it is one mistake, not several.
    if "tax_country" in causes:
        causes.pop("document_missing", None)
        causes.pop("document_wrong_type", None)
    # Long expiry is a second, independent failure on top of expiry itself.
    months = expiry_months(v)
    if months >= LONG_EXPIRY_MONTHS and "document_expired" in causes:
        causes["document_expired_long"] = RISK_POINTS["document_expired_long"]
    fraud = "name_mismatch" in causes or (orig is not None and not same_bank)
    dedupe = orig is not None and same_bank
    score = sum(causes.values())
    reasons = list(dict.fromkeys(c.reason for c in all_checks if not c.passed))
    if orig is not None and not same_bank:
        reasons.append(
            f"FRAUD SIGNAL: duplicate of {orig.vendor_id} with different bank "
            f"details ({v.bank_name} {v.ifsc_or_swift} vs "
            f"{orig.bank_name} {orig.ifsc_or_swift}). Flag for human review."
        )
    if dedupe:
        reasons.append(
            f"Duplicate submission of {orig.vendor_id}: same vendor, same bank "
            f"details. Link to the original case, do not create a second vendor."
        )
    if fraud:
        status = "rejected"
    elif score >= REJECT_AT_SCORE:
        status = "rejected"
        reasons.append(
            f"Multiple independent failures (risk score {score}). No clean path; "
            "needs human review."
        )
    elif score > 0 or dedupe:
        status = "pending"
    else:
        status = "approved"
    return Decision(
        vendor_id=v.vendor_id, status=status, risk_score=score,
        causes=causes, reasons=reasons,
        duplicate_of=orig.vendor_id if orig is not None else None,
        fraud=fraud,
    )


def run_all():
    vendors = day3.load_vendors()
    registry = {}
    results = {}
    for v in vendors:
        checks_1_2 = day3.run_checks_1_2(v)
        checks_3 = run_check_3(v, checks_1_2)
        dup = find_duplicate(v, registry)
        results[v.vendor_id] = decide(v, checks_1_2, checks_3, dup)
        # Only a submission that passed checks 1+2 can be the "original"
        # a later duplicate links to.
        if not any(not c.passed for c in checks_1_2):
            registry[(normalize_name(v.legal_name), v.tax_id)] = v
    return vendors, results


def main():
    vendors, results = run_all()
    golden = day3.load_golden()
    eval_mode = "--eval" in sys.argv
    mismatches = []
    false_approves = []
    counts = {"approved": 0, "pending": 0, "rejected": 0}
    for v in vendors:
        d = results[v.vendor_id]
        counts[d.status] += 1
        if not eval_mode:
            print(f"\n{v.vendor_id} {v.legal_name} ({v.country}) — "
                  f"{d.status.upper()} "
                  f"(risk {'FRAUD' if d.fraud else d.risk_score})")
            for r in d.reasons:
                print(f"  - {r}")
            if d.status == "approved":
                print("  - All 5 checks pass.")
        exp = golden.get(v.vendor_id)
        if exp is None:
            continue
        # Golden set says approve / pending / reject; engine says
        # approved / pending / rejected.
        expected = {"approve": "approved", "pending": "pending",
                    "reject": "rejected"}[exp["expected_status"]]
        if d.status != expected:
            mismatches.append((v.vendor_id, exp["category"],
                               expected, d.status, d.reasons))
        if d.status == "approved" and expected != "approved":
            false_approves.append(v.vendor_id)
    if eval_mode:
        print(f"Decisions: {counts['approved']} approved, "
              f"{counts['pending']} pending, {counts['rejected']} rejected "
              f"(golden set expects 11 / 23 / 6)")
        print(f"False-approve rate on the golden set: "
              f"{len(false_approves)}/40"
              + (" (must be zero)" if false_approves else " — zero, KPI met"))
        if mismatches:
            print(f"MISMATCHES vs golden set: {len(mismatches)}")
            for m in mismatches:
                print(f"  {m[0]} cat {m[1]}: expected {m[2]}, got {m[3]}; {m[4]}")
        else:
            print("Decisions agree with the golden set on all 40 vendors.")
        return 1 if (mismatches or false_approves) else 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
