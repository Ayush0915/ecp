from fastapi.testclient import TestClient

from inventory import api

client = TestClient(api.app)


def test_add_then_get():
    client.post("/stock/add", json={"sku": "pear", "qty": 4})
    assert client.get("/stock/pear").json()["level"] == 4


def test_bad_quantity_is_400():
    assert client.post("/stock/add", json={"sku": "pear", "qty": 0}).status_code == 400
