from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import Inventory, InventoryReservation, Product, Order
from typing import Optional, List, Dict, Tuple


class InventoryError(Exception):
    pass


class InventoryInsufficientError(InventoryError):
    pass


class InventoryService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_inventory(self, product_id: int, warehouse_date: str) -> Inventory:
        inventory = self.db.query(Inventory).filter(
            Inventory.product_id == product_id,
            Inventory.warehouse_date == warehouse_date
        ).first()

        if not inventory:
            product = self.db.query(Product).filter(Product.id == product_id).first()
            if not product:
                raise InventoryError(f"产品不存在: {product_id}")

            daily_stock = product.daily_limit if product.daily_limit else product.stock_total
            inventory = Inventory(
                product_id=product_id,
                warehouse_date=warehouse_date,
                available_qty=daily_stock,
                reserved_qty=0,
                sorted_qty=0
            )
            self.db.add(inventory)
            self.db.flush()

        return inventory

    def get_available_qty(self, product_id: int, warehouse_date: str) -> int:
        inventory = self.db.query(Inventory).filter(
            Inventory.product_id == product_id,
            Inventory.warehouse_date == warehouse_date
        ).first()

        if not inventory:
            product = self.db.query(Product).filter(Product.id == product_id).first()
            return product.daily_limit if product and product.daily_limit else (product.stock_total if product else 0)

        return max(0, inventory.available_qty - inventory.reserved_qty)

    def check_stock(self, items: List[Dict], warehouse_date: str) -> Tuple[bool, List[str]]:
        errors = []
        for item in items:
            product_id = item["product_id"]
            qty = item["qty"]
            available = self.get_available_qty(product_id, warehouse_date)
            if qty > available:
                product = self.db.query(Product).filter(Product.id == product_id).first()
                product_name = product.name if product else f"产品{product_id}"
                errors.append(f"{product_name}: 需求{qty}, 可用{available}")

        return (len(errors) == 0), errors

    def reserve(self, order_id: int, items: List[Dict], warehouse_date: str,
                expire_minutes: int = 30) -> List[InventoryReservation]:
        self._clean_expired_reservations(warehouse_date)

        success, errors = self.check_stock(items, warehouse_date)
        if not success:
            raise InventoryInsufficientError("库存不足: " + "; ".join(errors))

        reservations = []
        expires_at = datetime.utcnow() + timedelta(minutes=expire_minutes)

        for item in items:
            product_id = item["product_id"]
            qty = item["qty"]

            inventory = self.get_or_create_inventory(product_id, warehouse_date)
            inventory.reserved_qty += qty

            reservation = InventoryReservation(
                product_id=product_id,
                order_id=order_id,
                warehouse_date=warehouse_date,
                qty=qty,
                expires_at=expires_at,
                is_active=True
            )
            self.db.add(reservation)
            reservations.append(reservation)

        self.db.flush()
        return reservations

    def confirm_reservations(self, order_id: int, warehouse_date: str) -> None:
        reservations = self.db.query(InventoryReservation).filter(
            InventoryReservation.order_id == order_id,
            InventoryReservation.warehouse_date == warehouse_date,
            InventoryReservation.is_active == True
        ).all()

        for reservation in reservations:
            inventory = self.get_or_create_inventory(reservation.product_id, warehouse_date)
            actual_decrease = min(reservation.qty, inventory.available_qty)
            inventory.available_qty -= actual_decrease
            inventory.reserved_qty = max(0, inventory.reserved_qty - actual_decrease)
            reservation.is_active = False

        self.db.flush()

    def release_reservations(self, order_id: int, warehouse_date: str) -> None:
        reservations = self.db.query(InventoryReservation).filter(
            InventoryReservation.order_id == order_id,
            InventoryReservation.warehouse_date == warehouse_date,
            InventoryReservation.is_active == True
        ).all()

        for reservation in reservations:
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == reservation.product_id,
                Inventory.warehouse_date == warehouse_date
            ).first()

            if inventory:
                inventory.reserved_qty = max(0, inventory.reserved_qty - reservation.qty)
                reservation.is_active = False

        self.db.flush()

    def _clean_expired_reservations(self, warehouse_date: str) -> None:
        now = datetime.utcnow()
        expired = self.db.query(InventoryReservation).filter(
            InventoryReservation.warehouse_date == warehouse_date,
            InventoryReservation.is_active == True,
            InventoryReservation.expires_at < now
        ).all()

        for reservation in expired:
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == reservation.product_id,
                Inventory.warehouse_date == warehouse_date
            ).first()

            if inventory:
                inventory.reserved_qty = max(0, inventory.reserved_qty - reservation.qty)
                reservation.is_active = False

        self.db.flush()

    def restock(self, product_id: int, warehouse_date: str, qty: int) -> Inventory:
        inventory = self.get_or_create_inventory(product_id, warehouse_date)
        inventory.available_qty += qty
        self.db.flush()
        return inventory

    def mark_sorted(self, product_id: int, warehouse_date: str, qty: int) -> None:
        inventory = self.get_or_create_inventory(product_id, warehouse_date)
        inventory.sorted_qty = min(inventory.sorted_qty + qty, inventory.available_qty + inventory.sorted_qty)
        self.db.flush()

    def return_stock(self, items: List[Dict], warehouse_date: str) -> None:
        for item in items:
            product_id = item["product_id"]
            qty = item["qty"]
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.warehouse_date == warehouse_date
            ).first()

            if inventory:
                inventory.available_qty += qty
            else:
                self.db.add(Inventory(
                    product_id=product_id,
                    warehouse_date=warehouse_date,
                    available_qty=qty,
                    reserved_qty=0,
                    sorted_qty=0
                ))

        self.db.flush()

    def get_inventory_summary(self, warehouse_date: str) -> List[Dict]:
        inventories = self.db.query(Inventory).filter(
            Inventory.warehouse_date == warehouse_date
        ).all()

        result = []
        for inv in inventories:
            product = self.db.query(Product).filter(Product.id == inv.product_id).first()
            result.append({
                "product_id": inv.product_id,
                "product_name": product.name if product else "未知",
                "available": inv.available_qty,
                "reserved": inv.reserved_qty,
                "sorted": inv.sorted_qty,
                "usable": max(0, inv.available_qty - inv.reserved_qty)
            })

        return result
