"""Illustrative master records and parent-first insertion."""
PRODUCTS=[
 ('PRD-001','Vaccine A',2,8,30,60,15),('PRD-002','Medicine B',15,25,30,65,30),('PRD-003','Frozen Product C',-25,-15,20,50,10),
 ('PRD-004','Diagnostic Kit D',2,10,20,65,20),('PRD-005','Biologic E',2,8,25,55,15),
 ('PRD-006','Reagent F',2,12,20,60,25),('PRD-007','Sample Media G',-10,4,15,55,20),
 ('PRD-008','Laboratory Supply H',10,24,20,70,30),('PRD-009','Thermal Material I',-5,5,15,50,15),
 ('PRD-010','Research Material J',5,18,25,65,20)]

def insert_master_data(db,container_count):
    for product in PRODUCTS:
        db.execute('INSERT INTO Product VALUES(?,?,?,?,?,?,?)',product)
        db.execute('INSERT INTO ThresholdPolicy VALUES(?,?,?,?,?,?,?,?,?)',(f'POL-{product[0]}',product[0],'2026-01-01T00:00:00+00:00',None,*product[2:]))
    for i in range(1,container_count+1):
        container=f'CNT-{i:06d}'
        db.execute("INSERT INTO Container VALUES(?,?,'AVAILABLE')",(container,'REFRIGERATED'))
        for kind in ('TEMP','HUM'):
            calibration='VALID'
            db.execute('INSERT INTO Sensor VALUES(?,?,?,?,?)',(f'SNS-{container}-{kind}',container,kind,'2026-01-01T00:00:00+00:00',calibration))
