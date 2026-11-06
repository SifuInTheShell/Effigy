// M39: Import: a model file (or a .zip of one with its textures) becomes an asset; import_model reads it in Blender, keeps a
// skeleton the app knows and its clips, and brings anything else in as a still model (docs/IMPLEMENTATION.md M39)
import { useEffect, useRef, useState } from "react";
import { NewTexture } from "./textures/Workspace";
import { api, type Asset, type Meta } from "./api";
import { useBusy } from "./hooks";
import { actsAs } from "./workflow";

type Chain = (asset: string, parent: string | null, steps: [string, object][]) => Promise<string | null>;
export const MODEL_TYPES = [".glb", ".gltf", ".fbx", ".obj", ".stl", ".ply", ".usd", ".usda", ".usdc", ".usdz", ".zip"];
export const ANIM_TYPES = [".fbx", ".glb", ".gltf", ".bvh"]; // an animation made elsewhere (steps/import_motion)
const MAX = 2 ** 30; // the server's cap
const SKELETONS: Record<string, string> = { mixamo52: "humanoid (Mixamo-style)", fox: "four-legged animal (fox)", horse: "hoofed animal (horse)", bird: "bird", kaiju: "two-legged monster with a tail (kaiju)", shark: "shark or fish", dragon: "winged dragon", spider: "spider (eight legs)", snake: "snake (no legs)" };
const mb = (n: number) => `${(n / 1e6).toFixed(1)} MB`;
const count = (n: number, what: string) => `${n} ${what}${n === 1 ? "" : "s"}`;

export function ImportPage(props: {
  hidden: boolean;
  chain: Chain;
  assets: Asset[] | null;
  onCreated: (id: string, version?: string) => void;
  onError: (e: unknown) => void;
}) {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [still, setStill] = useState(false);
  const [over, setOver] = useState(false);
  const [inputKey, setInputKey] = useState(0); // remounts the file input to clear it
  const [busy, run] = useBusy();
  const made = useRef<string | null>(null); // the asset a failed upload created: retry in it, not in a new one
  const [mode, setMode] = useState<"model" | "animation" | "texture">("model");
  useEffect(() => {
    if (!props.hidden && new URLSearchParams(location.hash.split("?")[1]).get("kind") === "texture") setMode("texture");
  }, [props.hidden]);
  const choose = (f: File | null) => {
    setFile(null);
    setNote(null);
    if (!f) return;
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!MODEL_TYPES.includes(ext)) return setNote(`${f.name}: this isn't a model file the app reads (${MODEL_TYPES.join(", ")}).`);
    if (f.size > MAX) return setNote(`${f.name} is ${mb(f.size)}: at most 1 GB.`);
    setFile(f);
  };
  const clear = () => (choose(null), setInputKey((k) => k + 1));
  const submit = () =>
    run(async () => {
      try {
        let id = made.current;
        if (!id) made.current = id = (await api.createAsset(name.trim() || file!.name.replace(/\.\w+$/, ""))).id;
        const up = await api.upload(id, file!);
        props.onCreated(id, up.version);
        made.current = null;
        setName("");
        setStill(false);
        clear();
        await props.chain(id, up.version, [["import_model", still ? { rig: false } : {}]]);
      } catch (e) {
        props.onError(e);
      }
    });
  return (
    <div className="page" hidden={props.hidden}>
      <div className="head">
        <h1>Import</h1>
      </div>
      <div className="form">
        <div className="modes" role="group" aria-label="What to import">
          <button aria-pressed={mode === "model"} onClick={() => setMode("model")}>
            A model
          </button>
          <button aria-pressed={mode === "animation"} onClick={() => setMode("animation")}>
            An animation for a character
          </button>
          <button aria-pressed={mode === "texture"} onClick={() => setMode("texture")}>Textures / Materials</button>
        </div>
        {mode === "model" ? (
          <>
            <h2>Bring in a model you already have</h2>
            <p className="muted">
              FBX, glTF or GLB, OBJ, USD or USDZ, STL and PLY. A model that comes with other files (an OBJ with its .mtl and textures, a .gltf with its .bin) goes
              in as one .zip: right-click its folder, Send to, Compressed folder. A character on a Mixamo-style skeleton, or one exported from this app, keeps its
              skeleton and animations; anything else comes in as a still model you can rig here.
            </p>
            <label>
              Name (optional)
              <input value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <div
              className={`drop ${over ? "over" : ""}`}
              onDragOver={(e) => (e.preventDefault(), setOver(true))}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => (e.preventDefault(), setOver(false), choose(e.dataTransfer.files[0] ?? null))}
            >
              <label>
                Model file or .zip
                <input
                  key={inputKey}
                  type="file"
                  accept={MODEL_TYPES.join(",")}
                  aria-invalid={!!note}
                  aria-describedby={note ? "model-file-error" : undefined}
                  onChange={(e) => choose(e.target.files?.[0] ?? null)}
                />
              </label>
              <span className="muted">…or drop the file here.</span>
              {file && <span>{`${file.name} · ${mb(file.size)}`}</span>}
              {note && (
                <span className="error" role="alert" id="model-file-error">
                  {note}
                </span>
              )}
              {file && <button onClick={clear}>Remove file</button>}
            </div>
            <label className="check">
              <input type="checkbox" checked={still} onChange={(e) => setStill(e.target.checked)} />
              Keep it as a still model (ignore its skeleton and animations)
            </label>
            <button className="primary" disabled={busy || !file} onClick={submit}>
              {busy ? "Importing…" : "Import"}
            </button>
          </>
        ) : mode === "texture" ? (
          <NewTexture importOnly chain={props.chain} onCreated={props.onCreated} onError={props.onError} missing={() => false} />
        ) : (
          <AnimationImport assets={props.assets} chain={props.chain} onError={props.onError} />
        )}
      </div>
    </div>
  );
}

