/// <reference types="vite/client" />
import "./styles.css";
import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, fileUrl, waitJob, type Asset, type BlenderSetup, type PortInfo, type TrashEntry, type CopyResult, type Check, type Drive, type Env, type FitQuality, type Folder, type HfStatus, type JobStatus, type Licence, type Meta, type Model, type AgentSetup, type Preset, type Presets, type Storage, type StorageItem } from "./api";
import { AnimEditor } from "./anim/Editor";
import { Actions2D, PictureForm, SpritesForm, animatable, redrawable } from "./pixel/Panels";
import { EditorCanvas, EditorPanel, usePixelEditor } from "./pixel/Editor";
import { PixelView, is2D, isSheet } from "./pixel/View";
import { DrawForm } from "./pixel/Panels";
import { Compare } from "./Compare";
import { ANIM_TYPES, ImportInfo, ImportPage, OriginalFile } from "./Import";
import { Transport, useTransportKeys } from "./anim/Transport";
import { onEvent } from "./events";
import { leave, unsaved, useBusy, useHistory, useUnsaved } from "./hooks";
import { VIEW_MODES, Viewer, type Joint, type Moved, type SkeletonEdit, type Stats, type Vec3, type ViewMode } from "./viewer/Viewer";
import {
  actsAs,
  CHECKS,
  exportPick,
  EXPORTS,
  EXPORTS_2D,
  FIGHT_PROMPTS,
  GATES,
  MOVE_SETS,
  RECIPE,
  TASKS_2D,
  TASKS_3D,
  ancestors,
  bakeable,
  generated,
  hasGlb,
  hasImage,
  isImported,
  label,
  mainFile,
  source,
  spritesFrom,
  phaseOf,
  prepState,
  taskOf,
  type Task,
} from "./workflow";

type Job = {
  job_id: string;
  step?: string;
  asset?: string; // recorded at submission: events and job records don't carry it
  input?: string | null; // the version the job started from
  of?: string; // "2 of 5" inside a multi-step action
  status: JobStatus;
  p?: number;
  text?: string;
  log: string[];
  error?: { code: string; message: string; suggest?: object };
  version?: string;
};
type Gpu = { resident: string | null; free_gb: number | null };
type GpuLoad = {
  step: string | null;
  util: number | null;
  used_gb: number | null;
  total_gb: number | null;
};
// one selection record, so a version can never be paired with another asset's metadata
type Sel = {
  asset: string | null;
  versions: Meta[];
  version: string | null;
  loaded?: boolean;
  missing?: boolean;
  kind?: string; // the asset's kind ("2d"), with its versions: the library list may come later
};
type Chain = (asset: string, parent: string | null, steps: [string, object][]) => Promise<string | null>;
type Route =
  | { page: "library" | "new" | "import" | "setup" | "welcome"; id?: undefined; version?: undefined; cap?: string }
  | { page: "asset"; id: string; version?: string; cap?: undefined };

declare global {
  interface Window {
    __viewer?: Viewer;
  }
}

