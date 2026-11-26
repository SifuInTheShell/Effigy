# 19. 8 GB cards and 16 GB of RAM: the Fast model on smaller machines

Status: plan, proposed 2026-11-26, nothing built. Source: andrakis/TRELLIS.2-stableprojectorz at `269b74c` (MIT,
built on IgorAherne/TRELLIS.2-stableprojectorz, which first fitted TRELLIS.2 onto 8 GB cards), compared line by line
with our vendored `engine/trellis2/trellis2` and `engine/trellis2/patches.py`. Every saving below is the fork's claim
or an estimate; none has been measured on our build yet.

## 1. Bottom line

- **Goal:** an 8 GB NVIDIA card runs the core 3D path with the Fast model (picture → 3D model → prepare → rig →
  animate → export), and a 16 GB-RAM PC can run the 3D model step at all.
- **Today:** Setup rates an 8 GB card "bad" and refuses every 3D model (`docs/bench/m45.md`: RTX 4060 Laptop 8 GB,
  6.8 GB per step). Fast peaks at 8.1 GB allocated / 9.0 GB reserved in `decode_shape_slat` (`docs/bench/phase0.md`);
  sampling stays at ≤ 6.9 GB. Fast also needs ~18–20 GB of host RAM to load (`server/presets.py` `RAM_GB`), so a
  16 GB-RAM PC is refused whatever its GPU.
- **Not in scope:** the image models over an 8 GB card's 6.7 GB budget (concepts 8.5, canon 9.2, refine/redraw 12.3,
  depth 9.0, layers 7.8, Pixal3D Faithful 13.3). They stay greyed out with their reason on 8 GB cards. Each would
  need its own work; none is touched by this fork.
- **Possible bonus:** Pixal3D ships its own copy of the same decoder (`engine/pixal3d/pixal3d/models/sc_vaes/
  sparse_unet_vae.py`), so the decoder patch may lower Faithful's decode too: Faithful on 12 GB cards, Faithful HD
  (1536, 19.4 GB today) on 16 GB. Measure, don't promise.

## 2. What an 8 GB / 16 GB machine gets, if it all lands

| Machine | Today | After M73–M74 (VRAM) | After M76 (RAM) |
|---|---|---|---|
| 8 GB card, 32 GB RAM | no 3D model | Fast + prepare + rig + animate + export | same, with RAM to spare |
| 8 GB card, 16 GB RAM | no 3D model (RAM) | still no 3D model (RAM) | Fast + the rest |
| 12 GB card | Fast | Fast; Faithful if the Pixal3D decode drops enough | same |
| 16 GB (the reference laptop) | Fast, Faithful, Faithful HQ | maybe Faithful HD | the model resident at ~2 GB of RAM instead of ~18 |

Stays unavailable on 8 GB: Faithful and the HD/HQ presets, text-to-picture concepts, the character-sheet cleanup,
AI texture refine/redraw/enhance, depth and parallax layers.

## 3. What we already have, and what the fork adds

| Technique | Fork | Effigy today | Plan |
|---|---|---|---|
| One model on the GPU per stage (`low_vram`), cache emptied between stages | yes | yes (upstream, `steps/trellis2.py`) | – |
| `cudaMallocAsync` (fragmentation: 23.7 → 9.0 GB reserved) | no | yes (`server/jobs.py` `_env`) | keep |
| Chunked explicit GEMM for the sparse convs | yes (`conv_flex_gemm.py`) | yes, our own `submconv_explicit_chunked`, but only installed on ROCm (`patches.py` `explicit_gemm_on_rocm`) | M73 |
| Decoder: skip features parked on the CPU, chunked skip-add, LayerNorm and MLP in row chunks, large `h.feats` round-tripped to the CPU | yes (`models/sc_vaes/sparse_unet_vae.py`, 11 hunks; ~0.9–1.3 GB per resblock by its comments) | no | M74 |
| Neighbour map built with the features on the CPU, then streamed | yes (`conv_flex_gemm.py` ~50–80) | no | M75, only if needed |
| Lean loading (meta device, no throwaway fp32 copy), CPU weights reused across GPU round trips, read-only memory-mapped weights, fp16 "twins" of the bf16 flow models, `MIMALLOC_PURGE_DELAY=0` | yes (README "Lower System-RAM Use"; 24 → 1.7 GB at rest, 512 peak 11.7 GB, +2–4 % time, bit-identical) | only `no_random_init` (`patches.py`) | M76 |