// an animation made elsewhere onto a character skinned here: the file becomes a motion of that asset
// (import_motion), then goes onto its newest skinned version (anim), which opens and plays (owner 2026-10-12)
function AnimationImport(props: { assets: Asset[] | null; chain: Chain; onError: (e: unknown) => void }) {
  const [target, setTarget] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [busy, run] = useBusy();
  const choose = (f: File | null) => {
    setFile(null);
    setNote(null);
    if (!f) return;
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!ANIM_TYPES.includes(ext)) return setNote(`${f.name}: this isn't an animation file the app reads (${ANIM_TYPES.join(", ")}).`);
    if (f.size > MAX) return setNote(`${f.name} is ${mb(f.size)}: at most 1 GB.`);
    setFile(f);
  };
  const submit = () =>
    run(async () => {
      try {
        const d = await api.asset(target);
        const skin = d.versions.filter((x) => actsAs(x) === "rig_skin").at(-1);
        if (!skin) return setNote(`${d.asset.name} has no skinned version yet: open it, Character, Attach to skeleton, then import the animation.`);
        const up = await api.upload(target, file!);
        setFile(null);
        setInputKey((k) => k + 1);
        const motion = await props.chain(target, up.version, [["import_motion", { for: skin.version }]]);
        if (motion) await props.chain(target, skin.version, [["anim", { clips: [motion] }]]);
      } catch (e) {
        props.onError(e);
      }
    });
  const characters = (props.assets ?? []).filter((a) => !a.archived);
  return (
    <>
      <h2>Put an animation on one of your characters</h2>
      <p className="muted">
        An animation made elsewhere, as FBX, GLB or glTF, or BVH, on a Mixamo, Unreal (Manny, Quinn) or Kimodo skeleton. It goes onto the character's newest
        skinned version as a new version that plays in the viewer and exports with the model. A file with several animations brings in the first; its
        page offers the others.
      </p>
      <label>
        Character
        <select value={target} onChange={(e) => (setTarget(e.target.value), setNote(null))}>
          <option value="">Choose a character…</option>
          {characters.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Animation file
        <input
          key={inputKey}
          type="file"
          accept={ANIM_TYPES.join(",")}
          aria-invalid={!!note}
          aria-describedby={note ? "anim-file-error" : undefined}
          onChange={(e) => choose(e.target.files?.[0] ?? null)}
        />
      </label>
      {file && <span>{`${file.name} · ${mb(file.size)}`}</span>}
      {note && (
        <p className="error" role="alert" id="anim-file-error">
          {note}
        </p>
      )}
      <button className="primary" disabled={busy || !target || !file} onClick={submit}>
        {busy ? "Importing…" : "Import animation"}
      </button>
    </>
  );
}

const FACTORS: [string, number][] = [
  ["Centimetres", 0.01],
  ["Millimetres", 0.001],
  ["Inches", 0.0254],
  ["×100", 100],
  ["×1000", 1000],
];

