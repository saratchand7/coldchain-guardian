"""Deterministic shipment and trip timeline helpers."""
from datetime import timedelta
def shipment_id(number): return f'SHP-2026-{number:06d}'
def trip_end(start,duration_hours): return start+timedelta(hours=duration_hours)
def route_time(start,end,sequence): return start+(end-start)*sequence/5
def final_status(active,delayed): return 'DELAYED' if active and delayed else ('IN_TRANSIT' if active else 'DELIVERED')
