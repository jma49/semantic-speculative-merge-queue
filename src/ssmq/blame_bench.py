#!/usr/bin/env python3
"""Real Executable Multi-PR Semantic Fault Attribution Micro-Benchmark (v3).

Constructs and ACTUALLY EXECUTES 64 realistic multi-PR failing integration
batches on an isolated multi-module Python codebase (6 interconnected modules,
18 functions, full unit/integration test suite) via subprocess `unittest`:

  - 24 disjoint single-PR functional regressions (sizes 4 and 8)
  - 16 co-file-distractor single-PR functional regressions (batches where an
    innocent PR edits a sibling function or docstring in the SAME file as the
    bug, directly testing AST symbol-level filtering vs file-level overlap)
  - 24 true pairwise cross-module semantic merge conflicts (strict invariant:
    PR_A passes alone, PR_B passes alone, PR_A + PR_B fails combined)

Evaluates four deterministic attribution rankers on the REAL stack traces:
  1. `RandomRanker` (lower bound)
  2. `ChurnRiskRanker` (ranks PRs solely by LOC churn + file count)
  3. `FileTracebackOverlapRanker` (strong non-AST baseline: parses stack trace
     files and failing test imports, scoring by file-level overlap + churn
     without AST function-level diffing or 1-hop call-graph expansion)
  4. `ASTStackCausalRanker` (ours: parses real Python tracebacks, filters
     comment/docstring-only edits via AST, extracts modified function symbols,
     expands a 1-hop AST call graph from failing test targets, and scores each
     PR by causal symbol reachability)

Outputs real measured Top-1 accuracy, Top-2 accuracy, Mean Reciprocal Rank (MRR),
per-category accuracy breakdown, and wall-clock attribution latency (ms).
"""
from __future__ import annotations

