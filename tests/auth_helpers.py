"""Real authentication for isolated test workspaces; never a production bypass."""


def sign_in(client):
    body = {"email": "admin@example.test", "password": "test-workspace-password"}
    if client.get("/auth/status").json()["setup_required"]:
        response = client.post("/auth/setup", json={**body, "tenant_id": "acme"})
    else:
        response = client.post("/auth/login", json=body)
    assert response.status_code == 200, response.text
    return response.json()["user"]
