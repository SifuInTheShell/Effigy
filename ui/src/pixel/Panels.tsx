// The 2D track's panels (research 15 §5, M50/M51): pictures from a description (concepts op image), pixel art from a
// picture or frames (pixelize), the palette and a recolour. Plain forms over the steps; the chain runs and opens them.
import { useEffect, useState } from "react";
import { api, fileUrl, type Meta } from "../api";
import { useBusy } from "../hooks";
import { ancestors } from "../workflow";
import { is2D, pictureKeys } from "./View";

type Chain = (asset: string, parent: string | null, steps: [string, object][]) => Promise<string | null>;
const setupAt = (group: string) => `#/setup?cap=${group}`;

// what each 2D kind frames (steps/brief.py's templates) and the size it starts with
export const KINDS_2D: [string, string, string, string][] = [
  ["picture", "Picture", "your words as they are, nothing added", "1:1"],
  ["wallpaper", "Wallpaper", "a wide scene, no text", "16:9"],
  ["background", "Game background", "a side-scrolling stage with a floor band, no characters", "8:3"],
  ["portrait", "Portrait", "head and shoulders on a plain background", "1:1"],
  ["icon", "Icon", "one bold shape on a plain background", "1:1"],
  ["item", "Item or sprite", "one object, side view, cut out", "1:1"],
  ["tile", "Seamless tile", "a material that repeats in every direction: a wall, a floor", "512"],
];
// steps/concepts.py: multiples of 64, 256 to 2048
export const SIZES: [string, string, number, number][] = [
  ["16:9", "Wide 16:9 (1920 × 1088)", 1920, 1088],
  ["8:3", "Stage strip 8:3 (2048 × 768)", 2048, 768],
  ["1:1", "Square (1024 × 1024)", 1024, 1024],
  ["4:3", "4:3 (1280 × 960)", 1280, 960],
  ["9:16", "Tall 9:16 (1088 × 1920)", 1088, 1920],
  ["custom", "Custom (width × height)", 0, 0],
];
// a seamless tile is square (steps/concepts.py op tile: 256 to 1024)
const TILE_SIZES = [256, 512, 1024];
const SIDE = { min: 256, max: 2048, step: 64 }; // the picture model's bounds per side
/** A typed side snapped to the model's grid: 1080 → 1088, kept inside 256 to 2048. */
export const snapSide = (x: number) => Math.min(SIDE.max, Math.max(SIDE.min, Math.round(x / SIDE.step) * SIDE.step));
const STYLES: [string, string][] = [
  ["", "As described"],
  ["pixel", "Pixel art"],
  ["painting", "Classic painting"],
  ["drawing", "Children's drawing"],
];
const CUT = new Set(["item", "icon"]); // kinds cut out by default

