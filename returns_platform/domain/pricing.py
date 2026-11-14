"""Order price waterfall: gross → line discount → allocated coupon → tax → paid."""
from __future__ import annotations

from dataclasses import dataclass

from returns_platform.domain.money import allocate_pro_rata, round_half_up


@dataclass(frozen=True)
class LineInput:
    quantity: int
    unit_price_paise: int
    line_discount_paise: int
    tax_rate_bp: int


@dataclass(frozen=True)
class LinePrice:
    quantity: int
    unit_price_paise: int
    line_discount_paise: int
    allocated_order_discount_paise: int
    taxable_paise: int
    tax_rate_bp: int
    tax_paise: int
    line_paid_paise: int

    @property
    def gross_paise(self) -> int:
        return self.quantity * self.unit_price_paise


@dataclass(frozen=True)
class OrderPrice:
    lines: tuple[LinePrice, ...]
    subtotal_paise: int
    line_discount_paise: int
    order_discount_paise: int
    tax_paise: int
    shipping_fee_paise: int
    total_paid_paise: int


def price_order(lines: list[LineInput], order_discount_paise: int, shipping_fee_paise: int) -> OrderPrice:
    if not lines:
        raise ValueError("order needs at least one line")
    for ln in lines:
        if ln.quantity < 1 or ln.unit_price_paise < 1:
            raise ValueError("quantity and unit price must be positive")
        if not 0 <= ln.line_discount_paise <= ln.quantity * ln.unit_price_paise:
            raise ValueError("line discount out of range")
    net_before_coupon = [ln.quantity * ln.unit_price_paise - ln.line_discount_paise for ln in lines]
    alloc = allocate_pro_rata(order_discount_paise, net_before_coupon)
    priced = []
    for ln, net, a in zip(lines, net_before_coupon, alloc):
        taxable = net - a
        tax = round_half_up(taxable * ln.tax_rate_bp, 10000)
        priced.append(LinePrice(ln.quantity, ln.unit_price_paise, ln.line_discount_paise, a,
                                taxable, ln.tax_rate_bp, tax, taxable + tax))
    return OrderPrice(
        lines=tuple(priced),
        subtotal_paise=sum(p.gross_paise for p in priced),
        line_discount_paise=sum(p.line_discount_paise for p in priced),
        order_discount_paise=order_discount_paise,
        tax_paise=sum(p.tax_paise for p in priced),
        shipping_fee_paise=shipping_fee_paise,
        total_paid_paise=sum(p.line_paid_paise for p in priced) + shipping_fee_paise,
    )
