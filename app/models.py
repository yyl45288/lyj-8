from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class OrderStatus(str, enum.Enum):
    CREATED = "created"
    PAID = "paid"
    CUTOFF = "cutoff"
    SORTING = "sorting"
    SORTED = "sorted"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    PICKED_UP = "picked_up"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDING = "refunding"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    REFUNDING = "refunding"
    REFUNDED = "refunded"


class CouponType(str, enum.Enum):
    FIXED = "fixed"
    PERCENT = "percent"
    FREE_SHIPPING = "free_shipping"


class PromotionType(str, enum.Enum):
    FULL_REDUCTION = "full_reduction"
    GROUP_BUY = "group_buy"
    SECOND_HALF_PRICE = "second_half_price"


class VehicleStatus(str, enum.Enum):
    IDLE = "idle"
    LOADING = "loading"
    DELIVERING = "delivering"
    MAINTENANCE = "maintenance"


class DeliveryStatus(str, enum.Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    IN_TRANSIT = "in_transit"
    ARRIVED = "arrived"


class SortingStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class AfterSaleStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    CLOSED = "closed"


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100), nullable=False)
    unit = Column(String(20), nullable=False)
    price = Column(Float, nullable=False)
    group_price = Column(Float, nullable=True)
    min_group_size = Column(Integer, default=2)
    stock_total = Column(Integer, default=0)
    daily_limit = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    image_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    inventory_records = relationship("Inventory", back_populates="product")
    order_items = relationship("OrderItem", back_populates="product")
    reservations = relationship("InventoryReservation", back_populates="product")


class Leader(Base):
    __tablename__ = "leaders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False)
    pickup_address = Column(String(500), nullable=False)
    district = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    commission_rate = Column(Float, default=0.05)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    orders = relationship("Order", back_populates="leader")
    sorting_tasks = relationship("SortingTask", back_populates="leader")
    delivery_stops = relationship("DeliveryStop", back_populates="leader")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone = Column(String(20), nullable=False, unique=True)
    default_leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    orders = relationship("Order", back_populates="user")
    coupons = relationship("UserCoupon", back_populates="user")
    after_sales = relationship("AfterSale", back_populates="user")


class Inventory(Base):
    __tablename__ = "inventories"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    warehouse_date = Column(String(20), nullable=False, index=True)
    available_qty = Column(Integer, default=0)
    reserved_qty = Column(Integer, default=0)
    sorted_qty = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="inventory_records")


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    warehouse_date = Column(String(20), nullable=False)
    qty = Column(Integer, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    product = relationship("Product", back_populates="reservations")
    order = relationship("Order", back_populates="reservations")


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    coupon_type = Column(Enum(CouponType), nullable=False)
    value = Column(Float, nullable=False)
    min_order_amount = Column(Float, default=0)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    total_issued = Column(Integer, default=0)
    used_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user_coupons = relationship("UserCoupon", back_populates="coupon")


class UserCoupon(Base):
    __tablename__ = "user_coupons"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=False)
    used = Column(Boolean, default=False)
    used_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    obtained_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="coupons")
    coupon = relationship("Coupon", back_populates="user_coupons")


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    promotion_type = Column(Enum(PromotionType), nullable=False)
    config = Column(Text, nullable=False)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=False)
    status = Column(Enum(OrderStatus), default=OrderStatus.CREATED, nullable=False)
    payment_status = Column(Enum(PaymentStatus), default=PaymentStatus.UNPAID, nullable=False)
    warehouse_date = Column(String(20), nullable=False, index=True)

    goods_amount = Column(Float, default=0)
    promotion_discount = Column(Float, default=0)
    coupon_discount = Column(Float, default=0)
    shipping_fee = Column(Float, default=0)
    order_amount = Column(Float, default=0)
    paid_amount = Column(Float, default=0)

    coupon_id = Column(Integer, ForeignKey("user_coupons.id"), nullable=True)
    remark = Column(String(500), nullable=True)

    paid_at = Column(DateTime(timezone=True), nullable=True)
    cutoff_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    picked_up_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="orders")
    leader = relationship("Leader", back_populates="orders")
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    reservations = relationship("InventoryReservation", back_populates="order")
    status_logs = relationship("OrderStatusLog", back_populates="order", cascade="all, delete-orphan")
    sorting_tasks = relationship("SortingTask", back_populates="order")
    delivery_stops = relationship("DeliveryStop", back_populates="order")
    after_sales = relationship("AfterSale", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    product_name = Column(String(200), nullable=False)
    unit_price = Column(Float, nullable=False)
    actual_price = Column(Float, nullable=False)
    qty = Column(Integer, nullable=False)
    subtotal = Column(Float, nullable=False)
    discount_amount = Column(Float, default=0)
    promotion_note = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class OrderStatusLog(Base):
    __tablename__ = "order_status_logs"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    from_status = Column(String(50), nullable=True)
    to_status = Column(String(50), nullable=False)
    operator = Column(String(100), nullable=True)
    remark = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="status_logs")