/** The picture settings: kind, size, how many, style, cut-out. `onRun` gets the kind and the concepts params. */
export function PictureForm(props: {
  kind: string;
  onKind: (kind: string) => void;
  missing: (group: string) => boolean;
  ready: boolean; // a description is there
  busy: boolean;
  onRun: (kind: string, params: Record<string, unknown>) => void;
}) {
  const startSize = (k: string) => KINDS_2D.find(([id]) => id === k)?.[3] ?? "1:1";
  const [size, setSize] = useState(startSize(props.kind));
  const [count, setCount] = useState(["picture", "wallpaper", "background"].includes(props.kind) ? 2 : 4);
  const [style, setStyle] = useState("");
  const [cutout, setCutout] = useState(CUT.has(props.kind));
  const [custom, setCustom] = useState({ w: "1280", h: "720" });
  const tile = props.kind === "tile";
  const pick = (k: string) => (props.onKind(k), setSize(startSize(k)), setCutout(CUT.has(k)));
  const [, , w0, h0] = SIZES.find(([id]) => id === size) ?? SIZES[2];
  const [w, h] = size === "custom" ? [Number(custom.w), Number(custom.h)] : [w0, h0];
  const fits = (x: number) => Number.isInteger(x) && x % SIDE.step === 0 && x >= SIDE.min && x <= SIDE.max;
  const sized = tile || (fits(w) && fits(h));
  const noStyles = props.missing("styles");
  if (props.missing("concepts"))
    return (
      <>
        <p className="note warn">The picture model's files weren't found. Your description stays here while you set them up.</p>
        <a className="button primary" href={setupAt("concepts")}>
          Set up concept images
        </a>
      </>
    );
  return (
    <>
      <fieldset>
        <legend>Kind (adds framing for the picture)</legend>
        <div className="choices">
          {KINDS_2D.map(([id, title, adds]) => (
            <label key={id} className="choice">
              <input type="radio" name="kind-2d" checked={props.kind === id} onChange={() => pick(id)} />
              <span>{title}</span>
              <span className="muted">{adds}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {tile ? (
        <label>
          Tile size
          <select value={TILE_SIZES.includes(Number(size)) ? size : "512"} onChange={(e) => setSize(e.target.value)}>
            {TILE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n} × {n}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label>
          Size
          <select value={size} onChange={(e) => setSize(e.target.value)}>
            {SIZES.map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
      )}
      {!tile && size === "custom" && (
        <>
          <div className="row">
            {(["w", "h"] as const).map((k) => (
              <label key={k}>
                {k === "w" ? "Width (px)" : "Height (px)"}
                <input
                  type="number"
                  min={SIDE.min}
                  max={SIDE.max}
                  step={SIDE.step}
                  value={custom[k]}
                  aria-invalid={!fits(Number(custom[k]))}
                  onChange={(e) => setCustom((c) => ({ ...c, [k]: e.target.value }))}
                  onBlur={(e) => setCustom((c) => ({ ...c, [k]: String(snapSide(Number(e.target.value) || SIDE.min)) }))}
                />
              </label>
            ))}
          </div>
          <p className={sized ? "muted" : "note warn"}>
            From {SIDE.min} to {SIDE.max} px a side, in steps of {SIDE.step} (the picture model's grid): a size in between is rounded when
            you leave the field.
          </p>
        </>
      )}
      {!tile && (
        <label>
          Pictures
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </label>
      )}
      <label>
        Style
        <select value={style} onChange={(e) => setStyle(e.target.value)}>
          {STYLES.map(([id, title]) => (
            <option key={id} value={id} disabled={!!id && noStyles}>
              {title}
            </option>
          ))}
        </select>
      </label>
      {noStyles && (
        <p className="muted">
          The styles are an optional download: <a href={setupAt("styles")}>set up 2D styles</a>.
        </p>
      )}
      {tile ? (
        <p className="muted">Its edges are drawn again so they continue each other: the tile repeats in every direction with no visible seam.</p>
      ) : (
        <label className="check">
          <input type="checkbox" checked={cutout} onChange={(e) => setCutout(e.target.checked)} /> Cut out the subject (a transparent background)
        </label>
      )}
      <button
        className="primary"
        disabled={props.busy || !props.ready || !sized}
        onClick={() =>
          props.onRun(
            props.kind,
            tile
              ? { op: "tile", size: TILE_SIZES.includes(Number(size)) ? Number(size) : 512, ...(style ? { lora: style } : {}) }
              : { op: "image", width: w, height: h, count, ...(style ? { lora: style } : {}), ...(cutout ? { cutout: true } : {}) },
          )
        }
      >
        {props.busy ? "Starting…" : tile ? "Make a seamless tile" : `Make ${count} picture${count > 1 ? "s" : ""}`}
      </button>
    </>
  );
}

/** Pixel art from the shown version's picture (or its picked one) or frames. */
/** M57: a blank sheet to draw on by hand (a hit spark, dust, a barrel's states): its size, frames and colours. `onRun`
 * gets pixelize op edit's params for a sheet without a parent. */
export function DrawForm(props: { busy: boolean; onRun: (params: Record<string, unknown>) => void }) {
  const [side, setSide] = useState({ w: "32", h: "32", n: "4" });
  const [palette, setPalette] = useState("pico-8");
  const ok = (k: "w" | "h" | "n") => {
    const x = Number(side[k]);
    return Number.isInteger(x) && (k === "n" ? x >= 1 && x <= 64 : x >= 4 && x <= 512);
  };
  return (
    <>
      <div className="row">
        {(
          [
            ["w", "Width (px)", 4, 512],
            ["h", "Height (px)", 4, 512],
            ["n", "Frames", 1, 64],
          ] as const
        ).map(([k, label, min, max]) => (
          <label key={k}>
            {label}
            <input type="number" min={min} max={max} value={side[k]} aria-invalid={!ok(k)} onChange={(e) => setSide((s) => ({ ...s, [k]: e.target.value }))} />
          </label>
        ))}
      </div>
      <p className={ok("w") && ok("h") && ok("n") ? "muted" : "note warn"}>4 to 512 px a side, 1 to 64 frames; more frames can be added while drawing.</p>
      <label>
        Colours
        <select value={palette} onChange={(e) => setPalette(e.target.value)}>
          <option value="pico-8">PICO-8 (16 colours)</option>
          <option value="">Any colour</option>
        </select>
      </label>
      <button
        className="primary"
        disabled={props.busy || !(ok("w") && ok("h") && ok("n"))}
        onClick={() =>
          props.onRun({
            op: "edit",
            size: [Number(side.w), Number(side.h)],
            order: Array.from({ length: Number(side.n) }, (_, k) => `frame_${k + 1}`),
            ...(palette ? { palette } : {}),
          })
        }
      >
        {props.busy ? "Starting…" : "Start drawing"}
      </button>
    </>
  );
}

export function PixelizeForm(props: { asset: string; version: Meta; picked: string; chain: Chain; start?: Record<string, unknown> }) {
  // a pixel-style picture is cut on its own grid; anything else to a height: a wide scene to the 640 × 360 playfield's
  // 360 px, a subject to 64 px (research 15 §2)
  const v = props.version;
  const wide = Number(v.params.width) > Number(v.params.height);
  // a seamless tile (M54) is cut into whole tiles: its size in pixels divides the tile's, so the edges still meet
  const tileSide = v.outputs.tile ? Number(v.metrics.size ?? (Array.isArray(v.metrics.cell) ? v.metrics.cell[0] : 0)) : 0;
  const tileSizes = [8, 16, 24, 32, 48, 64, 96, 128, 256].filter((n) => tileSide && n < tileSide && tileSide % n === 0);
  const s =
    props.start ??
    // an eighth of the tile (a 512 tile at 64 px kept its bricks on the M54 bench, at 32 px they melted), the middle
    // colour unless it was drawn pixel-style (the most frequent colour picked the texture's noise)
    (tileSide
      ? { height_px: tileSizes.includes(tileSide / 8) ? tileSide / 8 : tileSizes.at(-1), ...(v.params.lora === "pixel" ? {} : { method: "median" }) }
      : undefined) ??
    (v.step === "sprites"
      ? { scale: Number(v.metrics.supersample ?? 1), colors: 15, outline: "darkest" }
      : v.params.lora === "pixel" || v.step === "pixelize"
        ? {}
        : { height_px: wide ? 360 : 64 });
  const [size, setSize] = useState<"auto" | "height" | "scale">(s.height_px ? "height" : typeof s.scale === "number" ? "scale" : "auto");
  const [height, setHeight] = useState(String(s.height_px ?? 64));
  const [scale, setScale] = useState(String(typeof s.scale === "number" ? s.scale : 8));
  const [palette, setPalette] = useState(String(s.palette ?? ""));
  const [colors, setColors] = useState(String(s.colors ?? 16));
  const [dither, setDither] = useState(String(s.dither ?? "none"));
  const [outline, setOutline] = useState(String(s.outline ?? ""));
  const [method, setMethod] = useState(String(s.method ?? "dominant"));
  const [cut, setCut] = useState(Number(s.alpha_cut ?? 0.5));
  const [busy, run] = useBusy();
  const keys = pictureKeys(props.version);
  const key = keys.length ? (props.version.outputs[props.picked] ? props.picked : keys[0]) : undefined;
  const n = (x: string, lo: number, hi: number) => Number.isInteger(Number(x)) && Number(x) >= lo && Number(x) <= hi;
  const ok = (size === "auto" || (size === "height" ? n(height, 8, 1024) : n(scale, 1, 64))) && (!!palette || n(colors, 2, 64));
  const params = {
    ...(key ? { key } : {}),
    ...(size === "height" ? { height_px: Number(height) } : size === "scale" ? { scale: Number(scale) } : {}),
    ...(palette ? { palette } : { colors: Number(colors) }),
    dither,
    ...(outline ? { outline } : {}),
    method,
    alpha_cut: cut,
  };
  return (
    <div className="stack">
      {key && <p className="muted">From picture {keys.indexOf(key) + 1} of {keys.length}: pick another under the view.</p>}
      {tileSide > 0 ? (
        <fieldset>
          <legend>Tile size</legend>
          <label className="check">
            <input type="radio" name="px-size" checked={size === "auto"} onChange={() => setSize("auto")} /> The grid it was drawn on (pixel-style tiles)
          </label>
          <label className="check">
            <input type="radio" name="px-size" checked={size !== "auto"} onChange={() => setSize("height")} /> Pixels a side
          </label>
          {size !== "auto" && (
            <select aria-label="Pixels a side" value={height} onChange={(e) => (setSize("height"), setHeight(e.target.value))}>
              {tileSizes.map((n) => (
                <option key={n} value={n}>
                  {n} × {n}
                </option>
              ))}
            </select>
          )}
        </fieldset>
      ) : (
      <fieldset>
        <legend>Pixel size</legend>
        <label className="check">
          <input type="radio" name="px-size" checked={size === "auto"} onChange={() => setSize("auto")} /> The grid the picture was drawn on (pixel-style pictures)
        </label>
        <label className="check">
          <input type="radio" name="px-size" checked={size === "height"} onChange={() => setSize("height")} /> A height for the subject
        </label>
        <label className="check">
          <input type="radio" name="px-size" checked={size === "scale"} onChange={() => setSize("scale")} /> A fixed pixel size
        </label>
      </fieldset>
      )}
      {!tileSide && size === "height" && (
        <label>
          Subject's height (px)
          <input type="number" min={8} max={1024} value={height} onChange={(e) => setHeight(e.target.value)} />
        </label>
      )}
      {size === "scale" && (
        <label>
          Picture pixels per pixel
          <input type="number" min={1} max={64} value={scale} onChange={(e) => setScale(e.target.value)} />
        </label>
      )}
      <label>
        Colours
        <select value={palette} onChange={(e) => setPalette(e.target.value)}>
          <option value="">From the picture</option>
          <option value="pico-8">PICO-8 palette (16)</option>
        </select>
      </label>
      {!palette && (
        <label>
          Number of colours
          <input type="number" min={2} max={64} value={colors} onChange={(e) => setColors(e.target.value)} />
        </label>
      )}
      <label>
        Dither
        <select value={dither} onChange={(e) => setDither(e.target.value)}>
          <option value="none">None</option>
          <option value="bayer2">Ordered 2 × 2</option>
          <option value="bayer4">Ordered 4 × 4</option>
        </select>
      </label>
      {!tileSide && (
      <label>
        Outline
        <select value={outline} onChange={(e) => setOutline(e.target.value)}>
          <option value="">None</option>
          <option value="darkest">Darkest colour</option>
          <option value="#000000">Black</option>
        </select>
      </label>
      )}
      <details>
        <summary>More settings</summary>
        <div className="stack">
          <label>
            Each pixel's colour
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="dominant">The most frequent (hard edges, pixel-style pictures)</option>
              <option value="median">The middle value (renders and photos)</option>
            </select>
          </label>
          <label>
            Transparent below {Math.round(cut * 100)} % cover{" "}
            <input type="range" aria-label="Alpha cut" min={0.05} max={0.95} step={0.05} value={cut} onChange={(e) => setCut(Number(e.target.value))} />
          </label>
        </div>
      </details>
      <button className="primary" disabled={busy || !ok} onClick={() => run(() => props.chain(props.asset, props.version.version, [["pixelize", params]]))}>
        {busy ? "Starting…" : "Make pixel art"}
      </button>
    </div>
  );
}

/** palette.gpl → ["#rrggbb", …] (GIMP palette: "R G B name" rows after the header). */
export function readGpl(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim().match(/^(\d+)\s+(\d+)\s+(\d+)/))
    .filter((m): m is RegExpMatchArray => !!m)
    .map((m) => "#" + m.slice(1, 4).map((x) => Number(x).toString(16).padStart(2, "0")).join(""));
}

/** A pixelize version: what it made and its files. */
export function PixelInfo(props: { asset: string; version: Meta }) {
  const v = props.version;
  const m = v.metrics as Record<string, number | number[]>;
  if (v.outputs.tile && Array.isArray(m.cell))
    return (
      <>
        <p>
          A seamless {m.cell.join(" × ")} px tile, {String(m.colors)} colours{Number(m.scale) > 1 ? `, one pixel per ${m.scale} × ${m.scale} of the picture` : ""}.
        </p>
        <Seams version={v} />
        <Files asset={props.asset} version={v} files={[["tile", "The tile (PNG)"], ["palette", "Palette (GIMP .gpl)"]]} />
      </>
    );
  return (
    <>
      {m.frames !== undefined && (
        <p>
          {String(m.frames)} frame{m.frames === 1 ? "" : "s"} of {Array.isArray(m.cell) ? m.cell.join(" × ") : "?"} px, {String(m.colors)} colours
          {Number(m.scale) > 1 ? `, one pixel per ${m.scale} × ${m.scale} of the picture` : ""}.
        </p>
      )}
      {m.scale === 1 && !v.params.height_px && (v.params.scale ?? "auto") === "auto" && Array.isArray(m.cell) && m.cell[1] > 256 && (
        <p className="note warn">No pixel grid was found in this picture, so it kept every pixel. Pixelize again with a height for the subject.</p>
      )}
      <Files
        asset={props.asset}
        version={v}
        files={[
          ["sheet", "Sprite sheet (PNG)"],
          ["data", "Sheet data (Aseprite JSON)"],
          ["palette", "Palette (GIMP .gpl)"],
          ["image", "The picture (PNG)"],
        ]}
      />
    </>
  );
}

/** How well a tile's edges meet (steps/_pixel.seam_ratio: 1.1 or less passes). */
function Seams(props: { version: Meta }) {
  const m = props.version.metrics as Record<string, number>;
  if (m.seam_ratio === undefined) return null;
  return m.seam_ratio <= 1.1 ? (
    <p className="muted">
      Its edges meet: the step across them is {m.seam_ratio.toFixed(2)} of its own lines'{m.seam_ratio_before ? ` (${m.seam_ratio_before.toFixed(2)} before the mending)` : ""}.
    </p>
  ) : (
    <p className="note warn">
      Its edges still show where it repeats (the step across them is {m.seam_ratio.toFixed(2)} of its own lines'; 1.10 or less is seamless). Make
      another tile, or a plainer material.
    </p>
  );
}

/** A tile from a description (concepts op tile): how it came out and the file. */
function TileInfo(props: { asset: string; version: Meta }) {
  return (
    <>
      <p>A seamless {String(props.version.metrics.size)} × {String(props.version.metrics.size)} px tile, shown repeated.</p>
      <Seams version={props.version} />
      <Files asset={props.asset} version={props.version} files={[["tile", "The tile (PNG)"]]} />
    </>
  );
}

/** The version's palette, each colour changeable: a variant (palette swap) that keeps every pixel. */
function Recolour(props: { asset: string; version: Meta; chain: Chain }) {
  const v = props.version;
  const [colors, setColors] = useState<string[] | null>(null);
  const [swap, setSwap] = useState<Record<string, string>>({});
  const [busy, run] = useBusy();
  useEffect(() => {
    if (!v.outputs.palette) return;
    fetch(fileUrl(props.asset, v.version, v.outputs.palette))
      .then((r) => r.text())
      .then((t) => setColors(readGpl(t)), () => setColors([]));
  }, [props.asset, v.version]);
  const changed = Object.entries(swap).filter(([a, b]) => a !== b);
  if (!colors?.length) return null;
  return (
    <details id="recolour">
      <summary>Palette and recolour</summary>
      <div className="stack">
        <p className="muted">Change a colour to make a variant (an enemy in other clothes): the new version keeps every pixel, only the colours change.</p>
        <div className="palette" role="group" aria-label="Palette">
          {colors.map((c) => (
            <label key={c} className="swatch" title={c}>
              <input type="color" aria-label={`Colour ${c}`} value={swap[c] ?? c} onChange={(e) => setSwap((s) => ({ ...s, [c]: e.target.value }))} />
            </label>
          ))}
        </div>
        <button
          disabled={busy || !changed.length}
          onClick={() => run(() => props.chain(props.asset, v.version, [["pixelize", { scale: 1, palette: "input", palette_map: Object.fromEntries(changed) }]]))}
        >
          {busy ? "Starting…" : changed.length ? `Make the recoloured version (${changed.length} colour${changed.length > 1 ? "s" : ""})` : "Make the recoloured version"}
        </button>
      </div>
    </details>
  );
}

/** A version's files as the 3D export lists them: small download buttons. */
function Files(props: { asset: string; version: Meta; files: [string, string][] }) {
  const v = props.version;
  return (
    <ul className="downloads">
      {props.files
        .filter(([k]) => v.outputs[k])
        .map(([k, title]) => (
          <li key={k}>
            <a className="button small" href={fileUrl(props.asset, v.version, v.outputs[k])} download>
              {title}
            </a>
          </li>
        ))}
    </ul>
  );
}

// steps/redraw.py: MAX_PX, the window and its overlap, and the windows' time on the reference card
const REDRAW = { maxPx: 500e6, window: 1024, loraWindow: 768, overlap: 0.25, seconds: 10 };
const windows = (n: number, win: number, ov: number) => (n <= win ? 1 : Math.ceil((n - win) / (win - ov)) + 1);
/** The picture's size in px: a generated one's, a redrawn one's; null when the version does not say. */
const sizeOf = (v: Meta): [number, number] | null => {
  const s = (v.metrics as Record<string, unknown>).size;
  const [w, h] = Array.isArray(s) ? s.map(Number) : [Number(v.params.width), Number(v.params.height)];
  return w > 0 && h > 0 ? [w, h] : null;
};

/** Upscale or redraw the shown version's picture (step redraw, M61): bigger, or drawn again from itself in windows, with a
 * mask that keeps parts as they are. */
export function RedrawForm(props: { asset: string; version: Meta; versions: Meta[]; picked: string; chain: Chain; missing: (group: string) => boolean }) {
  const v = props.version;
  const [op, setOp] = useState("upscale");
  const [factor, setFactor] = useState("4");
  const [prompt, setPrompt] = useState("");
  const [strength, setStrength] = useState("0.35");
  const [seed, setSeed] = useState("");
  const [style, setStyle] = useState("");
  const [protect, setProtect] = useState("");
  const [mask, setMask] = useState<Meta[]>([]); // masks added here: the asset's versions do not list them yet
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const [err, setErr] = useState("");
  const [busy, run] = useBusy();
  const redraw = op === "redraw";
  const f = Number(factor);
  const need = redraw && props.missing("refine") ? ["refine", "Texture detail", "the Tile control net"] : f > 1 && props.missing("texture") ? ["texture", "Face detail", "the upscaler (Real-ESRGAN)"] : null;
  const keys = pictureKeys(v);
  const key = keys.length ? (v.outputs[props.picked] ? props.picked : keys[0]) : undefined;
  const siblings = [...props.versions, ...mask].filter((x) => x.version !== v.version && (x.outputs.image || pictureKeys(x).length) && x.step !== "brief");
  const size = sizeOf(v);
  const win = style ? REDRAW.loraWindow : REDRAW.window;
  const [w, h] = size ? [size[0] * f, size[1] * f] : [0, 0];
  const count = redraw && size ? windows(w, win, win * REDRAW.overlap) * windows(h, win, win * REDRAW.overlap) : 0;
  const tooBig = w * h > REDRAW.maxPx;
  const strong = Number(strength) >= 0.2 && Number(strength) <= 1;
  const ok = (!redraw || (!!prompt.trim() && strong)) && (seed === "" || Number.isInteger(Number(seed))) && !tooBig;
  const params = {
    op,
    ...(key ? { key } : {}),
    factor: f,
    ...(redraw ? { prompt: prompt.trim(), strength: Number(strength), ...(seed !== "" ? { seed: Number(seed) } : {}), ...(style ? { lora: style } : {}), ...(protect ? { protect } : {}) } : {}),
  };
  const add = () =>
    run(async () => {
      try {
        const up = await api.upload(props.asset, file!);
        setMask((m) => [...m, up]);
        setProtect(up.version);
        setFile(null);
        setInputKey((k) => k + 1);
        setErr("");
      } catch (e) {
        setErr(String((e as Error).message ?? e));
      }
    });
  if (need)
    return (
      <>
        <p className="note warn">
          {redraw ? "Redrawing" : "Upscaling"} needs {need[2]}.
        </p>
        <a className="button" href={setupAt(need[0])}>
          Set up {need[1]}
        </a>
      </>
    );
  return (
    <div className="stack">
      <label>
        What to do
        <select value={op} onChange={(e) => (setOp(e.target.value), setFactor(e.target.value === "redraw" ? "1" : "4"))}>
          <option value="upscale">Make it bigger (upscale)</option>
          <option value="redraw">Draw it again (redraw)</option>
        </select>
      </label>
      <label>
        {redraw ? "Bigger first" : "Size"}
        <select value={factor} onChange={(e) => setFactor(e.target.value)}>
          {(redraw ? [1, 2, 4] : [2, 4]).map((n) => (
            <option key={n} value={n}>
              {n === 1 ? "As it is" : `${n}× a side`}
            </option>
          ))}
        </select>
      </label>
      {redraw && (
        <>
          <label>
            Description
            <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </label>
          <label>
            Strength{" "}
            <input type="range" aria-label="Redraw strength" min={0.2} max={1} step={0.05} value={strength} onChange={(e) => setStrength(e.target.value)} /> {Number(strength).toFixed(2)}
          </label>
          <label>
            Seed (empty: any)
            <input type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          <label>
            Style
            <select value={style} onChange={(e) => setStyle(e.target.value)}>
              {STYLES.map(([id, title]) => (
                <option key={id} value={id} disabled={!!id && props.missing("styles")}>
                  {title}
                </option>
              ))}
            </select>
          </label>
          <label>
            Keep as it is (a mask)
            <select value={protect} onChange={(e) => setProtect(e.target.value)}>
              <option value="">Nothing: draw it all</option>
              {siblings.map((x) => (
                <option key={x.version} value={x.version}>
                  {x.version}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">A mask is a grey picture in this asset: white stays as it is, black is drawn again, grey in between. Add one:</p>
          <input key={inputKey} type="file" aria-label="Mask picture" accept="image/*" aria-invalid={!!err} onChange={(e) => (setErr(""), setFile(e.target.files?.[0] ?? null))} />
          {err && (
            <p className="error" role="alert">
              {err}
            </p>
          )}
          <button disabled={busy || !file} onClick={add}>
            Add the mask
          </button>
        </>
      )}
      <p className={tooBig ? "note warn" : "muted"}>
        {tooBig
          ? `${w} × ${h} px is more than the ${REDRAW.maxPx / 1e6} MP the step makes: a smaller size.`
          : size
            ? `${w} × ${h} px.${count ? ` About ${count} window${count > 1 ? "s" : ""}, ${REDRAW.seconds} seconds each: ${Math.max(1, Math.round((count * REDRAW.seconds) / 60))} min.` : ""}`
            : redraw
              ? `Drawn in windows of ${win} px, about ${REDRAW.seconds} seconds each.`
              : "The picture's size is not known here."}
        {redraw && " There is no negative prompt: the model runs without one."}
      </p>
      <button className="primary" disabled={busy || !ok} onClick={() => run(() => props.chain(props.asset, v.version, [["redraw", params]]))}>
        {busy ? "Starting…" : redraw ? "Redraw the picture" : "Make it bigger"}
      </button>
    </div>
  );
}

/** A redraw version: its size and how close it stayed (steps/redraw.py thresholds in its report). */
function RedrawInfo(props: { asset: string; version: Meta }) {
  const v = props.version;
  const m = v.metrics as Record<string, number | number[] | Record<string, boolean>>;
  const pass = (m.pass ?? {}) as Record<string, boolean>;
  const bad = Object.keys(pass).filter((k) => !pass[k]);
  const num = (k: string) => (typeof m[k] === "number" ? (m[k] as number).toFixed(2) : "?");
  const windowed = m.seam_ratio !== undefined;
  return (
    <>
      <p>
        {Array.isArray(m.size) ? m.size.join(" × ") : "?"} px, from {Array.isArray(m.input_size) ? m.input_size.join(" × ") : "?"}
        {windowed ? `, drawn in ${m.windows} windows` : ""}.
      </p>
      <p className={bad.length ? "note warn" : "muted"}>
        {bad.length ? `It moved far from the picture (${bad.join(", ")}): ` : "It stayed close to the picture: "}
        {num("drift")} grey levels on average
        {windowed ? `, ${num("drift_max")} in the worst window, seam ${num("seam_ratio")} (1 is none)` : ""}.{bad.length ? " A lower strength stays closer." : ""}
      </p>
      <Files asset={props.asset} version={v} files={[["image", "The picture (PNG)"], ["report", "Report (JSON)"]]} />
    </>
  );
}

/** A picture that is not pixel art, sprites, a tile or layers already (M55: it can become parallax layers). */
const isLayerable = (v: Meta) =>
  !!(v.outputs.image || pictureKeys(v).length) && !v.outputs.tile && !["pixelize", "sprites", "depth", "layers"].includes(v.step);

/** The pictures Upscale or redraw (M61) is offered on: the panel's section and the Tools menu ask the same. */
export const redrawable = (v: Meta) => isLayerable(v) || v.step === "redraw";

/** M71: which tools the 2D strip offers for a version (the same rules Actions2D had for its sections), and the one it asks for first. */
export function tools2D(versions: Meta[], v: Meta, editor: boolean, kind?: string): { on: Record<string, boolean>; rec: string } {
  const parent = versions.find((x) => x.version === v.parent);
  const brief = v.step === "brief" || parent?.step === "brief";
  const category = ancestors(versions, v).find((a) => a.step === "brief")?.params?.category;
  const stage = category === "background" || category === "wallpaper";
  const layerable = isLayerable(v);
  const animate = animatable(versions, v, kind);
  const hasPicture = !!(v.outputs.image || pictureKeys(v).length || v.outputs.frames || v.outputs.sheet) && !["layers", "depth"].includes(v.step);
  const on = {
    pictures: brief,
    tile: brief,
    redraw: redrawable(v),
    pixelize: hasPicture && (v.step !== "pixelize" || !!parent),
    recolour: v.step === "pixelize",
    animate,
    layers: layerable || v.step === "depth" || (v.step === "layers" && !!parent),
    editor: editor && (v.step === "pixelize" || v.step === "sprites"),
    finish2d: is2D(v) || v.step === "validate" || v.step === "export",
  };
  const rec =
    v.step === "brief"
      ? "pictures"
      : ["validate", "export", "layers", "sprites"].includes(v.step) || v.outputs.tile
        ? "finish2d"
        : v.step === "depth" || (layerable && stage)
          ? "layers"
          : v.step === "pixelize"
            ? animate
              ? "animate"
              : "finish2d"
            : "pixelize";
  return { on, rec };
}

/** The panel of a 2D asset's version: what the version is, then the chosen tool (M71; no tool: every section, a sheet in a 3D asset). */
export function Actions2D(props: {
  asset: string;
  name?: string;
  version: Meta;
  versions: Meta[];
  chain: Chain;
  missing: (group: string) => boolean;
  picked: string;
  kind?: string;
  tool?: string;
  finish?: React.ReactNode; // M53: the check and the export (main.tsx's export panel)
  onEdit?: () => void; // M56: the pixel editor on this sheet
}) {
  const v = props.version;
  const t = props.tool;
  const show = (id: string) => !t || t === id;
  const [busy, run] = useBusy();
  const parent = props.versions.find((x) => x.version === v.parent);
  const brief = v.step === "brief" ? v : parent?.step === "brief" ? parent : undefined;
  const [prompt, setPrompt] = useState(String(brief?.params.prompt ?? ""));
  const [kind, setKind] = useState(String(brief?.params.category ?? "picture"));
  const pictures = (k: string, params: Record<string, unknown>) =>
    run(() =>
      prompt.trim() === String(brief?.params.prompt ?? "") && k === brief?.params.category
        ? props.chain(props.asset, brief!.version, [["concepts", params]])
        : props.chain(props.asset, null, [
            ["brief", { prompt: prompt.trim(), category: k }],
            ["concepts", params],
          ]),
    );
  const hasPicture = !!(v.outputs.image || pictureKeys(v).length || v.outputs.frames || v.outputs.sheet) && !["layers", "depth"].includes(v.step);
  const animate = animatable(props.versions, v, props.kind);
  // M55: a picture that is not pixel art, sprites, a tile or layers already can become parallax layers; a stage first
  const category = ancestors(props.versions, v).find((a) => a.step === "brief")?.params?.category;
  const stage = category === "background" || category === "wallpaper";
  const layerable = isLayerable(v);
  const layersFrom = <LayersForm asset={props.asset} version={v} picked={props.picked} chain={props.chain} missing={props.missing} primary={stage} />;
  return (
    <>
      {brief && (t === "pictures" || t === "tile") && (
        <div className="stack">
          <label>
            Description
            <textarea rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </label>
          <PictureForm kind={kind} onKind={setKind} missing={props.missing} ready={!!prompt.trim()} busy={busy} onRun={pictures} />
        </div>
      )}
      {v.step === "sprites" && (
        <section className="stack">
          <h3>Sprites</h3>
          <SpritesInfo asset={props.asset} version={v} />
        </section>
      )}
      {v.step === "concepts" && v.outputs.tile && (
        <section className="stack">
          <h3>Seamless tile</h3>
          <TileInfo asset={props.asset} version={v} />
        </section>
      )}
      {v.step === "depth" && (
        <section className="stack">
          <h3>Depth</h3>
          <p className="muted">How far each part of the picture is (Depth in the view: lighter is nearer). Next, the layers.</p>
        </section>
      )}
      {v.step === "layers" && (
        <section className="stack">
          <h3>Parallax layers</h3>
          <LayersInfo asset={props.asset} version={v} />
        </section>
      )}
      {v.step === "redraw" && (
        <section className="stack">
          <h3>{v.params.op === "upscale" ? "Upscaled picture" : "Redrawn picture"}</h3>
          <RedrawInfo asset={props.asset} version={v} />
        </section>
      )}
      {v.step === "pixelize" && (
        <section className="stack">
          <h3>Pixel art</h3>
          <PixelInfo asset={props.asset} version={v} />
        </section>
      )}
      {show("animate") && animate && <AnimateIt {...props} primary={v.step === "pixelize"} />}
      {show("editor") && props.onEdit && (v.step === "pixelize" || v.step === "sprites") && (
        <section className="stack" id="edit">
          <p className="muted">Draw on the frames in whole pixels with the version's colours: touch up a frame, draw a hit spark. Save makes a new version.</p>
          <button onClick={props.onEdit}>Edit the pixels</button>
        </section>
      )}
      {show("finish2d") && props.finish}
      {show("recolour") && v.step === "pixelize" && <Recolour asset={props.asset} version={v} chain={props.chain} />}
      {show("pixelize") && hasPicture && v.step !== "pixelize" && (
        <section className="stack" id="pixelize">
          <h3>Make it pixel art</h3>
          <PixelizeForm asset={props.asset} version={v} picked={props.picked} chain={props.chain} />
        </section>
      )}
      {show("pixelize") && v.step === "pixelize" && parent && (
        <section className="stack" id="pixelize">
          <h3>Pixelize again with other settings</h3>
          <PixelizeForm asset={props.asset} version={parent} picked={String(v.params.key ?? "")} chain={props.chain} start={v.params} />
        </section>
      )}
      {show("redraw") && redrawable(v) && (
        <section className="stack" id="redraw">
          <RedrawForm asset={props.asset} version={v} versions={props.versions} picked={props.picked} chain={props.chain} missing={props.missing} />
        </section>
      )}
      {show("layers") && (layerable || v.step === "depth") && (
        <section className="stack" id="layers">
          {layersFrom}
        </section>
      )}
      {show("layers") && v.step === "layers" && parent && (
        <section className="stack" id="layers">
          <h3>Make the layers again</h3>
          <LayersForm asset={props.asset} version={parent} picked="" chain={props.chain} missing={props.missing} start={v.params} />
        </section>
      )}
    </>
  );
}

/** Parallax layers (M55) from a picture (the depth step first) or from a depth version: how many, and a figure in front
 * as a layer of its own. */
export function LayersForm(props: {
  asset: string;
  version: Meta;
  picked: string;
  chain: Chain;
  missing: (group: string) => boolean;
  start?: Record<string, unknown>;
  primary?: boolean;
}) {
  const v = props.version;
  const [count, setCount] = useState(Number(props.start?.count ?? 4));
  const [subject, setSubject] = useState(Boolean(props.start?.subject ?? false));
  const [busy, run] = useBusy();
  // the depth model, and the picture model that draws what the nearer layers hide
  const need = props.missing("layers") ? ["layers", "Parallax layers", "the depth model (MoGe-3, 5 GB)"] : props.missing("concepts") ? ["concepts", "Concept images", "the picture model (Z-Image)"] : null;
  if (need)
    return (
      <>
        <p className="note warn">Parallax layers need {need[2]}.</p>
        <a className={props.primary ? "button primary" : "button"} href={setupAt(need[0])}>
          Set up {need[1]}
        </a>
      </>
    );
  const keys = pictureKeys(v);
  const key = keys.length ? (v.outputs[props.picked] ? props.picked : keys[0]) : undefined;
  const params = { count, subject };
  const steps: [string, object][] =
    v.step === "depth" ? [["layers", params]] : [["depth", key ? { key } : {}], ["layers", params]];
  return (
    <div className="stack">
      <label>
        Layers
        <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
          {[2, 3, 4, 5].map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </label>
      <label className="check">
        <input type="checkbox" checked={subject} onChange={(e) => setSubject(e.target.checked)} /> A figure in front as a layer of its own
      </label>
      <p className="muted">
        The picture's depth splits it far to near; what each layer hides behind the nearer ones is drawn in, so it can scroll past. About two minutes.
      </p>
      <button className={props.primary ? "primary" : ""} disabled={busy} onClick={() => run(() => props.chain(props.asset, v.version, steps))}>
        {busy ? "Starting…" : "Make parallax layers"}
      </button>
    </div>
  );
}

type Layer = { file: string; scroll_scale: number; share: number };

/** A layers version: each layer's speed and its file, far to near. */
function LayersInfo(props: { asset: string; version: Meta }) {
  const v = props.version;
  const [data, setData] = useState<Layer[] | null>(null);
  useEffect(() => {
    fetch(fileUrl(props.asset, v.version, v.outputs.layers))
      .then((r) => r.json())
      .then(setData, () => setData([]));
  }, [props.asset, v.version]);
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <p>
        {data.length} layers, far to near. Each moves at its share of the camera's move: the farthest nearly still, the nearest with the camera.
      </p>
      <ul className="downloads">
        {data.map((d, k) => (
          <li key={d.file}>
            <a className="button small" href={fileUrl(props.asset, v.version, d.file)} download>
              {k === 0 ? "Farthest" : k === data.length - 1 ? "Nearest" : `Layer ${k + 1}`} (× {d.scroll_scale})
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}

/** A still picture of a character, to animate next: a 2D asset's "Item or sprite" picture (or an uploaded one), not a
 * sheet of frames already, and not a wallpaper, background, portrait or icon. */
export function animatable(versions: Meta[], v: Meta, kind?: string): boolean {
  if (kind !== "2d" || Number(v.metrics?.frames ?? 1) > 1) return false;
  const category = ancestors(versions, v).find((a) => a.step === "brief")?.params?.category;
  return (category === undefined || category === "item") && !!source(versions, v, "image_0");
}

/** The picture a 2D version holds at full size: a set's picked one, or the one a pixel art was made from. */
function source(versions: Meta[], v: Meta, picked: string): [Meta, string] | null {
  if (v.step === "pixelize") {
    const parent = versions.find((x) => x.version === v.parent);
    if (parent && (parent.step === "pixelize" || !parent.outputs.sheet)) return source(versions, parent, String(v.params.key ?? "image"));
  }
  if (v.outputs.sheet && v.outputs.data && v.step !== "pixelize") return null;
  const keys = pictureKeys(v);
  const key = keys.length ? (v.outputs[picked] ? picked : keys[0]) : "image";
  return v.outputs[key] ? [v, v.outputs[key]] : null;
}

/** A 2D character moves through the 3D route: its picture becomes a 3D asset, redrawn there as a shaded 3D figure in
 * the standing pose and cut out (a flat picture makes a cardboard model: canon's look "render"), which Generate,
 * Prepare and Character turn into a rigged, animated model; Tools → Sprites for 2D draws it back as pixel art. */
function AnimateIt(props: {
  asset: string;
  name?: string;
  version: Meta;
  versions: Meta[];
  chain: Chain;
  missing: (group: string) => boolean;
  picked: string;
  primary?: boolean;
}) {
  const [busy, run] = useBusy();
  const [error, setError] = useState("");
  const from = source(props.versions, props.version, props.picked);
  if (!from) return null;
  const [pic, file] = from;
  const go = () =>
    run(async () => {
      setError("");
      try {
        const blob = await (await fetch(fileUrl(props.asset, pic.version, file))).blob();
        const name = `${props.name ?? props.asset} (3D)`;
        const made = await api.createAsset(name);
        const up = await api.upload(made.id, new File([blob], `${made.id}.png`, { type: "image/png" }));
        // cut out, redrawn standing and shaded, cut out again; it opens there
        await props.chain(made.id, up.version, [
          ["prep", {}],
          ["canon", { look: "render" }],
          ["prep", {}],
        ]);
      } catch (e) {
        setError(String((e as Error).message ?? e));
      }
    });
  return (
    <section className="stack" id="animate">
      <h3>Animate this character</h3>
      <p className="muted">
        Walking, punching, jumping: the moves come from a 3D model that Effigy makes from this picture
        {pic.version !== props.version.version ? " (the full-size one the pixel art was made from)" : ""}, with no modelling on your part. A new 3D asset
        opens with the character redrawn as a shaded 3D figure, standing, hands empty (about a minute; a flat picture would make a flat model).
        There:
      </p>
      <ol className="muted steps">
        <li>Generate 3D, then Prepare model</li>
        <li>Fit a skeleton, then Attach to skeleton</li>
        <li>A move set (Brawler, Sword fighter) or moves from a sentence</li>
        <li>Tools → Sprites for 2D, then Make pixel art</li>
      </ol>
      {props.missing("canon-flux") ? (
        <>
          <p className="note warn">The redraw needs the standing-pose model.</p>
          <a className={props.primary ? "button primary" : "button"} href={setupAt("canon-flux")}>
            Set up Standing pose
          </a>
        </>
      ) : (
        <button className={props.primary ? "primary" : ""} disabled={busy} onClick={go}>
          {busy ? "Redrawing it as a 3D figure…" : "Make a 3D character from this picture"}
        </button>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}


// research 15 §2: a side view facing right (the engine mirrors it), the other views on request
const VIEWS: [number, string][] = [
  [90, "Facing right"],
  [270, "Facing left"],
  [0, "Front"],
  [180, "Back"],
];

/** Sprites of a 3D character's clips, or of a still model turned (steps/sprites.py, M52). */
export function SpritesForm(props: { asset: string; version: Meta; chain: Chain }) {
  const v = props.version;
  const animated = !!v.outputs.rigged_anim;
  const [clips, setClips] = useState<string[] | null>(animated ? null : []);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [height, setHeight] = useState("");
  const [views, setViews] = useState<Set<number>>(new Set([90]));
  const [fps, setFps] = useState(12);
  const [shading, setShading] = useState("textured");
  const [cell, setCell] = useState("128");
  const [pitch, setPitch] = useState("12");
  const [busy, run] = useBusy();
  useEffect(() => {
    if (!animated || !v.outputs.clips) return;
    fetch(fileUrl(props.asset, v.version, v.outputs.clips))
      .then((r) => r.json())
      .then(
        (c: { clips: { name: string }[] }) => {
          const names = c.clips.map((x) => x.name);
          setClips(names);
          setPicked(new Set(names));
        },
        () => setClips([]),
      );
  }, [props.asset, v.version]);
  const h = height.trim() === "" ? null : Number(height);
  const ok =
    (!animated || picked.size > 0) &&
    views.size > 0 &&
    (h === null || (Number.isInteger(h) && h >= 8 && h <= 1024)) &&
    Number.isInteger(Number(cell)) &&
    Number(cell) >= 16 &&
    Number(cell) <= 1024 &&
    Number(pitch) >= 0 &&
    Number(pitch) <= 45;
  const toggle = <T,>(set: Set<T>, x: T) => {
    const n = new Set(set);
    if (!n.delete(x)) n.add(x);
    return n;
  };
  const params = {
    ...(animated && clips && picked.size < clips.length ? { clips: clips.filter((c) => picked.has(c)) } : {}),
    yaws: VIEWS.map(([y]) => y).filter((y) => views.has(y)),
    ...(h !== null ? { height_px: h } : {}),
    fps,
    shading,
    cell: Number(cell),
    pitch_deg: Number(pitch),
  };
  return (
    <div className="stack">
      <p className="muted">
        {animated
          ? "Each animation drawn from the side in whole pixels, in place, on a transparent background: a sheet for a 2D game. Make pixel art from it next."
          : "The model drawn from each view in whole pixels on a transparent background: a prop for a 2D game."}
      </p>
      {animated &&
        (clips === null ? (
          <p className="muted">Loading the animations…</p>
        ) : (
          <fieldset>
            <legend>Animations</legend>
            <div className="row">
              {clips.map((c) => (
                <label key={c} className="check">
                  <input type="checkbox" checked={picked.has(c)} onChange={() => setPicked((s) => toggle(s, c))} /> {c}
                </label>
              ))}
            </div>
          </fieldset>
        ))}
      <fieldset>
        <legend>Views</legend>
        <div className="row">
          {VIEWS.map(([y, name]) => (
            <label key={y} className="check">
              <input type="checkbox" checked={views.has(y)} onChange={() => setViews((s) => toggle(s, y))} /> {name}
            </label>
          ))}
        </div>
      </fieldset>
      <label>
        Height in pixels
        <input type="number" min={8} max={1024} placeholder="104" value={height} onChange={(e) => setHeight(e.target.value)} />
      </label>
      <label>
        Frames per second
        <select value={fps} onChange={(e) => setFps(Number(e.target.value))}>
          {[8, 10, 12, 15, 24].map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </label>
      <label>
        Look
        <select value={shading} onChange={(e) => setShading(e.target.value)}>
          <option value="textured">Lit (studio light)</option>
          <option value="flat">Flat (the texture unlit)</option>
        </select>
      </label>
      <p className="muted">
        Empty height: this asset's earlier sprites' scale, or 104 px the first time. Every later run keeps one scale, so all animations match.
      </p>
      <details>
        <summary>More settings</summary>
        <div className="stack">
          <label>
            Least cell (px)
            <input type="number" min={16} max={1024} value={cell} onChange={(e) => setCell(e.target.value)} />
          </label>
          <label>
            Camera tilt (°)
            <input type="number" min={0} max={45} value={pitch} onChange={(e) => setPitch(e.target.value)} />
          </label>
        </div>
      </details>
      <button className="primary" disabled={busy || !ok} onClick={() => run(() => props.chain(props.asset, v.version, [["sprites", params]]))}>
        {busy ? "Starting…" : "Render sprites"}
      </button>
    </div>
  );
}

/** A sprites version: what it holds, the files, and the way on to pixel art. */
export function SpritesInfo(props: { asset: string; version: Meta }) {
  const v = props.version;
  const m = v.metrics as Record<string, unknown>;
  const clips = (m.clips as string[] | undefined) ?? [];
  return (
    <div className="stack">
      {m.frames !== undefined && (
        <p>
          {String(m.frames)} frames of {String(m.cell)} × {String(m.cell)} px{clips.length ? `: ${clips.join(", ")}` : ""}, {String(m.height_px)} px tall.
        </p>
      )}
      <Files
        asset={props.asset}
        version={v}
        files={[
          ["sheet", "Sprite sheet (PNG)"],
          ["data", "Sheet data (Aseprite JSON)"],
        ]}
      />
    </div>
  );
}
