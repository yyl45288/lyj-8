from datetime import datetime
from sqlalchemy.orm import Session
from app.models import (
    Product, Coupon, UserCoupon, Promotion, PromotionType, CouponType
)
import json
from typing import List, Dict, Optional, Tuple


class PromotionDiscount:
    def __init__(self, promotion_id: int, name: str, discount_type: str,
                 discount_amount: float, affected_items: List[int]):
        self.promotion_id = promotion_id
        self.name = name
        self.discount_type = discount_type
        self.discount_amount = discount_amount
        self.affected_items = affected_items


class CouponDiscount:
    def __init__(self, user_coupon_id: int, name: str, coupon_type: str,
                 discount_amount: float, free_shipping: bool = False):
        self.user_coupon_id = user_coupon_id
        self.name = name
        self.coupon_type = coupon_type
        self.discount_amount = discount_amount
        self.free_shipping = free_shipping


class PromotionEngine:
    def __init__(self, db: Session):
        self.db = db

    def _get_active_promotions(self) -> List[Promotion]:
        now = datetime.utcnow()
        promotions = self.db.query(Promotion).filter(
            Promotion.is_active == True
        ).all()

        active = []
        for p in promotions:
            if p.valid_from and now < p.valid_from:
                continue
            if p.valid_until and now > p.valid_until:
                continue
            active.append(p)

        return active

    def _apply_full_reduction(self, promotion: Promotion, items: List[Dict]) -> PromotionDiscount:
        config = json.loads(promotion.config)
        thresholds = sorted(config["thresholds"], key=lambda x: x["amount"])

        subtotal = sum(it["unit_price"] * it["qty"] for it in items)
        best_threshold = None

        for th in thresholds:
            if subtotal >= th["amount"]:
                best_threshold = th

        if not best_threshold:
            return None

        discount = best_threshold["discount"]
        affected_item_ids = [it["product_id"] for it in items]
        discount_per_item = self._allocate_discount(items, discount, subtotal)

        for i, it in enumerate(items):
            it["promotion_discount"] = it.get("promotion_discount", 0) + discount_per_item[i]
            it["promotion_note"] = promotion.name

        return PromotionDiscount(
            promotion_id=promotion.id,
            name=promotion.name,
            discount_type="full_reduction",
            discount_amount=discount,
            affected_items=affected_item_ids
        )

    def _apply_second_half_price(self, promotion: Promotion, items: List[Dict]) -> PromotionDiscount:
        config = json.loads(promotion.config)
        target_product_ids = set(config.get("product_ids", []))
        categories = set(config.get("categories", []))

        total_discount = 0.0
        affected_item_ids = []

        for it in items:
            product = self.db.query(Product).filter(Product.id == it["product_id"]).first()
            if not product:
                continue

            is_target = False
            if target_product_ids and it["product_id"] in target_product_ids:
                is_target = True
            if categories and product.category in categories:
                is_target = True
            if not target_product_ids and not categories:
                is_target = True

            if not is_target:
                continue

            qty = it["qty"]
            if qty >= 2:
                half_price_qty = qty // 2
                item_discount = half_price_qty * it["unit_price"] * 0.5
                it["promotion_discount"] = it.get("promotion_discount", 0) + item_discount
                it["promotion_note"] = f"{promotion.name}(第{half_price_qty * 2 - 1}件半价)"
                total_discount += item_discount
                affected_item_ids.append(it["product_id"])

        if total_discount <= 0:
            return None

        return PromotionDiscount(
            promotion_id=promotion.id,
            name=promotion.name,
            discount_type="second_half_price",
            discount_amount=total_discount,
            affected_items=affected_item_ids
        )

    def _apply_group_buy(self, items: List[Dict]) -> PromotionDiscount:
        total_discount = 0.0
        affected_item_ids = []

        for it in items:
            product = self.db.query(Product).filter(Product.id == it["product_id"]).first()
            if not product or not product.group_price or product.min_group_size is None:
                continue

            if it["qty"] >= product.min_group_size:
                original_price = it["unit_price"]
                group_price = product.group_price
                item_discount = (original_price - group_price) * it["qty"]
                it["unit_price"] = group_price
                it["promotion_discount"] = it.get("promotion_discount", 0) + item_discount
                it["promotion_note"] = f"拼团价(≥{product.min_group_size}{product.unit})"
                total_discount += item_discount
                affected_item_ids.append(it["product_id"])

        if total_discount <= 0:
            return None

        return PromotionDiscount(
            promotion_id=0,
            name="拼团购",
            discount_type="group_buy",
            discount_amount=total_discount,
            affected_items=affected_item_ids
        )

    def _allocate_discount(self, items: List[Dict], total_discount: float,
                           subtotal: float) -> List[float]:
        if subtotal <= 0 or total_discount <= 0:
            return [0.0] * len(items)

        allocated = []
        remaining = total_discount
        total_items = sum(it["qty"] for it in items)

        for i, it in enumerate(items):
            if i == len(items) - 1:
                allocated.append(round(remaining, 2))
                break

            item_share = (it["unit_price"] * it["qty"] / subtotal) * total_discount
            item_share = round(item_share, 2)
            allocated.append(item_share)
            remaining -= item_share

        return allocated

    def apply_promotions(self, items: List[Dict]) -> Tuple[List[Dict], List[PromotionDiscount], float]:
        processed_items = []
        for it in items:
            pi = dict(it)
            pi["promotion_discount"] = 0
            pi["promotion_note"] = None
            processed_items.append(pi)

        applied_discounts = []

        gb = self._apply_group_buy(processed_items)
        if gb:
            applied_discounts.append(gb)

        promotions = self._get_active_promotions()
        for promo in promotions:
            if promo.promotion_type == PromotionType.FULL_REDUCTION:
                result = self._apply_full_reduction(promo, processed_items)
            elif promo.promotion_type == PromotionType.SECOND_HALF_PRICE:
                result = self._apply_second_half_price(promo, processed_items)
            else:
                result = None

            if result:
                applied_discounts.append(result)

        total_promotion_discount = sum(d.discount_amount for d in applied_discounts)

        for it in processed_items:
            promo_disc = it.get("promotion_discount", 0)
            it["actual_price"] = it["unit_price"]
            it["subtotal"] = round(it["unit_price"] * it["qty"], 2)
            it["final_subtotal"] = round(it["subtotal"] - promo_disc, 2)

        return processed_items, applied_discounts, total_promotion_discount