class Vehicle(Base):
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, index=True)
    plate_no = Column(String(20), unique=True, nullable=False)
    driver_name = Column(String(100), nullable=False)
    driver_phone = Column(String(20), nullable=False)
    capacity = Column(Float, default=500)
    current_load = Column(Float, default=0)
    status = Column(Enum(VehicleStatus), default=VehicleStatus.IDLE, nullable=False)
    district = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    routes = relationship("DeliveryRoute", back_populates="vehicle")


class DeliveryRoute(Base):
    __tablename__ = "delivery_routes"

    id = Column(Integer, primary_key=True, index=True)
    route_name = Column(String(100), nullable=False)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"), nullable=False)
    warehouse_date = Column(String(20), nullable=False, index=True)
    status = Column(Enum(DeliveryStatus), default=DeliveryStatus.PENDING, nullable=False)
    total_stops = Column(Integer, default=0)
    total_orders = Column(Integer, default=0)
    total_volume = Column(Float, default=0)
    estimated_distance = Column(Float, default=0)
    estimated_duration = Column(Float, default=0)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    arrived_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vehicle = relationship("Vehicle", back_populates="routes")
    stops = relationship("DeliveryStop", back_populates="route", cascade="all, delete-orphan")


class DeliveryStop(Base):
    __tablename__ = "delivery_stops"

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("delivery_routes.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=False)
    stop_sequence = Column(Integer, nullable=False)
    stop_name = Column(String(200), nullable=False)
    stop_address = Column(String(500), nullable=False)
    order_count = Column(Integer, default=0)
    volume = Column(Float, default=0)
    arrived_at = Column(DateTime(timezone=True), nullable=True)
    departed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    route = relationship("DeliveryRoute", back_populates="stops")
    order = relationship("Order", back_populates="delivery_stops")
    leader = relationship("Leader", back_populates="delivery_stops")


class SortingTask(Base):
    __tablename__ = "sorting_tasks"

    id = Column(Integer, primary_key=True, index=True)
    warehouse_date = Column(String(20), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    leader_id = Column(Integer, ForeignKey("leaders.id"), nullable=False)
    status = Column(Enum(SortingStatus), default=SortingStatus.PENDING, nullable=False)
    total_items = Column(Integer, default=0)
    sorted_items = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    operator = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    order = relationship("Order", back_populates="sorting_tasks")
    leader = relationship("Leader", back_populates="sorting_tasks")
    items = relationship("SortingItem", back_populates="task", cascade="all, delete-orphan")


class SortingItem(Base):
    __tablename__ = "sorting_items"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(Integer, ForeignKey("sorting_tasks.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    product_name = Column(String(200), nullable=False)
    required_qty = Column(Integer, nullable=False)
    sorted_qty = Column(Integer, default=0)
    is_complete = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    task = relationship("SortingTask", back_populates="items")


class AfterSale(Base):
    __tablename__ = "after_sales"

    id = Column(Integer, primary_key=True, index=True)
    after_sale_no = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    status = Column(Enum(AfterSaleStatus), default=AfterSaleStatus.SUBMITTED, nullable=False)
    refund_amount = Column(Float, default=0)
    reason = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    operator = Column(String(100), nullable=True)
    reject_reason = Column(String(500), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="after_sales")
    order = relationship("Order", back_populates="after_sales")
    items = relationship("AfterSaleItem", back_populates="after_sale", cascade="all, delete-orphan")


class AfterSaleItem(Base):
    __tablename__ = "after_sale_items"

    id = Column(Integer, primary_key=True, index=True)
    after_sale_id = Column(Integer, ForeignKey("after_sales.id"), nullable=False)
    order_item_id = Column(Integer, ForeignKey("order_items.id"), nullable=False)
    product_name = Column(String(200), nullable=False)
    qty = Column(Integer, nullable=False)
    refund_amount = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    after_sale = relationship("AfterSale", back_populates="items")
