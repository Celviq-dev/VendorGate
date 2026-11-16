# VendorGate — Day 3: ingestion + validation rules (Zamp PS-2)
#
# Day 3 (Mon 9 Nov 2026) per zamp-ps2-day2-spec.md: checks 1 + 2 only.
#   Check 1 — field validation: required fields present, tax-ID format for
#              the claimed country, IFSC (India) / SWIFT (other) format and
#              bank consistency, account number present.
#   Check 2 — document checks: required document present, right type
#              attached (an invoice is not a GST certificate), compliance
#              document not expired vs the submission date.
# Checks 3-5 (cross-field consistency, duplicates/fraud, risk + decision)
# are Day 4 work and are NOT in this file.
#
# Stdlib only (no pydantic in this environment). Run from this folder:
#   python3 zamp-ps2-day3-validation.py            # per-vendor check log
#   python3 zamp-ps2-day3-validation.py --eval     # summary vs golden set
#
# Day 3 target from the spec: all 30 golden-set submissions parse and
# validate cleanly — i.e. every vendor loads as a VendorSubmission, and
# checks 1+2 pass/fail exactly where the golden set says they should:
#   pass checks 1+2: categories 1, 3, 4, 8, 9 (fail later, at checks 3-4)
#   fail checks 1+2: categories 2, 5, 6, 7, 10, 11, 12
# A vendor that passes 1+2 is NOT approved yet — approval needs every
# check (Day 4) to pass. Precision-first: ambiguous is pending, never
# auto-approved.

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

# --- Tax-ID formats, per claimed country (Day 2 spec) -----------------------
# GSTIN: 15 chars = 2-digit state code + 10-char PAN + entity digit + "Z"
#        + check char. PAN: 5 letters + 4 digits + 1 letter.
# EIN: NN-NNNNNNN. UK VAT: GB + 9 digits. UEN: 9-10 alphanumerics.
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9]Z[A-Z0-9]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
EIN_RE = re.compile(r"^[0-9]{2}-[0-9]{7}$")
UK_VAT_RE = re.compile(r"^GB[0-9]{9}$")
UEN_RE = re.compile(r"^[A-Z0-9]{9,10}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")          # 11 chars, 5th is 0
SWIFT_RE = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Tax-ID type the claimed country actually uses. A GSTIN-shaped string on
# a US vendor (golden cat 6) fails here even though the format itself is
# a valid GSTIN — format alone is not enough, it must match the country.
COUNTRY_TAX_ID = {"India": "GSTIN", "USA": "EIN", "UK": "VAT", "Singapore": "UEN"}

# IFSC first 4 letters identify the bank. Only checked for India; other
# countries use SWIFT codes, where no bank-name mapping is enforced.
IFSC_BANK_PREFIX = {
    "SBIN": "State Bank of India",
    "PUNB": "Punjab National Bank",
    "UTIB": "Axis Bank",
    "ICIC": "ICICI Bank",
    "YESB": "Yes Bank",
    "KKBK": "Kotak Mahindra Bank",
    "HDFC": "HDFC Bank",
    "BARB": "Bank of Baroda",
}

# Required documents by claimed country (from the generated test data).
REQUIRED_DOCS = {
    "India": ["gst_certificate", "pan_card", "cancelled_cheque"],
    "USA": ["incorporation_certificate", "tax_registration", "bank_statement"],
    "UK": ["incorporation_certificate", "tax_registration", "bank_statement"],
    "Singapore": ["incorporation_certificate", "tax_registration", "bank_statement"],
}
# Attached files that are never a valid required document.
WRONG_DOC_TYPES = {"invoice", "quotation", "salary_slip"}

# Categories whose failure is expected at checks 1+2 (Day 2 golden set).
# Everything else should pass 1+2 and fail later (checks 3-4, Day 4).
FAIL_AT_1_2 = {2, 5, 6, 7, 10, 11, 12}

# Known golden-set issues, if any (vendor_id -> explanation). Empty now:
# V014 was labelled cat 2 "MSME certificate missing" but MSME was never
# a required document; it was regenerated (option A) with its PAN card
# actually missing, so it fails checks 1+2 as the golden set expects.
KNOWN_GOLDEN_ISSUES = {}


@dataclass
class Check:
    name: str
    passed: bool
    reason: str

    def __str__(self):
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.reason}"


@dataclass
class Document:
    type: str
    status: str
    expiry: str | None = None