**Not ported, ever:** the fork's flash-attn/xformers attention (we use SDPA), RMBG-2.0 and nvdiffrast defaults (we
have BiRefNet, `uvraster` and our own bake), its DINOv3 loader (ours is pinned), its debug prints (`[CONV]`,
`[TIMING]`, `SPARSE_DEBUG`). As with every TRELLIS change: runtime patches in `engine/trellis2/patches.py`, recorded in
`engine/trellis2/PATCHES.md`; the vendored `engine/trellis2/trellis2/` stays unmodified.

## 4. Switching it on only where it is needed

The chunked paths cost time (PCIe round trips, more kernel launches). A 16 GB or bigger card should not pay for
them. Each patch is installed by `steps/trellis2.py` (and the Pixal3D equivalent) only when the step's budget
(`EFFIGY_VRAM_BUDGET_GB`, set by `server/jobs.py`) is below the variant's measured unpatched peak
(`presets.VRAM_GB`), and `EFFIGY_LOW_VRAM=1|0` forces it either way for measuring. The RAM work (M76) is cheap at run
time and helps every machine, so it is on everywhere once measured.

## 5. Build plan (each one PR, in this order)

### M73: chunked explicit GEMM on NVIDIA + the measuring harness

- `patches.py`: let `explicit_gemm_on_rocm` (renamed `explicit_gemm`) install `submconv_explicit_chunked` on CUDA
  too, under §4's switch.
- `tools/gpu_report.py`: an `--emulate-gb N` option that runs Fast with `EFFIGY_VRAM_RESERVE_GB` set so the step's
  cap equals an N GB card's budget (`budget_gb(N)`, 6.7 for 8), and records per-stage `max_memory_allocated`,
  `max_memory_reserved`, the whole-card peak (NVML), time per stage and the mesh's face count.
- Effort: under half a day of code, plus the laptop run.
- **Acceptance, here:** unit test that the switch installs the chunked conv when the budget is below the peak and
  not above it; ruff. **On the laptop:** Fast on the golden crate, character and creature with the switch off and
  on, unemulated and at `--emulate-gb 8`: peak per stage, decode time, mesh identical in face count and no new
  holes or inverted faces (`validate`). Result in `docs/bench/m73.md`.
- If the decode peak is already ≤ 6.5 GB here, M74 shrinks to the Pixal3D bonus.

### M74: chunked decoder (TRELLIS.2 and Pixal3D)

- Port the fork's `sparse_unet_vae.py` changes as monkeypatches of the decoder's resblocks (upsample and plain):
  skip features to the CPU, skip-add / LayerNorm / MLP in row chunks (the fork uses 200k–1M rows; make the chunk
  derive from the budget), the CPU round trip of large `h.feats` only above a size threshold. Same patch applied to
  Pixal3D's copy if its classes match (they share upstream's file).
- Effort: 1–2 days.
- **Acceptance, here:** a CPU test that the patched resblock's output equals the unpatched one on a small random
  sparse tensor (`torch.allclose`, same dtype), chunk sizes 1 row to all rows. **On the laptop:** `--emulate-gb 8`
  Fast decode ≤ 6.5 GB reserved on all three golden assets; meshes compared with the unpatched run (face count
  within 0.1 %, Hausdorff distance ~0, same validate result); decode time recorded. Pixal3D Faithful unemulated and
  at `--emulate-gb 12`: peak and time; Faithful HD at 16 GB if Faithful's decode dropped. `docs/bench/m74.md`.
- Then: `presets.VRAM_GB` gains the patched peaks per variant (keyed so `machine.check` offers Fast on an 8 GB card,
  and Faithful/HD wherever they now fit), and m45's card table is redone.

### M75 (only if M74 leaves the decode above ~6.5 GB): neighbour map on the CPU

- Port `conv_flex_gemm.py`'s neighbour-map construction with the features on the CPU, streamed in chunks, under the
  same switch. Effort ~1 day. Acceptance as M74 (identical output test, emulated peak, time).

### M76: lower host RAM

