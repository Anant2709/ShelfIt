def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_is_served(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Shelf It API"


def test_expected_routes_are_registered(client):
    paths = client.get("/openapi.json").json()["paths"]
    for expected in [
        "/api/inventory/",
        "/api/inventory/scan",
        "/api/inventory/label",
        "/api/inventory/reminders",
        "/api/inventory/{item_id}",
        "/api/inventory/{item_id}/expiration",
        "/api/chat/",
        "/api/analytics/waste",
        "/api/inventory/{item_id}/dispositions",
    ]:
        assert expected in paths, f"missing route {expected}"
