"""M35c: the first run: the machine check, the models folder (chosen, switched, moved), the presets' sizes,
and the Hugging Face login, whose token must end up in huggingface_hub's store and nowhere else."""

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import install, jobs, machine, models, settings
from server.app import app
from server.library import ApiError

GOOD = "hf_" + "a" * 34
FAKE_HUB = f"""
import os, pathlib
from huggingface_hub import errors
STORE = pathlib.Path(os.environ["FAKE_HF_STORE"])

def get_token():
    return STORE.read_text() if STORE.exists() else None

class HfApi:
    def whoami(self, token=None):
        if (token or get_token()) != "{GOOD}":
            raise PermissionError("401 Client Error: Unauthorized")
        return {{"name": "tester"}}

    def auth_check(self, repo):
        if "llama" in repo:
            raise errors.GatedRepoError(repo)

def whoami(token=None):
    return HfApi().whoami(token)

def login(token, add_to_git_credential=False):
    STORE.write_text(token)

def logout():
    STORE.unlink(missing_ok=True)
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A settings file of its own, no EFFIGY_MODELS, one model entry `Fx` with one file."""
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.delenv("EFFIGY_MODELS", raising=False)
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setattr(settings, "ROOT", tmp_path / "app")
    entry = {
        "name": "a/fx",
        "groups": ["fx"],
        "revision": "r",
        "where": "Fx",
        "filters": {},
        "hub": False,
        "key": "k",
    }
    monkeypatch.setattr(models, "entries", lambda: [entry])
    monkeypatch.setattr(models, "MANIFEST_ROWS", {"k": {"w.bin": [5, None]}})
    return tmp_path


def put_models(folder):
    (folder / "Fx").mkdir(parents=True)
    (folder / "Fx" / "w.bin").write_bytes(b"12345")


def wait(job, timeout=20):
    end = time.time() + timeout
    while job.status in ("queued", "running"):
        assert time.time() < end, job.public()
        time.sleep(0.02)
    return job


def test_the_models_folder_comes_from_env_settings_or_default(home, monkeypatch):
    assert models.root() == home / "app" / "models" and settings.models_source() == "default"
    settings.write(models=str(home / "chosen"))
    assert models.root() == home / "chosen" and settings.models_source() == "settings"
    assert jobs._env()["EFFIGY_MODELS"] == str(home / "chosen")  # the steps get it too
    assert json.loads((home / "settings.json").read_text()) == {"models": str(home / "chosen")}
    monkeypatch.setenv("EFFIGY_MODELS", str(home / "env"))
    assert models.root() == home / "env"
    with pytest.raises(ApiError) as e:
        machine.choose_folder(str(home / "elsewhere"))
    assert e.value.status == 409


def test_choosing_a_folder(home):
    new = home / "big drive" / "models"
    with pytest.raises(ApiError):
        machine.choose_folder("relative/models")
    with pytest.raises(ApiError):
        machine.choose_folder(str(home / "library" / "models"))  # inside the library
    (home / "busy").mkdir()
    (home / "busy" / "notes.txt").write_text("mine")
    with pytest.raises(ApiError) as e:
        machine.choose_folder(str(home / "busy"))  # someone's other files
    assert e.value.code == "not_empty"
    assert machine.choose_folder(str(new)) == {
        "path": str(new),
        "job_id": None,
        "left": None,
    }  # nothing to carry over
    assert models.root() == new
    other = home / "other install"
    put_models(other)
    put_models(new)
    assert (
        machine.choose_folder(str(other))["job_id"] is None
    )  # a models folder already: just use it
    assert models.root() == other and (new / "Fx" / "w.bin").exists()


@pytest.mark.parametrize("across", [False, True])
def test_moving_the_models(home, monkeypatch, across):
    old, new = home / "app" / "models", home / "ssd" / "models"
    put_models(old)
    if across:  # another drive: a folder cannot be renamed there, the files are copied
        real = install.os.replace

        def replace(a, b):
            if Path(a) == old:  # the staging folder beside `new` renames fine
                raise OSError(18, "cross-device link")
            real(a, b)

        monkeypatch.setattr(install.os, "replace", replace)
    r = machine.choose_folder(str(new))
    job = wait(jobs.JOBS[r["job_id"]])
    assert job.status == "done", job.error
    assert (new / "Fx" / "w.bin").read_bytes() == b"12345" and not old.exists()
    assert models.root() == new and all(row["installed"] for row in models.listing())
    assert not jobs.GPU.locked()


def test_presets_count_shared_files_once(home, monkeypatch):
    shared = {
        "name": "a/s",
        "groups": ["fx", "fy"],
        "revision": "r",
        "where": "S",
        "filters": {},
        "hub": False,
        "key": "s",
    }
    own = {
        "name": "a/fx",
        "groups": ["fx"],
        "revision": "r",
        "where": "Fx",
        "filters": {},
        "hub": False,
        "key": "k",
    }
    monkeypatch.setattr(models, "entries", lambda: [own, shared])
    monkeypatch.setattr(
        models, "MANIFEST_ROWS", {"k": {"w.bin": [5, None]}, "s": {"s.bin": [100, None]}}
    )
    monkeypatch.setattr(install, "PRESETS", [("both", "Both", "", ["fx", "fy"])])
    put_models(home / "app" / "models")
    assert install.presets() == [
        {
            "id": "both",
            "name": "Both",
            "what": "",
            "groups": ["fx", "fy"],
            "bytes": 105,
            "missing": 100,
        }
    ]


