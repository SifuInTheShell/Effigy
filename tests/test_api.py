"""§3.5 driven with TestClient and steps._echo: jobs, meta.json, events, cancel, OOM, resident worker,
Models & Licences (M14)."""

import sys
import time
from pathlib import Path

import httpx2
import pytest
import yaml
from fastapi.testclient import TestClient

from server import install, jobs, library
from server.app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    q = jobs.subscribe()
    with TestClient(app) as c:
        c.events = q
        yield c
    jobs.unsubscribe(q)


def wait(c, job_id, until=("done", "failed", "cancelled"), timeout=20):
    end = time.time() + timeout
    while (job := c.get(f"/api/jobs/{job_id}").json())["status"] not in until:
        assert time.time() < end, job
        time.sleep(0.05)
    return job


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


def run(c, asset, parent=None, **params):
    r = c.post(f"/api/assets/{asset}/steps/_echo", json={"parent": parent, "params": params})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def test_job_runs_and_writes_meta(client):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    first = wait(client, run(client, asset, op="gen", licenses=["pyyaml"]))
    assert first["status"] == "done", first
    second = wait(client, run(client, asset, parent=first["version"]))
    assert second["version"] == "002__echo"

    versions = client.get(f"/api/assets/{asset}").json()["versions"]
    meta = versions[1]
    assert meta["parent"] == first["version"] and meta["outputs"] == {"echo": "echo.json"}
    assert (
        meta["licenses"] == ["pyyaml"] and meta["restricted"] is False
    )  # inherited from the parent
    assert [m["version"] for m in library.chain(asset, meta["version"])] == [
        "002__echo",
        "001__echo",
    ]
    assert client.get(f"/files/{asset}/001__echo/echo.json").json()["op"] == "gen"
    assert client.get(f"/files/{asset}/../../etc/passwd").status_code == 404

    events = drain(client.events)
    names = [e for e, _ in events]
    assert {"progress", "log", "version_created"} <= set(names)
    assert any(
        e == "job"
        and d.items()
        >= {"job_id": first["job_id"], "status": "done", "version": "001__echo"}.items()
        for e, d in events
    )  # public() also carries asset_id and the last progress (the MCP bridge polls them)


def test_an_oom_suggests_the_lower_params_unless_the_run_had_them(client, monkeypatch):
    """M46a: canon's and motion's `{"offload": true}` after their own OOM; an offloaded run's OOM suggests nothing."""
    monkeypatch.setitem(jobs.LOWER, "_echo", {"offload": True})
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    assert wait(client, run(client, asset, oom=True))["error"]["suggest"] == {"offload": True}
    assert "suggest" not in wait(client, run(client, asset, oom=True, offload=True))["error"]


def test_cancel_failures_and_errors(client):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    sleeper = run(client, asset, sleep=30)
    wait(client, sleeper, until=("running",))
    assert client.post(f"/api/jobs/{sleeper}/cancel").json() == {"ok": True}
    assert wait(client, sleeper)["status"] == "cancelled"

    oom = wait(client, run(client, asset, oom=True))
    assert oom["status"] == "failed" and oom["error"]["code"] == "oom"
    assert client.get(f"/api/assets/{asset}").json()["versions"] == []  # nothing half-written

    r = client.post(f"/api/assets/{asset}/steps/nope", json={})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post(f"/api/assets/{asset}/steps/_echo", json={"parent": "999_x"})
    assert r.status_code == 404
    assert client.post("/api/assets", json={}).status_code == 400


def test_gpu_load_while_running(client):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    assert wait(client, run(client, asset, sleep=0.5))["status"] == "done"
    loads, end = [], time.time() + 5
    while not loads or loads[-1]["step"] is not None:  # the idle tick lands ≤ 2 s after the end
        assert time.time() < end, loads
        loads += [d for e, d in drain(client.events) if e == "gpu_load"]
        time.sleep(0.05)
    assert loads[0]["step"] == "_echo" and loads[-1]["step"] is None, loads
    assert set(loads[0]) == {"step", "util", "used_gb", "total_gb"}


