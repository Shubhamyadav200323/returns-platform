"""Loads the versioned return policy. All rule engines take a `Policy` object,
so tests can inject variants without touching the YAML."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POLICY_PATH = ROOT / "config" / "return_policy.yaml"


@dataclass(frozen=True)
class CategoryRule:
    name: str
    window_days: int
    returnable: bool
    restocking_fee_bp: int
    refurbishable: bool
    tax_rate_bp: int


@dataclass(frozen=True)
class Policy:
    version: str
    effective_from: date
    currency: str
    categories: dict[str, CategoryRule]
    reason_fault: dict[str, str]
    return_shipping_fee_paise: int
    grade_refund_bp: dict[str, int]
    merchant_fault_grade_floor_bp: int
    disposition_by_grade: dict[str, dict[str, str]]
    ship_by_days: int
    sla: dict[str, int] = field(default_factory=dict)

    def category(self, name: str) -> CategoryRule:
        try:
            return self.categories[name]
        except KeyError as exc:
            raise ValueError(f"unknown category {name!r}") from exc

    def is_merchant_fault(self, reason_code: str) -> bool:
        try:
            return self.reason_fault[reason_code] == "merchant"
        except KeyError as exc:
            raise ValueError(f"unknown reason code {reason_code!r}") from exc


def load_policy(path: Path | str = DEFAULT_POLICY_PATH) -> Policy:
    raw = yaml.safe_load(Path(path).read_text())
    cats = {
        name: CategoryRule(name=name, **spec) for name, spec in raw["categories"].items()
    }
    return Policy(
        version=raw["policy_version"],
        effective_from=date.fromisoformat(str(raw["effective_from"])),
        currency=raw["currency"],
        categories=cats,
        reason_fault=dict(raw["reasons"]),
        return_shipping_fee_paise=int(raw["return_shipping_fee_paise"]),
        grade_refund_bp={k: int(v) for k, v in raw["grade_refund_bp"].items()},
        merchant_fault_grade_floor_bp=int(raw["merchant_fault_grade_floor_bp"]),
        disposition_by_grade=raw["disposition_by_grade"],
        ship_by_days=int(raw["ship_by_days"]),
        sla=dict(raw.get("sla", {})),
    )
