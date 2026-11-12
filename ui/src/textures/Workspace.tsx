import { useEffect, useRef, useState } from "react";
import {
  api,
  fileUrl,
  type Asset,
  type Meta,
  type TextureInventory,
  type TextureRole,
} from "../api";
import { leave, useBusy } from "../hooks";
import { mainFile } from "../workflow";
import { MaterialPreview, ModelPreview, MapPreview, mapUrl } from "./Preview";
import { Painter } from "./Painter";
import "./textures.css";

type Chain = (
  asset: string,
  parent: string | null,
  steps: [string, object][],
) => Promise<string | null>;
const ROLES: [TextureRole, string][] = [
  ["basecolor", "Base colour"],
  ["roughness", "Roughness"],
  ["metallic", "Metallic"],
  ["normal", "Normal"],
  ["ao", "Ambient occlusion"],
  ["height", "Height"],
  ["emissive", "Emission"],
];
const title = (role: TextureRole) => ROLES.find(([r]) => r === role)![1];
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const ACCEPT = ".png,.jpg,.jpeg,.webp,.tif,.tiff,.tga";

// Keep image generation in its existing resident worker; the CPU step packages its result.
function generationSteps(
  prompt: string,
  size: number,
  tileable: boolean,
): [string, object][] {
  return [
    [
      "concepts",
      {
        op: tileable ? "tile" : "image",
        prompt,
        count: 1,
        ...(tileable
          ? { size: Math.min(size, 1024) }
          : { width: Math.min(size, 2048), height: Math.min(size, 2048) }),
      },
    ],
    ["textures", { op: "settings", tileable }],
  ];
}

