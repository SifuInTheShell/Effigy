"""M35b: what "installed" means (every file of the manifest at its size), and installing and repairing a
feature on a fake models folder: its environment set up, damaged files fetched again, installs queued."""

import hashlib
import json
import os
import sys
import threading
import time

import pytest

from server import install, jobs, models
from server.library import ApiError
from tools import download_models as dm

GOOD = b"w" * 1000


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Group `fx`: a local folder with two files (one with a SHA-256) and an HF-cache repo with one."""
    mod = tmp_path / "models"
    monkeypatch.setenv("EFFIGY_MODELS", str(mod))
    rows = {
        "local": {"w.bin": [1000, hashlib.sha256(GOOD).hexdigest()], "cfg/c.json": [2, None]},
        "hub": {"m.bin": [1000, None]},
    }
    monkeypatch.setattr(models, "MANIFEST_ROWS", rows)
    entries = [
        {
            "name": "a/fx",
            "groups": ["fx"],
            "revision": "r1",
            "where": "Fx",
            "filters": {},
            "hub": False,
            "key": "local",
        },
        {
            "name": "a/hub",
            "groups": ["fx"],
            "revision": "abc",
            "where": "hub/models--a--hub",
            "filters": {},
            "hub": True,
            "key": "hub",
        },
    ]
    monkeypatch.setattr(models, "entries", lambda: entries)
    monkeypatch.setattr(models, "GROUPS", {"fx", "fy"})
    monkeypatch.setattr(install, "GROUP_VENV", {"fx": ".venv-fx"})
    monkeypatch.setattr(jobs, "VENV_GROUP", {".venv-ml": "ml", ".venv-fx": "fx"})
    monkeypatch.setattr(install, "IMPORTS", {".venv-ml": "json", ".venv-fx": "json"})
    venvs = {".venv-ml": "ok", ".venv-fx": "missing"}
    monkeypatch.setattr(install, "venv_state", lambda v: venvs[v])
    synced = []

    def sync_cmd(venv, *extra):  # stands in for uv: records the sync and "creates" the environment
        synced.append((venv, *extra))
        venvs[venv] = "ok"
        return [sys.executable, "-c", f"print('synced {venv}')"]

    monkeypatch.setattr(install, "_sync_cmd", sync_cmd)
    monkeypatch.setattr(jobs, "venv_python", lambda venv: sys.executable)
    fetched = []

    def download(job, group):  # stands in for tools.download_models: writes what is missing
        for e in models.entries():
            for rel in models.state(e)["bad"]:
                f = models.target(e, rel)
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(GOOD if rel != "cfg/c.json" else b"{}")
                fetched.append(rel)

    monkeypatch.setattr(install, "_download", download)
    return mod, synced, fetched


def wait(job, timeout=10):
    end = time.time() + timeout
    while job.status in ("queued", "running"):
        assert time.time() < end, job.public()
        time.sleep(0.02)
    return job


def test_manifest_covers_every_pinned_row_and_gives_the_sizes():
    keys = [k for _, k in dm.rows()]
    assert len(set(keys)) == len(keys)  # GEM-X's rows differ only by folder
    assert set(keys) == set(models.MANIFEST_ROWS), (
        "a pin changed: python -m tools.download_models --manifest"
    )
    assert set(dm.SIZES) == models.GROUPS and all(dm.SIZES.values())


def test_installed_means_every_file_at_its_size(fake):
    mod, _, _ = fake
    entry = models.entries()[0]
    assert models.state(entry)["bad"] == ["w.bin", "cfg/c.json"]
    (mod / "Fx").mkdir(parents=True)
    (mod / "Fx" / "w.bin").write_bytes(GOOD)
    st = models.state(entry)
    assert st == {"want": 1002, "have": 1000, "bad": ["cfg/c.json"]}
    row = models.listing()[0]
    assert not row["installed"] and row["partial"] and row["missing_bytes"] == 2
    hub = models.target(
        models.entries()[1], "m.bin"
    )  # an HF-cache file sits in its pinned snapshot
    assert hub == mod / "hub/models--a--hub/snapshots/abc/m.bin"
    assert models.missing_bytes("fx") == 1002


def test_install_sets_up_the_environment_then_the_files(fake):
    _mod, synced, fetched = fake
    job = wait(install.install("fx"))
    assert job.status == "done", job.error
    assert synced == [(".venv-fx",)]  # .venv-ml was ok, the feature's own was missing
    assert sorted(fetched) == ["cfg/c.json", "m.bin", "w.bin"]
    assert all(r["installed"] for r in models.listing())
    assert not jobs.GPU.locked()


def test_repair_fetches_damaged_files_again(fake):
    mod, synced, fetched = fake
    wait(install.install("fx"))
    fetched.clear()
    (mod / "Fx" / "w.bin").write_bytes(
        b"x" * 1000
    )  # the right size, the wrong content: only the hash knows
    (mod / "hub/models--a--hub/snapshots/abc/m.bin").write_bytes(b"w" * 10)  # truncated
    assert not models.listing()[1]["installed"]
    job = wait(install.install("fx", repair=True))
    assert job.status == "done", job.error
    assert sorted(fetched) == ["m.bin", "w.bin"]
    assert (mod / "Fx" / "w.bin").read_bytes() == GOOD
    assert (".venv-fx",) not in synced[1:]  # the environment imports: no reinstall


def test_repair_reinstalls_an_environment_that_does_not_import(fake, monkeypatch):
    _, synced, _ = fake
    wait(install.install("fx"))
    monkeypatch.setattr(install, "IMPORTS", {".venv-ml": "json", ".venv-fx": "no_such_module_here"})
    job = wait(install.install("fx", repair=True))
    assert (".venv-fx", "--reinstall") in synced
    assert job.status == "failed" and "does not work after reinstalling" in job.error["message"]


def test_installs_wait_their_turn(fake, monkeypatch):
    gate = threading.Event()
    real = install._download
    monkeypatch.setattr(install, "_download", lambda job, group: (gate.wait(5), real(job, group)))
    first = install.install("fx")
    end = time.time() + 5
    while first.status != "running":
        assert time.time() < end
        time.sleep(0.02)
    with pytest.raises(ApiError) as e:
        install.install("fx")  # the same group twice
    assert e.value.status == 409
    second = install.install("fy")
    time.sleep(0.2)
    assert second.status == "queued"
    gate.set()
    assert wait(first).status == "done"
    assert wait(second).status in ("done", "failed")  # fy has no files of its own: done


def test_a_gated_download_says_so(fake, monkeypatch):
    def gated(job, group):
        raise install._Failed("gated", "accept the licence at https://huggingface.co/a/fx")

    monkeypatch.setattr(install, "_download", gated)
    job = wait(install.install("fx"))
    assert job.status == "failed" and job.error["code"] == "gated"


def test_amd_leaves_the_rocm_environment_to_run_bat(fake, monkeypatch):
    """AMD: .venv-ml comes from requirements-ml-rocm.txt (run.bat); a uv sync would put CUDA torch back."""
    _, synced, _ = fake
    monkeypatch.setattr(install.gpu_amd, "rocm_fits", lambda: True)
    monkeypatch.setattr(install, "venv_state", lambda v: "outdated")
    assert wait(install.install("fx", repair=True)).status == "done"
    assert [s[0] for s in synced] == [".venv-fx"]


def test_environment_state_asks_uv(monkeypatch, tmp_path):
    exe = tmp_path / "python.exe"
    monkeypatch.setattr(jobs, "venv_python", lambda venv: exe)
    monkeypatch.setattr(install.gpu_amd, "rocm_fits", lambda: False)  # an NVIDIA machine
    assert install.venv_state(".venv-ml") == "missing"
    exe.write_text("")
    for code, state in ((0, "ok"), (1, "outdated")):
        monkeypatch.setattr(
            install,
            "_sync_cmd",
            lambda venv, *x, c=code: [sys.executable, "-c", f"raise SystemExit({c})"],
        )
        assert install.venv_state(".venv-ml") == state
    # AMD: not the lock's environment, uv not asked
    monkeypatch.setattr(install.gpu_amd, "rocm_fits", lambda: True)
    assert install.venv_state(".venv-ml") == "ok" and install.venv_state(".venv-skin") == "outdated"
    rows = install.environments()
    assert {r["name"] for r in rows} >= {".venv-ml", ".venv-kimodo", ".venv-gemx"}
    assert "mocap" in next(r for r in rows if r["name"] == ".venv-gemx")["groups"]
    assert json.dumps(rows)


def test_progress_counts_the_parts_being_written(fake):
    mod, _, _ = fake
    entry = models.entries()[0]
    (mod / "Fx" / ".cache" / "huggingface" / "download").mkdir(parents=True)
    (mod / "Fx" / "cfg").mkdir()
    (mod / "Fx" / "cfg" / "c.json").write_bytes(b"{}")
    (mod / "Fx" / ".cache" / "huggingface" / "download" / "w.bin.incomplete").write_bytes(
        b"w" * 400
    )
    assert install._on_disk(entry) == 402


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows thing")
def test_repair_never_deletes_behind_a_link(fake, monkeypatch, tmp_path):
    import _winapi

    mod, _, _ = fake
    outside = tmp_path / "other_checkout"
    outside.mkdir()
    (outside / "w.bin").write_bytes(b"x" * 5)  # "damaged": the wrong size
    (mod / "Fx").mkdir(parents=True)
    _winapi.CreateJunction(str(outside), str(mod / "Fx" / "linked"))
    entry = {**models.entries()[0], "where": "Fx/linked", "key": "linked"}
    monkeypatch.setattr(models, "entries", lambda: [entry])
    monkeypatch.setattr(models, "MANIFEST_ROWS", {"linked": {"w.bin": [1000, None]}})
    monkeypatch.setattr(install, "_download", lambda job, group: None)
    job = wait(install.install("fx", repair=True))
    assert job.status == "failed"  # still damaged, and said so
    assert (outside / "w.bin").read_bytes() == b"x" * 5


def test_a_queued_install_can_be_cancelled(fake, monkeypatch):
    gate, ran, real = threading.Event(), [], install._download

    def download(job, group):
        ran.append(group)
        gate.wait(5)
        real(job, group)

    monkeypatch.setattr(install, "_download", download)
    first = install.install("fx")
    second = install.install("fy")
    jobs.cancel(second.id)
    gate.set()
    assert wait(first).status == "done" and first.proc is None  # no stale process for a cancel
    time.sleep(0.2)
    assert second.status == "cancelled" and ran == ["fx"]


def test_without_uv_to_ask_an_environment_is_left_alone(fake, monkeypatch):
    _, synced, _ = fake
    monkeypatch.setattr(install, "venv_state", lambda v: "unknown")
    assert wait(install.install("fx")).status == "done"
    assert synced == []


def test_a_copy_leaves_a_strangers_partial_folder_alone(tmp_path, monkeypatch):
    """OPS-001: a sibling called <new>.effigy-partial is not ours; the copy stages under its job's own name."""
    old = tmp_path / "src"
    (old / "Fx").mkdir(parents=True)
    (old / "Fx" / "w.bin").write_bytes(GOOD)
    new = tmp_path / "elsewhere" / "models"
    stranger = new.with_name("models.effigy-partial")
    stranger.mkdir(parents=True)
    (stranger / "note.txt").write_text("mine", encoding="utf-8")
    job = jobs.Job("", "move_models", None, {"from": str(old), "to": str(new)})
    install._copy(job, old, new)
    assert (stranger / "note.txt").read_text(encoding="utf-8") == "mine"
    assert (new / "Fx" / "w.bin").read_bytes() == GOOD
    assert not list(new.parent.glob("*.effigy-partial-*"))  # ours was renamed into place


def test_a_failed_settings_write_puts_the_models_back(tmp_path, monkeypatch):
    """OPS-002: the setting is the switch; when it cannot be saved, the models return to where they were and
    the job fails with the reason."""
    old = tmp_path / "models"
    (old / "Fx").mkdir(parents=True)
    (old / "Fx" / "w.bin").write_bytes(GOOD)
    new = tmp_path / "moved"

    def refuse(**values):
        raise OSError("disk full")

    monkeypatch.setattr(install.settings, "write", refuse)
    job = jobs.Job("", "move_models", None, {"from": str(old), "to": str(new)})
    install._move(job, old, new)
    assert job.status == "failed" and "disk full" in job.error["message"]
    assert (old / "Fx" / "w.bin").read_bytes() == GOOD and not new.exists()
