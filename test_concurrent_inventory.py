import os
import sys
import threading
import time
import tempfile
import shutil
from datetime import datetime, timedelta
from typing import List, Dict
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import models
from app.database import Base
from app.models import (
    Product, Leader, User, Order, OrderStatus, PaymentStatus,
    Inventory, InventoryReservation
)
from app.modules.inventory import InventoryService, InventoryInsufficientError
from app.modules.order_service import OrderService

PASS = "[PASS]"
FAIL = "[FAIL]"
WARN = "[WARN]"
OK = "[OK]"
BORDER = "=" * 70


def create_test_engine(db_path: str):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 60},
        pool_pre_ping=True
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=60000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

    return engine


TEST_STOCK = 10
CONCURRENT_USERS = 30
QTY_PER_USER = 1

WAREHOUSE_DATE = "2026-06-22"
TEST_PRODUCT_ID = 9999
TEST_USER_BASE = 10000
TEST_LEADER_ID = 9999


def setup_test_db(db_path: str):
    engine = create_test_engine(db_path)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        product = Product(
            id=TEST_PRODUCT_ID,
            name="limited_test_product",
            category="test",
            unit="unit",
            price=10.0,
            stock_total=TEST_STOCK,
            daily_limit=TEST_STOCK,
            is_active=True
        )
        db.add(product)

        leader = Leader(
            id=TEST_LEADER_ID,
            name="test_leader",
            phone="13800000000",
            pickup_address="test_address",
            district="test_district",
            is_active=True
        )
        db.add(leader)

        for i in range(CONCURRENT_USERS):
            user = User(
                id=TEST_USER_BASE + i,
                name=f"test_user_{i}",
                phone=f"139{10000000 + i}",
                default_leader_id=TEST_LEADER_ID
            )
            db.add(user)

        db.commit()
    finally:
        db.close()

    return engine, SessionLocal


def concurrent_create_order(SessionLocal, user_idx: int, results: List[Dict], lock: threading.Lock):
    db = SessionLocal()
    try:
        service = OrderService(db)
        user_id = TEST_USER_BASE + user_idx
        items = [{"product_id": TEST_PRODUCT_ID, "qty": QTY_PER_USER}]

        try:
            order, price_result = service.create_order(
                user_id=user_id,
                leader_id=TEST_LEADER_ID,
                warehouse_date=WAREHOUSE_DATE,
                items=items
            )
            db.commit()

            try:
                service.pay_order(order.id)
                db.commit()
                with lock:
                    results.append({
                        "user": user_idx,
                        "success": True,
                        "order_id": order.id,
                        "paid": True,
                        "error": None
                    })
            except Exception as e:
                db.rollback()
                with lock:
                    results.append({
                        "user": user_idx,
                        "success": True,
                        "order_id": order.id,
                        "paid": False,
                        "error": f"pay_fail: {str(e)}"
                    })
        except InventoryInsufficientError as e:
            db.rollback()
            with lock:
                results.append({
                    "user": user_idx,
                    "success": False,
                    "order_id": None,
                    "paid": False,
                    "error": str(e)
                })
        except Exception as e:
            db.rollback()
            with lock:
                results.append({
                    "user": user_idx,
                    "success": False,
                    "order_id": None,
                    "paid": False,
                    "error": str(e)
                })
    finally:
        db.close()


