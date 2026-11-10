// Thin fetch wrappers for IMPLEMENTATION.md §3.5.

/** M40: what to paste into each agent so it reaches this install's MCP server (paths filled in at runtime). */
export type AgentSetup = { root: string; server: string; uv: string | null; clients: { id: string; name: string; how: string; text: string }[] };
export type Meta = {
  version: string;
  step: string;
  parent: string | null;
  created: string;
  params: Record<string, unknown>;
  outputs: Record<string, string>;
  metrics: Record<string, unknown>;
  provenance: string;
  restricted: boolean;
  starred?: boolean;
  label?: string;
  licenses?: string[]; // licenses.yaml row names (M42)
};
/** M42: tags, licenses (union of its versions'), restricted (any version) and imported (any imported model) ride on the listing. */
export type Asset = {
  id: string;
  name: string;
  created: string;
  versions?: number;
  updated?: string;
  archived?: boolean;
  tags?: string[];
  licenses?: string[];
  restricted?: boolean;
  imported?: boolean;
  kind?: string; // "2d" for a 2D asset (research 15, the 2D contract); absent = a 3D asset
};
export type CopyResult = {
  copied: { asset_id: string; version: string; dir: string; files: string[] }[];
  skipped: { asset_id: string; version: string; reason: string }[];
};
/** Research 18: one manifest item; `action` from a dry run, `status` once it runs. */
export type BatchItem = { name: string; file: string; tags: string[]; restricted: boolean; license: string; action: "new" | "update" | "skip"; reason?: string; status?: string; asset_id?: string; version?: string; error?: string };
export type Batch = { batch_id: string; status: "running" | "done" | "cancelled"; items: BatchItem[]; counts: Record<string, number> };
export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";
export type TextureRole = "basecolor" | "roughness" | "metallic" | "normal" | "ao" | "height" | "emissive";
export type TextureMaterial = {
  index: number; name: string; maps: Partial<Record<TextureRole, { width: number; height: number; mode: string; color_space: string }>>;
  settings: Record<string, any>; tileable: boolean; uv?: boolean; reason?: string;
  can_generate: boolean; can_upscale: boolean;
};
export type TextureInventory = { model: boolean; mesh?: string; materials: TextureMaterial[]; rigged?: boolean; animated?: boolean; manifest?: Record<string, any> };
export type Presets = Record<string, Record<string, Record<string, unknown>>>;
export type Model = {
  id: number;
  name: string;
  groups: string[];
  revision: string;
  where: string;
  installed: boolean; // every file the manifest lists is there at its size (M35b)
  partial: boolean; // some are: Repair finishes it
  bytes: number;
  missing_bytes: number;
};
export type Check = { id: string; level: "ok" | "warn" | "bad"; label: string; detail: string; budget_gb?: number; too_big?: TooBig[] };
// M45: a variant this card can never run; M46b: the 3D model by its preset, `reason` when the card cannot run it at
// all; M46c: `group`, the download that variant needs
export type TooBig = { step: string; param: string | null; value: string | null; need_gb: number | null; reason?: string; group?: string };
export type TrashEntry = { name: string; deleted: string; asset_id: string; asset_name: string } & (
  | { kind: "asset"; versions: number }
  | { kind: "versions"; versions: string[]; first: Record<string, unknown> }
);
export type PortInfo = { running: number; next: number; source: "env" | "settings" | "default"; default: number };
// Setup's Blender card: Open in Blender starts `exe` when none answers; `found`: the connected Blender's path
export type BlenderSetup = { start: boolean; exe: string | null; found: string | null };
export type Folder = {
  path: string;
  source: "env" | "settings" | "default";
  bytes: number;
  choices: { path: string; drive: string; free: number; total: number }[];
};
export type Preset = { id: string; name: string; what: string; groups: string[]; bytes: number; missing: number };
export type HfStatus = { token: boolean; user: string | null; access: Record<string, boolean | null>; gated: Record<string, string[]> };
export type Env = { name: string; state: "ok" | "missing" | "outdated" | "installing" | "unknown"; groups: string[] };
export type StorageItem = { id: string; kind: string; name: string; bytes: number; drive: string; action: string | null };
export type Drive = { drive: string; total: number; free: number; low: boolean };
export type Storage = {
  scanned: number;
  seconds?: number;
  items: StorageItem[];
  assets: { id: string; name: string; archived: boolean; bytes: number }[];
  drives: Drive[];
  freed?: number;
  failed?: number;
};
export type Licence = {
  name: string;
  kind: string;
  license: string;
  commercial: boolean;
  attribution?: string;
  flag?: string;
  region_note?: string;
  experimental?: boolean;
};