def test_light_steps_and_ops_keep_the_resident_worker():
    def keeps(step, **params):
        return jobs._keeps_worker(jobs.Job("a", step, None, params))

    assert keeps("export") and keeps("trellis2", op="generate") and keeps("parts", op="rename")
    assert not keeps("parts", op="segment") and not keeps("parts") and not keeps("rig_skin")


def test_resident_worker_is_reused(client, monkeypatch):
    monkeypatch.setattr(jobs, "SERVE", {"_echo"})
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    a = wait(client, run(client, asset, op="generate"))
    pid = jobs._worker.proc.pid
    b = wait(client, run(client, asset, parent=a["version"], op="texture"))
    assert a["status"] == b["status"] == "done" and jobs._worker.proc.pid == pid
    gpu = [d for e, d in drain(client.events) if e == "gpu"]
    assert gpu == [{"resident": "_echo", "free_gb": gpu[0]["free_gb"]}]  # one cold start
    jobs.evict()
    assert jobs._worker is None


def test_a_resident_worker_that_dies_says_its_exit_code(client, monkeypatch):
    """M46: Fast at 1536 ended its worker without a line; the job names the exit code, as Windows writes a crash."""
    monkeypatch.setattr(jobs, "SERVE", {"_echo"})
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    job = wait(client, run(client, asset, op="generate", exit=7))
    assert job["status"] == "failed" and jobs._worker is None
    assert job["error"]["message"] == "the _echo worker exited 7 without ERROR"
    assert jobs._rc(3221225477) == "0xc0000005" and jobs._rc(-1) == "-1"


def test_cancel_during_a_resident_spawn_leaves_no_worker(client, monkeypatch):
    """BE-007: a cancel that lands while the resident process is starting (no process to kill yet) ends the
    job cancelled, evicts the fresh worker and leaves its process dead; a warm worker stays for the next job."""
    monkeypatch.setattr(jobs, "SERVE", {"_echo"})
    spawned, real = [], jobs._spawn

    def spawn(step, *args, **kw):
        proc = real(step, *args, **kw)
        spawned.append(proc)
        running = next(j for j in jobs.JOBS.values() if j.status == "running")
        jobs.cancel(running.id)  # between the spawn and the process binding
        return proc

    monkeypatch.setattr(jobs, "_spawn", spawn)
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    job = wait(client, run(client, asset, op="generate"))
    assert job["status"] == "cancelled"
    assert jobs.GPU.acquire(
        timeout=30
    )  # the status flips at the cancel; the runner is still evicting
    jobs.GPU.release()
    assert (
        jobs._worker is None and spawned[0].poll() is not None
    )  # the process did not outlive the cancel
    assert sorted(p.name for p in library.asset_dir(asset).iterdir() if p.is_dir()) == []
    monkeypatch.setattr(jobs, "_spawn", real)
    warm = wait(client, run(client, asset, op="generate"))  # a fresh worker, kept warm
    assert warm["status"] == "done" and jobs._worker is not None
    pid = jobs._worker.proc.pid
    monkeypatch.setattr(
        jobs, "_spawn", spawn
    )  # no spawn happens on a warm worker: the cancel must not either
    again = wait(client, run(client, asset, op="texture"))
    assert again["status"] == "done" and jobs._worker.proc.pid == pid
    assert jobs.GPU.acquire(timeout=30)
    jobs.GPU.release()
    jobs.evict()


def test_a_preset_name_stands_for_its_params(tmp_path, monkeypatch):
    """The owner's agent (2026-10-11): describe_steps lists presets but nothing took one. {"preset": name}
    becomes that preset's params with the caller's keys on top; an unknown name is a bad_params."""
    from server import library, presets

    monkeypatch.setenv("EFFIGY_LIBRARY", str(tmp_path))
    monkeypatch.setattr(jobs, "_run", lambda job: None)
    asset = library.create_asset("preset crate")["id"]
    job, _ = jobs.submit(asset, "trellis2", None, {"preset": "fast", "seed": 3})
    jobs.JOBS.pop(job.id)  # never run: it would hold the asset for the tests after this one
    assert job.params == presets.PRESETS["trellis2"]["fast"] | {"seed": 3}
    with pytest.raises(library.ApiError) as e:
        jobs.submit(asset, "trellis2", None, {"preset": "sharp"})
    assert e.value.code == "bad_params" and "fast" in str(e.value)


