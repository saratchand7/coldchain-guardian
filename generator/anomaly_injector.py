"""Deterministic fault plan applied only after valid baseline readings exist."""
def injected_value(value,shipment_number,kind,index,high):
    if shipment_number%7==0 and 30<=index<=31:return high+4
    if (shipment_number%19==0 or shipment_number==12) and 60<=index<=73:return high+4
    if shipment_number==1 and kind=='TEMP' and index==45:return 150.0
    return value
def quality(value,kind):
    physical=(-80,100) if kind=='TEMP' else (0,100)
    return 'INVALID_PHYSICAL_RANGE' if not physical[0]<=value<=physical[1] else 'VALID'