import ast
import copy
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Base multi-module Python repository (6 modules + integration tests)
BASE_REPO: Dict[str, str] = {
    "pkg/__init__.py": "",
    "pkg/pricing.py": """\
def base_price(units: int, unit_cost: float) -> float:
    return round(units * unit_cost, 2)

def apply_discount(amount: float, pct: float) -> float:
    return round(amount * (1.0 - pct), 2)

def compute_tax(subtotal: float, rate: float = 0.08) -> float:
    return round(subtotal * rate, 2)
""",
    "pkg/inventory.py": """\
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
def shipping_fee(weight_kg: float, express: bool = False) -> float:
    base = 5.0 + 1.5 * weight_kg
    return round(base * 2.0 if express else base, 2)

def estimate_days(zone: str, express: bool = False) -> int:
    table = {"local": 2, "regional": 4, "remote": 7}
    days = table.get(zone, 5)
    return max(1, days // 2) if express else days
""",
    "pkg/auth.py": """\
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
def format_entry(order_id: str, total: float) -> dict:
    return {"order_id": order_id, "total": round(total, 2), "currency": "USD"}

def summarize_entries(entries: list) -> float:
    return round(sum(e["total"] for e in entries), 2)
""",
    "pkg/checkout.py": """\
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
    def test_pricing_flow(self):
        self.assertEqual(base_price(4, 25.0), 100.0)
        self.assertEqual(apply_discount(100.0, 0.1), 90.0)
        self.assertEqual(compute_tax(90.0, 0.08), 7.2)

    def test_inventory_flow(self):
        cat = {"A": 5}
        self.assertTrue(check_stock("A", 3, cat))
        nxt = reserve_stock("A", 3, cat)
        self.assertEqual(nxt["A"], 2)
        self.assertEqual(restock_level("A", nxt), 8)

    def test_shipping_flow(self):
        self.assertEqual(shipping_fee(2.0, False), 8.0)
        self.assertEqual(shipping_fee(2.0, True), 16.0)
        self.assertEqual(estimate_days("regional", True), 2)

    def test_auth_flow(self):
        self.assertEqual(normalize_role(" Manager "), "manager")
        self.assertTrue(can_approve("manager", 500.0))
        self.assertFalse(can_approve("user", 500.0))

    def test_ledger_flow(self):
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


# Clean benign PRs across all modules (real functional AST edits)
CLEAN_PRS: List[PatchPR] = [
    PatchPR("clean_pricing_guard", {"pkg/pricing.py": ("return round(units * unit_cost, 2)", "if units < 0:\n        raise ValueError('neg')\n    return round(units * unit_cost, 2)")}, "Validate non-negative units in base_price", 2),
    PatchPR("clean_pricing_clamp", {"pkg/pricing.py": ("return round(subtotal * rate, 2)", "eff_rate = max(0.0, rate)\n    return round(subtotal * eff_rate, 2)")}, "Clamp negative tax rate to zero", 2),
    PatchPR("clean_pricing_zero_disc", {"pkg/pricing.py": ("return round(amount * (1.0 - pct), 2)", "if pct == 0.0:\n        return round(amount, 2)\n    return round(amount * (1.0 - pct), 2)")}, "Fast-path zero discount", 2),
    PatchPR("clean_inv_check_zero", {"pkg/inventory.py": ("return catalog.get(sku, 0) >= qty", "avail = catalog.get(sku, 0)\n    return avail >= qty and qty >= 0")}, "Validate non-negative qty in check_stock", 2),
    PatchPR("clean_inv_helper", {"pkg/inventory.py": ("return max(0, 10 - catalog.get(sku, 0))", "target = 10\n    return max(0, target - catalog.get(sku, 0))")}, "Extract restock target constant", 2),
    PatchPR("clean_ship_zone", {"pkg/shipping.py": ('"remote": 7}', '"remote": 7, "island": 9}')}, "Add island shipping zone", 1),
    PatchPR("clean_ship_floor", {"pkg/shipping.py": ("base = 5.0 + 1.5 * weight_kg", "w = max(0.0, weight_kg)\n    base = 5.0 + 1.5 * w")}, "Clamp negative weight in shipping_fee", 2),
    PatchPR("clean_auth_lead", {"pkg/auth.py": ('if r == "admin":', 'if r in ("admin", "superadmin"):')}, "Support superadmin role in can_approve", 1),
    PatchPR("clean_auth_empty", {"pkg/auth.py": ("return role.strip().lower()", "cleaned = (role or '').strip().lower()\n    return cleaned")}, "Handle empty role string safely", 2),
    PatchPR("clean_ledger_meta", {"pkg/ledger.py": ('"currency": "USD"}', '"currency": "USD", "v": 1}')}, "Add schema version key to ledger entry", 1),
    PatchPR("clean_ledger_empty", {"pkg/ledger.py": ('return round(sum(e["total"] for e in entries), 2)', 'if not entries:\n        return 0.0\n    return round(sum(e["total"] for e in entries), 2)')}, "Fast-path empty ledger summary", 2),
    PatchPR("clean_checkout_meta", {"pkg/checkout.py": ('"total": total,', '"total": total,\n        "discount_applied": discount > 0,')}, "Include discount_applied metadata in checkout", 1),
]

# Co-file benign distractor PRs (touch the SAME file via comments/docstrings or
# uncalled helper code, with higher line churn to challenge file-level rankers)
COFILE_DISTRACTOR_PRS: Dict[str, PatchPR] = {
    "pkg/pricing.py": PatchPR(
        "cofile_pricing_docs",
        {"pkg/pricing.py": ("def base_price(", "# Pricing helpers for order totals.\n# All monetary values are rounded to 2 decimal places.\n# Currency defaults to USD unless converted downstream.\ndef base_price(")},
        "Document pricing rounding policy in module header",
        3,
    ),
    "pkg/inventory.py": PatchPR(
        "cofile_inv_docs",
        {"pkg/inventory.py": ("def check_stock(", "# Inventory catalog utilities.\n# Catalog keys are string SKUs mapped to integer stock.\n# Negative balances are prevented at checkout.\ndef check_stock(")},
        "Add module documentation comments to inventory",
        3,
    ),
    "pkg/shipping.py": PatchPR(
        "cofile_ship_docs",
        {"pkg/shipping.py": ("def shipping_fee(", "# Shipping cost and transit-day estimators.\n# Express mode halves transit days and doubles base fee.\n# Remote zones default to 7 business days.\ndef shipping_fee(")},
        "Document shipping fee and zone table",
        3,
    ),
    "pkg/auth.py": PatchPR(
        "cofile_auth_docs",
        {"pkg/auth.py": ("def normalize_role(", "# Role-based approval policy module.\n# Admins have unrestricted order approval limits.\n# Managers may approve orders up to 1000.0 USD.\ndef normalize_role(")},
        "Document role approval thresholds",
        3,
    ),
    "pkg/ledger.py": PatchPR(
        "cofile_ledger_docs",
        {"pkg/ledger.py": ("def format_entry(", "# Ledger formatting and aggregation helpers.\n# Entries are stored as normalized dictionaries.\n# Totals are rounded to cents.\ndef format_entry(")},
        "Document ledger entry schema",
        3,
    ),
    "pkg/checkout.py": PatchPR(
        "cofile_checkout_docs",
        {"pkg/checkout.py": ("def process_order(", "# Orchestrates stock check, pricing, shipping, auth, and ledger.\n# Raises ValueError on insufficient stock.\n# Raises PermissionError when approval limit is exceeded.\ndef process_order(")},
        "Document checkout orchestration flow",
        3,
    ),
}

# 12 real single-PR functional defects spanning every module
DEFECT_PRS: List[PatchPR] = [
    PatchPR("bug_pricing_mult", {"pkg/pricing.py": ("units * unit_cost", "units + unit_cost")}, "Wrong operator in base_price", 1),
    PatchPR("bug_pricing_disc", {"pkg/pricing.py": ("1.0 - pct", "1.0 + pct")}, "Inverted discount sign", 1),
    PatchPR("bug_pricing_tax", {"pkg/pricing.py": ("subtotal * rate", "subtotal * (rate * 2.0)")}, "Double tax calculation", 1),
    PatchPR("bug_inv_check", {"pkg/inventory.py": ("catalog.get(sku, 0) >= qty", "catalog.get(sku, 0) > qty + 5")}, "Off-by-5 stock guard", 1),
    PatchPR("bug_inv_reserve", {"pkg/inventory.py": ("updated.get(sku, 0) - qty", "updated.get(sku, 0) + qty")}, "Stock added instead of deducted", 1),
    PatchPR("bug_inv_restock", {"pkg/inventory.py": ("10 - catalog.get(sku, 0)", "5 - catalog.get(sku, 0)")}, "Broken restock target", 1),
    PatchPR("bug_ship_fee", {"pkg/shipping.py": ("5.0 + 1.5 * weight_kg", "5.0 + 3.0 * weight_kg")}, "Incorrect weight multiplier", 1),
    PatchPR("bug_ship_days", {"pkg/shipping.py": ("days // 2", "days * 2")}, "Express doubles delivery days", 1),
    PatchPR("bug_auth_norm", {"pkg/auth.py": ("role.strip().lower()", "role.strip().upper()")}, "Uppercase breaks role check", 1),
    PatchPR("bug_auth_limit", {"pkg/auth.py": ("amount <= 1000.0", "amount <= 50.0")}, "Manager approval cap broken", 1),
    PatchPR("bug_ledger_sum", {"pkg/ledger.py": ('sum(e["total"] for e in entries)', 'sum(e["total"] for e in entries) + 1.0')}, "Off-by-one ledger summary", 1),
    PatchPR("bug_checkout_total", {"pkg/checkout.py": ("sub + tax + ship", "sub + tax")}, "Checkout drops shipping fee", 1),
]

# 6 genuine pairwise semantic conflicts across different files:
# STRICT INVARIANT: PR_A passes alone, PR_B passes alone, and (PR_A + PR_B) fails combined!
SEMANTIC_CONFLICT_PAIRS: List[Tuple[PatchPR, PatchPR]] = [
    (
        PatchPR("sem_pricing_round0", {"pkg/pricing.py": ("return round(amount * (1.0 - pct), 2)", "return round(amount * (1.0 - pct), 0)")}, "Round discounted subtotal to whole dollars", 1),
        PatchPR("sem_checkout_penny", {"pkg/checkout.py": ("sub = apply_discount(base_price(qty, unit_cost), discount)", "sub = apply_discount(base_price(qty, unit_cost) + 0.01, discount) - 0.01")}, "Sub-cent adjustment around apply_discount", 1),
    ),
    (
        PatchPR("sem_pricing_tax_tier", {"pkg/pricing.py": ("return round(subtotal * rate, 2)", "eff = rate + (0.02 if subtotal > 95.0 else 0.0)\n    return round(subtotal * eff, 2)")}, "Add luxury tax tier above $95", 2),
        PatchPR("sem_checkout_tax_bundle", {"pkg/checkout.py": ("tax = compute_tax(sub)\n    ship = shipping_fee(weight_kg, express=express)", "ship = shipping_fee(weight_kg, express=express)\n    tax = round(compute_tax(sub + ship) - compute_tax(ship), 2)")}, "Compute tax via linear bundle difference", 2),
    ),
    (
        PatchPR("sem_inv_inplace_mut", {"pkg/inventory.py": ("updated = dict(catalog)\n    updated[sku] = updated.get(sku, 0) - qty\n    return updated", "catalog[sku] = catalog.get(sku, 0) - qty\n    return catalog")}, "Avoid dict copy by mutating catalog in place", 1),
        PatchPR("sem_checkout_cat_invar", {"pkg/checkout.py": ("new_catalog = reserve_stock(sku, qty, catalog)", "new_catalog = reserve_stock(sku, qty, catalog)\n    assert catalog[sku] == new_catalog[sku] + qty")}, "Assert input catalog immutability after reserve", 1),
    ),
    (
        PatchPR("sem_auth_strict_trim", {"pkg/auth.py": ("r = normalize_role(role)", 'if role != role.strip():\n        return False\n    r = normalize_role(role)')}, "Reject untrimmed whitespace roles in can_approve", 2),
        PatchPR("sem_checkout_pad_role", {"pkg/checkout.py": ("if not can_approve(role, total):", 'if not can_approve(" " + role + " ", total):')}, "Pass padded role assuming normalize_role strips", 1),
    ),
    (
        PatchPR("sem_ledger_id_prefix", {"pkg/ledger.py": ('return {"order_id": order_id, "total": round(total, 2), "currency": "USD"}', 'if not order_id.startswith("o"):\n        raise ValueError("order_id must start with lowercase o")\n    return {"order_id": order_id, "total": round(total, 2), "currency": "USD"}')}, "Enforce lowercase 'o' prefix on ledger order_id", 2),
        PatchPR("sem_checkout_upper_id", {"pkg/checkout.py": ("entry = format_entry(order_id, total)", "entry = format_entry(order_id.upper(), total)")}, "Normalize order_id to uppercase for ledger", 1),
    ),
    (
        PatchPR("sem_ship_strict_bool", {"pkg/shipping.py": ("base = 5.0 + 1.5 * weight_kg", 'if not isinstance(express, bool):\n        raise TypeError("express must be bool")\n    base = 5.0 + 1.5 * weight_kg')}, "Enforce strict bool type for express flag", 2),
        PatchPR("sem_checkout_opt_expr", {"pkg/checkout.py": ("ship = shipping_fee(weight_kg, express=express)", "ship = shipping_fee(weight_kg, express=(True if express else None))")}, "Pass None for non-express relying on truthiness", 1),
    ),
]


def _apply_patches(prs: List[PatchPR]) -> Dict[str, str]:
    repo = copy.deepcopy(BASE_REPO)
    for pr in prs:
        for rel_path, (old, new) in pr.files.items():
            if old not in repo[rel_path]:
                continue
            repo[rel_path] = repo[rel_path].replace(old, new, 1)
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
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode == 0, (proc.stdout + "\n" + proc.stderr)


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


def rank_file_traceback_overlap(prs: List[PatchPR], traceback_text: str) -> List[str]:
    """Strong non-AST deterministic baseline: file-level traceback + test-module overlap.

    Parses the exact same real Python traceback to score PRs whose touched files
    appear in stack frames or match the failing test's module name, breaking ties
    by line churn. Unlike ASTStackCausalRanker, it operates purely at file granularity
    without AST symbol diffing, comment filtering, or 1-hop function call-graph expansion.
    """
    tb_frames = re.findall(r'File "([^"]+)", line \d+, in ([A-Za-z0-9_<>]+)', traceback_text)
    implicated_files: Dict[str, float] = {}
    for depth, (fpath, _) in enumerate(tb_frames, start=1):
        norm = fpath.replace("\\", "/")
        for repo_rel in BASE_REPO:
            if norm.endswith(repo_rel):
                implicated_files[repo_rel] = max(implicated_files.get(repo_rel, 0.0), float(depth))

    # Map failing test names (e.g., test_pricing_flow) heuristically to pkg/<name>.py
    test_failures = re.findall(r"(?:FAIL|ERROR): (test_[a-zA-Z0-9_]+)", traceback_text)
    test_hint_files: Set[str] = set()
    for tf in test_failures:
        for repo_rel in BASE_REPO:
            if repo_rel.startswith("pkg/"):
                stem = repo_rel.split("/")[-1].replace(".py", "")
                if stem in tf:
                    test_hint_files.add(repo_rel)

    scored: List[Tuple[float, int, str]] = []
    for pr in prs:
        score = 0.0
        for rel in pr.files:
            if rel in implicated_files:
                score += 4.0 + 0.5 * implicated_files[rel]
            if rel in test_hint_files:
                score += 3.0
        scored.append((score, pr.added_lines, pr.pr_id))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [pid for _, _, pid in scored]


def rank_ast_stack_causal(prs: List[PatchPR], traceback_text: str) -> List[str]:
    """Deterministic AST-diff + stack-trace causal blame ranker (zero LLM oracle)."""
    tb_frames = re.findall(r'File "([^"]+)", line \d+, in ([A-Za-z0-9_<>]+)', traceback_text)
    implicated_files: Dict[str, float] = {}
    implicated_funcs: Dict[str, float] = {}
    for depth, (fpath, func) in enumerate(tb_frames, start=1):
        norm = fpath.replace("\\", "/")
        for repo_rel in BASE_REPO:
            if norm.endswith(repo_rel):
                implicated_files[repo_rel] = max(implicated_files.get(repo_rel, 0.0), float(depth))
                implicated_funcs[func] = max(implicated_funcs.get(func, 0.0), float(depth))

    test_failures = re.findall(r"(?:FAIL|ERROR): (test_[a-zA-Z0-9_]+)", traceback_text)
    test_tree = ast.parse(BASE_REPO["tests/test_integration.py"])
    called_in_failed_tests: Set[str] = set()
    for node in ast.walk(test_tree):
        if isinstance(node, ast.FunctionDef) and node.name in test_failures:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    called_in_failed_tests.add(sub.func.id)

    func_to_file: Dict[str, str] = {}
    func_calls: Dict[str, Set[str]] = {}
    for rel, src in BASE_REPO.items():
        if not rel.startswith("pkg/"):
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_to_file[node.name] = rel
                calls = {
                    sub.func.id
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                }
                func_calls[node.name] = calls

    expanded_funcs = set(called_in_failed_tests)
    for fn in list(called_in_failed_tests):
        expanded_funcs.update(func_calls.get(fn, set()))
    expanded_files = {func_to_file[fn] for fn in expanded_funcs if fn in func_to_file}

    scored: List[Tuple[float, str]] = []
    for pr in prs:
        score = 0.0
        for rel, (old, new) in pr.files.items():
            patched_src = BASE_REPO[rel].replace(old, new, 1)
            changed_syms = _extract_changed_symbols(BASE_REPO[rel], patched_src)
            if not changed_syms:
                # Comment/docstring/whitespace-only PR -> near-zero causal suspicion
                score += 0.01
                continue
            score += 2.0
            if rel in implicated_files:
                score += 4.0 + 0.5 * implicated_files[rel]
            if rel in expanded_files:
                score += 3.0
            for sym in changed_syms:
                if sym in implicated_funcs:
                    score += 6.0 + implicated_funcs[sym]
                if sym in called_in_failed_tests:
                    score += 5.0
                elif sym in expanded_funcs:
                    score += 3.5
            if rel in expanded_files or (_extract_module_imports(patched_src) & expanded_files):
                score += 1.0
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
    cofile_distractor_batches: int
    semantic_conflict_batches: int
    ast_stack_top1_acc: float
    ast_stack_top2_acc: float
    ast_stack_mrr: float
    ast_stack_mean_ms: float
    ast_stack_cofile_top1_acc: float
    ast_stack_sem_top1_acc: float
    file_overlap_top1_acc: float
    file_overlap_top2_acc: float
    file_overlap_mrr: float
    file_overlap_mean_ms: float
    file_overlap_cofile_top1_acc: float
    file_overlap_sem_top1_acc: float
    churn_risk_top1_acc: float
    churn_risk_top2_acc: float
    random_top1_acc: float
    random_top2_acc: float

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def run_blame_benchmark(seed: int = 2026) -> Tuple[BlameBenchSummary, List[Dict[str, object]]]:
    rng = random.Random(seed)
    records: List[Dict[str, object]] = []

    cases: List[Tuple[str, List[PatchPR], Set[str]]] = []

    # 1. 24 disjoint single-PR defect batches (sizes 4 and 8)
    for defect in DEFECT_PRS:
        for batch_size in (4, 8):
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(defect.files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 1, len(clean_pool)))
            batch = chosen_clean + [defect]
            rng.shuffle(batch)
            cases.append(("single_defect", batch, {defect.pr_id}))

    # 2. 16 co-file-distractor single-PR defect batches: include a benign doc/comment
    #    PR editing the EXACT SAME FILE as the defect to test AST filtering vs file overlap!
    for idx, defect in enumerate(DEFECT_PRS):
        defect_file = next(iter(defect.files))
        distractor = COFILE_DISTRACTOR_PRS[defect_file]
        sizes = (4, 8) if idx < 4 else (6,)
        for batch_size in sizes:
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(defect.files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 2, len(clean_pool)))
            batch = chosen_clean + [distractor, defect]
            rng.shuffle(batch)
            cases.append(("cofile_distractor", batch, {defect.pr_id}))

    # 3. 24 pairwise semantic conflict batches (4 trials x 6 conflict pairs, sizes 4..8)
    for pr_a, pr_b in SEMANTIC_CONFLICT_PAIRS:
        for batch_size in (4, 6, 6, 8):
            conflict_files = set(pr_a.files) | set(pr_b.files)
            clean_pool = [c for c in CLEAN_PRS if set(c.files).isdisjoint(conflict_files)]
            chosen_clean = rng.sample(clean_pool, min(batch_size - 2, len(clean_pool)))
            batch = chosen_clean + [pr_a, pr_b]
            rng.shuffle(batch)
            cases.append(("semantic_conflict", batch, {pr_a.pr_id, pr_b.pr_id}))

    ast_t1 = ast_t2 = 0
    ast_rr_sum = 0.0
    ast_ms_sum = 0.0
    ast_cofile_t1 = ast_sem_t1 = 0

    file_t1 = file_t2 = 0
    file_rr_sum = 0.0
    file_ms_sum = 0.0
    file_cofile_t1 = file_sem_t1 = 0

    churn_t1 = churn_t2 = 0
    rand_t1 = rand_t2 = 0
    executed_failures = 0
    n_single = n_cofile = n_sem = 0

    for idx, (kind, batch, culprits) in enumerate(cases):
        passed, tb = execute_batch(batch)
        if passed:
            continue
        executed_failures += 1
        if kind == "single_defect":
            n_single += 1
        elif kind == "cofile_distractor":
            n_cofile += 1
        else:
            n_sem += 1

        t0 = time.perf_counter()
        ast_rank = rank_ast_stack_causal(batch, tb)
        elapsed_ast_ms = (time.perf_counter() - t0) * 1000.0
        ast_ms_sum += elapsed_ast_ms

        t1 = time.perf_counter()
        file_rank = rank_file_traceback_overlap(batch, tb)
        elapsed_file_ms = (time.perf_counter() - t1) * 1000.0
        file_ms_sum += elapsed_file_ms

        churn_rank = rank_churn_risk(batch)
        rand_rank = [p.pr_id for p in batch]
        rng.shuffle(rand_rank)

        a1 = int(ast_rank[0] in culprits)
        a2 = int(any(x in culprits for x in ast_rank[:2]))
        ast_first = next(i + 1 for i, x in enumerate(ast_rank) if x in culprits)
        ast_t1 += a1
        ast_t2 += a2
        ast_rr_sum += 1.0 / ast_first

        f1 = int(file_rank[0] in culprits)
        f2 = int(any(x in culprits for x in file_rank[:2]))
        file_first = next(i + 1 for i, x in enumerate(file_rank) if x in culprits)
        file_t1 += f1
        file_t2 += f2
        file_rr_sum += 1.0 / file_first

        if kind == "cofile_distractor":
            ast_cofile_t1 += a1
            file_cofile_t1 += f1
        elif kind == "semantic_conflict":
            ast_sem_t1 += a1
            file_sem_t1 += f1

        churn_t1 += int(churn_rank[0] in culprits)
        churn_t2 += int(any(x in culprits for x in churn_rank[:2]))
        rand_t1 += int(rand_rank[0] in culprits)
        rand_t2 += int(any(x in culprits for x in rand_rank[:2]))

        records.append({
            "case_id": idx,
            "kind": kind,
            "batch_size": len(batch),
            "culprits": sorted(culprits),
            "ast_top1": ast_rank[0],
            "ast_top2": ast_rank[:2],
            "ast_hit_top1": bool(a1),
            "ast_hit_top2": bool(a2),
            "ast_latency_ms": round(elapsed_ast_ms, 3),
            "file_top1": file_rank[0],
            "file_hit_top1": bool(f1),
            "file_hit_top2": bool(f2),
            "file_latency_ms": round(elapsed_file_ms, 3),
        })

    denom = max(1, executed_failures)
    summary = BlameBenchSummary(
        total_failing_batches=executed_failures,
        single_defect_batches=n_single,
        cofile_distractor_batches=n_cofile,
        semantic_conflict_batches=n_sem,
        ast_stack_top1_acc=round(ast_t1 / denom, 4),
        ast_stack_top2_acc=round(ast_t2 / denom, 4),
        ast_stack_mrr=round(ast_rr_sum / denom, 4),
        ast_stack_mean_ms=round(ast_ms_sum / denom, 3),
        ast_stack_cofile_top1_acc=round(ast_cofile_t1 / max(1, n_cofile), 4),
        ast_stack_sem_top1_acc=round(ast_sem_t1 / max(1, n_sem), 4),
        file_overlap_top1_acc=round(file_t1 / denom, 4),
        file_overlap_top2_acc=round(file_t2 / denom, 4),
        file_overlap_mrr=round(file_rr_sum / denom, 4),
        file_overlap_mean_ms=round(file_ms_sum / denom, 3),
        file_overlap_cofile_top1_acc=round(file_cofile_t1 / max(1, n_cofile), 4),
        file_overlap_sem_top1_acc=round(file_sem_t1 / max(1, n_sem), 4),
        churn_risk_top1_acc=round(churn_t1 / denom, 4),
        churn_risk_top2_acc=round(churn_t2 / denom, 4),
        random_top1_acc=round(rand_t1 / denom, 4),
        random_top2_acc=round(rand_t2 / denom, 4),
    )
    return summary, records


if __name__ == "__main__":
    import json
    summ, recs = run_blame_benchmark()
    print(json.dumps(summ.to_dict(), indent=2))