- In order of cost, each measured before the next:
  1. `MIMALLOC_PURGE_DELAY=0` in `server/jobs._env` (check it affects torch 2.8 on Windows at all; one line).
  2. Lean loading: build the models on the meta device and assign the checkpoint tensors (`load_state_dict(...,
     assign=True)`), replacing `no_random_init`.
  3. Keep the CPU copy of each model's weights across its GPU round trips instead of re-allocating it.
  4. Read-only memory-mapped weights (safetensors mapped from file; Windows can drop them under pressure without the
     pagefile).
  5. fp16 twins of the bf16 flow models (the fork's `make_fp16_twins.py`, ~12 GB of disk), so they can be mapped
     instead of converted. Written once by Setup into `models/`, SHA-256 recorded like every download.
- Effort: 2–3 days. Windows-specific behaviour (the fork notes safetensors' own mapping is charged in full there).
- **Acceptance, here:** a test that a lean-loaded model's state dict equals the normally loaded one, bit for bit.
  **On the laptop:** Fast and Faithful process peak RSS / private bytes and resident-worker RSS at rest, before and
  after each item; outputs hash-identical to the unpatched run; first-load time from a cold disk. Target: the Fast
  process peak ≤ ~10 GB so it fits a 16 GB PC beside Windows and a browser. `docs/bench/m76.md`.
- Then: `presets.RAM_GB` takes the measured peaks, `machine.RAM_LOW` and Setup's "16 GB cannot run the 3D model"
  wording change, m45's note too.

### M77: the tiers, said in the app

- Setup's GPU card for 8 GB: "Fast only: pictures you bring → 3D model → prepare, rig, animate, export", with the
  greyed-out list and why. The generator menu already greys out what does not fit (M46b); check the wording on an
  emulated 8 GB card in Playwright. MANUAL, README requirements, PLAN §2 table, HANDOVER.

## 6. Measuring without the hardware

- **8 GB VRAM:** `tools/gpu_report.py --emulate-gb 8` on the reference laptop caps torch at 6.7 GB. It does not
  reproduce a smaller card's other tenants (display, other apps), so the whole-card peak is recorded too and
  checked against 8 GB.
- **16 GB RAM:** the laptop has 32 GB; record the process peak and compare with a 16 GB budget (16 − ~4 for Windows
  − ~2 for a browser ≈ 10 GB).
- **Real hardware:** a call for testers on GitHub (an 8 GB card, ideally in a 16 GB PC) once M74 and M76 are on a
  branch, with the same `gpu_report` JSON; the issue #16 tester has 64 GB of RAM and a 16 GB card, so not them.

## 7. Risks

- **Speed:** chunking and CPU round trips slow the decode; §4 limits that to cards that need it. The fork states
  no decoder timing; M73/M74 measure it.
- **Correctness:** chunked LayerNorm and MLP are exact in principle; the tests compare outputs, and the meshes are
  compared on real assets.
- **Upstream drift:** monkeypatches against pinned upstream (`75fbf01`); a TRELLIS bump re-runs the equality tests.
- **Disk:** +12 GB for the fp16 twins (M76 item 5); skipped if items 1–4 already reach the target.

## 8. Decisions for the owner

1. Scope: M73 + M74 first (VRAM, plus the possible Faithful-on-smaller-cards bonus), M76 after, or the RAM work
   first because 16 GB-RAM machines are the more common blocker?
2. Is a slower decode acceptable on 8–12 GB cards (it never applies to 16 GB+ under §4)?
3. Is +12 GB of disk for the fp16 twins acceptable, or only if items 1–4 fall short?
4. A call for 8 GB / 16 GB-RAM testers on GitHub once M74 is on a branch?

**Decided (owner, 2026-11-26):**
1. Available VRAM always goes first: M73–M75 before M76.
2. Yes: a slower decode on the cards that need the chunked paths is acceptable.
3. Yes: +12 GB of disk for the fp16 twins is acceptable.
4. Open.

## Sources

- andrakis/TRELLIS.2-stableprojectorz, `269b74c` (2026-11-27): README ("Lower System-RAM Use", measured table),
  `trellis2/models/sc_vaes/sparse_unet_vae.py`, `trellis2/modules/sparse/conv/conv_flex_gemm.py`,
  `make_fp16_twins.py`. MIT (Microsoft's licence, unchanged).
- IgorAherne/TRELLIS.2-stableprojectorz: "Optimized the code to fit better into 8GB gpus, even with 1024³ voxels".
- Ours: `docs/bench/phase0.md` (Fast's decode peak), `docs/bench/m45.md` (card layouts, RAM), `server/presets.py`
  (`VRAM_GB`, `RAM_GB`, `budget_gb`), `engine/trellis2/patches.py`, `engine/trellis2/PATCHES.md` (the deferred "SPZ
  chunked decode + EXPLICIT_GEMM" row), PLAN §2 (the 8–12 GB tier).
