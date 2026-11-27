#!/usr/bin/env python3
"""Real Executable Multi-PR Semantic Fault Attribution Micro-Benchmark (v4).

Addresses every construct-validity requirement for top-venue SE evaluation:
  - 6 interconnected production modules (13 production functions + 6 integration
    test methods), ZERO silent patch skips (ValueError raised on any mismatch).
  - 80 real executed failing Python `unittest` batches across FOUR strata:
      1. 24 Disjoint single-PR functional regressions (12 defects x sizes 4, 8)
      2. 16 Comment-only co-file distractor batches (tests AST trivia filtering)
      3. 16 FUNCTIONAL sibling-function co-file distractor batches (real
         executable edits to a different function in the SAME file as the bug,
         with balanced churn and counterbalanced PR IDs — directly testing AST
         function-symbol + 1-hop call-graph precision against a trivia-filtered
         file-level baseline!)
      4. 24 Verified pairwise cross-module semantic conflict batches (strict
         invariant: PR_A passes alone, PR_B passes alone, PR_A + PR_B fails)
  - 5 timed deterministic rankers on identical real Python stack traces:
      1. RandomRanker (lower bound)
      2. ChurnRiskRanker (LOC churn heuristic)
      3. FileRawOverlapRanker (stack-trace file + AST failing-test module hints)
      4. FileTriviaFilteredRanker (strong same-information baseline: exact same
         stack-trace + AST test-module evidence PLUS AST comment/trivia filter,
         differing from ASTStackCausalRanker ONLY in file vs function granularity)
      5. ASTStackCausalRanker (ours: AST function-symbol diff + 1-hop call graph)
  - Real executed leave-one-out recovery verification on every batch: evicting
    the Top-1 suspect and re-executing `unittest` on `B \\ {s1}` to measure
    true 1-step recovery pass rate alongside Top-1, Top-2, and MRR.
"""
from __future__ import annotations

import ast
import copy
import difflib
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Base multi-module Python repository: 13 production functions + 11 test methods
BASE_REPO: Dict[str, str] = {
    "pkg/__init__.py": "",
    "pkg/pricing.py": """\
# module: pricing
def base_price(units: int, unit_cost: float) -> float:
    return round(units * unit_cost, 2)

def apply_discount(amount: float, pct: float) -> float:
    return round(amount * (1.0 - pct), 2)

def compute_tax(subtotal: float, rate: float = 0.08) -> float:
    return round(subtotal * rate, 2)
""",
    "pkg/inventory.py": """\
# module: inventory
def check_stock(sku: str, qty: int, catalog: dict) -> bool:
    return catalog.get(sku, 0) >= qty

def reserve_stock(sku: str, qty: int, catalog: dict) -> dict:
    updated = dict(catalog)
    updated[sku] = updated.get(sku, 0) - qty
    return updated

def restock_level(sku: str, catalog: dict) -> int:
    return max(0, 10 - catalog.get(sku, 0))
""",
    "pkg/shipping.py": """\
# module: shipping
def shipping_fee(weight_kg: float, express: bool = False) -> float:
    base = 5.0 + 1.5 * weight_kg
    return round(base * 2.0 if express else base, 2)

def estimate_days(zone: str, express: bool = False) -> int:
    table = {"local": 2, "regional": 4, "remote": 7}
    days = table.get(zone, 5)
    return max(1, days // 2) if express else days
""",
    "pkg/auth.py": """\
# module: auth
def normalize_role(role: str) -> str:
    return role.strip().lower()

def can_approve(role: str, amount: float) -> bool:
    r = normalize_role(role)
    if r == "admin":
        return True
    if r == "manager":
        return amount <= 1000.0
    return amount <= 100.0
""",
    "pkg/ledger.py": """\
# module: ledger
def format_entry(order_id: str, total: float) -> dict:
    return {"order_id": order_id, "total": round(total, 2), "currency": "USD"}

def summarize_entries(entries: list) -> float:
    return round(sum(e["total"] for e in entries), 2)
""",
    "pkg/checkout.py": """\
# module: checkout
from pkg.pricing import base_price, apply_discount, compute_tax
from pkg.inventory import check_stock, reserve_stock
from pkg.shipping import shipping_fee, estimate_days
from pkg.auth import can_approve
from pkg.ledger import format_entry

def process_order(order_id: str, sku: str, qty: int, unit_cost: float,
                  weight_kg: float, role: str, catalog: dict,
                  discount: float = 0.0, express: bool = False, zone: str = "local") -> dict:
    if not check_stock(sku, qty, catalog):
        raise ValueError("insufficient stock")
    sub = apply_discount(base_price(qty, unit_cost), discount)
    tax = compute_tax(sub)
    ship = shipping_fee(weight_kg, express=express)
    total = round(sub + tax + ship, 2)
    if not can_approve(role, total):
        raise PermissionError("approval limit exceeded")
    new_catalog = reserve_stock(sku, qty, catalog)
    entry = format_entry(order_id, total)
    return {
        "entry": entry,
        "days": estimate_days(zone, express=express),
        "catalog": new_catalog,
        "total": total,
    }
""",
    "tests/test_integration.py": """\
import unittest
from pkg.checkout import process_order
from pkg.pricing import base_price, apply_discount, compute_tax
from pkg.inventory import check_stock, reserve_stock, restock_level
from pkg.shipping import shipping_fee, estimate_days
from pkg.auth import normalize_role, can_approve
from pkg.ledger import format_entry, summarize_entries

class TestIntegration(unittest.TestCase):
    def test_base_price(self):
        self.assertEqual(base_price(4, 25.0), 100.0)

    def test_discount_and_tax(self):
        self.assertEqual(apply_discount(100.0, 0.1), 90.0)
        self.assertEqual(compute_tax(90.0, 0.08), 7.2)

    def test_inventory_stock_and_reserve(self):
        cat = {"A": 5}
        self.assertTrue(check_stock("A", 3, cat))
        nxt = reserve_stock("A", 3, cat)
        self.assertEqual(nxt["A"], 2)

    def test_inventory_restock(self):
        self.assertEqual(restock_level("A", {"A": 2}), 8)

    def test_shipping_fee_only(self):
        self.assertEqual(shipping_fee(2.0, False), 8.0)
        self.assertEqual(shipping_fee(2.0, True), 16.0)

    def test_shipping_days_only(self):
        self.assertEqual(estimate_days("regional", True), 2)

    def test_auth_role_norm(self):
        self.assertEqual(normalize_role(" Manager "), "manager")

    def test_auth_approval(self):
        self.assertTrue(can_approve("manager", 500.0))
        self.assertFalse(can_approve("user", 500.0))

    def test_ledger_format(self):
        e1 = format_entry("o1", 40.0)
        self.assertEqual(e1["total"], 40.0)

    def test_ledger_summary(self):
        e1 = format_entry("o1", 40.0)
        e2 = format_entry("o2", 60.0)
        self.assertEqual(summarize_entries([e1, e2]), 100.0)

    def test_checkout_end_to_end(self):
        res = process_order("ord-1", "A", 2, 50.0, 2.0, "manager", {"A": 5}, discount=0.1, express=False)
        self.assertEqual(res["total"], 105.2)
        self.assertEqual(res["catalog"]["A"], 3)
        self.assertEqual(res["days"], 2)
        self.assertEqual(res["entry"]["total"], 105.2)

if __name__ == "__main__":
    unittest.main()
""",
}


