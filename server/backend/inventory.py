from datetime import datetime, timezone
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/inventory", tags=["inventory"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inventory_prices(manufacturer: str | None, purchase_price_net: float | None, sales_price_net: float | None):
    """OekoFEN purchase price is list price less the fixed 32% purchasing discount."""
    is_oekofen = str(manufacturer or "").strip().casefold() in {"ökofen".casefold(), "oekofen"}
    if purchase_price_net is None and is_oekofen and sales_price_net is not None:
        return round(float(sales_price_net) * 0.68, 2), sales_price_net
    if sales_price_net is None and not is_oekofen and purchase_price_net is not None:
        return purchase_price_net, round(float(purchase_price_net) * 1.40, 2)
    return purchase_price_net, sales_price_net


def init_inventory_db(db_path) -> None:
    """Create inventory tables without changing customer or portal schemas."""
    con = sqlite3.connect(db_path)
    try:
        con.executescript("""
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS inventory_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            code TEXT UNIQUE,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_number TEXT,
            name TEXT NOT NULL,
            manufacturer TEXT,
            category TEXT,
            supplier TEXT,
            unit TEXT NOT NULL DEFAULT 'Stk.',
            sevdesk_object_id TEXT,
            sevdesk_article_number TEXT,
            purchase_price_net REAL,
            sales_price_net REAL,
            minimum_stock REAL NOT NULL DEFAULT 0,
            target_stock REAL,
            active INTEGER NOT NULL DEFAULT 1,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_items_article_number
            ON inventory_items(article_number) WHERE article_number IS NOT NULL AND article_number <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_items_sevdesk
            ON inventory_items(sevdesk_object_id) WHERE sevdesk_object_id IS NOT NULL AND sevdesk_object_id <> '';

        CREATE TABLE IF NOT EXISTS inventory_stock (
            item_id INTEGER NOT NULL,
            location_id INTEGER NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(item_id, location_id),
            FOREIGN KEY(item_id) REFERENCES inventory_items(id) ON DELETE CASCADE,
            FOREIGN KEY(location_id) REFERENCES inventory_locations(id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS inventory_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            movement_type TEXT NOT NULL CHECK(movement_type IN ('receipt','issue','transfer','adjustment')),
            quantity REAL NOT NULL,
            from_location_id INTEGER,
            to_location_id INTEGER,
            customer_id INTEGER,
            occurred_at TEXT NOT NULL,
            note TEXT,
            unit_sales_price_net REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(item_id) REFERENCES inventory_items(id) ON DELETE RESTRICT,
            FOREIGN KEY(from_location_id) REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            FOREIGN KEY(to_location_id) REFERENCES inventory_locations(id) ON DELETE RESTRICT,
            FOREIGN KEY(customer_id) REFERENCES customers(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_movements_item ON inventory_movements(item_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_inventory_movements_customer ON inventory_movements(customer_id, occurred_at DESC);
        """)
        con.commit()
    finally:
        con.close()


class InventoryLocationCreate(BaseModel):
    name: str = Field(min_length=1)
    code: str | None = None


class InventoryItemCreate(BaseModel):
    name: str = Field(min_length=1)
    article_number: str | None = None
    manufacturer: str | None = None
    category: str | None = None
    supplier: str | None = None
    unit: str = "Stk."
    sevdesk_object_id: str | None = None
    sevdesk_article_number: str | None = None
    purchase_price_net: float | None = None
    sales_price_net: float | None = None
    minimum_stock: float = 0
    target_stock: float | None = None
    notes: str | None = None


class InventoryItemUpdate(InventoryItemCreate):
    active: bool = True


class InventoryMovementCreate(BaseModel):
    item_id: int
    movement_type: str
    quantity: float = Field(gt=0)
    from_location_id: int | None = None
    to_location_id: int | None = None
    customer_id: int | None = None
    occurred_at: str | None = None
    note: str | None = None