def test_the_machine_check_says_what_to_do(monkeypatch):
    monkeypatch.setattr(machine, "_gpu", lambda: ("RTX 4060", 8.0, "560.94"))
    monkeypatch.setattr(machine, "_ram_gb", lambda: 16.0)
    monkeypatch.setattr(machine, "_long_paths", lambda: False)
    # an NVIDIA machine, even when the test runs on an AMD one
    monkeypatch.setattr(machine.gpu_amd, "active", lambda: False)
    levels = {c["id"]: c["level"] for c in machine.check()}
    assert levels == {"gpu": "bad", "driver": "bad", "ram": "warn", "long_paths": "warn"}
    monkeypatch.setattr(machine, "_gpu", lambda: ("RTX 5080 Laptop GPU", 15.9, "617.42"))
    monkeypatch.setattr(machine, "_ram_gb", lambda: 31.7)
    monkeypatch.setattr(machine, "_long_paths", lambda: None)  # not Windows
    assert {c["id"]: c["level"] for c in machine.check()} == {
        "gpu": "ok",
        "driver": "ok",
        "ram": "ok",
    }
    monkeypatch.setattr(machine, "_gpu", lambda: None)
    assert machine.check()[0]["level"] == "bad"


def test_the_token_goes_to_huggingface_only(tmp_path, monkeypatch):
    fake = tmp_path / "fake" / "huggingface_hub"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(FAKE_HUB)
    (fake / "errors.py").write_text("class GatedRepoError(Exception):\n    pass\n")
    store = tmp_path / "hf-home" / "token"
    store.parent.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fake"))
    monkeypatch.setenv("FAKE_HF_STORE", str(store))
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(jobs, "_python", lambda step: sys.executable)  # stands in for .venv-ml
    events = jobs.subscribe()
    try:
        with TestClient(app) as c:
            assert c.get("/api/hf").json()["user"] is None
            r = c.post("/api/hf/login", json={"token": "not a token"})
            assert r.status_code == 400 and "not a token" not in r.text
            bad = "hf_" + "b" * 34
            r = c.post("/api/hf/login", json={"token": bad})
            assert (
                r.status_code == 400
                and r.json()["error"]["code"] == "bad_token"
                and bad not in r.text
            )
            assert not store.exists()
            r = c.post("/api/hf/login", json={"token": f"  {GOOD}\n"})
            assert r.json() == {"user": "tester"} and GOOD not in r.text
            assert store.read_text() == GOOD  # in Hugging Face's own store
            st = c.get("/api/hf").json()
            assert st["user"] == "tester" and GOOD not in json.dumps(st)
            assert list(st["access"].values()) == [True, False]  # DINOv3 accepted, Llama not yet
            assert c.post("/api/hf/logout").json() == {"ok": True} and not store.exists()
    finally:
        jobs.unsubscribe(events)
    published = []
    while not events.empty():
        published.append(json.dumps(events.get()))
    assert not any(GOOD in p for p in published)  # never an event


def test_hf_status_without_the_ml_venv_is_unknown_not_an_error(monkeypatch, tmp_path):
    """A dev checkout or CI has no .venv-ml: the Setup page's poll gets the unknown status (treated as "log
    in first"), not a 503 the browser would log; the login itself still reports that it cannot run."""
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")

    def missing(step):
        raise RuntimeError(".venv-ml missing")

    monkeypatch.setattr(jobs, "_python", missing)
    with TestClient(app) as c:
        st = c.get("/api/hf")
        assert st.status_code == 200 and st.json()["token"] is False and st.json()["access"] == {}
        assert c.post("/api/hf/login", json={"token": "hf_" + "a" * 34}).status_code == 503
    assert not (tmp_path / "settings.json").exists()
    for f in (tmp_path / "library").rglob("*") if (tmp_path / "library").exists() else []:
        assert not f.is_file() or GOOD not in f.read_text(errors="ignore")


def test_a_move_must_fit_on_the_other_drive(home, monkeypatch):
    put_models(home / "app" / "models")
    monkeypatch.setattr(machine.storage, "_drive", lambda p: "E:\\" if "ssd" in str(p) else "D:\\")
    full = type("U", (), {"total": 10**12, "free": 10**6, "used": 0})()
    monkeypatch.setattr(machine.storage.shutil, "disk_usage", lambda d: full)
    with pytest.raises(ApiError) as e:
        machine.choose_folder(str(home / "ssd" / "models"))
    assert e.value.status == 507 and models.root() == home / "app" / "models"


