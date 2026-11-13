"""/api/clips reads the models folder chosen in Setup."""

from fastapi.testclient import TestClient

from server import settings
from server.app import app


def test_clips_come_from_the_chosen_models_folder(tmp_path, monkeypatch):
    """The server process has no EFFIGY_MODELS when the user chose the folder in Setup: /api/clips looked in
    the app's own models/ and found none ("No ready-made animations found")."""
    from steps import anim

    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.delenv("EFFIGY_MODELS", raising=False)
    settings.write(models=str(tmp_path / "F-models"))
    seen = []
    monkeypatch.setattr(anim, "catalogue", lambda t, root: seen.append(root) or [])
    TestClient(app).get("/api/clips")
    assert seen == [tmp_path / "F-models" / "clips"]
