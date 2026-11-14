"""Generator parameters. `starter` and `full` use the SAME code path — only scale differs."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime

DEFAULT_SEED = 20261022


@dataclass(frozen=True)
class GenSettings:
    seed: int = DEFAULT_SEED
    n_customers: int = 12_000
    n_products: int = 600
    n_orders: int = 30_000
    n_requests: int = 5_000
    n_return_lines: int = 8_000
    first_order_date: date = date(2026, 1, 1)
    last_order_date: date = date(2026, 10, 12)
    as_of: datetime = datetime(2026, 10, 22, 23, 59, 59)   # snapshot cut-off; later events don't exist yet
    follow_up_request_share: float = 0.02      # second RMA on an already-returned order
    anomalies_per_type: int = 40
    rates: dict = field(default_factory=lambda: {
        "exchange": 0.15,
        "late_request": 0.12,
        "override_given_late": 0.25,
        "over_request": 0.04,                  # customer asks for more than purchased
        "partial_qty": 0.30,                   # returns fewer units than purchased
        "never_shipped": 0.04,
        "lost_in_transit": 0.025,
        "short_received": 0.03,
        "duplicate_refund_attempt": 0.03,      # client retry with a new key -> blocked
        "goodwill_overpay_attempt": 0.005,     # operator tries to pay more than owed -> blocked
    })


SCALES = {
    "full": GenSettings(),
    "starter": GenSettings(
        n_customers=1_200, n_products=150, n_orders=3_000, n_requests=500, n_return_lines=800,
        anomalies_per_type=6,
    ),
    "tiny": GenSettings(
        n_customers=150, n_products=60, n_orders=400, n_requests=80, n_return_lines=128,
        anomalies_per_type=3,
    ),
}


def settings_for(scale: str, seed: int | None = None) -> GenSettings:
    s = SCALES[scale]
    return replace(s, seed=seed) if seed is not None else s


def stream(seed: int, name: str) -> random.Random:
    """Independent, reproducible RNG per generation stage.

    Seeding each stage separately means changing one stage (e.g. anomaly volume)
    never shifts the random draws of another (the clean baseline stays identical).
    Uses SHA-256, not Python's salted hash(), so it is stable across processes.
    """
    digest = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))