def test_a_failed_copy_leaves_nothing_to_mistake_for_models(home, monkeypatch):
    old, new = home / "app" / "models", home / "ssd" / "models"
    put_models(old)
    (old / "Fx" / "z.bin").write_bytes(b"z")  # a second file: the copy dies on it
    real = install.os.replace

    def replace(a, b):
        if Path(a) == old:
            raise OSError(18, "cross-device link")
        real(a, b)

    monkeypatch.setattr(install.os, "replace", replace)
    copied = []

    def copy2(src, dst):
        if copied:
            raise OSError(28, "No space left on device")
        copied.append(src)
        install.shutil.copyfile(src, dst)

    monkeypatch.setattr(install.shutil, "copy2", copy2)
    job = wait(jobs.JOBS[machine.choose_folder(str(new))["job_id"]])
    assert job.status == "failed" and models.root() == old  # nothing switched, nothing lost
    assert (old / "Fx" / "w.bin").exists() and not (home / "ssd" / "models.effigy-partial").exists()
    assert not new.exists() or not any(new.iterdir())
    monkeypatch.setattr(install.shutil, "copy2", install.shutil.copyfile)
    job = wait(
        jobs.JOBS[machine.choose_folder(str(new))["job_id"]]
    )  # again: a real move, not an adoption
    assert job.status == "done" and models.root() == new and not old.exists()


def test_no_install_while_the_models_move(home, monkeypatch):
    move = jobs.Job("", "move_models", None, {})
    jobs.JOBS[move.id] = move
    monkeypatch.setattr(models, "GROUPS", {"fx"})
    try:
        with pytest.raises(ApiError) as e:
            install.install("fx")
        assert e.value.status == 409
    finally:
        del jobs.JOBS[move.id]


def test_a_drive_root_is_not_a_models_folder(home):
    with pytest.raises(ApiError) as e:
        machine.choose_folder(str(Path(home.anchor)))
    assert e.value.status == 400


def test_a_bad_body_never_echoes_its_values():
    with TestClient(app) as c:
        r = c.post("/api/hf/login", json={"tok": GOOD})
        assert r.status_code == 400 and GOOD not in r.text and "token" in r.text
        r = c.post("/api/hf/login", json={"token": ["x", GOOD]})
        assert r.status_code == 400 and GOOD not in r.text


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows thing")
def test_a_copy_does_not_follow_links(home, monkeypatch):
    import _winapi

    old, new = home / "app" / "models", home / "ssd" / "models"
    put_models(old)
    elsewhere = home / "other checkout"
    elsewhere.mkdir()
    (elsewhere / "big.bin").write_bytes(b"x" * 1000)
    _winapi.CreateJunction(str(elsewhere), str(old / "linked"))
    real = install.os.replace

    def replace(a, b):
        if Path(a) == old:
            raise OSError(18, "cross-device link")
        real(a, b)

    monkeypatch.setattr(install.os, "replace", replace)
    job = wait(jobs.JOBS[machine.choose_folder(str(new))["job_id"]])
    assert job.status == "done" and (new / "Fx" / "w.bin").exists()
    assert not (new / "linked").exists() and (elsewhere / "big.bin").read_bytes() == b"x" * 1000


def test_the_first_run_opens_the_guided_setup_until_decided(home, monkeypatch):
    assert machine.first_run()  # nothing installed, nothing decided
    with TestClient(app) as c:
        assert c.get("/api/setup/first-run").json() == {"first_run": True}
        assert c.post("/api/setup/welcomed").json() == {"ok": True}
        assert c.get("/api/setup/first-run").json() == {"first_run": False}
    assert json.loads((home / "settings.json").read_text())["welcomed"] is True
    monkeypatch.setattr(settings, "_cache", {})  # a new start, undecided, but with models: no
    put_models(home / "app" / "models")
    assert not machine.first_run()


def test_another_settings_file(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv("EFFIGY_SETTINGS", str(tmp_path / "mine.json"))
    try:
        assert importlib.reload(settings).FILE == tmp_path / "mine.json"
    finally:
        monkeypatch.delenv("EFFIGY_SETTINGS")
        importlib.reload(settings)


def test_a_group_for_bigger_cards_is_offered_where_it_fits(home, monkeypatch):
    """M46c: the full Tile (refine-full, 15.6 GB) on the 16 GB laptop: left out of Everything and refused, so nobody
    downloads 6.7 GB they cannot run; a 24 GB card gets it."""
    monkeypatch.setattr(models, "GROUPS", {"refine", "refine-full"})
    monkeypatch.setattr(models, "entries", list)
    monkeypatch.setattr(
        install, "PRESETS", [("everything", "Everything", "", ["refine", "refine-full"])]
    )
    monkeypatch.setattr(machine, "_gpu", lambda: ("RTX 5080 Laptop GPU", 15.92, "617.42"))
    assert install.presets()[0]["groups"] == ["refine"]
    with pytest.raises(ApiError) as e:
        install.install("refine-full")
    assert e.value.status == 409 and "about 15.6 GB, this one allows 14.6 GB" in str(e.value)
    monkeypatch.setattr(machine, "_gpu", lambda: ("NVIDIA GeForce RTX 4090", 23.99, "617.42"))
    assert install.presets()[0]["groups"] == ["refine", "refine-full"]
    assert install.too_big("refine-full") is None and install.too_big("refine") is None
