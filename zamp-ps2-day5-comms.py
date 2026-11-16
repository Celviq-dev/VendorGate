# VendorGate — Day 5: comms / pending loop + dashboard data (Zamp PS-2)
#
# Day 5 (Wed 11 Nov 2026) per zamp-ps2-day2-spec.md and the starter pack:
#   - Pending loop: for each pending vendor, a follow-up message listing
#     exactly what is missing or inconsistent, addressed to the vendor
#     contact. A resubmission re-runs the checks; the audit trail shows
#     both rounds.
#   - Approved: approval-ready package (verified fields, check log,
#     decision). Rejected: reason stated per failed check; fraud flags
#     go to human review, and the vendor is told only that the submission
#     cannot proceed as filed and is under manual review (no fraud
#     accusation is sent to a vendor).
#   - Dashboard data (the Streamlit app in zamp-ps2-day5-app.py renders
#     it): submissions by status, median time-to-decision, pending
#     ageing, top failure causes, false-approve rate on the golden set.
#
# Reuses Day 4 decisions (single source of truth). Stdlib only, except
# the Streamlit app which is a separate file. Run from this folder:
#   python3 zamp-ps2-day5-comms.py              # summary + audit JSON
#   python3 zamp-ps2-day5-comms.py --eval       # assertions vs golden set
#   python3 zamp-ps2-day5-comms.py --vendor V014  # print one vendor's comms
#
# Verified 2026-11-11: comms generated for all 40 vendors (11 approved
# packages, 23 pending follow-ups, 6 rejected human-review flags);
# scripted resubmissions: V014 (attach PAN card) -> approved, V025
# (country corrected to India + IFSC corrected) -> approved, V026
# (country corrected to USA only) -> still pending on missing US
# documents, which is the partial-fix case the audit trail must show.

import importlib.util
import json
import statistics
import sys
import time
from copy import deepcopy
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
AS_OF = date(2026, 11, 11)  # Day 5 date; pass --as-of YYYY-MM-DD to change

_spec = importlib.util.spec_from_file_location(
    "zamp_day4", HERE / "zamp-ps2-day4-decision.py"
)
day4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(day4)
day3 = day4.day3

# --- Named fix per risk cause (what the vendor is actually asked to do) -----
FIX_TEXT = {
    "document_missing": "Attach the missing required document named in the message below.",
    "document_wrong_type": "Reattach the correct document type. An invoice is not a tax certificate.",
    "document_expired": "Send a renewed copy of the expired document.",
    "document_expired_long": "Send a renewed copy. The document expired more than a year ago, so a fresh copy is needed, not a scan of the old one.",
    "tax_format": "Correct the tax ID so it matches the format for your country.",
    "tax_country": "Correct the country on the submission, or provide the tax ID for the country claimed. The tax ID and the country must agree.",
    "bank_details": "Verify the bank details. The IFSC or SWIFT code must match the bank and branch, and the account number must be complete.",
    "required_fields": "Fill in the missing submission field named in the message below.",
    "contact_email": "Provide a valid contact email address.",
    "name_mismatch": "Human review only. Do not ask the vendor to fix this by message.",
}

DOC_LABELS = {
    "gst_certificate": "GST certificate",
    "pan_card": "PAN card",
    "cancelled_cheque": "cancelled cheque or bank statement",
    "incorporation_certificate": "incorporation certificate",
    "tax_registration": "tax registration proof",
    "bank_statement": "bank statement",
}


