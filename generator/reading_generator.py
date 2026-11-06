"""Reproducible, bounded-memory sensor reading helpers."""
def should_drop(shipment_number,reading_index): return shipment_number%13==0 and 80<reading_index<84
def normal_value(rng,low,high): return low+(high-low)*(0.1+0.8*rng.random())
def reading_id(sensor_id,at): return 'RDG-'+sensor_id+'-'+at.strftime('%Y%m%dT%H%M%SZ')