def test_a_result_followed_by_a_crash_is_a_failure(tmp_path, monkeypatch):
    """BE-005: a step that prints RESULT and then exits nonzero failed; exit 0 after RESULT is the result;
    an ERROR with its exit code keeps its code."""
    import subprocess

    def fake(script):
        def spawn(step, *args, **kw):
            return subprocess.Popen(
                [sys.executable, "-c", script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )

        return spawn

    job = jobs.Job("a", "_echo", None, {})
    result = 'print(\'RESULT {"outputs": {}, "metrics": {}}\', flush=True)'
    monkeypatch.setattr(jobs, "_spawn", fake(result + "; raise SystemExit(1)"))
    kind, payload = jobs._run_oneshot(job, None, tmp_path / "out")
    assert (
        kind == "ERROR" and payload["code"] == "failed" and "after its RESULT" in payload["message"]
    )
    monkeypatch.setattr(jobs, "_spawn", fake(result))
    assert jobs._run_oneshot(job, None, tmp_path / "out")[0] == "RESULT"
    error = (
        'print(\'ERROR {"code": "bad_params", "message": "no"}\', flush=True); raise SystemExit(2)'
    )
    monkeypatch.setattr(jobs, "_spawn", fake(error))
    kind, payload = jobs._run_oneshot(job, None, tmp_path / "out")
    assert kind == "ERROR" and payload == {"code": "bad_params", "message": "no"}


def test_events_wire_format(live_server):
    """/events over a real server: TestClient can't read an endless stream."""
    with httpx2.stream("GET", f"{live_server}/events", timeout=10) as r:
        jobs.publish("job", {"job_id": "x", "status": "done"})  # subscribed once headers arrived
        lines = []
        for line in r.iter_lines():
            lines.append(line)
            if line.startswith("data:"):
                break
    assert r.headers["content-type"].startswith("text/event-stream")
    assert lines[-2:] == ["event: job", 'data: {"job_id": "x", "status": "done"}']


def test_user_files_are_served_inert(client):
    """BE-001: an HTML (or SVG) companion from an imported zip, opened at its /files URL, must not run in the
    app's origin: every user file comes with a sandbox CSP and nosniff; images and the rest stay as they are."""
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    client.put(f"/api/assets/{asset}/upload/ref.jpg", content=b"\xff\xd8jpeg")
    page = library.asset_dir(asset) / "001_upload" / "preview.html"
    page.write_text("<script>fetch('/api/assets', {method: 'POST'})</script>", encoding="utf-8")
    r = client.get(f"/files/{asset}/001_upload/preview.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"
    img = client.get(f"/files/{asset}/001_upload/image.jpg")
    assert img.content == b"\xff\xd8jpeg" and img.headers["content-security-policy"] == "sandbox"


def test_upload_and_step_list(client):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    meta = client.put(f"/api/assets/{asset}/upload/Ref Photo.JPG", content=b"\xff\xd8jpeg").json()
    assert meta["version"] == "001_upload" and meta["provenance"] == "upload"
    assert client.get(f"/files/{asset}/001_upload/image.jpg").content == b"\xff\xd8jpeg"
    r = client.put(f"/api/assets/{asset}/upload/model.docx", content=b"x")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    video = client.put(f"/api/assets/{asset}/upload/dance.MOV", content=b"moov").json()  # M34
    assert video["outputs"] == {"video": "video.mov"} and video["params"]["name"] == "dance.MOV"
    steps = client.get("/api/steps").json()
    assert "_echo" not in steps and "_common" not in steps


def models_by_key(c):
    return {(m["name"], m["groups"][0]): m for m in c.get("/api/models").json()}


def test_models_listing_and_remove(client, tmp_path, monkeypatch):
    models = tmp_path / "models"
    monkeypatch.setenv("EFFIGY_MODELS", str(models))
    for rel, n in [
        ("TRELLIS.2-4B/pipeline.json", 2),
        ("Pixal3D/ckpts/ss.pt", 4),
        ("Pixal3D/ckpts/ss_mv.pt", 6),
        ("hub/models--ZhengPeng7--BiRefNet/blobs/ab12", 3),
    ]:
        (models / rel).parent.mkdir(parents=True, exist_ok=True)
        (models / rel).write_bytes(b"x" * n)
    m = models_by_key(client)
    trellis, birefnet = m["microsoft/TRELLIS.2-4B", "fast"], m["ZhengPeng7/BiRefNet", "fast"]
    assert (
        trellis["partial"] and not trellis["installed"] and trellis["bytes"] == 2
    )  # one file of many
    single, multi = m["TencentARC/Pixal3D", "faithful"], m["TencentARC/Pixal3D", "multiview"]
    assert (single["bytes"], multi["bytes"]) == (4, 6)  # one folder, split by the download filters
    assert birefnet["groups"] == ["fast", "faithful"] and birefnet["bytes"] == 3  # HF cache
    assert not m["facebook/dinov3-vitl16-pretrain-lvd1689m", "fast"]["installed"]
    # a FILES row, named like its licences row
    assert m["valeoai/NAF", "faithful"]["where"] == "NAF/naf_release.pth"

    assert client.delete(f"/api/models/{multi['id']}").json() == {"ok": True}
    assert not (models / "Pixal3D/ckpts/ss_mv.pt").exists()
    assert (models / "Pixal3D/ckpts/ss.pt").is_file()
    assert client.delete(f"/api/models/{birefnet['id']}").json() == {"ok": True}
    assert not (models / "hub/models--ZhengPeng7--BiRefNet").exists()

    # only known entries by id; a busy GPU (a step running) blocks removal
    assert client.delete("/api/models/999").status_code == 404
    assert client.delete("/api/models/-1").status_code == 404
    assert client.delete("/api/models/TRELLIS.2-4B").status_code == 400
    assert not client.delete("/api/models/..%2F..%2Fpipeline.json").is_success  # 405 if ui/dist
    with jobs.GPU:
        r = client.delete(f"/api/models/{trellis['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    assert (models / "TRELLIS.2-4B/pipeline.json").is_file()


def test_licenses_route(client):
    rows = client.get("/api/licenses").json()
    root = Path(__file__).resolve().parents[1]
    assert rows == yaml.safe_load((root / "licenses.yaml").read_text(encoding="utf-8"))
    assert any(r.get("attribution") == "Built with DINOv3" for r in rows)


FAKE_HUB = """
import os, pathlib
from huggingface_hub.errors import GatedRepoError

def snapshot_download(repo, revision, local_dir=None, allow_patterns=("w.bin",), **_):
    if "dinov3" in repo:
        raise GatedRepoError(repo)
    cache = os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ["HF_HOME"], "hub")
    d = pathlib.Path(local_dir or os.path.join(cache, "models--" + repo.replace("/", "--"), "blobs"))
    f = d / allow_patterns[0].replace("*", "x")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"w" * 5)
"""


def test_install_runs_download_models(client, tmp_path, monkeypatch):
    """The real tools.download_models as a job, against a fake huggingface_hub (no network)."""
    hub = tmp_path / "fake" / "huggingface_hub"
    hub.mkdir(parents=True)
    (hub / "__init__.py").write_text(FAKE_HUB)
    (hub / "errors.py").write_text("class GatedRepoError(Exception):\n    pass\n")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fake"))
    monkeypatch.setenv("EFFIGY_MODELS", str(tmp_path / "models"))
    monkeypatch.setattr(jobs, "_python", lambda step: sys.executable)  # stands in for .venv-ml
    monkeypatch.setattr(install, "venv_state", lambda venv: "ok")  # no uv sync here
    # This fake downloader writes a few bytes, independent of the host drive's real free space.
    monkeypatch.setattr(install.models, "missing_bytes", lambda group: 0)

    r = client.post("/api/models/install", json={"group": "fast"})
    assert r.status_code == 202, r.text
    job = wait(client, r.json()["job_id"], timeout=60)
    assert job["status"] == "failed" and job["error"]["code"] == "gated", job
    msg = job["error"]["message"]
    assert (
        "huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m" in msg and "hf auth login" in msg
    )
    fast = [m for m in models_by_key(client).values() if "fast" in m["groups"]]
    assert not any(m["installed"] for m in fast)  # one fake file each: not the manifest's files
    assert {m["name"] for m in fast if m["partial"]} == {
        "microsoft/TRELLIS.2-4B",
        "microsoft/TRELLIS-image-large",
        "ZhengPeng7/BiRefNet",
    }
    logs = [
        d["line"] for e, d in drain(client.events) if e == "log" and d["job_id"] == job["job_id"]
    ]
    assert any("TRELLIS.2-4B" in line for line in logs)
    assert client.post("/api/models/install", json={"group": "nope"}).status_code == 404


def test_idle_resident_worker_is_stopped(client, monkeypatch):
    monkeypatch.setattr(jobs, "SERVE", {"_echo"})
    monkeypatch.setattr(jobs, "IDLE_S", 0.3)
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    assert wait(client, run(client, asset))["status"] == "done"
    assert jobs._worker is not None  # resident after the job ...
    end = time.time() + 5
    while jobs._worker is not None:  # ... and gone once idle
        assert time.time() < end, "worker not evicted"
        time.sleep(0.05)


def test_delete_branch_rename_and_delete_asset(client, tmp_path):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    a = wait(client, run(client, asset))["version"]
    b = wait(client, run(client, asset, parent=a))["version"]
    c = wait(client, run(client, asset, parent=a))["version"]
    d = wait(client, run(client, asset, parent=b))["version"]

    busy = run(client, asset, parent=b, sleep=2)  # a job on the branch blocks deleting it
    assert client.delete(f"/api/assets/{asset}/versions/{b}").status_code == 409
    assert client.delete(f"/api/assets/{asset}").status_code == 409
    wait(client, busy)
    e = client.get(f"/api/jobs/{busy}").json()["version"]

    r = client.delete(f"/api/assets/{asset}/versions/{b}")
    assert r.status_code == 200 and r.json()["deleted"] == [b, d, e]
    left = [v["version"] for v in client.get(f"/api/assets/{asset}").json()["versions"]]
    assert left == [a, c]
    assert len(list((tmp_path / "_trash").glob(f"*_{asset}_{b}/{d}"))) == 1  # recoverable

    assert (
        client.patch(f"/api/assets/{asset}", json={"name": "Old crate"}).json()["name"]
        == "Old crate"
    )
    assert client.patch(f"/api/assets/{asset}", json={"name": " "}).status_code == 400
    assert client.delete(f"/api/assets/{asset}").status_code == 200
    assert client.get(f"/api/assets/{asset}").status_code == 404
    assert [x["id"] for x in client.get("/api/assets").json()] == []

    # UX-14: both come back from Recently deleted, the asset first
    trash = {x["kind"]: x["name"] for x in client.get("/api/trash").json()}
    assert set(trash) == {"asset", "versions"}
    assert (
        client.post(f"/api/trash/{trash['versions']}/restore").status_code == 409
    )  # its asset is gone too
    assert client.post(f"/api/trash/{trash['asset']}/restore").json() == {
        "asset_id": asset,
        "versions": [],
    }
    r = client.post(f"/api/trash/{trash['versions']}/restore")
    assert r.status_code == 200 and r.json()["versions"] == [b, d, e]
    left = [v["version"] for v in client.get(f"/api/assets/{asset}").json()["versions"]]
    assert left == [a, b, c, d, e]
    assert client.post(f"/api/trash/{trash['asset']}/restore").status_code == 404


def test_fit_check_scores_a_skeleton_with_unsaved_moves(client, tmp_path):
    """The skeleton editor's Check fit: a rig version's skeleton in its mesh, the editor's moves on top."""
    import json

    from steps._mesh import write_glb
    from tests.test_rig import limb

    asset = client.post("/api/assets", json={"name": "Limb"}).json()["id"]
    d = tmp_path / asset / "020_rig_fit"
    d.mkdir()
    v, f, joints = limb()
    write_glb(d / "mesh.glb", v, f)
    (d / "skeleton.json").write_text(json.dumps({"template": "fox", "joints": joints}))
    outputs = {"mesh": "mesh.glb", "skeleton": "skeleton.json"}
    (d / "meta.json").write_text(
        json.dumps({"version": "020_rig_fit", "step": "rig_fit", "outputs": outputs})
    )
    url = f"/api/assets/{asset}/versions/020_rig_fit/fit-check"
    assert client.post(url, json={}).json()["score"] == 100
    moved = client.post(url, json={"joints": {"B": [0.5, 0, 0]}}).json()
    assert moved["attention"][0] == ["B", "outside the model"] and moved["score"] < 100
    assert client.post(url, json={"joints": {"B": [0, "x", 0]}}).status_code == 400
    assert client.post(url, json={"joints": {"B": [0, 0]}}).status_code == 400
    (d / "meta.json").write_text(
        json.dumps({"version": "020_rig_fit", "step": "rig_fit", "outputs": {}})
    )
    assert client.post(url, json={}).status_code == 404


def test_a_failure_while_finishing_ends_the_job_as_failed(client, monkeypatch):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(jobs.library, "write_meta", boom)
    job = wait(client, run(client, asset))
    assert job["status"] == "failed" and "disk full" in job["error"]["message"]
    assert client.get(f"/api/assets/{asset}").json()["versions"] == []


def test_cancel_takes_the_steps_children_with_it(tmp_path):
    import subprocess

    pidfile = tmp_path / "child.pid"
    child = "import time; time.sleep(60)"
    code = (
        f"import subprocess, sys, time, pathlib; c = subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"pathlib.Path({str(pidfile)!r}).write_text(str(c.pid)); time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    end = time.time() + 20
    while not pidfile.is_file() or not pidfile.read_text():
        assert time.time() < end
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    jobs._kill_tree(proc)
    proc.wait(timeout=10)
    time.sleep(0.5)
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, check=False
        ).stdout.decode(errors="replace")
        assert str(pid) not in out
    else:
        import os

        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_a_missing_thumbnail_is_a_blank_picture_not_a_404(client):
    """The library and the history put every asset's and version's thumb in an <img>: with nothing to show (a
    brief, no Blender to render a mesh) the route answers a blank PNG, so the browser logs no error (the smoke
    tests count them; CI has no Blender). An unknown asset or version is still a 404."""
    from server.app import BLANK_PNG

    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    r = client.get(f"/api/assets/{asset}/thumb")
    assert (
        r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == BLANK_PNG
    )
    assert client.get("/api/assets/nope/thumb").status_code == 404
    assert client.get(f"/api/assets/{asset}/versions/001_upload/thumb").status_code == 404
    first = wait(client, run(client, asset, op="gen"))  # an echo version: no picture, no mesh
    r = client.get(f"/api/assets/{asset}/versions/{first['version']}/thumb")
    assert r.status_code == 200 and r.content == BLANK_PNG


def test_archive_star_and_label_routes(client):
    """M28: PATCH an asset (archived) and a version (starred, label); the listing carries counts."""
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    first = wait(client, run(client, asset, op="gen"))
    assert client.patch(f"/api/assets/{asset}", json={}).status_code == 400
    assert client.patch(f"/api/assets/{asset}", json={"archived": True}).json()["archived"] is True
    listed = {a["id"]: a for a in client.get("/api/assets").json()}[asset]
    assert listed["archived"] and listed["versions"] == 1 and listed["updated"]
    r = client.patch(
        f"/api/assets/{asset}/versions/{first['version']}", json={"starred": True, "label": "keep"}
    )
    assert r.status_code == 200 and r.json()["starred"] is True and r.json()["label"] == "keep"
    assert (
        client.patch(f"/api/assets/{asset}/versions/999_nope", json={"starred": True}).status_code
        == 404
    )
    assert client.get(f"/api/assets/{asset}").json()["versions"][0]["label"] == "keep"


def test_uploads_are_capped(client, monkeypatch):
    """M34: the body is held in RAM, so a video upload has a ceiling."""
    from server import app as app_module

    monkeypatch.setattr(app_module, "MAX_UPLOAD", 10)
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    r = client.put(f"/api/assets/{asset}/upload/clip.mp4", content=b"x" * 11)
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"


def test_dedupe_hands_back_the_running_job_marked_as_not_yours(client):
    asset = client.post("/api/assets", json={"name": "Crate"}).json()["id"]
    body = {"parent": None, "params": {"sleep": 5}, "dedupe": True}
    first = client.post(f"/api/assets/{asset}/steps/_echo", json=body).json()
    again = client.post(f"/api/assets/{asset}/steps/_echo", json=body).json()
    other = client.post(
        f"/api/assets/{asset}/steps/_echo", json=body | {"params": {"sleep": 4}}
    ).json()
    assert first["deduped"] is False and again == {"job_id": first["job_id"], "deduped": True}
    assert (
        other["job_id"] != first["job_id"] and other["deduped"] is False
    )  # other params: its own job
    for j in (first, other):
        client.post(f"/api/jobs/{j['job_id']}/cancel")


def test_the_app_port_is_chosen_in_setup(client, tmp_path, monkeypatch):
    """GET says where the app runs and where it starts next; POST saves a free port, refuses one another
    program listens on, one out of range, and any while EFFIGY_PORT sets it."""
    import socket

    from server import settings

    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.delenv("EFFIGY_PORT", raising=False)
    got = client.get("/api/setup/port").json()
    assert got["next"] == 8000 and got["source"] == "default" and got["default"] == 8000
    assert client.post("/api/setup/port", json={"port": 80}).json()["error"]["code"] == "bad_params"
    with socket.socket() as held:  # another program on a port
        held.bind(("127.0.0.1", 0))
        held.listen()
        taken = held.getsockname()[1]
        r = client.post("/api/setup/port", json={"port": taken})
        assert r.status_code == 409 and r.json()["error"]["code"] == "in_use"
    with socket.socket() as probe:  # a port nobody holds
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    got = client.post("/api/setup/port", json={"port": free}).json()
    assert got["next"] == free and got["source"] == "settings"
    assert settings.read()["port"] == free
    monkeypatch.setenv("EFFIGY_PORT", "8005")
    assert client.post("/api/setup/port", json={"port": free}).json()["error"]["code"] == "env"
    assert client.get("/api/setup/port").json()["next"] == 8005


def test_the_launcher_serves_on_the_chosen_port_and_stops_on_a_taken_one(tmp_path, monkeypatch):
    """python -m server (run.bat): uvicorn on settings.port(); a port another program holds stops it with a
    message that says what to do."""
    import socket

    from server import __main__ as launcher
    from server import settings

    monkeypatch.setattr(settings, "FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setenv("EFFIGY_NO_BROWSER", "1")
    ran = {}
    monkeypatch.setattr(
        launcher.uvicorn, "run", lambda app, host, port: ran.update(app=app, host=host, port=port)
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    monkeypatch.setenv("EFFIGY_PORT", str(free))
    launcher.main()
    assert ran == {"app": "server.app:app", "host": "127.0.0.1", "port": free}
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        monkeypatch.setenv("EFFIGY_PORT", str(held.getsockname()[1]))
        with pytest.raises(SystemExit) as e:
            launcher.main()
    assert "in use" in str(e.value) and "EFFIGY_PORT" in str(e.value)


def test_create_asset_kind(client):
    """A 2D asset over the API (research 15); a bad kind is a 400, a 3D asset carries no kind."""
    assert client.post("/api/assets", json={"name": "Sky", "kind": "2d"}).json()["kind"] == "2d"
    assert "kind" not in client.post("/api/assets", json={"name": "Crate"}).json()
    assert client.post("/api/assets", json={"name": "x", "kind": "4d"}).status_code == 400


def test_an_output_may_be_a_folder_of_files_but_not_an_empty_one(tmp_path):
    """The 2D track's `frames` (research 15, M51): a folder output passes when it holds files; empty, it is missing."""
    from server import jobs

    (tmp_path / "frames").mkdir()
    (tmp_path / "sheet.png").write_bytes(b"x")
    out = {"outputs": {"frames": "frames", "sheet": "sheet.png"}}
    assert jobs._check_outputs(tmp_path, out) == "missing outputs: ['frames']"
    (tmp_path / "frames" / "a.png").write_bytes(b"x")
    assert jobs._check_outputs(tmp_path, out) is None