def build_comms(v, d):
    """One comms record per vendor, from its Day 4 decision."""
    if d.status == "approved":
        return {
            "vendor_id": v.vendor_id,
            "kind": "approval_package",
            "to": v.contact_email,
            "subject": f"Vendor onboarding approved: {v.legal_name} ({v.vendor_id})",
            "body": (
                f"Hi {v.contact_person},\n\n"
                f"{v.legal_name} ({v.vendor_id}) passed all 5 checks and is "
                "approved for onboarding. The approval package lists the "
                "verified fields, the full check log and this decision.\n\n"
                "Verified: tax ID, bank details, documents, name match and "
                "no duplicate or fraud signal. Risk score 0.\n\n"
                "VendorGate"
            ),
            "internal_note": "Approval-ready package. Verified fields, check log and decision attached.",
        }
    if d.status == "pending":
        items = []
        for cause in d.causes:
            if cause in ("document_expired_long",):
                continue
            items.append(f"- {FIX_TEXT.get(cause, cause)}")
        # Duplicate with same bank details has no vendor fix: link, do not re-ask.
        if d.duplicate_of and not d.fraud:
            items = [
                f"- No action needed from you. This looks like a duplicate of "
                f"case {d.duplicate_of} with the same bank details, so it is "
                "linked to the original case instead of creating a second vendor."
            ]
        reasons = "\n".join(f"  * {r}" for r in d.reasons)
        return {
            "vendor_id": v.vendor_id,
            "kind": "pending_followup",
            "to": v.contact_email,
            "subject": f"Action needed to onboard {v.legal_name} ({v.vendor_id})",
            "body": (
                f"Hi {v.contact_person},\n\n"
                f"Your vendor onboarding submission for {v.legal_name} "
                f"({v.vendor_id}) needs a few corrections before we can approve it:\n\n"
                + "\n".join(items)
                + "\n\nWhat we found:\n"
                + reasons
                + "\n\nPlease reply with the corrected details and we will "
                "re-run the checks. Nothing else about your submission needs to change.\n\n"
                "VendorGate"
            ),
            "internal_note": "Pending follow-up sent. Resubmission re-runs all 5 checks; audit trail keeps both rounds.",
        }
    # rejected
    return {
        "vendor_id": v.vendor_id,
        "kind": "rejected_human_review",
        "to": v.contact_email,
        "subject": f"Vendor onboarding update: {v.legal_name} ({v.vendor_id})",
        "body": (
            f"Hi {v.contact_person},\n\n"
            f"We cannot proceed with the onboarding submission for "
            f"{v.legal_name} ({v.vendor_id}) as filed. It has been referred "
            "for manual review and our team will contact you if anything "
            "further is needed from you.\n\n"
            "VendorGate"
        ),
        "internal_note": (
            "REJECTED. Reasons: " + "; ".join(d.reasons)
            + (" FRAUD FLAG: human review required." if d.fraud else " Multiple independent failures: human review required.")
        ),
    }


def apply_corrections(v, corrections):
    """Return a corrected copy of a vendor submission (a resubmission).

    corrections keys: field overrides (country, tax_id, tax_id_type,
    ifsc_or_swift, bank_account_name, ...) and "attach": [doc types]
    which adds present documents. The original is never mutated.
    """
    fixed = deepcopy(v)
    for doc_type in corrections.get("attach", []):
        if not any(d.type == doc_type for d in fixed.documents):
            fixed.documents.append(day3.Document(type=doc_type, status="present"))
    for key, value in corrections.items():
        if key != "attach":
            setattr(fixed, key, value)
    return fixed


def decide_single(v, registry=None):
    """Decide one (possibly resubmitted) vendor against a registry."""
    registry = registry or {}
    c12 = day3.run_checks_1_2(v)
    c3 = day4.run_check_3(v, c12)
    dup = day4.find_duplicate(v, registry)
    return day4.decide(v, c12, c3, dup), c12 + c3


# Scripted resubmissions for the demo: each names the exact fix the
# pending follow-up asked for, and the audit trail keeps round 1 and 2.
SCRIPTED_RESUBMISSIONS = {
    "V014": {"attach": ["pan_card"]},
    "V025": {"country": "India", "ifsc_or_swift": "ICIC0001234"},
    "V026": {"country": "USA"},
}


def dashboard(vendors, results, as_of=AS_OF):
    """Dashboard figures from the Day 4 decisions (no invented numbers)."""
    counts = {"approved": 0, "pending": 0, "rejected": 0}
    causes = {}
    ageing = []
    timings = []
    registry = {}
    for v in vendors:
        start = time.perf_counter()
        # Time the real work: checks 1-5 plus decision, not a dict lookup.
        c12 = day3.run_checks_1_2(v)
        c3 = day4.run_check_3(v, c12)
        dup = day4.find_duplicate(v, registry)
        day4.decide(v, c12, c3, dup)
        if not any(not c.passed for c in c12):
            registry[(day4.normalize_name(v.legal_name), v.tax_id)] = v
        timings.append((time.perf_counter() - start) * 1000)
        d = results[v.vendor_id]
        counts[d.status] += 1
        for cause in d.causes:
            causes[cause] = causes.get(cause, 0) + 1
        if d.status == "pending":
            try:
                ageing.append((as_of - date.fromisoformat(v.submitted_on)).days)
            except ValueError:
                pass
    golden = day3.load_golden()
    false_approves = [
        vid for vid, d in results.items()
        if d.status == "approved"
        and golden.get(vid, {}).get("expected_status") != "approve"
    ]
    correct = sum(
        1 for vid, d in results.items()
        if vid in golden
        and d.status == {"approve": "approved", "pending": "pending", "reject": "rejected"}[golden[vid]["expected_status"]]
    )
    return {
        "as_of": as_of.isoformat(),
        "counts": counts,
        "median_decision_ms": round(statistics.median(timings), 3) if timings else None,
        "decision_ms_note": "Engine decision time per vendor (checks 1 to 5), measured in this run. Not wall-clock review time.",
        "pending_ageing_days": {
            "median": statistics.median(ageing) if ageing else None,
            "max": max(ageing) if ageing else None,
            "buckets": {
                "0-2 days": sum(1 for a in ageing if a <= 2),
                "3-5 days": sum(1 for a in ageing if 3 <= a <= 5),
                "6+ days": sum(1 for a in ageing if a >= 6),
            },
        },
        "top_failure_causes": sorted(causes.items(), key=lambda kv: -kv[1]),
        "false_approve_rate": f"{len(false_approves)}/40",
        "golden_accuracy": f"{correct}/{len(golden)}",
    }


