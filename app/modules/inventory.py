from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select, update, and_
from sqlalchemy.exc import IntegrityError
from app.models import Inventory, InventoryReservation, Product, Order, OrderStatus, PaymentStatus
from typing import Optional, List, Dict, Tuple


class InventoryError(Exception):
    pass


class InventoryInsufficientError(InventoryError):
    pass


class InventoryService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_inventory(self, product_id: int, warehouse_date: str,
                                lock: bool = False) -> Inventory:
        query = self.db.query(Inventory).filter(
            Inventory.product_id == product_id,
            Inventory.warehouse_date == warehouse_date
        )
        if lock:
            query = query.with_for_update()
        inventories = query.all()

        if len(inventories) == 1:
            return inventories[0]
        elif len(inventories) > 1:
            return self._merge_duplicate_inventories(product_id, warehouse_date, inventories, lock)

        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise InventoryError(f"产品不存在: {product_id}")

        daily_stock = product.daily_limit if product.daily_limit else product.stock_total

        try:
            with self.db.begin_nested():
                inventory = Inventory(
                    product_id=product_id,
                    warehouse_date=warehouse_date,
                    available_qty=daily_stock,
                    reserved_qty=0,
                    sorted_qty=0
                )
                self.db.add(inventory)
                self.db.flush()
        except IntegrityError:
            query2 = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.warehouse_date == warehouse_date
            )
            if lock:
                query2 = query2.with_for_update()
            invs2 = query2.all()
            if len(invs2) == 0:
                raise InventoryError(
                    f"库存记录创建失败 (product={product_id}, date={warehouse_date})"
                )
            if len(invs2) > 1:
                return self._merge_duplicate_inventories(product_id, warehouse_date, invs2, lock)
            return invs2[0]

        if lock:
            locked = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.warehouse_date == warehouse_date
            ).with_for_update().all()
            if len(locked) > 1:
                return self._merge_duplicate_inventories(product_id, warehouse_date, locked, lock)
            return locked[0] if locked else inventory

        return inventory

    def _merge_duplicate_inventories(self, product_id: int, warehouse_date: str,
                                     inventories: List[Inventory], lock: bool) -> Inventory:
        total_available = sum(inv.available_qty for inv in inventories)
        total_reserved = sum(inv.reserved_qty for inv in inventories)
        total_sorted = sum(inv.sorted_qty for inv in inventories)

        keep = inventories[0]
        for inv in inventories[1:]:
            self.db.delete(inv)

        keep.available_qty = total_available
        keep.reserved_qty = total_reserved
        keep.sorted_qty = total_sorted
        self.db.flush()

        if lock:
            keep = self.db.query(Inventory).filter(
                Inventory.id == keep.id
            ).with_for_update().first()

        return keep

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

        reservations = []
        expires_at = datetime.utcnow() + timedelta(minutes=expire_minutes)

        sorted_items = sorted(items, key=lambda x: x["product_id"])

        for item in sorted_items:
            product_id = item["product_id"]
            qty = item["qty"]

            inventory = self.get_or_create_inventory(product_id, warehouse_date, lock=False)

            stmt = (
                update(Inventory)
                .where(and_(
                    Inventory.id == inventory.id,
                    (Inventory.available_qty - Inventory.reserved_qty) >= qty
                ))
                .values(reserved_qty=Inventory.reserved_qty + qty)
                .execution_options(synchronize_session="fetch")
            )
            result = self.db.execute(stmt)

            if result.rowcount == 0:
                product = self.db.query(Product).filter(Product.id == product_id).first()
                product_name = product.name if product else f"产品{product_id}"
                self.db.refresh(inventory)
                usable = max(0, inventory.available_qty - inventory.reserved_qty)
                raise InventoryInsufficientError(
                    f"库存不足: {product_name}: 需求{qty}, 可用{usable}"
                )

            self.db.refresh(inventory)

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

        sorted_reservations = sorted(reservations, key=lambda r: r.product_id)

        for reservation in sorted_reservations:
            inventory = self.get_or_create_inventory(reservation.product_id, warehouse_date, lock=False)

            actual_decrease = min(reservation.qty, inventory.available_qty, inventory.reserved_qty)
            if actual_decrease <= 0:
                reservation.is_active = False
                continue

            stmt = (
                update(Inventory)
                .where(and_(
                    Inventory.id == inventory.id,
                    Inventory.available_qty >= actual_decrease,
                    Inventory.reserved_qty >= actual_decrease
                ))
                .values(
                    available_qty=Inventory.available_qty - actual_decrease,
                    reserved_qty=Inventory.reserved_qty - actual_decrease
                )
                .execution_options(synchronize_session="fetch")
            )
            result = self.db.execute(stmt)

            if result.rowcount > 0:
                reservation.is_active = False
            else:
                self.db.refresh(inventory)
                actual = min(reservation.qty, inventory.available_qty, inventory.reserved_qty)
                if actual > 0:
                    stmt2 = (
                        update(Inventory)
                        .where(Inventory.id == inventory.id)
                        .values(
                            available_qty=Inventory.available_qty - actual,
                            reserved_qty=Inventory.reserved_qty - actual
                        )
                        .execution_options(synchronize_session="fetch")
                    )
                    self.db.execute(stmt2)
                reservation.is_active = False

        self.db.flush()

    def release_reservations(self, order_id: int, warehouse_date: str) -> None:
        reservations = self.db.query(InventoryReservation).filter(
            InventoryReservation.order_id == order_id,
            InventoryReservation.warehouse_date == warehouse_date,
            InventoryReservation.is_active == True
        ).all()

        sorted_reservations = sorted(reservations, key=lambda r: r.product_id)

        for reservation in sorted_reservations:
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == reservation.product_id,
                Inventory.warehouse_date == warehouse_date
            ).first()

            if not inventory:
                reservation.is_active = False
                continue

            qty_to_release = min(reservation.qty, inventory.reserved_qty)
            if qty_to_release > 0:
                stmt = (
                    update(Inventory)
                    .where(and_(
                        Inventory.id == inventory.id,
                        Inventory.reserved_qty >= qty_to_release
                    ))
                    .values(reserved_qty=Inventory.reserved_qty - qty_to_release)
                    .execution_options(synchronize_session="fetch")
                )
                self.db.execute(stmt)

            reservation.is_active = False

        self.db.flush()

    def _clean_expired_reservations(self, warehouse_date: str) -> int:
        now = datetime.utcnow()
        expired = self.db.query(InventoryReservation).filter(
            InventoryReservation.warehouse_date == warehouse_date,
            InventoryReservation.is_active == True,
            InventoryReservation.expires_at < now
        ).all()

        count = 0
        sorted_expired = sorted(expired, key=lambda r: r.product_id)

        for reservation in sorted_expired:
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == reservation.product_id,
                Inventory.warehouse_date == warehouse_date
            ).first()

            if not inventory:
                reservation.is_active = False
                count += 1
                continue

            qty_to_release = min(reservation.qty, inventory.reserved_qty)
            if qty_to_release > 0:
                stmt = (
                    update(Inventory)
                    .where(and_(
                        Inventory.id == inventory.id,
                        Inventory.reserved_qty >= qty_to_release
                    ))
                    .values(reserved_qty=Inventory.reserved_qty - qty_to_release)
                    .execution_options(synchronize_session="fetch")
                )
                self.db.execute(stmt)

            reservation.is_active = False
            count += 1

        self.db.flush()
        return count

    def clean_all_expired_reservations(self) -> Dict:
        now = datetime.utcnow()
        from app.models import OrderStatus, PaymentStatus

        expired_order_ids_stmt = (
            select(InventoryReservation.order_id)
            .where(
                InventoryReservation.is_active == True,
                InventoryReservation.expires_at < now,
                InventoryReservation.order_id.isnot(None)
            )
            .distinct()
        )
        expired_order_ids = [r[0] for r in self.db.execute(expired_order_ids_stmt).all()]

        cancelled_count = 0
        for oid in expired_order_ids:
            try:
                with self.db.begin_nested():
                    order = self.db.query(Order).filter(
                        Order.id == oid,
                        Order.status == OrderStatus.CREATED,
                        Order.payment_status == PaymentStatus.UNPAID
                    ).first()
                    if not order:
                        continue
                    from app.modules.order_service import OrderService
                    order_service = OrderService(self.db)
                    order_service.cancel_order(
                        order.id,
                        operator="system",
                        reason="订单超时未支付，自动取消"
                    )
                    cancelled_count += 1
            except Exception:
                continue

        cleaned_count = 0
        all_warehouse_dates = self.db.query(Inventory.warehouse_date).distinct().all()
        for (wd,) in all_warehouse_dates:
            cleaned_count += self._clean_expired_reservations(wd)

        dangling = self.db.query(InventoryReservation).filter(
            InventoryReservation.is_active == True,
            InventoryReservation.expires_at < now
        ).filter(
            ~InventoryReservation.order_id.in_(
                select(Order.id).where(Order.status == OrderStatus.CREATED)
            )
        ).all()
        if dangling:
            sorted_dangling = sorted(dangling, key=lambda r: (r.product_id, r.id))
            for res in sorted_dangling:
                inv = self.db.query(Inventory).filter(
                    Inventory.product_id == res.product_id,
                    Inventory.warehouse_date == res.warehouse_date
                ).first()
                if inv:
                    release = min(res.qty, inv.reserved_qty)
                    if release > 0:
                        stmt = (
                            update(Inventory)
                            .where(and_(
                                Inventory.id == inv.id,
                                Inventory.reserved_qty >= release
                            ))
                            .values(reserved_qty=Inventory.reserved_qty - release)
                            .execution_options(synchronize_session="fetch")
                        )
                        self.db.execute(stmt)
                res.is_active = False
                cleaned_count += 1

        self.db.flush()
        return {
            "cancelled_orders": cancelled_count,
            "cleaned_reservations": cleaned_count
        }

    def restock(self, product_id: int, warehouse_date: str, qty: int) -> Inventory:
        inventory = self.get_or_create_inventory(product_id, warehouse_date, lock=False)

        stmt = (
            update(Inventory)
            .where(Inventory.id == inventory.id)
            .values(available_qty=Inventory.available_qty + qty)
            .execution_options(synchronize_session="fetch")
        )
        self.db.execute(stmt)
        self.db.refresh(inventory)
        self.db.flush()
        return inventory

    def mark_sorted(self, product_id: int, warehouse_date: str, qty: int) -> None:
        inventory = self.get_or_create_inventory(product_id, warehouse_date, lock=False)

        new_sorted = min(inventory.sorted_qty + qty, inventory.available_qty + inventory.sorted_qty)
        actual_inc = max(0, new_sorted - inventory.sorted_qty)

        if actual_inc > 0:
            stmt = (
                update(Inventory)
                .where(Inventory.id == inventory.id)
                .values(sorted_qty=Inventory.sorted_qty + actual_inc)
                .execution_options(synchronize_session="fetch")
            )
            self.db.execute(stmt)

        self.db.flush()

    def return_stock(self, items: List[Dict], warehouse_date: str) -> None:
        sorted_items = sorted(items, key=lambda x: x["product_id"])
        for item in sorted_items:
            product_id = item["product_id"]
            qty = item["qty"]
            inventory = self.db.query(Inventory).filter(
                Inventory.product_id == product_id,
                Inventory.warehouse_date == warehouse_date
            ).first()

            if inventory:
                stmt = (
                    update(Inventory)
                    .where(Inventory.id == inventory.id)
                    .values(available_qty=Inventory.available_qty + qty)
                    .execution_options(synchronize_session="fetch")
                )
                self.db.execute(stmt)
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
