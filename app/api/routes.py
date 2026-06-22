from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta

from app.database import get_db
from app.models import Product, Leader, User, Order, SortingTask, Coupon
from app.modules.inventory import InventoryService, InventoryInsufficientError, InventoryError
from app.modules.pricing import PricingService, CouponEngine
from app.modules.order_service import OrderService, OrderStateError
from app.modules.dispatch import DispatchService, DispatchError
from app.modules.route_transaction_helper import TransactionalRouteHelper

router = APIRouter(prefix="/api", tags=["API"])


def get_today_warehouse_date() -> str:
    now = datetime.now()
    if now.hour >= 22:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


class CreateOrderItem(BaseModel):
    product_id: int
    qty: int


class CreateOrderRequest(BaseModel):
    user_id: int
    leader_id: int
    items: List[CreateOrderItem]
    user_coupon_id: Optional[int] = None
    warehouse_date: Optional[str] = None
    remark: Optional[str] = None


class AfterSaleItemReq(BaseModel):
    order_item_id: int
    qty: int


class AfterSaleRequest(BaseModel):
    order_id: int
    user_id: int
    reason: str
    items: List[AfterSaleItemReq]
    description: Optional[str] = None


@router.get("/products")
def list_products(category: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Product).filter(Product.is_active == True)
    if category:
        query = query.filter(Product.category == category)
    products = query.all()

    warehouse_date = get_today_warehouse_date()
    inv_service = InventoryService(db)

    result = []
    for p in products:
        available = inv_service.get_available_qty(p.id, warehouse_date)
        result.append({
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "unit": p.unit,
            "price": p.price,
            "group_price": p.group_price,
            "min_group_size": p.min_group_size,
            "available_qty": available,
            "description": p.description
        })
    return {"data": result, "warehouse_date": warehouse_date}


@router.get("/products/categories")
def list_categories(db: Session = Depends(get_db)):
    categories = db.query(Product.category).filter(
        Product.is_active == True
    ).distinct().all()
    return {"data": [c[0] for c in categories]}


@router.get("/leaders")
def list_leaders(db: Session = Depends(get_db)):
    leaders = db.query(Leader).filter(Leader.is_active == True).all()
    return {"data": [
        {
            "id": l.id,
            "name": l.name,
            "phone": l.phone,
            "address": l.pickup_address,
            "district": l.district
        }
        for l in leaders
    ]}


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return {"data": [
        {
            "id": u.id,
            "name": u.name,
            "phone": u.phone,
            "default_leader_id": u.default_leader_id
        }
        for u in users
    ]}


@router.get("/coupons/available")
def available_coupons(user_id: int, order_amount: float = Query(0),
                      db: Session = Depends(get_db)):
    engine = CouponEngine(db)
    coupons = engine.get_available_coupons(user_id, order_amount)
    return {"data": coupons}


@router.post("/orders/preview")
def preview_order(req: CreateOrderRequest, db: Session = Depends(get_db)):
    warehouse_date = req.warehouse_date or get_today_warehouse_date()
    items = []
    for it in req.items:
        product = db.query(Product).filter(Product.id == it.product_id).first()
        if not product:
            raise HTTPException(400, f"商品不存在: {it.product_id}")
        items.append({
            "product_id": it.product_id,
            "product_name": product.name,
            "unit_price": product.price,
            "qty": it.qty
        })

    pricing = PricingService(db)
    result = pricing.calculate_price(
        items=items,
        user_id=req.user_id,
        user_coupon_id=req.user_coupon_id
    )
    result["warehouse_date"] = warehouse_date
    return {"data": result}


