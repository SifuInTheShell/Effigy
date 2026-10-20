# Setting up Effigy on a Windows PC or laptop

A step-by-step guide from a fresh Windows 11 machine to your first generated 3D asset. Written for a Windows 11
PC or laptop with a 16 GB NVIDIA RTX card (the reference machine is an RTX 5080 Laptop, 32 GB RAM).

| You need | Roughly |
|---|---|
| Free disk space | **70 to 160 GB**: AI models 60 to 141 GB (you choose what you want to make), the app's Python + PyTorch ~8 GB, Blender ~1 GB. The models may go on another drive |
| Download | the same, mostly AI models: ~25 min at 500 Mbit/s, ~4 h at 50 Mbit/s for 100 GB |
| Your time | ~15 minutes of clicking; the downloads run on their own |
| Accounts | GitHub (to download the app), Hugging Face (free, for the gated models) |

You install **only the NVIDIA driver** yourself. Everything else (Python, PyTorch, Blender, the models)
is downloaded by the app into its own folder. Nothing is installed system-wide.

Steps 1–6 get you generating. Steps 7–8 (Blender live link, an AI agent) are optional.

---

## Step 1: Update the NVIDIA driver

1. Open the **NVIDIA App** (or get it from nvidia.com) → Drivers → install the latest **Game Ready** or
   **Studio** driver. Version **575 or newer** is needed.
2. Check: open PowerShell (Start → type "PowerShell") and run `nvidia-smi`.

   ✅ Your GPU's name, `Driver Version: 575` or higher, `CUDA Version: 12.8` or higher.

## Step 2: Download and unpack the app

