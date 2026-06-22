from typing import Dict, List, Callable, Type
from sqlalchemy.orm import Session
from app.modules.order_state_machine import (
    OrderStateMachine, StateTransitionContext
)
from app.modules.side_effects import (
    SideEffectHandler, InventorySideEffectHandler, CouponSideEffectHandler,
    PaymentSideEffectHandler, SortingSideEffectHandler, DeliverySideEffectHandler
)
from app.models import OrderStatus


class SideEffectRegistry:
    def __init__(self, db: Session):
        self.db = db
        self.state_machine = OrderStateMachine(db)
        self._handlers: Dict[str, List[SideEffectHandler]] = {}
        self._register_default_handlers()

    def _make_handler_wrapper(self, handler: SideEffectHandler) -> Callable:
        def wrapper(ctx: StateTransitionContext) -> None:
            handler.handle(ctx)
        return wrapper

    def register_handler(self, transition_key: str, handler: SideEffectHandler) -> None:
        self._handlers.setdefault(transition_key, []).append(handler)
        wrapper = self._make_handler_wrapper(handler)
        self.state_machine.register_after(transition_key, wrapper)

    def register_handler_for_all(self, handler: SideEffectHandler) -> None:
        self._handlers.setdefault("*", []).append(handler)
        wrapper = self._make_handler_wrapper(handler)
        self.state_machine.register_after("*", wrapper)

    def _register_default_handlers(self) -> None:
        inv_handler = InventorySideEffectHandler(self.db)
        coupon_handler = CouponSideEffectHandler(self.db)
        payment_handler = PaymentSideEffectHandler(self.db)
        sorting_handler = SortingSideEffectHandler(self.db)
        delivery_handler = DeliverySideEffectHandler(self.db)

        CREATED_PAID = OrderStateMachine.transition_key(OrderStatus.CREATED, OrderStatus.PAID)
        CREATED_CANCELLED = OrderStateMachine.transition_key(OrderStatus.CREATED, OrderStatus.CANCELLED)
        PAID_CANCELLED = OrderStateMachine.transition_key(OrderStatus.PAID, OrderStatus.CANCELLED)
        PAID_CUTOFF = OrderStateMachine.transition_key(OrderStatus.PAID, OrderStatus.CUTOFF)
        CUTOFF_SORTING = OrderStateMachine.transition_key(OrderStatus.CUTOFF, OrderStatus.SORTING)
        SORTING_SORTED = OrderStateMachine.transition_key(OrderStatus.SORTING, OrderStatus.SORTED)
        SORTED_DELIVERING = OrderStateMachine.transition_key(OrderStatus.SORTED, OrderStatus.DELIVERING)
        DELIVERED_REFUNDING = OrderStateMachine.transition_key(OrderStatus.DELIVERED, OrderStatus.REFUNDING)
        PICKED_UP_REFUNDING = OrderStateMachine.transition_key(OrderStatus.PICKED_UP, OrderStatus.REFUNDING)
        COMPLETED_REFUNDING = OrderStateMachine.transition_key(OrderStatus.COMPLETED, OrderStatus.REFUNDING)
        REFUNDING_REFUNDED = OrderStateMachine.transition_key(OrderStatus.REFUNDING, OrderStatus.REFUNDED)

        self.register_handler(CREATED_PAID, payment_handler)
        self.register_handler(CREATED_PAID, inv_handler)
        self.register_handler(CREATED_PAID, coupon_handler)

        self.register_handler(PAID_CUTOFF, sorting_handler)

        self.register_handler(CUTOFF_SORTING, sorting_handler)

        self.register_handler(SORTING_SORTED, sorting_handler)

        self.register_handler(CREATED_CANCELLED, payment_handler)
        self.register_handler(CREATED_CANCELLED, inv_handler)

        self.register_handler(PAID_CANCELLED, payment_handler)
        self.register_handler(PAID_CANCELLED, inv_handler)
        self.register_handler(PAID_CANCELLED, coupon_handler)

        self.register_handler(DELIVERED_REFUNDING, payment_handler)
        self.register_handler(PICKED_UP_REFUNDING, payment_handler)
        self.register_handler(COMPLETED_REFUNDING, payment_handler)

        self.register_handler(REFUNDING_REFUNDED, payment_handler)