@dataclass
class VendorSubmission:
    vendor_id: str
    legal_name: str
    bank_account_name: str
    country: str
    tax_id: str
    tax_id_type: str
    bank_name: str
    ifsc_or_swift: str
    account_number: str
    contact_person: str
    contact_email: str
    submitted_on: str
    documents: list = field(default_factory=list)
    city: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "VendorSubmission":
        docs = [Document(**d) for d in raw.get("documents", [])]
        return cls(
            documents=docs,
            **{k: v for k, v in raw.items() if k != "documents"},
        )


def validate_tax_id(v: VendorSubmission) -> Check:
    """Check 1a: tax-ID type matches the claimed country, format is valid."""
    expected = COUNTRY_TAX_ID.get(v.country)
    if expected is None:
        return Check("tax_id_country", False, f"Unknown country {v.country!r}")
    if v.tax_id_type != expected:
        article = "an" if expected == "EIN" else "a"
        return Check(
            "tax_id_country",
            False,
            f"Tax ID is {v.tax_id_type} format but vendor claims {v.country}, "
            f"which uses {expected}. Ask vendor to provide {article} {expected}.",
        )
    pattern = {
        "GSTIN": GSTIN_RE,
        "EIN": EIN_RE,
        "VAT": UK_VAT_RE,
        "UEN": UEN_RE,
    }[expected]
    if not pattern.match(v.tax_id or ""):
        return Check(
            "tax_id_format",
            False,
            f"Tax ID format invalid: {v.tax_id!r} is not a valid {expected}. "
            "Ask vendor to correct the tax ID.",
        )
    if expected == "GSTIN" and not PAN_RE.match(v.tax_id[2:12]):
        return Check(
            "tax_id_format",
            False,
            f"GSTIN {v.tax_id!r} does not embed a valid PAN in positions 3-12.",
        )
    return Check("tax_id", True, f"{expected} {v.tax_id!r} valid for {v.country}.")


def validate_bank_details(v: VendorSubmission) -> list[Check]:
    """Check 1b: bank code format + bank/branch consistency, account no."""
    checks = []
    if not v.account_number or not str(v.account_number).isdigit():
        checks.append(Check("account_number", False, "Account number missing or not numeric."))
    else:
        checks.append(Check("account_number", True, "Account number present."))
    code = v.ifsc_or_swift or ""
    if v.country == "India":
        if not IFSC_RE.match(code):
            checks.append(Check("ifsc_format", False, f"IFSC {code!r} is not 11 chars (AAAA0XXXXXX)."))
        else:
            bank = IFSC_BANK_PREFIX.get(code[:4])
            if bank is None:
                checks.append(Check("ifsc_bank", False, f"IFSC prefix {code[:4]} is not a known bank."))
            elif bank != v.bank_name:
                checks.append(
                    Check("ifsc_bank", False,
                          f"IFSC belongs to {bank}, not the stated {v.bank_name}. "
                          "Ask vendor to verify bank details.")
                )
            else:
                checks.append(Check("ifsc_bank", True, f"IFSC matches {v.bank_name}."))
    else:
        if not SWIFT_RE.match(code):
            checks.append(Check("swift_format", False, f"SWIFT/BIC {code!r} is not 8 or 11 chars."))
        else:
            checks.append(Check("swift_format", True, f"SWIFT/BIC {code!r} valid."))
    return checks


def validate_fields(v: VendorSubmission) -> list[Check]:
    """Check 1: required fields present + tax ID + bank details."""
    checks = []
    missing = [
        f for f in (
            "legal_name", "bank_account_name", "country", "tax_id",
            "bank_name", "ifsc_or_swift", "account_number",
            "contact_person", "contact_email", "submitted_on",
        )
        if not getattr(v, f)
    ]
    if missing:
        checks.append(Check("required_fields", False, f"Missing required fields: {', '.join(missing)}."))
    else:
        checks.append(Check("required_fields", True, "All required fields present."))
    if v.contact_email and not EMAIL_RE.match(v.contact_email):
        checks.append(Check("contact_email", False, f"Contact email {v.contact_email!r} is not valid."))
    checks.append(validate_tax_id(v))
    checks.extend(validate_bank_details(v))
    return checks


