from datetime import datetime
from sqlalchemy.orm import Session
from app.models import (
    DeliveryRoute, DeliveryStop, Vehicle, VehicleStatus,
    DeliveryStatus, Order, OrderStatus, Leader, SortingTask, SortingStatus
)
from app.modules.side_effect_registry import SideEffectRegistry
from app.modules.order_state_machine import StateTransitionError
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
import math
import random


class DispatchError(Exception):
    pass


class GeoCalculator:
    @staticmethod
    def distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return random.uniform(2, 15)

        R = 6371.0
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = math.sin(dlat / 2) ** 2 + \
            math.cos(lat1_rad) * math.cos(lat2_rad) * \
            math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return round(R * c, 2)

    @staticmethod
    def estimate_duration(distance_km: float, avg_speed_kmh: float = 30) -> float:
        return round((distance_km / avg_speed_kmh) * 60, 1)


class DispatchService:
    def __init__(self, db: Session):
        self.db = db
        self.geo = GeoCalculator()
        self.WAREHOUSE_LAT = 31.2304
        self.WAREHOUSE_LON = 121.4737
        self.registry = SideEffectRegistry(db)
        self.state_machine = self.registry.state_machine

    def get_available_vehicles(self, district: str = None) -> List[Vehicle]:
        query = self.db.query(Vehicle).filter(
            Vehicle.status == VehicleStatus.IDLE
        )
        if district:
            query = query.filter(Vehicle.district == district)
        return query.all()

    def get_dispatched_orders(self, warehouse_date: str) -> Dict[int, bool]:
        stops = self.db.query(DeliveryStop).filter(
            DeliveryRoute.warehouse_date == warehouse_date
        ).join(DeliveryRoute).all()
        return {stop.order_id: True for stop in stops}

    def get_ready_orders(self, warehouse_date: str) -> List[Order]:
        sorting_tasks = self.db.query(SortingTask).filter(
            SortingTask.warehouse_date == warehouse_date,
            SortingTask.status == SortingStatus.COMPLETED
        ).all()

        completed_task_ids = {st.order_id for st in sorting_tasks}

        dispatched = self.get_dispatched_orders(warehouse_date)

        orders = self.db.query(Order).filter(
            Order.warehouse_date == warehouse_date,
            Order.status == OrderStatus.SORTED
        ).all()

        return [
            o for o in orders
            if o.id in completed_task_ids and o.id not in dispatched
        ]

    def _group_orders_by_district(self, orders: List[Order]) -> Dict[str, List[Order]]:
        groups = defaultdict(list)
        for order in orders:
            leader = self.db.query(Leader).filter(Leader.id == order.leader_id).first()
            district = leader.district if leader else "未知区域"
            groups[district].append(order)
        return dict(groups)

    def _group_orders_by_leader(self, orders: List[Order]) -> Dict[int, List[Order]]:
        groups = defaultdict(list)
        for order in orders:
            groups[order.leader_id].append(order)
        return dict(groups)

    def _calculate_order_volume(self, order: Order) -> float:
        volume = 0.0
        for item in order.items:
            volume += item.qty * 0.5
        return round(volume, 2)

    def _optimize_stop_sequence(self, stops: List[Dict]) -> Tuple[List[Dict], float, float]:
        if not stops:
            return [], 0, 0

        current_lat, current_lon = self.WAREHOUSE_LAT, self.WAREHOUSE_LON
        unvisited = list(stops)
        ordered = []
        total_distance = 0.0

        while unvisited:
            nearest_idx = 0
            nearest_dist = float('inf')

            for i, stop in enumerate(unvisited):
                leader = self.db.query(Leader).filter(Leader.id == stop["leader_id"]).first()
                if leader:
                    d = self.geo.distance(current_lat, current_lon, leader.latitude, leader.longitude)
                else:
                    d = random.uniform(1, 10)

                if d < nearest_dist:
                    nearest_dist = d
                    nearest_idx = i

            next_stop = unvisited.pop(nearest_idx)
            next_stop["sequence"] = len(ordered) + 1
            ordered.append(next_stop)
            total_distance += nearest_dist

            leader = self.db.query(Leader).filter(Leader.id == next_stop["leader_id"]).first()
            if leader:
                current_lat, current_lon = leader.latitude, leader.longitude

        if ordered:
            total_distance += self.geo.distance(
                current_lat, current_lon,
                self.WAREHOUSE_LAT, self.WAREHOUSE_LON
            )

        total_duration = self.geo.estimate_duration(total_distance)
        return ordered, round(total_distance, 2), total_duration

    def create_delivery_routes(self, warehouse_date: str, strategy: str = "district",
                               operator: str = "dispatcher") -> List[DeliveryRoute]:
        ready_orders = self.get_ready_orders(warehouse_date)
        if not ready_orders:
            raise DispatchError("没有待配送的订单")

        vehicles = self.get_available_vehicles()
        if not vehicles:
            raise DispatchError("没有可用车辆")

        routes = []

        if strategy == "district":
            grouped_orders = self._group_orders_by_district(ready_orders)
        elif strategy == "leader":
            all_orders = ready_orders
            grouped_orders = {"综合": all_orders}
        else:
            grouped_orders = {"综合": ready_orders}

        vehicle_idx = 0
        all_orders_flat = []

        for group_name, orders in grouped_orders.items():
            all_orders_flat.extend([(group_name, o) for o in orders])

        grouped_by_leader = defaultdict(lambda: defaultdict(list))
        for group_name, order in all_orders_flat:
            grouped_by_leader[group_name][order.leader_id].append(order)

        stops_data = []
        for group_name, leader_groups in grouped_by_leader.items():
            for leader_id, orders in leader_groups.items():
                leader = self.db.query(Leader).filter(Leader.id == leader_id).first()
                total_volume = sum(self._calculate_order_volume(o) for o in orders)
                stop_info = {
                    "leader_id": leader_id,
                    "leader_name": leader.name if leader else "未知自提点",
                    "address": leader.pickup_address if leader else "未知地址",
                    "orders": orders,
                    "order_count": len(orders),
                    "volume": round(total_volume, 2),
                    "group": group_name
                }
                stops_data.append(stop_info)

        stops_by_group = defaultdict(list)
        for s in stops_data:
            stops_by_group[s["group"]].append(s)

        for group_name, stops in stops_by_group.items():
            if vehicle_idx >= len(vehicles):
                vehicle_idx = 0

            stop_chunks = self._split_stops_by_capacity(stops, vehicles[vehicle_idx].capacity)

            for chunk in stop_chunks:
                if vehicle_idx >= len(vehicles):
                    raise DispatchError("车辆不足，无法完成全部配送")

                vehicle = vehicles[vehicle_idx]
                route = self._create_route(
                    warehouse_date=warehouse_date,
                    group_name=group_name,
                    vehicle=vehicle,
                    stops_info=chunk,
                    operator=operator
                )
                routes.append(route)
                vehicle_idx += 1

        self.db.flush()
        return routes

    def _split_stops_by_capacity(self, stops: List[Dict], capacity: float) -> List[List[Dict]]:
        chunks = []
        current_chunk = []
        current_load = 0.0

        for stop in stops:
            if current_load + stop["volume"] > capacity and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_load = 0.0

            current_chunk.append(stop)
            current_load += stop["volume"]

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _create_route(self, warehouse_date: str, group_name: str, vehicle: Vehicle,
                      stops_info: List[Dict], operator: str) -> DeliveryRoute:
        optimized_stops, total_distance, total_duration = self._optimize_stop_sequence(stops_info)

        total_orders = sum(s["order_count"] for s in optimized_stops)
        total_volume = sum(s["volume"] for s in optimized_stops)

        route_name = f"{warehouse_date[5:]}-{group_name}-{vehicle.plate_no[-4:]}"
        route = DeliveryRoute(
            route_name=route_name,
            vehicle_id=vehicle.id,
            warehouse_date=warehouse_date,
            status=DeliveryStatus.PENDING,
            total_stops=len(optimized_stops),
            total_orders=total_orders,
            total_volume=round(total_volume, 2),
            estimated_distance=total_distance,
            estimated_duration=total_duration
        )
        self.db.add(route)
        self.db.flush()

        for stop_info in optimized_stops:
            for order in stop_info["orders"]:
                stop = DeliveryStop(
                    route_id=route.id,
                    order_id=order.id,
                    leader_id=stop_info["leader_id"],
                    stop_sequence=stop_info["sequence"],
                    stop_name=stop_info["leader_name"],
                    stop_address=stop_info["address"],
                    order_count=1,
                    volume=round(self._calculate_order_volume(order), 2)
                )
                self.db.add(stop)

                try:
                    self.state_machine.transition(
                        order, OrderStatus.DELIVERING,
                        operator=operator, remark="开始配送"
                    )
                except StateTransitionError:
                    pass

        vehicle.status = VehicleStatus.LOADING
        vehicle.current_load = total_volume

        return route

    def dispatch_route(self, route_id: int, operator: str = "dispatcher") -> DeliveryRoute:
        route = self.db.query(DeliveryRoute).filter(DeliveryRoute.id == route_id).first()
        if not route:
            raise DispatchError(f"配送路线不存在: {route_id}")
        if route.status != DeliveryStatus.PENDING:
            raise DispatchError(f"路线状态 {route.status.value} 不允许发车")

        vehicle = self.db.query(Vehicle).filter(Vehicle.id == route.vehicle_id).first()
        if vehicle:
            vehicle.status = VehicleStatus.DELIVERING

        route.status = DeliveryStatus.DISPATCHED
        route.dispatched_at = datetime.utcnow()

        stop_orders = defaultdict(list)
        for stop in route.stops:
            stop_orders[(stop.stop_sequence, stop.leader_id)].append(stop.order_id)

        for (seq, leader_id), order_ids in stop_orders.items():
            if len(order_ids) > 1:
                stop = self.db.query(DeliveryStop).filter(
                    DeliveryStop.route_id == route_id,
                    DeliveryStop.stop_sequence == seq,
                    DeliveryStop.leader_id == leader_id
                ).first()
                if stop:
                    stop.order_count = len(order_ids)
                    for oid in order_ids[1:]:
                        dup = self.db.query(DeliveryStop).filter(
                            DeliveryStop.route_id == route_id,
                            DeliveryStop.order_id == oid
                        ).first()
                        if dup and dup.id != stop.id:
                            stop.volume = round(stop.volume + dup.volume, 2)
                            self.db.delete(dup)

        self.db.flush()
        return route

    def stop_arrived(self, stop_id: int, operator: str = "driver") -> DeliveryStop:
        stop = self.db.query(DeliveryStop).filter(DeliveryStop.id == stop_id).first()
        if not stop:
            raise DispatchError(f"配送站点不存在: {stop_id}")

        route = self.db.query(DeliveryRoute).filter(DeliveryRoute.id == stop.route_id).first()
        if route.status != DeliveryStatus.DISPATCHED and route.status != DeliveryStatus.IN_TRANSIT:
            raise DispatchError(f"路线状态不允许到达")

        stop.arrived_at = datetime.utcnow()
        route.status = DeliveryStatus.IN_TRANSIT

        order = self.db.query(Order).filter(Order.id == stop.order_id).first()
        if order and order.status == OrderStatus.DELIVERING:
            try:
                self.state_machine.transition(
                    order, OrderStatus.DELIVERED,
                    operator=operator, remark="团长签收"
                )
            except StateTransitionError:
                pass

        same_seq_stops = self.db.query(DeliveryStop).filter(
            DeliveryStop.route_id == stop.route_id,
            DeliveryStop.stop_sequence == stop.stop_sequence
        ).all()
        for s in same_seq_stops:
            if s.id != stop.id and not s.arrived_at:
                s.arrived_at = stop.arrived_at
                o = self.db.query(Order).filter(Order.id == s.order_id).first()
                if o and o.status == OrderStatus.DELIVERING:
                    try:
                        self.state_machine.transition(
                            o, OrderStatus.DELIVERED,
                            operator=operator, remark="团长签收"
                        )
                    except StateTransitionError:
                        pass

        self.db.flush()
        return stop

    def stop_departed(self, stop_id: int, operator: str = "driver") -> DeliveryStop:
        stop = self.db.query(DeliveryStop).filter(DeliveryStop.id == stop_id).first()
        if not stop:
            raise DispatchError(f"配送站点不存在: {stop_id}")
        if not stop.arrived_at:
            raise DispatchError("站点尚未到达")

        stop.departed_at = datetime.utcnow()

        route = self.db.query(DeliveryRoute).filter(DeliveryRoute.id == stop.route_id).first()
        all_arrived = all(s.arrived_at for s in route.stops)
        all_departed = all(s.departed_at for s in route.stops)

        if all_arrived and all_departed:
            route.status = DeliveryStatus.ARRIVED
            route.arrived_at = datetime.utcnow()

            vehicle = self.db.query(Vehicle).filter(Vehicle.id == route.vehicle_id).first()
            if vehicle:
                vehicle.status = VehicleStatus.IDLE
                vehicle.current_load = 0

        self.db.flush()
        return stop

    def get_route_details(self, route_id: int) -> Optional[Dict]:
        route = self.db.query(DeliveryRoute).filter(DeliveryRoute.id == route_id).first()
        if not route:
            return None

        vehicle = self.db.query(Vehicle).filter(Vehicle.id == route.vehicle_id).first()

        stops_grouped = defaultdict(list)
        for stop in route.stops:
            stops_grouped[(stop.stop_sequence, stop.leader_id)].append(stop)

        merged_stops = []
        for (seq, lid), stop_list in stops_grouped.items():
            first = stop_list[0]
            order_ids = [s.order_id for s in stop_list]
            orders = self.db.query(Order).filter(Order.id.in_(order_ids)).all()

            merged_stops.append({
                "sequence": seq,
                "leader_id": lid,
                "stop_name": first.stop_name,
                "stop_address": first.stop_address,
                "order_count": len(order_ids),
                "volume": round(sum(s.volume for s in stop_list), 2),
                "orders": [
                    {
                        "id": o.id,
                        "order_no": o.order_no,
                        "status": o.status.value,
                        "items": [f"{i.product_name}x{i.qty}" for i in o.items]
                    }
                    for o in orders
                ],
                "arrived_at": first.arrived_at.isoformat() if first.arrived_at else None,
                "departed_at": first.departed_at.isoformat() if first.departed_at else None
            })

        merged_stops.sort(key=lambda x: x["sequence"])

        return {
            "id": route.id,
            "route_name": route.route_name,
            "warehouse_date": route.warehouse_date,
            "status": route.status.value,
            "status_text": self._status_text(route.status),
            "vehicle": {
                "id": vehicle.id,
                "plate_no": vehicle.plate_no,
                "driver": vehicle.driver_name,
                "phone": vehicle.driver_phone,
                "capacity": vehicle.capacity
            } if vehicle else None,
            "stats": {
                "total_stops": len(merged_stops),
                "total_orders": route.total_orders,
                "total_volume": route.total_volume,
                "estimated_distance_km": route.estimated_distance,
                "estimated_duration_min": route.estimated_duration
            },
            "stops": merged_stops,
            "timestamps": {
                "dispatched_at": route.dispatched_at.isoformat() if route.dispatched_at else None,
                "arrived_at": route.arrived_at.isoformat() if route.arrived_at else None,
                "created_at": route.created_at.isoformat()
            }
        }

    def get_dispatch_summary(self, warehouse_date: str) -> Dict:
        routes = self.db.query(DeliveryRoute).filter(
            DeliveryRoute.warehouse_date == warehouse_date
        ).all()

        ready_count = len(self.get_ready_orders(warehouse_date))

        status_counts = defaultdict(int)
        total_routes = len(routes)
        total_stops = 0
        total_orders = 0
        total_volume = 0.0

        for r in routes:
            status_counts[r.status.value] += 1
            total_stops += r.total_stops
            total_orders += r.total_orders
            total_volume += r.total_volume

        vehicles = self.db.query(Vehicle).all()
        vehicle_status = defaultdict(int)
        for v in vehicles:
            vehicle_status[v.status.value] += 1

        return {
            "warehouse_date": warehouse_date,
            "ready_orders": ready_count,
            "total_routes": total_routes,
            "total_stops": total_stops,
            "total_orders_in_routes": total_orders,
            "total_volume": round(total_volume, 2),
            "routes_by_status": dict(status_counts),
            "vehicles": {
                "total": len(vehicles),
                "by_status": dict(vehicle_status)
            }
        }

    def list_routes(self, warehouse_date: str = None, status: str = None) -> List[Dict]:
        query = self.db.query(DeliveryRoute)
        if warehouse_date:
            query = query.filter(DeliveryRoute.warehouse_date == warehouse_date)
        if status:
            query = query.filter(DeliveryRoute.status == status)

        routes = query.order_by(DeliveryRoute.id.desc()).all()

        result = []
        for r in routes:
            vehicle = self.db.query(Vehicle).filter(Vehicle.id == r.vehicle_id).first()
            result.append({
                "id": r.id,
                "route_name": r.route_name,
                "warehouse_date": r.warehouse_date,
                "status": r.status.value,
                "status_text": self._status_text(r.status),
                "vehicle_plate": vehicle.plate_no if vehicle else "-",
                "driver": vehicle.driver_name if vehicle else "-",
                "total_stops": r.total_stops,
                "total_orders": r.total_orders,
                "estimated_distance_km": r.estimated_distance,
                "dispatched_at": r.dispatched_at.isoformat() if r.dispatched_at else None
            })

        return result

    def _status_text(self, status) -> str:
        mapping = {
            DeliveryStatus.PENDING: "待发车",
            DeliveryStatus.DISPATCHED: "已发车",
            DeliveryStatus.IN_TRANSIT: "配送中",
            DeliveryStatus.ARRIVED: "已完成"
        }
        return mapping.get(status, status.value if hasattr(status, 'value') else str(status))
