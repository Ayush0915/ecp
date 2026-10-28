from shop.auth import check_admin, hash_password


def test_admin_check():
    assert check_admin("hunter2-admin-pass")
    assert not check_admin("nope")


def test_hash_is_stable():
    assert hash_password("a") == hash_password("a")
