from app import app

def test_dashboard_and_required_pages_load():
    client=app.test_client()
    for route in ('/','/shipments','/monitoring','/routes','/excursions','/actions','/custody','/reports'):
        assert client.get(route).status_code==200
    assert client.get('/api/dashboard').status_code==200
    assert client.get('/api/metrics').status_code==200
    assert client.get('/api/routes').status_code==200
    assert client.get('/api/custody').status_code==200

def test_streaming_exports_are_valid():
    client=app.test_client()
    response=client.get('/api/export/shipments.json')
    assert response.status_code==200 and response.mimetype=='application/json'
    assert response.data.startswith(b'[') and response.data.endswith(b']')
    csv_response=client.get('/api/export/readings.csv',buffered=False)
    first_chunk=next(csv_response.response)
    assert csv_response.status_code==200 and b'reading_id,sensor_id' in first_chunk
    csv_response.close()
