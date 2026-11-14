"""Explicit state machines. Every state change is validated here and recorded as an event."""
from __future__ import annotations

RMA_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"REQUESTED"}),
    "REQUESTED": frozenset({"AUTHORIZED", "REJECTED"}),
    "AUTHORIZED": frozenset({"IN_TRANSIT", "EXPIRED"}),
    "IN_TRANSIT": frozenset({"RECEIVED", "SHIPMENT_LOST"}),
    "RECEIVED": frozenset({"INSPECTED"}),
    "INSPECTED": frozenset({"COMPLETED"}),
    "REJECTED": frozenset(),
    "EXPIRED": frozenset(),
    "SHIPMENT_LOST": frozenset(),
    "COMPLETED": frozenset(),
}

SHIPMENT_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"LABEL_CREATED"}),
    "LABEL_CREATED": frozenset({"IN_TRANSIT", "EXPIRED"}),
    "IN_TRANSIT": frozenset({"RECEIVED", "LOST"}),
    "RECEIVED": frozenset(),
    "LOST": frozenset(),
    "EXPIRED": frozenset(),
}

TERMINAL_RMA_STATES = frozenset(s for s, nxt in RMA_TRANSITIONS.items() if s and not nxt)

MACHINES = {"RETURN_REQUEST": RMA_TRANSITIONS, "RETURN_SHIPMENT": SHIPMENT_TRANSITIONS}


class IllegalTransition(Exception):
    pass


def can_transition(machine: str, current: str | None, target: str) -> bool:
    return target in MACHINES[machine].get(current, frozenset())


def assert_transition(machine: str, current: str | None, target: str) -> None:
    if not can_transition(machine, current, target):
        allowed = sorted(MACHINES[machine].get(current, frozenset()))
        raise IllegalTransition(f"{machine}: {current} → {target} not allowed (allowed: {allowed})")


def is_valid_path(machine: str, states: list[str]) -> bool:
    prev: str | None = None
    for s in states:
        if not can_transition(machine, prev, s):
            return False
        prev = s
    return True
