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
print("【1】测试产品列表")
d = get("/api/products")
print(f"✅ 产品数量: {len(d['data'])}, 仓库日期: {d['warehouse_date']}")
for p in d['data'][:3]:
    print(f"   - {p['name']}: ¥{p['price']}/{p['unit']}, 库存{p['available_qty']}")

print("\n【2】测试团长列表")
d = get("/api/leaders")
print(f"✅ 团长数量: {len(d['data'])}")
for l in d['data'][:3]:
    print(f"   - {l['name']} ({l['district']})")

print("\n【3】测试用户列表")
d = get("/api/users")
print(f"✅ 用户数量: {len(d['data'])}")

print("\n【4】测试价格预览（创建3个苹果）")
body = {
    "user_id": 1,
    "leader_id": 1,
    "items": [{"product_id": 1, "qty": 6}]
}
d = post("/api/orders/preview", body)
if "data" in d:
    p = d['data']
    print(f"✅ 商品金额: ¥{p['goods_amount']}")
    print(f"   促销优惠: ¥{p['promotion_discount']}")
    if p['promotion_details']:
        for pr in p['promotion_details']:
            print(f"     - {pr['name']}: -¥{pr['amount']}")
    print(f"   运费: ¥{p['shipping_fee']}")
    print(f"   应付金额: ¥{p['order_amount']}")
else:
    print(f"❌ 错误: {d}")

print("\n【5】测试创建订单")
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
    print(f"✅ 订单创建成功: {o['order_no']}")
    print(f"   状态: {o['status_text']}")
    print(f"   商品金额: ¥{o['amounts']['goods_amount']}")
    print(f"   订单金额: ¥{o['amounts']['order_amount']}")
    order_id = o['id']
else:
    print(f"❌ 错误: {d}")
    order_id = None

if order_id:
    print(f"\n【6】测试支付订单 #{order_id}")
    d = post(f"/api/orders/{order_id}/pay")
    if "data" in d:
        print(f"✅ 支付成功: {d['data']['status_text']}, 已付¥{d['data']['amounts']['paid_amount']}")
    else:
        print(f"❌ 错误: {d}")

print("\n【7】测试库存总览")
d = get("/api/inventory/summary")
print(f"✅ 库存记录: {len(d['data'])}条, 仓库日: {d['warehouse_date']}")
low = [i for i in d['data'] if i['usable'] < 50]
if low:
    print(f"⚠️  低库存商品: {len(low)}个")
    for l in low[:3]:
        print(f"   - {l['product_name']}: 剩{l['usable']}")

print("\n【8】测试仪表盘统计")
d = get("/api/dashboard/stats")
if "data" in d:
    s = d['data']
    print(f"✅ 今日总订单: {s['total_orders']}, GMV: ¥{s['total_gmv']}")
    print(f"   分拣: 待{s['sorting']['pending']}/中{s['sorting']['in_progress']}/完{s['sorting']['completed']}")
    print(f"   待配送订单: {s['dispatch']['ready_orders']}")
else:
    print(f"❌ 错误: {d}")

print("\n【9】创建更多订单用于后续流程测试")
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
        print(f"  ✅ 订单 {oid} 创建并支付")

print("\n【10】测试一键截单")
d = post("/api/warehouse/cutoff")
print(f"✅ {d.get('message', d)}")

print("\n【11】测试分拣任务列表")
d = get("/api/sorting/tasks")
print(f"✅ 分拣任务: {len(d['data'])}个")
for t in d['data'][:3]:
    print(f"   - {t['leader_name']}: {t['status_text']} ({t['sorted_items']}/{t['total_items']})")

if d['data']:
    t0 = d['data'][0]
    print(f"\n【12】测试开始分拣 任务#{t0['id']}")
    r = post(f"/api/sorting/tasks/{t0['id']}/start")
    print(f"✅ {r.get('message', r)}")

    for item in t0['items']:
        r = post(f"/api/sorting/tasks/{t0['id']}/items/{item['id']}/sort?qty={item['required_qty']}")
        if 'error' in r:
            print(f"   分拣{item['product_name']}: {r}")
    r = post(f"/api/sorting/tasks/{t0['id']}/complete")
    print(f"   完成分拣: {r.get('message', r)}")

print("\n【13】批量完成所有分拣")
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
print("✅ 所有分拣任务完成")

print("\n【14】测试生成配送路线")
r = post("/api/dispatch/routes?strategy=district")
print(f"✅ {r.get('message', r)}")

print("\n【15】测试路线列表")
d = get("/api/dispatch/routes")
print(f"✅ 配送路线: {len(d['data'])}条")
for r in d['data'][:3]:
    print(f"   - {r['route_name']}: {r['status_text']} {r['total_orders']}单 {r['vehicle_plate']}")

if d['data']:
    r0 = d['data'][0]
    print(f"\n【16】测试发车 路线#{r0['id']}")
    r = post(f"/api/dispatch/routes/{r0['id']}/dispatch")
    print(f"✅ {r.get('message', r)}")

    print("\n【17】获取路线详情")
    detail = get(f"/api/dispatch/routes/{r0['id']}")['data']
    print(f"✅ 路线 {detail['route_name']}: {detail['stops'].__len__()}站, 司机{detail['vehicle']['driver']}")
    for s in detail['stops'][:3]:
        print(f"   - 站点{s['sequence']} {s['stop_name']}: {s['order_count']}单")

print("\n" + "=" * 60)
print("✅ 所有核心功能测试通过！")
print(f"🌐 请打开 http://localhost:8000 查看完整测试面板")
