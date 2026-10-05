from conftest import PASSWORD


async def test_login_returns_tokens_and_me(client, tenants):
    u = tenants.a.users["manager"]
    r = await client.post("/api/auth/login", json={"email": u["email"], "password": PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    data = me.json()
    assert data["id"] == u["id"]
    assert data["tenant_id"] == tenants.a.id
    assert data["role"] == "manager"
    assert data["last_login_at"] is not None


async def test_login_wrong_password(client, tenants):
    u = tenants.a.users["operator"]
    r = await client.post("/api/auth/login", json={"email": u["email"], "password": "wrong-password"})
    assert r.status_code == 401


async def test_login_unknown_email(client):
    r = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert r.status_code == 401


async def test_refresh_flow(client, tenants):
    u = tenants.b.users["admin"]
    r = await client.post("/api/auth/login", json={"email": u["email"], "password": PASSWORD})
    tokens = r.json()

    r = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200, r.text
    new = r.json()
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 200
    assert me.json()["tenant_id"] == tenants.b.id


async def test_access_token_cannot_be_used_as_refresh(client, tenants):
    u = tenants.a.users["admin"]
    r = await client.post("/api/auth/login", json={"email": u["email"], "password": PASSWORD})
    access = r.json()["access_token"]
    r = await client.post("/api/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401


async def test_me_requires_valid_token(client):
    assert (await client.get("/api/auth/me")).status_code == 401
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_suspended_tenant_cannot_login(client, db):
    import uuid
    from src.auth.security import hash_password
    from src.models import Tenant, User, UserRole

    t = Tenant(name="Suspensa", slug=f"susp-{uuid.uuid4().hex[:8]}", is_active=False,
               suspended_reason="Pagamento pendente")
    db.add(t)
    await db.flush()
    email = f"susp.{uuid.uuid4().hex[:8]}@example.com"
    db.add(User(tenant_id=t.id, email=email, password_hash=hash_password(PASSWORD),
                name="x", role=UserRole.admin))
    await db.commit()
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 403
    assert "Pagamento pendente" in r.json()["detail"]
