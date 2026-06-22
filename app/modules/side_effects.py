from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import (
    Order, OrderStatus, OrderItem, SortingTask, SortingItem, SortingStatus,
    UserCoupon, InventoryReservation, PaymentStatus
)
from app.modules.order_state_machine import StateTransitionContext
from app.modules.inventory import InventoryService
from app.modules.pricing import CouponEngine


class SideEffectError(Exception):
    pass


class SideEffectHandler(ABC):
    def __init__(self, db: Session):
        self.db = db

    @abstractmethod
    def handle(self, ctx: StateTransitionContext) -> None:
        pass


class InventorySideEffectHandler(SideEffectHandler):
    def __init__(self, db: Session):
        super().__init__(db)
        self.inventory = InventoryService(db)

    def handle(self, ctx: StateTransitionContext) -> None:
        transition = f"{ctx.from_status.value}->{ctx.to_status.value}"
        order = ctx.order

        if transition == "created->paid":
            self._on_paid(order)
        elif transition == "paid->cancelled":
            self._on_cancelled_after_paid(order)
        elif transition == "created->cancelled":
            self._on_cancelled_before_paid(order)
        elif transition == "sorted->delivering":
            pass

    def _on_paid(self, order: Order) -> None:
        self.inventory.confirm_reservations(order.id, order.warehouse_date)

    def _on_cancelled_after_paid(self, order: Order) -> None:
        items = [
            {"product_id": it.product_id, "qty": it.qty}
            for it in order.items
        ]
        self.inventory.return_stock(items, order.warehouse_date)

    def _on_cancelled_before_paid(self, order: Order) -> None:
        self.inventory.release_reservations(order.id, order.warehouse_date)


class CouponSideEffectHandler(SideEffectHandler):
    def __init__(self, db: Session):
        super().__init__(db)
        self.coupon_engine = CouponEngine(db)

    def handle(self, ctx: StateTransitionContext) -> None:
        transition = f"{ctx.from_status.value}->{ctx.to_status.value}"
        order = ctx.order

        if transition == "created->paid":
            self._on_paid(order)
        elif transition == "paid->cancelled":
            self._on_cancelled(order)

    def _on_paid(self, order: Order) -> None:
        if order.coupon_id:
            self.coupon_engine.mark_coupon_used(order.coupon_id, order.id)

    def _on_cancelled(self, order: Order) -> None:
        if order.coupon_id:
            self.coupon_engine.unmark_coupon_used(order.coupon_id)


class PaymentSideEffectHandler(SideEffectHandler):
    def handle(self, ctx: StateTransitionContext) -> None:
        transition = f"{ctx.from_status.value}->{ctx.to_status.value}"
        order = ctx.order

        if transition == "created->paid":
            order.payment_status = PaymentStatus.PAID
            order.paid_amount = order.order_amount
        elif transition == "paid->cancelled":
            if order.paid_amount > 0:
                order.payment_status = PaymentStatus.REFUNDED
        elif transition == "created->cancelled":
            order.payment_status = PaymentStatus.UNPAID
        elif transition == "delivered->refunding":
            order.payment_status = PaymentStatus.REFUNDING
        elif transition == "picked_up->refunding":
            order.payment_status = PaymentStatus.REFUNDING
        elif transition == "completed->refunding":
            order.payment_status = PaymentStatus.REFUNDING
        elif transition == "refunding->refunded":
            order.payment_status = PaymentStatus.REFUNDED


class SortingSideEffectHandler(SideEffectHandler):
    def handle(self, ctx: StateTransitionContext) -> None:
        transition = f"{ctx.from_status.value}->{ctx.to_status.value}"
        order = ctx.order

        if transition == "paid->cutoff":
            self._on_cutoff(order)
        elif transition == "cutoff->sorting":
            self._on_start_sorting(ctx)
        elif transition == "sorting->sorted":
            self._on_complete_sorting(ctx)

    def _on_cutoff(self, order: Order) -> None:
        task = SortingTask(
            warehouse_date=order.warehouse_date,
            order_id=order.id,
            leader_id=order.leader_id,
            status=SortingStatus.PENDING,
            total_items=sum(it.qty for it in order.items),
            sorted_items=0
        )
        self.db.add(task)
        self.db.flush()

        for item in order.items:
            si = SortingItem(
                task_id=task.id,
                product_id=item.product_id,
                product_name=item.product_name,
                required_qty=item.qty,
                sorted_qty=0,
                is_complete=False
            )
            self.db.add(si)

    def _on_start_sorting(self, ctx: StateTransitionContext) -> None:
        task_id = ctx.extra.get("task_id")
        if not task_id:
            return
        task = self.db.query(SortingTask).filter(SortingTask.id == task_id).first()
        if task:
            task.status = SortingStatus.IN_PROGRESS
            task.started_at = datetime.utcnow()
            task.operator = ctx.operator

    def _on_complete_sorting(self, ctx: StateTransitionContext) -> None:
        task_id = ctx.extra.get("task_id")
        if not task_id:
            return
        task = self.db.query(SortingTask).filter(SortingTask.id == task_id).first()
        if task:
            task.status = SortingStatus.COMPLETED
            task.completed_at = datetime.utcnow()

            from app.modules.inventory import InventoryService
            inventory = InventoryService(self.db)
            for item in task.items:
                inventory.mark_sorted(item.product_id, task.warehouse_date, item.required_qty)


class DeliverySideEffectHandler(SideEffectHandler):
    def handle(self, ctx: StateTransitionContext) -> None:
        pass


class ReservationExpirySideEffectHandler:
    def __init__(self, db: Session):
        self.db = db
        self.inventory = InventoryService(db)

    def check_and_cancel_expired(self, order_id: int) -> None:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order or order.status != OrderStatus.CREATED:
            return

        now = datetime.utcnow()
        first_reservation = self.db.query(InventoryReservation).filter(
            InventoryReservation.order_id == order_id,
            InventoryReservation.is_active == True
        ).order_by(InventoryReservation.expires_at.asc()).first()

        if first_reservation and first_reservation.expires_at and first_reservation.expires_at < now:
            expired_minutes = int((now - first_reservation.expires_at).total_seconds() / 60)
            raise SideEffectError(
                f"订单已过期（超时{expired_minutes}分钟），无法支付，已自动取消并释放锁定库存"
            )