def test_concurrent_purchase_no_supersell():
    print(BORDER)
    print("Test 1: Concurrent purchase - no overselling")
    print(BORDER)
    print(f"Initial stock: {TEST_STOCK}")
    print(f"Concurrent users: {CONCURRENT_USERS}")
    print(f"Qty per user: {QTY_PER_USER}")
    print(f"Max possible successful orders: {TEST_STOCK // QTY_PER_USER}")
    print()

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_concurrent.db")

    try:
        engine, SessionLocal = setup_test_db(db_path)

        results: List[Dict] = []
        lock = threading.Lock()
        threads: List[threading.Thread] = []

        start_barrier = threading.Barrier(CONCURRENT_USERS)

        def thread_target(idx):
            start_barrier.wait()
            concurrent_create_order(SessionLocal, idx, results, lock)

        for i in range(CONCURRENT_USERS):
            t = threading.Thread(target=thread_target, args=(i,), name=f"user-{i}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=180)

        for t in threads:
            if t.is_alive():
                print(f"{WARN} Thread {t.name} did not finish properly")

        print("Execution Results:")
        success_orders = [r for r in results if r["success"] and r["paid"]]
        failed_insufficient = [r for r in results if not r["success"] and "insufficient" in str(r.get("error", "")).lower() or (not r["success"] and "stock" in str(r.get("error", "")).lower())]
        insufficient_keywords = ["insufficient", "stock", "库存不足"]
        failed_insufficient = [
            r for r in results
            if not r["success"] and any(kw in str(r.get("error", "")).lower() for kw in insufficient_keywords)
        ]
        other_errors = [r for r in results if not r["success"] and not any(kw in str(r.get("error", "")).lower() for kw in insufficient_keywords)]
        unpaid_orders = [r for r in results if r["success"] and not r["paid"]]

        print(f"  - Created and paid successfully: {len(success_orders)}")
        print(f"  - Failed (insufficient stock): {len(failed_insufficient)}")
        print(f"  - Created but pay failed: {len(unpaid_orders)}")
        if other_errors:
            print(f"  - Other errors: {len(other_errors)}")
            for e in other_errors[:5]:
                print(f"      User{e['user']}: {e['error']}")
        print()

        db = SessionLocal()
        try:
            inventory = db.query(Inventory).filter(
                Inventory.product_id == TEST_PRODUCT_ID,
                Inventory.warehouse_date == WAREHOUSE_DATE
            ).first()

            paid_orders_from_db = db.query(Order).filter(
                Order.status == OrderStatus.PAID,
                Order.warehouse_date == WAREHOUSE_DATE
            ).all()

            total_qty_sold = 0
            for o in paid_orders_from_db:
                for item in o.items:
                    if item.product_id == TEST_PRODUCT_ID:
                        total_qty_sold += item.qty

            print("Database Verification:")
            inv_count = db.query(Inventory).filter(
                Inventory.product_id == TEST_PRODUCT_ID,
                Inventory.warehouse_date == WAREHOUSE_DATE
            ).count()
            print(f"  - Inventory record count: {inv_count} (should be exactly 1)")
            if inventory:
                print(f"  - Inventory available_qty = {inventory.available_qty}")
                print(f"  - Inventory reserved_qty  = {inventory.reserved_qty}")
                print(f"  - Inventory sorted_qty    = {inventory.sorted_qty}")
                print(f"  - Real usable = max(0, available - reserved) = {max(0, inventory.available_qty - inventory.reserved_qty)}")
            print(f"  - Paid orders in DB: {len(paid_orders_from_db)}")
            print(f"  - Total qty in paid orders: {total_qty_sold}")
            print()

            test_passed = True
            issues = []

            if inv_count != 1:
                issues.append(f"{FAIL} Duplicate inventory records: {inv_count} (expected 1)")
                test_passed = False
            else:
                print(f"{OK} Single inventory record (no duplicates)")

            if total_qty_sold > TEST_STOCK:
                issues.append(
                    f"{FAIL} OVERSOLD! Sold {total_qty_sold} units, but stock is only {TEST_STOCK}"
                )
                test_passed = False
            else:
                print(f"{OK} No oversell: sold {total_qty_sold} <= stock {TEST_STOCK}")

            if len(success_orders) != len(paid_orders_from_db):
                issues.append(
                    f"{FAIL} Result mismatch: app reports {len(success_orders)} success, DB has {len(paid_orders_from_db)}"
                )
                test_passed = False
            else:
                print(f"{OK} Result consistent: app and DB both have {len(success_orders)} paid orders")

            if inventory and inventory.available_qty < 0:
                issues.append(f"{FAIL} Inventory available_qty negative: {inventory.available_qty}")
                test_passed = False
            else:
                print(f"{OK} Inventory available_qty is non-negative")

            if inventory and inventory.reserved_qty < 0:
                issues.append(f"{FAIL} Reserved qty negative: {inventory.reserved_qty}")
                test_passed = False
            else:
                print(f"{OK} Reserved qty is non-negative")

            if unpaid_orders:
                print(f"{WARN} {len(unpaid_orders)} orders created but unpaid (doesn't affect oversell)")

            if other_errors:
                print(f"{WARN} {len(other_errors)} non-stock errors occurred (monitor)")

            print()
            if test_passed:
                print(f"{PASS} Test 1 PASSED: Concurrent purchase with no overselling!")
            else:
                print(f"{FAIL} Test 1 FAILED:")
                for issue in issues:
                    print(f"   {issue}")

            return test_passed
        finally:
            db.close()
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def test_inventory_lock_release_flow():
    print("\n" + BORDER)
    print("Test 2: Full flow - Lock / Confirm / Release / Restock")
    print(BORDER)

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_lock_flow.db")

    try:
        engine, SessionLocal = setup_test_db(db_path)
        test_passed = True
        issues = []

        db = SessionLocal()
        try:
            inv_service = InventoryService(db)
            order_service = OrderService(db)

            print("\nStep 1: Create order (lock inventory)")
            order1, _ = order_service.create_order(
                user_id=TEST_USER_BASE,
                leader_id=TEST_LEADER_ID,
                warehouse_date=WAREHOUSE_DATE,
                items=[{"product_id": TEST_PRODUCT_ID, "qty": 3}]
            )
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Created order #{order1.id}, qty 3")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}, "
                  f"usable={max(0, inv.available_qty - inv.reserved_qty)}")

            if inv.available_qty != TEST_STOCK or inv.reserved_qty != 3:
                issues.append("After create: lock qty incorrect")
                test_passed = False

            print("\nStep 2: Pay first order (confirm inventory consumption)")
            order_service.pay_order(order1.id)
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Order #{order1.id} paid")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}, "
                  f"usable={max(0, inv.available_qty - inv.reserved_qty)}")

            if inv.available_qty != TEST_STOCK - 3 or inv.reserved_qty != 0:
                issues.append("After pay: confirmed stock incorrect")
                test_passed = False

            print("\nStep 3: Create 2nd order (unpaid, then cancel - release lock)")
            order2, _ = order_service.create_order(
                user_id=TEST_USER_BASE + 1,
                leader_id=TEST_LEADER_ID,
                warehouse_date=WAREHOUSE_DATE,
                items=[{"product_id": TEST_PRODUCT_ID, "qty": 2}]
            )
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Created order #{order2.id}, qty 2 (unpaid)")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}, "
                  f"usable={max(0, inv.available_qty - inv.reserved_qty)}")

            if inv.reserved_qty != 2:
                issues.append("2nd order: incorrect lock after create")
                test_passed = False

            order_service.cancel_order(order2.id, operator="user", reason="cancel_unpaid")
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Order #{order2.id} cancelled (unpaid)")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}, "
                  f"usable={max(0, inv.available_qty - inv.reserved_qty)}")

            if inv.available_qty != TEST_STOCK - 3 or inv.reserved_qty != 0:
                issues.append("After unpaid cancel: lock not properly released")
                test_passed = False

            print("\nStep 4: Create 3rd order (pay then cancel - restock)")
            order3, _ = order_service.create_order(
                user_id=TEST_USER_BASE + 2,
                leader_id=TEST_LEADER_ID,
                warehouse_date=WAREHOUSE_DATE,
                items=[{"product_id": TEST_PRODUCT_ID, "qty": 4}]
            )
            db.commit()
            order_service.pay_order(order3.id)
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Created order #{order3.id}, qty 4 and paid")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}, "
                  f"usable={max(0, inv.available_qty - inv.reserved_qty)}")

            if inv.available_qty != TEST_STOCK - 3 - 4 or inv.reserved_qty != 0:
                issues.append("After 3rd order pay: incorrect stock level")
                test_passed = False

            order_service.cancel_order(order3.id, operator="system", reason="cancel_paid")
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Order #{order3.id} cancelled (paid) - should restock")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}, "
                  f"usable={max(0, inv.available_qty - inv.reserved_qty)}")

            expected_available = TEST_STOCK - 3
            if inv.available_qty != expected_available or inv.reserved_qty != 0:
                issues.append(
                    f"After paid cancel: restock incorrect. "
                    f"Expected available={expected_available}, reserved=0. "
                    f"Actual available={inv.available_qty}, reserved={inv.reserved_qty}"
                )
                test_passed = False

            print("\nStep 5: Auto release expired reservation")
            order4, _ = order_service.create_order(
                user_id=TEST_USER_BASE + 3,
                leader_id=TEST_LEADER_ID,
                warehouse_date=WAREHOUSE_DATE,
                items=[{"product_id": TEST_PRODUCT_ID, "qty": 2}]
            )
            db.commit()

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            print(f"  Created order #{order4.id}, qty 2 (unpaid, will expire)")
            print(f"  Before expire: reserved={inv.reserved_qty}")

            reservations = db.query(InventoryReservation).filter(
                InventoryReservation.order_id == order4.id
            ).all()
            for r in reservations:
                r.expires_at = datetime.utcnow() - timedelta(minutes=60)
            db.commit()
            print(f"  Set reservation expire time to 60 minutes ago")

            cleaned = inv_service.clean_all_expired_reservations()
            db.commit()
            print(f"  Cleanup expired: cancelled_orders={cleaned['cancelled_orders']}, "
                  f"released_reservations={cleaned['cleaned_reservations']}")

            inv = inv_service.get_or_create_inventory(TEST_PRODUCT_ID, WAREHOUSE_DATE)
            order4_refreshed = db.query(Order).filter(Order.id == order4.id).first()
            print(f"  Order status: {order4_refreshed.status.value}")
            print(f"  Inventory: available={inv.available_qty}, reserved={inv.reserved_qty}")

            if inv.reserved_qty != 0:
                issues.append(f"Expired lock not released: reserved={inv.reserved_qty} (should be 0)")
                test_passed = False
            if order4_refreshed.status != OrderStatus.CANCELLED:
                issues.append(f"Expired order not cancelled: status={order4_refreshed.status.value}")
                test_passed = False

            print()
            if test_passed:
                print(f"{PASS} Test 2 PASSED: Lock / Confirm / Release / Restock flow is correct!")
            else:
                print(f"{FAIL} Test 2 FAILED:")
                for issue in issues:
                    print(f"   {issue}")

            return test_passed
        finally:
            db.close()
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def test_multiple_products_deadlock_prevention():
    print("\n" + BORDER)
    print("Test 3: Multi-product purchase - deadlock prevention via ID ordering")
    print(BORDER)

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_deadlock.db")

    try:
        engine = create_test_engine(db_path)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        ADDITIONAL_PRODUCT = TEST_PRODUCT_ID + 1
        STOCK_A = 20
        STOCK_B = 20

        db = SessionLocal()
        try:
            db.add_all([
                Product(
                    id=TEST_PRODUCT_ID,
                    name="product_A_low_id",
                    category="test",
                    unit="unit",
                    price=10.0,
                    stock_total=STOCK_A,
                    daily_limit=STOCK_A,
                    is_active=True
                ),
                Product(
                    id=ADDITIONAL_PRODUCT,
                    name="product_B_high_id",
                    category="test",
                    unit="unit",
                    price=20.0,
                    stock_total=STOCK_B,
                    daily_limit=STOCK_B,
                    is_active=True
                ),
                Leader(
                    id=TEST_LEADER_ID,
                    name="test_leader",
                    phone="13800000000",
                    pickup_address="test_address",
                    district="test_district",
                    is_active=True
                )
            ])
            for i in range(20):
                db.add(User(
                    id=TEST_USER_BASE + i,
                    name=f"test_user_{i}",
                    phone=f"139{20000000 + i}",
                    default_leader_id=TEST_LEADER_ID
                ))
            db.commit()
        finally:
            db.close()

        results: List[Dict] = []
        lock = threading.Lock()
        barrier = threading.Barrier(20)

        def thread_a(idx):
            barrier.wait()
            db = SessionLocal()
            try:
                svc = OrderService(db)
                try:
                    order, _ = svc.create_order(
                        user_id=TEST_USER_BASE + idx,
                        leader_id=TEST_LEADER_ID,
                        warehouse_date=WAREHOUSE_DATE,
                        items=[
                            {"product_id": TEST_PRODUCT_ID, "qty": 1},
                            {"product_id": ADDITIONAL_PRODUCT, "qty": 1}
                        ]
                    )
                    svc.pay_order(order.id)
                    db.commit()
                    with lock:
                        results.append({"ok": True, "error": None})
                except Exception as e:
                    db.rollback()
                    with lock:
                        results.append({"ok": False, "error": str(e)})
            finally:
                db.close()

        threads = []
        for i in range(20):
            t = threading.Thread(target=thread_a, args=(i,), name=f"buyer-{i}")
            threads.append(t)
            t.start()

        deadline = time.time() + 90
        all_done = True
        for t in threads:
            remaining = deadline - time.time()
            if remaining <= 0:
                all_done = False
                break
            t.join(timeout=remaining)

        if not all_done:
            for t in threads:
                if t.is_alive():
                    print(f"{WARN} Thread {t.name} still alive (possible DEADLOCK)")
            print(f"{FAIL} Test 3 FAILED: Possible deadlock - threads did not complete within 90s")
            return False

        success_count = sum(1 for r in results if r["ok"])
        errors = [r["error"] for r in results if not r["ok"]]

        print(f"  Successful orders: {success_count}/20")
        if errors:
            print(f"  Failed: {len(errors)}")
            db_errs = [e for e in errors if "database is locked" in str(e).lower() or "deadlock" in str(e).lower()]
            if db_errs:
                print(f"  DB lock errors: {len(db_errs)} (may need retry mechanism)")
        print()

        db = SessionLocal()
        try:
            inv_a = db.query(Inventory).filter(
                Inventory.product_id == TEST_PRODUCT_ID,
                Inventory.warehouse_date == WAREHOUSE_DATE
            ).first()
            inv_b = db.query(Inventory).filter(
                Inventory.product_id == ADDITIONAL_PRODUCT,
                Inventory.warehouse_date == WAREHOUSE_DATE
            ).first()

            sold_a = STOCK_A - (inv_a.available_qty if inv_a else STOCK_A)
            sold_b = STOCK_B - (inv_b.available_qty if inv_b else STOCK_B)

            test_passed = True
            if sold_a > STOCK_A:
                print(f"{FAIL} Product A oversold: sold {sold_a} > stock {STOCK_A}")
                test_passed = False
            else:
                print(f"{OK} Product A: sold {sold_a} <= stock {STOCK_A}")

            if sold_b > STOCK_B:
                print(f"{FAIL} Product B oversold: sold {sold_b} > stock {STOCK_B}")
                test_passed = False
            else:
                print(f"{OK} Product B: sold {sold_b} <= stock {STOCK_B}")

            if sold_a != sold_b:
                print(f"{WARN} Note: A and B sold qty differ (A={sold_a}, B={sold_b}), "
                      f"may be due to partial order failures")

            if test_passed and all_done:
                print(f"\n{PASS} Test 3 PASSED: No deadlock & no overselling!")
            return test_passed and all_done
        finally:
            db.close()
    finally:
        try:
            engine.dispose()
        except Exception:
            pass
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    print()
    print("+" + "-" * 68 + "+")
    print("|" + "  INVENTORY LOCK & CONCURRENCY TESTS  ".center(68) + "|")
    print("+" + "-" * 68 + "+")
    print()

    test_results = []

    test_results.append(("Concurrent purchase no oversell", test_concurrent_purchase_no_supersell()))
    test_results.append(("Lock / Confirm / Release / Restock flow", test_inventory_lock_release_flow()))
    test_results.append(("Multi-product no deadlock", test_multiple_products_deadlock_prevention()))

    print("\n" + BORDER)
    print("SUMMARY:")
    print(BORDER)
    passed = 0
    for name, ok in test_results:
        status = PASS if ok else FAIL
        print(f"  {status} - {name}")
        if ok:
            passed += 1

    print(f"\nTotal: {passed}/{len(test_results)} tests passed")

    if passed == len(test_results):
        print(f"\n{PASS} ALL TESTS PASSED!")
        sys.exit(0)
    else:
        print(f"\n{FAIL} {len(test_results) - passed} TEST(S) FAILED!")
        sys.exit(1)