class CouponEngine:
    def __init__(self, db: Session):
        self.db = db

    def get_available_coupons(self, user_id: int, order_amount: float) -> List[Dict]:
        now = datetime.utcnow()
        user_coupons = self.db.query(UserCoupon).filter(
            UserCoupon.user_id == user_id,
            UserCoupon.used == False
        ).all()

        available = []
        for uc in user_coupons:
            coupon = self.db.query(Coupon).filter(Coupon.id == uc.coupon_id).first()
            if not coupon or not coupon.is_active:
                continue

            if coupon.valid_from and now < coupon.valid_from:
                continue
            if coupon.valid_until and now > coupon.valid_until:
                continue

            if order_amount < coupon.min_order_amount:
                continue

            discount_info = self._calculate_coupon_discount(coupon, order_amount)
            available.append({
                "user_coupon_id": uc.id,
                "coupon_id": coupon.id,
                "name": coupon.name,
                "type": coupon.coupon_type.value,
                "discount_amount": discount_info["discount_amount"],
                "free_shipping": discount_info["free_shipping"],
                "min_order_amount": coupon.min_order_amount,
                "valid_until": coupon.valid_until.strftime("%Y-%m-%d") if coupon.valid_until else "永久有效"
            })

        available.sort(key=lambda x: x["discount_amount"], reverse=True)
        return available

    def _calculate_coupon_discount(self, coupon: Coupon, order_amount: float) -> Dict:
        if coupon.coupon_type == CouponType.FIXED:
            return {
                "discount_amount": min(coupon.value, order_amount),
                "free_shipping": False
            }
        elif coupon.coupon_type == CouponType.PERCENT:
            pct = min(coupon.value, 100) / 100
            return {
                "discount_amount": round(order_amount * pct, 2),
                "free_shipping": False
            }
        elif coupon.coupon_type == CouponType.FREE_SHIPPING:
            return {
                "discount_amount": 0,
                "free_shipping": True
            }

        return {"discount_amount": 0, "free_shipping": False}

    def apply_coupon(self, user_coupon_id: int, user_id: int,
                     order_amount: float, shipping_fee: float) -> Optional[CouponDiscount]:
        uc = self.db.query(UserCoupon).filter(
            UserCoupon.id == user_coupon_id,
            UserCoupon.user_id == user_id,
            UserCoupon.used == False
        ).first()

        if not uc:
            return None

        coupon = self.db.query(Coupon).filter(Coupon.id == uc.coupon_id).first()
        if not coupon or not coupon.is_active:
            return None

        now = datetime.utcnow()
        if coupon.valid_from and now < coupon.valid_from:
            return None
        if coupon.valid_until and now > coupon.valid_until:
            return None

        if order_amount < coupon.min_order_amount:
            return None

        discount_info = self._calculate_coupon_discount(coupon, order_amount)
        actual_discount = min(discount_info["discount_amount"], order_amount)

        return CouponDiscount(
            user_coupon_id=uc.id,
            name=coupon.name,
            coupon_type=coupon.coupon_type.value,
            discount_amount=actual_discount,
            free_shipping=discount_info["free_shipping"]
        )

    def mark_coupon_used(self, user_coupon_id: int, order_id: int) -> bool:
        uc = self.db.query(UserCoupon).filter(
            UserCoupon.id == user_coupon_id,
            UserCoupon.used == False
        ).first()

        if not uc:
            return False

        uc.used = True
        uc.used_order_id = order_id
        uc.used_at = datetime.utcnow()

        coupon = self.db.query(Coupon).filter(Coupon.id == uc.coupon_id).first()
        if coupon:
            coupon.used_count += 1

        self.db.flush()
        return True

    def unmark_coupon_used(self, user_coupon_id: int) -> bool:
        uc = self.db.query(UserCoupon).filter(
            UserCoupon.id == user_coupon_id
        ).first()

        if not uc or not uc.used:
            return False

        uc.used = False
        uc.used_order_id = None
        uc.used_at = None

        coupon = self.db.query(Coupon).filter(Coupon.id == uc.coupon_id).first()
        if coupon and coupon.used_count > 0:
            coupon.used_count -= 1

        self.db.flush()
        return True


