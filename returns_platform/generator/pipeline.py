"""Generation pipeline: master data → orders → returns lifecycle → expected labels → anomalies."""
from __future__ import annotations

from returns_platform.config import Policy, load_policy
from returns_platform.generator import anomalies, baseline
from returns_platform.generator.settings import GenSettings
from returns_platform.schema.spec import TABLES


def generate(s: GenSettings, policy: Policy | None = None, inject_anomalies: bool = True) -> baseline.Dataset:
    policy = policy or load_policy()
    ds = baseline.Dataset(settings=s, policy_version=policy.version)
    customers = baseline.gen_customers(s)
    products = baseline.gen_products(s)
    orders, lines = baseline.gen_orders(s, policy, customers, products)
    ds.tables["customer"] = customers
    ds.tables["product"] = products
    ds.tables["sales_order"] = orders
    ds.tables["order_line"] = lines
    baseline.gen_returns(s, policy, ds, customers, products, orders, lines)
    ds.expected = baseline.expected_outcomes(ds)
    if inject_anomalies:
        anomalies.inject(s, policy, ds)
    for name, rows in ds.tables.items():
        pk = TABLES[name].pk.name
        rows.sort(key=lambda r: r[pk])
    return ds
