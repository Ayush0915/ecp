"""Prices and discounts for a tiny shop."""
import os


def apply_discount(price: float, percent: float) -> float:
    """Return the price after a percentage discount."""
    return price - price * percent / 100


def bulk_price(prices: list[float], percent: float) -> list[float]:
    # TODO: round to two decimals
    return [apply_discount(p, percent) for p in prices]


def add_tax(price: float, rate: float = 0.18) -> float:
    return round(price * (1 + rate), 2)
