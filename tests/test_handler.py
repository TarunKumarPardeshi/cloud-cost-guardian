from src.handler import lambda_handler

def test_demo_mode(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    assert lambda_handler({}, None)["status"] == "ok"