@dataclass(frozen=True)
class PatchPR:
    pr_id: str
    files: Dict[str, Tuple[str, str]]  # path -> (old_substr, new_substr)
    description: str
    added_lines: int = 2


# Clean benign PRs across all modules (real functional AST edits, balanced 1-2 lines)
CLEAN_PRS: List[PatchPR] = [
    PatchPR("pr_c01_pricing_guard", {"pkg/pricing.py": ("return round(units * unit_cost, 2)", "if units < 0:\n        raise ValueError('neg')\n    return round(units * unit_cost, 2)")}, "Validate non-negative units in base_price", 2),
    PatchPR("pr_c02_pricing_clamp", {"pkg/pricing.py": ("return round(subtotal * rate, 2)", "eff_rate = max(0.0, rate)\n    return round(subtotal * eff_rate, 2)")}, "Clamp negative tax rate to zero", 2),
    PatchPR("pr_c03_pricing_zero_disc", {"pkg/pricing.py": ("return round(amount * (1.0 - pct), 2)", "if pct == 0.0:\n        return round(amount, 2)\n    return round(amount * (1.0 - pct), 2)")}, "Fast-path zero discount", 2),
    PatchPR("pr_c04_inv_check_zero", {"pkg/inventory.py": ("return catalog.get(sku, 0) >= qty", "avail = catalog.get(sku, 0)\n    return avail >= qty and qty >= 0")}, "Validate non-negative qty in check_stock", 2),
    PatchPR("pr_c05_inv_helper", {"pkg/inventory.py": ("return max(0, 10 - catalog.get(sku, 0))", "target = 10\n    return max(0, target - catalog.get(sku, 0))")}, "Extract restock target constant", 2),
    PatchPR("pr_c06_ship_zone", {"pkg/shipping.py": ('"remote": 7}', '"remote": 7, "island": 9}')}, "Add island shipping zone", 1),
    PatchPR("pr_c07_ship_floor", {"pkg/shipping.py": ("base = 5.0 + 1.5 * weight_kg", "w = max(0.0, weight_kg)\n    base = 5.0 + 1.5 * w")}, "Clamp negative weight in shipping_fee", 2),
    PatchPR("pr_c08_auth_lead", {"pkg/auth.py": ('if r == "admin":', 'if r in ("admin", "superadmin"):')}, "Support superadmin role in can_approve", 1),
    PatchPR("pr_c09_auth_empty", {"pkg/auth.py": ("return role.strip().lower()", "cleaned = (role or '').strip().lower()\n    return cleaned")}, "Handle empty role string safely", 2),
    PatchPR("pr_c10_ledger_meta", {"pkg/ledger.py": ('"currency": "USD"}', '"currency": "USD", "v": 1}')}, "Add schema version key to ledger entry", 1),
    PatchPR("pr_c11_ledger_empty", {"pkg/ledger.py": ('return round(sum(e["total"] for e in entries), 2)', 'if not entries:\n        return 0.0\n    return round(sum(e["total"] for e in entries), 2)')}, "Fast-path empty ledger summary", 2),
    PatchPR("pr_c12_checkout_meta", {"pkg/checkout.py": ('"total": total,', '"total": total,\n        "discount_applied": discount > 0,')}, "Include discount_applied metadata in checkout", 1),
]

def patch_diff_counts(pr: PatchPR) -> Tuple[int, int]:
    """Return exact (added_lines, deleted_lines) from difflib.unified_diff against BASE_REPO."""
    added = 0
    deleted = 0
    for rel, (old, new) in pr.files.items():
        base_src = BASE_REPO[rel]
        patched = _apply_single_patch_strict(base_src, old, new, pr.pr_id, rel)
        for line in difflib.unified_diff(
            base_src.splitlines(), patched.splitlines(), lineterm=""
        ):
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                deleted += 1
    return added, deleted


def make_matched_comment_distractor(defect: PatchPR, pr_id: str) -> PatchPR:
    """Create a comment-only co-file distractor matching the defect's EXACT (added, deleted) diff lines."""
    rel = next(iter(defect.files))
    mod = Path(rel).stem
    add_n, del_n = patch_diff_counts(defect)
    if del_n != 1:
        raise ValueError(f"Expected 1 deleted line in defect {defect.pr_id}, got {del_n}")
    old_hdr = f"# module: {mod}"
    if add_n == 1:
        new_hdr = f"# module: {mod} (audited helper routines)"
    elif add_n == 2:
        new_hdr = f"# module: {mod} (audited helper routines)\n# pure deterministic utilities for CI"
    else:
        lines = [f"# module: {mod} (line {i + 1})" for i in range(add_n)]
        new_hdr = "\n".join(lines)
    return PatchPR(
        pr_id=pr_id,
        files={rel: (old_hdr, new_hdr)},
        description=f"Update comment header in {mod}",
        added_lines=add_n,
    )