async function call<T>(method: string, path: string, body?: BodyInit | object): Promise<T> {
  const raw = body instanceof Blob || body === undefined;
  const r = await fetch(path, {
    method,
    headers: raw ? undefined : { "content-type": "application/json" },
    body: raw ? (body as BodyInit | undefined) : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  // keep the backend's code (gated, oom, busy, restricted, missing_input…) so the UI can route on it
  if (!r.ok)
    throw Object.assign(new Error(data.error?.message ?? `${method} ${path}: ${r.status}`), {
      code: (data.error?.code ?? String(r.status)) as string,
    });
  return data as T;
}

export const api = {
  assets: () => call<Asset[]>("GET", "/api/assets"),
  asset: (id: string) => call<{ asset: Asset; versions: Meta[] }>("GET", `/api/assets/${id}`),
  createAsset: (name: string, kind?: "2d" | "texture") => call<Asset>("POST", "/api/assets", kind ? { name, kind } : { name }),
  textures: (id: string, version: string) => call<TextureInventory>("GET", `/api/assets/${id}/versions/${version}/textures`),
  textureUV: (id: string, version: string, material: number) => call<{ triangles: number[][][] }>("GET", `/api/assets/${id}/versions/${version}/textures/uv?material=${material}`),
  extractTexture: (id: string, version: string, material: number, name: string) => call<{ asset: Asset; version: Meta }>("POST", `/api/assets/${id}/versions/${version}/textures/extract`, { material, name }),
  renameAsset: (id: string, name: string) => call<Asset>("PATCH", `/api/assets/${id}`, { name }),
  archiveAsset: (id: string, archived: boolean) => call<Asset>("PATCH", `/api/assets/${id}`, { archived }),
  patchTags: (id: string, tags: string[]) => call<Asset>("PATCH", `/api/assets/${id}`, { tags }),
  batchPlan: (source: string, license: string) => call<{ items: BatchItem[] }>("POST", "/api/import/batch", { source, license, dry_run: true }),
  batchStart: (source: string, license: string) => call<{ batch_id: string; items: number }>("POST", "/api/import/batch", { source, license }),
  pick: (kind: "folder" | "file") => call<{ path: string | null }>("POST", "/api/import/pick", { kind }),
  batch: (id: string) => call<Batch>("GET", `/api/import/batch/${id}`),
  batchCancel: (id: string) => call<{ ok: boolean }>("POST", `/api/import/batch/${id}/cancel`, {}),
  exportCopy: (folder: string, items: { asset_id: string; version: string }[]) => call<CopyResult>("POST", "/api/export/copy", { folder, items }),
  patchVersion: (id: string, version: string, body: { starred?: boolean; label?: string }) =>
    call<Meta>("PATCH", `/api/assets/${id}/versions/${version}`, body),
  deleteAsset: (id: string) => call<{ ok: boolean }>("DELETE", `/api/assets/${id}`),
  deleteVersion: (id: string, version: string) => call<{ deleted: string[] }>("DELETE", `/api/assets/${id}/versions/${version}`),
  fitCheck: (id: string, version: string, joints: Record<string, number[]> = {}) =>
    call<FitQuality>("POST", `/api/assets/${id}/versions/${version}/fit-check`, { joints }),
  clips: (template = "mixamo52") => call<{ name: string; file: string }[]>("GET", `/api/clips?template=${encodeURIComponent(template)}`),
  agentSetup: () => call<AgentSetup>("GET", "/api/agent/setup"),
  agentTest: () => call<{ ok: boolean; tools?: string[]; error?: string }>("POST", "/api/agent/test"),
  upload: (id: string, file: File) => call<Meta>("PUT", `/api/assets/${id}/upload/${encodeURIComponent(file.name)}`, file),
  steps: () => call<Presets>("GET", "/api/steps"),
  run: (id: string, step: string, parent: string | null, params: object) =>
    call<{ job_id: string }>("POST", `/api/assets/${id}/steps/${step}`, { parent, params }),
  job: (jobId: string) => call<JobInfo>("GET", `/api/jobs/${jobId}`),
  cancel: (jobId: string) => call<{ ok: boolean }>("POST", `/api/jobs/${jobId}/cancel`),
  blenderOpen: (id: string, version: string) =>
    call<{ ok: boolean; version: string; objects: string[] }>("POST", `/api/assets/${id}/blender/open`, { version }),
  blenderPull: (id: string) => call<{ version: string }>("POST", `/api/assets/${id}/blender/pull`, {}),
  blenderStatus: () => call<{ link: "pro" | "free" | null; start?: boolean }>("GET", "/api/blender/status"),
  blenderSetup: () => call<BlenderSetup>("GET", "/api/setup/blender"),
  setBlenderSetup: (start: boolean, exe: string) => call<BlenderSetup>("POST", "/api/setup/blender", { start, exe }),
  models: () => call<Model[]>("GET", "/api/models"),
  installModels: (group: string, repair = false) => call<{ job_id: string }>("POST", "/api/models/install", { group, repair }),
  environments: () => call<Env[]>("GET", "/api/environments"),
  machine: () => call<Check[]>("GET", "/api/setup/machine"),
  folder: () => call<Folder>("GET", "/api/setup/folder"),
  port: () => call<PortInfo>("GET", "/api/setup/port"),
  trash: () => call<TrashEntry[]>("GET", "/api/trash"),
  restoreTrash: (name: string) => call<{ asset_id: string; versions: string[] }>("POST", `/api/trash/${encodeURIComponent(name)}/restore`),
  setPort: (port: number) => call<PortInfo>("POST", "/api/setup/port", { port }),
  chooseFolder: (path: string) => call<{ path: string; job_id: string | null; left?: string | null }>("POST", "/api/setup/folder", { path }),
  presets: () => call<Preset[]>("GET", "/api/setup/presets"),
  firstRun: () => call<{ first_run: boolean }>("GET", "/api/setup/first-run"),
  welcomed: () => call<{ ok: boolean }>("POST", "/api/setup/welcomed"),
  hf: () => call<HfStatus>("GET", "/api/hf"),
  hfLogin: (token: string) => call<{ user: string }>("POST", "/api/hf/login", { token }),
  hfLogout: () => call<{ ok: boolean }>("POST", "/api/hf/logout"),
  licenses: () => call<Licence[]>("GET", "/api/licenses"),
  modelSizes: () => call<Record<string, number>>("GET", "/api/models/sizes"),
  storage: (refresh = false) => call<Storage>("GET", `/api/storage${refresh ? "?refresh=true" : ""}`),
  drives: () => call<Drive[]>("GET", "/api/storage/drives"),
  cleanup: (action: string) => call<Storage>("POST", "/api/storage/cleanup", { action }),
};

// how well a skeleton sits in its mesh (steps/_rig.fit_quality)
export type FitQuality = {
  score: number; // the joints' mean centring across their limbs, %
  joints: number;
  inside: number;
  centred: Record<string, number>; // 1 in the middle of the limb, 0 on the skin or outside
  bones_outside: Record<string, number>; // "Parent > Child" → share outside the body
  attention: [string, string][]; // [joint, why], worst first
};

export type JobInfo = { status: JobStatus; version?: string; error?: { code: string; message: string } };

/** Polls until the job ends (SSE drives the live view; this is for chaining steps). */
export async function waitJob(id: string): Promise<JobInfo> {
  for (;;) {
    const j = await api.job(id);
    if (j.status !== "queued" && j.status !== "running") return j;
    await new Promise((r) => setTimeout(r, 500));
  }
}

export const fileUrl = (asset: string, version: string, file: string) => `/files/${asset}/${version}/${file}`;
