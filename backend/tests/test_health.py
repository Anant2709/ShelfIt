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
        "/api/chat/stream",
        "/api/chat/conversations",
        "/api/chat/conversations/{conversation_id}",
        "/api/analytics/waste",
        "/api/inventory/{item_id}/dispositions",
        "/api/inventory/{item_id}/dispositions/{disposition_id}",
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/providers",
        "/api/auth/google",
        "/api/auth/google/callback",
        "/api/diet/questionnaire",
        "/api/diet/profile",
        "/api/diet/plan",
        "/api/diet/today",
        "/api/diet/log",
        "/api/diet/adherence",
        "/api/diet/weigh-ins",
        "/api/diet/progress",
    ]:
        assert expected in paths, f"missing route {expected}"


def test_health_is_public(anonymous_client):
    """Liveness must not require a session, or a restart cannot be probed."""
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
