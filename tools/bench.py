"""Chain benches (IMPLEMENTATION.md M11 props, M22 characters): a chain preset over an image set, through the
running backend.

    uv run python -m tools.bench --preset game-prop --set tests/fixtures/golden --out docs/bench/phase0_fast.csv
    uv run python -m tools.bench ... --weights faithful          # the A/B: same chain, Pixal3D
    uv run python -m tools.bench --preset game-character --set tests/fixtures/characters --out docs/bench/m22.csv
    uv run python -m tools.bench --make-golden --set tests/fixtures/golden   # once: prompts.yaml → <name>.png
    uv run python -m tools.bench --preset game-prop --set D:/refs --export godot --to D:/game/assets   # batch mode

Batch mode (PLAN Phase 3 "CLI batch mode"): with `--export <profile>` every image's finished chain is exported
and, with `--to <folder>`, copied into `<folder>/<asset>_<profile>/` (the library's batch export, M42: never
over an existing folder); the assets are named after their images. The CSV still records every step.

A turnaround sheet (M17) in the set carries `<name>.views.json`, its view order left to right
(`["front", "right", "back", "left"]`): it goes through prep op views instead of the single-image head.

Start the backend first (run.bat / run.sh): the bench uses the same jobs, library and resident worker as
the UI. One CSV row per image: success, seconds and peak VRAM per step (`vram_` = whole GPU from
nvidia-smi, sampled while the job runs; `torch_` = the step's own allocator peak when it reports one),
then the final mesh's faces, non-manifold edges, components and UV texel-density spread (validate).
"""

import argparse
import csv
import json
import platform
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from server.presets import CHAINS, GENERATE, PRESETS
from steps.export import PROFILES

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
# step metrics worth a column (M22: a character needs no marker fix when every joint is inside the mesh; the
# skin QA fails are recorded, not counted: a held prop or a robe fails it whatever the markers)
COLS = {
    "canon": ("pose", "seed"),
    "trellis2": ("seed",),
    "project": ("iou", "changed", "head_from"),
    "rig_fit": ("inside", "joints", "hands_detected"),
    "rig_skin": ("engine", "fail"),
    "anim": ("clips",),
}


