from database import initialize
from services.validation import validate
def test_empty_database_integrity(tmp_path):
 p=tmp_path/'v.db';initialize(p);result=validate(p);assert result['status']=='PASS' and result['passed_checks']==result['total_checks'] and result['total_checks']>=14