1. On GitHub open **[SifuInTheShell/Effigy → Releases](https://github.com/SifuInTheShell/Effigy/releases/latest)**
   and download **`Effigy-<version>-win64.zip`** under **Assets**.
2. **Before unpacking it**: right-click the zip → **Properties** → tick **Unblock** → OK. (Otherwise
   Windows marks every file as "from the internet" and asks about each script.)
3. Unpack it to a **short path**, e.g. `C:\` → you get `C:\Effigy\`.

(Want the newest code between releases? Build the zip yourself from a checkout: `python tools/package.py`.)

## Step 3: Allow long file paths (recommended, once)

PyTorch has very deep folders. Open PowerShell **as administrator** (right-click → "Run as
administrator") and run:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

Close that window.

## Step 4: First start

Double-click **`run.bat`** in `C:\Effigy`. If Windows shows "Windows protected your PC": **More
info → Run anyway** (the app is not code-signed).

The first start downloads into the app folder: Python 3.12 (~20 MB), PyTorch with CUDA and our GPU
add-ons (~3.5 GB), Blender 5.2.2 (~400 MB) and gltfpack (~1 MB, for LODs). Every download is checked
against a pinned fingerprint. Then your browser opens **http://127.0.0.1:8000**.

✅ The Effigy page with **"GPU: idle"** at the top.

Keep the black window open: it *is* the app. To stop it, close the window (or press Ctrl+C in it).
Later starts take seconds.

## Step 5: Get started (in the app)

On the first start the app opens **Get started** by itself (if you skip it: the library shows a
**Get started** card while nothing is installed, and **Setup** has **Open the guided setup** at the top).
The page has five parts:

1. **Your computer**: green = fine; yellow = it works with less (what to do is written next to it, e.g.
   use the 3D model *Fast* on a 12 GB card); red = fix this first (no NVIDIA GPU, a driver older than 575).
2. **Where the models go**: the app's own `models` folder, or a drive with more room (the free space of
   each is shown). You can move them later in Setup; the app copies, checks and only then deletes.
3. **What you want to make**: *Props and objects* (~60 GB), *Game characters* (~78 GB), *Characters
   with motion* (~102 GB) or *Everything* (~141 GB), each with its download time. If it doesn't fit, the
   page says so: pick another folder in part 2 or a smaller set.
4. **Hugging Face**: some models are shared only with people who accept their licence there.
   1. Create a free account at **https://huggingface.co/join**.
   2. Open each licence link the page shows (DINOv3; Meta Llama 3 for motion), fill in the short form and
      accept. Approval takes minutes to a day; you get an email. The page shows *accepted* once it is.
   3. Create a token of type **Read** at **https://huggingface.co/settings/tokens**, paste it into the
      page and click **Log in**. It goes only into Hugging Face's own login on your computer (where
      `hf auth login` puts it); Effigy does not keep it.
5. **Install**: one button. The features download one after another; you can close the page, the activity
   bar at the bottom shows them.

✅ **"Ready. Make your first asset"** at the end of the page.

- **Still waiting for a licence approval? Install anyway**: everything else downloads, the gated model's
  install ends with *Needs a Hugging Face login first*. Once approved, click **Install** again (in the
  guided setup or on the card in Setup): only that model is left to fetch. Before that, everything but the
  3D generators (Fast, Faithful) and retexturing works: concepts and pictures, the 2D tools, importing your own
  model and preparing, rigging and exporting it, Blender.
- Later, in **Setup**: each feature's card (**Install**, **Repair** when a file is missing or damaged or
  its Python environment is out of date after an update, **Check and repair** to check every file's
  fingerprint), **Where the models are** (move them), **Space on disk** (what takes room, safe cleanups),
  and every component's licence with the credit lines your game must carry ("Built with DINOv3"; exports
  write them into `CREDITS.txt` for you).
- Something red you can't fix, or an install that fails twice: send me the activity bar's log.

## Step 6: Make your first asset

**From a text description**

1. Under **Create**, type what you want, e.g. *"wooden storage crate with iron corner brackets"*, pick a
   category (`prop`), click **Make 4 concepts**.
2. Four images appear over the viewer (if not, click the `…_concepts` entry in the **Versions** list).
   In the dropdown choose **"pick = cut out + generate: fast"**, then click **pick** under the image you
   like.
3. The jobs bar shows the background being removed, then the 3D model being generated; the result
   appears in the viewer. The very first generation is slower (models load from disk, the GPU tunes
   itself once).

**From your own image**: under **Create**, choose an image file, choose **"cut out + generate: fast"**,
click **Create from image**.

**Make it game-ready**

Steps always run on the version selected in the **Versions** list (named like `040_trellis2`). Click the
newest version, open **"Run a step on …"**, type the step name, paste its params, click **Run**. When it
finishes, **click the new version it created** and run the next one:

| Step | Params | What it does |
|---|---|---|
| `clean` | `{}` | fixes holes and stray bits, puts it on the floor |
| `remesh` | `{"faces": 10000}` | reduces it to 10k triangles |
| `uv` | `{}` | unwraps the texture layout |
| `bake` | `{"texture_size": 2048}` | bakes colour, roughness/metal and a normal map |
| `validate` | `{"max_triangles": 10000}` | checklist: watertight, scale, UVs… |
| `export` | `{"profile": "unity"}` | see below |

Export profiles: `unity` and `unreal` (FBX with 4 LOD levels; Unreal also gets a collision hull),
`godot` (GLB + collision), `web` (compressed GLB + USDZ), `print` (STL + 3MF in millimetres), `obj`.
Files land in `C:\Effigy\library\<asset>\<NNN>_export\<profile>\` together with `CREDITS.txt`.

**Retexture** (new textures on the same mesh, e.g. after editing it in Blender): click the mesh version,
run step `trellis2` with the **retexture** preset. By default it uses the picture the mesh was made from;
to use another one, add `"image": "<version>"` with a `…_prep` version of this asset (make one from text
with brief → concepts → **pick** with "cut out only"). The mesh and its UVs stay exactly as they were.

If a job ends with **oom** (out of GPU memory): close GPU-heavy programs (games, video apps, browsers
with many tabs) and run it again with the lower settings the job suggests.

## Step 7 (optional): Live link to Blender

Open any version in Blender, edit it, pull it back as a new version.

1. Open Blender 5.2: your own install, or the app's:
   `C:\Effigy\blender\blender-5.2.2-windows-x64\blender.exe`.
2. **Edit → Preferences → Add-ons → ⌄ (top right) → Install from Disk…** → the add-on `.zip` from your
   Blender MCP Pro download → tick it to enable.
3. In the 3D viewport press **N** → **Blender MCP Pro** tab → **Start**.
4. In Effigy: click a version → **Open in Blender**. Edit, then **Pull from Blender**.

**No Blender MCP Pro licence?** The free, open-source [blender-mcp](https://github.com/ahujasid/blender-mcp)
add-on (MIT) works too: install its `addon.py` the same way (Install from Disk…), then in the N panel →
**BlenderMCP** tab → **Connect to Claude** (it listens on port 9876). The app uses whichever add-on is
running (Pro first). An agent then uses the free add-on's own MCP server, which `uv` downloads on first use;
step 8's `EFFIGY_BLENDER_MCP_PRO_DIR` isn't needed. Don't run both add-ons at once.

## Step 8 (optional): Let an AI agent work with the app

Claude Code or Claude Desktop, Codex, Cursor, VS Code or Gemini can import, generate, check and export
assets for you, and look at the results: the app is an MCP server for them.

1. Start the app (`run.bat`) and open **Setup → Connect an agent**.
2. Pick your agent, click **Copy**, and paste it where the line above the box says (Claude Code: a
   terminal; the others: their settings file). Click **Test the connection** to see the app's tools.
   Use this text, with its absolute paths, and not the `.mcp.json` from the app folder: that one is relative
   (`--project .`) and only works for a Claude Code session started inside the app folder; anywhere else
   `uv` finds no project, exits at once, and the agent reports "connection closed".
3. Restart your agent. Ask it, for example: "import C:\models\crate.fbx into Effigy, check it and
   export it for Godot into C:\games\mygame\assets".

Every step the agent runs appears in the app as a new version; it cannot delete anything. To let it edit
the live Blender scene too, add your Blender MCP Pro server to the agent (step 7 and its folder):

```powershell
[Environment]::SetEnvironmentVariable("EFFIGY_BLENDER_MCP_PRO_DIR", "C:\path\to\blender-mcp-pro", "User")
[Environment]::SetEnvironmentVariable("BLENDER_MCP_PRO_LICENSE", "your-licence-key", "User")
```

They live in your Windows user settings, never in the app folder.

---

## Everyday use

How each part of the app works, page by page: the **[user manual](MANUAL.md)**.

- **Start**: double-click `run.bat`. **Stop**: close its window.
- **Update**: download the newest zip (step 2), unblock it, unpack it **into the same folder** and
  replace files when asked. Your `library\`, `models\`, Python and Blender stay; `run.bat` installs only
  what changed.

## Where things live (all inside `C:\Effigy`)

| Folder | What | Safe to delete? |
|---|---|---|
| `library\` | your assets, every version of each | ❌ that's your work |
| `models\` | AI models | yes: reinstall them in Setup |
| `python\`, `cache\`, `.venv\`, `.venv-ml\`, `.venv-skin\` | Python, PyTorch and the download cache (`.venv-skin`: the skinning model's own environment, a second PyTorch, ~6 GB) | yes: `run.bat` rebuilds them |
| `blender\`, `gltfpack\` | the pinned Blender and gltfpack | yes: `run.bat` downloads them again |

Uninstall = delete the folder (and the two optional user variables from step 8). Models in a folder of
their own (step 5, part 2): delete that folder too.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Windows protected your PC" | More info → Run anyway; or Unblock the zip before unpacking (step 2) |
| "path too long" errors | Step 3, then delete `.venv-ml` and start `run.bat` again |
| Model install ends with `gated` | Step 5, part 4: the licence is not approved yet, or you are not logged in |
| Warning about "symlinks" during downloads | Harmless. To silence it: Settings → System → For developers → Developer Mode on |
| Browser shows nothing at http://127.0.0.1:8000 | Is the `run.bat` window still open, without a red error? Only one copy can run at a time |
| `run.bat` says the port is in use | Another program has port 8000 (the Unreal editor's MCP plugin uses it). In the app folder run `set EFFIGY_PORT=8001` then `run.bat`, and pick a port for good in Setup → App address |
| Job ends with `oom` | Close other GPU programs; use the suggested lower settings |
| Red in step 5, part 1 | Update the driver (step 1). A GPU error: in PowerShell in the app folder run `.\.venv-ml\Scripts\python.exe -m tools.smoke_m1`: it names the failing piece (driver, CUDA, our kernels) |

## What's not ready yet

- Tested on one machine so far (RTX 5080 Laptop, 16 GB); other 16 GB RTX cards should behave the same,
  12 GB cards are best effort (the 3D model *Fast*). The bundled GPU kernels are built for the RTX 40 and
  50 series. **Another card, above all a 24 or 32 GB one? Please measure it**:
  - Keep the app running.
  - In PowerShell in the app folder, run `.\.venv\Scripts\python.exe -m tools.gpu_report`. It takes 20–40 minutes and
    runs each generator once on a test picture, with the app's usual memory safeguards.
  - Send the `gpu-report-….json` it writes to the developer. It holds the card, its memory and timings, and no
    pictures, file names or paths.

  Bigger cards get bigger settings from these reports.
- Rigging covers humanoid characters and eight creature templates (fox, horse, bird, dragon, shark, kaiju,
  spider, snake); anything else needs a template of its own (`docs/PLAN.md` §7).
- The Unity export has not been import-checked yet (Godot and Unreal have).

---

## Appendix: developer setup from Git (instead of the zip)

Only if you want to change the code. Needs Git, uv and Node.js:

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
winget install --id OpenJS.NodeJS.LTS -e
```

Open a new PowerShell window, then:

```powershell
git config --global core.longpaths true
cd C:\
git clone https://github.com/SifuInTheShell/Effigy.git Effigy
cd C:\Effigy
.\run.bat
```

(The working code is on that branch until it's merged into `main`.) Everything from step 5 on is the
same, run inside `C:\Effigy`. Update with `git pull`, then `run.bat`. Build the zip yourself with
`python tools/package.py` → `dist\`.

**AMD GPU (RX 9070 / 9070 XT):** a developer setup from Git, not the zip or `run.bat`:
[docs/howto/amd-rocm-windows.md](docs/howto/amd-rocm-windows.md).