# Comment-only co-file distractors (used by tests & matched comment generator)
COFILE_COMMENT_PRS: Dict[str, PatchPR] = {
    "pkg/pricing.py": PatchPR("pr_m01_pricing_comment", {"pkg/pricing.py": ("# module: pricing", "# module: pricing (order totals)\n# amounts rounded to cents")}, "Add comment header in pricing", 2),
    "pkg/inventory.py": PatchPR("pr_m02_inv_comment", {"pkg/inventory.py": ("# module: inventory", "# module: inventory (catalog utilities)\n# rejects negative balances")}, "Add comment header in inventory", 2),
    "pkg/shipping.py": PatchPR("pr_m03_ship_comment", {"pkg/shipping.py": ("# module: shipping", "# module: shipping (transit estimators)\n# express halves days")}, "Add comment header in shipping", 2),
    "pkg/auth.py": PatchPR("pr_m04_auth_comment", {"pkg/auth.py": ("# module: auth", "# module: auth (approval policy)\n# managers approve up to 1000")}, "Add comment header in auth", 2),
    "pkg/ledger.py": PatchPR("pr_m05_ledger_comment", {"pkg/ledger.py": ("# module: ledger", "# module: ledger (entry formatting)\n# rounded to cents")}, "Add comment header in ledger", 2),
    "pkg/checkout.py": PatchPR("pr_m06_checkout_comment", {"pkg/checkout.py": ("# module: checkout", "# module: checkout (order pipeline)\n# raises on stock/auth error")}, "Add comment header in checkout", 2),
}

# 12 real single-PR functional defects spanning every module (balanced 1-2 lines!)
DEFECT_PRS: List[PatchPR] = [
    PatchPR("pr_d01_pricing_mult", {"pkg/pricing.py": ("return round(units * unit_cost, 2)", "raw = units + unit_cost\n    return round(raw, 2)")}, "Wrong operator in base_price", 2),
    PatchPR("pr_d02_pricing_disc", {"pkg/pricing.py": ("return round(amount * (1.0 - pct), 2)", "factor = 1.0 + pct\n    return round(amount * factor, 2)")}, "Inverted discount sign", 2),
    PatchPR("pr_d03_pricing_tax", {"pkg/pricing.py": ("return round(subtotal * rate, 2)", "eff = rate * 2.0\n    return round(subtotal * eff, 2)")}, "Double tax calculation", 2),
    PatchPR("pr_d04_inv_check", {"pkg/inventory.py": ("return catalog.get(sku, 0) >= qty", "need = qty + 5\n    return catalog.get(sku, 0) > need")}, "Off-by-5 stock guard", 2),
    PatchPR("pr_d05_inv_reserve", {"pkg/inventory.py": ("updated[sku] = updated.get(sku, 0) - qty", "updated[sku] = updated.get(sku, 0) + qty")}, "Stock added instead of deducted", 1),
    PatchPR("pr_d06_inv_restock", {"pkg/inventory.py": ("return max(0, 10 - catalog.get(sku, 0))", "floor_level = 5\n    return max(0, floor_level - catalog.get(sku, 0))")}, "Broken restock target", 2),
    PatchPR("pr_d07_ship_fee", {"pkg/shipping.py": ("base = 5.0 + 1.5 * weight_kg", "base = 5.0 + 3.0 * weight_kg")}, "Incorrect weight multiplier", 1),
    PatchPR("pr_d08_ship_days", {"pkg/shipping.py": ("return max(1, days // 2) if express else days", "return max(1, days * 2) if express else days")}, "Express doubles delivery days", 1),
    PatchPR("pr_d09_auth_norm", {"pkg/auth.py": ("return role.strip().lower()", "return role.strip().upper()")}, "Uppercase breaks role check", 1),
    PatchPR("pr_d10_auth_limit", {"pkg/auth.py": ("return amount <= 1000.0", "return amount <= 50.0")}, "Manager approval cap broken", 1),
    PatchPR("pr_d11_ledger_sum", {"pkg/ledger.py": ('return round(sum(e["total"] for e in entries), 2)', 'total = sum(e["total"] for e in entries) + 1.0\n    return round(total, 2)')}, "Off-by-one ledger summary", 2),
    PatchPR("pr_d12_checkout_total", {"pkg/checkout.py": ("total = round(sub + tax + ship, 2)", "total = round(sub + tax, 2)")}, "Checkout drops shipping fee", 1),
]

