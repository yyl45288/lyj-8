from datetime import datetime
from sqlalchemy.orm import Session
from app.models import (
    Order, OrderItem, OrderStatusLog, OrderStatus, PaymentStatus,
    Product, User, Leader, SortingTask, SortingItem, SortingStatus,
    UserCoupon, InventoryReservation, AfterSale, AfterSaleItem,
    AfterSaleStatus
)
from app.modules.inventory import InventoryService, InventoryInsufficientError
from app.modules.pricing import PricingService, CouponEngine
from typing import List, Dict, Optional, Tuple
import uuid
import random


class OrderStateError(Exception):
    pass


STATE_TRANSITIONS = {
    OrderStatus.CREATED: [OrderStatus.PAID, OrderStatus.CANCELLED],
    OrderStatus.PAID: [OrderStatus.CUTOFF, OrderStatus.CANCELLED, OrderStatus.REFUNDING],
    OrderStatus.CUTOFF: [OrderStatus.SORTING, OrderStatus.REFUNDING],
    OrderStatus.SORTING: [OrderStatus.SORTED],
    OrderStatus.SORTED: [OrderStatus.DELIVERING],
    OrderStatus.DELIVERING: [OrderStatus.DELIVERED],
    OrderStatus.DELIVERED: [OrderStatus.PICKED_UP],
    OrderStatus.PICKED_UP: [OrderStatus.COMPLETED],
    OrderStatus.REFUNDING: [OrderStatus.REFUNDED, OrderStatus.PAID],
    OrderStatus.REFUNDED: [],
    OrderStatus.CANCELLED: [],
    OrderStatus.COMPLETED: [],
}


