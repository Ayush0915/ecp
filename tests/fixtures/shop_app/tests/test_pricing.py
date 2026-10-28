from shop.pricing import add_tax


def test_add_tax():
    assert add_tax(100) == 118.0
