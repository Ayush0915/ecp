"""HTTP layer over the Store."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .store import Store

app = FastAPI()
store = Store()


class Move(BaseModel):
    sku: str
    qty: int


@app.post("/stock/add")
def add(m: Move):
    try:
        return {"sku": m.sku, "level": store.add_stock(m.sku, m.qty)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/stock/remove")
def remove(m: Move):
    try:
        return {"sku": m.sku, "level": store.remove_stock(m.sku, m.qty)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/stock/{sku}")
def get(sku: str):
    return {"sku": sku, "level": store.level(sku)}
