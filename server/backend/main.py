from typing import Any

from app import app, DB_PATH, mcp
from inventory import router as inventory_router, configure_inventory, init_inventory_db

# Inventory is deliberately isolated from the customer portal. It only references
# customers optionally when an issue is assigned to a customer.
init_inventory_db(DB_PATH)
configure_inventory(DB_PATH)
app.include_router(inventory_router)


def _inventory_connect():
    import sqlite3
    con=sqlite3.connect(DB_PATH)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


@mcp.tool()
def ins_ei_inventory_items(search: str | None = None) -> dict[str, Any]:
    """Search inventory articles and return stock by location, prices and reorder state."""
    needle=str(search or "").strip().casefold()
    with _inventory_connect() as con:
        rows=con.execute("""SELECT i.*,COALESCE(SUM(s.quantity),0) total_stock
            FROM inventory_items i LEFT JOIN inventory_stock s ON s.item_id=i.id
            WHERE i.active=1 GROUP BY i.id ORDER BY i.name COLLATE NOCASE""").fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            hay=" ".join(str(item.get(k) or "") for k in ("article_number","name","manufacturer","category","supplier","sevdesk_article_number")).casefold()
            if needle and needle not in hay: continue
            item["stocks"]=[dict(x) for x in con.execute("""SELECT l.id location_id,l.name,l.code,COALESCE(s.quantity,0) quantity
                FROM inventory_locations l LEFT JOIN inventory_stock s ON s.location_id=l.id AND s.item_id=?
                WHERE l.active=1 ORDER BY l.name""",(row["id"],)).fetchall()]
            item["needs_reorder"]=float(item["total_stock"]) < float(item["minimum_stock"] or 0)
            result.append(item)
    return {"count":len(result),"items":result}


@mcp.tool()
def ins_ei_inventory_item_create(name: str, article_number: str | None = None,
                                 manufacturer: str | None = None, category: str | None = None,
                                 supplier: str | None = None, unit: str = "Stk.",
                                 purchase_price_net: float | None = None, sales_price_net: float | None = None,
                                 minimum_stock: float = 0, target_stock: float | None = None,
                                 sevdesk_article_number: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """Create a new inventory article. Prices and sevdesk reference are optional."""
    from inventory import InventoryItemCreate
    payload=InventoryItemCreate(name=name,article_number=article_number,manufacturer=manufacturer,
        category=category,supplier=supplier,unit=unit,purchase_price_net=purchase_price_net,
        sales_price_net=sales_price_net,minimum_stock=minimum_stock,target_stock=target_stock,
        sevdesk_article_number=sevdesk_article_number,notes=notes)
    for route in inventory_router.routes:
        if getattr(route,"path","")=="/api/inventory/items" and "POST" in getattr(route,"methods",set()):
            return route.endpoint(payload)
    raise RuntimeError("INVENTORY_ITEM_CREATE_ENDPOINT_MISSING")


@mcp.tool()
def ins_ei_inventory_locations() -> dict[str, Any]:
    """Return active inventory locations."""
    with _inventory_connect() as con:
        rows=[dict(x) for x in con.execute("SELECT id,name,code FROM inventory_locations WHERE active=1 ORDER BY name")]
    return {"count":len(rows),"locations":rows}


@mcp.tool()
def ins_ei_inventory_book(item_id: int, movement_type: str, quantity: float,
                          from_location_id: int | None = None, to_location_id: int | None = None,
                          customer_id: int | None = None, note: str | None = None) -> dict[str, Any]:
    """Book inventory receipt, issue or transfer. Customer is optional for issues."""
    if movement_type not in {"receipt","issue","transfer"}: raise ValueError("movement_type must be receipt, issue or transfer")
    if quantity <= 0: raise ValueError("quantity must be positive")
    from inventory import InventoryMovementCreate
    payload=InventoryMovementCreate(item_id=item_id,movement_type=movement_type,quantity=quantity,
        from_location_id=from_location_id,to_location_id=to_location_id,customer_id=customer_id,note=note)
    # Reuse the exact same validated booking logic as the service-center API.
    for route in inventory_router.routes:
        if getattr(route,"path","")=="/api/inventory/movements" and "POST" in getattr(route,"methods",set()):
            return route.endpoint(payload)
    raise RuntimeError("INVENTORY_BOOKING_ENDPOINT_MISSING")


@mcp.tool()
def ins_ei_inventory_reorder() -> dict[str, Any]:
    """Return all articles below minimum stock with suggested order quantity."""
    with _inventory_connect() as con:
        rows=[dict(x) for x in con.execute("""SELECT i.id,i.article_number,i.name,i.manufacturer,i.supplier,i.unit,
            i.minimum_stock,i.target_stock,i.purchase_price_net,i.sales_price_net,COALESCE(SUM(s.quantity),0) total_stock,
            MAX(0,COALESCE(i.target_stock,i.minimum_stock)-COALESCE(SUM(s.quantity),0)) suggested_order_quantity
            FROM inventory_items i LEFT JOIN inventory_stock s ON s.item_id=i.id WHERE i.active=1
            GROUP BY i.id HAVING total_stock < i.minimum_stock ORDER BY COALESCE(i.supplier,''),i.name COLLATE NOCASE""")]
    return {"count":len(rows),"items":rows}
