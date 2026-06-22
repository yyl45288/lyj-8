from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
from enum import Enum
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from app.models import Order, OrderStatus, OrderStatusLog


class StateTransitionError(Exception):
    pass


@dataclass
class StateTransitionContext:
    order: Order
    from_status: OrderStatus
    to_status: OrderStatus
    operator: str
    remark: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    db: Optional[Session] = None


class OrderStateMachine:
    STATE_TRANSITIONS = {
        OrderStatus.CREATED: [OrderStatus.PAID, OrderStatus.CANCELLED],
        OrderStatus.PAID: [OrderStatus.CUTOFF, OrderStatus.CANCELLED, OrderStatus.REFUNDING],
        OrderStatus.CUTOFF: [OrderStatus.SORTING, OrderStatus.REFUNDING],
        OrderStatus.SORTING: [OrderStatus.SORTED],
        OrderStatus.SORTED: [OrderStatus.DELIVERING],
        OrderStatus.DELIVERING: [OrderStatus.DELIVERED],
        OrderStatus.DELIVERED: [OrderStatus.PICKED_UP, OrderStatus.REFUNDING],
        OrderStatus.PICKED_UP: [OrderStatus.COMPLETED, OrderStatus.REFUNDING],
        OrderStatus.REFUNDING: [OrderStatus.REFUNDED, OrderStatus.PAID, OrderStatus.COMPLETED, OrderStatus.DELIVERED],
        OrderStatus.REFUNDED: [],
        OrderStatus.CANCELLED: [],
        OrderStatus.COMPLETED: [OrderStatus.REFUNDING],
    }

    def __init__(self, db: Session):
        self.db = db
        self._before_handlers: Dict[str, List[Callable]] = {}
        self._after_handlers: Dict[str, List[Callable]] = {}

    def validate_transition(self, current: OrderStatus, target: OrderStatus) -> bool:
        allowed = self.STATE_TRANSITIONS.get(current, [])
        return target in allowed

    def register_before(self, transition_key: str, handler: Callable):
        self._before_handlers.setdefault(transition_key, []).append(handler)

    def register_after(self, transition_key: str, handler: Callable):
        self._after_handlers.setdefault(transition_key, []).append(handler)

    @staticmethod
    def transition_key(from_status: OrderStatus, to_status: OrderStatus) -> str:
        return f"{from_status.value}->{to_status.value}"

    def _log_status_change(self, order: Order, from_status: Optional[str],
                           to_status: str, operator: str = "system",
                           remark: str = None):
        log = OrderStatusLog(
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            operator=operator,
            remark=remark
        )
        self.db.add(log)

    def _run_handlers(self, handlers_dict: Dict[str, List[Callable]],
                      key: str, ctx: StateTransitionContext):
        handlers = handlers_dict.get(key, [])
        for handler in handlers:
            handler(ctx)

    def can_transition(self, order: Order, target: OrderStatus) -> bool:
        return self.validate_transition(order.status, target)

    def transition(self, order: Order, target: OrderStatus,
                   operator: str = "system", remark: str = None,
                   extra: Dict[str, Any] = None) -> Order:
        from_status = order.status
        t_key = self.transition_key(from_status, target)

        if not self.validate_transition(from_status, target):
            raise StateTransitionError(
                f"订单状态 {from_status.value} 不允许流转到 {target.value}"
            )

        ctx = StateTransitionContext(
            order=order,
            from_status=from_status,
            to_status=target,
            operator=operator,
            remark=remark,
            extra=extra or {},
            db=self.db
        )

        self._run_handlers(self._before_handlers, t_key, ctx)
        self._run_handlers(self._before_handlers, "*", ctx)

        old_status_value = from_status.value
        order.status = target

        self._update_order_timestamp(order, target)

        self._log_status_change(
            order, old_status_value, target.value,
            operator=operator, remark=remark
        )

        self._run_handlers(self._after_handlers, t_key, ctx)
        self._run_handlers(self._after_handlers, "*", ctx)

        self.db.flush()
        return order

    def _update_order_timestamp(self, order: Order, target: OrderStatus):
        now = datetime.utcnow()
        if target == OrderStatus.PAID:
            order.paid_at = now
        elif target == OrderStatus.CUTOFF:
            order.cutoff_at = now
        elif target == OrderStatus.DELIVERED:
            order.delivered_at = now
        elif target == OrderStatus.PICKED_UP:
            order.picked_up_at = now
        elif target == OrderStatus.COMPLETED:
            order.completed_at = now
        elif target == OrderStatus.CANCELLED:
            order.cancelled_at = now