class OrderService:
    def __init__(self, db: Session):
        self.db = db
        self.inventory = InventoryService(db)
        self.pricing = PricingService(db)
        self.coupon_engine = CouponEngine(db)

    def _generate_order_no(self) -> str:
        now = datetime.now()
        date_part = now.strftime("%Y%m%d%H%M%S")
        random_part = str(random.randint(1000, 9999))
        return f"CG{date_part}{random_part}"

    def _log_status_change(self, order: Order, from_status: Optional[str], to_status: str,
                           operator: str = "system", remark: str = None):
        log = OrderStatusLog(
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            operator=operator,
            remark=remark
        )
        self.db.add(log)

    def _validate_transition(self, current: OrderStatus, target: OrderStatus) -> bool:
        allowed = STATE_TRANSITIONS.get(current, [])
        return target in allowed

    def create_order(self, user_id: int, leader_id: int, warehouse_date: str,
                     items: List[Dict], user_coupon_id: int = None,
                     remark: str = None) -> Tuple[Order, Dict]:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise OrderStateError(f"用户不存在: {user_id}")

        leader = self.db.query(Leader).filter(Leader.id == leader_id).first()
        if not leader:
            raise OrderStateError(f"团长不存在: {leader_id}")

        for it in items:
            product = self.db.query(Product).filter(Product.id == it["product_id"]).first()
            if not product:
                raise OrderStateError(f"商品不存在: {it['product_id']}")
            if not product.is_active:
                raise OrderStateError(f"商品已下架: {product.name}")
            it["product_name"] = product.name
            it["unit_price"] = product.price

        inventory_items = [
            {"product_id": it["product_id"], "qty": it["qty"]}
            for it in items
        ]

        price_result = self.pricing.calculate_price(
            items=items,
            user_id=user_id,
            user_coupon_id=user_coupon_id
        )

        order_no = self._generate_order_no()
        order = Order(
            order_no=order_no,
            user_id=user_id,
            leader_id=leader_id,
            warehouse_date=warehouse_date,
            status=OrderStatus.CREATED,
            payment_status=PaymentStatus.UNPAID,
            goods_amount=price_result["goods_amount"],
            promotion_discount=price_result["promotion_discount"],
            coupon_discount=price_result["coupon_discount"],
            shipping_fee=price_result["shipping_fee"],
            order_amount=price_result["order_amount"],
            paid_amount=0,
            coupon_id=user_coupon_id,
            remark=remark
        )
        self.db.add(order)
        self.db.flush()

        for it in price_result["items"]:
            order_item = OrderItem(
                order_id=order.id,
                product_id=it["product_id"],
                product_name=it["product_name"],
                unit_price=it["unit_price"],
                actual_price=it["actual_price"],
                qty=it["qty"],
                subtotal=it["subtotal"],
                discount_amount=it["discount_amount"],
                promotion_note=it["promotion_note"]
            )
            self.db.add(order_item)

        self.inventory.reserve(order.id, inventory_items, warehouse_date)

        self._log_status_change(order, None, OrderStatus.CREATED.value,
                                remark="订单创建")

        self.db.flush()
        return order, price_result

    def pay_order(self, order_id: int) -> Order:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")

        if not self._validate_transition(order.status, OrderStatus.PAID):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许支付")

        self.inventory.confirm_reservations(order.id, order.warehouse_date)

        if order.coupon_id:
            self.coupon_engine.mark_coupon_used(order.coupon_id, order.id)

        old_status = order.status.value
        order.status = OrderStatus.PAID
        order.payment_status = PaymentStatus.PAID
        order.paid_amount = order.order_amount
        order.paid_at = datetime.utcnow()

        self._log_status_change(order, old_status, OrderStatus.PAID.value,
                                remark="用户支付成功")

        self.db.flush()
        return order

    def cancel_order(self, order_id: int, operator: str = "user",
                     reason: str = None) -> Order:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")

        if not self._validate_transition(order.status, OrderStatus.CANCELLED):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许取消")

        if order.status == OrderStatus.PAID:
            self.inventory.return_stock(
                [{"product_id": it.product_id, "qty": it.qty} for it in order.items],
                order.warehouse_date
            )
            if order.coupon_id:
                self.coupon_engine.unmark_coupon_used(order.coupon_id)
        else:
            self.inventory.release_reservations(order.id, order.warehouse_date)

        old_status = order.status.value
        order.status = OrderStatus.CANCELLED
        order.payment_status = PaymentStatus.REFUNDING if order.paid_amount > 0 else PaymentStatus.UNPAID
        order.cancelled_at = datetime.utcnow()

        self._log_status_change(order, old_status, OrderStatus.CANCELLED.value,
                                operator=operator,
                                remark=reason or "订单取消")

        if order.paid_amount > 0:
            order.payment_status = PaymentStatus.REFUNDED
            self._log_status_change(order, OrderStatus.CANCELLED.value, "refunded",
                                    operator="system",
                                    remark="退款完成")

        self.db.flush()
        return order

    def cutoff_orders(self, warehouse_date: str, operator: str = "system") -> List[Order]:
        orders = self.db.query(Order).filter(
            Order.warehouse_date == warehouse_date,
            Order.status == OrderStatus.PAID
        ).all()

        processed = []
        for order in orders:
            if not self._validate_transition(order.status, OrderStatus.CUTOFF):
                continue

            old_status = order.status.value
            order.status = OrderStatus.CUTOFF
            order.cutoff_at = datetime.utcnow()

            self._create_sorting_task(order)

            self._log_status_change(order, old_status, OrderStatus.CUTOFF.value,
                                    operator=operator,
                                    remark="截单完成")
            processed.append(order)

        self.db.flush()
        return processed

    def _create_sorting_task(self, order: Order):
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

    def start_sorting(self, task_id: int, operator: str = "sorter") -> SortingTask:
        task = self.db.query(SortingTask).filter(SortingTask.id == task_id).first()
        if not task:
            raise OrderStateError(f"分拣任务不存在: {task_id}")
        if task.status != SortingStatus.PENDING:
            raise OrderStateError(f"分拣任务状态 {task.status.value} 不允许开始")

        order = self.db.query(Order).filter(Order.id == task.order_id).first()
        if not self._validate_transition(order.status, OrderStatus.SORTING):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许分拣")

        old_order_status = order.status.value
        order.status = OrderStatus.SORTING

        task.status = SortingStatus.IN_PROGRESS
        task.started_at = datetime.utcnow()
        task.operator = operator

        self._log_status_change(order, old_order_status, OrderStatus.SORTING.value,
                                operator=operator, remark="开始分拣")

        self.db.flush()
        return task

    def sort_item(self, task_id: int, sorting_item_id: int, qty: int,
                  operator: str = "sorter") -> SortingItem:
        task = self.db.query(SortingTask).filter(SortingTask.id == task_id).first()
        if not task or task.status != SortingStatus.IN_PROGRESS:
            raise OrderStateError("分拣任务未开始或已完成")

        item = self.db.query(SortingItem).filter(SortingItem.id == sorting_item_id).first()
        if not item or item.task_id != task_id:
            raise OrderStateError(f"分拣明细不存在: {sorting_item_id}")

        item.sorted_qty = min(item.sorted_qty + qty, item.required_qty)
        if item.sorted_qty >= item.required_qty:
            item.is_complete = True

        task.sorted_items = sum(i.sorted_qty for i in task.items)

        self.db.flush()
        return item

    def complete_sorting(self, task_id: int, operator: str = "sorter") -> SortingTask:
        task = self.db.query(SortingTask).filter(SortingTask.id == task_id).first()
        if not task:
            raise OrderStateError(f"分拣任务不存在: {task_id}")
        if task.status != SortingStatus.IN_PROGRESS:
            raise OrderStateError(f"分拣任务状态 {task.status.value} 不允许完成")

        for item in task.items:
            if not item.is_complete:
                raise OrderStateError(f"商品 {item.product_name} 分拣未完成")

        order = self.db.query(Order).filter(Order.id == task.order_id).first()
        if not self._validate_transition(order.status, OrderStatus.SORTED):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许完成分拣")

        old_order_status = order.status.value
        order.status = OrderStatus.SORTED

        task.status = SortingStatus.COMPLETED
        task.completed_at = datetime.utcnow()

        for item in task.items:
            self.inventory.mark_sorted(item.product_id, task.warehouse_date, item.required_qty)

        self._log_status_change(order, old_order_status, OrderStatus.SORTED.value,
                                operator=operator, remark="分拣完成")

        self.db.flush()
        return task

    def mark_delivering(self, order_id: int, operator: str = "dispatcher") -> Order:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")
        if not self._validate_transition(order.status, OrderStatus.DELIVERING):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许配送")

        old_status = order.status.value
        order.status = OrderStatus.DELIVERING

        self._log_status_change(order, old_status, OrderStatus.DELIVERING.value,
                                operator=operator, remark="开始配送")

        self.db.flush()
        return order

    def mark_delivered(self, order_id: int, operator: str = "driver") -> Order:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")
        if not self._validate_transition(order.status, OrderStatus.DELIVERED):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许签收")

        old_status = order.status.value
        order.status = OrderStatus.DELIVERED
        order.delivered_at = datetime.utcnow()

        self._log_status_change(order, old_status, OrderStatus.DELIVERED.value,
                                operator=operator, remark="团长签收")

        self.db.flush()
        return order

    def mark_picked_up(self, order_id: int, operator: str = "leader") -> Order:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")
        if not self._validate_transition(order.status, OrderStatus.PICKED_UP):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许提货")

        old_status = order.status.value
        order.status = OrderStatus.PICKED_UP
        order.picked_up_at = datetime.utcnow()

        self._log_status_change(order, old_status, OrderStatus.PICKED_UP.value,
                                operator=operator, remark="用户提货")

        self.db.flush()
        return order

    def complete_order(self, order_id: int, operator: str = "system") -> Order:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")
        if not self._validate_transition(order.status, OrderStatus.COMPLETED):
            raise OrderStateError(f"订单状态 {order.status.value} 不允许完成")

        old_status = order.status.value
        order.status = OrderStatus.COMPLETED
        order.completed_at = datetime.utcnow()

        self._log_status_change(order, old_status, OrderStatus.COMPLETED.value,
                                operator=operator, remark="订单完成")

        self.db.flush()
        return order

    def apply_after_sale(self, order_id: int, user_id: int, reason: str,
                         items: List[Dict], description: str = None) -> AfterSale:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            raise OrderStateError(f"订单不存在: {order_id}")
        if order.user_id != user_id:
            raise OrderStateError("订单不属于该用户")
        if order.status not in [OrderStatus.DELIVERED, OrderStatus.PICKED_UP, OrderStatus.COMPLETED]:
            raise OrderStateError(f"订单状态 {order.status.value} 不允许售后")

        existing = self.db.query(AfterSale).filter(
            AfterSale.order_id == order_id,
            AfterSale.status.in_([
                AfterSaleStatus.SUBMITTED,
                AfterSaleStatus.APPROVED,
                AfterSaleStatus.REFUNDING
            ])
        ).first()
        if existing:
            raise OrderStateError("已有处理中的售后申请")

        total_refund = 0.0
        after_sale_no = f"AS{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"

        after_sale = AfterSale(
            after_sale_no=after_sale_no,
            user_id=user_id,
            order_id=order_id,
            status=AfterSaleStatus.SUBMITTED,
            refund_amount=0,
            reason=reason,
            description=description
        )
        self.db.add(after_sale)
        self.db.flush()

        for req in items:
            order_item = self.db.query(OrderItem).filter(
                OrderItem.id == req["order_item_id"],
                OrderItem.order_id == order_id
            ).first()
            if not order_item:
                continue

            qty = min(req["qty"], order_item.qty)
            refund_per_unit = (order_item.subtotal - order_item.discount_amount) / order_item.qty
            item_refund = round(refund_per_unit * qty, 2)

            asi = AfterSaleItem(
                after_sale_id=after_sale.id,
                order_item_id=order_item.id,
                product_name=order_item.product_name,
                qty=qty,
                refund_amount=item_refund
            )
            self.db.add(asi)
            total_refund += item_refund

        after_sale.refund_amount = round(min(total_refund, order.paid_amount), 2)

        if not self._validate_transition(order.status, OrderStatus.REFUNDING):
            pass
        else:
            old_status = order.status.value
            order.status = OrderStatus.REFUNDING
            order.payment_status = PaymentStatus.REFUNDING
            self._log_status_change(order, old_status, OrderStatus.REFUNDING.value,
                                    remark=f"售后申请: {after_sale_no}")

        self.db.flush()
        return after_sale

    def approve_after_sale(self, after_sale_id: int, operator: str = "cs") -> AfterSale:
        after_sale = self.db.query(AfterSale).filter(AfterSale.id == after_sale_id).first()
        if not after_sale:
            raise OrderStateError(f"售后申请不存在: {after_sale_id}")
        if after_sale.status != AfterSaleStatus.SUBMITTED:
            raise OrderStateError(f"售后状态 {after_sale.status.value} 不允许审批")

        after_sale.status = AfterSaleStatus.APPROVED
        after_sale.approved_at = datetime.utcnow()
        after_sale.operator = operator

        order = self.db.query(Order).filter(Order.id == after_sale.order_id).first()
        if order.status != OrderStatus.REFUNDING:
            old_status = order.status.value
            order.status = OrderStatus.REFUNDING
            order.payment_status = PaymentStatus.REFUNDING
            self._log_status_change(order, old_status, OrderStatus.REFUNDING.value,
                                    operator=operator,
                                    remark=f"售后审批通过: {after_sale.after_sale_no}")

        self.db.flush()
        return after_sale

    def reject_after_sale(self, after_sale_id: int, reject_reason: str,
                          operator: str = "cs") -> AfterSale:
        after_sale = self.db.query(AfterSale).filter(AfterSale.id == after_sale_id).first()
        if not after_sale:
            raise OrderStateError(f"售后申请不存在: {after_sale_id}")
        if after_sale.status != AfterSaleStatus.SUBMITTED:
            raise OrderStateError(f"售后状态 {after_sale.status.value} 不允许审批")

        after_sale.status = AfterSaleStatus.REJECTED
        after_sale.rejected_at = datetime.utcnow()
        after_sale.operator = operator
        after_sale.reject_reason = reject_reason

        order = self.db.query(Order).filter(Order.id == after_sale.order_id).first()
        if order.status == OrderStatus.REFUNDING:
            order.status = OrderStatus.COMPLETED if order.picked_up_at else OrderStatus.DELIVERED
            order.payment_status = PaymentStatus.PAID

        self.db.flush()
        return after_sale

    def execute_refund(self, after_sale_id: int, operator: str = "finance") -> AfterSale:
        after_sale = self.db.query(AfterSale).filter(AfterSale.id == after_sale_id).first()
        if not after_sale:
            raise OrderStateError(f"售后申请不存在: {after_sale_id}")
        if after_sale.status != AfterSaleStatus.APPROVED:
            raise OrderStateError(f"售后状态 {after_sale.status.value} 不允许退款")

        after_sale.status = AfterSaleStatus.REFUNDED
        after_sale.refunded_at = datetime.utcnow()

        order = self.db.query(Order).filter(Order.id == after_sale.order_id).first()
        old_status = order.status.value
        order.status = OrderStatus.REFUNDED
        order.payment_status = PaymentStatus.REFUNDED
        self._log_status_change(order, old_status, OrderStatus.REFUNDED.value,
                                operator=operator,
                                remark=f"售后退款完成: {after_sale.after_sale_no}, 金额¥{after_sale.refund_amount}")

        after_sale.status = AfterSaleStatus.CLOSED
        after_sale.closed_at = datetime.utcnow()

        self.db.flush()
        return after_sale

    def get_order_details(self, order_id: int) -> Dict:
        order = self.db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return None

        return {
            "id": order.id,
            "order_no": order.order_no,
            "status": order.status.value,
            "status_text": self._status_text(order.status),
            "payment_status": order.payment_status.value,
            "warehouse_date": order.warehouse_date,
            "user": {"id": order.user.id, "name": order.user.name, "phone": order.user.phone},
            "leader": {"id": order.leader.id, "name": order.leader.name,
                       "phone": order.leader.phone, "address": order.leader.pickup_address},
            "items": [
                {
                    "id": it.id,
                    "product_id": it.product_id,
                    "product_name": it.product_name,
                    "unit_price": it.unit_price,
                    "actual_price": it.actual_price,
                    "qty": it.qty,
                    "subtotal": it.subtotal,
                    "discount_amount": it.discount_amount,
                    "promotion_note": it.promotion_note
                }
                for it in order.items
            ],
            "amounts": {
                "goods_amount": order.goods_amount,
                "promotion_discount": order.promotion_discount,
                "coupon_discount": order.coupon_discount,
                "shipping_fee": order.shipping_fee,
                "order_amount": order.order_amount,
                "paid_amount": order.paid_amount
            },
            "remark": order.remark,
            "timestamps": {
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "cutoff_at": order.cutoff_at.isoformat() if order.cutoff_at else None,
                "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
                "picked_up_at": order.picked_up_at.isoformat() if order.picked_up_at else None,
                "completed_at": order.completed_at.isoformat() if order.completed_at else None,
                "cancelled_at": order.cancelled_at.isoformat() if order.cancelled_at else None
            },
            "status_logs": [
                {
                    "from": log.from_status,
                    "to": log.to_status,
                    "operator": log.operator,
                    "remark": log.remark,
                    "time": log.created_at.isoformat()
                }
                for log in sorted(order.status_logs, key=lambda x: x.id)
            ]
        }

    def _status_text(self, status: OrderStatus) -> str:
        mapping = {
            OrderStatus.CREATED: "待支付",
            OrderStatus.PAID: "已支付",
            OrderStatus.CUTOFF: "已截单",
            OrderStatus.SORTING: "分拣中",
            OrderStatus.SORTED: "分拣完成",
            OrderStatus.DELIVERING: "配送中",
            OrderStatus.DELIVERED: "团长签收",
            OrderStatus.PICKED_UP: "已提货",
            OrderStatus.COMPLETED: "已完成",
            OrderStatus.CANCELLED: "已取消",
            OrderStatus.REFUNDING: "退款中",
            OrderStatus.REFUNDED: "已退款",
        }
        return mapping.get(status, status.value)