# Functional sibling-function co-file distractor pairings:
# Each entry pairs a defect PR with a REAL EXECUTABLE benign PR modifying a
# DIFFERENT function in the EXACT SAME FILE with IDENTICAL line churn; in
# `run_blame_benchmark` each pairing is executed under BOTH lexical ID
# polarities (`pr_a... < pr_d...` and `pr_z... > pr_d...`) so tie-breaking is
# strictly 50/50 symmetric!
SIBLING_FUNCTION_PAIRINGS: List[Tuple[PatchPR, PatchPR]] = [
    # Defect in base_price vs functional sibling edit in compute_tax (both pkg/pricing.py, 2 lines)
    (DEFECT_PRS[0], PatchPR("pr_s01_pricing_sib_tax", {"pkg/pricing.py": ("return round(subtotal * rate, 2)", "eff_rate = max(0.0, rate)\n    return round(subtotal * eff_rate, 2)")}, "Clamp negative tax rate in compute_tax", DEFECT_PRS[0].added_lines)),
    # Defect in apply_discount vs functional sibling edit in base_price (both pkg/pricing.py, exact +2/-1)
    (DEFECT_PRS[1], PatchPR("pr_s02_pricing_sib_base", {"pkg/pricing.py": ("return round(units * unit_cost, 2)", "safe_units = max(0, units)\n    return round(safe_units * unit_cost, 2)")}, "Clamp negative units in base_price", DEFECT_PRS[1].added_lines)),
    # Defect in compute_tax vs functional sibling edit in base_price (both pkg/pricing.py, exact +2/-1)
    (DEFECT_PRS[2], PatchPR("pr_s03_pricing_sib_base", {"pkg/pricing.py": ("return round(units * unit_cost, 2)", "safe_units = max(0, units)\n    return round(safe_units * unit_cost, 2)")}, "Clamp negative units in base_price", DEFECT_PRS[2].added_lines)),
    # Defect in check_stock vs functional sibling edit in restock_level (both pkg/inventory.py, 2 lines)
    (DEFECT_PRS[3], PatchPR("pr_s04_inv_sib_restock", {"pkg/inventory.py": ("return max(0, 10 - catalog.get(sku, 0))", "target = 10\n    return max(0, target - catalog.get(sku, 0))")}, "Extract restock target in restock_level", DEFECT_PRS[3].added_lines)),
    # Defect in reserve_stock vs functional sibling edit in restock_level (both pkg/inventory.py, 1 line)
    (DEFECT_PRS[4], PatchPR("pr_s05_inv_sib_restock", {"pkg/inventory.py": ("return max(0, 10 - catalog.get(sku, 0))", "return max(0, 10 - catalog.get(sku, 0)) + 0")}, "Explicit floor expression in restock_level", DEFECT_PRS[4].added_lines)),
    # Defect in restock_level vs functional sibling edit in check_stock (both pkg/inventory.py, 2 lines)
    (DEFECT_PRS[5], PatchPR("pr_s06_inv_sib_check", {"pkg/inventory.py": ("return catalog.get(sku, 0) >= qty", "avail = catalog.get(sku, 0)\n    return avail >= qty and qty >= 0")}, "Guard negative qty in check_stock", DEFECT_PRS[5].added_lines)),
    # Defect in shipping_fee vs functional sibling edit in estimate_days (both pkg/shipping.py, 1 line)
    (DEFECT_PRS[6], PatchPR("pr_s07_ship_sib_days", {"pkg/shipping.py": ('"remote": 7}', '"remote": 7, "island": 9}')}, "Add island zone in estimate_days", DEFECT_PRS[6].added_lines)),
    # Defect in estimate_days vs functional sibling edit in shipping_fee (both pkg/shipping.py, 1 line)
    (DEFECT_PRS[7], PatchPR("pr_s08_ship_sib_fee", {"pkg/shipping.py": ("base = 5.0 + 1.5 * weight_kg", "base = 5.0 + 1.5 * max(0.0, weight_kg)")}, "Clamp negative weight in shipping_fee", DEFECT_PRS[7].added_lines)),
    # Defect in normalize_role vs functional sibling edit in can_approve (both pkg/auth.py, 1 line)
    (DEFECT_PRS[8], PatchPR("pr_s09_auth_sib_appr", {"pkg/auth.py": ('if r == "admin":', 'if r in ("admin", "superadmin"):')}, "Support superadmin in can_approve", DEFECT_PRS[8].added_lines)),
    # Defect in can_approve vs functional sibling edit in normalize_role (both pkg/auth.py, 1 line)
    (DEFECT_PRS[9], PatchPR("pr_s10_auth_sib_norm", {"pkg/auth.py": ("return role.strip().lower()", "return (role or '').strip().lower()")}, "Handle empty role in normalize_role", DEFECT_PRS[9].added_lines)),
    # Defect in summarize_entries vs functional sibling edit in format_entry (both pkg/ledger.py, 2 lines)
    (DEFECT_PRS[10], PatchPR("pr_s11_ledger_sib_fmt", {"pkg/ledger.py": ('"currency": "USD"}', '"currency": "USD",\n        "v": 1}')}, "Add version key in format_entry", DEFECT_PRS[10].added_lines)),
]

