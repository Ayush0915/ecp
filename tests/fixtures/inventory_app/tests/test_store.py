import pytest

from inventory.store import Store


def test_add_and_level():
    s = Store()
    assert s.add_stock("apple", 5) == 5
    assert s.level("apple") == 5


def test_remove_reduces_level():
    s = Store()
    s.add_stock("apple", 5)
    assert s.remove_stock("apple", 2) == 3


def test_rejects_non_positive_quantity():
    s = Store()
    with pytest.raises(ValueError):
        s.add_stock("apple", 0)
    with pytest.raises(ValueError):
        s.remove_stock("apple", -1)
