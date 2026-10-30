# GEM-X: what we do differently from upstream's demo

The vendored code is unmodified. Every difference lives in `steps/mocap.py`; each has a test or a measured
reason (research 12, IMPLEMENTATION M34).

| What | Why | Where / test |
|---|---|---|
| The DDIM switch sets `regression_only = False` on every module that has the flag | upstream's `demo_soma_onnx.py --ddim` sets it on `model.pipeline`, while the sampler reads it on the diffusion module: its "DDIM" run was bit-identical to regression (measured, research 12) | `steps/mocap.py::gem`; the spike's mode comparison |
| The person detector is YOLOX-L from `yzd-v/DWPose` (COCO, Apache-2.0), decoded and NMS'd by us, tracked by overlap | upstream fetches mmpose's YOLOX "humanart" at run time from openmmlab; its training data's terms (Human-Art) are unverified for commercial use | `yolox_boxes`, `track`; `tests/test_mocap.py::test_track_follows_the_chosen_person` |
| `onnxruntime-gpu < 1.24` and `preload_dlls` from torch's `lib` | 1.24+ is built for CUDA 13 (`cublasLt64_13.dll`), torch 2.10 cu128 brings CUDA 12.8; on the CPU the detector took 110 ms a frame, on the GPU 10.6 ms | `pyproject.toml` group `gemx`, `gem` |
| A `wandb` stub module | only GEM-X's training helpers use wandb (`gem/utils/tools.py` imports it at the top); a heavy telemetry package for nothing | `_engine` |
| DINOv3's code unpacked from a pinned zip into `TORCH_HOME/hub/facebookresearch_dinov3_main` with a `trusted_list` | ViTPose calls `torch.hub.load("facebookresearch/dinov3", …)` for the class only (weights from `vitpose.pth`): pinned to `6876159`, nothing fetched at run time | `_engine` |
| SOMA's assets from its Git LFS at `e0f8ff0` (`py-soma-x` 0.1.3) | without `inputs/soma_assets`, SOMA fetches `nvidia/soma-x` at `main` (0.3.3), whose `SOMA_neutral.npz` lacks `joint_parent_ids` this code reads (the spike's first failure) | `tools/download_models.py` FILES |
| Our own BVH writer with an upright rest | GEM-X's `export_soma_bvh` writes offsets in SOMA's joint-orient frames; a BVH has no rest rotations, so its rest skeleton lies along X and our retarget tumbled the character | `bvh_text`; `tests/test_mocap.py::test_bvh_reads_back_to_the_same_posed_joints` |
| The clip at 30 fps, at most 720 × 1280; a still camera, or a panning one's turn read off the background (M64) | GEM-X's rate; its demo reads the whole clip into RAM (4K: ~10 GB); 720 × 1280 is what the spike measured (a lower cap is untested); its camera tracking (VO) is not in the release, so `camera_path` (OpenCV corners, LK flow, a RANSAC homography through its K) fills `R_w2c` / `cam_angvel` for a camera that turns on the spot | `prepare_video`, `camera_path`; `tests/test_mocap.py::test_prepare_video_resamples_trims_and_scales`, `::test_camera_path_reads_a_pan_off_the_background_and_a_still_camera_as_still` |