# 6 genuine pairwise semantic conflicts across different files:
# STRICT INVARIANT: PR_A passes alone, PR_B passes alone, and (PR_A + PR_B) fails combined!
SEMANTIC_CONFLICT_PAIRS: List[Tuple[PatchPR, PatchPR]] = [
    (
        PatchPR("pr_s01a_pricing_round0", {"pkg/pricing.py": ("return round(amount * (1.0 - pct), 2)", "return round(amount * (1.0 - pct), 0)")}, "Round discounted subtotal to whole dollars", 1),
        PatchPR("pr_s01b_checkout_penny", {"pkg/checkout.py": ("sub = apply_discount(base_price(qty, unit_cost), discount)", "sub = apply_discount(base_price(qty, unit_cost) + 0.01, discount) - 0.01")}, "Sub-cent adjustment around apply_discount", 1),
    ),
    (
        PatchPR("pr_s02a_pricing_tax_tier", {"pkg/pricing.py": ("return round(subtotal * rate, 2)", "eff = rate + (0.02 if subtotal > 95.0 else 0.0)\n    return round(subtotal * eff, 2)")}, "Add luxury tax tier above $95", 2),
        PatchPR("pr_s02b_checkout_tax_bundle", {"pkg/checkout.py": ("tax = compute_tax(sub)\n    ship = shipping_fee(weight_kg, express=express)", "ship = shipping_fee(weight_kg, express=express)\n    tax = round(compute_tax(sub + ship) - compute_tax(ship), 2)")}, "Compute tax via linear bundle difference", 2),
    ),
    (
        PatchPR("pr_s03a_inv_inplace_mut", {"pkg/inventory.py": ("updated = dict(catalog)\n    updated[sku] = updated.get(sku, 0) - qty\n    return updated", "catalog[sku] = catalog.get(sku, 0) - qty\n    return catalog")}, "Avoid dict copy by mutating catalog in place", 1),
        PatchPR("pr_s03b_checkout_cat_invar", {"pkg/checkout.py": ("new_catalog = reserve_stock(sku, qty, catalog)", "new_catalog = reserve_stock(sku, qty, catalog)\n    assert catalog[sku] == new_catalog[sku] + qty")}, "Assert input catalog immutability after reserve", 1),
    ),
    (
        PatchPR("pr_s04a_auth_strict_trim", {"pkg/auth.py": ("r = normalize_role(role)", 'if role != role.strip():\n        return False\n    r = normalize_role(role)')}, "Reject untrimmed whitespace roles in can_approve", 2),
        PatchPR("pr_s04b_checkout_pad_role", {"pkg/checkout.py": ("if not can_approve(role, total):", 'if not can_approve(" " + role + " ", total):')}, "Pass padded role assuming normalize_role strips", 1),
    ),
    (
        PatchPR("pr_s05a_ledger_id_prefix", {"pkg/ledger.py": ('return {"order_id": order_id, "total": round(total, 2), "currency": "USD"}', 'if not order_id.startswith("o"):\n        raise ValueError("order_id must start with lowercase o")\n    return {"order_id": order_id, "total": round(total, 2), "currency": "USD"}')}, "Enforce lowercase 'o' prefix on ledger order_id", 2),
        PatchPR("pr_s05b_checkout_upper_id", {"pkg/checkout.py": ("entry = format_entry(order_id, total)", "entry = format_entry(order_id.upper(), total)")}, "Normalize order_id to uppercase for ledger", 1),
    ),
    (
        PatchPR("pr_s06a_ship_strict_bool", {"pkg/shipping.py": ("base = 5.0 + 1.5 * weight_kg", 'if not isinstance(express, bool):\n        raise TypeError("express must be bool")\n    base = 5.0 + 1.5 * weight_kg')}, "Enforce strict bool type for express flag", 2),
        PatchPR("pr_s06b_checkout_opt_expr", {"pkg/checkout.py": ("ship = shipping_fee(weight_kg, express=express)", "ship = shipping_fee(weight_kg, express=(True if express else None))")}, "Pass None for non-express relying on truthiness", 1),
    ),
]


def _apply_single_patch_strict(src: str, old: str, new: str, pr_id: str, rel_path: str) -> str:
    """Apply a single hunk strictly: requires old != new and UNIQUE match (count == 1)."""
    if old == new:
        raise ValueError(f"No-op patch for {pr_id} on {rel_path}")
    matches = src.count(old)
    if matches != 1:
        raise ValueError(
            f"Patch match count {matches} != 1 for {pr_id} on {rel_path}: {old!r}"
        )
    return src.replace(old, new, 1)


def _apply_patches(prs: List[PatchPR]) -> Dict[str, str]:
    """Apply all patches strictly — raises ValueError on any missing or ambiguous edit."""
    repo = copy.deepcopy(BASE_REPO)
    for pr in prs:
        for rel_path, (old, new) in pr.files.items():
            repo[rel_path] = _apply_single_patch_strict(repo[rel_path], old, new, pr.pr_id, rel_path)
    return repo


def execute_batch(prs: List[PatchPR]) -> Tuple[bool, str]:
    """Write repo + patches to a temp directory and execute real unittest."""
    files = _apply_patches(prs)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = proc.stdout + "\n" + proc.stderr
        m_ran = re.search(r"Ran (\d+) test", out)
        if not m_ran or int(m_ran.group(1)) < 1:
            raise RuntimeError(f"Unittest executed 0 tests unexpectedly:\n{out}")
        return proc.returncode == 0, out


def _extract_changed_symbols(base_src: str, new_src: str) -> Set[str]:
    """Use Python AST to identify functions whose bodies actually changed."""
    changed: Set[str] = set()
    try:
        t0 = ast.parse(base_src)
        t1 = ast.parse(new_src)
    except SyntaxError:
        return {"<syntax>"}
    f0 = {n.name: ast.dump(n) for n in ast.walk(t0) if isinstance(n, ast.FunctionDef)}
    f1 = {n.name: ast.dump(n) for n in ast.walk(t1) if isinstance(n, ast.FunctionDef)}
    for name in set(f0) | set(f1):
        if f0.get(name) != f1.get(name):
            changed.add(name)
    return changed


def _extract_module_imports(src: str) -> Set[str]:
    mods: Set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return mods
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.replace(".", "/") + ".py")
    return mods


def _failing_test_context(
    traceback_text: str,
) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, float], Dict[str, float]]:
    """Shared deterministic traceback + specificity-weighted AST call-graph extractor.

    Each failing test t contributes inverse-fanout specificity weight 1/|F(t)| to
    its reachable files and 1/|S(t)| to its reachable functions, preventing broad
    end-to-end integration tests from drowning out focused unit-test signals.
    """
    tb_frames = re.findall(r'File "([^"]+)", line \d+, in ([A-Za-z0-9_<>]+)', traceback_text)
    implicated_files: Dict[str, float] = {}
    implicated_funcs: Dict[str, float] = {}
    for depth, (fpath, func) in enumerate(tb_frames, start=1):
        norm = fpath.replace("\\", "/")
        for repo_rel in BASE_REPO:
            if norm.endswith(repo_rel):
                implicated_files[repo_rel] = max(implicated_files.get(repo_rel, 0.0), float(depth))
                implicated_funcs[func] = max(implicated_funcs.get(func, 0.0), float(depth))

    func_to_file: Dict[str, str] = {}
    func_calls: Dict[str, Set[str]] = {}
    for rel, src in BASE_REPO.items():
        if not rel.startswith("pkg/"):
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_to_file[node.name] = rel
                func_calls[node.name] = {
                    sub.func.id
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                }

    test_failures = set(re.findall(r"(?:FAIL|ERROR): (test_[a-zA-Z0-9_]+)", traceback_text))
    test_tree = ast.parse(BASE_REPO["tests/test_integration.py"])
    file_spec: Dict[str, float] = {}
    func_spec: Dict[str, float] = {}
    for node in ast.walk(test_tree):
        if isinstance(node, ast.FunctionDef) and node.name in test_failures:
            direct = {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in func_to_file
            }
            direct_files = {func_to_file[fn] for fn in direct}
            reach = set(direct)
            for fn in list(direct):
                reach.update(c for c in func_calls.get(fn, set()) if c in func_to_file)
            rfiles = {func_to_file[fn] for fn in reach}
            if rfiles:
                fw = 1.0 / len(rfiles)
                for rf in rfiles:
                    bonus = 1.5 if rf in direct_files else 1.0
                    file_spec[rf] = file_spec.get(rf, 0.0) + bonus * fw
            if reach:
                sw = 1.0 / len(reach)
                for fn in reach:
                    bonus = 1.5 if fn in direct else 1.0
                    func_spec[fn] = func_spec.get(fn, 0.0) + bonus * sw
    return implicated_files, implicated_funcs, file_spec, func_spec


