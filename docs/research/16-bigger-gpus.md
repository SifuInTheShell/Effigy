# 16 · Bigger GPUs: what a 24 or 32 GB card should get (research + plan)

The owner, 2026-10-12: a user with a more capable GPU, "with 32GB for example", should be able to download and run
bigger models instead of being held to the 16 GB setup. The app should check what the machine has, pick the
"best fit" for each model, and keep the safeguards. Researched 2026-10-13 from this repo (the M45 code and the bench
notes) and from primary sources: the Hugging Face API's licence fields, file sizes and parameter counts, and the
model cards. Every VRAM number is marked **measured** (on the laptop), **claimed** (a source's) or **est.** (mine).
Build plan: §6, proposed **M46a–c**, not yet in IMPLEMENTATION.md §4. **The owner's decisions (§7) came the same
day, and so did the laptop measurements** (§5.1, `docs/bench/m46.md`).

## 1. Bottom line

1. **Most of the gain needs no new download.** On 16 GB, three steps shuffle parts of their models between the CPU
   and the GPU. A bigger card can keep them on the GPU:
   - canon (FLUX.2 klein)
   - motion (Kimodo's 16 GB text encoder)
   - trellis2's `low_vram` stages

   The same models also have bigger settings that the 16 GB presets leave out: TRELLIS 2's `1536_cascade`, 4K
   textures and more steps. PLAN §5's **Hero Character** preset (1536, 4K) was planned for exactly this and never
   built.
2. **There is one clear bigger download: the full refine Tile.** It is 6.7 GB against the lite file's 2.0 GB, and
   cleaner (m43b). It was measured at 15.6 GB reserved, and it restarted the laptop. The code already refuses it
   below 20 GB, so all that is missing is a Setup card that offers it when the card fits.
3. **The obvious bigger models are out or unproven:**
   - **FLUX.2 klein 9B** is under the FLUX Non-Commercial License (already "never used" in PLAN §6).
   - **Qwen-Image-Edit-2511** (Apache-2.0, 57.7 GB download, about 20.5 GB at fp8) lost to klein on this laptop in
     m16.
   - **HiDream-O1-Image** is "MIT" on its card, but its repo has no LICENSE file.

   Both edit models stay spikes until a bake-off on a big card beats klein's 9/10.
4. **The budget picks, not a tier name.** Every GPU step already gets `EFFIGY_VRAM_BUDGET_GB` (M45). A step uses
   that number to skip its offload when the model fits, and the presets carry their own needs, so "best fit" is
   `presets.lighter()`, which already returns "the largest variant that fits". The cap, the refusal, the RAM check
   and the out-of-memory suggestion stay as they are.
5. **Measuring is the hard part.** The laptop cannot go above ~14.6 GB. The 24/32 GB numbers come from users:
   `tools/gpu_report.py` measures a card through the app's own jobs and makes a report to send in (§5.2).
   - Measured on the laptop:
     - **20 steps + 4K fit 16 GB** (same peak, +25 % time).
     - **1536 does not**: the whole card reached 15.5 of 15.9 GB with torch inside its cap, host RAM fell to
       ~2 GB, and the app's worker once ended without a word.
   - The M45 cap bounds torch, not the whole card, so 1536 needs its own entry in the table.
6. **Found on the way:** a 24 GB RTX 3090, A5000 or A6000 cannot run TRELLIS here at all. Our CUDA wheels are built
   for `8.9;12.0` (`.github/workflows/wheels.yml`), Ampere is `8.6`, and there is no PTX. `machine.check` would still
   report such a card as fine.

## 2. What we have (the M45 safeguards)

| Piece | Where | What it does |
|---|---|---|
| `VRAM_GB` | `server/presets.py` | A GPU step's measured peak on the laptop, by the one param that picks the model (trellis2: `weights`) |
| `budget_gb()` | `server/presets.py` | What a step may allocate: what is free now, minus 0.3 GB of context and a 0.5 GB margin (14.65 GB on the idle laptop) |
| the cap | `steps/_common._cap_vram` | torch inside the step process cannot pass the budget, so going over is a clean out-of-memory error, never a spill |
| the refusal | `server/jobs._refusal` | Refuses a variant that never fits this card, or that does not fit beside other programs now, and names the lighter one |
| `LOWER` | `server/presets.py` | The next-lower params after an out-of-memory error |
| `RAM_GB` | `server/presets.py` | Host RAM to load the models (trellis2 20 GB) |
| `machine.check` | `server/machine.py` | The card, its budget, and the variants it can never run (`too_big`); the generator menu greys them out with the reason |
| peaks in `meta.json` | every GPU step's metrics | trellis2, canon, concepts, refine, mocap, parts, bake and SkinTokens write `torch.cuda.max_memory_allocated()` |

Gaps for bigger cards:
- `VRAM_GB["trellis2"]` keys on `weights` only, so a `1536_cascade` run counts as 1024's 13.3 GB. The step already
  accepts 1536, 4096 textures and up to 50 steps, so an agent or Advanced can ask for them today, and only the cap
  stands between them and the card.
- `EFFIGY_PRESET_TIER` (`"16"`, set in `server/jobs._env`) is read by nothing.

## 3. What a bigger card can buy

### 3.1 Same models, less shuffling: speed, no download

| Step | On 16 GB today | On a bigger card | Gain | Needs |
|---|---|---|---|---|
| `canon` (FLUX.2 klein 4B) | `enable_model_cpu_offload()`: **measured** 9.23 GB, ~27 s an image, ~42 s with the load (m16) | the whole pipeline on the GPU: the weights are 14.9 GB, so **est.** ~17 GB (claimed ~13; **measured**: out of memory under a 12.5 GB cap, m46) | the per-image CPU↔GPU moves (est. 5–10 s an image) | budget ≥ 17 (est.): **24 GB** (M46a) |
| `motion` (Kimodo) | the Llama 3 8B text encoder on the CPU (`TEXT_ENCODER_DEVICE=cpu`): **measured** first prompt 30.7 s, then 3.7 s; 16 GB of host RAM while encoding (m26) | `TEXT_ENCODER_DEVICE=cuda`: Kimodo's README: "requires ~17GB of VRAM to generate locally entirely on GPU" (**claimed**) | a faster first prompt (est. 10–20 s less) and 16 GB of host RAM freed | budget ≥ ~18: **24 GB** |
| `trellis2` | `low_vram`: each sub-model moves to the GPU for its stage only | all stages stay on the GPU | the stage moves inside every generation (not measured) | not known: Pixal3D's single-view weights are 24 GB on disk (the dtype on the card is to be read); **measure on a big card** |
| `concepts` | DiT stored fp8 (`enable_layerwise_casting`), the text encoder moved off after encoding | bf16 storage, the encoder kept | **none worth having**: m10 measured fp8 storage faster than bf16 (2.5 vs 2.9 s a step), the swaps cost ~5 s a batch, and the fp8 is computed in bf16 | – |

### 3.2 Same models, bigger settings: quality, no download

- **`1536_cascade`** gives finer geometry and a finer O-Voxel volume for every later bake.
  - TRELLIS.2's card says "at least 24GB", verified on A100/H100 (**claimed**, not specific to 1536); it gives H100
    times of 1024³ ~17 s and 1536³ ~60 s, so 1536 takes about 3.5× as long.
  - Pixal3D without `--low_vram` defaults to 1536 (its card).
  - Research 09 has 1536 ≈ 32 GB from the ComfyUI wrapper author's RTX 5090 (**claimed**, not found again on
    2026-10-13).
  - PLAN §2.2 lists "`1536_cascade` with low-VRAM offload" for the 16 GB tier: **Fast at 1536 may fit the laptop.**
- **More steps (16–20) and 4096 textures** need no more memory: **measured** on the laptop (m46), Faithful at
  20 steps + 4K peaked at 11.86 GB for the whole card against 11.73 at 12 steps + 2K, with torch at 9.20 in both.
  Sampling took 128 s against 100 s. The "16 steps / 4K ≈ 17 GB" from research 09 was the wrapper without
  `low_vram`. **A 16 GB quality option.**
- **1536 on 16 GB, measured** (m46):
  - Fast at `1536_cascade` sampled inside the cap (torch 12.62 GB), but the whole card reached 15.52 of 15.92 GB.
  - Host RAM fell to ~2 GB available while the 23.2 M-face mesh (1024: 10.8 M) was simplified.
  - In the app's worker it ended without a word; as a one-shot step it finished in 655 s.
  - **Not for 16 GB.** Faithful at 1536 was not tried.
- Where it shows: a game character is remeshed to 15k triangles and baked at 2K, so for `game-character` 1536 mostly
  feeds the bake source. It shows in hero characters, close-ups and prints. The bench must show it before it becomes
  anyone's default.

### 3.3 Bigger models: downloads

| Candidate | Licence (primary source) | Download | VRAM | Verdict |
|---|---|---|---|---|
| **Full refine Tile** `Z-Image-Turbo-Fun-Controlnet-Tile-2.1-2601-8steps` (alibaba-pai, same repo and revision as the lite file) | Apache-2.0 (HF licence field) | 6.71 GB (lite: 2.02) | **measured** 15.57 GB reserved (m43b); it restarted the laptop | **Yes, first.** `full` is already the code default and is refused below 20 GB; offer it on Setup only when the card fits |
| FLUX.2 klein 9B | FLUX Non-Commercial License (its card) | ~29 GB | – | **Out**: PLAN §6 "never used" |
| Qwen-Image-Edit-2511 (re-pose and redraw) | Apache-2.0 (HF field and card; **no LICENSE file in the repo**) | 57.7 GB: a 20.4B bf16 transformer (41 GB) + a 16.6 GB text encoder | **claimed** fp8 ~20.5 GB for the transformer alone, so 32 GB in practice | **Spike only.** m16 ran it at Q4 with offload on 16 GB: > 660 s an image, while klein passed 9/10 at 27 s. Worth it only if a 32 GB bake-off beats klein |
| HiDream-O1-Image (8.8B, edit up to 2048², skeleton conditioning) | "mit" in the card's metadata, **no LICENSE file in the repo** | 35.2 GB (stored fp32) | ~18 GB in bf16 (est.), so 24 GB | **Spike only**, after a LICENSE file exists. PLAN §3 already lists it for a re-pose bake-off. Own code (not diffusers); its card warns about torch 2.9 |
| Z-Image (Base) | Apache-2.0 | 20.5 GB | same size as Turbo | **Out**: not bigger, 28–50 steps, and its own card says Turbo has the higher visual quality |
| A bigger Kimodo | – | – | – | none exists (SOMA-RP v1.1 is the one; SMPLX-RP is under the NVIDIA R&D licence, out) |
| BiRefNet_HR | MIT | 0.4 GB | small | not a tier question (it fits anywhere); a separate cut-out item if one is ever needed |

## 4. Which cards

| Budget (`budget_gb`, idle desktop) | Cards | Our wheels |
|---|---|---|
| ~14.6 GB (today's tier) | 4060 Ti 16G, 4070 Ti Super, 4080, 5060 Ti 16G, 5070 Ti, 5080 | ✔ sm_89, sm_120 |
| ~22.7 GB | **4090** (sm_89) | ✔ |
| ~22.7 GB | **3090, 3090 Ti, A5000** (sm_86) | **✘: TRELLIS's kernels are not built for 8.6** |
| ~30.5 GB | **5090** (sm_120) | ✔ |
| 46+ GB | RTX 6000 Ada (sm_89), RTX PRO 6000 Blackwell (sm_120) ✔; A6000 (sm_86) ✘ | as listed |

**Update (issue #16):** the wheels are now built for `8.6;8.9;12.0`, so the sm_86 rows above become ✔ once
`wheels.yml` has run; `MIN_SM` is 8.6.

The 3090 is probably the most common 24 GB card among hobbyists. Covering it means building the wheels with
`8.6;8.9;12.0`: one `wheels.yml` run, the owner's call (build minutes). Either way, `machine.check` should read the
compute capability and say so, instead of passing the card and failing in the first generation.

Bigger cards usually sit in machines with 64 GB of RAM. The RAM check stays per step (`RAM_GB`), and a new variant
gets its own row once measured.

## 5. Measuring without a bigger card

1. **On the laptop, done 2026-10-13** (`docs/bench/m46.md`, through the tool below):
   - Faithful at 20 steps + 4K fits: 11.86 GB for the whole card, the same as 12 steps + 2K.
   - Fast at 1536 does not fit 16 GB: the card reached 15.52 of 15.92 GB, and the app's worker once ended without a
     word.
   - Faithful at 1536 was not tried.
   - Canon without the offload does not fit (M46a): out of memory under a 12.5 GB cap while loading; its weights are
     14.9 GB.
2. **Above 16 GB: users send their numbers** (the owner, 2026-10-13: "i would rely on other users to send me in their
   gpu statistics"). **`tools/gpu_report.py`** is built for this:
   - It runs 13 probes as ordinary jobs on a scratch asset, through the running app, so the cap, the refusal and the
     RAM check hold.
     - Each probe whose models are downloaded runs: Fast and Faithful at 1024, 20 steps + 4K, and 1536, the
       texture-detail chain, the standing pose, Kimodo.
     - The 1536 probes are skipped on a budget below their measured need (`NEED`, ≥ 15.1 GB).
   - It writes `gpu-report-<card>-<date>.json`: the card, driver, compute capability, budget, RAM, OS and torch, then
     per probe its outcome, seconds, whole-card and torch peaks. Paths and the user name are stripped from messages.
   - `--send [file]` opens a new GitHub issue with the report filled in. That works once the repo is public (the
     owner plans to); until then the file is attached by hand.
   - A user runs `uv run python -m tools.gpu_report` in the app folder while the app runs, which takes 20–40 minutes.
   - Until a number is measured it goes into `VRAM_GB` as an estimate, commented `# est.`. The safeguards still hold.

## 6. Build plan (proposed; each is one PR)

**M46 tooling** (this branch, `m46-gpu-report`):
- `tools/gpu_report.py`.
- A resident worker that dies now says how: `the trellis2 worker exited <code> without ERROR` (Windows crash codes
  in hex). Before, it said "no RESULT".
- The laptop's numbers (`docs/bench/m46.md`).

**M46a: the budget skips the offload** (no downloads; the speed of §3.1).
- `canon`, `motion` and `trellis2` read `EFFIGY_VRAM_BUDGET_GB`:
  - canon: the whole pipeline on the GPU at ≥ its measured need (measured on the laptop under the cap first)
  - motion: `TEXT_ENCODER_DEVICE=cuda` at ≥ ~18 GB
  - trellis2: `low_vram` off once a user's report has measured it
- A wrong threshold costs one out-of-memory error, and `LOWER` gains `{"offload": true}` for the retry.
- Drop `EFFIGY_PRESET_TIER`.
- `tools/gpu_report.py` gains the matching probes.

**M46b: bigger presets, the best fit as the default** (no downloads; the quality of §3.2).
- `VRAM_GB["trellis2"]` keys on the weights *and* the pipeline type. 1536 counts as ≥ 15.1 GB (measured lower bound,
  from `gpu_report.NEED`), and `RAM_GB` gets a 1536 row (est. ≥ 28 GB: the worker's ~18 GB plus the 1536 mesh).
- New presets, named after what they are, not after a card:
  - `faithful-hq`: 20 steps, 4K (fits 16 GB, measured)
  - `faithful-hd`: `1536_cascade`, 4K, 16 steps (24 GB and up)
  - `fast-hd`, only if a user's report shows Fast at 1536 is worth having
- **The default is the best fit on every card** (the owner: "quality over speed should be the default, but the user
  may choose other"):
  - The generate form preselects the largest preset this card fits (`presets.lighter`), with its time.
  - A faster one stays one click away, and the user's choice is kept.
  - The menu already greys out what this card can never run, with the reason.
- PLAN §5's **Hero Character** chain (`hero-character`: canon → the best-fit Faithful → quads 30–50k → 4K → rig).
- `machine.check` reads the compute capability: a card below sm_89 (the 3090, A5000, A6000) gets a plain "not
  supported: the 3D models need an RTX 40 or 50 series card". The owner skips the 3090, so there is no 8.6 wheel
  build.
- Check: the laptop picks `faithful-hq` (§7.6), a faked 24/32 GB card `faithful-hd`. A close-up of 2K vs 4K on two
  or three golden assets goes into the bench note.

**M46c: Setup offers what the card fits** (downloads; §3.3).
- A download group can carry a `min_gb`. Setup shows a group above the card's budget as "needs a 24 GB card" and does
  not offer it, the same wording as the generator menu.
- First group: `refine-full` (the full Tile, ≥ 20 GB). `refine` uses full when it is downloaded and fits, else lite.
- Kept for later (the owner): Qwen-Image-Edit-2511 (32 GB) and HiDream-O1-Image (24 GB, once it has a LICENSE file)
  as `canon` engines. Each gets a bake-off against klein from a user's big card, and PLAN §6 first.
- Check: Setup on the laptop hides the group, and a test with a faked 24 GB card (`machine._gpu`, as the M45 card
  layouts in `tests/test_gpu_safeguards.py` do) shows it.

Order: M46 tooling → M46a → M46b → M46c. The 24/32 GB numbers land as users send reports.

## 7. Decisions

The owner, 2026-10-13:
1. **Measuring**: other users send their GPU statistics, through a small tool that measures and sends. Built:
   `tools/gpu_report.py` (§5.2). The repo goes public later, and `--send` works from then on.
2. **The default on bigger cards**: quality over speed, unless the user decides otherwise. So the best fit is the
   default (M46b).
3. **The RTX 3090**: skipped. No 8.6 wheels; the check says it is not supported (M46b).
4. **Bigger edit models**: kept as later spikes (M46c).
5. **The laptop measurements**: "go ahead". Run 2026-10-13 (`docs/bench/m46.md`).
6. **Quality over speed on every card** (after the laptop results): "quality over speed should be the default, but
   the user may choose other". On 16 GB the default becomes `faithful-hq` (20 steps + 4K: same memory, +25 % time);
   Fast and today's Faithful stay one click away (M46b).

## Sources (2026-10-13)

- Hugging Face API (`/api/models/<repo>?blobs=true`): licence fields, file sizes, parameter counts for
  alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1, Qwen/Qwen-Image-Edit-2511, HiDream-ai/HiDream-O1-Image,
  Tongyi-MAI/Z-Image(-Turbo), ZhengPeng7/BiRefNet_HR
- https://huggingface.co/microsoft/TRELLIS.2-4B, https://huggingface.co/TencentARC/Pixal3D
- https://huggingface.co/black-forest-labs/FLUX.2-klein-9B, https://huggingface.co/black-forest-labs/FLUX.2-klein-4B
- https://huggingface.co/Tongyi-MAI/Z-Image
- https://github.com/nv-tlabs/kimodo (README: the ~17 GB all-GPU figure, `TEXT_ENCODER_DEVICE`)
- https://lilting.ch/en/articles/qwen-image-edit-2511-local-specs (Qwen sizes, community)
- https://ai-muninn.com/en/blog/dgx-spark-zimage-turbo-nvfp4-bench (Z-Image working sets, community)
- This repo: docs/bench/m10, m16, m26, m43b, m45; research 01, 02, 07, 09; PLAN §2.2, §5, §6