def validate_documents(v: VendorSubmission) -> list[Check]:
    """Check 2: required doc present, right type, not expired."""
    checks = []
    by_type = {d.type: d for d in v.documents}
    for wrong in sorted(WRONG_DOC_TYPES & set(by_type)):
        checks.append(
            Check("document_type", False,
                  f"Wrong document attached: {wrong} attached instead of a required "
                  "document. Ask vendor to re-attach the correct document.")
        )
    required = REQUIRED_DOCS.get(v.country, [])
    for doc_type in required:
        doc = by_type.get(doc_type)
        if doc is None or doc.status != "present":
            checks.append(
                Check("document_present", False,
                      f"Document missing: {doc_type} missing. Request the document from the vendor.")
            )
            continue
        if doc.expiry:
            try:
                expiry = date.fromisoformat(doc.expiry)
                submitted = date.fromisoformat(v.submitted_on)
            except ValueError:
                checks.append(Check("document_expiry", False, f"{doc_type} expiry {doc.expiry!r} is not a date."))
                continue
            if expiry < submitted:
                months = (submitted.year - expiry.year) * 12 + (submitted.month - expiry.month)
                checks.append(
                    Check("document_expiry", False,
                          f"{doc_type} expired {doc.expiry} "
                          f"({months} month(s) before submission). "
                          + ("Ask vendor for a renewed certificate." if months < 3
                             else "Long expired with no clean path; needs human review."))
                )
            else:
                checks.append(Check("document_expiry", True, f"{doc_type} valid until {doc.expiry}."))
    if not any(c.name == "document_present" and not c.passed for c in checks) \
            and not any(c.name == "document_type" and not c.passed for c in checks):
        checks.append(Check("document_present", True, "All required documents present and of the right type."))
    return checks


def run_checks_1_2(v: VendorSubmission) -> list[Check]:
    return validate_fields(v) + validate_documents(v)


def load_vendors():
    raw = json.loads((HERE / "zamp-ps2-vendors.json").read_text())
    return [VendorSubmission.from_dict(r) for r in raw["vendors"]]


def load_golden():
    raw = json.loads((HERE / "zamp-ps2-golden-set.json").read_text())
    expected = {e["vendor_id"]: e for e in raw["eval_set"] + raw["demo_holdout"]}
    return expected


def main():
    vendors = load_vendors()
    golden = load_golden()
    eval_mode = "--eval" in sys.argv
    mismatches = []
    for v in vendors:
        checks = run_checks_1_2(v)
        failed = [c for c in checks if not c.passed]
        if not eval_mode:
            print(f"\n{v.vendor_id} {v.legal_name} ({v.country}) — "
                  f"{'PASS 1+2' if not failed else f'FAIL 1+2 ({len(failed)})'}")
            for c in checks:
                print(f"  {c}")
        exp = golden.get(v.vendor_id)
        if exp is None:
            continue
        should_fail = exp["category"] in FAIL_AT_1_2
        actually_failed = bool(failed)
        if should_fail != actually_failed:
            if v.vendor_id in KNOWN_GOLDEN_ISSUES:
                mismatches.append(
                    (v.vendor_id, exp["category"], exp["expected_status"],
                     "KNOWN TEST-DATA ISSUE: " + KNOWN_GOLDEN_ISSUES[v.vendor_id],
                     [c.reason for c in failed])
                )
            else:
                mismatches.append(
                    (v.vendor_id, exp["category"], exp["expected_status"],
                     "expected FAIL 1+2" if should_fail else "expected PASS 1+2",
                     [c.reason for c in failed])
                )
    if eval_mode:
        print(f"Vendors parsed: {len(vendors)}/40")
        print(f"Golden-set vendors checked: {len(golden)} (30 eval + 10 holdout)")
        real = [m for m in mismatches if not m[3].startswith("KNOWN")]
        known = [m for m in mismatches if m[3].startswith("KNOWN")]
        if known:
            print(f"Known test-data issues (not validator failures): {len(known)}")
            for m in known:
                print(f"  {m[0]} cat {m[1]}: {m[3]}")
        if real:
            print(f"MISMATCHES vs golden set: {len(real)}")
            for m in real:
                print(f"  {m[0]} cat {m[1]} ({m[2]}): {m[3]}; got: {m[4]}")
        else:
            agree = len(vendors) - len(mismatches)
            print(f"Checks 1+2 agree with the golden set on all {agree} vendors.")
            print("Day 3 target met: every submission parses, and validation "
                  "fails exactly where the answer key says it should.")
        return 1 if real else 0
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