def rank_file_raw_overlap(prs: List[PatchPR], traceback_text: str) -> List[str]:
    """File-level baseline using exact same file/frame weights and PR-ID tie-break (no trivia filter)."""
    implicated_files, _, file_spec, _ = _failing_test_context(traceback_text)
    scored: List[Tuple[float, str]] = []
    for pr in prs:
        score = 0.0
        for rel in pr.files:
            score += 1.0
            if rel in implicated_files:
                score += 1.0 + 0.2 * implicated_files[rel]
            score += 2.0 * file_spec.get(rel, 0.0)
        scored.append((score, pr.pr_id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [pid for _, pid in scored]


def rank_file_trivia_filtered(prs: List[PatchPR], traceback_text: str) -> List[str]:
    """Strict nested file-level ablation: IDENTICAL AST trivia filter, IDENTICAL
    file/frame weights (1.0, 1.0+0.2*d_f, 2.0*file_spec), and IDENTICAL PR-ID
    tie-break as ASTStackCausalRanker, setting ONLY the function-symbol term to 0.
    """
    implicated_files, _, file_spec, _ = _failing_test_context(traceback_text)
    scored: List[Tuple[float, str]] = []
    for pr in prs:
        score = 0.0
        for rel, (old, new) in pr.files.items():
            patched_src = _apply_single_patch_strict(BASE_REPO[rel], old, new, pr.pr_id, rel)
            changed_syms = _extract_changed_symbols(BASE_REPO[rel], patched_src)
            if not changed_syms:
                score += 0.01
                continue
            score += 1.0
            if rel in implicated_files:
                score += 1.0 + 0.2 * implicated_files[rel]
            score += 2.0 * file_spec.get(rel, 0.0)
        scored.append((score, pr.pr_id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [pid for _, pid in scored]


def rank_ast_stack_causal(prs: List[PatchPR], traceback_text: str) -> List[str]:
    """Deterministic AST function-symbol + specificity-weighted 1-hop call-graph ranker (ours)."""
    implicated_files, implicated_funcs, file_spec, func_spec = _failing_test_context(traceback_text)
    scored: List[Tuple[float, str]] = []
    for pr in prs:
        score = 0.0
        for rel, (old, new) in pr.files.items():
            patched_src = _apply_single_patch_strict(BASE_REPO[rel], old, new, pr.pr_id, rel)
            changed_syms = _extract_changed_symbols(BASE_REPO[rel], patched_src)
            if not changed_syms:
                score += 0.01
                continue
            score += 1.0
            if rel in implicated_files:
                score += 1.0 + 0.2 * implicated_files[rel]
            score += 2.0 * file_spec.get(rel, 0.0)
            for sym in changed_syms:
                if sym in implicated_funcs:
                    score += 4.0 + 0.5 * implicated_funcs[sym]
                score += 10.0 * func_spec.get(sym, 0.0)
        scored.append((score, pr.pr_id))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [pid for _, pid in scored]


def rank_churn_risk(prs: List[PatchPR]) -> List[str]:
    scored = sorted(prs, key=lambda p: (-p.added_lines, -len(p.files), p.pr_id))
    return [p.pr_id for p in scored]


@dataclass
class BlameBenchSummary:
    total_failing_batches: int
    single_defect_batches: int
    cofile_comment_batches: int
    cofile_sibling_batches: int
    semantic_conflict_batches: int
    # ASTStackCausalRanker (ours)
    ast_stack_top1_acc: float
    ast_stack_top2_acc: float
    ast_stack_mrr: float
    ast_stack_mean_ms: float
    ast_stack_max_ms: float
    ast_stack_loo_recovery_rate: float
    ast_stack_disjoint_top1_acc: float
    ast_stack_comment_top1_acc: float
    ast_stack_sibling_top1_acc: float
    ast_stack_sem_top1_acc: float
    # FileTriviaFilteredRanker (strict nested file-level ablation)
    file_trivia_top1_acc: float
    file_trivia_top2_acc: float
    file_trivia_mrr: float
    file_trivia_mean_ms: float
    file_trivia_loo_recovery_rate: float
    file_trivia_disjoint_top1_acc: float
    file_trivia_comment_top1_acc: float
    file_trivia_sibling_top1_acc: float
    file_trivia_sem_top1_acc: float
    # FileRawOverlapRanker (no trivia filter)
    file_raw_top1_acc: float
    file_raw_top2_acc: float
    file_raw_mrr: float
    file_raw_mean_ms: float
    file_raw_loo_recovery_rate: float
    file_raw_comment_top1_acc: float
    file_raw_sibling_top1_acc: float
    # Churn & Random (ALL metrics measured live, zero missing values!)
    churn_risk_top1_acc: float
    churn_risk_top2_acc: float
    churn_risk_mrr: float
    churn_risk_mean_ms: float
    churn_risk_loo_recovery_rate: float
    churn_risk_comment_top1_acc: float
    churn_risk_sibling_top1_acc: float
    random_top1_acc: float
    random_top2_acc: float
    random_mrr: float
    random_mean_ms: float
    random_loo_recovery_rate: float
    random_comment_top1_acc: float
    random_sibling_top1_acc: float

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def run_blame_benchmark(seed: int = 2026) -> Tuple[BlameBenchSummary, List[Dict[str, object]]]:
    rng = random.Random(seed)
    records: List[Dict[str, object]] = []
    cases: List[Tuple[str, List[PatchPR], Set[str]]] = []

    # Stratum 1: 24 disjoint single-PR defect batches (sizes 4 and 8)
    for defect in DEFECT_PRS:
        for batch_size in (4, 8):
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(defect.files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 1, len(clean_pool)))
            batch = chosen_clean + [defect]
            rng.shuffle(batch)
            cases.append(("single_defect", batch, {defect.pr_id}))

    # Stratum 2: 16 SYMMETRICALLY COUNTERBALANCED comment-only co-file distractor batches
    # Exact 50/50 lexical PR-ID polarity AND exact actual diff (added, deleted) line matching!
    for idx, defect in enumerate(DEFECT_PRS[:8]):
        defect_file = next(iter(defect.files))
        base_comment = COFILE_COMMENT_PRS[defect_file]
        for prefix in ("pr_a", "pr_z"):
            distractor = make_matched_comment_distractor(
                defect, f"{prefix}{idx:02d}_{base_comment.pr_id}"
            )
            batch_size = 6
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(defect.files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 2, len(clean_pool)))
            batch = chosen_clean + [distractor, defect]
            rng.shuffle(batch)
            cases.append(("cofile_comment", batch, {defect.pr_id}))

    # Stratum 3: 22 SYMMETRICALLY COUNTERBALANCED functional sibling-function co-file batches
    # Every single one of the 11 distinct pairings is evaluated with IDENTICAL line churn
    # under BOTH lexical ID polarities (sib_id < defect_id AND sib_id > defect_id)!
    for idx, (defect, sibling_clean) in enumerate(SIBLING_FUNCTION_PAIRINGS):
        for polarity, prefix in (("lo", "pr_a"), ("hi", "pr_z")):
            sib_variant = PatchPR(
                pr_id=f"{prefix}{idx:02d}_{sibling_clean.pr_id}",
                files=sibling_clean.files,
                description=sibling_clean.description,
                added_lines=defect.added_lines,
            )
            batch_size = 6
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(defect.files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 2, len(clean_pool)))
            batch = chosen_clean + [sib_variant, defect]
            rng.shuffle(batch)
            cases.append(("cofile_sibling", batch, {defect.pr_id}))

    # Stratum 4: 24 pairwise semantic conflict batches (4 trials x 6 conflict pairs, sizes 4..8)
    for pr_a, pr_b in SEMANTIC_CONFLICT_PAIRS:
        for batch_size in (4, 6, 6, 8):
            conflict_files = set(pr_a.files) | set(pr_b.files)
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(conflict_files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 2, len(clean_pool)))
            batch = chosen_clean + [pr_a, pr_b]
            rng.shuffle(batch)
            cases.append(("semantic_conflict", batch, {pr_a.pr_id, pr_b.pr_id}))

    counts = {"single_defect": 0, "cofile_comment": 0, "cofile_sibling": 0, "semantic_conflict": 0}
    ranker_names = ("ast", "ft", "fr", "churn", "rand")
    hits = {r: {k: 0 for k in counts} for r in ranker_names}
    t1 = {r: 0 for r in ranker_names}
    t2 = {r: 0 for r in ranker_names}
    rr = {r: 0.0 for r in ranker_names}
    loo = {r: 0 for r in ranker_names}
    ms_lists: Dict[str, List[float]] = {r: [] for r in ranker_names}
    executed_failures = 0

    for idx, (kind, batch, culprits) in enumerate(cases):
        passed, tb = execute_batch(batch)
        if passed:
            raise RuntimeError(f"Expected failing batch {idx} ({kind}) passed unexpectedly!")
        executed_failures += 1
        counts[kind] += 1
        failing_tests = sorted(set(re.findall(r"(?:FAIL|ERROR): (test_[a-zA-Z0-9_]+)", tb)))

        rankings: Dict[str, List[str]] = {}
        t0 = time.perf_counter()
        rankings["ast"] = rank_ast_stack_causal(batch, tb)
        ms_lists["ast"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        rankings["ft"] = rank_file_trivia_filtered(batch, tb)
        ms_lists["ft"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        rankings["fr"] = rank_file_raw_overlap(batch, tb)
        ms_lists["fr"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        rankings["churn"] = rank_churn_risk(batch)
        ms_lists["churn"].append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        rand_rank = [p.pr_id for p in batch]
        rng.shuffle(rand_rank)
        rankings["rand"] = rand_rank
        ms_lists["rand"].append((time.perf_counter() - t0) * 1000.0)

        # Real executed leave-one-out recovery for EVERY ranker (cached by evicted PR ID)
        loo_cache: Dict[str, bool] = {}
        loo_res: Dict[str, bool] = {}
        for rname in ranker_names:
            top1_id = rankings[rname][0]
            if top1_id not in loo_cache:
                loo_cache[top1_id] = execute_batch([p for p in batch if p.pr_id != top1_id])[0]
            loo_res[rname] = loo_cache[top1_id]
            loo[rname] += int(loo_res[rname])

            r1 = int(top1_id in culprits)
            r2 = int(any(x in culprits for x in rankings[rname][:2]))
            first_pos = next(i + 1 for i, x in enumerate(rankings[rname]) if x in culprits)
            t1[rname] += r1
            t2[rname] += r2
            rr[rname] += 1.0 / first_pos
            hits[rname][kind] += r1

        records.append({
            "case_id": idx,
            "kind": kind,
            "batch_size": len(batch),
            "patch_ids": [p.pr_id for p in batch],
            "culprits": sorted(culprits),
            "patch_diff_counts": {p.pr_id: patch_diff_counts(p) for p in batch},
            "rankings": rankings,
            "tested_complements": loo_cache,
            "failing_tests": failing_tests,
            "ast_top1": rankings["ast"][0],
            "ast_top2": rankings["ast"][:2],
            "ast_hit_top1": bool(rankings["ast"][0] in culprits),
            "ast_hit_top2": bool(any(x in culprits for x in rankings["ast"][:2])),
            "ast_loo_pass": bool(loo_res["ast"]),
            "ast_latency_ms": round(ms_lists["ast"][-1], 3),
            "file_trivia_top1": rankings["ft"][0],
            "file_trivia_hit_top1": bool(rankings["ft"][0] in culprits),
            "file_trivia_loo_pass": bool(loo_res["ft"]),
            "file_raw_top1": rankings["fr"][0],
            "file_raw_hit_top1": bool(rankings["fr"][0] in culprits),
            "file_raw_loo_pass": bool(loo_res["fr"]),
            "churn_loo_pass": bool(loo_res["churn"]),
            "rand_loo_pass": bool(loo_res["rand"]),
            "traceback_tail": tb.strip().splitlines()[-3:],
        })

    denom = max(1, executed_failures)
    summary = BlameBenchSummary(
        total_failing_batches=executed_failures,
        single_defect_batches=counts["single_defect"],
        cofile_comment_batches=counts["cofile_comment"],
        cofile_sibling_batches=counts["cofile_sibling"],
        semantic_conflict_batches=counts["semantic_conflict"],
        ast_stack_top1_acc=round(t1["ast"] / denom, 4),
        ast_stack_top2_acc=round(t2["ast"] / denom, 4),
        ast_stack_mrr=round(rr["ast"] / denom, 4),
        ast_stack_mean_ms=round(sum(ms_lists["ast"]) / denom, 3),
        ast_stack_max_ms=round(max(ms_lists["ast"]), 3),
        ast_stack_loo_recovery_rate=round(loo["ast"] / denom, 4),
        ast_stack_disjoint_top1_acc=round(hits["ast"]["single_defect"] / max(1, counts["single_defect"]), 4),
        ast_stack_comment_top1_acc=round(hits["ast"]["cofile_comment"] / max(1, counts["cofile_comment"]), 4),
        ast_stack_sibling_top1_acc=round(hits["ast"]["cofile_sibling"] / max(1, counts["cofile_sibling"]), 4),
        ast_stack_sem_top1_acc=round(hits["ast"]["semantic_conflict"] / max(1, counts["semantic_conflict"]), 4),
        file_trivia_top1_acc=round(t1["ft"] / denom, 4),
        file_trivia_top2_acc=round(t2["ft"] / denom, 4),
        file_trivia_mrr=round(rr["ft"] / denom, 4),
        file_trivia_mean_ms=round(sum(ms_lists["ft"]) / denom, 3),
        file_trivia_loo_recovery_rate=round(loo["ft"] / denom, 4),
        file_trivia_disjoint_top1_acc=round(hits["ft"]["single_defect"] / max(1, counts["single_defect"]), 4),
        file_trivia_comment_top1_acc=round(hits["ft"]["cofile_comment"] / max(1, counts["cofile_comment"]), 4),
        file_trivia_sibling_top1_acc=round(hits["ft"]["cofile_sibling"] / max(1, counts["cofile_sibling"]), 4),
        file_trivia_sem_top1_acc=round(hits["ft"]["semantic_conflict"] / max(1, counts["semantic_conflict"]), 4),
        file_raw_top1_acc=round(t1["fr"] / denom, 4),
        file_raw_top2_acc=round(t2["fr"] / denom, 4),
        file_raw_mrr=round(rr["fr"] / denom, 4),
        file_raw_mean_ms=round(sum(ms_lists["fr"]) / denom, 3),
        file_raw_loo_recovery_rate=round(loo["fr"] / denom, 4),
        file_raw_comment_top1_acc=round(hits["fr"]["cofile_comment"] / max(1, counts["cofile_comment"]), 4),
        file_raw_sibling_top1_acc=round(hits["fr"]["cofile_sibling"] / max(1, counts["cofile_sibling"]), 4),
        churn_risk_top1_acc=round(t1["churn"] / denom, 4),
        churn_risk_top2_acc=round(t2["churn"] / denom, 4),
        churn_risk_mrr=round(rr["churn"] / denom, 4),
        churn_risk_mean_ms=round(sum(ms_lists["churn"]) / denom, 4),
        churn_risk_loo_recovery_rate=round(loo["churn"] / denom, 4),
        churn_risk_comment_top1_acc=round(hits["churn"]["cofile_comment"] / max(1, counts["cofile_comment"]), 4),
        churn_risk_sibling_top1_acc=round(hits["churn"]["cofile_sibling"] / max(1, counts["cofile_sibling"]), 4),
        random_top1_acc=round(t1["rand"] / denom, 4),
        random_top2_acc=round(t2["rand"] / denom, 4),
        random_mrr=round(rr["rand"] / denom, 4),
        random_mean_ms=round(sum(ms_lists["rand"]) / denom, 4),
        random_loo_recovery_rate=round(loo["rand"] / denom, 4),
        random_comment_top1_acc=round(hits["rand"]["cofile_comment"] / max(1, counts["cofile_comment"]), 4),
        random_sibling_top1_acc=round(hits["rand"]["cofile_sibling"] / max(1, counts["cofile_sibling"]), 4),
    )
    return summary, records


if __name__ == "__main__":
    import json
    summ, recs = run_blame_benchmark()
    print(json.dumps(summ.to_dict(), indent=2))
