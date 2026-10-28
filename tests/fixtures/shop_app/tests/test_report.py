from shop.report import find_duplicates


def test_finds_duplicates_in_a_large_list():
    items = list(range(7000)) + [7, 9]
    assert find_duplicates(items) == [7, 9]