@router.post("/orders")
def create_order(req: CreateOrderRequest, db: Session = Depends(get_db)):
    warehouse_date = req.warehouse_date or get_today_warehouse_date()
    items = [{"product_id": it.product_id, "qty": it.qty} for it in req.items]

    def _op(db):
        service = OrderService(db)
        order, price_result = service.create_order(
            user_id=req.user_id,
            leader_id=req.leader_id,
            warehouse_date=warehouse_date,
            items=items,
            user_coupon_id=req.user_coupon_id,
            remark=req.remark
        )
        details = service.get_order_details(order.id)
        return {"data": details, "message": "订单创建成功"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(InventoryInsufficientError, OrderStateError, InventoryError),
        error_500_msg="创建订单失败"
    )


@router.post("/orders/{order_id}/pay")
def pay_order(order_id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        order = service.pay_order(order_id)
        return {"data": service.get_order_details(order_id), "message": "支付成功"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="支付失败"
    )


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, reason: Optional[str] = None,
                 operator: str = "user", db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        order = service.cancel_order(order_id, operator=operator, reason=reason)
        return {"data": service.get_order_details(order_id), "message": "订单已取消"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="取消订单失败"
    )


@router.get("/orders")
def list_orders(status: Optional[str] = None,
                warehouse_date: Optional[str] = None,
                user_id: Optional[int] = None,
                leader_id: Optional[int] = None,
                limit: int = 50, offset: int = 0,
                db: Session = Depends(get_db)):
    query = db.query(Order)
    if status:
        from app.models import OrderStatus
        query = query.filter(Order.status == OrderStatus(status))
    if warehouse_date:
        query = query.filter(Order.warehouse_date == warehouse_date)
    if user_id:
        query = query.filter(Order.user_id == user_id)
    if leader_id:
        query = query.filter(Order.leader_id == leader_id)

    orders = query.order_by(Order.id.desc()).offset(offset).limit(limit).all()
    total = query.count()

    service = OrderService(db)
    result = [service.get_order_details(o.id) for o in orders]
    return {"data": result, "total": total}


@router.get("/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    service = OrderService(db)
    details = service.get_order_details(order_id)
    if not details:
        raise HTTPException(404, "订单不存在")
    return {"data": details}


@router.post("/warehouse/cutoff")
def cutoff_orders(warehouse_date: Optional[str] = None,
                  db: Session = Depends(get_db)):
    warehouse_date = warehouse_date or get_today_warehouse_date()

    def _op(db):
        service = OrderService(db)
        orders = service.cutoff_orders(warehouse_date)
        return {
            "data": {"warehouse_date": warehouse_date, "processed_count": len(orders)},
            "message": f"截单完成，处理订单 {len(orders)} 个"
        }

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="截单失败"
    )


@router.get("/sorting/tasks")
def list_sorting_tasks(warehouse_date: Optional[str] = None,
                       status: Optional[str] = None,
                       leader_id: Optional[int] = None,
                       db: Session = Depends(get_db)):
    warehouse_date = warehouse_date or get_today_warehouse_date()
    from app.models import SortingStatus

    query = db.query(SortingTask).filter(SortingTask.warehouse_date == warehouse_date)
    if status:
        query = query.filter(SortingTask.status == SortingStatus(status))
    if leader_id:
        query = query.filter(SortingTask.leader_id == leader_id)

    tasks = query.order_by(SortingTask.id).all()

    result = []
    for t in tasks:
        leader = db.query(Leader).filter(Leader.id == t.leader_id).first()
        order = db.query(Order).filter(Order.id == t.order_id).first()
        result.append({
            "id": t.id,
            "order_id": t.order_id,
            "order_no": order.order_no if order else "",
            "leader_id": t.leader_id,
            "leader_name": leader.name if leader else "",
            "leader_address": leader.pickup_address if leader else "",
            "status": t.status.value,
            "status_text": {
                "pending": "待分拣",
                "in_progress": "分拣中",
                "completed": "已完成"
            }.get(t.status.value, t.status.value),
            "total_items": t.total_items,
            "sorted_items": t.sorted_items,
            "progress": round(t.sorted_items / t.total_items * 100, 1) if t.total_items > 0 else 0,
            "items": [
                {
                    "id": si.id,
                    "product_id": si.product_id,
                    "product_name": si.product_name,
                    "required_qty": si.required_qty,
                    "sorted_qty": si.sorted_qty,
                    "is_complete": si.is_complete
                }
                for si in t.items
            ],
            "operator": t.operator,
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None
        })

    return {"data": result}


@router.post("/sorting/tasks/{task_id}/start")
def start_sorting(task_id: int, operator: str = "sorter",
                  db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        service.start_sorting(task_id, operator=operator)
        return {"message": "分拣已开始"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="开始分拣失败"
    )


@router.post("/sorting/tasks/{task_id}/items/{item_id}/sort")
def sort_item(task_id: int, item_id: int, qty: int = 1,
              operator: str = "sorter", db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        service.sort_item(task_id, item_id, qty, operator=operator)
        return {"message": "分拣更新成功"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="分拣更新失败"
    )


@router.post("/sorting/tasks/{task_id}/complete")
def complete_sorting(task_id: int, operator: str = "sorter",
                     db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        service.complete_sorting(task_id, operator=operator)
        return {"message": "分拣完成"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="完成分拣失败"
    )


@router.post("/dispatch/routes")
def create_routes(warehouse_date: Optional[str] = None,
                  strategy: str = "district",
                  db: Session = Depends(get_db)):
    warehouse_date = warehouse_date or get_today_warehouse_date()

    def _op(db):
        service = DispatchService(db)
        routes = service.create_delivery_routes(warehouse_date, strategy=strategy)
        return {
            "data": {"count": len(routes), "warehouse_date": warehouse_date},
            "message": f"生成 {len(routes)} 条配送路线"
        }

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(DispatchError,),
        error_500_msg="生成配送路线失败"
    )


@router.post("/dispatch/routes/{route_id}/dispatch")
def dispatch_route(route_id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = DispatchService(db)
        service.dispatch_route(route_id)
        return {"message": "发车成功"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(DispatchError,),
        error_500_msg="发车失败"
    )


@router.post("/dispatch/stops/{stop_id}/arrive")
def stop_arrive(stop_id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = DispatchService(db)
        service.stop_arrived(stop_id)
        return {"message": "站点已到达"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(DispatchError,),
        error_500_msg="站点到达确认失败"
    )


@router.post("/dispatch/stops/{stop_id}/depart")
def stop_depart(stop_id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = DispatchService(db)
        service.stop_departed(stop_id)
        return {"message": "站点已离开"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(DispatchError,),
        error_500_msg="站点离开确认失败"
    )


@router.get("/dispatch/summary")
def dispatch_summary(warehouse_date: Optional[str] = None,
                     db: Session = Depends(get_db)):
    warehouse_date = warehouse_date or get_today_warehouse_date()
    service = DispatchService(db)
    return {"data": service.get_dispatch_summary(warehouse_date)}


@router.get("/dispatch/routes")
def list_routes(warehouse_date: Optional[str] = None, status: Optional[str] = None,
                db: Session = Depends(get_db)):
    warehouse_date = warehouse_date or get_today_warehouse_date()
    service = DispatchService(db)
    return {"data": service.list_routes(warehouse_date=warehouse_date, status=status)}


@router.get("/dispatch/routes/{route_id}")
def get_route(route_id: int, db: Session = Depends(get_db)):
    service = DispatchService(db)
    details = service.get_route_details(route_id)
    if not details:
        raise HTTPException(404, "路线不存在")
    return {"data": details}


@router.post("/orders/{order_id}/pickup")
def pickup_order(order_id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        order = service.mark_picked_up(order_id)
        return {"data": service.get_order_details(order_id), "message": "用户已提货"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="提货确认失败"
    )


@router.post("/orders/{order_id}/complete")
def complete_order(order_id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        order = service.complete_order(order_id)
        return {"data": service.get_order_details(order_id), "message": "订单完成"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="订单完成失败"
    )


@router.post("/aftersale")
def create_aftersale(req: AfterSaleRequest, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        items = [{"order_item_id": it.order_item_id, "qty": it.qty} for it in req.items]
        after_sale = service.apply_after_sale(
            req.order_id, req.user_id, req.reason, items, req.description
        )
        return {"data": {"id": after_sale.id, "after_sale_no": after_sale.after_sale_no},
                "message": "售后申请已提交"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="提交售后申请失败"
    )


@router.post("/aftersale/{id}/approve")
def approve_aftersale(id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        service.approve_after_sale(id)
        return {"message": "售后已通过"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="售后审批失败"
    )


@router.post("/aftersale/{id}/reject")
def reject_aftersale(id: int, reason: str, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        service.reject_after_sale(id, reason)
        return {"message": "售后已拒绝"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="售后拒绝失败"
    )


@router.post("/aftersale/{id}/refund")
def execute_refund(id: int, db: Session = Depends(get_db)):
    def _op(db):
        service = OrderService(db)
        service.execute_refund(id)
        return {"message": "退款完成"}

    return TransactionalRouteHelper.handle(
        _op, db,
        bad_request_errors=(OrderStateError,),
        error_500_msg="退款失败"
    )


@router.get("/inventory/summary")
def inventory_summary(warehouse_date: Optional[str] = None,
                      db: Session = Depends(get_db)):
    warehouse_date = warehouse_date or get_today_warehouse_date()
    service = InventoryService(db)
    return {"data": service.get_inventory_summary(warehouse_date),
            "warehouse_date": warehouse_date}


@router.post("/inventory/clean-expired")
def clean_expired_reservations(db: Session = Depends(get_db)):
    def _op(db):
        service = InventoryService(db)
        result = service.clean_all_expired_reservations()
        return {
            "data": result,
            "message": f"清理完成：取消订单 {result['cancelled_orders']} 个，释放锁定 {result['cleaned_reservations']} 条"
        }

    return TransactionalRouteHelper.handle(
        _op, db,
        error_500_msg="清理失败"
    )


@router.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    warehouse_date = get_today_warehouse_date()
    from app.models import OrderStatus, SortingStatus

    stats = {}

    orders_by_status = {}
    for status in list(OrderStatus):
        count = db.query(Order).filter(
            Order.warehouse_date == warehouse_date,
            Order.status == status
        ).count()
        orders_by_status[status.value] = count
    stats["orders_by_status"] = orders_by_status

    total_orders = sum(orders_by_status.values())
    paid_amount = db.query(Order).filter(
        Order.warehouse_date == warehouse_date,
        Order.paid_amount > 0
    ).all()
    stats["total_orders"] = total_orders
    stats["total_gmv"] = round(sum(o.paid_amount for o in paid_amount), 2)

    inv_service = InventoryService(db)
    inv_list = inv_service.get_inventory_summary(warehouse_date)
    stats["total_products_in_stock"] = len(inv_list)
    stats["low_stock_count"] = len([i for i in inv_list if i["usable"] < 20])

    dispatch_service = DispatchService(db)
    dispatch_summary = dispatch_service.get_dispatch_summary(warehouse_date)
    stats["dispatch"] = dispatch_summary

    sorting_pending = db.query(SortingTask).filter(
        SortingTask.warehouse_date == warehouse_date,
        SortingTask.status == SortingStatus.PENDING
    ).count()
    sorting_in_progress = db.query(SortingTask).filter(
        SortingTask.warehouse_date == warehouse_date,
        SortingTask.status == SortingStatus.IN_PROGRESS
    ).count()
    sorting_completed = db.query(SortingTask).filter(
        SortingTask.warehouse_date == warehouse_date,
        SortingTask.status == SortingStatus.COMPLETED
    ).count()
    stats["sorting"] = {
        "pending": sorting_pending,
        "in_progress": sorting_in_progress,
        "completed": sorting_completed
    }

    return {"data": stats, "warehouse_date": warehouse_date}
