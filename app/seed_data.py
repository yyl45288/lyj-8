from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import (
    Product, Leader, User, Coupon, UserCoupon, Promotion, Vehicle,
    CouponType, PromotionType
)
import json


def seed_all(db: Session):
    seed_products(db)
    seed_leaders(db)
    seed_users(db)
    seed_coupons(db)
    seed_promotions(db)
    seed_vehicles(db)
    db.commit()


def seed_products(db: Session):
    if db.query(Product).count() > 0:
        return

    products = [
        Product(
            name="山东烟台红富士苹果",
            category="水果",
            unit="斤",
            price=8.9,
            group_price=6.9,
            min_group_size=5,
            stock_total=500,
            daily_limit=200,
            description="新鲜脆甜，果园直采"
        ),
        Product(
            name="海南金钻凤梨",
            category="水果",
            unit="个",
            price=19.9,
            group_price=15.9,
            min_group_size=2,
            stock_total=300,
            daily_limit=100,
            description="无眼凤梨，去皮即食"
        ),
        Product(
            name="云南沃柑",
            category="水果",
            unit="斤",
            price=7.5,
            group_price=5.9,
            min_group_size=5,
            stock_total=400,
            daily_limit=150,
            description="皮薄多汁，甜度高"
        ),
        Product(
            name="东北有机大米",
            category="粮油",
            unit="袋(10斤)",
            price=59.9,
            group_price=49.9,
            min_group_size=2,
            stock_total=200,
            daily_limit=80,
            description="五常稻花香，颗粒饱满"
        ),
        Product(
            name="金龙鱼非转基因菜籽油",
            category="粮油",
            unit="桶(5L)",
            price=89.9,
            stock_total=150,
            daily_limit=50,
            description="物理压榨，非转基因"
        ),
        Product(
            name="伊利纯牛奶",
            category="乳品",
            unit="箱(250ml*24)",
            price=69.9,
            group_price=59.9,
            min_group_size=2,
            stock_total=250,
            daily_limit=100,
            description="优质牧场奶源"
        ),
        Product(
            name="土鸡蛋",
            category="蛋品",
            unit="盒(30枚)",
            price=35.9,
            group_price=29.9,
            min_group_size=2,
            stock_total=300,
            daily_limit=120,
            description="农家散养土鸡蛋"
        ),
        Product(
            name="有机蔬菜套装",
            category="蔬菜",
            unit="份(5种)",
            price=29.9,
            stock_total=200,
            daily_limit=80,
            description="当季新鲜有机蔬菜组合"
        ),
        Product(
            name="国产蓝鳍金枪鱼肉",
            category="肉禽",
            unit="斤",
            price=128.0,
            stock_total=80,
            daily_limit=30,
            description="深海捕捞，急冻保鲜"
        ),
        Product(
            name="进口车厘子(JJJ级)",
            category="水果",
            unit="斤",
            price=59.9,
            group_price=45.9,
            min_group_size=3,
            stock_total=200,
            daily_limit=80,
            description="智利进口，果大核小"
        ),
    ]

    for p in products:
        db.add(p)
    db.flush()


def seed_leaders(db: Session):
    if db.query(Leader).count() > 0:
        return

    leaders = [
        Leader(
            name="张阿姨便利店",
            phone="13800138001",
            pickup_address="浦东新区张江高科技园区博云路2号",
            district="浦东新区",
            latitude=31.2096,
            longitude=121.5970,
            commission_rate=0.05
        ),
        Leader(
            name="李叔百货店",
            phone="13800138002",
            pickup_address="浦东新区陆家嘴环路1000号",
            district="浦东新区",
            latitude=31.2397,
            longitude=121.4998,
            commission_rate=0.06
        ),
        Leader(
            name="王姐生鲜超市",
            phone="13800138003",
            pickup_address="徐汇区漕溪北路18号",
            district="徐汇区",
            latitude=31.1864,
            longitude=121.4365,
            commission_rate=0.05
        ),
        Leader(
            name="赵师傅水果店",
            phone="13800138004",
            pickup_address="徐汇区衡山路890号",
            district="徐汇区",
            latitude=31.2001,
            longitude=121.4450,
            commission_rate=0.05
        ),
        Leader(
            name="钱阿姨社区点",
            phone="13800138005",
            pickup_address="静安区南京西路1266号",
            district="静安区",
            latitude=31.2304,
            longitude=121.4536,
            commission_rate=0.06
        ),
        Leader(
            name="孙哥小超市",
            phone="13800138006",
            pickup_address="静安区共和新路1898号",
            district="静安区",
            latitude=31.2765,
            longitude=121.4500,
            commission_rate=0.05
        ),
        Leader(
            name="周姐烟杂店",
            phone="13800138007",
            pickup_address="杨浦区五角场邯郸路600号",
            district="杨浦区",
            latitude=31.2989,
            longitude=121.5101,
            commission_rate=0.05
        ),
        Leader(
            name="吴师傅提货点",
            phone="13800138008",
            pickup_address="虹口区四川北路1688号",
            district="虹口区",
            latitude=31.2605,
            longitude=121.4858,
            commission_rate=0.06
        ),
    ]

    for l in leaders:
        db.add(l)
    db.flush()