/** Under an import_model version: what came in, and a scale fix when its size looks off. */
export function ImportInfo(props: { asset: string; version: Meta; chain: Chain }) {
  const v = props.version;
  const m = v.metrics ?? {};
  const [busy, run] = useBusy();
  const scale = Number(v.params?.scale ?? 1);
  const h = Number(m.height_m ?? 0);
  const hint = m.scale_hint as number | undefined;
  const off = !!hint || (h > 0 && (h < 0.05 || h > 50));
  const clips = (m.clips as string[] | undefined) ?? [];
  const missing = (m.missing_textures as string[] | undefined) ?? [];
  const merged = Number(m.merged_bones ?? 0);
  const dropped = (m.dropped_meshes as string[] | undefined) ?? [];
  const again = (params: Record<string, unknown>) => v.parent && run(() => props.chain(props.asset, v.parent!, [["import_model", params]]));
  const keepRig = v.params?.rig === false ? { rig: false } : {};
  const factors = hint ? [...FACTORS.filter(([, k]) => k === hint), ...FACTORS.filter(([, k]) => k !== hint)] : FACTORS;
  return (
    <section aria-label="What came in">
      <h3>What came in</h3>
      <dl className="facts">
        <dt>File</dt>
        <dd>{String(m.format ?? "").replace(/^\./, "").toUpperCase()}</dd>
        <dt>Height</dt>
        <dd>
          {h.toFixed(2)} m{scale !== 1 && ` (scaled ×${scale})`}
        </dd>
        <dt>Triangles</dt>
        <dd>{Number(m.triangles ?? 0).toLocaleString()}</dd>
        <dt>Materials</dt>
        <dd>
          {count(Number(m.materials ?? 0), "material")}, {count(Number(m.textures ?? 0), "texture")}
        </dd>
        <dt>Skeleton</dt>
        <dd>
          {m.skeleton
            ? `${SKELETONS[String(m.skeleton)] ?? m.skeleton}, kept${merged ? ` (${count(merged, "extra bone")} merged)` : ""}`
            : !m.skeleton_note || m.skeleton_note === "no skeleton"
              ? "none"
              : `not kept: ${m.skeleton_note}`}
        </dd>
        {clips.length > 0 && (
          <>
            <dt>Animations</dt>
            <dd>{clips.join(", ")}</dd>
          </>
        )}
      </dl>
      {!!m.turned && <p className="note">It faced away: turned round to face forward, as the app's characters do.</p>}
      {dropped.length > 0 && (
        <p className="note warn">
          Left out, not attached to the skeleton: {dropped.join(", ")}. Import it again as a still model to keep every part.
        </p>
      )}
      {missing.length > 0 && (
        <p className="note warn">
          Textures not found: {missing.join(", ")}. Import the model's folder as a .zip to bring them along.
        </p>
      )}
      {off && (
        <div className="note warn stack" role="group" aria-label="Scale">
          <p>
            It is {h.toFixed(2)} m tall: {hint ? (hint < 1 ? `about ${Math.round(1 / hint)}× too big` : `about ${hint}× too small`) : "outside 0.05–50 m"}. Was
            it made in another unit?
          </p>
          <div className="row">
            {factors.map(([name, k]) => (
              <button key={name} disabled={busy} className={k === hint ? "primary" : ""} onClick={() => again({ ...keepRig, scale: +(scale * k).toPrecision(6) })}>
                {name}
              </button>
            ))}
          </div>
        </div>
      )}
      {!!m.skeleton && (
        <button disabled={busy} onClick={() => again({ scale, rig: false })} title="Without its skeleton and animations, to rig it in the app">
          Import again as a still model
        </button>
      )}
    </section>
  );
}

/** Under an upload that holds a model file but no import of it yet (or a failed one): import it. */
export function OriginalFile(props: { asset: string; version: Meta; chain: Chain }) {
  const [busy, run] = useBusy();
  const [still, setStill] = useState(false);
  const v = props.version;
  return (
    <section>
      <h3>Original file</h3>
      <p className="muted">{String(v.params?.name ?? v.outputs.model)}: read it in to work on it.</p>
      <label className="check">
        <input type="checkbox" checked={still} onChange={(e) => setStill(e.target.checked)} />
        Keep it as a still model (ignore its skeleton and animations)
      </label>
      <button className="primary" disabled={busy} onClick={() => run(() => props.chain(props.asset, v.version, [["import_model", still ? { rig: false } : {}]]))}>
        Import it
      </button>
    </section>
  );
}