def call(api: str, method: str, path: str, body=None, raw: bytes | None = None):
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    headers = {} if raw is not None else {"Content-Type": "application/json"}
    req = urllib.request.Request(api + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = r.read()
    return payload if path.startswith("/files/") else json.loads(payload)


def gpu_query(fields: str) -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 else None
    except (OSError, IndexError, subprocess.SubprocessError):
        return _amd_query(fields)


def _amd_query(fields: str) -> str | None:
    """AMD: nvidia-smi's CSV line from the AMD card (server/gpu_amd.py); blank for what it can't tell."""
    from server import gpu_amd

    if (g := gpu_amd.adapter()) is None:
        return None
    have = {"name": g["name"], "driver_version": g["driver"]}
    if (v := gpu_amd.smi(*(f for f in fields.split(",") if f.startswith("memory.")))) is not None:
        have |= dict(
            zip((f for f in fields.split(",") if f.startswith("memory.")), map(str, map(int, v)))
        )
    return ", ".join(have.get(f, "") for f in fields.split(","))


def run_job(api: str, asset: str, step: str, parent: str | None, params: dict):
    """→ (job, seconds, whole-GPU peak GB or None). ponytail: 0.5 s sampling misses sub-second spikes;
    the step's own torch peak (meta metrics) covers those."""
    t0, peak = time.time(), None
    body = {"parent": parent, "params": params}
    job_id = call(api, "POST", f"/api/assets/{asset}/steps/{step}", body)["job_id"]
    try:
        while (job := call(api, "GET", f"/api/jobs/{job_id}"))["status"] not in (
            "done",
            "failed",
            "cancelled",
        ):
            used = gpu_query("memory.used")
            if used is not None:
                peak = max(peak or 0.0, int(used) / 1024)
            time.sleep(0.5)
    except KeyboardInterrupt:  # Ctrl+C: the job would go on without us, holding the GPU
        call(api, "POST", f"/api/jobs/{job_id}/cancel")
        raise
    return job, round(time.time() - t0, 1), None if peak is None else round(peak, 2)


def tagged(steps: list) -> list[tuple[str, str, dict]]:
    """(column tag, step, params): a step that runs twice in a chain (prep around canon) gets prep, prep2."""
    seen: dict[str, int] = {}
    out = []
    for step, params in steps:
        seen[step] = seen.get(step, 0) + 1
        out.append((step if seen[step] == 1 else f"{step}{seen[step]}", step, params))
    return out


def chain_for(image: Path, chain: dict) -> dict:
    """A turnaround sheet skips everything before generate (prep, canon, prep) for prep op views, so
    generate gets all views. canon re-poses one image; a turnaround keeps the pose it was drawn in."""
    sidecar = image.with_suffix(".views.json")
    if not sidecar.is_file():
        return chain
    head = next(i for i, (s, _) in enumerate(chain["build"]) if s == "trellis2")
    views = json.loads(sidecar.read_text(encoding="utf-8"))
    return chain | {"build": [("prep", {"op": "views", "views": views}), *chain["build"][head:]]}


def version_meta(api: str, asset: str, version: str) -> dict:
    metas = call(api, "GET", f"/api/assets/{asset}")["versions"]
    return next(m for m in metas if m["version"] == version)


def bench_image(
    api: str, image: Path, chain: dict, export: str | None = None, to: Path | None = None
) -> dict:
    engine = next(
        (p.get("weights", p.get("preset", "")) for s, p in chain["build"] if s == "trellis2"), ""
    )
    name = image.stem if export else f"bench {engine} {image.stem}"
    asset = call(api, "POST", "/api/assets", {"name": name})["id"]
    name = urllib.parse.quote(image.name)  # a space in a user's file name is not a URL
    up = call(api, "PUT", f"/api/assets/{asset}/upload/{name}", raw=image.read_bytes())
    row, parent = {"image": image.name, "asset": asset, "ok": True}, up["version"]
    for kind in ("build", "check"):
        for tag, step, params in tagged(chain[kind]):
            job, secs, peak = run_job(api, asset, step, parent, dict(params))
            row |= {f"s_{tag}": secs, f"vram_{tag}": peak}
            if job["status"] != "done":
                error = (job.get("error") or {}).get("message", job["status"])
                return row | {"ok": False, "failed_step": tag, "error": error[:200]}
            metrics = version_meta(api, asset, job["version"]).get("metrics", {})
            row[f"torch_{tag}"] = metrics.get("peak_vram_gb")
            for k in COLS.get(step, ()):
                v = metrics.get(k)
                row[f"{tag}_{k}"] = "+".join(map(str, v)) if isinstance(v, list) else v
            if step == "validate":
                path = f"/files/{asset}/{job['version']}/checklist.json"
                checklist = json.loads(call(api, "GET", path))
                rep, uv = checklist["report"], checklist.get("uv") or {}
                p10, p90 = uv.get("texel_density_p10_p90") or (None, None)
                row |= {  # a 2D chain's checklist has no mesh: those columns stay empty (M54)
                    "faces": rep.get("faces"),
                    "non_manifold_edges": rep.get("non_manifold_edges"),
                    "components": rep.get("components"),
                    "texel_p10": p10,
                    "texel_p90": p90,
                    "checklist_fails": metrics.get("fail"),
                }
            if kind == "build":
                parent = job["version"]
    if export:  # batch mode: the finished chain into the game files, and into the user's folder
        job, secs, _ = run_job(api, asset, "export", parent, {"profile": export})
        row["s_export"] = secs
        if job["status"] != "done":
            error = (job.get("error") or {}).get("message", job["status"])
            return row | {"ok": False, "failed_step": "export", "error": error[:200]}
        row["export"] = f"{asset}/{job['version']}"
        if to:  # the files are what was asked for: a copy that did not happen fails the row
            items = [{"asset_id": asset, "version": job["version"]}]
            try:
                copied = call(api, "POST", "/api/export/copy", {"folder": str(to), "items": items})
            except urllib.error.HTTPError as e:
                return row | {"ok": False, "failed_step": "copy", "error": str(e)[:200]}
            if not copied["copied"]:
                reason = copied["skipped"][0]["reason"]
                return row | {"ok": False, "failed_step": "copy", "error": f"not copied: {reason}"}
            row["export"] = copied["copied"][0]["dir"]
    return row


def header(preset: str, weights: str | None) -> list[str]:
    from server.blender_install import blender_exe
    from server.jobs import _python

    def out(cmd):
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, check=False
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    version = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").is_file() else None
    try:
        torch = out([_python("trellis2"), "-c", "import torch; print(torch.__version__)"])
    except RuntimeError:  # no .venv-ml (CI): the header just says so
        torch = None
    blender = out([str(blender_exe()), "--version"])
    return [
        f"commit: {out(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']) or version}",
        f"gpu, driver: {gpu_query('name,driver_version')}",
        f"torch: {torch}",
        f"blender: {blender.splitlines()[0] if blender else None}",
        f"host: {platform.platform()}",
        f"preset: {preset}, weights: {weights or 'preset default'}",
        f"date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]


def bench(
    api: str,
    preset: str,
    set_dir: Path,
    out: Path,
    weights: str | None,
    export: str | None = None,
    to: Path | None = None,
) -> list[dict]:
    chain = json.loads(json.dumps(CHAINS[preset]))  # a copy we may edit
    if export:  # the presets' own check export (godot) gives way to the one asked for
        chain["check"] = [(s, p) for s, p in chain["check"] if s != "export"]
    if weights:
        chain["build"] = [
            (s, PRESETS["trellis2"][weights] if s == "trellis2" else p) for s, p in chain["build"]
        ]
    images = sorted(p for p in set_dir.iterdir() if p.suffix.lower() in IMAGES)
    if not images:
        raise SystemExit(f"no images in {set_dir}: run --make-golden first")
    tags = [t for kind in ("build", "check") for t, _, _ in tagged(chain[kind])]
    steps = [t for kind in ("build", "check") for _, t, _ in tagged(chain[kind])]
    fields = ["image", "asset", "ok", "failed_step", "error"]
    fields += [f"{k}_{t}" for t in tags for k in ("s", "vram", "torch")]
    fields += [f"{t}_{k}" for t, s in zip(tags, steps) for k in COLS.get(s, ())]
    fields += ["faces", "non_manifold_edges", "components", "texel_p10", "texel_p90"]
    fields += ["checklist_fails", "export"]
    if export:
        fields.append("s_export")
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("w", newline="", encoding="utf-8") as f:
        f.writelines(f"# {line}\n" for line in header(preset, weights))
        w = csv.DictWriter(f, fields)
        w.writeheader()
        for image in images:
            print(f"{image.name} ...", flush=True)
            rows.append(bench_image(api, image, chain_for(image, chain), export, to))
            w.writerow(rows[-1])
            f.flush()
    print(summary(rows, steps))
    return rows


def summary(rows: list[dict], steps: list[str]) -> str:
    lines = [f"{sum(r['ok'] for r in rows)}/{len(rows)} succeeded"]
    for s in steps:
        secs = [r[f"s_{s}"] for r in rows if r.get(f"s_{s}") is not None]
        peaks = [r[f"vram_{s}"] for r in rows if r.get(f"vram_{s}") is not None]
        if secs:
            peak = f"{max(peaks):.2f} GB" if peaks else "n/a"
            lines.append(f"  {s}: median {statistics.median(secs):.1f} s, peak {peak}")
    if "rig_fit" in steps:
        fine = [r for r in rows if r["ok"] and r["rig_fit_inside"] == r["rig_fit_joints"]]
        lines.append(
            f"  no marker fix needed: {len(fine)}/{len(rows)} (every joint inside the mesh)"
        )
    return "\n".join(lines)


def make_golden(api: str, set_dir: Path) -> None:
    """prompts.yaml → <name>.png: brief, concepts with the fixed seed, the best-ranked candidate."""
    for g in yaml.safe_load((set_dir / "prompts.yaml").read_text(encoding="utf-8")):
        if (set_dir / f"{g['name']}.png").is_file():  # resume: delete an image to make it again
            continue
        asset = call(api, "POST", "/api/assets", {"name": f"golden {g['name']}"})["id"]
        brief = {"prompt": g["prompt"], "category": g.get("category", "prop")}
        job, _, _ = run_job(api, asset, "brief", None, brief)
        job, secs, _ = run_job(api, asset, "concepts", job.get("version"), {"seed": g["seed"]})
        if job["status"] != "done":
            raise SystemExit(f"{g['name']}: concepts {job['status']} {job.get('error')}")
        scores = json.loads(call(api, "GET", f"/files/{asset}/{job['version']}/scores.json"))
        best = min(scores, key=lambda k: scores[k]["rank"])
        png = call(api, "GET", f"/files/{asset}/{job['version']}/{best}")
        (set_dir / f"{g['name']}.png").write_bytes(png)
        print(f"{g['name']}.png <- {best} ({secs} s)", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--preset", default="game-prop", choices=sorted(CHAINS))
    ap.add_argument("--set", type=Path, default=ROOT / "tests" / "fixtures" / "golden")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "bench" / "phase0.csv")
    ap.add_argument("--weights", choices=GENERATE)
    ap.add_argument("--make-golden", action="store_true")
    ap.add_argument("--export", choices=PROFILES, help="batch mode: export every finished chain")
    ap.add_argument("--to", type=Path, help="batch mode: copy each export into this folder")
    a = ap.parse_args(argv)
    if a.make_golden:
        make_golden(a.api, a.set)
        return 0
    if a.to and not a.export:
        ap.error("--to needs --export")
    if a.to and not a.to.is_dir():  # before the first image's chain runs, not after
        ap.error(f"--to {a.to}: not an existing folder")
    rows = bench(a.api, a.preset, a.set, a.out, a.weights, a.export, a.to and a.to.resolve())
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