def seed_users(db: Session):
    if db.query(User).count() > 0:
        return

    users = [
        User(name="陈小明", phone="13900139001", default_leader_id=1),
        User(name="刘小红", phone="13900139002", default_leader_id=1),
        User(name="黄小华", phone="13900139003", default_leader_id=2),
        User(name="周小丽", phone="13900139004", default_leader_id=3),
        User(name="吴小强", phone="13900139005", default_leader_id=4),
        User(name="郑小燕", phone="13900139006", default_leader_id=5),
        User(name="孙小伟", phone="13900139007", default_leader_id=6),
        User(name="马小芳", phone="13900139008", default_leader_id=7),
        User(name="朱小杰", phone="13900139009", default_leader_id=8),
        User(name="胡小敏", phone="13900139010", default_leader_id=2),
    ]

    for u in users:
        db.add(u)
    db.flush()


def seed_coupons(db: Session):
    if db.query(Coupon).count() > 0:
        return

    now = datetime.utcnow()
    future = now + timedelta(days=30)

    coupons = [
        Coupon(
            name="新人专享5元券",
            coupon_type=CouponType.FIXED,
            value=5,
            min_order_amount=0,
            valid_from=now,
            valid_until=future,
            total_issued=1000,
            used_count=0,
            is_active=True
        ),
        Coupon(
            name="满50减10元券",
            coupon_type=CouponType.FIXED,
            value=10,
            min_order_amount=50,
            valid_from=now,
            valid_until=future,
            total_issued=2000,
            used_count=0,
            is_active=True
        ),
        Coupon(
            name="满100减20元券",
            coupon_type=CouponType.FIXED,
            value=20,
            min_order_amount=100,
            valid_from=now,
            valid_until=future,
            total_issued=1000,
            used_count=0,
            is_active=True
        ),
        Coupon(
            name="8折优惠券",
            coupon_type=CouponType.PERCENT,
            value=20,
            min_order_amount=30,
            valid_from=now,
            valid_until=future,
            total_issued=500,
            used_count=0,
            is_active=True
        ),
        Coupon(
            name="免运费券",
            coupon_type=CouponType.FREE_SHIPPING,
            value=0,
            min_order_amount=0,
            valid_from=now,
            valid_until=future,
            total_issued=1500,
            used_count=0,
            is_active=True
        ),
    ]

    for c in coupons:
        db.add(c)
    db.flush()

    user_coupons = []
    for user_id in range(1, 11):
        user_coupons.extend([
            UserCoupon(user_id=user_id, coupon_id=1, used=False),
            UserCoupon(user_id=user_id, coupon_id=2, used=False),
            UserCoupon(user_id=user_id, coupon_id=5, used=False),
        ])

    user_coupons.extend([
        UserCoupon(user_id=1, coupon_id=3, used=False),
        UserCoupon(user_id=1, coupon_id=4, used=False),
    ])

    for uc in user_coupons:
        db.add(uc)
    db.flush()


def seed_promotions(db: Session):
    if db.query(Promotion).count() > 0:
        return

    now = datetime.utcnow()
    future = now + timedelta(days=30)

    promotions = [
        Promotion(
            name="全场满减",
            promotion_type=PromotionType.FULL_REDUCTION,
            config=json.dumps({
                "thresholds": [
                    {"amount": 80, "discount": 8},
                    {"amount": 150, "discount": 20},
                    {"amount": 300, "discount": 50}
                ]
            }),
            valid_from=now,
            valid_until=future,
            is_active=True
        ),
        Promotion(
            name="水果第二件半价",
            promotion_type=PromotionType.SECOND_HALF_PRICE,
            config=json.dumps({
                "categories": ["水果"]
            }),
            valid_from=now,
            valid_until=future,
            is_active=True
        ),
    ]

    for p in promotions:
        db.add(p)
    db.flush()


def seed_vehicles(db: Session):
    if db.query(Vehicle).count() > 0:
        return

    vehicles = [
        Vehicle(
            plate_no="沪A·12345",
            driver_name="张师傅",
            driver_phone="13700137001",
            capacity=300,
            district="浦东新区"
        ),
        Vehicle(
            plate_no="沪A·23456",
            driver_name="李师傅",
            driver_phone="13700137002",
            capacity=300,
            district="浦东新区"
        ),
        Vehicle(
            plate_no="沪A·34567",
            driver_name="王师傅",
            driver_phone="13700137003",
            capacity=250,
            district="徐汇区"
        ),
        Vehicle(
            plate_no="沪A·45678",
            driver_name="赵师傅",
            driver_phone="13700137004",
            capacity=250,
            district="静安区"
        ),
        Vehicle(
            plate_no="沪A·56789",
            driver_name="钱师傅",
            driver_phone="13700137005",
            capacity=200,
            district="杨浦区"
        ),
        Vehicle(
            plate_no="沪A·67890",
            driver_name="孙师傅",
            driver_phone="13700137006",
            capacity=200,
            district="虹口区"
        ),
    ]

    for v in vehicles:
        db.add(v)
    db.flush()
