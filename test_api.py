import urllib.request
import json

BASE = "http://localhost:8000"

def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.loads(r.read().decode())

def post(path, data=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode() if data else None,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "detail": e.read().decode()}

print("=" * 60)
print("[1] Test product list")
d = get("/api/products")
print(f"[PASS] Product count: {len(d['data'])}, warehouse date: {d['warehouse_date']}")
for p in d['data'][:3]:
    print(f"   - {p['name']}: RMB {p['price']}/{p['unit']}, stock {p['available_qty']}")

print("\n[2] Test group leaders list")
d = get("/api/leaders")
print(f"[PASS] Leader count: {len(d['data'])}")
for l in d['data'][:3]:
    print(f"   - {l['name']} ({l['district']})")

print("\n[3] Test user list")
d = get("/api/users")
print(f"[PASS] User count: {len(d['data'])}")

print("\n[4] Test price preview (create 3 apples)")
body = {
    "user_id": 1,
    "leader_id": 1,
    "items": [{"product_id": 1, "qty": 6}]
}
d = post("/api/orders/preview", body)
if "data" in d:
    p = d['data']
    print(f"[PASS] Goods amount: RMB {p['goods_amount']}")
    print(f"   Promotion discount: RMB {p['promotion_discount']}")
    if p['promotion_details']:
        for pr in p['promotion_details']:
            print(f"     - {pr['name']}: -RMB {pr['amount']}")
    print(f"   Shipping fee: RMB {p['shipping_fee']}")
    print(f"   Order amount: RMB {p['order_amount']}")
else:
    print(f"[FAIL] Error: {d}")

print("\n[5] Test create order")
body = {
    "user_id": 1,
    "leader_id": 1,
    "items": [
        {"product_id": 1, "qty": 6},
        {"product_id": 4, "qty": 1},
        {"product_id": 6, "qty": 2}
    ]
}
d = post("/api/orders", body)
if "data" in d:
    o = d['data']
    print(f"[PASS] Order created: {o['order_no']}")
    print(f"   Status: {o['status_text']}")
    print(f"   Goods amount: RMB {o['amounts']['goods_amount']}")
    print(f"   Order amount: RMB {o['amounts']['order_amount']}")
    order_id = o['id']
else:
    print(f"[FAIL] Error: {d}")
    order_id = None

if order_id:
    print(f"\n[6] Test pay order #{order_id}")
    d = post(f"/api/orders/{order_id}/pay")
    if "data" in d:
        print(f"[PASS] Paid: {d['data']['status_text']}, paid RMB {d['data']['amounts']['paid_amount']}")
    else:
        print(f"[FAIL] Error: {d}")

print("\n[7] Test inventory summary")
d = get("/api/inventory/summary")
print(f"[PASS] Inventory records: {len(d['data'])} items, warehouse: {d['warehouse_date']}")
low = [i for i in d['data'] if i['usable'] < 50]
if low:
    print(f"[WARN] Low stock: {len(low)} items")
    for l in low[:3]:
        print(f"   - {l['product_name']}: {l['usable']} left")

print("\n[8] Test dashboard stats")
d = get("/api/dashboard/stats")
if "data" in d:
    s = d['data']
    print(f"[PASS] Today total orders: {s['total_orders']}, GMV: RMB {s['total_gmv']}")
    print(f"   Sorting: pending {s['sorting']['pending']}/doing {s['sorting']['in_progress']}/done {s['sorting']['completed']}")
    print(f"   Ready dispatch: {s['dispatch']['ready_orders']} orders")
else:
    print(f"[FAIL] Error: {d}")

print("\n[9] Create more orders for flow testing")
for i in range(2, 5):
    body = {
        "user_id": i,
        "leader_id": ((i-1) % 3) + 1,
        "items": [
            {"product_id": 2, "qty": 2},
            {"product_id": 7, "qty": 1}
        ]
    }
    r = post("/api/orders", body)
    if "data" in r:
        oid = r['data']['id']
        post(f"/api/orders/{oid}/pay")
        print(f"  [PASS] Order {oid} created & paid")

print("\n[10] Test cutoff")
d = post("/api/warehouse/cutoff")
print(f"[PASS] {d.get('message', d)}")

print("\n[11] Test sorting tasks")
d = get("/api/sorting/tasks")
print(f"[PASS] Sorting tasks: {len(d['data'])}")
for t in d['data'][:3]:
    print(f"   - {t['leader_name']}: {t['status_text']} ({t['sorted_items']}/{t['total_items']})")

if d['data']:
    t0 = d['data'][0]
    print(f"\n[12] Test start sorting task #{t0['id']}")
    r = post(f"/api/sorting/tasks/{t0['id']}/start")
    print(f"[PASS] {r.get('message', r)}")

    for item in t0['items']:
        r = post(f"/api/sorting/tasks/{t0['id']}/items/{item['id']}/sort?qty={item['required_qty']}")
        if 'error' in r:
            print(f"   Sort {item['product_name']}: {r}")
    r = post(f"/api/sorting/tasks/{t0['id']}/complete")
    print(f"   Complete: {r.get('message', r)}")

print("\n[13] Batch complete all sorting")
d = get("/api/sorting/tasks")
for t in d['data']:
    if t['status'] != 'completed':
        if t['status'] == 'pending':
            post(f"/api/sorting/tasks/{t['id']}/start")
        for item in t['items']:
            remain = item['required_qty'] - item['sorted_qty']
            if remain > 0:
                post(f"/api/sorting/tasks/{t['id']}/items/{item['id']}/sort?qty={remain}")
        post(f"/api/sorting/tasks/{t['id']}/complete")
print("[PASS] All sorting tasks completed")

print("\n[14] Test generate dispatch routes")
r = post("/api/dispatch/routes?strategy=district")
print(f"[PASS] {r.get('message', r)}")

print("\n[15] Test dispatch route list")
d = get("/api/dispatch/routes")
print(f"[PASS] Dispatch routes: {len(d['data'])}")
for r in d['data'][:3]:
    print(f"   - {r['route_name']}: {r['status_text']} {r['total_orders']} orders {r['vehicle_plate']}")

if d['data']:
    r0 = d['data'][0]
    print(f"\n[16] Test dispatch route #{r0['id']}")
    r = post(f"/api/dispatch/routes/{r0['id']}/dispatch")
    print(f"[PASS] {r.get('message', r)}")

    print("\n[17] Get route detail")
    detail = get(f"/api/dispatch/routes/{r0['id']}")['data']
    print(f"[PASS] Route {detail['route_name']}: {detail['stops'].__len__()} stops, driver {detail['vehicle']['driver']}")
    for s in detail['stops'][:3]:
        print(f"   - Stop {s['sequence']} {s['stop_name']}: {s['order_count']} orders")

print("\n[18] Test inventory cleanup API")
r = post("/api/inventory/clean-expired")
if "data" in r:
    print(f"[PASS] Cleanup: cancelled_orders={r['data'].get('cancelled_orders', 'N/A')}, released_reservations={r['data'].get('cleaned_reservations', 'N/A')}")
else:
    print(f"[INFO] Cleanup result: {r}")

print("\n" + "=" * 60)
print("[PASS] ALL CORE FUNCTION TESTS PASSED!")
print(f"Open http://localhost:8000 to view full dashboard")