// #/library, #/new, #/import, #/setup, #/asset/<id>?version=<v>; old #models = Setup
function parse(hash: string): Route {
  const cap = hash.match(/^#\/setup\?cap=([\w-]+)$/); // a blocked action's link lands on its capability (UX-12)
  if (cap) return { page: "setup", cap: cap[1] };
  const m = hash.match(/^#\/asset\/([^?]+)(?:\?version=(.+))?$/);
  if (m)
    return {
      page: "asset",
      id: decodeURIComponent(m[1]),
      version: m[2] && decodeURIComponent(m[2]),
    };
  return {
    page:
      hash === "#/new" ? "new" : hash === "#/import" ? "import" : hash === "#/setup" || hash === "#models" ? "setup" : hash === "#/welcome" ? "welcome" : "library",
  };
}
/** The Setup page at one capability's card (UX-12): where a blocked action sends the user. */
const setupAt = (group: string) => `#/setup?cap=${group}`;
const assetHash = (id: string, version?: string | null) => `#/asset/${encodeURIComponent(id)}${version ? `?version=${encodeURIComponent(version)}` : ""}`;
const when = (iso: string) =>
  new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
const basename = (f: string) => f.split("/").pop() ?? f;

// backend error codes worth a sentence of their own; the rest keep the backend's message
function explain(e: any): string {
  const said: Record<string, string> = {
    oom: "This run ran out of GPU memory. Your previous version is safe; try smaller settings.",
    busy: "Another operation is using the GPU. Try again when it finishes.",
    gated: "This model needs access approval first: see Setup.",
    stopped: "Stopped. The last finished step is kept.",
  };
  return said[e?.code] ?? e?.message ?? String(e);
}

/** The triangle budget of a remesh: quads are stored as two triangles. */
const triBudget = (faces: number, topology?: unknown) => (topology === "quad" ? 2 * faces : faces);
/** The nearest remesh's budget in triangles, for the validate step. */
const budgetOf = (line: Meta[]) => {
  const p = line.find((a) => a.step === "remesh")?.params;
  return p?.faces ? triBudget(p.faces as number, p.topology) : undefined;
};

function App() {
  const [assets, setAssets] = useState<Asset[] | null>(null);
  const [sel, setSel] = useState<Sel>({
    asset: null,
    versions: [],
    version: null,
  });
  const [steps, setSteps] = useState<Presets>({});
  const [models, setModels] = useState<Model[] | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [gpu, setGpu] = useState<Gpu | null>(null);
  const [load, setLoad] = useState<GpuLoad | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [result, setResult] = useState<{
    asset: string;
    version: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [route, setRoute] = useState<Route>(parse(location.hash));
  const selRef = useRef(sel);
  selRef.current = sel;
  const jobsRef = useRef(jobs);
  jobsRef.current = jobs;
  const opening = useRef(0); // the latest openAsset() wins; earlier answers are dropped
  const nav = useRef(0); // bumped by every user navigation; an action opens its result only if unchanged
  const lastHash = useRef(location.hash); // the hash this app set itself (not a user navigation)
  const stopping = useRef(new Set<string>()); // cancelled jobs: their chain must not start a successor
  const down = useRef(false);
  const version = sel.versions.find((v) => v.version === sel.version) ?? null;
  const assetName = (id?: string) => assets?.find((a) => a.id === id)?.name ?? id ?? "?";

  // a branch of History is gone (the delete's own reply, or another client's through the event stream):
  // drop it from the list; a deleted current version falls back to the branch's parent
  const dropVersions = (asset: string, versions: string[]) =>
    setSel((s) => {
      if (s.asset !== asset) return s;
      const gone = new Set(versions);
      const root = s.versions.find((v) => v.version === versions[0]);
      const version = s.version && gone.has(s.version) ? (root?.parent ?? null) : s.version;
      return { ...s, versions: s.versions.filter((v) => !gone.has(v.version)), version };
    });
  const refresh = () =>
    api
      .assets()
      .then(setAssets)
      .catch((e) => setError(explain(e)));
  // nothing installed yet: the library points to the guided setup (M35c); on the very first start the app
  // opens it by itself (the server knows: nothing installed, not skipped), unless a link asked for a page
  const fresh = !!models?.length && models.every((m) => !m.installed && !m.partial);
  useEffect(() => {
    api.firstRun().then(
      (r) => r.first_run && parse(location.hash).page === "library" && location.hash !== "#/library" && navigate("#/welcome"),
      () => undefined,
    );
  }, []);
  const loadModels = () =>
    api
      .models()
      .then(setModels)
      .catch(() => {});
  // version: the one asked for if valid, else the one already selected on this asset, else the newest
  const openAsset = async (id: string, want?: string) => {
    const token = ++opening.current;
    if (selRef.current.asset !== id) setSel({ asset: id, versions: [], version: null });
    let d;
    try {
      d = await api.asset(id);
    } catch (e: any) {
      if (token !== opening.current) return;
      if (e.code === "not_found")
        setSel({
          asset: id,
          versions: [],
          version: null,
          loaded: true,
          missing: true,
        });
      else setError(explain(e));
      return;
    }
    if (token !== opening.current) return;
    setSel((s) => {
      const has = (v?: string | null) => (v && d.versions.some((m) => m.version === v) ? v : null);
      const version = has(want) ?? (s.asset === id ? has(s.version) : null) ?? d.versions.at(-1)?.version ?? null;
      return { asset: id, versions: d.versions, version, loaded: true, kind: d.asset.kind };
    });
  };
  // the hash is the source of truth for page and selection, so Back/Forward restore both
  const apply = (hash: string) => {
    if (hash === "#models") {
      hash = "#/setup";
      lastHash.current = hash;
      history.replaceState(null, "", hash);
    }
    const r = parse(hash);
    setRoute(r);
    if (r.page !== "asset") return;
    const s = selRef.current;
    if (s.asset === r.id && s.loaded && !s.missing && (!r.version || s.versions.some((v) => v.version === r.version)))
      setSel({ ...s, version: r.version ?? s.version });
    else openAsset(r.id, r.version);
  };
  const navigate = (hash: string) => {
    if (hash !== location.hash && !leave()) return; // an editor's unsaved work (UX-01)
    nav.current++;
    lastHash.current = hash;
    if (location.hash === hash) apply(hash);
    else location.hash = hash;
  };
  const pick = (v: string) => sel.asset && navigate(assetHash(sel.asset, v));
  const guard = (p: Promise<unknown>) => p.then(() => setError("")).catch((e) => setError(explain(e)));
  // a user action's result: open it while the user still follows that action, else offer "View result"
  // (with an editor's unsaved work open, the result is offered too: opening it asked out of nowhere, and keeping the
  // edits dropped the result)
  const show = (asset: string, version: string, since: number) =>
    nav.current === since && !unsaved() ? navigate(assetHash(asset, version)) : setResult({ asset, version });
  // a single call that makes a version (pull, accept)
  const act = (asset: string | undefined, p: Promise<{ version: string }>) => {
    const since = nav.current;
    return guard(p.then((r) => asset && show(asset, r.version, since)));
  };
  // missed events while the stream was down: re-read the open asset and the jobs we know are active
  const reconcile = () => {
    refresh();
    const id = selRef.current.asset;
    if (id)
      api.asset(id).then(
        (d) =>
          setSel((s) => {
            if (s.asset !== id) return s;
            // the shown version may be gone (deleted while the stream was down): its nearest surviving
            // ancestor, else the newest (audit 2026-10-10, UI-003: it stayed selected and the asset read as empty)
            const alive = new Set(d.versions.map((v) => v.version));
            let version = s.version;
            if (version && !alive.has(version)) {
              const old = new Map(s.versions.map((v) => [v.version, v]));
              let p = old.get(version)?.parent ?? null;
              while (p && !alive.has(p)) p = old.get(p)?.parent ?? null;
              version = p ?? d.versions.at(-1)?.version ?? null;
            }
            return { ...s, versions: d.versions, version };
          }),
        () => {},
      );
    for (const j of Object.values(jobsRef.current))
      if (j.status === "queued" || j.status === "running")
        api.job(j.job_id).then(
          (r) =>
            setJobs((all) => ({
              ...all,
              [j.job_id]: { ...all[j.job_id], ...r },
            })),
          (e) => {
            // a job the server no longer knows ended with the server (a restart): over, not running
            // (UI-004: it kept its Cancel and held the app "busy"); any other failure: still unknown
            const lost = e?.code === "not_found" || e?.code === "404";
            const message = `${STEP_ACTS[j.step ?? ""] ?? j.step ?? "a step"}: the app was restarted while it ran; finished versions are in the library`;
            setJobs((all) => ({
              ...all,
              [j.job_id]: {
                ...all[j.job_id],
                ...(lost ? { status: "failed" as const, error: { code: "not_found", message } } : { text: "this session ended; finished versions are in the library" }),
              },
            }));
            if (lost) setError(message);
          },
        );
  };

  useEffect(() => {
    const onHash = () => {
      if (location.hash !== lastHash.current) {
        // Back/Forward or a link: the user moved; with unsaved editor work, the address goes back (UX-01)
        if (!leave()) return history.replaceState(null, "", lastHash.current);
        nav.current++;
      }
      lastHash.current = location.hash;
      apply(location.hash);
    };
    window.addEventListener("hashchange", onHash);
    apply(location.hash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  // keep the address on the version actually shown (opening an asset picks one), without a new history entry
  useEffect(() => {
    if (route.page !== "asset" || sel.asset !== route.id || !sel.version || route.version === sel.version) return;
    const hash = assetHash(sel.asset, sel.version);
    lastHash.current = hash;
    history.replaceState(null, "", hash);
    setRoute({ ...route, version: sel.version });
  }, [sel.asset, sel.version, route]);
  useEffect(() => {
    refresh();
    loadModels();
    api
      .steps()
      .then(setSteps)
      .catch(() => {});
    return onEvent((name, d) => {
      if (name === "connection") {
        setOnline(d.ok);
        if (d.ok && down.current) reconcile();
        if (!d.ok) {
          setGpu(null); // a reading from before the drop says nothing about now
          setLoad(null);
        }
        down.current = !d.ok;
        return;
      }
      if (name === "gpu") setGpu(d);
      if (name === "gpu_load") setLoad(d);
      if (name === "asset_deleted") {
        refresh();
        if (selRef.current.asset === d.asset_id) navigate("#/");
      }
      if (name === "versions_deleted") dropVersions(d.asset_id, d.versions);
      if (name === "version_created")
        setSel((s) =>
          s.asset !== d.asset_id || s.versions.some((v) => v.version === d.meta.version)
            ? s
            : {
                ...s,
                versions: [...s.versions, d.meta].sort((a, b) => a.version.localeCompare(b.version)),
              },
        );
      if (name === "job" && !["queued", "running"].includes(d.status)) loadModels(); // a download may have ended
      if (!d.job_id) return;
      setJobs((all) => {
        const j: Job = all[d.job_id] ?? {
          job_id: d.job_id,
          status: "queued",
          log: [],
        };
        if (name === "progress")
          return {
            ...all,
            [d.job_id]: {
              ...j,
              step: d.step,
              asset: d.asset_id,
              p: d.p,
              text: d.text,
            },
          };
        if (name === "log")
          return {
            ...all,
            [d.job_id]: { ...j, log: [...j.log.slice(-200), d.line] },
          };
        if (name === "job")
          return {
            ...all,
            [d.job_id]: {
              ...j,
              status: d.status,
              error: d.error,
              version: d.version,
            },
          };
        return all;
      });
    });
  }, []);

  // trellis2's generate presets, in server order (faithful first = the default); retexture runs from "Run a step"
  const presets = Object.fromEntries(Object.entries(steps.trellis2 ?? {}).filter(([, p]) => p.op === "generate"));
  // a model group whose files the listing says are incomplete (false while unknown)
  const missing = (group: string) => {
    const rows = (models ?? []).filter((m) => m.groups.includes(group));
    return rows.length > 0 && !rows.every((m) => m.installed);
  };
  const track = (job_id: string, step: string, asset?: string, input?: string | null, of?: string) =>
    setJobs((all) => ({
      ...all,
      [job_id]: {
        ...(all[job_id] ?? { job_id, status: "queued", log: [] }),
        step,
        asset,
        input,
        of,
      },
    }));
  const cancel = (id: string) => {
    stopping.current.add(id); // first, so a completion racing the cancel can't start the next step
    guard(api.cancel(id));
  };
  // run steps one after another, each on the previous one's version
  const chain: Chain = async (asset, parent, steps) => {
    const since = nav.current;
    const start = parent;
    try {
      for (const [i, [step, params]] of steps.entries()) {
        const { job_id } = await api.run(asset, step, parent, params);
        track(job_id, step, asset, parent, steps.length > 1 ? `${i + 1} of ${steps.length}` : undefined);
        const j = await waitJob(job_id);
        // the poll is authoritative too: the activity bar must not stay "queued" if SSE missed the end
        setJobs((all) => ({
          ...all,
          [job_id]: {
            ...all[job_id],
            status: j.status,
            version: j.version,
            error: j.error ?? all[job_id]?.error,
          },
        }));
        if (j.status === "done" && j.version) parent = j.version;
        if (stopping.current.has(job_id)) throw Object.assign(new Error("stopped"), { code: "stopped" });
        if (j.status !== "done") throw Object.assign(new Error(`${step} ${j.status}${j.error ? `: ${j.error.message}` : ""}`), { code: j.error?.code });
      }
      setError("");
      if (parent) show(asset, parent, since);
      return parent;
    } catch (e) {
      setError(explain(e));
      if (parent && parent !== start) show(asset, parent, since); // the last step that did work
      return null;
    }
  };

  return (
    <>
      <header className="top">
        <button className="skip" onClick={() => document.getElementById("main")?.focus()}>
          Skip to the content
        </button>
        <span className="brand">
          <img src="/effigy.svg" alt="" />
          Effigy
        </span>
        <nav aria-label="Main">
          <a href="#/library" aria-current={route.page === "library" || route.page === "asset" ? "page" : undefined}>
            Library
          </a>
          <a href="#/new" aria-current={route.page === "new" ? "page" : undefined}>
            New asset
          </a>
          <a href="#/import" aria-current={route.page === "import" ? "page" : undefined}>
            Import
          </a>
          <a href="#/setup" aria-current={route.page === "setup" || route.page === "welcome" ? "page" : undefined}>
            Setup
          </a>
        </nav>
        <div className="right">
          <LowSpace />
          {result && (
            <span role="status">
              Result ready
              {result.asset !== sel.asset && ` in ${assetName(result.asset)}`}
              <button
                className="small"
                onClick={() => {
                  setResult(null);
                  navigate(assetHash(result.asset, result.version));
                }}
              >
                View result
              </button>
            </span>
          )}
          <span id="gpu" className={load?.step ? "busy" : ""}>
            GPU:{" "}
            {online === false
              ? "unknown · connection interrupted, the server may still be working"
              : load?.step
                ? `running ${load.step}`
                : load || gpu
                  ? "idle"
                  : "no reading yet"}
            {gpu?.resident && ` · ${gpu.resident} loaded`}
            {load?.step && load.util != null && (
              <>
                {" · "}
                <meter min={0} max={100} value={load.util} /> {load.util}%
              </>
            )}
            {load?.used_gb != null ? ` · ${load.used_gb} / ${load.total_gb} GB` : gpu?.free_gb != null && ` · ${gpu.free_gb} GB free`}
          </span>
        </div>
      </header>
      {error ? (
        <div className="alert" role="alert">
          {error}
          <button onClick={() => setError("")}>Dismiss</button>
        </div>
      ) : (
        <div />
      )}
      <main className="main" id="main" tabIndex={-1}>
        {/* always mounted so a draft survives a trip to Setup */}
        <NewAsset
          hidden={route.page !== "new"}
          chain={chain}
          missing={missing}
          onCreated={(id, version) => {
            refresh();
            navigate(assetHash(id, version));
          }}
          onError={(e) => setError(explain(e))}
        />
        <ImportPage
          hidden={route.page !== "import"}
          chain={chain}
          assets={assets}
          onCreated={(id, version) => {
            refresh();
            navigate(assetHash(id, version));
          }}
          onError={(e) => setError(explain(e))}
        />
        {route.page === "library" && (
          <Library
            assets={assets}
            fresh={fresh}
            onRename={(a) => {
              const name = window.prompt("New name", a.name);
              if (name && name.trim() && name !== a.name) guard(api.renameAsset(a.id, name).then(refresh));
            }}
            onArchive={(a, on) => guard(api.archiveAsset(a.id, on).then(refresh))}
            onTags={(a) => {
              const t = window.prompt("Tags, separated by commas", (a.tags ?? []).join(", "));
              if (t !== null) guard(api.patchTags(a.id, t.split(",").map((x) => x.trim()).filter(Boolean)).then(refresh));
            }}
            onDelete={(a) => {
              if (window.confirm(`Delete "${a.name}" and all its versions?\n\nIt moves to Recently deleted, at the end of the library, where you can restore it.`))
                guard(api.deleteAsset(a.id));
            }}
            onRestored={refresh}
          />
        )}
        {route.page === "setup" && <Setup models={models} reload={loadModels} jobs={jobs} track={track} guard={guard} cap={route.cap} />}
        {route.page === "welcome" && <Welcome models={models} reload={loadModels} jobs={jobs} track={track} guard={guard} />}
        {route.page === "asset" && (
          <Workspace
            asset={assets?.find((a) => a.id === sel.asset)}
            sel={sel}
            version={version}
            jobs={jobs}
            steps={steps}
            presets={presets}
            missing={missing}
            chain={chain}
            pick={pick}
            guard={guard}
            onRenamed={refresh}
            onVersion={(id, version, body) =>
              guard(
                api
                  .patchVersion(id, version, body)
                  .then(() => setSel((s) => (s.asset === id ? { ...s, versions: s.versions.map((m) => (m.version === version ? { ...m, ...body } : m)) } : s))),
              )
            }
            onDeleted={dropVersions}
            assetName={assetName}
            track={track}
            onPull={(id) => act(id, api.blenderPull(id))}
            onCancel={cancel}
          />
        )}
      </main>
      <Jobs jobs={jobs} assetName={assetName} onCancel={cancel} />
    </>
  );
}

const FOLDED = new Set(["clean", "remesh", "uv"]); // History folds these: the steps between a model and its bake

// Library filters: bench and golden runs are the test assets; everything else is the owner's
const isTest = (a: Asset) => a.id.startsWith("bench_") || a.id.startsWith("golden_");
// the library's view (search, sort, filter, tag, scroll): in the address while the library shows, so Back and a link bring it
// back, and remembered here for the Library button (UX-11: it reset whenever the user went elsewhere)
type LibView = { q: string; sort: string; filter: string; tag: string; top: number };
let libView: LibView = { q: "", sort: "new", filter: "All", tag: "", top: 0 };
const libFromHash = (hash: string): Partial<LibView> => {
  const i = hash.indexOf("?");
  if (i < 0 || !/^#\/(library)?\?/.test(hash)) return {};
  const p = new URLSearchParams(hash.slice(i + 1));
  return Object.fromEntries((["q", "sort", "filter", "tag"] as const).filter((k) => p.has(k)).map((k) => [k, p.get(k)!]));
};
const libHash = (v: LibView, base = "#/") => {
  const p = new URLSearchParams();
  if (v.q) p.set("q", v.q);
  if (v.sort !== "new") p.set("sort", v.sort);
  if (v.filter !== "All") p.set("filter", v.filter);
  if (v.tag) p.set("tag", v.tag);
  const s = p.toString();
  return s ? `${base}?${s}` : base;
};

const FILTERS: Record<string, (a: Asset) => boolean> = {
  All: () => true,
  Mine: (a) => !isTest(a),
  Bench: (a) => a.id.startsWith("bench_"),
  Golden: (a) => a.id.startsWith("golden_"),
};

function Library(props: {
  assets: Asset[] | null;
  fresh?: boolean;
  onRename: (a: Asset) => void;
  onDelete: (a: Asset) => void;
  onArchive: (a: Asset, on: boolean) => void;
  onTags: (a: Asset) => void;
  onRestored: () => void;
}) {
  const [start] = useState(() => ({ ...libView, ...libFromHash(location.hash) }));
  const [q, setQ] = useState(start.q);
  const [tagSel, setTag] = useState(start.tag);
  const [picking, setPicking] = useState(false); // M42: selection mode for the batch export
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [batch, setBatch] = useState(false);
  const [credits, setCredits] = useState<Record<string, string>>({}); // licenses.yaml row name -> its attribution line
  useEffect(() => {
    api.licenses().then((ls) => setCredits(Object.fromEntries(ls.filter((l) => l.attribution).map((l) => [l.name, l.attribution!]))), () => {});
  }, []);
  const [sort, setSort] = useState(start.sort);
  const [filter, setFilter] = useState(FILTERS[start.filter] || start.filter === "Archived" ? start.filter : "All");
  const page = useRef<HTMLDivElement>(null);
  useEffect(() => {
    libView = { ...libView, q, sort, filter, tag: tagSel };
    if (!/^(#\/(library)?(\?.*)?)?$/.test(location.hash)) return; // the library's own address only
    const hash = libHash(libView, location.hash.startsWith("#/library") ? "#/library" : "#/");
    if (location.hash !== hash) history.replaceState(null, "", hash); // no new history entry
  }, [q, sort, filter, tagSel]);
  useEffect(() => {
    // back where the user was in a long library, once the cards are there
    if (props.assets && page.current) page.current.scrollTop = libView.top;
  }, [!!props.assets]);
  const clear = () => (setQ(""), setFilter("All"), setTag(""));
  const filtered = !!q.trim() || filter !== "All" || !!tagSel;
  const total = props.assets ?? [];
  const stamp = (a: Asset) => a.updated ?? a.created;
  const needle = q.trim().toLowerCase();
  const tags = [...new Set(total.flatMap((a) => a.tags ?? []))].sort();
  const tag = tags.includes(tagSel) ? tagSel : ""; // a tag no asset has any more is no filter
  const all = total
    .filter((a) => (filter === "Archived" ? !!a.archived : !a.archived && FILTERS[filter](a)))
    .filter((a) => !tag || a.tags?.includes(tag))
    .filter((a) => !needle || a.id.toLowerCase().includes(needle) || a.name.toLowerCase().includes(needle) || a.tags?.some((t) => t.toLowerCase().includes(needle)))
    .sort(
      sort === "name"
        ? (a, b) => a.name.localeCompare(b.name)
        : sort === "old"
          ? (a, b) => stamp(a).localeCompare(stamp(b))
          : (a, b) => stamp(b).localeCompare(stamp(a)),
    );
  const chosen = all.filter((a) => picked.has(a.id)); // only what is listed right now
  const dup = (a: Asset) => total.some((b) => b.name === a.name && b.id !== a.id);
  return (
    <div className="page" ref={page} onScroll={(e) => (libView.top = e.currentTarget.scrollTop)}>
      <div className="head">
        <h1>Library</h1>
        {total.length > 0 && (
          <a className="button primary" href="#/new">
            New asset
          </a>
        )}
      </div>
      {total.length > 0 && (
        <div className="row library-tools" role="search">
          <input type="search" aria-label="Search assets" placeholder="Search by name, id or tag" value={q} onChange={(e) => setQ(e.target.value)} />
          <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="new">Recently changed</option>
            <option value="old">Changed longest ago</option>
            <option value="name">Name A–Z</option>
          </select>
          <select aria-label="Show" value={filter} onChange={(e) => setFilter(e.target.value)}>
            {[...Object.keys(FILTERS), "Archived"].map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
          {tags.length > 0 && (
            <select aria-label="Tag" value={tag} onChange={(e) => setTag(e.target.value)}>
              <option value="">Tag: all</option>
              {tags.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          )}
          <button aria-pressed={picking} onClick={() => (setPicking(!picking), setPicked(new Set()), setBatch(false))}>
            {picking ? "Done selecting" : "Select assets"}
          </button>
          {picking && (
            <button className="primary" disabled={!chosen.length} onClick={() => setBatch(true)}>
              Export selected ({chosen.length})
            </button>
          )}
          {filtered && (
            <button className="small" onClick={clear}>
              Clear filters
            </button>
          )}
          <span className="muted">
            {all.length} of {total.length} assets
          </span>
        </div>
      )}
      {batch && <BatchExport assets={chosen} onClose={() => setBatch(false)} />}
      {props.fresh && (
        <div className="note get-started">
          <span>
            <strong>Welcome.</strong> Nothing is installed yet: the guided setup checks your computer, asks where the models go and what you want to make, and
            installs it.
          </span>
          <a className="button primary" href="#/welcome">
            Get started
          </a>
        </div>
      )}
      {!props.assets ? (
        <p className="muted">Loading…</p>
      ) : total.length === 0 ? (
        <div className="empty">
          <h2>Your next asset starts here.</h2>
          <div className="row">
            <a className="button primary" href="#/new">
              New asset
            </a>
            <a className="button" href="#/import">
              Import a model
            </a>
          </div>
        </div>
      ) : all.length === 0 ? (
        <p className="muted">
          No assets match.{" "}
          <button className="small" onClick={clear}>
            Clear filters
          </button>
        </p>
      ) : (
        <ul className="cards">
          {all.map((a) => (
            <li key={a.id}>
              <Card
                a={a}
                dup={dup(a)}
                credit={(a.licenses ?? []).map((n) => credits[n]).filter(Boolean).join("\n")}
                picking={picking}
                picked={picked.has(a.id)}
                onPick={() => setPicked((p) => (p.delete(a.id) || p.add(a.id), new Set(p)))}
              />
              <details className="card-more">
                <summary aria-label={`More for ${a.name}`}>More</summary>
                <div className="card-actions">
                  <button onClick={() => props.onRename(a)}>Rename</button>
                  <button onClick={() => props.onTags(a)}>Tags</button>
                  <button onClick={() => props.onArchive(a, !a.archived)}>{a.archived ? "Unarchive" : "Archive"}</button>
                  <button className="danger" onClick={() => props.onDelete(a)}>
                    Delete
                  </button>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
      {props.assets && <Trash onRestored={props.onRestored} count={props.assets.length} />}
    </div>
  );
}

// what deleting moved aside, and the way back (UX-14: a misclick was undone only by moving folders by hand)
function Trash(props: { onRestored: () => void; count: number }) {
  const [rows, setRows] = useState<TrashEntry[] | null>(null);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, run] = useBusy();
  const load = () => api.trash().then(setRows, () => setRows([]));
  const open = useRef(false);
  useEffect(() => {
    if (open.current) load(); // an asset deleted (or restored) while the list is open shows at once
  }, [props.count]);
  const what = (r: TrashEntry) =>
    r.kind === "asset"
      ? `${r.asset_name}, ${r.versions} version${r.versions === 1 ? "" : "s"}`
      : `${r.asset_name}: ${label(r.first as Meta)} ${r.versions[0].slice(0, 3)}${r.versions.length > 1 ? ` and ${r.versions.length - 1} made from it` : ""}`;
  return (
    <details className="trash" onToggle={(e) => (open.current = (e.currentTarget as HTMLDetailsElement).open) && load()}>
      <summary>Recently deleted</summary>
      {!rows ? (
        <p className="muted">Loading…</p>
      ) : !rows.length ? (
        <p className="muted">Nothing deleted. Setup, Space on disk, empties this for good.</p>
      ) : (
        <ul aria-label="Recently deleted">
          {rows.map((r) => (
            <li key={r.name} className="row">
              <span>
                {what(r)} <span className="muted">· deleted {when(r.deleted)}</span>
              </span>
              <button
                className="small"
                disabled={busy}
                onClick={() =>
                  run(() =>
                    api.restoreTrash(r.name).then(
                      () => (setSaid({ ok: true, text: `${what(r)}: restored.` }), load(), props.onRestored()),
                      (e) => setSaid({ ok: false, text: explain(e) }),
                    ),
                  )
                }
              >
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}
      {said && (
        <p className={said.ok ? "note" : "error"} role="status">
          {said.text}
        </p>
      )}
    </details>
  );
}

const prefersStill = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** One library card: the still thumbnail, swapped for the turntable after 400 ms of hover or focus once its WebP has loaded (M42). */
function Card(props: { a: Asset; dup: boolean; credit: string; picking: boolean; picked: boolean; onPick: () => void }) {
  const { a } = props;
  const [turn, setTurn] = useState(false);
  const timer = useRef(0);
  const hover = useRef(false);
  const pending = useRef<HTMLImageElement | null>(null);
  const stamp = a.updated ?? a.created;
  const url = `/api/assets/${a.id}/turntable?v=${encodeURIComponent(a.updated ?? "")}`;
  const enter = () => {
    if (prefersStill()) return;
    hover.current = true;
    clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const im = (pending.current = new Image());
      im.onload = () => hover.current && setTurn(true);
      im.src = url;
    }, 400);
  };
  const leave = () => {
    hover.current = false;
    clearTimeout(timer.current);
    if (pending.current) (pending.current.onload = null), (pending.current.src = "");
    setTurn(false);
  };
  useEffect(() => () => clearTimeout(timer.current), []);
  return (
    <>
      {props.picking && <input className="pick" type="checkbox" aria-label={`Select ${a.name}`} checked={props.picked} onChange={props.onPick} />}
      <a
        className="card"
        href={assetHash(a.id)}
        onMouseEnter={enter}
        onMouseLeave={leave}
        onFocus={enter}
        onBlur={leave}
        onClick={(e) => props.picking && (e.preventDefault(), props.onPick())}
      >
        <img
          className="thumb"
          src={turn ? url : `/api/assets/${a.id}/thumb`}
          alt=""
          loading="lazy"
          onError={(e) => (e.currentTarget.style.visibility = "hidden")}
        />
        <strong>{a.name}</strong>
        {/* same-named assets: the id tells them apart */}
        <span className="muted">
          {a.versions != null && `${a.versions} version${a.versions === 1 ? "" : "s"} · `}
          {when(stamp)}
          {props.dup && ` · ${a.id}`}
        </span>
        <span className="chips">
          {a.restricted && <span className="chip bad">Restricted</span>}
          {a.imported && (
            <span className="chip" title="Imported: Effigy does not know this file's licence">
              Your file
            </span>
          )}
          {props.credit && (
            <span className="chip warn" title={props.credit}>
              Credit
            </span>
          )}
        </span>
        <span className="tags muted">{(a.tags ?? []).map((t) => `#${t}`).join(" ")}</span>
      </a>
    </>
  );
}

const SKIPS: Record<string, string> = { exists: "a folder for it is already there", restricted: "restricted, cannot be exported" };

// UX-10: one export's files into a new folder `<asset>_<profile>` inside the chosen one (the batch export's copy); the
// folder is remembered on this computer for next time
function SaveExport(props: { asset: string; version: Meta; primary?: boolean }) {
  const [folder, setFolder] = useState(() => {
    try {
      return localStorage.getItem("effigy.exportFolder") ?? "";
    } catch {
      return "";
    }
  });
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, run] = useBusy();
  const save = () =>
    run(async () => {
      try {
        const r = await api.exportCopy(folder.trim(), [{ asset_id: props.asset, version: props.version.version }]);
        try {
          localStorage.setItem("effigy.exportFolder", folder.trim());
        } catch {
          // a private window: the folder is not remembered, the copy is done
        }
        const c = r.copied[0];
        const k = r.skipped[0];
        setSaid(c ? { ok: true, text: `Saved ${c.files.length} files into ${c.dir}` } : { ok: false, text: `Not saved: ${SKIPS[k?.reason] ?? k?.reason}` });
      } catch (e) {
        setSaid({ ok: false, text: explain(e) });
      }
    });
  return (
    <div className="stack">
      <label>
        Folder
        <input value={folder} placeholder="e.g. D:\\Games\\MyGame\\Assets" onChange={(e) => (setFolder(e.target.value), setSaid(null))} />
      </label>
      <button className={props.primary ? "primary" : ""} disabled={busy || !folder.trim()} onClick={save}>
        Save all files to the folder
      </button>
      <p className="muted">They go into a new folder named after the asset and the destination, inside this one.</p>
      {said && (
        <p className={said.ok ? "note" : "error"} role="status">
          {said.text}
        </p>
      )}
    </div>
  );
}

/** M42: export the chosen assets one after another (reusing an identical export), then copy them into a folder. */
function BatchExport(props: { assets: Asset[]; onClose: () => void }) {
  const [profile, setProfile] = useState("godot");
  const [folder, setFolder] = useState("");
  const [busy, run] = useBusy();
  // which version of each asset the export takes, shown before it runs (UX-10: a starred one picked silently surprised)
  const [plan, setPlan] = useState<{ name: string; ok: boolean; text: string }[] | null>(null);
  useEffect(() => {
    let live = true;
    Promise.all(
      props.assets.map(async (a) => {
        try {
          const pick = exportPick((await api.asset(a.id)).versions);
          if (!pick) return { name: a.name, ok: false, text: "skipped, nothing prepared to export yet" };
          if (pick.restricted) return { name: a.name, ok: false, text: `skipped, ${SKIPS.restricted}` };
          return { name: a.name, ok: true, text: `${label(pick)} ${pick.version.slice(0, 3)}${pick.starred ? " (starred)" : ""}` };
        } catch (e) {
          return { name: a.name, ok: false, text: explain(e) };
        }
      }),
    ).then((p) => live && setPlan(p));
    return () => {
      live = false;
    };
  }, [props.assets.map((a) => a.id).join(" ")]);
  const [now, setNow] = useState("");
  const [out, setOut] = useState<{ ok: boolean; text: string }[] | null>(null);
  const start = () =>
    run(async () => {
      const lines: { ok: boolean; text: string }[] = [];
      try {
        await api.exportCopy(folder.trim(), []); // the folder first: no exports for a folder that cannot take them
      } catch (e) {
        setOut([{ ok: false, text: `Folder not usable: ${explain(e)}` }]);
        return;
      }
      const items: { asset_id: string; version: string }[] = [];
      const nameOf = (id: string) => props.assets.find((a) => a.id === id)?.name ?? id;
      for (const [i, a] of props.assets.entries()) {
        setNow(`Exporting ${a.name} (${i + 1} of ${props.assets.length})`);
        try {
          const { versions } = await api.asset(a.id);
          const pick = exportPick(versions);
          if (!pick) {
            lines.push({ ok: false, text: `${a.name}: skipped, nothing prepared to export yet` });
            continue;
          }
          if (pick.restricted) {
            lines.push({ ok: false, text: `${a.name}: skipped, ${SKIPS.restricted}` });
            continue;
          }
          const same = (x: (typeof versions)[number]) => x.step === "export" && x.parent === pick.version && JSON.stringify(x.params) === JSON.stringify({ profile });
          let ex = versions.filter(same).at(-1)?.version;
          if (!ex) {
            const { job_id } = await api.run(a.id, "export", pick.version, { profile });
            const j = await waitJob(job_id);
            if (j.status !== "done" || !j.version) throw new Error(j.error?.message ?? `the export ${j.status}`);
            ex = j.version;
          }
          items.push({ asset_id: a.id, version: ex });
        } catch (e) {
          lines.push({ ok: false, text: `${a.name}: skipped, ${explain(e)}` });
        }
      }
      if (items.length) {
        setNow("Copying into the folder");
        try {
          const r: CopyResult = await api.exportCopy(folder.trim(), items);
          for (const c of r.copied) lines.push({ ok: true, text: `${nameOf(c.asset_id)}: copied ${c.files.length} file${c.files.length === 1 ? "" : "s"} to ${c.dir}` });
          for (const k of r.skipped) lines.push({ ok: false, text: `${nameOf(k.asset_id)}: skipped, ${SKIPS[k.reason] ?? k.reason}` });
        } catch (e) {
          lines.push({ ok: false, text: `Copy failed: ${explain(e)}` });
        }
      }
      setNow("");
      setOut(lines);
    });
  return (
    <section className="batch" role="dialog" aria-label="Export selected">
      <div className="head">
        <h3>
          Export {props.assets.length} asset{props.assets.length === 1 ? "" : "s"}
        </h3>
        <button className="small" disabled={busy} onClick={props.onClose}>
          Close
        </button>
      </div>
      <p className="muted">
        Each asset exports its newest starred version that is at least prepared, else its newest one; an identical export that exists is reused. Restricted
        assets are skipped. The settings are a single export's: LODs where the destination has them, collision for a model that is not a character.
      </p>
      {!plan ? (
        <p className="muted">Looking up what each asset exports…</p>
      ) : (
        <ul aria-label="What will be exported" className="plan">
          {plan.map((p) => (
            <li key={p.name} className={p.ok ? "" : "muted"}>
              {p.name}: {p.text}
            </li>
          ))}
        </ul>
      )}
      <div className="dests">
        {EXPORTS.map((e) => (
          <label key={e.id} className="choice">
            <input type="radio" name="batch-profile" checked={profile === e.id} onChange={() => setProfile(e.id)} />
            <span>{e.name}</span>
          </label>
        ))}
      </div>
      <label>
        Folder
        <input type="text" aria-label="Folder" placeholder="C:\Games\MyGame\assets" value={folder} onChange={(e) => setFolder(e.target.value)} />
      </label>
      <div className="row">
        <button className="primary" disabled={busy || !folder.trim()} onClick={start}>
          Export and copy
        </button>
        {now && (
          <span className="muted" role="status">
            {now}
          </span>
        )}
      </div>
      {out && (
        <ul className="results" aria-label="Result">
          {out.map((l, i) => (
            <li key={i} className={l.ok ? "ok" : "skip"}>
              {l.ok ? "✓" : "–"} {l.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// brief.py's templates: what each category adds to the description
const CATEGORIES: [string, string, string][] = [
  ["prop", "Object", "centred, three-quarter view, plain grey background"],
  ["character", "Character", "full body in an A-pose, front view, plain background"],
  ["creature", "Creature", "full body, side three-quarter view, legs apart"],
];

function Category(props: { value: string; onChange: (c: string) => void; name: string }) {
  return (
    <fieldset>
      <legend>Kind (adds framing for a clean 3D reference picture)</legend>
      <div className="choices">
        {CATEGORIES.map(([id, title, adds]) => (
          <label key={id} className="choice">
            <input type="radio" name={props.name} checked={props.value === id} onChange={() => props.onChange(id)} />
            <span>{title}</span>
            <span className="muted">{adds}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

type Mode = "describe" | "image" | "draw"; // a model file: the Import page (M39); draw: a blank sheet (M57)

// M17 turnaround sheets: the views left to right ("right" = the character's right side faces you)
const SHEETS: [string, string[]][] = [
  ["Front · Right · Back · Left", ["front", "right", "back", "left"]],
  ["Front · Left · Back · Right", ["front", "left", "back", "right"]],
  ["Front · Right · Back", ["front", "right", "back"]],
  ["Front · Left · Back", ["front", "left", "back"]],
  ["Front · Back", ["front", "back"]],
];

function NewAsset(props: {
  hidden: boolean;
  chain: Chain;
  missing: (group: string) => boolean;
  onCreated: (id: string, version?: string) => void;
  onError: (e: unknown) => void;
}) {
  const [mode, setMode] = useState<Mode>("describe");
  const [dim, setDim] = useState<"3d" | "2d">("3d"); // the 2D track (research 15, M50): a picture stays a picture
  const [kind2, setKind2] = useState("picture"); // the owner, 2026-10-13: a picture as described, the first kind
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [category, setCategory] = useState("prop");
  const [file, setFile] = useState<File | null>(null);
  const [sheet, setSheet] = useState(-1); // -1: one view; else the SHEETS row
  const [info, setInfo] = useState<{
    url?: string;
    text: string;
    ok: boolean;
  } | null>(null);
  const [over, setOver] = useState(false);
  const [fileKey, setFileKey] = useState(0); // remounts the file input to clear it
  const [busy, run] = useBusy();
  const made = useRef<string | null>(null); // the asset a failed attempt created: retry in it, not in a new one
  const choose = async (f: File | null) => {
    if (info?.url) URL.revokeObjectURL(info.url);
    setFile(null);
    setInfo(null);
    if (!f) return;
    if (!/\.(png|jpe?g|webp)$/i.test(f.name))
      return setInfo({
        text: `${f.name}: use a PNG, JPEG or WebP picture.`,
        ok: false,
      });
    try {
      const b = await createImageBitmap(f); // proves it decodes
      setInfo({
        url: URL.createObjectURL(f),
        text: `${f.name} · ${b.width} × ${b.height} px`,
        ok: true,
      });
      b.close();
      setFile(f);
    } catch {
      setInfo({ text: `${f.name} can't be read as a picture.`, ok: false });
    }
  };
  const clearFile = () => {
    choose(null);
    setFileKey((k) => k + 1);
  };
  const submit = () =>
    run(async () => {
      try {
        let id = made.current;
        if (!id)
          made.current = id = (await api.createAsset(name || file?.name.replace(/\.\w+$/, "") || prompt.slice(0, 36) || "asset", dim === "2d" ? "2d" : undefined)).id;
        let ok = true;
        if (dim === "2d") {
          const up = await api.upload(id, file!); // a 2D picture is kept as it is: no cut-out, no 3D framing
          props.onCreated(id, up.version);
        } else if (mode === "describe") {
          props.onCreated(id);
          ok = !!(await props.chain(id, null, [
            ["brief", { prompt, category }],
            ["concepts", {}],
          ]));
        } else {
          const up = await api.upload(id, file!);
          props.onCreated(id, up.version);
          ok = !!(await props.chain(id, up.version, [["prep", sheet < 0 ? {} : { op: "views", views: SHEETS[sheet][1] }]])); // its background removed
        }
        if (!ok) return; // keep the form as it is for a retry
        made.current = null;
        setName("");
        setPrompt("");
        clearFile();
      } catch (e) {
        props.onError(e);
      }
    });
  // M57: a blank sheet, opened in the pixel editor (the workspace does that for a blank sheet)
  const blank = (params: Record<string, unknown>) =>
    run(async () => {
      try {
        let id = made.current;
        if (!id) made.current = id = (await api.createAsset(name || "drawing", "2d")).id;
        props.onCreated(id);
        if (!(await props.chain(id, null, [["pixelize", params]]))) return; // the form stays for a retry
        made.current = null;
        setName("");
      } catch (e) {
        props.onError(e);
      }
    });
  const pictures = (kind: string, params: Record<string, unknown>) =>
    run(async () => {
      try {
        let id = made.current;
        if (!id) made.current = id = (await api.createAsset(name || prompt.slice(0, 36) || "picture", "2d")).id;
        props.onCreated(id);
        const ok = await props.chain(id, null, [
          ["brief", { prompt, category: kind }],
          ["concepts", params],
        ]);
        if (!ok) return; // the form stays for a retry
        made.current = null;
        setName("");
        setPrompt("");
      } catch (e) {
        props.onError(e);
      }
    });
  return (
    <div className="page" hidden={props.hidden}>
      <div className="head">
        <h1>New asset</h1>
      </div>
      <div className="form">
        <h2>What would you like to make?</h2>
        <div className="modes" role="group" aria-label="Make">
          {(
            [
              ["3d", "A 3D model"],
              ["2d", "A 2D picture or sprite"],
            ] as ["3d" | "2d", string][]
          ).map(([d, title]) => (
            <button
              key={d}
              aria-pressed={dim === d}
              onClick={() => {
                setDim(d);
                if (d === "3d" && mode === "draw") setMode("describe");
                clearFile();
                made.current = null;
              }}
            >
              {title}
            </button>
          ))}
        </div>
        <div className="modes" role="group" aria-label="Start from">
          {(
            [
              ["describe", "From a description"],
              ["image", "From an image"],
              ...(dim === "2d" ? [["draw", "Draw it yourself"]] : []),
            ] as [Mode, string][]
          ).map(([m, title]) => (
            <button
              key={m}
              aria-pressed={mode === m}
              onClick={() => {
                setMode(m);
                clearFile();
                made.current = null;
              }}
            >
              {title}
            </button>
          ))}
        </div>
        <label>
          Name (optional)
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        {mode === "draw" ? (
          <DrawForm busy={busy} onRun={blank} />
        ) : mode === "describe" ? (
          <>
            <label>
              Description
              <textarea
                rows={3}
                placeholder={dim === "2d" ? "A rain-soaked neon street at night, 1980s arcade mood" : "A worn brass lantern for a stylized adventure game"}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </label>
            {dim === "2d" ? (
              <PictureForm kind={kind2} onKind={setKind2} missing={props.missing} ready={!!prompt.trim()} busy={busy} onRun={pictures} />
            ) : (
              <Category name="new-category" value={category} onChange={setCategory} />
            )}
            {dim === "2d" ? null : props.missing("concepts") ? (
              <>
                <p className="note warn">Concept-image files weren't found. Your description stays here while you set them up.</p>
                <a className="button primary" href={setupAt("concepts")}>
                  Set up concept images
                </a>
              </>
            ) : (
              <button className="primary" disabled={busy || !prompt.trim()} onClick={submit}>
                {busy ? "Starting…" : "Generate 4 concepts"}
              </button>
            )}
          </>
        ) : (
          <>
            <div
              className={`drop ${over ? "over" : ""}`}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setOver(false);
                choose(e.dataTransfer.files[0] ?? null);
              }}
            >
              <label>
                Picture (PNG, JPEG or WebP)
                <input
                  key={fileKey}
                  type="file"
                  accept=".png,.jpg,.jpeg,.webp"
                  aria-invalid={!!info && !info.ok}
                  aria-describedby={info && !info.ok ? "picture-error" : undefined}
                  onChange={(e) => choose(e.target.files?.[0] ?? null)}
                />
              </label>
              <span className="muted">…or drop the file here.</span>
              {info?.url && <img src={info.url} alt="the chosen picture" />}
              {info &&
                (info.ok ? (
                  <span>{info.text}</span>
                ) : (
                  <span className="error" role="alert" id="picture-error">
                    {info.text}
                  </span>
                ))}
              {file && <button onClick={clearFile}>Remove file</button>}
            </div>
            {mode === "image" && dim === "3d" && (
              <label>
                Views in the picture
                <select value={sheet} onChange={(e) => setSheet(Number(e.target.value))}>
                  <option value={-1}>One view</option>
                  {SHEETS.map(([title], i) => (
                    <option key={title} value={i}>
                      Turnaround sheet: {title}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {mode === "image" && dim === "3d" && sheet >= 0 && (
              <p className="muted">
                Use equal-width columns with the same pose and zoom. Faithful then uses every view. The views must be the same height, within 5%.
              </p>
            )}
            <p className="muted">
              {dim === "2d"
                ? "Click Add the picture to keep it as it is. Then make pixel art from it."
                : mode === "image"
                ? "Click Remove background to cut out your subject. You can check the result before you make the 3D model. A picture that is already transparent keeps its own cut-out."
                : "Click Import model to add it as it is. Its materials stay unchanged. Then you can check it, export it, or edit it in Blender."}
            </p>
            <button className="primary" disabled={busy || !file} onClick={submit}>
              {busy ? "Working…" : dim === "2d" ? "Add the picture" : mode === "image" ? "Remove background" : "Import model"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

const STEP_ACTS: Record<string, string> = {
  brief: "Writing the brief",
  concepts: "Generating concepts",
  prep: "Removing the background",
  pixelize: "Making pixel art",
  redraw: "Redrawing the picture",
  sprites: "Rendering sprites",
  trellis2: "Generating the 3D model",
  clean: "Cleaning",
  remesh: "Simplifying",
  uv: "Unwrapping",
  bake: "Baking textures",
  motion: "Generating the motion",
  mocap: "Motion from video",
  parts: "Finding the parts",
  project: "Projecting the face",
  refine: "Refining the texture",
  rig_fit: "Fitting the skeleton",
  rig_skin: "Skinning",
  anim: "Retargeting animations",
  import_model: "Importing the model",
  validate: "Checking",
  export: "Exporting",
};

function Workspace(props: {
  asset: Asset | undefined;
  sel: Sel;
  version: Meta | null;
  jobs: Record<string, Job>;
  steps: Presets;
  presets: Record<string, Record<string, unknown>>;
  missing: (group: string) => boolean;
  chain: Chain;
  pick: (version: string) => void;
  guard: (p: Promise<unknown>) => Promise<void>;
  onRenamed: () => void;
  onVersion: (asset: string, version: string, body: { starred?: boolean; label?: string }) => Promise<void>;
  onDeleted: (asset: string, versions: string[]) => void;
  assetName: (id?: string) => string;
  track: (job_id: string, step: string, asset?: string, input?: string | null) => void;
  onPull: (asset: string) => Promise<void>;
  onCancel: (id: string) => void;
}) {
  // one docked panel beside the view, History or Licences: its own column, so nothing is covered and the focus stays
  // visible (UX review 2026-10-12, UX-03 + UX-08)
  const [side, setSide] = useState<"history" | "licences" | null>(null);
  const [original, setOriginal] = useState(false);
  const [bigRef, setBigRef] = useState(false);
  const [rigView, setRigView] = useState<RigView | null>(null);
  const [picked, setPicked] = useState("image_0"); // the 2D track: the picture of a set the panel works on
  const [animEditing, setAnimEditing] = useState(false); // M37: the animation editor's timeline under the view
  const pixelEd = usePixelEditor(); // M56: the pixel editor, its canvas in the view and its tools in the panel
  // another version shown (a pick, or the save's new version) ends the editing; a switch with unsaved edits asked first
  useEffect(() => {
    const v = props.version;
    if (pixelEd.editing && pixelEd.target?.version.version !== v?.version) pixelEd.close();
    // M57: a blank sheet (New asset, Draw it yourself) is for drawing: it opens in the editor
    if (props.asset && v && v.step === "pixelize" && v.params?.op === "edit" && !v.parent && v.metrics?.edited_frames === 0) pixelEd.start(props.asset.id, v);
  }, [props.asset?.id, props.version?.version]); // a version name repeats across assets
  const [starredOnly, setStarredOnly] = useState(false);
  const [compare, setCompare] = useState<string | null>(null); // M42: the version compared with the shown one
  const [licences, setLicences] = useState<Licence[] | null>(null); // M42: the Licences panel (open once loaded)
  const [unfolded, setUnfolded] = useState<Set<string>>(new Set()); // History: the folded runs the user opened (first version of the run)
  const menu = useRef<HTMLDetailsElement>(null); // the Tools menu (UX Pass 2)
  const historyButton = useRef<HTMLButtonElement>(null);
  const licencesButton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (panel.current) panel.current.scrollTop = 0;
    setPicked("image_0");
    setCompare(null); // picking another version ends compare mode
  }, [props.version?.version]); // a new checkpoint starts at its action
  useEffect(() => {
    if (side === "licences" && !licences) api.licenses().then(setLicences, () => setLicences([]));
  }, [side, licences]);
  const closeSide = () => {
    (side === "licences" ? licencesButton : historyButton).current?.focus(); // back to what opened it
    setSide(null);
  };
  useEffect(() => {
    // Escape closes the docked panel wherever the focus is (UX-08)
    if (!side) return;
    const key = (e: KeyboardEvent) => e.key === "Escape" && closeSide();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [side]);
  const { sel, version: v } = props;
  const versions = sel.versions;
  const id = sel.asset!;
  if (sel.missing)
    return (
      <div className="page">
        <div className="empty">
          <h2>This asset doesn't exist.</h2>
          <p className="muted">This link may be wrong, or the asset was deleted.</p>
          <a className="button primary" href="#/library">
            Back to the library
          </a>
        </div>
      </div>
    );
  if (!sel.loaded) return <div className="page muted">Loading…</div>;
  const name = props.asset?.name ?? id;
  const active = Object.values(props.jobs)
    .filter((j) => j.asset === id && (j.status === "queued" || j.status === "running"))
    .at(-1);
  if (!v)
    return (
      <div className="page">
        <h1>{name}</h1>
        {active ? (
          <ActiveJob job={active} onCancel={props.onCancel} />
        ) : (
          <p className="muted">This asset is empty. Use New asset to add a picture or a model.</p>
        )}
      </div>
    );
  const stage = phaseOf(versions, v);
  const kind = props.asset?.kind ?? sel.kind;
  // M53: a check or an export of a sprite sheet: shown with its sheet, its panel the 3D one (the checklist, the files)
  const sheetOf = ["validate", "export"].includes(v.step) ? versions.find((x) => x.version === v.parent && is2D(x)) : undefined;
  const tasks = kind === "2d" ? TASKS_2D : TASKS_3D;
  const task = taskOf(versions, v, kind);
  const imported = isImported(versions, v);
  const onBranch = (x: Meta) => ancestors(versions, x).includes(v) || ancestors(versions, v).includes(x);
  const newest = (ok: (x: Meta) => boolean) => versions.filter((x) => ok(x) && onBranch(x)).at(-1);
  const target = (i: number) => newest((x) => taskOf(versions, x, kind) === i);
  const src = source(versions, v);
  const glb = src && hasGlb(src) ? fileUrl(id, src.version, mainFile(src)!) : null;
  // the optional tools the shown checkpoint offers (the same rules as Actions' sections)
  const face = (v.step === "bake" || v.step === "project") && generated(versions, v);
  const detail = TEXTURED.has(v.step) && hasGlb(v);
  const parts = stage === 2 && hasGlb(v) && v.step !== "export";
  // a cut-out can be compared with the picture it came from
  const parent = versions.find((x) => x.version === v.parent);
  const originalFile =
    v.step === "prep" && parent
      ? parent.step === "concepts"
        ? parent.outputs[`cand_${v.params.pick}`]
        : hasImage(parent)
          ? mainFile(parent)
          : undefined
      : undefined;
  const image = glb
    ? null
    : original && originalFile && parent
      ? fileUrl(id, parent.version, originalFile)
      : src && hasImage(src)
        ? fileUrl(id, src.version, mainFile(src)!)
        : null;
  const ref = glb ? ancestors(versions, src).find((a) => a.step === "prep") : undefined;
  const closeDrawer = closeSide;
  // the Character task's panel (optional): the rig form of a prepared model, or the skeleton, skinned or animated
  // version itself; a check or export of one shows the version it was made from
  const rig = glb && stage >= 2 && stage <= 4 && (
    <RigPanel
      key={`${id}/${src!.version}`}
      asset={id}
      version={src!}
      motions={versions.filter((x) => x.step === "motion" || x.step === "mocap" || x.step === "import_motion")}
      onView={setRigView}
      onEditing={setAnimEditing}
      chain={props.chain}
      onPick={props.pick}
      onRun={(params, step = "rig_fit", parent = src!.version) =>
        props.guard(
          (async () => {
            const { job_id } = await api.run(id, step, parent, params);
            props.track(job_id, step, id, parent);
          })(),
        )
      }
    />
  );
  // opens the panel section with that id (and every <details> around it) and scrolls to it: the Tools menu and the task row use it
  const reveal = (id: string) => {
    const el = document.getElementById(id);
    for (let d: HTMLElement | null = el; d; d = d.parentElement) if (d instanceof HTMLDetailsElement) d.open = true;
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  // a task with no checkpoint of its own yet opens where it is configured: Generate on the cut-out, Prepare on the raw
  // model, Character and Finish in the shown panel; otherwise its button is disabled and says what it needs
  const opens = (t: Task): (() => void) | null => {
    const at = (step: string, id: string) => {
      const x = newest((y) => y.step === step && y.params?.op !== "texture");
      return !x ? null : x.version === v.version ? () => reveal(id) : () => props.pick(x.version);
    };
    if (t.id === "generate") return at("prep", "generate");
    if (t.id === "prepare") return at("trellis2", "prepare");
    if (t.id === "character") return rig && stage === 2 ? () => reveal("character") : null;
    if (t.id === "finish" && is2D(v)) return () => reveal("finish"); // M53: a sheet's check and export
    if (t.id === "finish") return glb && stage >= 2 && v.step !== "export" ? () => reveal("finish") : null;
    if (t.id === "pixelize") return document.getElementById("pixelize") ? () => reveal("pixelize") : null; // 2D: the shown picture's form
    if (t.id === "edit") return isSheet(v) && !pixelEd.editing ? () => pixelEd.start(id, v) : null; // M56: the shown sheet
    return null;
  };
  // History: milestones stay as rows; clean, simplify and unwrap runs fold into one "N steps" row (opened by hand, or when one is the current version)
  const shown = starredOnly ? versions.filter((x) => x.starred) : versions;
  const rows: { x?: Meta; fold?: Meta[]; open?: boolean }[] = [];
  for (const x of shown) {
    const prev = rows.at(-1);
    if (starredOnly || !FOLDED.has(x.step)) rows.push({ x });
    else if (prev?.fold) prev.fold.push(x);
    else rows.push({ fold: [x] });
  }
  const listed = rows.flatMap((r) => {
    if (!r.fold) return [r];
    const open = unfolded.has(r.fold[0].version) || r.fold.some((x) => x.version === v.version);
    return [{ ...r, open }, ...(open ? r.fold.map((x) => ({ x })) : [])];
  });
  const HistoryRow = (p: { x: Meta }) => {
    const x = p.x;
    return (
      <li key={x.version}>
        <button
          aria-current={x.version === v.version ? "true" : undefined}
          style={{
            paddingLeft: 12 + 6 * Math.min(ancestors(versions, x).length - 1, 4), // deep chains must not squeeze the label
          }}
          onClick={() => props.pick(x.version)}
        >
          <img
            className="thumb-sm"
            src={`/api/assets/${id}/versions/${x.version}/thumb`}
            alt=""
            loading="lazy"
            onError={(e) => (e.currentTarget.style.visibility = "hidden")}
          />
          <span className="vlabel" title={x.label ? `${label(x)} · ${x.label}` : label(x)}>
            {label(x)}
            {x.label && <em> · {x.label}</em>}
            {x.restricted && <span className="tag">restricted</span>}
          </span>
          <span className="muted" title={x.created ? when(x.created) : ""}>
            {x.version.slice(0, 3)}
          </span>
        </button>
        <button
          className="icon"
          aria-label={`${x.starred ? "Unstar" : "Star"} ${label(x)} ${x.version.slice(0, 3)}`}
          aria-pressed={!!x.starred}
          title={x.starred ? "Starred: click to remove" : "Star this version to keep it easy to find"}
          onClick={() => props.guard(props.onVersion(id, x.version, { starred: !x.starred }))}
        >
          {x.starred ? "★" : "☆"}
        </button>
        {x.version !== v.version && (
          <button
            className="icon"
            aria-label={`Compare ${label(x)} ${x.version.slice(0, 3)} with the shown version`}
            title="Compare with the shown version"
            onClick={() => {
              setCompare(x.version);
              closeDrawer();
            }}
          >
            ⇆
          </button>
        )}
        <button
          className="icon"
          aria-label={`Label ${label(x)} ${x.version.slice(0, 3)}`}
          title="Give this version a label"
          onClick={() => {
            const l = window.prompt("Label", x.label ?? "");
            if (l !== null && l !== (x.label ?? "")) props.guard(props.onVersion(id, x.version, { label: l }));
          }}
        >
          ✎
        </button>
        <button
          className="danger icon"
          aria-label={`Delete ${label(x)} ${x.version.slice(0, 3)} and what was made from it`}
          title="Delete this version and everything made from it"
          onClick={() => {
            const branch = versions.filter((y) => ancestors(versions, y).includes(x));
            const rest = branch.length - 1;
            const what = rest ? `"${label(x)}" and the ${rest} version${rest > 1 ? "s" : ""} made from it` : `"${label(x)}"`;
            if (window.confirm(`Delete ${what}?\n\nThey move to Recently deleted, at the end of the library, where you can restore them.`))
              props.guard(api.deleteVersion(id, x.version).then((r) => props.onDeleted(id, r.deleted)));
          }}
        >
          ×
        </button>
      </li>
    );
  };
  return (
    <div className="workspace">
      <div className="ws-title">
        <h1>{name}</h1>
        <button
          className="small"
          onClick={() => {
            const n = window.prompt("New name", name);
            if (n && n.trim() && n !== name) props.guard(api.renameAsset(id, n).then(props.onRenamed));
          }}
        >
          Rename
        </button>
        <span className="muted">
          {label(v)} · {v.version.slice(0, 3)}
          {v.restricted && <span className="tag">restricted</span>}
        </span>
        <button ref={licencesButton} className="small" aria-expanded={side === "licences"} onClick={() => (side === "licences" ? closeSide() : setSide("licences"))}>
          Licences
        </button>
        <button ref={historyButton} aria-expanded={side === "history"} onClick={() => (side === "history" ? closeSide() : setSide("history"))}>
          History ({versions.length})
        </button>
        <ol className="stages" aria-label="Tasks">
          {imported && kind !== "2d" && (
            <li>
              <span className="chip">Imported model</span>
            </li>
          )}
          {tasks.map((t, i) => {
            if (imported && kind !== "2d" && i < 2) return null; // no reference and no generation behind an imported model
            const to = target(i);
            const open = to ? () => props.pick(to.version) : opens(t);
            return (
              <li key={t.id}>
                {/* opens the newest checkpoint of that task on this branch, or where the task is configured; never runs a step */}
                {/* not reachable yet: still focusable, so the keyboard and a screen reader get the reason a pointer gets
                    from the title (UX-15: guidance without relying on hover) */}
                <button
                  aria-disabled={open ? undefined : true}
                  aria-describedby={open ? undefined : `needs-${t.id}`}
                  aria-current={i === task ? "step" : undefined}
                  className={i < task && to ? "done" : ""}
                  title={open ? undefined : `Needs ${t.needs}`}
                  onClick={open ?? undefined}
                >
                  {i < task && to && "✓ "}
                  {t.label}
                  {t.optional && <small> optional</small>}
                </button>
                {!open && (
                  <span id={`needs-${t.id}`} className="needs">
                    Needs {t.needs}
                  </span>
                )}
              </li>
            );
          })}
        </ol>
        {/* the optional tools: a native menu; an entry opens its section of the panel (UX-06) */}
        <details className="tools" ref={menu} onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && (e.currentTarget.open = false)}>
          <summary>Tools ▾</summary>
          <ul>
            {/* a tool not open here says where it is, in the stages' own words (owner 2026-10-12: not "not for this checkpoint") */}
            {(
              [
                ["face", "Face from image", face, "project", null, "at Prepare, once textured; generated models only"],
                ["detail", "Texture detail", detail, "refine", "refine", "at Prepare, once textured"],
                ["parts", "Parts", parts, "parts", "parts", "at Prepare"],
                ["sprites", "Sprites for 2D", spritesFrom(versions, v), "sprites", null, "at Prepare (one still frame) or after Character (animated)"],
                ["redraw", "Upscale or redraw", kind === "2d" && redrawable(v), "redraw", null, "on a picture (not pixel art, sprites, a tile or layers)"],
                ["blender", "Edit in Blender", hasGlb(v), null, null, "from Generate on, once there is a 3D model"],
                ["advanced", "Advanced: run any step", true, null, null, ""],
              ] as [string, string, boolean, string | null, string | null, string][]
            ).map(([id, name, on, step, group, where]) => (
              <li key={id}>
                <button className="small" disabled={!on} onClick={() => ((menu.current!.open = false), reveal(id))}>
                  {name}
                </button>
                <span className="muted">
                  {kind === "2d" && id !== "advanced" && id !== "redraw" ? (
                    id === "sprites" ? "in a 3D character: Animate this character" : "3D models only"
                  ) : kind !== "2d" && id === "redraw" ? (
                    "2D pictures only"
                  ) : (
                    <>
                      {!on ? where : step && newest((x) => x.step === step) ? "has a checkpoint" : "available"}
                      {group && props.missing(group) && (
                        <>
                          {" · "}
                          <a href={setupAt(group)}>needs a setup item</a>
                        </>
                      )}
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </details>
      </div>
      <div className={`ws-body${side ? " with-side" : ""}`}>
        {side === "licences" && (
          <aside className="side" role="region" aria-label="Licences">
            <div className="head">
              <h2>
                Licences of {label(v)} · {v.version.slice(0, 3)}
              </h2>
              <button className="small" autoFocus onClick={closeSide}>
                Close
              </button>
            </div>
            {!licences ? (
            <p className="muted">Loading…</p>
          ) : (
            <ul>
              {(v.licenses ?? []).map((n) => {
                const l = licences.find((x) => x.name === n);
                return (
                  <li key={n}>
                    <strong>{n}</strong> · {l?.license ?? "unknown"}
                    {l?.attribution && <div className="muted">{l.attribution}</div>}
                  </li>
                );
              })}
              {!v.licenses?.length && <li className="muted">No component of this version asks for a credit or a licence note.</li>}
            </ul>
          )}
            {imported && <p className="muted">Your file: Effigy does not know this file's licence. It is yours to check.</p>}
            {v.restricted && <p className="error">Restricted: a component of this version is not cleared for commercial use, so it cannot be exported.</p>}
          </aside>
        )}
        {side === "history" && (
          <aside className="side" role="region" aria-label="History">
            <div className="head">
              <h2>History</h2>
              <button autoFocus onClick={closeSide}>
                Close
              </button>
            </div>
            <label className="check">
              <input type="checkbox" checked={starredOnly} onChange={(e) => setStarredOnly(e.target.checked)} />
              Starred only
            </label>
            <ol id="versions">
              {listed.map((r) =>
                r.fold ? (
                  <li key={`fold-${r.fold[0].version}`}>
                    <button
                      className="muted"
                      aria-expanded={r.open}
                      onClick={() => setUnfolded((u) => (u.delete(r.fold![0].version) || u.add(r.fold![0].version), new Set(u)))}
                    >
                      <span className="vlabel">
                        {r.open ? "▾" : "▸"} {r.fold.length} step{r.fold.length === 1 ? "" : "s"}
                      </span>
                    </button>
                  </li>
                ) : (
                  HistoryRow({ x: r.x! })
                ),
              )}
            </ol>
          </aside>
        )}
        {compare && versions.find((x) => x.version === compare) ? (
          <Compare asset={id} versions={versions} shown={v} other={versions.find((x) => x.version === compare)!} onClose={() => setCompare(null)} />
        ) : kind === "2d" || is2D(v) || sheetOf ? (
          pixelEd.editing ? (
            <EditorCanvas ed={pixelEd} />
          ) : (
            <PixelView asset={id} version={sheetOf ?? v} picked={picked} onPick={setPicked} />
          )
        ) : (
        <ViewerPane
          glb={glb}
          image={image}
          rig={rigView}
          editing={animEditing}
          parts={partNames(v)}
          quads={!!src && ancestors(versions, src).find((a) => a.step === "remesh")?.params?.topology === "quad"}
          overlay={v.step === "concepts" ? <Candidates key={`${id}/${v.version}`} asset={id} version={v} chain={props.chain} /> : null}
          hint={v.step === "brief" ? "The concept images appear here." : null}
        >
          {originalFile && (
            <div className="toolbar" role="group" aria-label="Reference view">
              <button aria-pressed={!original} onClick={() => setOriginal(false)}>
                Cut-out
              </button>
              <button aria-pressed={original} onClick={() => setOriginal(true)}>
                Original
              </button>
            </div>
          )}
          {ref && (
            <button className={`ref-thumb ${bigRef ? "big" : ""}`} aria-pressed={bigRef} onClick={() => setBigRef((b) => !b)} title="reference picture">
              <img src={fileUrl(id, ref.version, ref.outputs.image ?? mainFile(ref)!)} alt="reference picture" />
            </button>
          )}
        </ViewerPane>
        )}
        <aside className="panel" aria-label="Next step" ref={panel}>
          <h2 className="sr-only">Next step</h2>
          {active && <ActiveJob job={active} onCancel={props.onCancel} />}
          {(kind === "2d" || is2D(v)) && !sheetOf ? (
            pixelEd.editing ? (
              <EditorPanel ed={pixelEd} onSave={(params) => props.chain(id, pixelEd.target!.version.version, [["pixelize", params]])} />
            ) : (
            <Actions2D
              onEdit={isSheet(v) ? () => pixelEd.start(id, v) : undefined}
              finish={is2D(v) && <Finish2D asset={id} version={v} versions={versions} chain={props.chain} primary={!animatable(versions, v, kind)} />}
              key={`${id}/${v.version}`}
              asset={id}
              name={props.asset?.name}
              kind={kind}
              version={v}
              versions={versions}
              chain={props.chain}
              missing={props.missing}
              picked={picked}
            />
            )
          ) : (
            <Actions
              key={`${id}/${v.version}`}
              asset={id}
              version={v}
              versions={versions}
              presets={props.presets}
              chain={props.chain}
              missing={props.missing}
              pick={props.pick}
              running={active && RECIPE.some(([s]) => s === active.step) ? active.input : undefined}
              rig={rig}
            />
          )}
          {/* Blender works on a 3D model: a picture, a brief or a motion clip has none */}
          {hasGlb(v) && (
            <details id="blender">
              <summary>Edit in Blender</summary>
              <BlenderPanel asset={id} version={v} onPull={() => props.onPull(id)} />
            </details>
          )}
          <details id="advanced">
            <summary>Advanced: run any step</summary>
            <RunStep
              steps={props.steps}
              parent={v.version}
              onRun={(step, params) =>
                props.guard(
                  (async () => {
                    const { job_id } = await api.run(id, step, v.version, JSON.parse(params));
                    props.track(job_id, step, id, v.version);
                  })(),
                )
              }
            />
          </details>
        </aside>
      </div>
    </div>
  );
}

function ActiveJob(props: { job: Job; onCancel: (id: string) => void }) {
  const j = props.job;
  return (
    <div className="job" role="status">
      <strong>
        {j.status === "queued" ? "Waiting for the current operation to finish" : `${STEP_ACTS[j.step ?? ""] ?? j.step}…`}
        {j.of && <span className="muted"> · step {j.of}</span>}
      </strong>
      {/* no measured total: per-step progress only, indeterminate when the step reports none */}
      {j.status === "running" && (j.p != null ? <progress value={j.p} /> : <progress />)}
      {j.text && <span className="muted">{j.text}</span>}
      <div className="row">
        <button onClick={() => props.onCancel(j.job_id)}>Cancel</button>
      </div>
    </div>
  );
}

// the action panel: one primary action per checkpoint (docs/archive/UI_REDESIGN_PROPOSAL.md §6 decision table)
// M23: the generated picture projected onto the texture; a project version shows what it did
function FacePanel(props: { asset: string; version: Meta; chain: Chain }) {
  const [busy, run] = useBusy();
  const [region, setRegion] = useState("head");
  const [strength, setStrength] = useState(1);
  const [big, setBig] = useState(false);
  const v = props.version;
  const m = v.metrics ?? {};
  const pct = Math.round(Number(m.changed ?? 0) * 100);
  const iou = Number(m.iou ?? 0).toFixed(2);
  return (
    <section id="face">
      <h3>Face from image</h3>
      {v.step === "project" && (
        <>
          <p className={m.skipped ? "note warn" : "muted"}>
            {m.skipped
              ? `Not applied: the image does not line up with the mesh (silhouette match ${iou}).`
              : `Face from the image: silhouette match ${iou}, ${pct}% of the atlas${
                  m.region === "front" ? "" : `, head from ${m.head_from === "pose" ? "the pose model" : "the top of the figure"}`
                }.`}
            {m.upscaled === false && (
              <>
                {" "}
                Projected without the 4× upscale: <a href={setupAt("texture")}>install Face detail</a>.
              </>
            )}
          </p>
          {v.outputs.overlay && (
            <button className="image-button" aria-pressed={big} aria-label={big ? "Show the picture small" : "Enlarge the picture"} onClick={() => setBig((b) => !b)}>
              <img
                src={fileUrl(props.asset, v.version, v.outputs.overlay)}
                alt="the image with the mesh silhouette (red) and the head band (yellow)"
                style={{ width: big ? "100%" : 160 }}
              />
            </button>
          )}
        </>
      )}
      <p className="muted">
        Click Face from image to copy the face from your picture onto the texture. This makes the face as sharp as the picture. It takes a few seconds.
      </p>
      <div className="pair">
        <label>
          Region
          <select aria-label="Region" value={region} onChange={(e) => setRegion(e.target.value)}>
            <option value="head">Head</option>
            <option value="front">Whole front</option>
          </select>
        </label>
        <label>
          Strength{" "}
          <input
            type="number"
            aria-label="Strength"
            min={0.1}
            max={1}
            step={0.1}
            value={strength}
            onChange={(e) => setStrength(Math.min(1, Math.max(0.1, Number(e.target.value) || 1)))}
          />
        </label>
      </div>
      <button className="primary" disabled={busy} onClick={() => run(() => props.chain(props.asset, v.version, [["project", { region, strength }]]))}>
        Face from image
      </button>
    </section>
  );
}

// a panel slider: its name above, the bar the panel's width with its value beside it (no wrapped value line)
function Slider(p: { name: string; label: string; min: number; max: number; step: number; value: number; digits?: number; set: (x: number) => void }) {
  return (
    <label>
      {p.name}
      <span className="slider">
        <input type="range" aria-label={p.label} min={p.min} max={p.max} step={p.step} value={p.value} onChange={(e) => p.set(Number(e.target.value))} />
        {p.digits != null && <span>{p.value.toFixed(p.digits)}</span>}
      </span>
    </label>
  );
}

// M43b: the texture's detail redrawn by the Tile model from zoomed views; a refine version shows each view before | after
const TEXTURED = new Set(["bake", "project", "refine"]);
function DetailPanel(props: { asset: string; version: Meta; versions: Meta[]; chain: Chain; missing: boolean }) {
  const [busy, run] = useBusy();
  const brief = ancestors(props.versions, props.version).find((a) => a.step === "brief");
  const [prompt, setPrompt] = useState(String(brief?.params.prompt ?? ""));
  const [strength, setStrength] = useState(1);
  const [delight, setDelight] = useState(0);
  const [big, setBig] = useState(false);
  const [k4, setK4] = useState(false);
  const [painting, setPainting] = useState(false); // M68: the brush on the model
  const [brush, setBrush] = useState(0.03);
  const [erase, setErase] = useState(false); // the brush wipes paint off instead of adding it
  const [paintStrength, setPaintStrength] = useState(1); // its own: the paint is a separate tool from Refine texture
  const [becomes, setBecomes] = useState(""); // what the painted part becomes: never the whole asset's description
  const [err, setErr] = useState("");
  const sent = useRef<{ rev: number; version: string } | null>(null); // the paint uploaded last, while unchanged
  useEffect(
    () => () => {
      window.__viewer?.setPaint(false); // the brush off when the panel goes
      if (window.__viewer) window.__viewer.paintErase = false;
    },
    [],
  );
  const v = props.version;
  const m = v.metrics ?? {};
  // M68: the painted mask uploaded beside this version, then refine's edit on it
  const editPainted = () =>
    run(async () => {
      setErr("");
      const viewer = window.__viewer;
      const blob = await viewer?.paintMask();
      if (!viewer || !blob) return setErr("Paint the part to change on the model first.");
      const rev = viewer.paintRev();
      if (sent.current?.rev !== rev)
        try {
          sent.current = { rev, version: (await api.upload(props.asset, new File([blob], "paint.png", { type: "image/png" }))).version };
        } catch (e) {
          return setErr(explain(e));
        }
      await props.chain(props.asset, v.version, [["refine", { op: "edit", region: sent.current!.version, prompt: becomes.trim(), strength: paintStrength }]]);
    });
  const go = (params: object) => run(() => props.chain(props.asset, v.version, [["refine", { ...params, ...(delight ? { delight } : {}) }]]));
  // a redraw: the strength, the description if any, 4K if ticked
  const redraw = (params: object) => go({ strength, ...(prompt.trim() ? { prompt: prompt.trim() } : {}), ...(k4 ? { texture_size: 4096 } : {}), ...params });
  const shadows = m.ao_luma_corr != null && Number(m.delight) > 0 ? ` Baked shadows taken out (${m.delight}): their trace in the colours ${m.ao_luma_corr_before} → ${m.ao_luma_corr}.` : "";
  return (
    <section id="detail">
      <h3>Texture detail</h3>
      {v.step === "refine" && (
        <>
          <p className="muted">
            {m.op === "edit"
              ? `The painted part (${m.region}) redrawn from ${m.views} view${m.views === 1 ? "" : "s"}: ${Math.round(Number(m.region_seen ?? 0) * 100)}% of it seen${m.changed_outside === 0 ? ", nothing outside it changed" : `; outside it a change of up to ${m.changed_outside}`}.`
              : Number(m.views) > 0
                ? `${m.views} of ${m.views_planned ?? m.views} views in ${Math.round(Number(m.seconds ?? 0) / 60)} min: ${Math.round(Number(m.refined ?? 0) * 100)}% of the texture redrawn, colour drift ${m.drift ?? "?"}.`
                : "No redraw."}
            {shadows} Compare with the version before it in History.
          </p>
          {v.outputs.views && (
            <button className="image-button" aria-pressed={big} aria-label={big ? "Show the views small" : "Enlarge the views"} onClick={() => setBig((b) => !b)}>
              <img src={fileUrl(props.asset, v.version, v.outputs.views)} alt="each view before (left) and after (right)" style={{ width: big ? "100%" : 240 }} />
            </button>
          )}
        </>
      )}
      {props.missing && (
        <p className="note warn">
          The texture-detail model wasn't found. <a className="button small" href={setupAt("refine")}>Set it up</a>.
        </p>
      )}
      <div className="stack tool">
        <h4>Refine texture</h4>
        <p className="muted">
          Click Refine texture to redraw the texture's fine detail (buttons, stitching, wood grain) from close-up views. The shape and the colours stay. It
          takes about 4–6 minutes. To work on one place, frame it in the 3D view, describe what should be there, and click Redraw this view: the square in the
          middle of the view is redrawn, the rest stays (a face from the picture stays too).
        </p>
        <label>
          Description{" "}
          <input aria-label="Description" value={prompt} placeholder="the asset's own description" onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <Slider name="Strength" label="Detail strength" min={0.3} max={1} step={0.1} digits={1} value={strength} set={setStrength} />
        <label className="check" title="4096² instead of the texture's size, for characters seen up close: more views on a tall model (about twice), and the time and memory are not measured yet">
          <input type="checkbox" checked={k4} onChange={(e) => setK4(e.target.checked)} /> 4K texture (slower, not measured yet)
        </label>
        <button className="primary" disabled={busy || props.missing} onClick={() => redraw({})}>
          Refine texture
        </button>
        <button
          disabled={busy || props.missing}
          title="Redraws the square in the middle of the 3D view as it is now, with the description above: frame a detail, describe it, click"
          onClick={() => redraw({ views: [window.__viewer!.view()] })}
        >
          Redraw this view
        </button>
      </div>
      <div className="stack tool">
        <h4>Baked shadows</h4>
        <p className="muted">
          Lightens what the texture darkened where the model is occluded (from the bake's ambient occlusion), so the game engine's own shadows don't double
          them. Remove baked shadows alone takes a few seconds; Refine texture applies it too.
        </p>
        <Slider name="Baked shadows to remove" label="Baked shadows to remove" min={0} max={1} step={0.25} digits={2} value={delight} set={setDelight} />
        <button disabled={busy || !delight} title="Only takes the shadows out: no redraw, a few seconds" onClick={() => go({ views: [] })}>
          Remove baked shadows
        </button>
      </div>
      <div className="stack tool">
        <h4>Texture paint</h4>
        <p className="muted">
          To change one part, click Paint the part to change and paint it on the model (drag on it; Erase, or Shift, wipes a stroke that went too far; a drag
          beside it still turns the view), say what it should become, and click Redraw the painted part: it is redrawn from every side that sees it, and
          nothing outside it changes.
        </p>
        <label>
          It becomes{" "}
          <input aria-label="What the painted part becomes" value={becomes} placeholder="a gold lion's-head lock" onChange={(e) => setBecomes(e.target.value)} />
        </label>
        <Slider
          name="Brush"
          label="Brush size"
          min={0.01}
          max={0.1}
          step={0.01}
          value={brush}
          set={(x) => {
            setBrush(x);
            if (painting) window.__viewer?.setPaint(true, x);
          }}
        />
        <button aria-pressed={painting} disabled={busy || props.missing} onClick={() => setPainting(!!window.__viewer?.setPaint(!painting, brush))}>
          {painting ? "Stop painting" : "Paint the part to change"}
        </button>
        <div className="pair">
          <button
            aria-pressed={erase}
            disabled={busy}
            title="Wipes paint off where you drag, for a stroke that went too far (Shift does the same while you paint)"
            onClick={() => {
              if (window.__viewer) window.__viewer.paintErase = !erase;
              setErase(!erase);
            }}
          >
            Erase
          </button>
          <button disabled={busy} onClick={() => window.__viewer?.clearPaint()}>
            Clear the paint
          </button>
        </div>
        <Slider name="Strength" label="Paint strength" min={0.3} max={1} step={0.1} digits={1} value={paintStrength} set={setPaintStrength} />
        <button
          className="primary"
          disabled={busy || props.missing || !becomes.trim()}
          title={becomes.trim() ? "Redraws what is painted as you said; the rest stays as it is" : "Say above what the painted part should become"}
          onClick={editPainted}
        >
          Redraw the painted part
        </button>
      </div>
      {err && (
        <p className="error" role="alert">
          {err}
        </p>
      )}
    </section>
  );
}

const VIEW_ORDER = [3, 4, 5, 6, 7, 8, 9, 10, 11, 0, 1, 2]; // from the library frame's +Z side (a model's front, if it faces +Z), then around
const partsOf = (v: Meta) => (v.metrics?.parts as Record<string, number> | undefined) ?? {};
// the names the viewer tints: a segment version's parts without Body; none for the views or any other version
const partNames = (v: Meta) => {
  const names = v.step === "parts" ? Object.keys(partsOf(v)).filter((n) => n !== "Body") : [];
  return names.length ? names : undefined;
};
const viewFile = (n: number) => `views/color_${String(n).padStart(4, "0")}.webp`;

// M31: name the pieces of a model. A mesh version renders the twelve views; a parts version lets you click a piece in a view
function PartsPanel(props: { asset: string; version: Meta; chain: Chain; missing: boolean }) {
  const [busy, run] = useBusy();
  const v = props.version;
  const m = v.metrics ?? {};
  const isParts = v.step === "parts";
  const holes = (m.holes as Record<string, number> | undefined) ?? {}; // views with background inside the silhouette
  const [view, setView] = useState(Number(m.view ?? 3));
  const [points, setPoints] = useState<[number, number, number][]>([]);
  const [notPart, setNotPart] = useState(false);
  const [name, setName] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const renamed = Object.fromEntries(
    Object.entries(names)
      .map(([was, now]) => [was, now.trim()])
      .filter(([was, now]) => now && now !== was),
  );
  const go = (params: object) => run(() => props.chain(props.asset, v.version, [["parts", params]]));
  const missing = props.missing && (
    <p className="note warn">
      Parts files were not found. <a className="button small" href={setupAt("parts")}>Set them up</a>.
    </p>
  );
  if (!isParts)
    return (
      <section id="parts">
        <h3>Parts</h3>
        <p className="muted">
          Optional: click Render the views to take twelve pictures of the model from around it. Then you can click a piece, such as a helmet or a shield, to
          give it a name. It takes about 15 seconds.
        </p>
        {missing}
        <button disabled={busy} onClick={() => go({ op: "views" })}>
          Render the views
        </button>
      </section>
    );
  const views = String(m.views ?? v.version);
  const shown = v.outputs.click && view === m.view ? fileUrl(props.asset, v.version, v.outputs.click) : fileUrl(props.asset, views, viewFile(view));
  const list = Object.entries(partsOf(v)).sort(([a], [b]) => Number(a === "Body") - Number(b === "Body"));
  return (
    <section id="parts">
      <h3>Parts</h3>
      <p className="muted">
        Click on the piece itself in the big picture, two or three points spread over it. If the result spills over, add a "not the part" point just outside
        it: tick the box below or hold Shift while you click. Each Find part makes a new version, and the next click builds on it. All parts splits the whole
        model and is slower (about a minute). To rename a part, type over its name in the list and click Rename parts: the exports keep each part as its own
        object with that name.
      </p>
      {missing}
      {Object.keys(holes).length > 0 && (
        <p className="note warn">
          A hole through the model shows in view{Object.keys(holes).length > 1 ? "s" : ""} {Object.keys(holes).join(", ")}: fine for a handle, a ring or an
          arm on the hip; a torn shell is not.
        </p>
      )}
      <div className="views" role="group" aria-label="Views">
        {VIEW_ORDER.map((n) => (
          <button key={n} className="view-thumb" aria-pressed={n === view} title={`View ${n}`} onClick={() => (setView(n), setPoints([]))}>
            <img src={fileUrl(props.asset, views, viewFile(n))} alt={`View ${n}`} loading="lazy" />
          </button>
        ))}
      </div>
      <div className="big-view checker">
        <img
          src={shown}
          alt="the chosen view: click a piece"
          style={{ cursor: "crosshair" }}
          onClick={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const at = (d: number, size: number) => Math.round((d / size) * 1024);
            setPoints((p) => [...p, [at(e.clientX - r.left, r.width), at(e.clientY - r.top, r.height), e.shiftKey || notPart ? 0 : 1]]);
          }}
        />
        {points.map(([x, y, l], i) => (
          <span key={i} className={`pt ${l ? "yes" : "no"}`} style={{ left: `${(x / 1024) * 100}%`, top: `${(y / 1024) * 100}%` }} />
        ))}
      </div>
      <div className="row">
        <label className="check">
          <input type="checkbox" checked={notPart} onChange={(e) => setNotPart(e.target.checked)} />
          Next click is not the part
        </label>
        <button disabled={!points.length} onClick={() => setPoints([])}>
          Clear points
        </button>
      </div>
      <div className="row">
        <input aria-label="Name of the part" placeholder="Name of the part" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" disabled={busy || !points.length || !name.trim()} onClick={() => go({ op: "segment", view, points, name: name.trim() })}>
          Find part
        </button>
        <button disabled={busy} onClick={() => go({ op: "segment", full: true, view })}>
          All parts
        </button>
      </div>
      {list.length > 0 && (
        <ul className="parts-list">
          {list.map(([n, faces]) => (
            <li key={n} className={n === "Body" ? "muted" : undefined}>
              {n === "Body" ? n : <input aria-label={`Name of ${n}`} value={names[n] ?? n} onChange={(e) => setNames({ ...names, [n]: e.target.value })} />}{" "}
              <span className="muted">{Number(faces).toLocaleString()} faces</span>
            </li>
          ))}
        </ul>
      )}
      {list.length > 1 && (
        <button disabled={busy || !Object.keys(renamed).length} onClick={() => go({ op: "rename", names: renamed })}>
          Rename parts
        </button>
      )}
    </section>
  );
}

function Actions(props: {
  asset: string;
  version: Meta;
  versions: Meta[];
  presets: Record<string, Record<string, unknown>>;
  chain: Chain;
  missing: (group: string) => boolean;
  pick: (version: string) => void;
  running?: string | null; // the version the asset's running job started from (UX-07)
  rig?: React.ReactNode;
}) {
  const [busy, run] = useBusy();
  const v = props.version;
  const { versions } = props;
  const stage = phaseOf(versions, v);
  const go = (parent: string | null, steps: [string, object][]) => run(() => props.chain(props.asset, parent, steps));
  const line = ancestors(versions, v);
  const mesh = source(versions, v);
  const report = mesh && versions.filter((x) => x.step === "validate" && x.parent === mesh.version).at(-1);
  const budget = budgetOf(line);
  const check = (
    <button className="primary" disabled={busy} onClick={() => go(v.version, [["validate", budget ? { max_triangles: budget } : {}]])}>
      Check model
    </button>
  );
  const face = (v.step === "bake" || v.step === "project") && generated(versions, v) && <FacePanel asset={props.asset} version={v} chain={props.chain} />;
  const detail = TEXTURED.has(v.step) && hasGlb(v) && (
    <DetailPanel key={`detail-${v.version}`} asset={props.asset} version={v} versions={versions} chain={props.chain} missing={props.missing("refine")} />
  );
  const parts = stage === 2 && hasGlb(v) && v.step !== "export" && <PartsPanel key={v.version} asset={props.asset} version={v} chain={props.chain} missing={props.missing("parts")} />;
  // M52: a character's animations (or a prepared prop) as 2D sprites
  const sprites = spritesFrom(versions, v) && (
    <section id="sprites">
      <h3>Sprites for a 2D game</h3>
      <SpritesForm key={`sprites-${v.version}`} asset={props.asset} version={v} chain={props.chain} />
    </section>
  );
  // UX-05: the optional tools and the character work sit below the main action, collapsed (the Tools menu and the
  // Character task open them); a tool's own checkpoint shows its panel open
  const toolNames = [face && "face", detail && "texture detail", parts && "parts", sprites && "sprites"].filter(Boolean);
  const tools = toolNames.length > 0 && (
    <details id="tools" open={["project", "refine", "parts"].includes(v.step)}>
      <summary>Tools: {toolNames.join(", ")}</summary>
      <div className="stack">
        {face}
        {detail}
        {parts}
        {sprites}
      </div>
    </details>
  );
  const character = props.rig && (
    <details id="character">
      <summary>Character: fit a skeleton (optional)</summary>
      {props.rig}
    </details>
  );
  const sheet = v.step === "validate" ? versions.find((x) => x.version === v.parent && is2D(x)) : undefined; // M53
  const exporter = (title?: string, primary = false) => (
    <ExportPanel
      key={`export-${(sheet ?? mesh)?.version}`}
      asset={props.asset}
      mesh={sheet ?? mesh}
      report={sheet ? v : (report ?? undefined)}
      chain={props.chain}
      title={title}
      primary={primary}
      profiles={sheet ? EXPORTS_2D : EXPORTS}
    />
  );

  if (v.step === "brief") {
    const missing = props.missing("concepts"); // UX-09: a known-missing capability makes the setup link the main action
    return (
      <>
        <section>
          <h3>Concept images</h3>
          <p>
            "{String(v.params.prompt ?? "")}" · {String(v.params.category ?? "")}
          </p>
          <p className="muted">Four pictures from your description, the best-looking first.</p>
          {missing ? (
            <>
              <p className="note warn">Concept-image files weren't found. Your description stays here.</p>
              <a className="button primary" href={setupAt("concepts")}>
                Set up concept images
              </a>
            </>
          ) : (
            <button className="primary" disabled={busy} onClick={() => go(v.version, [["concepts", {}]])}>
              Generate 4 concepts
            </button>
          )}
        </section>
        <EditBrief asset={props.asset} version={v} versions={versions} chain={props.chain} missing={missing} />
      </>
    );
  }
  if (v.step === "concepts") return <ConceptsPanel {...props} />;
  if (v.step === "upload" && v.outputs.model && !hasGlb(v)) return <OriginalFile asset={props.asset} version={v} chain={props.chain} />;
  if (hasImage(v) && !hasGlb(v) && v.step !== "export") { // an FBX or OBJ export ships PNG textures: not a picture
    if (v.step === "prep") return <GeneratePanel {...props} />;
    return (
      <section>
        <h3>Prepare the reference</h3>
        <p className="muted">Cut out your subject, so the 3D model matches your picture. A picture that is already transparent keeps its own cut-out.</p>
        <button className="primary" disabled={busy} onClick={() => go(v.version, [["prep", {}]])}>
          Remove background
        </button>
      </section>
    );
  }
  if (v.step === "motion" || v.step === "mocap" || v.step === "import_motion")
    return <MotionInfo asset={props.asset} version={v} versions={versions} chain={props.chain} />;
  if (v.step === "export") {
    const files = Object.values(v.outputs);
    const credits = (f: string) => /(CREDITS\.txt|LICENSES\.json)$/.test(f);
    const main = files.find((f) => !credits(f) && /\.(glb|fbx|obj|stl|usdz|3mf)$/i.test(f)) ?? files[0];
    const dest = [...EXPORTS, ...EXPORTS_2D].find((e) => e.id === String(v.metrics?.profile ?? v.params?.profile));
    // the package is the folder: several files belong together (an FBX and its PNGs, an OBJ and its .mtl), so saving
    // them all is the main action; a lone GLB stays a download (UX-10)
    const many = files.filter((f) => !credits(f)).length > 1;
    const download = (f: string, primary = false) => (
      <a className={`button ${primary ? "primary" : "small"}`} href={fileUrl(props.asset, v.version, f)} download>
        {primary ? `Download ${basename(f)}` : basename(f)}
      </a>
    );
    return (
      <>
        <section>
          <h3>Exported for {dest?.name ?? String(v.metrics?.profile ?? "")}</h3>
          <p className="muted">
            {files.length} file{files.length === 1 ? "" : "s"}, with CREDITS.txt and LICENSES.json for your game's credits. {dest?.next}
          </p>
          {many ? <SaveExport asset={props.asset} version={v} primary /> : main && download(main, true)}
          {!many && files.length > 1 && (
            <ul className="downloads">
              {files
                .filter((f) => f !== main)
                .map((f) => (
                  <li key={f}>{download(f)}</li>
                ))}
            </ul>
          )}
          <details>
            <summary>{many ? "Or download the files one by one" : "Or save all files to a folder"}</summary>
            {many ? (
              <ul className="downloads">
                {files.map((f) => (
                  <li key={f}>{download(f)}</li>
                ))}
              </ul>
            ) : (
              <SaveExport asset={props.asset} version={v} />
            )}
          </details>
        </section>
        <details>
          <summary>Export for another destination</summary>
          {exporter()}
        </details>
      </>
    );
  }
  if (v.step === "validate")
    return (
      <>
        <Checklist asset={props.asset} version={v} />
        {exporter("Export", true)}
        {stage === 3 || stage === 4 ? props.rig : character}
        {tools}
      </>
    );
  if (RECIPE.some(([s]) => s === v.step)) {
    // UX-07: what this checkpoint is, from what came after it and what runs; Continue only when steps remain
    const p = prepState(versions, v, props.running);
    const cap = (s: string) => s.replace(/^./, (c) => c.toUpperCase());
    const again = cap(RECIPE.find(([s]) => s === v.step)![1]);
    const left = p.remaining.map(cap);
    const own = p.next?.parent === v.version; // the check is of this mesh, not of a later step's
    const fails = Number(p.next?.metrics?.fail ?? 0);
    const state =
      p.state === "completed"
        ? own
          ? `${v.step === "bake" ? "Textures baked" : "Model prepared"}; ${fails ? `${fails} check${fails > 1 ? "s" : ""} failed` : "checked"}.`
          : `An earlier step; the chain was checked later (${label(p.next!)} ${p.next!.version.slice(0, 3)}).`
        : p.state === "active"
          ? "Preparation is running."
          : p.state === "historical"
            ? `An earlier step; the newest is ${label(p.next!)} ${p.next!.version.slice(0, 3)}.`
            : `Prepared up to ${again}; ${left.length > 1 ? `${left.slice(0, -1).join(", ")} and ${left.at(-1)} remain` : `${left[0]} remains`}.`;
    return (
      <>
        <section id="finish">
          <h3>Preparation</h3>
          <p>{state}</p>
          {p.state === "completed" && (
            <button className="primary" onClick={() => props.pick(p.next!.version)}>
              View checked result
            </button>
          )}
          {p.state === "historical" && <button onClick={() => props.pick(p.next!.version)}>Open {label(p.next!)}</button>}
          {p.state === "interrupted" && <PrepareForm {...props} from={v.step} button="Continue preparation" />}
        </section>
        {p.state === "completed" && own && exporter()}
        <details>
          <summary>Run this step again</summary>
          <div className="stack">
            <p className="muted">Only this step, on this model, with your own settings.</p>
            <PrepareForm {...props} from={v.step} only={v.step} button={again} />
          </div>
        </details>
        {tools}
        {character}
      </>
    );
  }
  // a mesh: prepared before / retextured / imported → check it; raw from the generator → prepare it
  const imported = !generated(versions, v);
  if (stage >= 2 && stage <= 4)
    return (
      <>
        {v.step === "import_model" && <ImportInfo asset={props.asset} version={v} chain={props.chain} />}
        {(stage === 3 || stage === 4) && props.rig}
        <section id="finish">
          <h3>{imported ? "Imported model" : "Check the model"}</h3>
          <p className="muted">{imported ? "Check it before you export it. Its original materials stay unchanged." : "Check it before you export it."}</p>
          {check}
        </section>
        {exporter()}
        {tools}
        {stage === 2 && character}
        {imported && (
          <details>
            <summary>Simplify geometry (copy)</summary>
            <div className="stack">
              <p className="muted">
                A simpler copy, its materials baked into one texture set; skins and animation are not kept. The original stays in History.
              </p>
              <PrepareForm {...props} from="" button="Simplify geometry" />
            </div>
          </details>
        )}
      </>
    );
  const prep = line.find((a) => a.step === "prep");
  return (
    <>
      <section id="prepare">
        <h3>Prepare for your project</h3>
        <p className="muted">Simplify and texture the model for your project.</p>
        <PrepareForm {...props} from="" button="Prepare model" />
      </section>
      {v.step === "trellis2" && prep && (
        <button disabled={busy} onClick={() => go(prep.version, [["trellis2", Object.fromEntries(Object.entries(v.params).filter(([k]) => k !== "seed"))]])}>
          Try another variation
        </button>
      )}
      {v.step === "trellis2" && (
        <details>
          <summary>Help</summary>
          <p className="muted">
            Right after generating, a model shows small holes and loose bits: Prepare model closes them (its first step is Clean). If a whole area is missing,
            such as a black cat's back, generate again from the picture with Background: Grey.
            {v.metrics?.background ? ` This one was made on ${v.metrics.background}.` : ""}
          </p>
        </details>
      )}
    </>
  );
}

// the description and the category, editable under a brief or a concepts version: a new brief and four new concepts
function EditBrief(props: { asset: string; version: Meta; versions: Meta[]; chain: Chain; missing: boolean }) {
  const brief = ancestors(props.versions, props.version).find((a) => a.step === "brief");
  const [prompt, setPrompt] = useState(String(brief?.params.prompt ?? ""));
  const [category, setCategory] = useState(String(brief?.params.category ?? "prop"));
  const [busy, run] = useBusy();
  return (
    <details>
      <summary>Edit description</summary>
      <div className="stack">
        <label>
          Description
          <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <Category name="edit-category" value={category} onChange={setCategory} />
        <button
          disabled={busy || props.missing || !prompt.trim()}
          onClick={() =>
            run(() =>
              props.chain(props.asset, null, [
                ["brief", { prompt, category }],
                ["concepts", {}],
              ]),
            )
          }
        >
          Generate 4 concepts from this
        </button>
      </div>
    </details>
  );
}

function ConceptsPanel(props: { asset: string; version: Meta; versions: Meta[]; chain: Chain; missing: (group: string) => boolean }) {
  return (
    <>
      <section>
        <h3>Pick a concept</h3>
        <p className="muted">Click the best picture, then Use this image. The ranking is only a hint. It does not promise a good 3D model.</p>
      </section>
      <EditBrief asset={props.asset} version={props.version} versions={props.versions} chain={props.chain} missing={props.missing("concepts")} />
    </>
  );
}

// M46b: best first (server/presets.PRESETS); the default is the first this card runs, the user's own pick is kept
const GENERATORS: Record<string, [string, string]> = {
  "faithful-hd": ["Faithful HD", "Pixal3D at 1536: a finer shape and 4K textures, for 24 GB cards. About 3.5× as long."],
  "faithful-hq": ["Faithful, high quality", "Pixal3D, 20 steps and 4K textures: about a quarter longer than Faithful."],
  faithful: ["Faithful", "Pixal3D: follows the picture closely."],
  fast: ["Fast", "TRELLIS.2: another model, needs less memory."],
};
const PICKED = "effigy.generator"; // the generator the user chose, this browser only

function GeneratePanel(props: {
  asset: string;
  version: Meta;
  versions: Meta[];
  presets: Record<string, Record<string, unknown>>;
  chain: Chain;
  missing: (group: string) => boolean;
}) {
  const names = Object.keys(props.presets);
  const [preset, setPick] = useState(() => {
    try {
      return localStorage.getItem(PICKED) ?? "";
    } catch {
      return "";
    }
  });
  const setPreset = (n: string) => {
    setPick(n);
    try {
      localStorage.setItem(PICKED, n);
    } catch {
      // private window: the pick lasts this page
    }
  };
  const [seed, setSeed] = useState("");
  const [background, setBackground] = useState("auto");
  const [busy, run] = useBusy();
  const [gpu, setGpu] = useState<Check | null>(); // undefined: still asking; null: no answer
  useEffect(() => {
    api.machine().then(
      (c) => setGpu(c.find((x) => x.id === "gpu") ?? null),
      () => setGpu(null),
    );
  }, []);
  // M45: a generator this GPU can never run is shown, disabled, with why (the server refuses it anyway); by preset
  // since M46b, with a reason of its own on a card the 3D models do not support
  const tooBig = (n: string) => gpu?.too_big?.find((t) => t.step === "trellis2" && t.value === n);
  const why = (t: NonNullable<ReturnType<typeof tooBig>>) => t.reason ?? `Needs about ${t.need_gb} GB of video memory; this GPU allows ${gpu?.budget_gb} GB.`;
  // M46b: the best this card runs (server order: best first) is the default, quality over speed (the owner)
  const best = gpu === undefined ? undefined : names.find((n) => !tooBig(n)) ?? names[0];
  const chosen = (names.includes(preset) && !tooBig(preset) ? preset : best) ?? "";
  const seedOk = seed === "" || Number.isInteger(Number(seed));
  const [name, blurb] = GENERATORS[chosen] ?? [chosen, ""];
  const short = name;
  const group = String(props.presets[chosen]?.weights ?? chosen); // the download a preset needs: its weights
  const big = chosen ? tooBig(chosen) : undefined; // every generator too big: the first is shown, and why it cannot run
  // a cut-out of a redraw (Animate this character): a blank face or other clothes are a seed away
  const redrawn = props.versions.find((x) => x.version === props.version.parent && x.step === "canon");
  // a character's cut-out (a description of kind Character, or a picture of your own) can stand in an A-pose before
  // its model is made, which rigs and animates better; not an object, a creature, a turnaround or a redrawn one
  const category = props.versions.find((x) => x.step === "brief")?.params?.category;
  const pose = !redrawn && props.version.params?.op !== "views" && (category === undefined || category === "character");
  const poseSetup = ["canon-flux", "pose"].find((g) => props.missing(g)); // the redraw and its pose check
  return (
    <section id="generate">
      <h3>Generate the 3D model</h3>
      <p className="muted">Generate a 3D model from this reference.</p>
      <p>
        {name}
        {chosen === best && !tooBig(chosen) && names.length > 1 ? " — Recommended for this GPU" : ""} <span className="muted">{blurb}</span>
      </p>
      {chosen && props.missing(group) ? (
        <>
          <p className="note warn">The {short} generator's files weren't found. Your reference stays here.</p>
          <a className="button primary" href={setupAt(group)}>
            Set up {short}
          </a>
        </>
      ) : (
        <button
          className="primary"
          disabled={busy || !chosen || !seedOk || !!big}
          onClick={() =>
            run(() =>
              props.chain(props.asset, props.version.version, [
                [
                  "trellis2",
                  {
                    ...props.presets[chosen],
                    ...(seed ? { seed: Number(seed) } : {}),
                    ...(background !== "auto" ? { background } : {}),
                  },
                ],
              ]),
            )
          }
        >
          Generate 3D
        </button>
      )}
      {pose && (
        <>
          <p className="muted">A character? Put it in a standing A-pose first, arms a little away from the body: it rigs and animates better (about a minute).</p>
          {poseSetup ? (
            <a className="button" href={setupAt(poseSetup)}>
              Set up {CAPABILITIES.find(([g]) => g === poseSetup)?.[1]}
            </a>
          ) : (
            <button disabled={busy} onClick={() => run(() => props.chain(props.asset, props.version.version, [["canon", {}], ["prep", {}]]))}>
              Put it in a standing pose
            </button>
          )}
        </>
      )}
      {redrawn && (
        <>
          <p className="muted">Redrawn from your picture, standing. A blank face or other clothes? Draw it again (about a minute).</p>
          <button
            disabled={busy}
            onClick={() =>
              run(() => props.chain(props.asset, redrawn.parent, [["canon", redrawn.params.look ? { look: redrawn.params.look } : {}], ["prep", {}]]))
            }
          >
            Redraw again
          </button>
        </>
      )}
      {big && (
        <p className="note warn">
          {why(big)}
          {names.some((n) => !tooBig(n)) ? " Another generator may fit: Advanced." : ""}
        </p>
      )}
      <details>
        <summary>Advanced</summary>
        <div className="stack">
          <fieldset>
            <legend>Generator</legend>
            {names.map((n) => {
              const big = tooBig(n);
              return (
                <label key={n} className="choice">
                  <input type="radio" name="generator" checked={chosen === n} disabled={!!big} onChange={() => setPreset(n)} />
                  <span>{GENERATORS[n]?.[0] ?? n}</span>
                  <span className="muted">{big ? why(big) : GENERATORS[n]?.[1]}</span>
                </label>
              );
            })}
          </fieldset>
          <label>
            Background behind your picture{" "}
            <select aria-label="Background behind your picture" value={background} onChange={(e) => setBackground(e.target.value)}>
              <option value="auto">Automatic</option>
              <option value="grey">Grey: for dark subjects (black fur, dark armour)</option>
              <option value="black">Black</option>
              <option value="white">White</option>
            </select>
          </label>
          <p className="muted">
            The generator sees your cut-out on a plain background. On black, very dark parts can look like background and come out missing, such as a black
            cat's back. Grey avoids that. Automatic uses grey for Fast when the subject is dark.
          </p>
          <label>
            Seed (empty = random)
            <input type="number" step={1} value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          {!seedOk && <p className="error">The seed is a whole number.</p>}
          <span className="muted">
            Preset:{" "}
            {Object.entries(props.presets[chosen] ?? {})
              .map(([k, x]) => `${k} ${x}`)
              .join(" · ")}
          </span>
        </div>
      </details>
    </section>
  );
}

// the rest of the game-ready recipe after `from` ("" = all of it), with friendly, validated fields
function PrepareForm(props: { asset: string; version: Meta; versions: Meta[]; chain: Chain; from: string; button: string; only?: string }) {
  const [faces, setFaces] = useState("10000");
  const [quads, setQuads] = useState(false); // M18: QuadriFlow; the budget then counts quads (2 triangles each)
  const [size, setSize] = useState("2048");
  const [height, setHeight] = useState(""); // "" = keep the current size
  const [straighten, setStraighten] = useState(false); // clean: also square it to the axes, long side onto X
  const [upright, setUpright] = useState(() => generated(props.versions, props.version)); // clean: level a generated model
  const [press, setPress] = useState(false); // clean: what hangs just under the base pressed onto it
  const [close, setClose] = useState(""); // remesh: slits up to this many mm shut first; "" = off
  const [detaper, setDetaper] = useState(false); // bake: a flat prop's perspective taper undone
  const [busy, run] = useBusy();
  const v = props.version;
  const todo = (props.only ? RECIPE.filter(([s]) => s === props.only) : RECIPE.slice(RECIPE.findIndex(([s]) => s === props.from) + 1)).filter(
    ([s]) => s !== "bake" || bakeable(props.versions, v),
  ); // `only`: just that one step, on this version
  const has = (s: string) => todo.some(([t]) => t === s);
  const prior = budgetOf(ancestors(props.versions, v));
  const n = Number(faces);
  const h = height.trim() === "" ? null : Number(height);
  const mm = close.trim() === "" ? null : Number(close);
  const problems = [
    has("remesh") && !(Number.isInteger(n) && n >= 100 && n <= 2_000_000) && `${quads ? "Quad" : "Triangle"} budget: a whole number from 100 to 2,000,000.`,
    has("remesh") && mm !== null && !(Number.isFinite(mm) && mm > 0) && "Close slits: a positive number of millimetres, or empty for auto.",
    has("clean") && h !== null && !(Number.isFinite(h) && h > 0) && "Height: a positive number of metres, or empty to keep the size.",
  ].filter(Boolean) as string[];
  const params: Record<string, object> = {
    clean: { ...(h ? { height_m: h } : {}), level: upright, ...(straighten ? { straighten: true } : {}), ...(press ? { press_to_floor: true } : {}) },
    remesh: { faces: n, ...(quads ? { topology: "quad" } : {}), ...(mm ? { close: mm / 1000 } : {}) },
    bake: { texture_size: Number(size), ...(detaper ? { detaper: true } : {}) },
    validate: has("remesh") ? { max_triangles: triBudget(n, quads && "quad") } : prior ? { max_triangles: prior } : {},
  };
  const advanced = [
    has("remesh") && (
      <label key="topology">
        Topology
        <select value={quads ? "quad" : "tri"} onChange={(e) => setQuads(e.target.value === "quad")}>
          <option value="tri">Triangles (game-ready, smallest)</option>
          <option value="quad">Quads (clean loops for rigging and sculpting)</option>
        </select>
      </label>
    ),
    has("remesh") && (
      <label key="close">
        Close slits up to (mm)
        <input type="number" min={1} step={1} placeholder="auto" value={close} onChange={(e) => setClose(e.target.value)} />
        <small className="muted">
          Thin boards with hairline slits between them tear into holes when simplified: 12 shuts them first. Grooves narrower than this smooth out; the
          bake's normal map keeps them. Empty: a generated prop that comes back folded through itself gets its slits shut by itself.
        </small>
      </label>
    ),
    has("bake") && (
      <div key="detaper">
        <label className="check">
          <input type="checkbox" checked={detaper} onChange={(e) => setDetaper(e.target.checked)} /> Square a flat prop's perspective taper
        </label>
        <small className="muted">
          For a fence, wall or sign drawn in perspective: one end comes out shorter. After the texture is read, the short end is stretched to the tall end's
          height. Level and square it in Clean first.
        </small>
      </div>
    ),
    has("clean") && (
      <div key="straighten">
        <label className="check">
          <input type="checkbox" checked={upright || straighten} disabled={straighten} onChange={(e) => setUpright(e.target.checked)} /> Stand it upright on the
          ground
        </label>
        <small className="muted">
          A generated model keeps the picture's camera, often looking down 10–15°, so it leans: its floor is levelled (a post stands on its shaft). On by
          default for a generated model; off keeps it as it came.
        </small>
        <label className="check">
          <input type="checkbox" checked={straighten} onChange={(e) => setStraighten(e.target.checked)} /> Also turn it square to the axes
        </label>
        <small className="muted">
          A prop from a three-quarter picture comes out turned 30–45°: its long side turned onto X, for a wall or a fence that lines up with a grid. A thin
          panel is only turned.
        </small>
        <label className="check">
          <input type="checkbox" checked={press} onChange={(e) => setPress(e.target.checked)} /> Press what hangs just under its base onto it
        </label>
        <small className="muted">
          A detail from the top can come back mirrored underneath (a lifting eye under a concrete block) and hold the model up off the ground: a small part
          under the middle of a broad flat base, up to a tenth of its height, is pressed flat onto it.
        </small>
      </div>
    ),
  ].filter(Boolean);
  return (
    <div className="stack">
      {has("remesh") && (
        <label>
          {quads ? "Quad" : "Triangle"} budget
          <input type="number" min={100} max={2000000} step={1000} value={faces} onChange={(e) => setFaces(e.target.value)} />
        </label>
      )}
      {has("bake") && (
        <label>
          Texture size
          <select value={size} onChange={(e) => setSize(e.target.value)}>
            <option value="1024">1K (1024 px)</option>
            <option value="2048">2K (2048 px)</option>
          </select>
        </label>
      )}
      {has("clean") && (
        <label>
          Height in metres
          <input type="number" min={0} step={0.1} placeholder="keep the current size" value={height} onChange={(e) => setHeight(e.target.value)} />
        </label>
      )}
      {/* one step run again shows all of its settings; the recipe keeps the specialist ones under Advanced (UX-15) */}
      {advanced.length > 0 &&
        (props.only ? (
          advanced
        ) : (
          <details>
            <summary>Advanced</summary>
            <div className="stack">{advanced}</div>
          </details>
        ))}
      {!props.only && <p className="muted">{todo.map(([, name]) => name).join(" → ")}</p>}
      {problems.map((p) => (
        <p key={p} className="error">
          {p}
        </p>
      ))}
      <button
        className="primary"
        disabled={busy || problems.length > 0 || !todo.length}
        onClick={() =>
          run(() =>
            props.chain(
              props.asset,
              v.version,
              todo.map(([s]): [string, object] => [s, params[s] ?? {}]),
            ),
          )
        }
      >
        {props.button}
      </button>
    </div>
  );
}

function ExportPanel(props: {
  asset: string;
  mesh: Meta | null;
  report?: Meta;
  chain: Chain;
  title?: string;
  primary?: boolean;
  profiles?: typeof EXPORTS; // M53: a sprite sheet's 2D destinations
}) {
  const profiles = props.profiles ?? EXPORTS;
  const [dest, setDest] = useState(profiles[0].id);
  const [lods, setLods] = useState(true); // the server defaults
  // a rigged character moves, a hull stays in the rest pose: off by default there, the user decides (M32)
  const rigged = !!props.mesh && ["rig_skin", "anim", "anim_edit"].includes(actsAs(props.mesh));
  const [collision, setCollision] = useState(!rigged);
  const [busy, run] = useBusy();
  const d = profiles.find((e) => e.id === dest)!;
  const fails = Number(props.report?.metrics?.fail ?? 0);
  const params = {
    profile: dest,
    ...(d.lods ? { lods } : {}),
    ...(d.collision ? { collision } : {}),
  };
  return (
    <section>
      <h3>{props.title ?? "Export"}</h3>
      <fieldset>
        <legend>Destination</legend>
        <div className="dests">
          {profiles.map((e) => (
            <label key={e.id} className="choice">
              <input type="radio" name="destination" checked={dest === e.id} onChange={() => setDest(e.id)} />
              <span>{e.name}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <p className="muted">{d.what}</p>
      {d.lods && (
        <label className="check">
          <input type="checkbox" checked={lods} onChange={(e) => setLods(e.target.checked)} /> LODs
        </label>
      )}
      {d.collision && (
        <label className="check">
          <input type="checkbox" checked={collision} onChange={(e) => setCollision(e.target.checked)} /> Collision hull
        </label>
      )}
      {d.collision && rigged && (
        <p className="muted">
          Off for a rigged character: the hull would stay frozen in the rest pose while the character moves. The game engine gives a moving character its own
          collision (Unreal's physics asset, capsules on the bones in Godot). Tick it if you want the hull anyway; Unreal imports it as a separate static
          mesh beside the character.
        </p>
      )}
      <p className="muted">
        {props.mesh && `This exports "${label(props.mesh)}" (${props.mesh.version.slice(0, 3)}). `}
        {!props.report ? "It has not been checked yet." : fails ? `${fails} check${fails > 1 ? "s" : ""} failed, so the export is not certified ready.` : ""}
        {props.mesh?.restricted && " This model is restricted, so it cannot be exported."}
      </p>
      <button
        className={props.primary ? "primary" : ""}
        disabled={busy || !props.mesh || props.mesh.restricted}
        onClick={() => run(() => props.chain(props.asset, props.mesh!.version, [["export", params]]))}
      >
        {fails ? "Export anyway" : "Export"} for {d.name}
      </button>
    </section>
  );
}

// M53: a sprite sheet's Finish, as a 3D model's: check it, then export it for a 2D engine
function Finish2D(props: { asset: string; version: Meta; versions: Meta[]; chain: Chain; primary?: boolean }) {
  const v = props.version;
  const [busy, run] = useBusy();
  const report = props.versions.filter((x) => x.step === "validate" && x.parent === v.version).at(-1);
  const tile = !!v.outputs.tile; // M54
  const layered = !!v.outputs.layers; // M55
  return (
    <>
      <section id="finish">
        <h3>{layered ? "Check the layers" : tile ? "Check the tile" : "Check the sprites"}</h3>
        <p className="muted">
          {layered
            ? "That there are at least two, and that the farthest covers the whole picture (nothing to see through when the others scroll)."
            : tile
            ? "Whether its edges meet where it repeats" + (v.outputs.sheet ? ", and its colours and pixels" : "") + ", before a game paints with it."
            : "Cell sizes, half-transparent pixels, the colours, stray pixels and the animations, before a game uses them."}
        </p>
        <button disabled={busy} onClick={() => run(() => props.chain(props.asset, v.version, [["validate", {}]]))}>
          {layered ? "Check the layers" : tile ? "Check the tile" : "Check sprites"}
        </button>
      </section>
      <ExportPanel key={`export-${v.version}`} asset={props.asset} mesh={v} report={report} chain={props.chain} profiles={EXPORTS_2D} primary={v.step === "pixelize" && props.primary !== false} />
    </>
  );
}

function Candidates(props: { asset: string; version: Meta; chain: Chain }) {
  const [scores, setScores] = useState<Record<string, any> | null | undefined>(undefined); // null = unavailable
  const [attempt, setAttempt] = useState(0);
  const [chosen, setChosen] = useState<string | null>(null);
  const [busy, run] = useBusy();
  const v = props.version;
  useEffect(() => {
    setScores(undefined);
    fetch(fileUrl(props.asset, v.version, v.outputs.scores))
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setScores)
      .catch(() => setScores(null));
  }, [props.asset, v.version, attempt]);
  const score = (k: string) => scores?.[v.outputs[k]];
  // best rank first; the cand_<k> key (not the position) is what prep's pick gets
  const cands = Object.keys(v.outputs)
    .filter((k) => k.startsWith("cand_"))
    .sort((a, b) => (score(a)?.rank ?? 99) - (score(b)?.rank ?? 99) || a.localeCompare(b, undefined, { numeric: true }));
  const fails = (k: string) =>
    Object.entries(score(k)?.gates ?? {})
      .filter(([, g]: any) => g.hard && !g.ok)
      .map(([n]) => GATES[n] ?? n);
  return (
    <div className="grid" role="group" aria-label="Concept images">
      {cands.map((k) => {
        const s = score(k);
        const f = fails(k);
        return (
          <button key={k} className="cand" aria-pressed={k === chosen} onClick={() => setChosen(k)}>
            <img src={fileUrl(props.asset, v.version, v.outputs[k])} alt={`concept ${k.slice(5)}`} />
            <span className="caption">
              {s ? (
                <span className={f.length ? "tag" : "tag ok"}>
                  #{s.rank} {f.length ? `needs: ${f.join(", ")}` : "reference checks passed"}
                </span>
              ) : (
                <span className="muted">concept {k.slice(5)}</span>
              )}
              {k === chosen && <strong>selected</strong>}
            </span>
          </button>
        );
      })}
      <div className="row">
        <button
          className="primary"
          disabled={busy || !chosen}
          onClick={() => run(() => props.chain(props.asset, v.version, [["prep", { pick: Number(chosen!.slice(5)) }]]))}
        >
          {chosen && fails(chosen).length ? "Use this image anyway" : "Use this image"}
        </button>
        <button disabled={busy} onClick={() => run(() => props.chain(props.asset, v.parent, [["concepts", {}]]))}>
          Generate 4 more
        </button>
        {scores === null && (
          <span className="muted">
            Checks unavailable
            <button className="small" onClick={() => setAttempt((a) => a + 1)}>
              Retry
            </button>
          </span>
        )}
      </div>
    </div>
  );
}

const STATUS: Record<string, string> = {
  pass: "Passed",
  warn: "Warning",
  fail: "Failed",
};
// where a failed/warned check can be fixed (no automatic repair)
const FIXES: Record<string, string> = {
  hard_alpha: "make pixel art from it: its transparency is cut to on or off",
  palette: "make pixel art from it with fewer colours",
  stray_pixels: "make pixel art with an outline, or a larger pixel size",
  seamless: "make another tile (a plainer material mends better), or pixelize it: larger pixels hide a faint seam",
  far_layer_whole: "make the layers again: the farthest should cover the picture",
  has_uvs: "unwrap it (Prepare)",
  uvs_in_0_1: "unwrap it (Prepare)",
  uv_overlap: "unwrap it again (Prepare): islands over each other paint through each other in Bake and Texture detail",
  triangle_budget: "simplify it (Prepare)",
  scale_metres: "set its height (Prepare)",
  manifold: "clean it, or fix it in Blender",
  watertight: "clean it, or fix it in Blender",
  single_part: "fine if the model is built from separate pieces (a robot's head, armour plates); else join them in Blender",
  no_tunnels: "fine for a handle or a ring; else prepare it again (Simplify fills a hollow, holey model) or fix it in Blender",
  no_degenerate_faces: "clean it, or fix it in Blender",
  self_intersection: "the surface passes through itself there: fix it in Blender, or prepare it again (Simplify rebuilds the surface)",
  parts_apart: "an inner shell, or a piece sunk into another: fine if meant; else delete it in Blender, or Parts to cut it",
  size_drift_pct: "fine if the brief's size was only a guide; else set its height in Prepare, or fit it with Transform's scale per axis (Advanced: run any step)",
};
const fmt = (x: unknown) =>
  typeof x === "boolean" ? (x ? "yes" : "no") : typeof x === "number" ? (Number.isInteger(x) ? x.toLocaleString() : x.toFixed(2)) : String(x);

function Checklist(props: { asset: string; version: Meta }) {
  const [items, setItems] = useState<Record<string, { status: string; value: unknown }> | null | undefined>(undefined);
  const v = props.version;
  useEffect(() => {
    setItems(undefined);
    fetch(fileUrl(props.asset, v.version, v.outputs.checklist ?? "checklist.json"))
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => setItems(d?.items && typeof d.items === "object" ? d.items : null))
      .catch(() => setItems(null));
  }, [props.asset, v.version]);
  if (items === undefined) return <section className="muted">Loading the checks…</section>;
  if (items === null) return <section className="note warn">Report unavailable: the checks can't be read, so nothing is known to pass.</section>;
  const list = Object.entries(items);
  const n = (s: string) => list.filter(([, i]) => i.status === s).length;
  const unknown = list.length - n("pass") - n("warn") - n("fail");
  const plural = (k: number, word: string) => `${k} ${word}${k === 1 ? "" : "s"}`;
  return (
    <section className="report" id="checklist">
      <h3>
        {[
          `${plural(n("pass"), "check")} passed`,
          n("warn") && plural(n("warn"), "warning"),
          n("fail") && `${n("fail")} failed`,
          unknown && `${unknown} not checked`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </h3>
      <details open={list.length > n("pass")}>
        <summary>Details</summary>
        <ul>
          {list.map(([name, i]) => (
            <li key={name} className={i.status}>
              <b>{STATUS[i.status] ?? "Not checked"}</b> {CHECKS[name] ?? name}: {i.value == null ? "–" : fmt(i.value)}
              {(i.status === "warn" || i.status === "fail") && FIXES[name] && <span className="muted"> · {FIXES[name]}</span>}
            </li>
          ))}
        </ul>
      </details>
      <span className="muted">
        For {v.parent?.slice(0, 3) ?? "?"} with a {fmt(Number(v.params.max_triangles ?? 100000))}-triangle budget; a later edit needs a new check. Not checked:
        rigging, animation, print wall thickness.
      </span>
    </section>
  );
}

// what each step does, for "Advanced: run any step" (the steps' own docstrings are the full reference)
type StepHelp = {
  title: string;
  what: string;
  needs: string;
  params: [string, string][];
  example: object;
};
const STEP_HELP: Record<string, StepHelp> = {
  brief: {
    title: "Brief: describe the asset",
    what: "Turns a short description into the prompt the concept images are made from.",
    needs: "Nothing (start of an asset).",
    params: [
      ["prompt", 'what to make, e.g. "dwarf blacksmith"'],
      ["category", "prop, character or creature: adds framing for a clean 3D reference picture"],
    ],
    example: { prompt: "a worn brass lantern", category: "prop" },
  },
  concepts: {
    title: "Concepts: 4 candidate pictures",
    what: "Generates candidate pictures from a brief, checks them and ranks them.",
    needs: "A brief version.",
    params: [
      ["n", "how many pictures (default 4)"],
      ["seed", "a number: the same seed gives the same pictures"],
    ],
    example: { n: 4 },
  },
  prep: {
    title: "Prep: cut out the picture",
    what: 'Removes the background and frames the subject for 3D. With op "views" it splits a turnaround sheet into views for multi-view 3D.',
    needs: "An upload or concepts version.",
    params: [
      ["pick", "concepts only: which candidate (0-3)"],
      ["op", '"views" for a turnaround sheet'],
      ["views", 'views only: the order left to right, e.g. ["front", "right", "back", "left"]'],
    ],
    example: { pick: 0 },
  },
  transform: {
    title: "Transform: stand up, turn, scale, place",
    what: "Moves a still model without Blender: stands it upright, turns it, scales it, puts its base on the ground. Textures stay as they are.",
    needs: "A version with a still model (not a rigged one).",
    params: [
      ["level", "true: stand it upright (its floor levelled, a post on its shaft, a box on its own axes)"],
      ["square", "true: turned square to the axes by the smallest turn, its front (the camera's side) facing +Z"],
      ["face", '"+z", "-z", "+x" or "-x": its front turned to face that axis ("-z": the street side)'],
      ["rotate_deg", "[x, y, z] degrees, turned about X, then Y, then Z"],
      ["scale", "a number, or [x, y, z] per axis after the turn (or height_m)"],
      ["height_m", "a number: scale it to this height in metres"],
      ["ground", "true (default): its base centre onto the origin"],
      ["translate_m", "[x, y, z] metres: then moved by this"],
    ],
    example: { level: true, square: true, face: "-z", height_m: 1.0 },
  },
  canon: {
    title: "A-pose: re-pose a character",
    what: "Redraws a character in an A-pose (arms down at ~45°), which rigging needs. Run prep on the result again.",
    needs: "A prep version of a character.",
    params: [
      ["seed", "a number: try another for a different result"],
      ["look", '"render": also redraw a flat picture (pixel art, a drawing) as a shaded 3D figure, or its model comes out flat'],
    ],
    example: {},
  },
  trellis2: {
    title: "3D model: generate or retexture",
    what: "Makes a textured 3D model from a cut-out picture (op generate), or new textures for an existing UV-mapped mesh (op texture). Pick a preset to start.",
    needs: "generate: a prep version. texture: a version with a UV-mapped mesh (e.g. pulled from Blender).",
    params: [
      ["weights", "faithful (closer to the picture, default) or fast"],
      ["steps", "sampling steps, 1-50 (12 is standard)"],
      ["seed", "a number: the same seed gives the same model"],
      ["texture_size", "texture resolution: 1024, 2048 or 4096"],
      ["multiview", "faithful with a turnaround: false uses the front view only"],
    ],
    example: { op: "generate", weights: "faithful", steps: 12 },
  },
  clean: {
    title: "Clean: repair the mesh",
    what: "Welds seams, fills small holes, removes floating bits and can rescale to a real height.",
    needs: "A version with a mesh.",
    params: [
      ["height_m", "scale so the model is this many metres tall (empty: keep)"],
      ["hole", "largest hole to fill, as a fraction of the model's size (default 0.03)"],
      ["level", 'true, false or "auto" (default: true for a generated model): stand it upright, its floor levelled or a post on its shaft'],
      ["straighten", "true: also turn the long side onto X (a prop from a three-quarter picture); a thin panel is only turned"],
      ["press_to_floor", "true: a small part hanging just under a broad flat base (a mirrored lifting eye) pressed flat onto it"],
      ["drop_floating", 'true, false or "auto" (default: true for a generated model): small parts floating off the outside dropped'],
    ],
    example: { height_m: 1.8 },
  },
  remesh: {
    title: "Remesh: fewer polygons or quads",
    what: "Reduces the mesh to a triangle budget, or rebuilds it as clean quads (for rigging, sculpting).",
    needs: "A version with a mesh.",
    params: [
      ["faces", "the budget: triangles, or quads with topology quad"],
      ["topology", "tri (default) or quad"],
      ["symmetry", 'quad only: "x" mirrors left and right (not for one-handed props)'],
      ["fill", "true (default): a hollow model with holes is filled through them first; false for one meant to be open inside"],
      ["close", "metres: slits up to this wide shut before simplifying (thin boards: 0.012); at least the model's length / 200; unset, a generated prop folded through itself gets length / 150, 0 never"],
    ],
    example: { faces: 8000, topology: "quad" },
  },
  motion: {
    title: "Motion from text",
    what: "Makes an animation from a sentence (NVIDIA Kimodo). Add it to a skinned model with the anim step.",
    needs: "Any version of the asset; it is not used.",
    params: [
      ["prompt", "the sentence describing the movement (required)"],
      ["seconds", "length, 0.5 to 10 (default 5)"],
      ["seed", "a whole number (default 0)"],
      ["steps", "denoising steps (default 100)"],
    ],
    example: { prompt: "A person waves with the right hand.", seconds: 5, seed: 0 },
  },
  import_motion: {
    title: "Animation from a file",
    what: "Brings in an animation made elsewhere as one of your motions. Add it to a skinned model with the anim step.",
    needs: "An upload of an FBX, GLB, glTF or BVH file on a Mixamo, Unreal (Manny, Quinn) or Kimodo skeleton.",
    params: [
      ["clip", "which of the file's animations (default the first); the version lists them all"],
      ["name", "the clip's name (default the animation's own, or the file's)"],
      ["for", "the skinned version it is meant for"],
    ],
    example: {},
  },
  anim: {
    title: "Rig: add animations",
    what: "Retargets clips from the bundled pack (Mesh2Motion, CC0) onto the skeleton; the result plays in the viewer and exports with the model.",
    needs: "A Skinned version (Character: Attach to skeleton).",
    params: [
      ["clips", 'clip names from the pack, e.g. ["Idle_A", "Walk", "Jog"], or the version names of your motions, e.g. "310_motion" (the Character panel lists them)'],
      ["clearance", "true (default): on a person, the arms opened where a clip would swing the forearms or hands through the body; false keeps the clips as retargeted"],
    ],
    example: { clips: ["Idle_A", "Walk", "Jog"] },
  },
  rig_skin: {
    title: "Rig: skin weights",
    what: "Computes how strongly each bone moves each part of the model, then checks it in 10 test poses (qa.json).",
    needs: "A Skeleton version (Character: Fit a skeleton).",
    params: [
      ["engine", "auto (default: both, the checks pick), blender (bone heat, always available) or skintokens (learned; needs the skin weights)"],
      ["voxels", "blender: detail of the helper shape the weights are computed on (default 120)"],
    ],
    example: {},
  },
  rig_fit: {
    title: "Rig: fit a skeleton",
    what: "Fits a 52-joint humanoid skeleton (Mixamo names) from a front render: pose detection finds the joints, they're placed inside the body.",
    needs: "A version with a character mesh, standing upright (best in an A-pose).",
    params: [["markers", "positions for the 13 markers, to rebuild from them (the Character panel's editor writes these)"]],
    example: {},
  },
  uv: {
    title: "UV unwrap",
    what: "Lays the mesh out flat so textures can be painted onto it.",
    needs: "A version with a mesh (usually after remesh).",
    params: [["cone_deg", "how much a chart may curve before it's cut (default 90)"]],
    example: {},
  },
  bake: {
    title: "Bake textures",
    what: "Transfers colour, roughness, metal and surface detail from the generated model onto this mesh's UVs.",
    needs: "A UV-mapped version descended from a 3D-model version.",
    params: [
      ["texture_size", "1024, 2048 or 4096 (4096 bakes without supersampling)"],
      ["normal_map", "true: also bake surface detail as a normal map"],
      ["detaper", "true: a flat prop's perspective taper undone after the texture (straighten it in clean first)"],
    ],
    example: { texture_size: 2048 },
  },
  refine: {
    title: "Texture detail",
    what: "Redraws the texture's fine detail from close-up views with the Tile model and paints it back onto the same UVs; mesh, colours and maps stay.",
    needs: "A textured, UV-mapped version (bake, project or refine).",
    params: [
      ["strength", "0.3 to 1 (default 1: drawn fresh from the view)"],
      ["face_strength", "for views that see a projected face (default 0.5)"],
      ["prompt", "what the asset is (default: its description)"],
      ["zoom", "auto (1.25 view pixels per texel) or a number"],
      ["texture_size", "keep or 4096"],
      ["delight", "0 to 1: the baked shadows taken out of the colours first, using the bake's ambient occlusion (default 0)"],
      ["views", "auto, or [] for no redraw (only delight; no model, seconds)"],
      ["coverage", "0 to 8: the most views added where the auto views see too little (default 3, 0 off)"],
      ["op", "edit: only a painted part redrawn as the prompt says (takes region, prompt, strength, seed); the panel's Paint the part to change makes the region"],
      ["region", "with op edit: the version holding the painted mask in the texture's layout (white = redrawn, black = kept)"],
    ],
    example: { strength: 1 },
  },
  project: {
    title: "Face from image",
    what: "Projects the generated picture onto the texture where the camera sees the surface, so the face is as sharp as the picture.",
    needs: "A textured version descended from a Faithful 3D-model version.",
    params: [
      ["region", "head (default) or front: the whole front"],
      ["strength", "0.1 to 1 (default 1)"],
    ],
    example: { region: "head", strength: 1 },
  },
  validate: {
    title: "Validate: game-readiness check",
    what: "Checks the mesh (holes, scale, UVs, triangle budget) and lists each item as pass, warn or fail.",
    needs: "A version with a mesh.",
    params: [["max_triangles", "the budget to check against"]],
    example: { max_triangles: 20000 },
  },
  export: {
    title: "Export for an engine",
    what: "Writes the files an engine imports (with LODs, collision, credits and licences).",
    needs: "A textured, UV-mapped version.",
    params: [
      ["profile", "unity, unreal, godot, web, print or obj"],
      ["lods", "true: lower-detail copies for distance"],
      ["collision", "true: a simple collision shape"],
    ],
    example: { profile: "godot" },
  },
};

function RunStep(props: { steps: Presets; parent: string | null; onRun: (step: string, params: string) => void }) {
  const [step, setStep] = useState("");
  const [other, setOther] = useState(false); // a step by name (development: test steps aren't listed)
  const [params, setParams] = useState("{}");
  const presets = props.steps[step] ?? {};
  const help = STEP_HELP[step];
  let bad = "";
  try {
    if (typeof JSON.parse(params) !== "object") bad = "Settings must be a { … } object.";
  } catch {
    bad = "Settings aren't valid JSON yet (keys and text in double quotes).";
  }
  const pick = (s: string) => {
    setOther(s === "…");
    setStep(s === "…" ? "" : s);
    setParams(JSON.stringify(STEP_HELP[s]?.example ?? {}, null, 1));
  };
  return (
    <details>
      <summary>Run a step on {props.parent ?? "(no parent)"}</summary>
      <div className="stack">
        <p className="muted">
          Use this when the normal buttons are not enough. Run one step on the model you are viewing, with your own settings. The result is a new version in
          History. Nothing is overwritten.
        </p>
        <select aria-label="step" value={other ? "…" : step} onChange={(e) => pick(e.target.value)}>
          <option value="">Choose a step…</option>
          {Object.keys(props.steps).map((s) => (
            <option key={s} value={s}>
              {STEP_HELP[s]?.title ?? s}
            </option>
          ))}
          <option value={"…"}>Other step (by name)…</option>
        </select>
        {other && <input placeholder="step name" aria-label="step name" value={step} onChange={(e) => setStep(e.target.value)} />}
        {help && (
          <div className="step-help">
            <p>{help.what}</p>
            <p className="muted">Needs: {help.needs}</p>
            {help.params.length > 0 && (
              <dl>
                {help.params.map(([k, d]) => (
                  <div key={k}>
                    <dt>
                      <code>{k}</code>
                    </dt>
                    <dd>{d}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        )}
        {Object.keys(presets).length > 0 && (
          <select aria-label="preset" onChange={(e) => setParams(JSON.stringify(presets[e.target.value] ?? {}, null, 1))}>
            <option>preset…</option>
            {Object.keys(presets).map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
        )}
        {step && (
          <label>
            Settings (JSON; leave {"{}"} for the defaults)
            <textarea rows={5} aria-label="params" value={params} onChange={(e) => setParams(e.target.value)} style={{ fontFamily: "monospace" }} />
          </label>
        )}
        {step && bad && <p className="error">{bad}</p>}
        <button disabled={!step || !!bad} onClick={() => props.onRun(step, params)}>
          Run {help?.title.split(":")[0] ?? step}
        </button>
      </div>
    </details>
  );
}

// --- M19 rig: the fitted skeleton, and the 13 markers the user can move -------------------------
type RigView = {
  joints: Joint[];
  markers: Record<string, Vec3>;
  selected: string | null;
  onMarker?: (name: string, p: Vec3 | null) => void;
  edit?: SkeletonEdit;
  outside?: string[]; // joints the inside check flags
  xray?: boolean; // the model semi-transparent (the editors)
  twin?: (name: string, p: Vec3) => [string, Vec3] | null; // marker editor: the mirrored marker's place
};
// the skeleton editor's state: only what the user changed (joint positions, twists), with undo/redo snapshots
type Snap = { joints: Record<string, Vec3>; twists: Record<string, Vec3> };
const JOINT_GROUPS: [string, string[]][] = [
  ["Torso", ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"]],
  ...["Left", "Right"].map((s): [string, string[]] => [`${s} arm`, ["Shoulder", "Arm", "ForeArm", "Hand"].map((p) => s + p)]),
  ...["Left", "Right"].map((s): [string, string[]] => [`${s} leg`, ["UpLeg", "Leg", "Foot", "ToeBase"].map((p) => s + p)]),
];
// a creature's joints by side: its middle line (spine, head, tail), then its left and its right (M36)
const creatureGroups = (joints: Joint[], sides: [string, string]): [string, string[]][] => {
  const names = joints.filter((j) => j.parent).map((j) => j.name);
  return [
    ["Middle (spine, head, tail)", names.filter((n) => !twinOf(n, sides))],
    ["Left side", names.filter((n) => n.endsWith(sides[0]))],
    ["Right side", names.filter((n) => n.endsWith(sides[1]))],
  ];
};
const omit = (o: Record<string, Vec3>, k: string): Record<string, Vec3> => Object.fromEntries(Object.entries(o).filter(([n]) => n !== k));
const twinOf = (n: string, sides?: [string, string]) => {
  if (sides) {
    for (const [a, b] of [sides, [sides[1], sides[0]]]) if (n.endsWith(a)) return n.slice(0, -a.length) + b;
    return null;
  }
  return n.startsWith("Left") ? "Right" + n.slice(4) : n.startsWith("Right") ? "Left" + n.slice(5) : null;
};
// M36: what a skeleton can be fitted as (steps/_templates.py TEMPLATES)
const RIG_KINDS: [string, string][] = [
  ["mixamo52", "A person or humanoid character"],
  ["fox", "A four-legged animal (dog, cat, fox, wolf)"],
  ["horse", "A hoofed animal (horse, deer, cow)"],
  ["bird", "A bird"],
  ["kaiju", "A two-legged monster with a tail (kaiju, dinosaur)"],
  ["shark", "A shark or fish with fins and a tail"],
  ["dragon", "A winged dragon (spread wings, four legs, a tail)"],
  ["spider", "A spider or other eight-legged creature"],
  ["snake", "A snake (no legs)"],
];
type RigReport = {
  joints: number;
  inside: number;
  outside: string[];
  low_confidence: string[];
};
// where each joint and marker belongs, for the tooltips and the "Where it sits" line
const CENTRE_PLACEMENT: Record<string, string> = {
  Hips: "The root. The middle of the pelvis at the height of the hip joints, halfway between front and back.",
  Spine: "The lower back, a hand's width above the hips, inside the body.",
  Spine1: "The middle of the back, at the bottom of the rib cage.",
  Spine2: "The upper back, at the chest, between the shoulder blades.",
  Neck: "The base of the neck, where it meets the shoulders, inside the neck.",
  Head: "Where the skull meets the neck, just below the ears, inside the head. The head bone runs up from here.",
};
const SIDE_PLACEMENT: Record<string, string> = {
  Shoulder: "The inner end of the collarbone, next to the base of the neck. The bone runs out to the arm joint.",
  Arm: "The shoulder ball, where the upper arm rotates: inside the shoulder, not on its surface.",
  ForeArm: "The elbow, inside the arm at the crease.",
  Hand: "The wrist, between the two wrist bones. The palm tick shows which way the palm faces.",
  UpLeg: "The hip joint, where the thigh rotates: inside the pelvis, below the hip bone.",
  Leg: "The knee, inside the leg, a little behind the kneecap.",
  Foot: "The ankle, inside the foot, between the ankle bones.",
  ToeBase: "The ball of the foot, where the toes bend.",
};
const FINGER_PLACEMENT = (f: string): string[] =>
  f === "Thumb"
    ? ["The base of the thumb, at the edge of the palm by the wrist.", "The thumb's middle knuckle.", "The thumb's last knuckle, before the tip."]
    : [
        `The knuckle where the ${f.toLowerCase()} finger meets the palm.`,
        `The ${f.toLowerCase()} finger's middle knuckle.`,
        `The ${f.toLowerCase()} finger's last knuckle, before the fingertip.`,
      ];
const PLACEMENT: Record<string, string> = {
  ...CENTRE_PLACEMENT,
  ...Object.fromEntries(
    ["Left", "Right"].flatMap((s) => [
      ...Object.entries(SIDE_PLACEMENT).map(([k, t]) => [s + k, t]),
      ...["Thumb", "Index", "Middle", "Ring", "Pinky"].flatMap((f) => FINGER_PLACEMENT(f).map((t, i) => [`${s}Hand${f}${i + 1}`, t])),
    ]),
  ),
};
const MARKER_PLACEMENT: Record<string, string> = {
  Chin: "The tip of the chin, on the surface.",
  Groin: "The crotch, where the legs meet.",
  ...Object.fromEntries(
    ["Left", "Right"].flatMap((s) =>
      Object.entries({
        Shoulder: "The shoulder joint, where the arm rotates: not the tip of a shoulder pad.",
        Elbow: "The elbow crease.",
        Wrist: "The middle of the wrist.",
        Knee: "The kneecap.",
        Ankle: "The ankle, between the ankle bones.",
      }).map(([k, t]) => [s + k, t]),
    ),
  ),
};
const MARKER_NAMES = ["Chin", "Groin", ...["Left", "Right"].flatMap((s) => ["Shoulder", "Elbow", "Wrist", "Knee", "Ankle"].map((p) => s + p))];
const markerLabel = (m: string) =>
  m
    .replace(/^(Left|Right)/, "$1 ")
    .toLowerCase()
    .replace(/^./, (c) => c.toUpperCase());

// rig_skin's qa.json: the §3.9 Rig checks, and what each means for the user
const SKIN_CHECKS: Record<string, [string, string]> = {
  unweighted: ["vertices no bone moves", "they'd stay behind when the character moves"],
  weight_sums: ["vertices whose weights don't add up to 1", "they'd drift when posed"],
  influences: ["most bones on one vertex", "game engines take 4"],
  lr_symmetry: ["left/right difference", "high is expected with a one-sided prop (a hammer in one hand)"],
  pose_volume: ["volume change in test poses (%)", "long robes and coats change a lot between the legs"],
  pose_stretch: ["largest stretch in test poses (×)", "high: a part follows the wrong bone; check it in the weights view"],
  joint_loops: ["joints with too few edge loops to bend", "the elbow or knee creases instead of bending: prepare again with more faces, or Topology: Quads"],
};

// the bundled clips (GET /api/clips) with idle/walk/run ticked: one anim version per choice
function ClipPicker(props: {
  asset: string;
  rig: string;
  template?: string; // M36: a creature's own clips; the motions from text or a video are for people
  onRun: (params: object, step?: string, parent?: string) => void;
  busy: boolean;
  motions: Meta[];
}) {
  const template = props.template ?? "mixamo52";
  const animal = template !== "mixamo52";
  const [clips, setClips] = useState<string[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set(animal ? ["Idle", "Walk"] : ["Idle_A", "Walk", "Jog"]));
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(5);
  const [seed, setSeed] = useState(0);
  const [picked, setPicked] = useState<"clips" | "text" | "video" | "file" | null>(null); // UX-05: the animation's source; one form shown
  const [clearance, setClearance] = useState(true); // M67: a person's arms kept out of the body
  useEffect(() => {
    api
      .clips(template)
      .then((c) => setClips([...new Set(c.map((x) => x.name))].sort()))
      .catch(() => setClips([]));
  }, [template]);
  if (clips === null) return <p className="muted">Loading the clip list…</p>;
  const toggle = (n: string) =>
    setChosen((s) => {
      const next = new Set(s);
      next.has(n) ? next.delete(n) : next.add(n);
      return next;
    });
  const option = (n: string, text = n) => (
    <option key={n} value={n} onMouseDown={(e) => (e.preventDefault(), toggle(n))}>
      {chosen.has(n) ? "✓ " : ""}
      {text}
    </option>
  );
  const motions = animal ? [] : props.motions;
  const from = animal ? "clips" : (picked ?? (clips.length || motions.length ? "clips" : "text")); // ready-made by default, when there are any
  return (
    <div className="stack">
      {!clips.length && !motions.length && (
        <p className="note warn">
          No ready-made animations found: <a href={setupAt("clips")}>install them on the Setup page</a>.
        </p>
      )}
      {animal ? (
        <p className="muted">Animations from a sentence or a video are for people. This animal gets its own ready-made ones below.</p>
      ) : (
        <fieldset>
          <legend>Animation source</legend>
          {(
            [
              ["clips", "Ready-made"],
              ["text", "From a sentence"],
              ["video", "From a video"],
              ["file", "From a file"],
            ] as const
          ).map(([k, name]) => (
            <label key={k} className="check">
              <input type="radio" name="anim-source" checked={from === k} onChange={() => setPicked(k)} /> {name}
            </label>
          ))}
        </fieldset>
      )}
      {from === "text" && (
        <>
          <p className="muted">
            Describe the movement, then click Generate motion. When it is ready, its page opens: click Put this motion on the model there to see your
            character do it.
          </p>
          <input
            type="text"
            aria-label="Motion prompt"
            placeholder="A person waves with the right hand."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          {/* a fighter's moves the ready-made pack lacks: a click fills the sentence */}
          <div className="row" role="group" aria-label="Fighting moves">
            {FIGHT_PROMPTS.map(([name, text]) => (
              <button key={name} className="small" aria-pressed={prompt === text} onClick={() => (setPrompt(text), setSeconds(2.5))}>
                {name}
              </button>
            ))}
          </div>
          <div className="row">
            <label>
              Seconds{" "}
              <input
                type="number"
                aria-label="Seconds"
                min={0.5}
                max={10}
                step={0.5}
                value={seconds}
                style={{ width: "5em" }}
                onChange={(e) => setSeconds(Math.min(10, Math.max(0.5, Number(e.target.value) || 5)))}
              />
            </label>
            <label>
              Seed{" "}
              <input
                type="number"
                aria-label="Seed"
                step={1}
                value={seed}
                style={{ width: "5em" }}
                onChange={(e) => setSeed(Math.trunc(Number(e.target.value)) || 0)}
              />
            </label>
            <button disabled={props.busy || !prompt.trim()} onClick={() => props.onRun({ prompt: prompt.trim(), seconds, seed }, "motion")}>
              Generate motion
            </button>
          </div>
        </>
      )}
      {from === "video" && <VideoMotion asset={props.asset} rig={props.rig} onRun={props.onRun} busy={props.busy} />}
      {from === "file" && <FileMotion asset={props.asset} rig={props.rig} onRun={props.onRun} busy={props.busy} />}
      {from === "clips" && (
        <>
          <p className="muted">
            Choose the animations you want from the {clips.length} ready-made ones{motions.length > 0 && " and your own motions"}, then click Add. Each one
            takes about 10 seconds. They play in the viewer and export with the model.
          </p>
          {!animal && (
            <label>
              Move set
              <select
                value=""
                onChange={(e) => {
                  const set = MOVE_SETS.find(([id]) => id === e.target.value);
                  if (set) setChosen(new Set(set[2].filter((n) => clips.includes(n))));
                }}
              >
                <option value="">Choose a set…</option>
                {MOVE_SETS.map(([id, name, names]) => (
                  <option key={id} value={id}>
                    {name}: {names.filter((n) => clips.includes(n)).length} animations
                  </option>
                ))}
              </select>
            </label>
          )}
          <select
            multiple
            size={8}
            aria-label="Clips"
            value={[...chosen]}
            onChange={(e) => setChosen(new Set([...e.target.selectedOptions].map((o) => o.value)))} // the keyboard's path (UI-006)
          >
            {clips.map((n) => option(n))}
            {motions.length > 0 && (
              <optgroup label="Your motions">
                {motions.map((m) =>
                  option(
                    m.version,
                    `${m.metrics?.prompt ?? (m.metrics?.name ? `${m.metrics.name} (${m.step === "mocap" ? "video" : "file"})` : m.version)} · ${m.metrics?.seconds ?? "?"} s`,
                  ),
                )}
              </optgroup>
            )}
          </select>
          {!animal && (
            <label title="Where a clip swings the forearms or hands through the body (a stout character), the arms open just enough">
              <input type="checkbox" checked={clearance} onChange={(e) => setClearance(e.target.checked)} /> Keep the arms out of the body
            </label>
          )}
          <button
            className="primary"
            disabled={props.busy || !chosen.size}
            onClick={() => props.onRun({ clips: [...chosen], ...(animal || clearance ? {} : { clearance: false }) }, "anim")}
          >
            Add {chosen.size} animation{chosen.size === 1 ? "" : "s"}
          </button>
        </>
      )}
    </div>
  );
}

// M34: a video of a person becomes a motion (GEM-X): upload, then the mocap step on the upload
const MOCAP_MODES: [string, string][] = [
  ["best", "Best: smooth and accurate (recommended)"],
  ["quick", "Quick: follows the video most closely, a little shakier"],
  ["keypoints", "Light: from the body points alone, may drift across the floor"],
];
function VideoMotion(props: { asset: string; rig: string; onRun: (params: object, step?: string, parent?: string) => void; busy: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState("best");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [camera, setCamera] = useState("still");
  const [footLock, setFootLock] = useState(true);
  const [inPlace, setInPlace] = useState(false);
  const [smooth, setSmooth] = useState(0.5);
  const [uploading, setUploading] = useState(false);
  const [uploaded, setUploaded] = useState<{ file: File; version: string } | null>(null); // a retry reuses it
  // a small preview of the chosen file, straight from the disk (nothing uploaded yet); its paused time sets the trim
  const [url, setUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const vid = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (!file) return setUrl(null);
    const u = URL.createObjectURL(file);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);
  const span = (end ? Number(end) : duration) - (start ? Number(start) : 0); // the part that will be used
  const here = () => (vid.current?.currentTime ?? 0).toFixed(1);
  const make = async () => {
    setUploading(true);
    try {
      const version = uploaded?.file === file ? uploaded.version : (await api.upload(props.asset, file!)).version;
      setUploaded({ file: file!, version });
      const up = { version };
      const params = {
        mode,
        camera,
        foot_lock: footLock,
        in_place: inPlace,
        smooth,
        for: props.rig, // the motion page puts it back onto this skinned model
        ...(start ? { start: Number(start) } : {}),
        ...(end ? { end: Number(end) } : {}),
      };
      props.onRun(params, "mocap", up.version);
    } finally {
      setUploading(false);
    }
  };
  return (
    <div className="stack">
      <h4>Make an animation from a video</h4>
      <p className="muted">
        Film one person from head to toe with the camera held still (or turning on the spot to follow: say so under Camera), then choose the video (MP4, MOV or
        WebM). A 15-second clip takes about two minutes. When the motion is ready, its page opens: click Put this motion on the model there to see your character
        do it. Several people in the video: the largest comes first, the motion's page offers the others.
      </p>
      <input
        type="file"
        aria-label="Video file"
        accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm"
        onChange={(e) => (setFile(e.target.files?.[0] ?? null), setDuration(0), setStart(""), setEnd(""))}
      />
      {url && (
        <div className="stack">
          <video
            ref={vid}
            src={url}
            aria-label="Chosen video"
            controls
            muted
            playsInline
            style={{ maxWidth: 240, maxHeight: 240, background: "#000" }}
            onLoadedMetadata={(e) => setDuration(e.currentTarget.duration || 0)}
          />
          <div className="row">
            <button onClick={() => setStart(here())}>Start here</button>
            <button onClick={() => setEnd(here())}>End here</button>
            {duration > 0 && <span className="muted">{duration.toFixed(1)} s long</span>}
          </div>
          {duration > 0 && span > 30 && (
            <p className="note warn">The part to use is {span.toFixed(1)} s: at most 30 s works. Pause where it should start or end and click Start here or End here.</p>
          )}
        </div>
      )}
      <label>
        Quality{" "}
        <select aria-label="Video motion quality" value={mode} onChange={(e) => setMode(e.target.value)}>
          {MOCAP_MODES.map(([k, t]) => (
            <option key={k} value={k}>
              {t}
            </option>
          ))}
        </select>
      </label>
      <label>
        Camera{" "}
        <select aria-label="Camera" value={camera} onChange={(e) => setCamera(e.target.value)}>
          <option value="still">held still</option>
          <option value="panning">turning on the spot to follow (panning)</option>
        </select>
      </label>
      <div className="row">
        <label>
          From{" "}
          <input type="number" aria-label="Start second" min={0} step={0.5} placeholder="0" value={start} style={{ width: "5em" }} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          to{" "}
          <input type="number" aria-label="End second" min={0} step={0.5} placeholder="end" value={end} style={{ width: "5em" }} onChange={(e) => setEnd(e.target.value)} /> s
        </label>
      </div>
      <div className="row">
        <label className="check">
          <input type="checkbox" checked={footLock} onChange={(e) => setFootLock(e.target.checked)} /> Keep planted feet still
        </label>
        <label className="check">
          <input type="checkbox" checked={inPlace} onChange={(e) => setInPlace(e.target.checked)} /> In place (no travel)
        </label>
        <label>
          Smoothing{" "}
          <input type="range" aria-label="Smoothing" min={0} max={1} step={0.25} value={smooth} onChange={(e) => setSmooth(Number(e.target.value))} />
        </label>
      </div>
      <button disabled={props.busy || uploading || !file} onClick={make}>
        {uploading ? "Uploading…" : "Make motion from video"}
      </button>
    </div>
  );
}

// a motion made from a sentence (M26) or a video (M34): what it came from, and what to do with it
// an animation made elsewhere: uploaded to this asset, then import_motion on the upload (owner 2026-10-12)
function FileMotion(props: { asset: string; rig: string; onRun: (params: object, step?: string, parent?: string) => void; busy: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState("");
  const [inputKey, setInputKey] = useState(0);
  const [busy, run] = useBusy();
  const go = () =>
    run(async () => {
      try {
        const up = await api.upload(props.asset, file!);
        setFile(null);
        setInputKey((k) => k + 1);
        props.onRun({ for: props.rig }, "import_motion", up.version);
      } catch (e) {
        setErr(explain(e));
      }
    });
  return (
    <>
      <h4>Use an animation from a file</h4>
      <p className="muted">
        An animation made elsewhere, as FBX, GLB or glTF, or BVH, on a Mixamo, Unreal (Manny, Quinn) or Kimodo skeleton. It comes in as one of your motions:
        its page puts it on the model.
      </p>
      <input
        key={inputKey}
        type="file"
        aria-label="Animation file"
        accept={ANIM_TYPES.join(",")}
        aria-invalid={!!err}
        aria-describedby={err ? "file-motion-error" : undefined}
        onChange={(e) => (setErr(""), setFile(e.target.files?.[0] ?? null))}
      />
      {err && (
        <p className="error" role="alert" id="file-motion-error">
          {err}
        </p>
      )}
      <button disabled={props.busy || busy || !file} onClick={go}>
        Import animation
      </button>
    </>
  );
}

const SKELETON_KINDS: Record<string, string> = { mixamo: "Mixamo", ue5: "Unreal (Manny, Quinn)", soma: "Kimodo (SOMA)" };

function MotionInfo(props: { asset: string; version: Meta; versions: Meta[]; chain: Chain }) {
  const [busy, run] = useBusy();
  const v = props.version;
  const m = v.metrics ?? {};
  const [other, setOther] = useState(""); // a file's other animation, to bring in too
  // M65: a transition was made from an animated version's clips: it goes back on with them (the version's own clips, or
  // its base animation's when hand edits sit on top), onto the skinned model under it
  const connect = v.step === "motion" && !!(v.params?.from || v.params?.to);
  const byId = (id: string | null | undefined) => props.versions.find((x) => x.version === id);
  const source = connect ? byId(v.parent) : undefined;
  const below = (x: Meta | undefined, step: string): Meta | undefined => (!x || actsAs(x) === step ? x : below(byId(x.parent), step));
  const base = below(source, "anim");
  const keep = base?.step === "anim" ? ((base.params?.clips as string[] | undefined) ?? []).filter((c) => c !== v.version) : [];
  // the skinned model it was made for: recorded with a video motion, the parent of a text motion, else the newest
  const made = String(connect ? (below(source, "rig_skin")?.version ?? "") : (v.params?.for ?? (v.step === "motion" ? v.parent : "") ?? ""));
  const skinned = props.versions.filter((x) => actsAs(x) === "rig_skin");
  const target = skinned.find((x) => x.version === made) ?? skinned.at(-1) ?? null;
  const putOn = target ? (
    <>
      <p className="muted">
        A motion is made on its own, apart from your character. To see your character do it, click Put this motion on the model: it goes onto "
        {label(target)}" ({target.version.slice(0, 3)}) as a new version, which opens and plays in the viewer. Later you can add more motions or ready-made
        clips there, under Character.
      </p>
      {connect && source && (source !== base || !keep.length) && (
        <p className="muted">
          {keep.length
            ? `The hand edits of "${label(source)}" stay there: the new version has the clips of "${label(base!)}" and this transition.`
            : "The new version has only this transition: add the clips it connects there, under Character."}
        </p>
      )}
      <button
        className="primary"
        disabled={busy}
        onClick={() => run(() => props.chain(props.asset, target.version, [["anim", { clips: [...keep, v.version] }]]))}
      >
        Put this motion on the model{keep.length ? " with its clips" : ""}
      </button>
    </>
  ) : (
    <p className="note warn">Attach a model to a skeleton first (Character: Attach to skeleton). Then come back here and put this motion on it.</p>
  );
  if (v.step === "import_motion") {
    const rest = ((m.clips as string[] | undefined) ?? []).filter((c) => c !== m.picked);
    return (
      <section>
        <h3>Animation from a file</h3>
        <p>
          "{String(m.name ?? "")}", {String(m.seconds ?? "?")} s, from {String(m.file ?? "your file")} on a {SKELETON_KINDS[String(m.skeleton)] ?? String(m.skeleton)}{" "}
          skeleton.
        </p>
        {rest.length > 0 && v.parent && (
          <div className="row">
            <label>
              The file has {rest.length} more
              <select aria-label="Another animation in the file" value={other} onChange={(e) => setOther(e.target.value)}>
                <option value="">Choose one…</option>
                {rest.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <button
              disabled={busy || !other}
              onClick={() => run(() => props.chain(props.asset, v.parent!, [["import_motion", { clip: other, ...(target ? { for: target.version } : {}) }]]))}
            >
              Bring it in too
            </button>
          </div>
        )}
        {putOn}
        <p className="muted">Effigy does not know this file's licence: it is yours to check.</p>
      </section>
    );
  }
  if (v.step === "mocap") {
    // M64: the people at the start of the video (mocap takes person 0–7); the ones no motion of this video has yet
    const people = Math.min(Number(m.people ?? 1), 8);
    const have = new Set(props.versions.filter((x) => x.step === "mocap" && x.parent === v.parent).map((x) => Number(x.params?.person ?? 0)));
    const others = Array.from({ length: people }, (_, i) => i).filter((i) => !have.has(i));
    return (
      <section>
        <h3>Motion from video</h3>
        <p>
          "{String(m.name ?? "")}", {String(m.seconds ?? "?")} s, {MOCAP_MODES.find(([k]) => k === m.mode)?.[1].split(":")[0] ?? String(m.mode)} quality
          {m.total_seconds != null && `, made in ${Number(m.total_seconds).toFixed(0)} s`}
          {m.camera === "panning" && `, the camera's turn (${String(m.camera_turn_deg ?? "?")}° in all) taken out`}.
        </p>
        {v.outputs.preview && <video src={fileUrl(props.asset, v.version, v.outputs.preview)} controls muted loop style={{ maxWidth: "100%", maxHeight: 360 }} />}
        <p className="muted">
          The dots show the body points it followed. Feet sliding while planted: {m.foot_slide_mps_before_lock != null && `${m.foot_slide_mps_before_lock} m/s, then `}
          {String(m.foot_slide_mps ?? "?")} m/s.
        </p>
        {people > 1 && v.parent && (
          <div className="row">
            <span className="muted">
              {people} people at the start of the video; this is person {Number(v.params?.person ?? 0) + 1} (the largest first).
            </span>
            {others.length > 0 && (
              <button disabled={busy} onClick={() => run(() => Promise.all(others.map((i) => props.chain(props.asset, v.parent!, [["mocap", { ...v.params, person: i }]]))))}>
                Make the {others.length === 1 ? "other one's" : `other ${others.length}'s`} motion too
              </button>
            )}
          </div>
        )}
        {putOn}
      </section>
    );
  }
  if (connect)
    return (
      <section>
        <h3>Transition</h3>
        <p>
          "{String(m.name ?? "")}", {String(m.seconds ?? "?")} s
          {m.generate_seconds != null && `, made in ${Number(m.generate_seconds).toFixed(0)} s`}: it starts{" "}
          {v.params?.from ? `at the last pose of ${String(v.params.from)}` : "freely"} and ends {v.params?.to ? `at the first pose of ${String(v.params.to)}` : "freely"}.
        </p>
        {Array.isArray(m.seam_cm) && (
          <p className="muted">
            {(m.seam_cm as number[]).length > 1 ? "Its ends are" : "Its held end is"} {(m.seam_cm as number[]).join(" and ")} cm from{" "}
            {(m.seam_cm as number[]).length > 1 ? "those poses" : "that pose"} (on Kimodo's skeleton); a game blends the rest away when it switches clips.
          </p>
        )}
        {putOn}
      </section>
    );
  return (
    <section>
      <h3>Motion from text</h3>
      <p>"{String(m.prompt ?? "")}"</p>
      <p className="muted">
        {String(m.seconds ?? "?")} s{m.generate_seconds != null && `, generated in ${Number(m.generate_seconds).toFixed(0)} s`}.
      </p>
      {putOn}
    </section>
  );
}

function AnimInfo(props: {
  asset: string;
  version: Meta;
  onPick: (version: string) => void;
  onEdit: () => void;
  onRun: (params: object, step?: string, parent?: string) => void;
  busy: boolean;
}) {
  const [clips, setClips] = useState<{ name: string; seconds: number }[] | null>(null);
  const [still, setStill] = useState<string[]>([]);
  const [person, setPerson] = useState(false); // M65: Kimodo connects clips of people only
  // M67: the clips whose arms the anim step opened, and by how much
  const opened = Object.entries((props.version.metrics?.arms_opened ?? {}) as Record<string, { Left?: number; Right?: number; uncleared?: number }>);
  useEffect(() => {
    let live = true;
    fetch(fileUrl(props.asset, props.version.version, props.version.outputs.clips))
      .then((r) => r.json())
      .then((c) => live && (setClips(c.clips), setStill(c.still ?? [])));
    if (props.version.outputs.skeleton)
      fetch(fileUrl(props.asset, props.version.version, props.version.outputs.skeleton))
        .then((r) => r.json())
        .then((sk) => live && setPerson((sk.template ?? "mixamo52") === "mixamo52"))
        .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [props.asset, props.version.version]);
  return (
    <div className="stack">
      <p className="muted">
        Your model is animated. Choose an animation in the viewer's Animation menu to play it: the bar under the view pauses it, steps through it a frame at a
        time and plays it slower. The animations export with the model.
      </p>
      {props.version.step === "anim_edit" && <p className="note">Edited by hand: {(props.version.metrics?.clips as string[] | undefined)?.join(", ")}.</p>}
      {opened.length > 0 && (
        <p className="note">
          Arms opened so the hands clear the body: {opened.map(([c, o]) => `${c} (up to ${Math.max(o.Left ?? 0, o.Right ?? 0)}°${o.uncleared ? `, ${o.uncleared} frames still touching` : ""})`).join(", ")}.
        </p>
      )}
      {typeof props.version.metrics?.arms_clearance_skipped === "string" && (
        <p className="note warn">The arms were not checked against the body: {props.version.metrics.arms_clearance_skipped as string}</p>
      )}
      {still.length > 0 && (
        <p className="note warn">
          {still.join(", ")} {still.length === 1 ? "plays but never moves" : "play but never move"}: the animation's bones were not matched to this
          skeleton. Bring the animation in again from a file made on a known skeleton (Mixamo, Unreal, Kimodo), or choose another one.
        </p>
      )}
      {clips && (
        <ul className="downloads">
          {clips.map((c) => (
            <li key={c.name}>
              {c.name} · {c.seconds.toFixed(1)} s{still.includes(c.name) ? " · never moves" : ""}
            </li>
          ))}
        </ul>
      )}
      <div className="row">
        <button className="primary" onClick={props.onEdit}>
          Edit animation
        </button>
        {props.version.parent && props.version.step !== "import_model" && (
          <button onClick={() => props.onPick(props.version.parent!)}>{props.version.step === "anim" ? "Back to the skinned model" : "Back to the previous animation"}</button>
        )}
      </div>
      <p className="muted">Edit animation opens a timeline under the view: turn bones at any frame to correct a pose, trim, loop or smooth a clip.</p>
      {person && !!clips?.length && (
        <ConnectClips clips={clips.map((c) => c.name)} busy={props.busy} onRun={(params) => props.onRun(params, "motion", props.version.version)} />
      )}
    </div>
  );
}

// M65: Kimodo makes the motion from one of this model's clips into another (or after one, or into one): a transition clip,
// the motion step with from / to (steps/_connect.py)
function ConnectClips(props: { clips: string[]; busy: boolean; onRun: (params: object) => void }) {
  const [from, setFrom] = useState(props.clips[0] ?? "");
  const [to, setTo] = useState(props.clips[1] ?? "");
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(1.5);
  const [turn, setTurn] = useState(0);
  const [travel, setTravel] = useState("");
  const pick = (label: string, value: string, set: (v: string) => void, none: string) => (
    <label>
      {label}{" "}
      <select aria-label={`Connect ${label.toLowerCase()}`} value={value} onChange={(e) => set(e.target.value)}>
        <option value="">{none}</option>
        {props.clips.map((c) => (
          <option key={c}>{c}</option>
        ))}
      </select>
    </label>
  );
  const make = () =>
    props.onRun({
      ...(from ? { from } : {}),
      ...(to ? { to, turn } : {}),
      ...(to && travel.trim() !== "" ? { travel: Number(travel) } : {}),
      ...(prompt.trim() ? { prompt: prompt.trim() } : {}),
      seconds,
    });
  return (
    <details>
      <summary>Connect two clips</summary>
      <div className="stack">
        <p className="muted">
          Kimodo makes the motion that leads from the end of one clip into the start of another, for example from Idle into a fighting stance. Leave one side
          empty to continue a clip or to lead into one. The result is a transition clip: in the game it plays between the two. Its page puts it on the model with
          the other clips. Needs the Setup card Text to motion.
        </p>
        <div className="row">
          {pick("From", from, setFrom, "nothing: start freely")}
          {pick("To", to, setTo, "nothing: end freely")}
        </div>
        <input
          type="text"
          aria-label="What happens in between"
          placeholder="What happens in between (optional), for example: turns and draws the sword"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <div className="row">
          <label>
            Length{" "}
            <input type="number" aria-label="Transition seconds" min={0.5} max={8} step={0.5} value={seconds} style={{ width: "4.5em" }} onChange={(e) => setSeconds(Number(e.target.value))} /> s
          </label>
          {to && (
            <>
              <label title="Which way the second clip faces from where the first ended: left positive">
                Turn{" "}
                <input type="number" aria-label="Turn degrees" min={-180} max={180} step={15} value={turn} style={{ width: "4.5em" }} onChange={(e) => setTurn(Number(e.target.value))} /> °
              </label>
              <label title="How far straight on the second clip starts; empty: from how fast the two clips move">
                Travel{" "}
                <input type="number" aria-label="Travel metres" step={0.25} placeholder="auto" value={travel} style={{ width: "4.5em" }} onChange={(e) => setTravel(e.target.value)} /> m
              </label>
            </>
          )}
        </div>
        <button disabled={props.busy || (!from && !to) || !(seconds >= 0.5 && seconds <= 8) || !(Math.abs(turn) <= 180)} onClick={make}>
          Make transition
        </button>
      </div>
    </details>
  );
}

// Check fit: how well the skeleton sits in its mesh, this version (or the editor's unsaved moves) against the
// version before it (or the saved one); the joints to look at, a click selects one
function FitCheck(props: { asset: string; version: Meta; joints?: Record<string, Vec3>; onSelect: (joint: string) => void }) {
  const [made, setRes] = useState<{ now: FitQuality; then: FitQuality | null; label: string; joints?: Record<string, Vec3> } | null>(null);
  const res = made?.joints === props.joints ? made : null; // another move since: stale
  const [err, setErr] = useState<string | null>(null);
  const [busy, run] = useBusy();
  const v = props.version;
  const ref = props.joints ? v.version : v.parent;
  const check = () =>
    run(async () => {
      setErr(null);
      const joints = props.joints;
      try {
        const now = await api.fitCheck(props.asset, v.version, joints);
        const then = ref ? await api.fitCheck(props.asset, ref).catch(() => null) : null; // no skeleton before: none
        setRes({ now, then, label: joints ? "saved" : `before (${ref})`, joints });
      } catch (e) {
        setErr((e as Error).message);
      }
    });
  const d = res?.then ? res.now.score - res.then.score : 0;
  const item = ([n, why]: [string, string]) => (
    <li key={n}>
      <button className="small" onClick={() => props.onSelect(n)}>
        {n}
      </button>
      : {why}
    </li>
  );
  return (
    <div className="stack" aria-label="Check fit">
      <div className="row">
        <button disabled={busy} onClick={check}>
          {busy ? "Checking…" : "Check fit"}
        </button>
        {res && (
          <strong>
            Fit {res.now.score}
            {res.then && ` · ${res.label} ${res.then.score} · ${d > 2 ? "better" : d < -2 ? "worse" : "about the same"}`}
          </strong>
        )}
      </div>
      {res && (
        <p className="muted">
          {res.now.inside} of {res.now.joints} joints inside the model. The fit says how centred the joints sit across their limbs and body, less what lies
          outside: 100 is every joint in the middle and every bone inside the body.
        </p>
      )}
      {res && res.now.attention.length > 0 && (
        <>
          <p className="muted">To look at, the worst first (click one to select it):</p>
          <ul>{res.now.attention.slice(0, 8).map(item)}</ul>
          {res.now.attention.length > 8 && (
            <details>
              <summary>{res.now.attention.length - 8} more</summary>
              <ul>{res.now.attention.slice(8).map(item)}</ul>
            </details>
          )}
        </>
      )}
      {res && !res.now.attention.length && <p className="muted">Nothing to look at: every joint sits well inside and every bone runs inside the body.</p>}
      {err && <p className="note warn">{err}</p>}
    </div>
  );
}

function SkinQA(props: {
  asset: string;
  version: Meta;
  motions: Meta[];
  onPick: (version: string) => void;
  onRun: (params: object, step?: string, parent?: string) => void;
  busy: boolean;
}) {
  const [qa, setQa] = useState<{
    items: Record<string, { status: string; value: number | null }>;
    picked?: string;
    engines?: Record<string, object>;
    skipped?: Record<string, string>;
  } | null>(null);
  useEffect(() => {
    let live = true;
    fetch(fileUrl(props.asset, props.version.version, props.version.outputs.qa))
      .then((r) => r.json())
      .then((q) => live && setQa(q));
    return () => {
      live = false;
    };
  }, [props.asset, props.version.version]);
  if (!qa) return <p className="muted">Loading the checks…</p>;
  const ENGINE: Record<string, string> = { blender: "Blender bone heat", skintokens: "SkinTokens (learned)" };
  const others = Object.keys(qa.engines ?? {}).filter((e) => e !== qa.picked);
  const n = (s: string) => Object.values(qa.items).filter((i) => i.status === s).length;
  const plural = (k: number, word: string) => `${k} ${word}${k === 1 ? "" : "s"}`;
  const total = Object.keys(qa.items).length;
  const unknown = total - n("pass") - n("warn") - n("fail");
  const summary = [`${plural(n("pass"), "check")} passed`, n("warn") && plural(n("warn"), "warning"), n("fail") && `${n("fail")} failed`, unknown && `${unknown} not checked`]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="stack">
      {qa.picked && (
        <p>
          Weights by <strong>{ENGINE[qa.picked] ?? qa.picked}</strong>
          {others.length > 0 && `, chosen over ${others.map((e) => ENGINE[e] ?? e).join(", ")} by the checks below`}
          {qa.skipped &&
            ` (${Object.entries(qa.skipped)
              .map(([e, why]) => `${ENGINE[e] ?? e} unavailable: ${why}`)
              .join("; ")})`}
          .
        </p>
      )}
      {!!props.version.metrics?.rigid && (
        <p>
          Rigid parts:{" "}
          {Object.entries(props.version.metrics.rigid as Record<string, string>)
            .map(([part, bone]) => `${part} on ${bone}`)
            .join(", ")}
          .
        </p>
      )}
      <ClipPicker
        asset={props.asset}
        rig={props.version.version}
        template={String(props.version.metrics?.template ?? "mixamo52")}
        onRun={props.onRun}
        busy={props.busy}
        motions={props.motions}
      />
      <details open={total > n("pass")}>
        <summary>Skin checks: {summary}</summary>
        <div className="stack">
          <p className="muted">
            The model now moves with its skeleton. In the viewer, choose "Weights: a bone" to see what each bone moves. Red means fully and blue means not at
            all.{" "}
            {props.version.step === "import_model"
              ? "It came with this skeleton. To fit one of the app's instead, import it again as a still model and rig it."
              : "If a part follows the wrong bone, click Edit skeleton and fix the joints."}
          </p>
          {props.version.step !== "import_model" && props.version.parent && (
            <div className="row">
              <button onClick={() => props.onPick(props.version.parent!)}>Edit skeleton</button>
            </div>
          )}
          <section className="report">
            <ul>
              {Object.entries(qa.items).map(([k, it]) => (
                <li key={k} className={it.status}>
                  <b>{STATUS[it.status] ?? "Not checked"}</b> {SKIN_CHECKS[k]?.[0] ?? k}: {it.value ?? "–"}
                  {it.status !== "pass" && SKIN_CHECKS[k] && <span className="muted"> · {SKIN_CHECKS[k][1]}</span>}
                </li>
              ))}
            </ul>
          </section>
        </div>
      </details>
    </div>
  );
}

function RigPanel(props: {
  asset: string;
  version: Meta;
  motions: Meta[];
  onView: (r: RigView | null) => void;
  onEditing?: (on: boolean) => void; // M37: the animation editor is open (the viewer makes room for its timeline)
  chain: Chain; // M37: the editors' saves run through it: the view lands on the new version, and an editor closes only when one came back (UI-001)
  onPick: (version: string) => void;
  onRun: (params: object, step?: string, parent?: string) => Promise<unknown>;
}) {
  const [busy, run] = useBusy();
  const [animEditing, setAnimEditing] = useState(false);
  useEffect(() => {
    props.onEditing?.(animEditing);
    return () => props.onEditing?.(false);
  }, [animEditing]);
  const onRun = (params: object, step?: string, parent?: string) => run(() => props.onRun(params, step, parent));
  const v = props.version;
  // an editor's save: the new version, or null when the step was refused or failed (the editor then stays open
  // with its edits and history; audit 2026-10-10, UI-001). Without a chain (an older mount) the old fire-and-close.
  const save = (step: string, params: object): Promise<unknown> =>
    props.chain ? props.chain(props.asset, v.version, [[step, params]]) : props.onRun(params, step).then(() => true);
  const [data, setData] = useState<{
    joints: Joint[];
    markers: Record<string, Vec3>;
    report: RigReport;
    template: string; // M36: "mixamo52" or a creature's
    sides?: [string, string];
    yaw: number;
  } | null>(null);
  const [kind, setKind] = useState("mixamo52"); // what Fit a skeleton fits
  const creature = !!data && data.template !== "mixamo52";
  const [picked, setPicked] = useState<string[]>([]); // the skeleton editor's selection, the active joint first; the marker editor uses just the first
  const sel = picked[0] ?? null;
  const setSel = (n: string) => setPicked([n]);
  const pick = (n: string, add: boolean) => setPicked((p) => (!add ? [n] : p.includes(n) ? p.filter((x) => x !== n) : [...p, n]));
  const [xray, setXray] = useState(false);
  const moveJoints = useRef<(c: Moved) => void>(() => undefined); // the skeleton editor's commit, for the number fields
  const [sym, setSym] = useState(true);
  const sk = useHistory<Snap>();
  const mk = useHistory<Record<string, Vec3>>();
  useUnsaved("skeleton", !!sk.h?.past.length && !busy); // UX-01: Cancel, a version switch or a link asks first
  useUnsaved("markers", !!mk.h?.past.length && !busy);
  const hist = sk.h;
  const edit = mk.h?.now ?? null; // the markers being edited
  const [mode, setMode] = useState<SkeletonEdit["mode"]>("translate");
  const [rigid, setRigid] = useState<Record<string, string>>({}); // M32: part → bone, "" = the nearest
  const parts = Object.keys((v.metrics?.parts as Record<string, number> | undefined) ?? {}).filter((n) => n !== "Body");
  const { commit } = sk;
  useEffect(() => {
    if (!hist && !edit) return;
    const key = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).matches?.("textarea,select,input[type=text],input[type=number]")) return;
      if (e.ctrlKey || e.metaKey) {
        if (e.key.toLowerCase() === "z") (e.preventDefault(), e.shiftKey ? (sk.redo(), mk.redo()) : (sk.undo(), mk.undo()));
      } else if (hist && e.key === "g") setMode("translate");
      else if (hist && e.key === "r") setMode("rotate");
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [!!hist, !!edit]);
  useEffect(() => {
    if (!v.outputs.skeleton || ["rig_skin", "anim", "anim_edit"].includes(actsAs(v))) return;
    let live = true;
    const get = (k: string) => (v.outputs[k] ? fetch(fileUrl(props.asset, v.version, v.outputs[k])).then((r) => r.json()) : Promise.resolve(null));
    Promise.all(["skeleton", "markers", "report"].map(get)).then(
      ([sk, m, r]) =>
        live &&
        setData({ joints: sk.joints, markers: m?.markers ?? {}, report: r, template: sk.template ?? "mixamo52", sides: sk.sides, yaw: sk.yaw ?? 0 }),
    );
    return () => {
      live = false;
    };
  }, [props.asset, v.version]);
  const markers = edit ?? data?.markers;
  useEffect(() => {
    if (!data || !markers) return props.onView(null);
    if (hist) {
      // the body's middle, left and right mirror across it: x = (Chin.x + Groin.x) / 2 for a person; a creature
      // across its middle joints, along its left-right: the template's x turned by its yaw (x at 0°, z at 90°)
      const a = data.sides ? (data.yaw * Math.PI) / 180 : 0;
      const side = [Math.cos(a), 0, -Math.sin(a)].map((x) => Math.round(x * 1e12) / 1e12) as Vec3; // a quarter turn exactly
      const across = (p: Vec3) => p[0] * side[0] + p[2] * side[2];
      const mids = data.joints.filter((j) => j.parent && !twinOf(j.name, data.sides));
      const c = data.sides ? mids.reduce((t, j) => t + across(j.position), 0) / Math.max(mids.length, 1) : (markers.Chin[0] + markers.Groin[0]) / 2;
      const onJoints = (changes: Moved) =>
        commit((s) => {
          const next = { joints: { ...s.joints }, twists: { ...s.twists } };
          for (const [n, { position, twist }] of Object.entries(changes)) {
            next.joints[n] = position;
            if (twist) next.twists[n] = twist;
            const t = sym && twinOf(n, data.sides);
            if (!t) continue;
            const k = 2 * (c - across(position));
            next.joints[t] = [position[0] + k * side[0], position[1], position[2] + k * side[2]];
            if (twist) next.twists[t] = [-twist[0], twist[1], twist[2]]; // a direction mirrors by its x alone
          }
          return next;
        });
      moveJoints.current = onJoints;
      const joints = data.joints.map((j) => ({ ...j, position: hist.now.joints[j.name] ?? j.position, twist: hist.now.twists[j.name] ?? j.twist }));
      return props.onView({
        joints,
        markers: {},
        selected: sel,
        edit: busy ? undefined : { mode, selected: picked, onSelect: pick, onJoints }, // a save's wait: no new edits to lose
        outside: data.report.outside,
        xray,
      });
    }
    const onMarker = (name: string, p: Vec3 | null) => {
      if (!p) return setSel(name);
      mk.commit((m) => {
        const next = { ...m, [name]: p };
        const t = mirror(name, p, next);
        if (t) next[t[0]] = t[1];
        return next;
      });
    };
    const mirror = (name: string, p: Vec3, m: Record<string, Vec3>): [string, Vec3] | null => {
      const t = twinOf(name);
      return sym && t ? [t, [m.Chin[0] + m.Groin[0] - p[0], p[1], p[2]]] : null; // across the body's middle, x = (Chin.x + Groin.x) / 2
    };
    // while editing, the derived joints would lag behind the markers: only the markers are shown
    props.onView({
      joints: edit ? [] : data.joints,
      outside: data.report.outside,
      markers,
      selected: edit ? sel : null,
      onMarker: edit && !busy ? onMarker : undefined,
      xray: !!edit && xray,
      twin: edit && !busy ? (n, p) => mirror(n, p, edit) : undefined,
    });
  }, [data, mk.h, picked, sym, hist, mode, xray, busy]);
  useEffect(() => () => props.onView(null), []);

  const step = actsAs(v); // an imported rig or animation is handled as ours
  const title = !v.outputs.skeleton
    ? "Rig for animation (optional)"
    : step === "anim" || step === "anim_edit"
      ? animEditing
        ? "Edit animation"
        : step === "anim"
          ? "Animated model"
          : "Edited animation"
      : step === "rig_skin"
        ? "Skinned model"
        : "Skeleton";
  const body = () => {
    if (!v.outputs.skeleton)
      return (
        <div className="stack">
          <p className="muted">
            Add a skeleton to a character or an animal; skip this for props and scenery.
          </p>
          <fieldset>
            <legend>What is it?</legend>
            {RIG_KINDS.map(([id, name]) => (
              <label key={id} className="check">
                <input type="radio" name="rig-kind" checked={kind === id} onChange={() => setKind(id)} /> {name}
              </label>
            ))}
          </fieldset>
          <button
            className="primary"
            disabled={busy}
            onClick={() => onRun(kind === "mixamo52" ? {} : { template: kind })}
          >
            Fit a skeleton
          </button>
          <details>
            <summary>Help</summary>
            <p className="muted">
              A person works best standing upright with the arms away from the body; an animal standing on all its legs. It takes about 10 seconds. You can
              correct the result by hand afterwards.
            </p>
          </details>
        </div>
      );
    if (step === "anim" || step === "anim_edit")
      return animEditing && window.__viewer ? (
        <AnimEditor
          asset={props.asset}
          version={v}
          viewer={window.__viewer}
          onSave={(clips) => save("anim_edit", { clips })}
          onClose={() => setAnimEditing(false)}
        />
      ) : (
        <AnimInfo asset={props.asset} version={v} onPick={props.onPick} onEdit={() => setAnimEditing(true)} onRun={onRun} busy={busy} />
      );
    if (step === "rig_skin") return <SkinQA asset={props.asset} version={v} motions={props.motions} onPick={props.onPick} onRun={onRun} busy={busy} />;
    if (!data) return <p className="muted">Loading the skeleton…</p>;
    const r = data.report;
    if (hist) {
      const byName = new Map(data.joints.map((j) => [j.name, j]));
      const differs = (o: Record<string, Vec3>, of: (j: Joint) => Vec3 | undefined) =>
        Object.fromEntries(Object.entries(o).filter(([n, p]) => !of(byName.get(n)!)?.every((x, i) => Math.abs(x - p[i]) < 1e-6)));
      const sj = differs(hist.now.joints, (j) => j.position);
      const st = differs(hist.now.twists, (j) => j.twist);
      const joint = (n: string) =>
        byName.has(n) && (
          <button key={n} title={PLACEMENT[n]} aria-pressed={picked.includes(n)} onClick={(e) => pick(n, e.shiftKey)}>
            {n}
          </button>
        );
      return (
        <div className="stack">
          <details>
            <summary>Help</summary>
            <ul className="muted">
              <li>Click a joint to select it. Shift+click adds more. Select chain adds everything below.</li>
              <li>Drag the arrows to move. Drag the rings to rotate. The red ring turns the bone's front (the palm on a hand).</li>
              <li>The cyan ticks show where each bone faces. The dim parts are behind the surface. The white line drops to the ground.</li>
              <li>For depth, use Front, Side and Top, or tick X-ray. The X, Y and Z fields type an exact position.</li>
              <li>Keys: G move, R rotate, Esc cancels a drag, Ctrl+Z undoes, Ctrl+Shift+Z redoes.</li>
              <li>A joint sits inside the body, where the bone turns, not on the skin. Markers may sit on the surface; the fit moves the joints inside.</li>
              <li>Left and right are the character's own left and right.</li>
            </ul>
          </details>
          <div className="row" role="group" aria-label="Tool">
            <button aria-pressed={mode === "translate"} onClick={() => setMode("translate")}>
              Move
            </button>
            <button aria-pressed={mode === "rotate"} onClick={() => setMode("rotate")}>
              Rotate
            </button>
            <button disabled={busy || !hist.past.length} onClick={sk.undo}>
              Undo
            </button>
            <button disabled={busy || !hist.future.length} onClick={sk.redo}>
              Redo
            </button>
          </div>
          {(creature ? creatureGroups(data.joints, data.sides!) : JOINT_GROUPS).map(([g, names]) => (
            <div key={g} role="group" aria-label={g}>
              <h4>{g}</h4>
              <div className="marker-grid">{names.map(joint)}</div>
            </div>
          ))}
          {!creature && (
            <details>
              <summary>Fingers</summary>
              <div className="marker-grid">{data.joints.filter((j) => /^(Left|Right)Hand./.test(j.name)).map((j) => joint(j.name))}</div>
            </details>
          )}
          {sel && (
            <>
              <div className="row" role="group" aria-label="Active joint position">
                {([0, 1, 2] as const).map((i) => {
                  const at = hist.now.joints[sel] ?? byName.get(sel)!.position;
                  return (
                    <label key={`${sel}${i}${at[i]}`}>
                      {"XYZ"[i]}{" "}
                      <input
                        type="number"
                        step="0.001"
                        aria-label={"XYZ"[i]}
                        defaultValue={at[i].toFixed(3)}
                        style={{ width: "5.5em" }}
                        onBlur={(e) => {
                          const x = parseFloat(e.target.value);
                          if (Number.isFinite(x) && x.toFixed(3) !== at[i].toFixed(3))
                            moveJoints.current({ [sel]: { position: at.map((c, k) => (k === i ? x : c)) as Vec3 } });
                        }}
                        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                      />
                    </label>
                  );
                })}
                <span className="muted">
                  {((hist.now.joints[sel] ?? byName.get(sel)!.position)[1] - (window.__viewer?.ground() ?? 0)).toFixed(3)} m above the ground
                </span>
              </div>
              <p className="muted">Where it sits: {PLACEMENT[sel]}</p>
            </>
          )}
          <label className="check">
            <input type="checkbox" checked={sym} onChange={(e) => setSym(e.target.checked)} />
            Symmetry (mirror every edit to the other side)
          </label>
          <label className="check">
            <input type="checkbox" checked={xray} onChange={(e) => setXray(e.target.checked)} />
            X-ray (see through the model)
          </label>
          <div className="row">
            <button disabled={busy || !sel} onClick={() => commit((s) => ({ joints: picked.reduce(omit, s.joints), twists: picked.reduce(omit, s.twists) }))}>
              Reset joint
            </button>
            <button
              disabled={!sel}
              onClick={() => {
                const below = (n: string): string[] => [n, ...data.joints.filter((j) => j.parent === n).flatMap((j) => below(j.name))];
                setPicked((p) => [...new Set([...p, ...p.flatMap(below)])]);
              }}
            >
              Select chain
            </button>
            <button disabled={busy || !Object.keys({ ...sj, ...st }).length} onClick={() => commit(() => ({ joints: {}, twists: {} }))}>
              Reset all
            </button>
          </div>
          <FitCheck asset={props.asset} version={v} joints={hist.now.joints} onSelect={setSel} />
          <div className="row">
            <button
              className="primary"
              disabled={busy || !Object.keys({ ...sj, ...st }).length}
              onClick={() => run(async () => void ((await save("rig_fit", { joints: sj, twists: st })) && sk.stop()))}
            >
              Save skeleton
            </button>
            <button disabled={busy} onClick={() => leave() && sk.stop()}>
              Cancel
            </button>
          </div>
        </div>
      );
    }
    if (!edit)
      return (
        <div className="stack">
          {v.metrics?.source === "edited" && (
            <p className="note">
              Edited by hand:{" "}
              {Array.isArray(v.metrics.edited_joints) || Array.isArray(v.metrics.edited_twists)
                ? `${(v.metrics.edited_joints as string[] | undefined)?.length ?? 0} joints moved, ${(v.metrics.edited_twists as string[] | undefined)?.length ?? 0} twists set.`
                : "markers."}
            </p>
          )}
          <p>
            {r.joints} joints, {r.inside === r.joints ? "all inside the model" : `${r.inside} inside the model`}.
          </p>
          {r.outside.length > 0 && (
            <p className="note warn">
              Outside the model: {r.outside.join(", ")}. Move it in Edit skeleton, or the nearest marker in Edit markers (red in the view; tick Wireframe in the
              toolbar to see through the model).
            </p>
          )}
          {r.low_confidence.length > 0 && <p className="note warn">Unsure placement: {r.low_confidence.map(markerLabel).join(", ")}. Check these markers.</p>}
          {parts.length > 0 && (
            <>
              <p className="muted">
                Parts: a hard piece, such as a helmet, a shield or a weapon, can move with one bone instead of bending with the body. The nearest bone is
                usually right: the head for a helmet, the hand for a held weapon.
              </p>
              <ul className="parts-list">
                {parts.map((n) => (
                  <li key={n}>
                    {n}{" "}
                    <select
                      aria-label={`How ${n} moves`}
                      value={n in rigid ? rigid[n] || "nearest" : "bends"}
                      onChange={(e) => {
                        const { [n]: _, ...rest } = rigid;
                        const to = e.target.value;
                        setRigid(to === "bends" ? rest : { ...rest, [n]: to === "nearest" ? "" : to });
                      }}
                    >
                      <option value="bends">Bends with the body</option>
                      <option value="nearest">Rigid, on the nearest bone</option>
                      {JOINT_GROUPS.flatMap(([, names]) => names).map((b) => (
                        <option key={b} value={b}>
                          Rigid, on {b}
                        </option>
                      ))}
                    </select>
                  </li>
                ))}
              </ul>
            </>
          )}
          <button className="primary" disabled={busy} onClick={() => onRun(Object.keys(rigid).length ? { rigid } : {}, "rig_skin")}>
            Attach to skeleton
          </button>
          <p className="muted">Attach the mesh to the skeleton: about 10 seconds, then the result is tested in 10 poses.</p>
          <div className="row">
            {!creature && <button onClick={() => (mk.start({ ...data.markers }), setSel("Chin"))}>Edit markers</button>}
            <button onClick={() => (sk.start({ joints: {}, twists: {} }), setSel(creature ? data.joints.find((j) => j.parent)!.name : "Hips"))}>
              Edit skeleton
            </button>
          </div>
          {creature && (
            <div className="row" role="group" aria-label="Turn the skeleton">
              <span className="muted">Facing the wrong way?</span>
              <button disabled={busy} onClick={() => onRun({ template: data.template, yaw: (data.yaw + 180) % 360 })}>
                Turn around
              </button>
              <button disabled={busy} onClick={() => onRun({ template: data.template, yaw: (data.yaw + 90) % 360 })}>
                Turn a quarter
              </button>
            </div>
          )}
          <FitCheck asset={props.asset} version={v} onSelect={(n) => (sk.start({ joints: {}, twists: {} }), setSel(n))} />
          <p className="muted">
            Green dots are joints and yellow lines are bones. To fix one joint, click Edit skeleton.{creature ? "" : " To fix a whole area, click Edit markers."}
          </p>
        </div>
      );
    const moved = MARKER_NAMES.some((n) => edit[n] && !edit[n].every((x, i) => x === data.markers[n][i]));
    return (
      <div className="stack">
        <details>
          <summary>Help</summary>
          <ul className="muted">
            <li>Click a marker, then click the model where it belongs. It goes to the middle of the body under the cursor.</li>
            <li>To fine-tune, drag the arrows on the marker. Ctrl+Z undoes.</li>
            <li>Left and right are the character's own. Seen from the front, its left is on your right.</li>
            <li>Click Save markers to rebuild the skeleton from them.</li>
          </ul>
        </details>
        <div className="row">
          <button disabled={busy || !mk.h!.past.length} onClick={mk.undo}>
            Undo
          </button>
          <button disabled={busy || !mk.h!.future.length} onClick={mk.redo}>
            Redo
          </button>
          <button disabled={busy || !moved} onClick={() => mk.commit(() => ({ ...data.markers }))}>
            Reset all
          </button>
        </div>
        <div className="marker-grid" role="group" aria-label="Markers">
          {MARKER_NAMES.map((m) => (
            <button key={m} title={MARKER_PLACEMENT[m]} aria-pressed={sel === m} onClick={() => setSel(m)}>
              {markerLabel(m)}
            </button>
          ))}
        </div>
        {sel && MARKER_PLACEMENT[sel] && <p className="muted">Where it sits: {MARKER_PLACEMENT[sel]}</p>}
        <label className="check">
          <input type="checkbox" checked={sym} onChange={(e) => setSym(e.target.checked)} />
          Symmetry (move left and right together)
        </label>
        <label className="check">
          <input type="checkbox" checked={xray} onChange={(e) => setXray(e.target.checked)} />
          X-ray (see through the model)
        </label>
        <div className="row">
          <button className="primary" disabled={busy || !moved} onClick={() => run(async () => void ((await save("rig_fit", { markers: edit })) && mk.stop()))}>
            Save markers
          </button>
          <button disabled={busy} onClick={() => leave() && mk.stop()}>
            Cancel
          </button>
        </div>
      </div>
    );
  };
  return (
    <section>
      <h3>{title}</h3>
      {body()}
    </section>
  );
}

const MODE_NAMES: Record<ViewMode, string> = {
  pbr: "Material",
  albedo: "Base color",
  normal: "Surface normals",
  ao: "Ambient occlusion",
  roughness: "Roughness",
  metallic: "Metallic",
  uv: "UV checker",
};

// M37a: the time bar under a playing clip (ui/src/anim/Transport.tsx)
function TimeBar(props: { viewer: Viewer }) {
  useTransportKeys(props.viewer);
  return (
    <div className="timebar">
      <Transport viewer={props.viewer} />
    </div>
  );
}

function ViewerPane(props: {
  glb: string | null;
  image: string | null;
  overlay: React.ReactNode;
  hint: React.ReactNode;
  rig?: RigView | null;
  editing?: boolean; // M37: the animation editor's timeline takes the bottom of the pane (its slot), no time bar
  parts?: string[]; // M31: the part names of a segmented model, tinted on request
  quads?: boolean; // mesh-gates: a quad mesh (remesh, Topology: Quads): Wireframe draws its quads
  children?: React.ReactNode;
}) {
  const [tint, setTint] = useState(true);
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewer = useRef<Viewer | null>(null);
  const [mode, setMode] = useState<ViewMode>("pbr");
  const [wire, setWire] = useState(false);
  const [weights, setWeights] = useState(""); // a bone's name: the weights view
  const [skel, setSkel] = useState(false);
  const [clip, setClip] = useState(""); // the animation playing
  const [stats, setStats] = useState<Stats | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const { glb } = props;

  useEffect(() => {
    const v = new Viewer(canvas.current!);
    viewer.current = window.__viewer = v;
    return () => v.dispose();
  }, []);
  useEffect(() => {
    setStats(null);
    setFailed(false);
    setWeights("");
    setSkel(false);
    setClip("");
    if (glb)
      viewer.current!.load(glb).then(
        (s) => s && setStats(s),
        () => setFailed(true),
      );
    else viewer.current!.clear();
  }, [glb, attempt]);
  useEffect(() => viewer.current?.setWireframe(wire, !!props.quads), [wire, props.quads]);

  const { rig, parts } = props;
  const partsKey = parts?.join("\n");
  useEffect(() => {
    if (stats) viewer.current!.setTint(tint && parts ? parts : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- partsKey stands for parts
  }, [stats, tint, partsKey]);
  useEffect(() => {
    if (stats)
      viewer.current!.setRig(
        rig?.joints ?? null,
        rig?.markers ?? null,
        rig?.onMarker,
        rig?.selected,
        rig?.edit ? (n) => n : markerLabel,
        rig?.edit,
        rig?.twin,
        rig?.outside,
      );
  }, [rig, stats]);
  useEffect(() => {
    if (stats) viewer.current!.setXray(!!rig?.xray);
  }, [rig?.xray, stats]);
  // editing markers: straight from the front, so left and right aren't foreshortened (the character's
  // left is on the right of the screen)
  const editing = !!(rig?.onMarker || rig?.edit);
  useEffect(() => {
    if (editing && stats) viewer.current!.frame("front");
  }, [editing, stats]);

  const tools = glb && !props.overlay;
  const timebar = tools && stats && clip && !props.editing && viewer.current;
  return (
    <div className={`preview${props.editing ? " editing" : ""}`}>
      <canvas id="viewport" ref={canvas} aria-label="3D preview: drag to orbit, scroll to zoom, right-drag to pan" />
      {props.overlay ??
        (props.image ? (
          <img className="overlay checker" src={props.image} alt="reference picture" />
        ) : (
          !glb && props.hint && <div className="hint">{props.hint}</div>
        ))}
      {failed && (
        <div className="note warn preview-error" role="alert">
          Couldn't display this version.{" "}
          <button className="small" onClick={() => setAttempt((a) => a + 1)}>
            Retry preview
          </button>
        </div>
      )}
      {tools && (
        <div className="toolbar">
          <select
            aria-label="Inspect"
            value={mode}
            onChange={(e) => {
              const m = e.target.value as ViewMode;
              setMode(m);
              viewer.current!.setViewMode(m);
            }}
          >
            {VIEW_MODES.map((m) => (
              <option key={m} value={m}>
                {MODE_NAMES[m]}
              </option>
            ))}
          </select>
          <label className="check">
            <input
              type="checkbox"
              checked={wire}
              onChange={(e) => setWire(e.target.checked)}
            />
            Wireframe
          </label>
          {parts && (
            <label className="check">
              <input type="checkbox" checked={tint} onChange={(e) => setTint(e.target.checked)} />
              Tint parts
            </label>
          )}
          {stats && stats.bones.length > 0 && (
            <>
              {stats.clips.length > 0 && !props.editing && (
                <select
                  aria-label="Animation"
                  value={clip}
                  onChange={(e) => {
                    setClip(e.target.value);
                    viewer.current!.setClip(e.target.value || null);
                  }}
                >
                  <option value="">Animation: off</option>
                  {stats.clips.map((c) => (
                    <option key={c} value={c}>
                      Play: {c}
                    </option>
                  ))}
                </select>
              )}
              <select
                aria-label="Skin weights"
                value={weights}
                onChange={(e) => {
                  setWeights(e.target.value);
                  viewer.current!.setWeights(e.target.value || null, skel);
                }}
              >
                <option value="">Weights: off</option>
                {stats.bones.map((b) => (
                  <option key={b} value={b}>
                    Weights: {b}
                  </option>
                ))}
              </select>
              <label className="check">
                <input
                  type="checkbox"
                  checked={skel}
                  onChange={(e) => {
                    setSkel(e.target.checked);
                    viewer.current!.setWeights(weights || null, e.target.checked);
                  }}
                />
                Skeleton
              </label>
            </>
          )}
          <button onClick={() => viewer.current!.frame()}>Frame model</button>
          <button onClick={() => viewer.current!.frame("front")}>Front</button>
          <button onClick={() => viewer.current!.frame("side")}>Side</button>
          <button onClick={() => viewer.current!.frame("top")}>Top</button>
        </div>
      )}
      {timebar && <TimeBar viewer={viewer.current!} />}
      {props.editing && <div className="timeline-slot" id="timeline-slot" />}
      {tools && stats && !timebar && !props.editing && (
        <span id="stats">
          {stats.triangles.toLocaleString()} triangles · {stats.vertices.toLocaleString()} vertices · {stats.meshes} mesh
          {stats.meshes === 1 ? "" : "es"}
        </span>
      )}
      {!props.overlay && props.children}
    </div>
  );
}

function BlenderPanel(props: { asset: string; version: Meta; onPull: () => Promise<void> }) {
  const [link, setLink] = useState<"pro" | "free" | null | undefined>(undefined);
  const [start, setStart] = useState(false); // Setup: Open in Blender starts it when none answers
  const [busy, run] = useBusy();
  const check = () =>
    api
      .blenderStatus()
      .then((s) => (setLink(s.link), setStart(!!s.start)))
      .catch(() => setLink(null));
  useEffect(() => {
    check();
  }, []);
  const short = props.version.version.slice(0, 3);
  const [opened, setOpened] = useState<{ ok: boolean; text: string } | null>(null);
  const openInBlender = () =>
    run(() =>
      api.blenderOpen(props.asset, props.version.version).then(
        (r) => (
          setOpened({
            ok: true,
            text: `Opened ${r.version} in Blender as collection EFFIGY_${props.asset}_v${r.version.slice(0, 3)}: switch to Blender.`,
          }),
          check()
        ),
        (e) => setOpened({ ok: false, text: explain(e) }),
      ),
    );
  return (
    <div className="stack">
      <span className={link ? "tag ok" : "muted"}>
        {link === undefined
          ? "Checking Blender…"
          : link
            ? `● Connected (${link === "pro" ? "Blender MCP Pro" : "blender-mcp, free"})`
            : start
              ? "○ Not connected: Open starts your Blender and its add-on's server (Setup)."
              : "○ Not connected: open Blender, press N, and start the add-on's server (Blender MCP Pro or blender-mcp), or let Setup start it."}
        {!link && link !== undefined && (
          <button className="small" onClick={check}>
            Check again
          </button>
        )}
      </span>
      <div className="row">
        <button disabled={!(link || start) || busy} onClick={openInBlender}>
          {busy && !link ? "Starting Blender…" : `Open ${short} in Blender`}
        </button>
        <button disabled={!link || busy} onClick={() => run(props.onPull)}>
          Save Blender changes as version
        </button>
      </div>
      {opened && (
        <span className={opened.ok ? "muted" : "error"} role="status">
          {opened.text}
        </span>
      )}
      <span className="muted">Click Save Blender changes as version to bring the model from Blender back. It becomes a new version. The old one stays.</span>
    </div>
  );
}

// M40: Claude, Codex and other agents use the app over MCP; this shows each one what to paste, and tests the bridge
// the port this app serves on: another program may need 8000 (Unreal's MCP plugin); a restart applies a new one
// the owner's agent, 2026-10-13 (finding 4): Open in Blender starts the user's own Blender when none answers
function BlenderStart() {
  const [cfg, setCfg] = useState<BlenderSetup | null>(null);
  const [start, setStart] = useState(false);
  const [exe, setExe] = useState("");
  const [err, setErr] = useState("");
  const [busy, run] = useBusy();
  useEffect(() => {
    api.blenderSetup().then(
      (c) => (setCfg(c), setStart(c.start), setExe(c.exe ?? c.found ?? "")),
      (e) => setErr(explain(e)),
    );
  }, []);
  const changed = !!cfg && (start !== cfg.start || exe.trim() !== (cfg.exe ?? ""));
  const save = () =>
    run(() =>
      api.setBlenderSetup(start, exe).then(
        (c) => (setCfg(c), setExe(c.exe ?? ""), setErr("")),
        (e) => setErr(explain(e)),
      ),
    );
  return (
    <section aria-label="Start Blender">
      <h2>Start Blender</h2>
      <p className="muted">
        Edit in Blender needs your Blender open with its MCP add-on's server on. With this on, Open in Blender starts it and the server when none answers,
        and waits for it.
      </p>
      <label className="check">
        <input type="checkbox" checked={start} disabled={!cfg} onChange={(e) => setStart(e.target.checked)} /> Start Blender when it is needed
      </label>
      <div className="folder">
        <div className="row">
          <input aria-label="Your Blender" placeholder="Your blender.exe, the one with the add-on" value={exe} disabled={!cfg} onChange={(e) => setExe(e.target.value)} />
          <button disabled={busy || !changed} onClick={save}>
            Save
          </button>
        </div>
      </div>
      {cfg && !cfg.exe && cfg.found && <p className="note">Filled in from the Blender connected now: Save to keep it.</p>}
      {err && <p className="error">{err}</p>}
    </section>
  );
}

function AppPort() {
  const [info, setInfo] = useState<PortInfo | null>(null);
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  const [busy, run] = useBusy();
  useEffect(() => {
    api.port().then(
      (p) => (setInfo(p), setValue(String(p.next))),
      (e) => setErr(explain(e)),
    );
  }, []);
  const n = Number(value);
  const ok = Number.isInteger(n) && n >= 1024 && n <= 65535;
  const env = info?.source === "env";
  const save = () =>
    run(() =>
      api.setPort(n).then(
        (p) => (setInfo(p), setErr("")),
        (e) => setErr(explain(e)),
      ),
    );
  return (
    <section aria-label="App address">
      <h2>App address</h2>
      <p className="muted">
        {info ? `This app runs at http://127.0.0.1:${info.running}. ` : ""}
        Change the port when another program needs this one: the Unreal editor's MCP plugin uses 8000 too.
      </p>
      <div className="row" style={{ alignItems: "end" }}>
        <label>
          Port
          <input type="number" min={1024} max={65535} step={1} value={value} disabled={env || !info} onChange={(e) => setValue(e.target.value)} />
        </label>
        <button disabled={busy || !ok || env || !info || n === info.next} onClick={save}>
          Save port
        </button>
      </div>
      {value !== "" && !ok && <p className="error">A whole number from 1024 to 65535.</p>}
      {env && <p className="muted">The EFFIGY_PORT setting chooses the port: change it there.</p>}
      {info && info.next !== info.running && (
        <p className="note">
          Starts at http://127.0.0.1:{info.next} next time: close the run.bat window and start it again, the browser opens there. Agents connected over MCP
          follow on their next start.
        </p>
      )}
      {err && <p className="error">{err}</p>}
    </section>
  );
}

function ConnectAgent() {
  const [setup, setSetup] = useState<AgentSetup | null>(null);
  const [err, setErr] = useState("");
  const [pick, setPick] = useState("claude-code");
  const [copied, setCopied] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; tools?: string[]; error?: string } | null>(null);
  const [busy, run] = useBusy();
  useEffect(() => {
    api.agentSetup().then(setSetup, (e) => setErr(explain(e)));
  }, []);
  const c = setup?.clients.find((x) => x.id === pick) ?? setup?.clients[0];
  return (
    <section aria-label="Connect an agent">
      <h2>Connect an agent</h2>
      <p className="muted">
        Claude, Codex or another AI agent can use this app over MCP: it imports your files, runs the steps, looks at the results as pictures, checks
        them and exports into your game project. Every step it runs appears here as a new version, and it cannot delete anything.
      </p>
      {err && <p className="note warn">{err}</p>}
      {setup && c && (
        <div className="stack agent-connect">
          <div className="row" role="group" aria-label="Agent">
            {setup.clients.map((x) => (
              <button key={x.id} className="small" aria-pressed={x.id === c.id} onClick={() => (setPick(x.id), setCopied(false))}>
                {x.name}
              </button>
            ))}
          </div>
          <p>{c.how}</p>
          <pre aria-label={`${c.name} setup`}>{c.text}</pre>
          <div className="row">
            <button onClick={() => navigator.clipboard.writeText(c.text).then(() => setCopied(true))}>{copied ? "Copied" : "Copy"}</button>
            <button disabled={busy} onClick={() => run(() => api.agentTest().then(setTest, (e) => setTest({ ok: false, error: explain(e) })))}>
              {busy ? "Testing…" : "Test the connection"}
            </button>
          </div>
          {test &&
            (test.ok ? (
              <p className="note">
                The app's MCP server starts and offers {test.tools?.length} tools ({test.tools?.join(", ")}). Restart your agent after adding it.
              </p>
            ) : (
              <p className="note warn">The MCP server did not start: {test.error}</p>
            ))}
          {!setup.uv && <p className="note warn">uv was not found on this computer: the agent needs it to start the app's MCP server.</p>}
          <p className="muted">The app itself has to be running (run.bat) while the agent works.</p>
        </div>
      )}
    </section>
  );
}

const gb = (bytes: number) => `${(bytes / 1e9).toFixed(2)} GB`;
// what each install group enables (tools/download_models.py groups)
// in workflow order
const CAPABILITIES: [string, string, string][] = [
  ["concepts", "Concept images", "Makes four pictures from your description."],
  ["faithful", "3D model, Faithful (recommended)", "Makes the 3D model from your picture. This is the recommended one."],
  ["fast", "3D model, Fast (alternative)", "Makes the 3D model from your picture with less memory. Use it if Faithful does not run."],
  ["multiview", "3D model from four views", "Makes the Faithful 3D model from a four-view turnaround of your character or object instead of one picture (22 GB more)."],
  ["texture", "Face detail", "Sharpens your picture before it is copied onto the face."],
  ["refine", "Texture detail", "Redraws a texture's fine detail, such as buttons, stitching and wood grain (Z-Image + Tile, 2 GB more)."],
  [
    "refine-full",
    "Texture detail, full",
    "The full Tile control net: cleaner detail than the one above, used by itself once it is here (6.7 GB more). For cards with 24 GB of video memory.",
  ],
  ["parts", "Parts", "Finds the pieces of a model from a click and names them; the game files keep each piece as its own object (GeoSAM2, 0.6 GB)."],
  ["canon-flux", "Standing pose", "Puts a character picture into a standing A-pose before its 3D model is made, so it rigs well (FLUX.2 klein)."],
  ["pose", "Character skeleton", "Finds a character's joints to fit its skeleton (DWPose, 0.1 GB)."],
  ["skin", "Learned skinning", "Attaches the mesh to the skeleton with learned weights (SkinTokens). Optional: Blender's bone heat works without it."],
  ["clips", "Ready-made animations", "Idle, walk, run and dozens more for people, and each animal's own (Mesh2Motion, CC0)."],
  ["kimodo", "Text to motion", "Makes an animation from a sentence (NVIDIA Kimodo). Needs a Hugging Face login with access to Meta Llama 3."],
  ["mocap", "Motion from video", "Turns a video of a person into an animation for your character (NVIDIA GEM-X, 7 GB)."],
  ["styles", "2D styles", "Pixel art, classic painting and children's drawing looks for 2D pictures (three Z-Image LoRAs, 0.5 GB)."],
  ["layers", "Parallax layers", "Splits a background picture into far-to-near layers that scroll at their own speeds (MoGe-3 depth, 5 GB)."],
];
// models behind an access gate on Hugging Face: the licence to accept first
const GATED: [string, string][] = [
  ["facebook/dinov3", "DINOv3 License"],
  ["meta-llama/", "Meta Llama 3 Community License"],
];
const gb0 = (bytes: number) => `${Math.max(1, Math.round(bytes / 1e9))} GB`;
const size = (bytes: number) => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`);

// M35a: a warning in the top bar when a drive the app writes to runs low (checked every five minutes): a gauge per drive
function LowSpace() {
  const [low, setLow] = useState<Drive[]>([]);
  useEffect(() => {
    const check = () => api.drives().then((d) => setLow(d.filter((x) => x.low)), () => undefined);
    check();
    const t = setInterval(check, 300_000);
    return () => clearInterval(t);
  }, []);
  if (!low.length) return null;
  const said = low.map((d) => `${d.drive} ${size(d.free)} free`).join(", ");
  return (
    <a href="#/setup" className="low-space" aria-label={`Low disk space: ${said}`} title="Low disk space: open Setup, Space on disk">
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
        <path d="M8 1.5 15 14H1z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
        <path d="M8 6v3.5M8 11.5v.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
      {low.map((d) => (
        <span key={d.drive} className="gauge-item">
          {d.drive.replace(/[\\/]$/, "")}
          <span className="gauge">
            <i style={{ width: `${(100 * (d.total - d.free)) / d.total}%` }} />
          </span>
          {size(d.free)} free
        </span>
      ))}
    </a>
  );
}

// what a cleanup does, for its button and its question
// how a removed environment comes back: its card's Install sets it up (M35b)
const VENV_BACK: Record<string, string> = {
  ".venv-skin": "the app sets it up again when it starts",
  ".venv-hunyuan": "run this in the app's folder: UV_PROJECT_ENVIRONMENT=.venv-hunyuan uv sync --frozen --group hunyuan",
};
const CLEANUP: Record<string, [string, (i: StorageItem) => string]> = {
  trash: ["Empty the trash", (i) => `Delete the trash for good (${size(i.bytes)})? Deleted assets and versions cannot be brought back after this.`],
  prune: ["Clear unused", () => "Clear the downloaded Python packages no environment uses any more?"],
  leftovers: ["Delete", (i) => `Delete what is left of removed environments (${size(i.bytes)})?`],
  group: [
    "Remove",
    (i) => {
      const gs = i.action!.split(":")[1].split("+").map((g) => GROUP_TITLE[g] ?? g);
      return gs.length > 1
        ? `Remove the model files ${gs.join(" and ")} share (${size(i.bytes)})? Both stop working until you install them again here.`
        : `Remove the model files for ${gs[0]} (${size(i.bytes)})? It stops working until you install it again here.`;
    },
  ],
  venv: [
    "Remove",
    (i) => {
      const v = i.action!.split(":")[1];
      return `Remove ${i.name} (${size(i.bytes)})? That feature stops working. To use it again, ${VENV_BACK[v] ?? "click Install on its card above"}.`;
    },
  ],
};

// M35a: where the disk space goes, one group per kind, biggest first in each, and the cleanups that are safe
const SPACE_GROUPS: [string, string][] = [
  ["models", "Model files"],
  ["venv", "Python environments"],
  ["library", "Your library"],
  ["tools", "Tools"],
];
const groupOf = (i: StorageItem) => (i.kind === "cache" ? "venv" : i.kind);
// the features' names for the model groups, as on the cards; the groups no card names
const GROUP_TITLE: Record<string, string> = {
  ...Object.fromEntries(CAPABILITIES.map(([g, t]) => [g, t.replace(/ \(.*\)$/, "")])),
  pose: "A-pose check",
  "canon-flux": "Re-posing",
  clips: "Ready-made animation clips",
  skin: "Skinning with SkinTokens",
};
// a row's name under its group's heading: the feature, not the folder
const spaceName = (i: StorageItem) => {
  const [kind, what] = i.id.split(":");
  if (kind === "models")
    return what === "other" ? "Other files (caches, leftovers)" : what.includes("+") ? `Shared by ${what.split("+").map((g) => GROUP_TITLE[g] ?? g).join(" and ")}` : (GROUP_TITLE[what] ?? what);
  return i.name.startsWith("Python for ") ? i.name[11].toUpperCase() + i.name.slice(12) : i.name;
};

function Space(props: { reload: () => void }) {
  const [st, setSt] = useState<Storage | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const load = (refresh = false) => {
    setBusy(true);
    api
      .storage(refresh)
      .then(setSt, (e) => setNote(String(e)))
      .finally(() => setBusy(false));
  };
  useEffect(() => load(), []);
  const clean = (i: StorageItem) => run(i.action!, CLEANUP[i.action!.split(":")[0]][1](i));
  const run = (action: string, question: string) => {
    if (!confirm(question)) return;
    setBusy(true);
    api
      .cleanup(action)
      .then(
        (s) => (
          setSt(s),
          setNote(`Freed ${size(s.freed ?? 0)}.${s.failed ? ` ${s.failed} files could not be deleted (in use?): they stay, try again later.` : ""}`),
          props.reload()
        ),
        (e) => setNote(String(e)),
      )
      .finally(() => setBusy(false));
  };
  if (!st)
    return (
      <section id="space">
        <h2>Space on disk</h2>
        <p className="muted">{busy ? "Measuring…" : note}</p>
      </section>
    );
  const largest = Math.max(1, ...st.items.map((i) => i.bytes));
  const total = (items: StorageItem[]) => items.reduce((t, i) => t + i.bytes, 0);
  const row = (key: string, name: string, kind: string, bytes: number, button: React.ReactNode, sub = false) => (
    <tr key={key} className={sub ? "item sub" : "item"}>
      <td className="name" title={name}>
        {name}
      </td>
      <td className="share" aria-hidden>
        <span className="mini">
          <i className={kind} style={{ width: `${(100 * bytes) / largest}%` }} />
        </span>
      </td>
      <td className="num">{size(bytes)}</td>
      <td className="act">{button}</td>
    </tr>
  );
  return (
    <section id="space">
      <h2>Space on disk</h2>
      <p className="muted">What this app keeps on your drives. You can remove model files and environments you do not use and install them again here later.</p>
      <div className="drives">
        {st.drives.map((d) => {
          const mine = st.items.filter((i) => i.drive === d.drive);
          const parts = SPACE_GROUPS.map(([k, name]) => [k, name, total(mine.filter((i) => groupOf(i) === k))] as const).filter((p) => p[2] > 0);
          const other = Math.max(d.total - d.free - total(mine), 0);
          const pct = (b: number) => `${(100 * b) / d.total}%`;
          return (
            <div key={d.drive} className={d.low ? "drive low" : "drive"}>
              <div className="drive-head">
                <strong>{d.drive}</strong>
                <span>
                  {size(d.free)} free of {size(d.total)}
                </span>
              </div>
              <div className="drive-bar" role="img" aria-label={`${d.drive}: this app ${size(total(mine))}, other files ${size(other)}, free ${size(d.free)}`}>
                {parts.map(([k, name, b]) => (
                  <i key={k} className={k} style={{ width: pct(b) }} title={`${name}: ${size(b)}`} />
                ))}
                <i className="other" style={{ width: pct(other) }} title={`Other files on ${d.drive}: ${size(other)}`} />
              </div>
              <ul className="legend">
                {parts.map(([k, name, b]) => (
                  <li key={k}>
                    <span className={`swatch ${k}`} />
                    {name} <span className="muted">{size(b)}</span>
                  </li>
                ))}
                <li>
                  <span className="swatch other" />
                  Other files <span className="muted">{size(other)}</span>
                </li>
                <li>
                  <span className="swatch free" />
                  Free <span className="muted">{size(d.free)}</span>
                </li>
              </ul>
              {d.low && <p className="note warn">Space is low on {d.drive}: downloads and new versions may fail. Remove what you do not need below.</p>}
            </div>
          );
        })}
      </div>
      <table className="space-table">
        <colgroup>
          <col />
          <col className="c-share" />
          <col className="c-size" />
          <col className="c-act" />
        </colgroup>
        {SPACE_GROUPS.map(([k, name]) => {
          const items = st.items.filter((i) => groupOf(i) === k);
          if (!items.length) return null;
          return (
            <tbody key={k}>
              <tr className="group">
                <th colSpan={2}>
                  <span className={`swatch ${k}`} />
                  {name}
                </th>
                <th className="num">{size(total(items))}</th>
                <th />
              </tr>
              {items.flatMap((i) => [
                row(
                  i.id,
                  spaceName(i),
                  k,
                  i.bytes,
                  i.action && i.bytes > 0 && (
                    <button disabled={busy} onClick={() => clean(i)}>
                      {CLEANUP[i.action.split(":")[0]][0]}
                    </button>
                  ),
                ),
                ...(i.id === "library:archived"
                  ? st.assets
                      .filter((a) => a.archived)
                      .map((a) =>
                        row(
                          `archived:${a.id}`,
                          a.name,
                          k,
                          a.bytes,
                          <button disabled={busy} onClick={() => run(`archived:${a.id}`, `Delete "${a.name}" (${size(a.bytes)}) for good? It cannot be brought back.`)}>
                            Delete for good
                          </button>,
                          true,
                        ),
                      )
                  : []),
              ])}
            </tbody>
          );
        })}
      </table>
      <div className="space-foot">
        <span className="muted">
          {note && `${note} `}Measured {Math.max(0, Math.round((Date.now() / 1000 - st.scanned) / 60))} min ago.
        </span>
        <button disabled={busy} onClick={() => load(true)}>
          {busy ? "Measuring…" : "Measure again"}
        </button>
      </div>
    </section>
  );
}

// M35c: where the model files go: chosen in the first run, moved later from Setup (a job: steps wait)
function ModelsFolder(props: {
  jobs: Record<string, Job>;
  track: (job_id: string, step: string) => void;
  guard: (p: Promise<unknown>) => Promise<void>;
  onChange?: (f: Folder) => void;
}) {
  const [f, setF] = useState<Folder | null>(null);
  const [path, setPath] = useState("");
  const [note, setNote] = useState("");
  const moving = Object.values(props.jobs).find((j) => j.step === "move models" && (j.status === "queued" || j.status === "running"));
  const load = () =>
    api.folder().then(
      (x) => (setF(x), setPath(x.path), props.onChange?.(x)),
      () => undefined,
    );
  useEffect(() => {
    if (!moving) load();
  }, [!!moving]);
  if (!f) return <p className="muted">Checking…</p>;
  if (f.source === "env") return <p className="muted">The models are in {f.path} (set by EFFIGY_MODELS).</p>;
  const moves = f.bytes > 0;
  const choose = () => {
    if (moves && !confirm(`Move the model files (${size(f.bytes)}) to ${path}?\nGenerating waits until they are there; on another drive that takes a while.`)) return;
    props.guard(
      api.chooseFolder(path).then((r) => {
        if (r.job_id) props.track(r.job_id, "move models");
        else setNote(`The models go into ${r.path} now.${r.left ? ` The models in ${r.left} stay there: delete that folder if you no longer need it.` : ""}`), load();
      }),
    );
  };
  return (
    <div className="folder">
      <div className="folder-list" role="radiogroup" aria-label="Models folder">
        {f.choices.map((c) => (
          <label key={c.path} className="folder-option">
            <input type="radio" name="models-folder" checked={path === c.path} onChange={() => setPath(c.path)} />
            <code>{c.path}</code>
            <span className="muted">{c.path === f.path ? (moves ? `now, ${size(f.bytes)} of models · ` : "now · ") : ""}</span>
            <span className="num muted">{size(c.free)} free</span>
          </label>
        ))}
      </div>
      <div className="row">
        <input aria-label="Another folder" placeholder="Another folder, e.g. E:\Effigy-models" value={f.choices.some((c) => c.path === path) ? "" : path} onChange={(e) => setPath(e.target.value || f.path)} />
        <button disabled={!!moving || path === f.path} onClick={choose}>
          {moves ? "Move the models there" : "Use this folder"}
        </button>
      </div>
      {moving && (
        <p className="muted">
          Moving the models… {moving.text ?? ""} Generating waits until they are there.
        </p>
      )}
      {note && <p className="muted">{note}</p>}
    </div>
  );
}

// M35c: the guided first run: the computer, the models folder, what you want to make, the Hugging Face login, one install queue
const LEVEL_DOT = { ok: "ok", warn: "busy", bad: "bad" };
const took = (bytes: number, mbit: number) => {
  const min = (bytes * 8) / (mbit * 1e6) / 60;
  return min < 90 ? `${Math.max(1, Math.round(min / 5) * 5)} min` : `${(min / 60).toFixed(min < 600 ? 1 : 0)} h`;
};

/** Hugging Face: the login state, the gated licences of `groups` with their access, and the token login (the guided
 * setup's and Setup's own, one wording: UX-12). The token goes into Hugging Face's own login on this computer. */
function HfLogin(props: { hf: HfStatus | null; refresh: () => void; guard: (p: Promise<unknown>) => Promise<void>; groups: string[] }) {
  const [token, setToken] = useState(""); // in memory only, cleared once sent
  const [note, setNote] = useState("");
  const hf = props.hf;
  if (!hf) return <p className="muted">Checking…</p>;
  const repos = Object.entries(hf.gated)
    .filter(([, groups]) => groups.some((g) => props.groups.includes(g)))
    .map(([repo]) => repo);
  const licence = (repo: string) => GATED.find(([p]) => repo.startsWith(p))?.[1] ?? "licence";
  const login = () =>
    props.guard(
      api
        .hfLogin(token)
        .then((r) => (setNote(`Logged in as ${r.user}.`), props.refresh()))
        .finally(() => setToken("")),
    );
  return (
    <>
      <p className="status">
        <i className={`dot ${hf.user ? "ok" : "off"}`} />
        {hf.user ? `Logged in as ${hf.user}` : hf.token ? "Logged in (Hugging Face did not answer)" : "Not logged in"}
        {note && <span className="muted"> · {note}</span>}
      </p>
      <p className="muted">Some models are shared only with people who accept their licence on Hugging Face, the site the models come from.</p>
      <ol>
        {!hf.user && (
          <li>
            Create a free account: <a className="button small" href="https://huggingface.co/join" target="_blank" rel="noreferrer">huggingface.co/join</a>
          </li>
        )}
        {repos.map((repo) => (
          <li key={repo}>
            Accept the {licence(repo)}, signed in: <a className="button small" href={`https://huggingface.co/${repo}`} target="_blank" rel="noreferrer">huggingface.co/{repo}</a>{" "}
            <span className="muted">
              · {hf.access[repo] ? "accepted" : hf.access[repo] === false ? "not yet (approval can take a few hours)" : hf.user ? "unknown" : "log in to check"}
            </span>
          </li>
        ))}
        {!hf.user && (
          <li>
            Create a token of type Read at <a className="button small" href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer">huggingface.co/settings/tokens</a>, copy it,
            paste it here and log in:
            <div className="row">
              <input type="password" autoComplete="off" spellCheck={false} aria-label="Hugging Face token" placeholder="hf_…" value={token} onChange={(e) => setToken(e.target.value)} />
              <button disabled={!token.trim()} onClick={login}>
                Log in
              </button>
            </div>
          </li>
        )}
      </ol>
      <p className="muted">
        The token goes only into Hugging Face's own login on this computer (where <code>hf auth login</code> keeps it). This app does not store it.{" "}
        <button className="small" onClick={props.refresh}>
          Check again
        </button>
        {hf.user && (
          <button className="small" onClick={() => props.guard(api.hfLogout().then(props.refresh))}>
            Log out
          </button>
        )}
      </p>
    </>
  );
}

function Welcome(props: {
  models: Model[] | null;
  reload: () => void;
  jobs: Record<string, Job>;
  track: (job_id: string, step: string) => void;
  guard: (p: Promise<unknown>) => Promise<void>;
}) {
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [envs, setEnvs] = useState<Env[]>([]);
  const [folder, setFolder] = useState<Folder | null>(null);
  const [pick, setPick] = useState("characters");
  const [hf, setHf] = useState<HfStatus | null>(null);
  const installs = Object.values(props.jobs).filter((j) => j.step?.startsWith("install "));
  const busy = installs.some((j) => j.status === "queued" || j.status === "running");
  const checkHf = () => api.hf().then(setHf, () => setHf({ token: false, user: null, access: {}, gated: {} }));
  useEffect(() => {
    api.machine().then(setChecks, () => setChecks([]));
    checkHf();
  }, []);
  useEffect(() => {
    if (busy) return;
    api.presets().then(setPresets, () => undefined); // again when an install ends
    api.environments().then(setEnvs, () => undefined);
    props.reload();
  }, [busy]);
  const preset = presets?.find((p) => p.id === pick);
  const free = folder?.choices.find((c) => c.path === folder.path)?.free;
  const gated = Object.entries(hf?.gated ?? {}).filter(([, groups]) => groups.some((g) => preset?.groups.includes(g)));
  const done = (g: string) => {
    const rows = (props.models ?? []).filter((m) => m.groups.includes(g));
    const env = envs.find((e) => e.groups.includes(g));
    return rows.length > 0 && rows.every((m) => m.installed) && (!env || env.state === "ok" || env.state === "unknown");
  };
  const job = (g: string) => installs.filter((j) => j.step === `install ${g}`).at(-1);
  const todo = (preset?.groups ?? []).filter((g) => !done(g));
  const start = async () => {
    api.welcomed().catch(() => undefined); // decided: the app stops opening this page by itself
    for (const g of todo) {
      const j = job(g);
      if (j && (j.status === "queued" || j.status === "running")) continue;
      await props.guard(api.installModels(g).then(({ job_id }) => props.track(job_id, `install ${g}`)));
    }
  };
  return (
    <div className="page setup welcome">
      <div className="head">
        <h1>Get started</h1>
      </div>
      <p className="summary">Five steps, about ten minutes of your time, then the downloads run on their own. Everything here can be changed later in Setup.</p>

      <section>
        <h2>
          <span className="n">1</span> Your computer
        </h2>
        {!checks ? (
          <p className="muted">Checking…</p>
        ) : (
          <ul className="checks">
            {checks.map((c) => (
              <li key={c.id}>
                <i className={`dot ${LEVEL_DOT[c.level]}`} />
                <span>
                  {c.label}
                  {c.detail && <span className="muted"> · {c.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>
          <span className="n">2</span> Where the models go
        </h2>
        <p className="muted">The model files are big (60 to 140 GB). Choose a drive with room; an SSD loads them faster.</p>
        <ModelsFolder jobs={props.jobs} track={props.track} guard={props.guard} onChange={setFolder} />
      </section>

      <section>
        <h2>
          <span className="n">3</span> What you want to make
        </h2>
        <div className="presets" role="radiogroup" aria-label="What you want to make">
          {(presets ?? []).map((p) => (
            <label key={p.id} className="card choice">
              <input type="radio" name="preset" checked={p.id === pick} onChange={() => setPick(p.id)} />
              <strong>{p.name}</strong>
              <span className="muted">{p.what}</span>
              <span className="status">
                {p.missing ? `${size(p.missing)} to download` : "Installed"}
                {p.missing > 0 && (
                  <span className="muted">
                    {" "}
                    · about {took(p.missing, 500)} at 500 Mbit/s, {took(p.missing, 50)} at 50 Mbit/s
                  </span>
                )}
              </span>
            </label>
          ))}
        </div>
        {preset && free != null && preset.missing + 2 ** 30 > free && (
          <p className="note warn">
            {size(preset.missing)} do not fit on this drive ({size(free)} free): choose another folder in step 2, or a smaller set.
          </p>
        )}
      </section>

      <section>
        <h2>
          <span className="n">4</span> Hugging Face
        </h2>
        <HfLogin hf={hf} refresh={checkHf} guard={props.guard} groups={preset?.groups ?? []} />
      </section>

      <section>
        <h2>
          <span className="n">5</span> Install
        </h2>
        {preset && (
          <>
            <ul className="checks">
              {preset.groups.map((g) => {
                const j = job(g);
                const run = j && (j.status === "queued" || j.status === "running");
                return (
                  <li key={g}>
                    <i className={`dot ${done(g) ? "ok" : run ? "busy" : j?.status === "failed" ? "bad" : "off"}`} />
                    <span className="grow">{GROUP_TITLE[g] ?? g}</span>
                    <span className="muted">
                      {done(g) ? "installed" : j?.status === "queued" ? "waiting" : j?.status === "running" ? (j.text ?? "installing") : j?.status === "failed" ? (j.error?.message ?? "failed") : ""}
                    </span>
                    {j?.status === "running" && (
                      <span className="progress" aria-hidden>
                        <i style={{ width: `${Math.round((j.p ?? 0) * 100)}%` }} />
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
            {todo.length > 0 && gated.some(([repo]) => !hf?.access[repo]) && (
              <p className="muted">
                A licence in step 4 is not accepted yet: everything else downloads now; once Hugging Face approves it, click Install again for the rest.
              </p>
            )}
            {todo.length ? (
              <button className="primary" disabled={busy} onClick={start}>
                {busy ? "Installing…" : `Install ${preset.name} (${size(preset.missing)} to download)`}
              </button>
            ) : (
              <p>
                Ready. <a href="#/new" className="button primary">Make your first asset</a>
              </p>
            )}
            {busy && <p className="muted">You can close this page: the downloads go on, the activity bar at the bottom shows them.</p>}
          </>
        )}
      </section>
      <p className="muted">
        <a className="button small" href="#/library" onClick={() => void api.welcomed().catch(() => undefined)}>
          Skip for now
        </a>
        : you can install features one by one in Setup, or come back here from Setup.
      </p>
    </div>
  );
}

function Setup(props: {
  models: Model[] | null;
  reload: () => void;
  jobs: Record<string, Job>;
  track: (job_id: string, step: string) => void;
  guard: (p: Promise<unknown>) => Promise<void>;
  cap?: string; // a capability's card to show (a blocked action's link, UX-12)
}) {
  const [licences, setLicences] = useState<Licence[]>([]);
  const [envs, setEnvs] = useState<Env[] | null>(null);
  const [presets, setPresets] = useState<Preset[] | null>(null);
  const [hf, setHf] = useState<HfStatus | null>(null);
  const [gpu, setGpu] = useState<Check | null>(null); // M46c: a group this card cannot use is not offered
  const checkHf = () => api.hf().then(setHf, () => setHf({ token: false, user: null, access: {}, gated: {} }));
  useEffect(() => {
    checkHf();
    api.machine().then((c) => setGpu(c.find((x) => x.id === "gpu") ?? null), () => undefined);
  }, []);
  useEffect(() => {
    if (!props.cap || !props.models) return;
    const card = document.getElementById(`cap-${props.cap}`);
    card?.scrollIntoView({ block: "center" });
    card?.focus({ preventScroll: true });
  }, [props.cap, !!props.models]);
  const [link, setLink] = useState<"pro" | "free" | null | undefined>(undefined);
  const check = () =>
    api
      .blenderStatus()
      .then((s) => setLink(s.link))
      .catch(() => setLink(null));
  useEffect(() => {
    props.reload();
    props.guard(api.licenses().then(setLicences));
    check();
  }, []);
  const models = props.models ?? [];
  const downloads = Object.values(props.jobs).filter((j) => j.step?.startsWith("install "));
  const busy = downloads.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (busy) return; // again when an install ends
    api.environments().then(setEnvs, () => setEnvs([]));
    api.presets().then(setPresets, () => undefined);
  }, [busy]);
  const lic = Object.fromEntries(licences.map((r) => [r.name, r]));
  const install = (g: string, repair = false) => props.guard(api.installModels(g, repair).then(({ job_id }) => props.track(job_id, `install ${g}`)));
  // installed = every pinned file there at its size and the feature's environment in step with uv.lock (M35b)
  const status = (g: string) => {
    const job = downloads.filter((j) => j.step === `install ${g}`).at(-1);
    const rows = models.filter((m) => m.groups.includes(g));
    // a gated model needs the login only while this account may not read its repo (unknown counts as not)
    const allowed = (m: Model) => Object.entries(hf?.access ?? {}).some(([repo, ok]) => ok && m.name.startsWith(repo));
    const gate = rows.find((m) => !m.installed && GATED.some(([p]) => m.name.startsWith(p)) && !allowed(m));
    const here = rows.filter((m) => m.installed).reduce((t, m) => t + m.bytes, 0);
    const missing = rows.reduce((t, m) => t + m.missing_bytes, 0);
    const env = envs?.find((e) => e.groups.includes(g));
    const envNote = env?.state === "missing" ? "its Python environment is not set up" : env?.state === "outdated" ? "its Python environment needs an update" : "";
    const complete = rows.length > 0 && rows.every((m) => m.installed);
    const key = (dot: string, text: string, act?: [string, boolean], title?: string) => ({ dot, text, act, title, ok: dot === "ok", job });
    if (!props.models || !envs) return key("off", "Checking…");
    const big = gpu?.too_big?.find((t) => t.group === g);
    if (big && !complete) return key("off", `Needs a bigger card · about ${big.need_gb} GB of video memory, this one allows ${gpu?.budget_gb} GB`);
    if (job?.status === "queued") return key("busy", "Queued · starts after the install before it");
    if (job?.status === "running") return key("busy", `Installing · ${job.text ?? "starting"}`);
    if (complete && !envNote) return key("ok", `Installed · ${gb0(here)}`, undefined, "Every file is there at its size; Check and repair also checks their fingerprints.");
    if (job?.status === "failed" && job.error?.code !== "gated") return key("bad", "Install failed · try again", ["Try again", false], job.error?.message);
    if (gate || job?.error?.code === "gated") return key("off", "Needs a Hugging Face login first", ["Install", false]);
    if (complete) return key("bad", `Needs repair · ${envNote}`, ["Repair", false]);
    if (rows.some((m) => m.partial)) return key("bad", `Needs repair · ${size(missing)} missing or damaged`, ["Repair", true]);
    return key("off", `Not installed · ${size(missing)} to download`, ["Install", false]);
  };
  const feats = CAPABILITIES.map(([g, title, what]) => ({ g, title, what, st: status(g) }));
  const need = feats.filter((f) => !f.st.ok).length;
  // what the user can make now, by the guided setup's sets; the rest is optional (UX-12)
  const ready = (presets ?? []).filter((p) => p.id !== "everything" && p.groups.every((g) => status(g).ok));
  const absent = feats.filter((f) => !f.st.ok && !f.st.job);
  const onDisk = models.filter((m) => m.installed).reduce((t, m) => t + m.bytes, 0);
  const blender = link === undefined ? "" : link ? " Blender is connected." : " Blender is not connected.";
  return (
    <div className="page setup">
      <div className="head">
        <h1>Setup</h1>
      </div>
      {props.models && (
        <p className="summary">
          {need === 0
            ? `Everything is installed (${gb0(onDisk)} on disk).`
            : !presets
              ? `${need} of ${feats.length} features are not ready yet: install or repair them below.`
              : `${ready.length ? `Ready for ${ready.map((p) => p.name.toLowerCase()).join(", ")}.` : "Nothing is ready yet: the guided setup installs a whole set at once."}${
                  absent.length ? ` Not installed, each optional until you want it: ${absent.map((f) => f.title).join(", ")}.` : ""
                }`}
          {blender}
        </p>
      )}
      <div className="note get-started">
        <span>
          <strong>Setting up for the first time, or on another computer?</strong> The guided setup checks your computer, asks where the models go and
          what you want to make, logs you in to Hugging Face and installs it all in one go.
        </span>
        <a className="button primary" href="#/welcome">
          Open the guided setup
        </a>
      </div>
      <h2>What this app can do</h2>
      <p className="muted">Each feature needs some files on this computer. Install the ones you want to use.</p>
      <div className="cards caps">
        {feats.map(({ g, title, what, st }) => {
          const gate = models.find((m) => m.groups.includes(g) && GATED.some(([p]) => m.name.startsWith(p)));
          const licence = gate && GATED.find(([p]) => gate.name.startsWith(p))![1];
          return (
            <div key={g} className={`card${props.cap === g ? " target" : ""}`} id={`cap-${g}`} tabIndex={-1}>
              <strong>{title}</strong>
              <span className="muted">{what}</span>
              <span className="status" title={st.title}>
                <i className={`dot ${st.dot}`} />
                {st.text}
              </span>
              {st.job?.status === "running" && (
                <span className="progress" aria-hidden>
                  <i style={{ width: `${Math.round((st.job.p ?? 0) * 100)}%` }} />
                </span>
              )}
              {st.act && (
                <button onClick={() => install(g, st.act![1])}>
                  {st.act[0]}
                </button>
              )}
              {st.job?.status === "queued" && <button onClick={() => props.guard(api.cancel(st.job!.job_id))}>Cancel</button>}
              {st.ok && (
                <button className="small" onClick={() => install(g, true)}>
                  Check and repair
                </button>
              )}
              {gate && !gate.installed && (
                <details>
                  <summary>Before you install</summary>
                  <p className="muted">The {licence} has to be accepted on Hugging Face first, signed in; then log in here and click Install.</p>
                  <HfLogin hf={hf} refresh={checkHf} guard={props.guard} groups={[g]} />
                </details>
              )}
            </div>
          );
        })}
      </div>
      {busy && <p className="muted">You can follow the install in the activity bar below. Other installs you start wait their turn.</p>}
      <h2>Where the models are</h2>
      <ModelsFolder jobs={props.jobs} track={props.track} guard={props.guard} />
      <Space reload={props.reload} />
      <h2>Tools</h2>
      <p className="muted">Other programs the app can work with.</p>
      <div className="cards caps">
        <div className="card">
          <strong>Blender connection</strong>
          <span className="muted">Needed for Edit in Blender.</span>
          <span className="status">
            <i className={`dot ${link ? "ok" : "off"}`} />
            {link === undefined
              ? "Checking…"
              : link
                ? `Connected: ${link === "pro" ? "Blender MCP Pro" : "blender-mcp"}`
                : "Not connected · open Blender and start its add-on server"}
          </span>
          <button onClick={check}>Check again</button>
        </div>
      </div>
      <BlenderStart />
      <ConnectAgent />
      <AppPort />
      <h2>Credits for your game</h2>
      <p className="muted">When you export, these lines are written into CREDITS.txt. Put them in your game's credits.</p>
      <div id="attributions" className="note">
        <ul>
          {licences
            .filter((r) => r.attribution)
            .map((r) => (
              <li key={r.name}>
                <strong className="attribution">{r.attribution}</strong>{" "}
                <span className="muted">
                  ({r.name}, {r.license})
                </span>
              </li>
            ))}
        </ul>
      </div>
      <h2>Advanced</h2>
      <details>
        <summary>Model files and licences</summary>
        <div className="stack">
          <p className="muted">Which file each feature uses, and every licence. Install a feature on its card above; remove files under Space on disk.</p>
          <table id="model-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Used by</th>
                <th>Folder</th>
                <th>On disk</th>
                <th>Licence</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <tr key={m.id}>
                  <td title={m.revision}>{m.name}</td>
                  <td>
                    {m.groups.join(", ")}
                    {m.groups.length > 1 && <span className="tag">shared</span>}
                  </td>
                  <td>models/{m.where}</td>
                  <td>{m.installed ? gb(m.bytes) : <span className="muted">{m.partial ? `incomplete (${gb(m.bytes)})` : "not found"}</span>}</td>
                  <td>
                    {lic[m.name]?.license}
                    {lic[m.name]?.attribution && <span className="tag">{lic[m.name].attribution}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3>All licences ({licences.length})</h3>
          <table id="licence-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Licence</th>
                <th>Commercial</th>
                <th>Attribution</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {licences.map((r) => (
                <tr key={r.name}>
                  <td>{r.name}</td>
                  <td>{r.kind}</td>
                  <td>{r.license}</td>
                  <td>{r.commercial ? "yes" : "no"}</td>
                  <td className="attribution">{r.attribution}</td>
                  <td>{[r.experimental && "experimental", r.region_note, r.flag].filter(Boolean).join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}

function Jobs(props: { jobs: Record<string, Job>; assetName: (id?: string) => string; onCancel: (id: string) => void }) {
  const list = Object.values(props.jobs).reverse();
  const row = (j: Job) => (
    <details key={j.job_id}>
      <summary>
        {j.step ?? j.job_id} · {j.status}
        {j.asset && (
          <span className="muted">
            {" "}
            on {props.assetName(j.asset)} {j.input?.slice(0, 3)}
          </span>
        )}
        {j.of && <span className="muted"> · step {j.of}</span>}
        {j.status === "running" && (j.p != null ? <progress value={j.p} /> : <progress />)}
        {j.text && <span className="muted"> {j.text}</span>}
        {(j.status === "queued" || j.status === "running") && <button onClick={() => props.onCancel(j.job_id)}>Cancel</button>}
        {j.error && (
          <span className="error">
            {" "}
            {j.error.code}: {j.error.message}
            {j.error.suggest && ` · try ${JSON.stringify(j.error.suggest)}`}
          </span>
        )}
      </summary>
      <pre>{j.log.join("\n") || "no log lines"}</pre>
    </details>
  );
  return (
    <footer aria-label="Activity">
      {list.length === 0 ? <span className="muted">No activity yet.</span> : row(list[0])}
      {list.length > 1 && (
        <details>
          <summary>Earlier activity ({list.length - 1})</summary>
          {list.slice(1).map(row)}
        </details>
      )}
    </footer>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