def configure_inventory(db_path) -> None:
    def connect():
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    @router.get("/locations")
    def inventory_locations():
        with connect() as con:
            return [dict(r) for r in con.execute(
                "SELECT * FROM inventory_locations WHERE active=1 ORDER BY name"
            ).fetchall()]

    @router.post("/locations")
    def inventory_location_create(payload: InventoryLocationCreate):
        now=_now()
        with connect() as con:
            try:
                cur=con.execute("INSERT INTO inventory_locations(name,code,created_at) VALUES(?,?,?)",
                                (payload.name.strip(),payload.code.strip() if payload.code else None,now))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409,f"INVENTORY_LOCATION_CONFLICT: {exc}")
            return {"id":cur.lastrowid}

    @router.get("/items")
    def inventory_items():
        with connect() as con:
            rows = con.execute("""
                SELECT i.*,
                       COALESCE(SUM(s.quantity),0) AS total_stock
                FROM inventory_items i
                LEFT JOIN inventory_stock s ON s.item_id=i.id
                WHERE i.active=1
                GROUP BY i.id
                ORDER BY i.name COLLATE NOCASE
            """).fetchall()
            result=[]
            for row in rows:
                item=dict(row)
                stocks=con.execute("""
                    SELECT l.id AS location_id,l.name,l.code,COALESCE(s.quantity,0) AS quantity
                    FROM inventory_locations l
                    LEFT JOIN inventory_stock s ON s.location_id=l.id AND s.item_id=?
                    WHERE l.active=1 ORDER BY l.name
                """,(row["id"],)).fetchall()
                item["stocks"]=[dict(s) for s in stocks]
                bus_stock=next((float(s["quantity"]) for s in item["stocks"] if str(s.get("code") or "").casefold()=="bus"),0.0)
                item["bus_stock"]=bus_stock
                item["needs_bus_refill"]=bus_stock < float(item["minimum_stock"] or 0)
                item["suggested_bus_refill_quantity"]=max(0,float(item["minimum_stock"] or 0)-bus_stock)
                item["needs_reorder"]=item["needs_bus_refill"]
                result.append(item)
            return result

    @router.post("/items")
    def inventory_item_create(payload: InventoryItemCreate):
        now=_now()
        purchase_price_net,sales_price_net=_inventory_prices(payload.manufacturer,payload.purchase_price_net,payload.sales_price_net)
        with connect() as con:
            try:
                cur=con.execute("""INSERT INTO inventory_items
                    (article_number,name,manufacturer,category,supplier,unit,sevdesk_object_id,
                     sevdesk_article_number,purchase_price_net,sales_price_net,minimum_stock,
                     target_stock,notes,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (payload.article_number,payload.name,payload.manufacturer,payload.category,
                     payload.supplier,payload.unit,payload.sevdesk_object_id,payload.sevdesk_article_number,
                     purchase_price_net,sales_price_net,payload.minimum_stock,
                     payload.target_stock,payload.notes,now,now))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409,f"INVENTORY_ITEM_CONFLICT: {exc}")
            return {"id":cur.lastrowid}

    @router.put("/items/{item_id}")
    def inventory_item_update(item_id: int, payload: InventoryItemUpdate):
        now=_now()
        purchase_price_net,sales_price_net=_inventory_prices(payload.manufacturer,payload.purchase_price_net,payload.sales_price_net)
        with connect() as con:
            exists=con.execute("SELECT id FROM inventory_items WHERE id=?",(item_id,)).fetchone()
            if exists is None:
                raise HTTPException(404,"INVENTORY_ITEM_NOT_FOUND")
            try:
                con.execute("""UPDATE inventory_items SET
                    article_number=?,name=?,manufacturer=?,category=?,supplier=?,unit=?,
                    sevdesk_object_id=?,sevdesk_article_number=?,purchase_price_net=?,sales_price_net=?,
                    minimum_stock=?,target_stock=?,active=?,notes=?,updated_at=?
                    WHERE id=?""",
                    (payload.article_number,payload.name,payload.manufacturer,payload.category,
                     payload.supplier,payload.unit,payload.sevdesk_object_id,payload.sevdesk_article_number,
                     purchase_price_net,sales_price_net,payload.minimum_stock,
                     payload.target_stock,int(payload.active),payload.notes,now,item_id))
            except sqlite3.IntegrityError as exc:
                raise HTTPException(409,f"INVENTORY_ITEM_CONFLICT: {exc}")
            return {"updated":True,"id":item_id}

    @router.delete("/items/{item_id}")
    def inventory_item_delete(item_id: int):
        """Archive an inventory item while preserving stock and movement history."""
        now=_now()
        with connect() as con:
            item=con.execute("""SELECT i.id,i.article_number,i.name,COALESCE(SUM(s.quantity),0) AS total_stock
                FROM inventory_items i LEFT JOIN inventory_stock s ON s.item_id=i.id
                WHERE i.id=? GROUP BY i.id""",(item_id,)).fetchone()
            if item is None:
                raise HTTPException(404,"INVENTORY_ITEM_NOT_FOUND")
            con.execute("UPDATE inventory_items SET active=0,updated_at=? WHERE id=?",(now,item_id))
            return {"archived":True,"id":item_id,"article_number":item["article_number"],
                    "name":item["name"],"preserved_stock":item["total_stock"]}

    @router.get("/reorder")
    def inventory_reorder():
        with connect() as con:
            return [dict(r) for r in con.execute("""
                SELECT i.id,i.article_number,i.name,i.manufacturer,i.supplier,i.unit,
                       i.minimum_stock AS minimum_bus_stock,i.purchase_price_net,i.sales_price_net,
                       COALESCE(bs.quantity,0) AS bus_stock,
                       MAX(0,i.minimum_stock-COALESCE(bs.quantity,0)) AS suggested_bus_refill_quantity
                FROM inventory_items i
                LEFT JOIN inventory_locations bl ON lower(bl.code)='bus' AND bl.active=1
                LEFT JOIN inventory_stock bs ON bs.item_id=i.id AND bs.location_id=bl.id
                WHERE i.active=1 AND COALESCE(bs.quantity,0) < i.minimum_stock
                ORDER BY COALESCE(i.supplier,''),i.name COLLATE NOCASE
            """).fetchall()]

    @router.post("/movements")
    def inventory_movement_create(payload: InventoryMovementCreate):
        if payload.movement_type not in {"receipt","issue","transfer","adjustment"}:
            raise HTTPException(400,"INVENTORY_MOVEMENT_TYPE_INVALID")
        if payload.movement_type=="receipt" and not payload.to_location_id:
            raise HTTPException(400,"INVENTORY_TO_LOCATION_REQUIRED")
        if payload.movement_type=="issue" and not payload.from_location_id:
            raise HTTPException(400,"INVENTORY_FROM_LOCATION_REQUIRED")
        if payload.movement_type=="transfer" and (not payload.from_location_id or not payload.to_location_id):
            raise HTTPException(400,"INVENTORY_TRANSFER_LOCATIONS_REQUIRED")

        occurred=payload.occurred_at or _now()
        now=_now()
        with connect() as con:
            item=con.execute("SELECT sales_price_net FROM inventory_items WHERE id=? AND active=1",(payload.item_id,)).fetchone()
            if item is None:
                raise HTTPException(404,"INVENTORY_ITEM_NOT_FOUND")

            def change(location_id: int, delta: float):
                current=con.execute("SELECT quantity FROM inventory_stock WHERE item_id=? AND location_id=?",
                                    (payload.item_id,location_id)).fetchone()
                quantity=float(current["quantity"]) if current else 0.0
                new_quantity=quantity+delta
                if new_quantity < 0:
                    raise HTTPException(409,"INVENTORY_STOCK_TOO_LOW")
                con.execute("""INSERT INTO inventory_stock(item_id,location_id,quantity,updated_at)
                    VALUES(?,?,?,?) ON CONFLICT(item_id,location_id)
                    DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at""",
                    (payload.item_id,location_id,new_quantity,now))

            if payload.movement_type=="receipt":
                change(payload.to_location_id,payload.quantity)
            elif payload.movement_type=="issue":
                change(payload.from_location_id,-payload.quantity)
            elif payload.movement_type=="transfer":
                if payload.from_location_id==payload.to_location_id:
                    raise HTTPException(400,"INVENTORY_TRANSFER_SAME_LOCATION")
                change(payload.from_location_id,-payload.quantity)
                change(payload.to_location_id,payload.quantity)
            else:
                location=payload.to_location_id or payload.from_location_id
                if not location:
                    raise HTTPException(400,"INVENTORY_LOCATION_REQUIRED")
                # Adjustment is a positive correction in V1; negative corrections use an issue.
                change(location,payload.quantity)

            cur=con.execute("""INSERT INTO inventory_movements
                (item_id,movement_type,quantity,from_location_id,to_location_id,customer_id,
                 occurred_at,note,unit_sales_price_net,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (payload.item_id,payload.movement_type,payload.quantity,payload.from_location_id,
                 payload.to_location_id,payload.customer_id,occurred,payload.note,
                 item["sales_price_net"],now))
            return {"id":cur.lastrowid}

    def list_items_data(search: str | None = None):
        with connect() as con:
            rows=con.execute("""SELECT i.*,COALESCE(SUM(s.quantity),0) total_stock
                FROM inventory_items i LEFT JOIN inventory_stock s ON s.item_id=i.id
                WHERE i.active=1 GROUP BY i.id ORDER BY i.name COLLATE NOCASE""").fetchall()
            out=[]
            needle=str(search or "").strip().casefold()
            for row in rows:
                item=dict(row)
                if needle and needle not in " ".join(str(item.get(k) or "") for k in ("article_number","name","manufacturer","category","supplier","sevdesk_article_number","notes")).casefold():
                    continue
                item["stocks"]=[dict(x) for x in con.execute("""SELECT l.id location_id,l.name,l.code,COALESCE(s.quantity,0) quantity
                    FROM inventory_locations l LEFT JOIN inventory_stock s ON s.location_id=l.id AND s.item_id=?
                    WHERE l.active=1 ORDER BY l.name""",(row["id"],)).fetchall()]
                bus_stock=next((float(s["quantity"]) for s in item["stocks"] if str(s.get("code") or "").casefold()=="bus"),0.0)
                item["bus_stock"]=bus_stock
                item["minimum_bus_stock"]=float(item["minimum_stock"] or 0)
                item["needs_bus_refill"]=bus_stock < item["minimum_bus_stock"]
                item["suggested_bus_refill_quantity"]=max(0,item["minimum_bus_stock"]-bus_stock)
                item["needs_reorder"]=item["needs_bus_refill"]
                out.append(item)
            return out

    def create_movement_data(item_id: int, movement_type: str, quantity: float,
                             from_location_id: int | None = None, to_location_id: int | None = None,
                             customer_id: int | None = None, note: str | None = None):
        payload=InventoryMovementCreate(item_id=item_id,movement_type=movement_type,quantity=quantity,
            from_location_id=from_location_id,to_location_id=to_location_id,customer_id=customer_id,note=note)
        return inventory_movement_create(payload)

    @router.get("/items/{item_id}/movements")
    def inventory_item_movements(item_id: int):
        with connect() as con:
            rows=con.execute("""
                SELECT m.*,c.name AS customer_name,fl.name AS from_location,tl.name AS to_location
                FROM inventory_movements m
                LEFT JOIN customers c ON c.id=m.customer_id
                LEFT JOIN inventory_locations fl ON fl.id=m.from_location_id
                LEFT JOIN inventory_locations tl ON tl.id=m.to_location_id
                WHERE m.item_id=? ORDER BY m.occurred_at DESC,m.id DESC
            """,(item_id,)).fetchall()
            return [dict(r) for r in rows]