class PricingService:
    def __init__(self, db: Session):
        self.db = db
        self.promotion_engine = PromotionEngine(db)
        self.coupon_engine = CouponEngine(db)

    def calculate_price(self, items: List[Dict], user_id: int = None,
                        user_coupon_id: int = None,
                        shipping_fee: float = 5.0) -> Dict:
        processed_items, promo_discounts, total_promo_discount = \
            self.promotion_engine.apply_promotions(items)

        goods_amount = round(sum(it["unit_price"] * it["qty"] for it in processed_items), 2)
        after_promo_amount = round(goods_amount - total_promo_discount, 2)
        after_promo_amount = max(0, after_promo_amount)

        coupon_discount_obj = None
        coupon_discount_amount = 0.0
        free_shipping = False

        if user_id and user_coupon_id:
            coupon_discount_obj = self.coupon_engine.apply_coupon(
                user_coupon_id, user_id, after_promo_amount, shipping_fee
            )
            if coupon_discount_obj:
                coupon_discount_amount = coupon_discount_obj.discount_amount
                free_shipping = coupon_discount_obj.free_shipping

        after_coupon_amount = round(after_promo_amount - coupon_discount_amount, 2)
        after_coupon_amount = max(0, after_coupon_amount)

        actual_shipping = 0 if free_shipping else shipping_fee

        if after_coupon_amount >= 30:
            actual_shipping = 0

        order_amount = round(after_coupon_amount + actual_shipping, 2)

        final_items = []
        for it in processed_items:
            promo_disc = it.get("promotion_discount", 0)
            final_items.append({
                "product_id": it["product_id"],
                "product_name": it.get("product_name", ""),
                "unit_price": it["unit_price"],
                "actual_price": round(it["unit_price"] - (promo_disc / it["qty"] if it["qty"] > 0 else 0), 2),
                "qty": it["qty"],
                "subtotal": it["subtotal"],
                "discount_amount": round(promo_disc, 2),
                "promotion_note": it.get("promotion_note")
            })

        result = {
            "items": final_items,
            "goods_amount": goods_amount,
            "promotion_discount": round(total_promo_discount, 2),
            "promotion_details": [
                {"name": d.name, "amount": d.discount_amount, "type": d.discount_type}
                for d in promo_discounts
            ],
            "coupon_discount": round(coupon_discount_amount, 2),
            "coupon_detail": {
                "name": coupon_discount_obj.name,
                "amount": coupon_discount_amount,
                "type": coupon_discount_obj.coupon_type
            } if coupon_discount_obj else None,
            "shipping_fee": actual_shipping,
            "shipping_original": shipping_fee,
            "free_shipping": free_shipping or (after_coupon_amount >= 30),
            "order_amount": order_amount
        }

        return result
