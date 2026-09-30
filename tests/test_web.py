import re

from fastapi.testclient import TestClient

from gzp_finance.web import create_app
from test_importers import MI


def client(engine):
    return TestClient(create_app(engine, username="test", password="synthetic-test-password"))


def test_auth_csrf_and_review_flow(engine):
    c = client(engine)
    assert c.get("/").status_code == 401
    c.auth = ("test", "synthetic-test-password")
    index = c.get("/")
    assert index.status_code == 200 and index.headers["cache-control"] == "no-store"
    assert index.text.count('<select id="target_') == 5
    assert index.text.count('class="suggestions" role="listbox"') == 3
    token = re.search(r'name="csrf-token" content="([^"]+)"', index.text)[1]
    assert c.post("/api/imports", data={"bank": "MyInvestor"}, files={"file": ("a.csv", MI)}).status_code == 403
    c.headers["x-csrf-token"] = token
    assert c.post("/api/imports", data={"bank": "MyInvestor"}, files={"file": ("a.csv", MI)}).json()["inserted"] == 1
    tx = c.get("/api/transactions").json()["items"][0]
    assert tx["status"] == "sin clasificar"
    result = c.post(f"/api/transactions/{tx['id']}/confirm", json={"values": {"target_subtipo": "Fund"}, "revision": tx["revision"], "training": True, "rule_mode": "concept"})
    assert result.status_code == 200
    assert c.get("/api/transactions?status=confirmed").json()["total"] == 1
    assert c.get("/api/vocabulary").json()["fields"]["target_subtipo"] == ["Fund"]
    rule = c.get("/api/rules").json()[0]
    assert rule["source"] == "user_confirmed"
    assert c.post("/api/reclassify", headers={"origin": "https://attacker.invalid"}).status_code == 403


def test_invalid_import_is_atomic(engine):
    c = client(engine); c.auth = ("test", "synthetic-test-password")
    token = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").text)[1]
    c.headers["x-csrf-token"] = token
    bad = MI + "bad;bad;Example;100;EUR\n"
    assert c.post("/api/imports", data={"bank": "MyInvestor"}, files={"file": ("a.csv", bad)}).status_code == 422
    assert c.get("/api/transactions").json()["total"] == 0


def test_large_import_keeps_other_requests_responsive(engine, monkeypatch):
    import asyncio
    import threading
    import time
    import httpx
    import gzp_finance.web as web
    entered, release = threading.Event(), threading.Event()

    def slow_import(*args, **kwargs):
        entered.set()
        release.wait(3)
        return {"inserted": 0}

    monkeypatch.setattr(web, "import_statement", slow_import)

    async def exercise():
        app=create_app(engine, username="test", password="synthetic-test-password")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", auth=("test","synthetic-test-password")) as c:
            index=await c.get("/")
            token=re.search(r'name="csrf-token" content="([^"]+)"',index.text)[1]
            started=time.monotonic()
            upload=asyncio.create_task(c.post("/api/imports",headers={"x-csrf-token":token},data={"bank":"MyInvestor"},files={"file":("a.csv",MI)}))
            try:
                assert await asyncio.to_thread(entered.wait,1)
                response=await asyncio.wait_for(c.get("/api/validation"),1)
                assert response.status_code == 200
                assert time.monotonic()-started < 2
            finally:
                release.set()
                await upload
    asyncio.run(exercise())