export function NewTexture({
  chain,
  onCreated,
  onError,
  missing,
  importOnly = false,
}: {
  chain: Chain;
  onCreated: (id: string, version?: string) => void;
  onError: (e: unknown) => void;
  missing: (group: string) => boolean;
  importOnly?: boolean;
}) {
  const [mode, setMode] = useState(importOnly ? "import" : "generate"),
    [name, setName] = useState(""),
    [prompt, setPrompt] = useState(""),
    [tileable, setTileable] = useState(!importOnly),
    [size, setSize] = useState(1024),
    [color, setColor] = useState("#808080");
  const [files, setFiles] = useState<Partial<Record<TextureRole, File>>>({}),
    [normal, setNormal] = useState("OpenGL");
  const [packing, setPacking] = useState("separate"),
    [packed, setPacked] = useState<File | null>(null);
  const [model, setModel] = useState<File | null>(null),
    [slot, setSlot] = useState(0);
  const [modelMaterials, setModelMaterials] = useState<string[]>([]),
    [modelError, setModelError] = useState("");
  const modelRead = useRef(0);
  const chooseModel = async (file: File | null) => {
    const reading = ++modelRead.current;
    setModel(null);
    setModelMaterials([]);
    setModelError("");
    setSlot(0);
    if (!file) return;
    try {
      if (file.size > 2 ** 30)
        throw new Error("Choose a GLB smaller than 1 GB.");
      const header = new DataView(await file.slice(0, 20).arrayBuffer());
      const length = header.byteLength >= 20 ? header.getUint32(12, true) : 0;
      if (
        header.byteLength < 20 ||
        header.getUint32(0, true) !== 0x46546c67 ||
        header.getUint32(16, true) !== 0x4e4f534a ||
        length > 16 * 1024 ** 2 ||
        length + 20 > file.size
      )
        throw new Error(
          "Choose a readable GLB with a material list smaller than 16 MB.",
        );
      const doc = JSON.parse(await file.slice(20, 20 + length).text());
      if (!Array.isArray(doc.materials) || !doc.materials.length)
        throw new Error(
          "This GLB has no materials. Export it with materials included.",
        );
      if (reading !== modelRead.current) return;
      setModelMaterials(
        doc.materials.map((material: { name?: string }, index: number) =>
          typeof material.name === "string" && material.name.trim()
            ? material.name
            : `Material ${index + 1}`,
        ),
      );
      setModel(file);
    } catch (error) {
      if (reading === modelRead.current) setModelError(message(error));
    }
  };
  const [busy, run] = useBusy();
  const made = useRef<string | null>(null);
  const create = () =>
    run(async () => {
      try {
        const id =
          made.current ??
          (
            await api.createAsset(
              name.trim() || prompt.slice(0, 36) || "New material",
              "texture",
            )
          ).id;
        made.current = id;
        let parent: string | null = null;
        let params: object = {
          op: mode === "generate" ? "generate" : "create",
          prompt,
          tileable,
          size,
          color,
        };
        if (mode === "import") {
          const uploads: Record<string, string> = {};
          for (const [role, file] of Object.entries(files)) {
            const up = await api.upload(id, file);
            uploads[role] = up.version;
            parent = up.version;
          }
          let packedUpload: string | undefined;
          if (packing !== "separate" && packed) {
            const up = await api.upload(id, packed);
            packedUpload = parent = up.version;
          }
          params = {
            op: "import",
            uploads,
            tileable,
            normal_convention: normal,
            ...(packedUpload ? { packed_upload: packedUpload, packing } : {}),
          };
        } else if (mode === "model") {
          const up = await api.upload(id, model!);
          parent = up.version;
          params = {
            op: "extract",
            source_asset: id,
            source_version: parent,
            source_material: slot,
          };
        }
        onCreated(id, parent ?? undefined);
        if (
          await chain(
            id,
            parent,
            mode === "generate"
              ? generationSteps(prompt, size, tileable)
              : [["textures", params]],
          )
        )
          made.current = null;
      } catch (e) {
        onError(e);
      }
    });
  return (
    <div className="texture-new stack">
      <p className="muted">
        Create a texture or a complete material set. No model is needed. Your
        originals and each edit stay in History.
      </p>
      <div className="modes" role="group" aria-label="Start texture from">
        {(importOnly
          ? [
              ["import", "Import texture maps"],
              ["model", "From model GLB"],
            ]
          : [
              ["generate", "Describe a texture"],
              ["import", "Import texture maps"],
              ["blank", "Start with a colour"],
              ["model", "From model GLB"],
            ]
        ).map(([key, label]) => (
          <button
            key={key}
            aria-pressed={mode === key}
            onClick={() => setMode(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <label>
        Material name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Weathered blue timber"
        />
      </label>
      {mode === "generate" && (
        <label>
          Description
          <textarea
            rows={4}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Weathered blue-painted wood planks, flat even lighting, close-up surface texture"
          />
        </label>
      )}
      {mode === "blank" && (
        <label>
          Starting colour
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
          />
        </label>
      )}
      {mode === "import" ? (
        <>
          <p className="muted">
            Import original image files or maps exported from Blender, Unreal or
            Unity. Packed engine maps are split into editable channels. Separate
            maps below override packed channels.
          </p>
          <label>
            Packed map format
            <select
              value={packing}
              onChange={(e) => setPacking(e.target.value)}
            >
              <option value="separate">Separate PBR maps</option>
              <option value="orm">
                glTF / Unreal ORM · R AO, G roughness, B metallic
              </option>
              <option value="unity_mask">
                Unity HDRP Mask · R metallic, G AO, A smoothness
              </option>
              <option value="unity_metallic">
                Unity metallic / smoothness · R metallic, A smoothness
              </option>
            </select>
          </label>
          {packing !== "separate" && (
            <label>
              Packed map
              <input
                type="file"
                accept={ACCEPT}
                onChange={(e) => setPacked(e.target.files?.[0] ?? null)}
              />
            </label>
          )}
          <div className="texture-import-maps">
            {ROLES.map(([role, label]) => (
              <label key={role}>
                {label}
                <input
                  aria-label={`Import ${label.toLowerCase()}`}
                  type="file"
                  accept={ACCEPT}
                  onChange={(e) =>
                    setFiles((all) => {
                      const next = { ...all };
                      if (e.target.files?.[0]) next[role] = e.target.files[0];
                      else delete next[role];
                      return next;
                    })
                  }
                />
              </label>
            ))}
          </div>
          <label>
            Normal convention
            <select value={normal} onChange={(e) => setNormal(e.target.value)}>
              <option>OpenGL</option>
              <option>DirectX</option>
            </select>
          </label>
        </>
      ) : mode === "model" ? (
        <>
          <p className="muted">
            Export a model as GLB with embedded textures from Blender or a game
            engine. Only the selected material is copied into this asset; no
            model is required afterward. Native Unity .mat and Unreal .uasset
            files need their textures exported first.
          </p>
          <label>
            Textured GLB file
            <input
              type="file"
              accept=".glb"
              onChange={(e) => void chooseModel(e.target.files?.[0] ?? null)}
            />
          </label>
          <label>
            Material to import
            <select
              value={slot}
              disabled={!model}
              onChange={(e) => setSlot(Number(e.target.value))}
            >
              {modelMaterials.length ? (
                modelMaterials.map((name, index) => (
                  <option key={index} value={index}>
                    {name}
                  </option>
                ))
              ) : (
                <option value={0}>Choose a GLB first</option>
              )}
            </select>
          </label>
          {modelError && (
            <p className="error" role="alert">
              {modelError}
            </p>
          )}
        </>
      ) : (
        <label>
          Starting width
          <select
            value={size}
            onChange={(e) => setSize(Number(e.target.value))}
          >
            {[256, 512, 1024].map((n) => (
              <option key={n} value={n}>
                {n} px
              </option>
            ))}
          </select>
        </label>
      )}
      {mode !== "model" && (
        <label className="check">
          <input
            type="checkbox"
            checked={tileable}
            onChange={(e) => setTileable(e.target.checked)}
          />{" "}
          Seamless / repeating material
        </label>
      )}
      {mode === "generate" && (
        <p className="muted">
          Generates a colour texture. Import PBR maps, or estimate surface maps
          in the workspace afterwards.
        </p>
      )}
      {mode === "generate" && missing("concepts") && (
        <a href="#/setup?cap=concepts">
          Set up concept images to generate textures
        </a>
      )}
      <button
        className="primary"
        disabled={
          busy ||
          (mode === "generate" && (!prompt.trim() || missing("concepts"))) ||
          (mode === "import" &&
            !Object.keys(files).length &&
            !(packing !== "separate" && packed)) ||
          (mode === "model" &&
            (!model || !Number.isInteger(slot) || slot < 0 || slot > 999))
        }
        onClick={create}
      >
        {busy
          ? "Creating material…"
          : mode === "generate"
            ? "Generate texture"
            : mode === "import" || mode === "model"
              ? "Import material set"
              : "Create texture"}
      </button>
    </div>
  );
}

export function TextureWorkspace(props: {
  asset: Asset;
  version: Meta;
  versions: Meta[];
  chain: Chain;
  pick: (version: string) => void;
  missing: (group: string) => boolean;
  onExit?: () => void;
  onOpen: (id: string, version?: string) => void;
  active?: React.ReactNode;
  regionTools?: React.ReactNode;
}) {
  const { asset, version: selected, versions } = props;
  const v = ["export", "validate"].includes(selected.step)
    ? (versions.find((m) => m.version === selected.parent) ?? selected)
    : selected;
  const [info, setInfo] = useState<TextureInventory | null>(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [materialIndex, setMaterialIndex] = useState(
      Number(v.params.material ?? 0),
    );
  const [role, setRole] = useState<TextureRole>("basecolor"),
    [tab, setTab] = useState("maps"),
    [view, setView] = useState("surface"),
    [compare, setCompare] = useState("");
  const [size, setSize] = useState(2048),
    [prompt, setPrompt] = useState(""),
    [strength, setStrength] = useState(0.5),
    [tileable, setTileable] = useState(false);
  const [brightness, setBrightness] = useState(1),
    [contrast, setContrast] = useState(1),
    [saturation, setSaturation] = useState(1),
    [hue, setHue] = useState(0),
    [roughness, setRoughness] = useState(0.8),
    [metallic, setMetallic] = useState(0);
  const [alphaMode, setAlphaMode] = useState("OPAQUE"),
    [normalConvention, setNormalConvention] = useState("OpenGL"),
    [assets, setAssets] = useState<Asset[]>([]),
    [sourceAsset, setSourceAsset] = useState(""),
    [sourceVersions, setSourceVersions] = useState<Meta[]>([]),
    [sourceVersion, setSourceVersion] = useState(""),
    [mapping, setMapping] = useState("atlas"),
    [repeat, setRepeat] = useState(1),
    [rotation, setRotation] = useState(0);
  const [busy, run] = useBusy();
  useEffect(() => {
    let live = true;
    setInfo(null);
    setError("");
    api.textures(asset.id, v.version).then(
      (result) => {
        if (!live) return;
        setInfo(result);
        const index = Math.min(
          Number(v.params.material ?? 0),
          result.materials.length - 1,
        );
        setMaterialIndex(Math.max(0, index));
        const mat = result.materials[index];
        setTileable(mat?.tileable ?? false);
        setRoughness(mat?.settings.roughnessFactor ?? 0.8);
        setMetallic(mat?.settings.metallicFactor ?? 0);
      },
      (e) => live && setError(message(e)),
    );
    return () => {
      live = false;
    };
  }, [asset.id, v.version]);
  useEffect(() => {
    let live = true;
    api.assets().then(
      (all) =>
        live &&
        setAssets(all.filter((a) => a.kind === "texture" && !a.archived)),
      () => {},
    );
    return () => {
      live = false;
    };
  }, [asset.id, v.version]);
  useEffect(() => {
    let live = true;
    setSourceVersions([]);
    setSourceVersion("");
    if (sourceAsset)
      api.asset(sourceAsset).then(
        (a) => {
          if (!live) return;
          const vs = a.versions.filter(
            (m) => m.outputs.material && m.step !== "export",
          );
          setSourceVersions(vs);
          setSourceVersion(vs.at(-1)?.version ?? "");
        },
        (e) => live && setError(message(e)),
      );
    return () => {
      live = false;
    };
  }, [sourceAsset]);
  const material = info?.materials.find((m) => m.index === materialIndex),
    map = material?.maps[role];
  useEffect(() => {
    if (material) {
      setRoughness(material.settings.roughnessFactor ?? 0.8);
      setMetallic(material.settings.metallicFactor ?? 0);
      setAlphaMode(material.settings.alphaMode ?? "OPAQUE");
    }
  }, [material?.index, v.version, !!material]);
  const url = mapUrl(asset.id, v.version, materialIndex, role);
  const operate = (op: string, params: object = {}) =>
    run(async () => {
      setError("");
      setNotice("");
      try {
        if (
          await props.chain(
            asset.id,
            v.version,
            op === "generate"
              ? generationSteps(prompt, size, tileable)
              : [
                  [
                    "textures",
                    { op, material: materialIndex, role, ...params },
                  ],
                ],
          )
        )
          setNotice(
            "Saved as a new version. Earlier textures remain in History.",
          );
      } catch (e) {
        setError(message(e));
      }
    });
  const replace = async (file: File) => {
    const up = await api.upload(asset.id, file);
    const result = await props.chain(asset.id, v.version, [
      [
        "textures",
        {
          op: "replace",
          material: materialIndex,
          uploads: { [role]: up.version },
          normal_convention: normalConvention,
        },
      ],
    ]);
    if (!result)
      throw new Error(
        "The map was not saved. Your original version is unchanged.",
      );
  };
  const extract = () =>
    run(async () => {
      try {
        const result = await api.extractTexture(
          asset.id,
          v.version,
          materialIndex,
          `${asset.name} · ${material?.name ?? "texture"}`,
        );
        props.onOpen(result.asset.id, result.version.version);
      } catch (e) {
        setError(message(e));
      }
    });
  const retexture = () =>
    run(async () => {
      try {
        setError("");
        const prep = await props.chain(asset.id, v.version, [
          [
            "concepts",
            { op: "image", prompt, count: 1, width: 1024, height: 1024 },
          ],
          ["prep", {}],
        ]);
        if (prep)
          await props.chain(asset.id, v.version, [
            [
              "textures",
              {
                op: "retexture",
                prompt,
                material: materialIndex,
                image: prep,
                size,
              },
            ],
          ]);
      } catch (e) {
        setError(message(e));
      }
    });
  const before = versions.find((m) => m.version === compare);
  const modelFile = info?.model ? info.mesh : undefined;
  const cannotWork = busy || !!props.active;
  return (
    <div className="page texture-workspace">
      <header className="texture-head">
        <div>
          <a href="#/library" className="muted">
            Library
          </a>
          <h1>
            {asset.name}
            <span>Textures</span>
          </h1>
        </div>
        <div className="row">
          {props.onExit && (
            <button
              className="texture-back primary"
              onClick={() => leave() && props.onExit!()}
            >
              <span aria-hidden="true">←</span>
              Back to model
            </button>
          )}
          <label>
            History
            <select
              aria-label="Texture version"
              value={selected.version}
              onChange={(e) => props.pick(e.target.value)}
            >
              {versions.map((m) => (
                <option key={m.version} value={m.version}>
                  {m.version} · {String(m.params.op ?? m.step)}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>
      <div
        className="texture-tabs"
        role="tablist"
        aria-label="Texture workflow"
      >
        {[
          ["maps", "Edit maps"],
          ["upscale", "Upscale"],
          ["enhance", "Enhance"],
          ["create", "Create new"],
          ["apply", "Library"],
        ].map(([key, label], index) => (
          <button
            key={key}
            id={`texture-tab-${key}`}
            role="tab"
            aria-selected={tab === key}
            aria-controls="texture-tools"
            tabIndex={tab === key ? 0 : -1}
            onClick={() => setTab(key)}
            onKeyDown={(event) => {
              const keys = ["ArrowRight", "ArrowLeft", "Home", "End"];
              if (!keys.includes(event.key)) return;
              event.preventDefault();
              const tabs = Array.from(
                event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>(
                  '[role="tab"]',
                ),
              );
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? tabs.length - 1
                    : (index +
                        (event.key === "ArrowRight" ? 1 : -1) +
                        tabs.length) %
                      tabs.length;
              tabs[next].focus();
              tabs[next].click();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="texture-active">{props.active}</div>
      {error && (
        <p className="note error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="note" role="status">
          {notice}
        </p>
      )}
      {!info ? (
        <p className="muted">
          {error
            ? "Choose another version or add a readable texture map."
            : "Reading materials…"}
        </p>
      ) : !material ? (
        <p className="muted">
          This model has no material slots. Add a material in Blender, then pull
          the model back into Effigy.
        </p>
      ) : (
        <div className="texture-layout">
          <section className="texture-stage" aria-label="Texture preview">
            <div className="texture-view-toolbar toolbar">
              <div className="modes" role="group" aria-label="Texture view">
                {[
                  ["surface", info.model ? "Model" : "Material"],
                  ["image", "Map"],
                  ["repeat", "Tiling"],
                  ["paint", "Paint map"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    aria-pressed={view === key}
                    disabled={
                      key === "paint" &&
                      (!map ||
                        role === "normal" ||
                        (role === "height" && map.mode !== "L"))
                    }
                    title={
                      key === "paint" && role === "height" && map?.mode !== "L"
                        ? "Use adjustments or external editing to retain 16-bit height precision."
                        : undefined
                    }
                    onClick={() => {
                      if (leave()) {
                        setView(key);
                      }
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <label>
                Compare with
                <select
                  aria-label="Compare texture version"
                  value={compare}
                  onChange={(e) => setCompare(e.target.value)}
                >
                  <option value="">No comparison</option>
                  {versions
                    .filter(
                      (m) =>
                        m.version !== v.version &&
                        m.step !== "export" &&
                        (info.model
                          ? Object.values(m.outputs).some((f) =>
                              f.endsWith(".glb"),
                            )
                          : !!m.outputs.material),
                    )
                    .map((m) => (
                      <option key={m.version} value={m.version}>
                        {m.version}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            {view === "paint" &&
            map &&
            role !== "normal" &&
            (role !== "height" || map.mode === "L") ? (
              <Painter
                key={url}
                url={url}
                role={role}
                disabled={cannotWork}
                onSave={replace}
              />
            ) : view === "surface" ? (
              modelFile ? (
                <ModelPreview
                  url={fileUrl(asset.id, v.version, modelFile)}
                  before={
                    before
                      ? fileUrl(asset.id, before.version, mainFile(before)!)
                      : undefined
                  }
                />
              ) : (
                <MaterialPreview
                  asset={asset.id}
                  version={v.version}
                  material={material}
                />
              )
            ) : map ? (
              <div
                className={`texture-images ${view === "repeat" ? "repeated" : ""}`}
              >
                {view === "repeat" ? (
                  <div
                    className="texture-tiled"
                    role="img"
                    aria-label="Texture repeated three by three"
                    style={{ backgroundImage: `url("${url}")` }}
                  />
                ) : (
                  <MapPreview
                    asset={asset.id}
                    version={v.version}
                    material={materialIndex}
                    role={role}
                    width={map.width}
                    height={map.height}
                    model={info.model}
                  />
                )}
                {before && (
                  <figure>
                    <img
                      src={mapUrl(
                        asset.id,
                        before.version,
                        materialIndex,
                        role,
                      )}
                      alt={`Comparison ${title(role)} map`}
                    />
                    <figcaption>{before.version}</figcaption>
                  </figure>
                )}
              </div>
            ) : (
              <div className="empty">
                <p>
                  No {title(role).toLowerCase()} map. Import one, or create
                  surface maps from base colour.
                </p>
              </div>
            )}
            <div className="texture-foot">
              <span>
                {info.model
                  ? `${info.rigged ? "Rig preserved · " : ""}${info.animated ? "Animation preserved · " : ""}Texture edits keep the model`
                  : "Standalone material · no model required"}
              </span>
              <span>
                {map
                  ? `${map.width} × ${map.height} · ${map.color_space} · ${map.mode}`
                  : "No map selected"}
              </span>
            </div>
            {typeof v.metrics.seam_ratio === "number" && (
              <p className={`note ${v.metrics.seam_ratio > 1.1 ? "warn" : ""}`}>
                Tiling check: seam ratio {v.metrics.seam_ratio.toFixed(2)}.
                {v.metrics.seam_ratio > 1.1
                  ? " Inspect the repeated view: a border may be visible."
                  : " Inspect repetition at your intended scale too."}
              </p>
            )}
          </section>
          <aside
            id="texture-tools"
            className="texture-panel"
            role="tabpanel"
            aria-labelledby={`texture-tab-${tab}`}
          >
            <label>
              Material
              <select
                aria-label="Texture material"
                value={materialIndex}
                onChange={(e) => {
                  if (leave()) {
                    setMaterialIndex(Number(e.target.value));
                  }
                }}
              >
                {info.materials.map((m) => (
                  <option key={m.index} value={m.index}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <div
              className="texture-map-list"
              role="group"
              aria-label="Texture maps"
            >
              {ROLES.map(([r, label]) => (
                <button
                  key={r}
                  aria-pressed={role === r}
                  onClick={() => {
                    if (leave()) {
                      setRole(r);
                      if (view === "paint") setView("image");
                    }
                  }}
                >
                  <span>{label}</span>
                  <small>
                    {material.maps[r]
                      ? `${material.maps[r]!.width} × ${material.maps[r]!.height}`
                      : "No map"}
                  </small>
                </button>
              ))}
            </div>
            {material.reason && <p className="note warn">{material.reason}</p>}
            {tab === "maps" && (
              <div className="stack">
                <h2>Edit {title(role).toLowerCase()}</h2>
                <p className="muted">
                  Each save creates a version. Replacing a map keeps the other
                  maps and the original model.
                </p>
                <label>
                  Replace / add {title(role).toLowerCase()}
                  <input
                    type="file"
                    accept={ACCEPT}
                    disabled={cannotWork}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file)
                        void run(async () => {
                          try {
                            await replace(file);
                          } catch (e) {
                            setError(message(e));
                          }
                        });
                      e.target.value = "";
                    }}
                  />
                </label>
                {role === "normal" && (
                  <label>
                    Imported normal convention
                    <select
                      value={normalConvention}
                      onChange={(e) => setNormalConvention(e.target.value)}
                    >
                      <option>OpenGL</option>
                      <option>DirectX</option>
                    </select>
                  </label>
                )}
                {map && (
                  <a
                    className="button small"
                    href={url}
                    download={`${asset.id}-${role}.png`}
                  >
                    Download {title(role).toLowerCase()}
                  </a>
                )}
                {map && role !== "normal" && (
                  <>
                    <Range
                      label="Brightness"
                      value={brightness}
                      set={setBrightness}
                    />
                    <Range
                      label="Contrast"
                      value={contrast}
                      set={setContrast}
                    />
                    {["basecolor", "emissive"].includes(role) && (
                      <>
                        <Range
                          label="Saturation"
                          value={saturation}
                          set={setSaturation}
                        />
                        <Range
                          label="Hue"
                          value={hue}
                          set={setHue}
                          min={-180}
                          max={180}
                          step={1}
                        />
                      </>
                    )}
                    <button
                      disabled={cannotWork}
                      onClick={() =>
                        operate("adjust", {
                          brightness,
                          contrast,
                          saturation,
                          hue,
                        })
                      }
                    >
                      Save adjustments
                    </button>
                  </>
                )}
                <details>
                  <summary>Material response</summary>
                  <div className="stack">
                    <Range
                      label="Roughness factor"
                      value={roughness}
                      set={setRoughness}
                      max={1}
                    />
                    <Range
                      label="Metallic factor"
                      value={metallic}
                      set={setMetallic}
                      max={1}
                    />
                    <label>
                      Transparency
                      <select
                        value={alphaMode}
                        onChange={(e) => setAlphaMode(e.target.value)}
                      >
                        <option value="OPAQUE">Opaque</option>
                        <option value="MASK">Cutout</option>
                        <option value="BLEND">Blended alpha</option>
                      </select>
                    </label>
                    <button
                      disabled={
                        cannotWork || !Object.keys(material.maps).length
                      }
                      onClick={() =>
                        operate("settings", {
                          roughness,
                          metallic,
                          alpha_mode: alphaMode,
                        })
                      }
                    >
                      Save material response
                    </button>
                  </div>
                </details>
                {!info.model && (
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={tileable}
                      onChange={(e) => setTileable(e.target.checked)}
                    />{" "}
                    Treat as a repeating texture
                  </label>
                )}
                {!info.model && (
                  <button
                    disabled={
                      cannotWork ||
                      !map ||
                      !["basecolor", "emissive"].includes(role)
                    }
                    onClick={() => operate("seamless", {})}
                  >
                    Blend repeating edges
                  </button>
                )}
              </div>
            )}
            {tab === "upscale" && (
              <div className="stack">
                <h2>Increase texture resolution</h2>
                {info.model && !material.can_upscale && !material.reason && (
                  <p className="note warn">
                    AI atlas upscaling needs UV0 without texture-coordinate
                    transforms. Extract the material to upscale it
                    independently.
                  </p>
                )}
                <p className="muted">
                  AI upscaling sharpens existing colour detail. It can infer
                  detail, so compare the result. Other maps keep their current
                  resolution.
                </p>
                <Size value={size} set={setSize} />
                <p className="muted">
                  Current {map ? `${map.width} × ${map.height}` : "map missing"}
                  .{" "}
                  {info.model
                    ? "UV islands are processed separately to avoid mixing unrelated surfaces."
                    : tileable
                      ? "Uses wrapped borders to preserve repeating edges."
                      : "Processes the image in overlapping windows."}
                </p>
                <button
                  className="primary"
                  disabled={
                    cannotWork ||
                    !map ||
                    size <= map.width ||
                    !["basecolor", "emissive"].includes(role) ||
                    !material.can_upscale ||
                    props.missing("texture")
                  }
                  onClick={() => operate("upscale", { size, tileable })}
                >
                  AI upscale {title(role).toLowerCase()}
                </button>
                {props.missing("texture") && (
                  <a href="#/setup?cap=texture">Set up the image upscaler</a>
                )}
                <button
                  disabled={cannotWork || !map}
                  onClick={() => operate("resize", { size, tileable })}
                >
                  Resize without AI
                </button>
                <p className="muted">
                  Resize also supports data maps. Normal vectors are normalized
                  after resampling.
                </p>
              </div>
            )}
            {tab === "enhance" && (
              <div className="stack">
                <h2>Enhance material details</h2>
                {info.model && !material.can_generate && !material.reason && (
                  <p className="note warn">
                    AI model editing currently requires one mesh and one
                    material with UV0 and no texture-coordinate transforms.
                    Individual map edits remain available.
                  </p>
                )}
                <p className="muted">
                  AI redraws fabric, grain and other fine detail. Colours and
                  identity can change. The geometry and existing data maps stay
                  intact.
                </p>
                <Prompt value={prompt} set={setPrompt} />
                <Range
                  label="Detail strength"
                  value={strength}
                  set={setStrength}
                  min={0.3}
                  max={1}
                />
                <Size value={size} set={setSize} />
                <button
                  className="primary"
                  disabled={
                    cannotWork ||
                    !prompt.trim() ||
                    !material.maps.basecolor ||
                    (info.model && !material.can_generate) ||
                    props.missing("refine")
                  }
                  onClick={() =>
                    operate("enhance", { prompt, strength, size, tileable })
                  }
                >
                  Enhance colour details
                </button>
                {props.missing("refine") && (
                  <a href="#/setup?cap=refine">Set up texture detail</a>
                )}
                {info.model &&
                  material.can_generate &&
                  view === "surface" &&
                  props.regionTools && (
                    <details>
                      <summary>
                        Paint a region / advanced texture detail
                      </summary>
                      {props.regionTools}
                    </details>
                  )}
              </div>
            )}
            {tab === "create" && (
              <div className="stack">
                <h2>
                  {info.model
                    ? "Generate a new appearance"
                    : "Create another texture"}
                </h2>
                <Prompt value={prompt} set={setPrompt} />
                <Size value={size} set={setSize} />
                {info.model && !material.can_generate && !material.reason && (
                  <p className="note warn">
                    Generating a model appearance currently requires one mesh
                    and one material with UV0 and no texture-coordinate
                    transforms.
                  </p>
                )}
                {!info.model && (
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={tileable}
                      onChange={(e) => setTileable(e.target.checked)}
                    />{" "}
                    Seamless / repeating
                  </label>
                )}
                <p className="muted">
                  {info.model
                    ? "Creates a reference image from your description, then generates colour, roughness and metallic maps on the existing UVs. Normal and AO maps stay intact. The original texture stays in History."
                    : "Generates a new colour texture, up to 1K for seamless output or 2K for an ordinary image. Other maps are not carried into a new design."}
                </p>
                <button
                  className="primary"
                  disabled={
                    cannotWork ||
                    !prompt.trim() ||
                    props.missing("concepts") ||
                    (info.model &&
                      (!material.can_generate || props.missing("fast")))
                  }
                  onClick={
                    info.model
                      ? retexture
                      : () => operate("generate", { prompt, size, tileable })
                  }
                >
                  Generate new texture{info.model ? " set" : ""}
                </button>
                <details>
                  <summary>Estimate surface maps</summary>
                  <div className="stack">
                    <p className="muted">
                      Creates height and normals from image brightness, with
                      constant roughness and metallic. These are artistic
                      estimates; painted shadows may become bumps. Existing maps
                      are kept.
                    </p>
                    <Range
                      label="Surface strength"
                      value={strength}
                      set={setStrength}
                      max={4}
                    />
                    <button
                      disabled={cannotWork || !material.maps.basecolor}
                      onClick={() =>
                        operate("surface", { strength, roughness, metallic })
                      }
                    >
                      Create missing surface maps
                    </button>
                  </div>
                </details>
              </div>
            )}
            {tab === "apply" && (
              <div className="stack">
                <h2>Reusable materials</h2>
                <button
                  disabled={cannotWork || !Object.keys(material.maps).length}
                  onClick={extract}
                >
                  Save this set to Library
                </button>
                <p className="muted">
                  An extracted model atlas keeps its UV layout. It does not
                  become a seamless material automatically.
                </p>
                <label>
                  Material from Library
                  <select
                    value={sourceAsset}
                    onChange={(e) => setSourceAsset(e.target.value)}
                  >
                    <option value="">Choose a material</option>
                    {assets.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                </label>
                {sourceAsset && (
                  <label>
                    Material version
                    <select
                      value={sourceVersion}
                      onChange={(e) => setSourceVersion(e.target.value)}
                    >
                      {sourceVersions.map((m) => (
                        <option key={m.version}>{m.version}</option>
                      ))}
                    </select>
                  </label>
                )}
                {info.model && (
                  <>
                    <label>
                      Mapping
                      <select
                        value={mapping}
                        onChange={(e) => setMapping(e.target.value)}
                      >
                        <option value="atlas">
                          Replace atlas maps · matching UV layout
                        </option>
                        <option value="repeat">
                          Repeat a tileable material
                        </option>
                      </select>
                    </label>
                    {mapping === "repeat" && (
                      <>
                        <label>
                          Repeat
                          <input
                            type="number"
                            min={0.1}
                            max={100}
                            step={0.1}
                            value={repeat}
                            onChange={(e) => setRepeat(Number(e.target.value))}
                          />
                        </label>
                        <Range
                          label="Material rotation"
                          value={rotation}
                          set={setRotation}
                          min={-180}
                          max={180}
                          step={1}
                        />
                      </>
                    )}
                  </>
                )}
                <button
                  className="primary"
                  disabled={cannotWork || !sourceVersion}
                  onClick={() =>
                    operate("apply", {
                      source_asset: sourceAsset,
                      source_version: sourceVersion,
                      mapping,
                      repeat,
                      rotation,
                    })
                  }
                >
                  Apply material as new version
                </button>
                <p className="muted">
                  Uses this exact material version. Later Library edits do not
                  silently update this model.
                </p>
              </div>
            )}
            <section className="texture-export stack">
              <h3>Export texture set</h3>
              {v.restricted && (
                <p className="note warn">
                  This version includes restricted source material and cannot be
                  exported.
                </p>
              )}
              <button
                disabled={
                  cannotWork ||
                  v.restricted ||
                  !Object.keys(material.maps).length
                }
                onClick={() =>
                  run(async () => {
                    try {
                      await props.chain(asset.id, v.version, [
                        [
                          "export",
                          { profile: "textures", material: materialIndex },
                        ],
                      ]);
                    } catch (e) {
                      setError(message(e));
                    }
                  })
                }
              >
                Package maps for export
              </button>
              {selected.step === "export" &&
                Object.entries(selected.outputs).map(([key, file]) => (
                  <a
                    key={key}
                    href={fileUrl(asset.id, selected.version, file)}
                    download
                  >
                    {file.split("/").pop()}
                  </a>
                ))}
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}

function Range({
  label,
  value,
  set,
  min = 0,
  max = 2,
  step = 0.05,
}: {
  label: string;
  value: number;
  set: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <label className="texture-range">
      <span>
        {label}
        <output>
          {Number.isFinite(value) ? Number(value.toFixed(2)) : "—"}
        </output>
      </span>
      <input
        aria-label={label}
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => set(Number(e.target.value))}
      />
    </label>
  );
}
function Size({ value, set }: { value: number; set: (value: number) => void }) {
  return (
    <label>
      Output width
      <select value={value} onChange={(e) => set(Number(e.target.value))}>
        {[256, 512, 1024, 2048, 4096].map((size) => (
          <option key={size} value={size}>
            {size} px{size >= 1024 ? ` · ${size / 1024}K` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
function Prompt({
  value,
  set,
}: {
  value: string;
  set: (value: string) => void;
}) {
  return (
    <label>
      Description
      <textarea
        rows={3}
        value={value}
        onChange={(e) => set(e.target.value)}
        placeholder="Fine woven linen, worn edges, keep the blue colour"
      />
    </label>
  );
}