def run_all():
    vendors, results = day4.run_all()
    comms = {v.vendor_id: build_comms(v, results[v.vendor_id]) for v in vendors}
    by_id = {v.vendor_id: v for v in vendors}
    audit = []
    for vid, corrections in SCRIPTED_RESUBMISSIONS.items():
        original = by_id[vid]
        fixed = apply_corrections(original, corrections)
        d2, _ = decide_single(fixed)
        audit.append({
            "vendor_id": vid,
            "round": 2,
            "corrections": corrections,
            "round_1_status": results[vid].status,
            "round_2_status": d2.status,
            "round_2_risk_score": d2.risk_score,
            "round_2_reasons": d2.reasons,
        })
    dash = dashboard(vendors, results)
    return vendors, results, comms, audit, dash


def main():
    as_of = AS_OF
    if "--as-of" in sys.argv:
        as_of = date.fromisoformat(sys.argv[sys.argv.index("--as-of") + 1])
    vendors, results, comms, audit, dash = run_all()
    # Recompute dashboard with the requested as-of date if it changed.
    if as_of != AS_OF:
        dash = dashboard(vendors, results, as_of=as_of)
    out = {
        "day": 5,
        "as_of": dash["as_of"],
        "dashboard": dash,
        "comms": comms,
        "resubmission_audit": audit,
    }
    (HERE / "zamp-ps2-day5-comms.json").write_text(json.dumps(out, indent=2))
    eval_mode = "--eval" in sys.argv
    if "--vendor" in sys.argv:
        vid = sys.argv[sys.argv.index("--vendor") + 1]
        print(json.dumps(comms[vid], indent=2))
        return 0
    counts = dash["counts"]
    print(f"Day 5 comms: {counts['approved']} approved packages, "
          f"{counts['pending']} pending follow-ups, "
          f"{counts['rejected']} rejected (human review).")
    print(f"Dashboard: {dash['counts']}; median decision "
          f"{dash['median_decision_ms']} ms; false-approve "
          f"{dash['false_approve_rate']}; golden accuracy {dash['golden_accuracy']}.")
    print(f"Top failure causes: {dash['top_failure_causes'][:5]}")
    print(f"Pending ageing (days since submission): median "
          f"{dash['pending_ageing_days']['median']}, max "
          f"{dash['pending_ageing_days']['max']}, buckets "
          f"{dash['pending_ageing_days']['buckets']}.")
    for a in audit:
        print(f"Resubmission {a['vendor_id']}: round 1 {a['round_1_status']} "
              f"-> round 2 {a['round_2_status']} (risk {a['round_2_risk_score']}) "
              f"after {a['corrections']}.")
    if eval_mode:
        errors = []
        if counts != {"approved": 11, "pending": 23, "rejected": 6}:
            errors.append(f"counts {counts} != 11/23/6")
        if dash["false_approve_rate"] != "0/40":
            errors.append(f"false-approve {dash['false_approve_rate']} != 0/40")
        if dash["golden_accuracy"] != "40/40":
            errors.append(f"golden accuracy {dash['golden_accuracy']} != 40/40")
        if len(comms) != 40 or any(c["kind"] not in
                ("approval_package", "pending_followup", "rejected_human_review")
                for c in comms.values()):
            errors.append("comms missing or wrong kind for some vendor")
        expected_round2 = {"V014": "approved", "V025": "approved", "V026": "pending"}
        for a in audit:
            if a["round_2_status"] != expected_round2[a["vendor_id"]]:
                errors.append(f"{a['vendor_id']} round 2 {a['round_2_status']} "
                              f"!= {expected_round2[a['vendor_id']]}")
        if errors:
            print("EVAL FAILED: " + "; ".join(errors))
            return 1
        print("Day 5 eval passed: comms for all 40, resubmissions as expected, KPI met.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
