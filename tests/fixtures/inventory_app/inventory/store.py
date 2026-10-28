"""Stock bookkeeping for a tiny warehouse."""


class Store:
    def __init__(self):
        self._stock: dict[str, int] = {}

    def add_stock(self, sku: str, qty: int) -> int:
        if qty <= 0:
            raise ValueError("quantity must be positive")
        self._stock[sku] = self._stock.get(sku, 0) + qty
        return self._stock[sku]

    def remove_stock(self, sku: str, qty: int) -> int:
        if qty <= 0:
            raise ValueError("quantity must be positive")
        self._stock[sku] = self._stock.get(sku, 0) - qty
        return self._stock[sku]

    def level(self, sku: str) -> int:
        return self._stock.get(sku, 0)
