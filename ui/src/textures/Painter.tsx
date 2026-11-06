import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { TextureRole } from "../api";
import { useUnsaved } from "../hooks";

type Tool = "brush" | "eraser" | "picker" | "hand";
type Point = [number, number];
type Patch = { x: number; y: number; before: ImageData; after: ImageData };
type Edit = { patches: Patch[]; before: number; after: number; bytes: number };
const TILE = 128,
  HISTORY_BYTES = 64 * 1024 * 1024;

/** Painting always uses native map pixels; zoom only changes the presentation. */
export function Painter({
  url,
  role,
  disabled,
  onSave,
}: {
  url: string;
  role: TextureRole;
  disabled: boolean;
  onSave: (file: File) => Promise<void>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const undo = useRef<Edit[]>([]),
    redo = useRef<Edit[]>([]);
  const revision = useRef(0),
    nextRevision = useRef(0),
    savedRevision = useRef(0);
  const stroke = useRef<{
    last: Point;
    patches: Map<string, Omit<Patch, "after">>;
  } | null>(null);
  const pan = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const anchor = useRef<{ point: Point; client: Point } | null>(null);
  const space = useRef(false);
  const fitted = useRef(true);
  const [dirty, setDirty] = useState(false),
    [tool, setTool] = useState<Tool>("brush");
  const [shape, setShape] = useState("round"),
    [hardness, setHardness] = useState(1);
  const [color, setColor] = useState("#dfaa62"),
    [brush, setBrush] = useState(24);
  const [opacity, setOpacity] = useState(1),
    [zoom, setZoom] = useState(1);
  const [size, setSize] = useState<Point>([0, 0]),
    [ready, setReady] = useState(false);
  const [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [, refreshHistory] = useState(0);
  const locked = disabled || saving || !ready;
  useUnsaved("texture-map", dirty);
  const update = () => {
    setDirty(revision.current !== savedRevision.current);
    refreshHistory((n) => n + 1);
  };
  const fit = () => {
    const c = canvas.current,
      v = viewport.current;
    if (!c || !v || !c.width) return;
    fitted.current = true;
    anchor.current = null;
    setZoom(
      Math.min(
        1,
        (v.clientWidth - 48) / c.width,
        (v.clientHeight - 48) / c.height,
      ),
    );
    v.scrollTo(0, 0);
  };
  useEffect(() => {
    let live = true;
    setReady(false);
    setDirty(false);
    setError("");
    undo.current = [];
    redo.current = [];
    revision.current = savedRevision.current = nextRevision.current = 0;
    stroke.current = null;
    const image = new Image();
    image.onload = () => {
      if (!live || !canvas.current) return;
      const c = canvas.current;
      c.width = image.width;
      c.height = image.height;
      c.getContext("2d")!.drawImage(image, 0, 0);
      setSize([c.width, c.height]);
      setReady(true);
      fit();
    };
    image.onerror = () =>
      live && setError("The map could not be loaded for painting.");
    image.src = url;
    return () => {
      live = false;
    };
  }, [url]);
  const pointAt = (x: number, y: number): Point => {
    const c = canvas.current!,
      b = c.getBoundingClientRect();
    return [
      ((x - b.left) * c.width) / b.width,
      ((y - b.top) * c.height) / b.height,
    ];
  };
  const changeZoom = (value: number, client?: Point) => {
    if (stroke.current || pan.current) return;
    fitted.current = false;
    const v = viewport.current!;
    const b = v.getBoundingClientRect();
    const at: Point = client ?? [
      b.left + v.clientWidth / 2,
      b.top + v.clientHeight / 2,
    ];
    anchor.current = { point: pointAt(...at), client: at };
    setZoom(Math.max(0.02, Math.min(16, value)));
  };
  useLayoutEffect(() => {
    const a = anchor.current,
      c = canvas.current,
      v = viewport.current;
    if (!a || !c || !v) return;
    const b = c.getBoundingClientRect();
    v.scrollLeft += b.left + a.point[0] * zoom - a.client[0];
    v.scrollTop += b.top + a.point[1] * zoom - a.client[1];
    anchor.current = null;
  }, [zoom]);
  useEffect(() => {
    const v = viewport.current!;
    const wheel = (e: WheelEvent) => {
      if (!ready || stroke.current || pan.current) return;
      e.preventDefault();
      changeZoom(zoom * Math.exp(-e.deltaY * 0.002), [e.clientX, e.clientY]);
    };
    v.addEventListener("wheel", wheel, { passive: false });
    return () => v.removeEventListener("wheel", wheel);
  }, [ready, zoom]);
  useEffect(() => {
    const v = viewport.current!;
    const section = v.closest(".texture-painter")!;
    const stage = v.closest(".texture-stage")!;
    const footer = document.querySelector('footer[aria-label="Activity"]');
    const resize = () => {
      const bottom = Math.min(
        window.innerHeight,
        footer?.getBoundingClientRect().top ?? window.innerHeight,
      );
      const help =
        section.querySelector(".texture-paint-help")?.getBoundingClientRect()
          .height ?? 0;
      const foot =
        stage.querySelector(".texture-foot")?.getBoundingClientRect().height ??
        0;
      v.style.height = `${Math.max(96, Math.min(460, bottom - v.getBoundingClientRect().top - help - foot - 26))}px`;
      if (fitted.current) fit();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(section);
    if (footer) observer.observe(footer);
    window.addEventListener("resize", resize);
    resize();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [ready]);

  // Capture only touched tiles, rather than copying a whole 4K map for every stroke.
  const remember = (x: number, y: number) => {
    const c = canvas.current!,
      ctx = c.getContext("2d")!,
      s = stroke.current!;
    const r = brush / 2 + 1;
    for (
      let ty = Math.max(0, Math.floor((y - r) / TILE));
      ty <=
      Math.min(Math.ceil(c.height / TILE) - 1, Math.floor((y + r) / TILE));
      ty++
    ) {
      for (
        let tx = Math.max(0, Math.floor((x - r) / TILE));
        tx <=
        Math.min(Math.ceil(c.width / TILE) - 1, Math.floor((x + r) / TILE));
        tx++
      ) {
        const key = `${tx},${ty}`;
        if (!s.patches.has(key))
          s.patches.set(key, {
            x: tx * TILE,
            y: ty * TILE,
            before: ctx.getImageData(
              tx * TILE,
              ty * TILE,
              Math.min(TILE, c.width - tx * TILE),
              Math.min(TILE, c.height - ty * TILE),
            ),
          });
      }
    }
  };
  const stamp = (x: number, y: number, pressure: number) => {
    const ctx = canvas.current!.getContext("2d")!;
    remember(x, y);
    const r = brush / 2;
    ctx.save();
    ctx.globalCompositeOperation =
      tool === "eraser" ? "destination-out" : "source-over";
    ctx.globalAlpha = opacity * pressure;
    ctx.fillStyle = color;
    if (shape === "square") ctx.fillRect(x - r, y - r, brush, brush);
    else {
      if (hardness < 1) {
        const gradient = ctx.createRadialGradient(x, y, r * hardness, x, y, r);
        gradient.addColorStop(0, color);
        gradient.addColorStop(1, `${color}00`);
        ctx.fillStyle = gradient;
      }
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  };
  const draw = (e: React.PointerEvent) => {
    const s = stroke.current;
    if (!s) return;
    const p = pointAt(e.clientX, e.clientY),
      [x, y] = s.last;
    const distance = Math.hypot(p[0] - x, p[1] - y);
    const count = Math.max(1, Math.ceil(distance / Math.max(1, brush * 0.15)));
    for (let i = 1; i <= count; i++)
      stamp(
        x + ((p[0] - x) * i) / count,
        y + ((p[1] - y) * i) / count,
        e.pointerType === "pen" ? e.pressure : 1,
      );
    s.last = p;
  };
  const finish = () => {
    pan.current = null;
    const s = stroke.current;
    if (!s) return;
    stroke.current = null;
    const ctx = canvas.current!.getContext("2d")!;
    const patches: Patch[] = [...s.patches.values()]
      .map((p) => ({
        ...p,
        after: ctx.getImageData(p.x, p.y, p.before.width, p.before.height),
      }))
      .filter((p) => p.before.data.some((v, i) => v !== p.after.data[i]));
    if (!patches.length) {
      update();
      return;
    }
    const after = ++nextRevision.current;
    undo.current.push({
      patches,
      before: revision.current,
      after,
      bytes: patches.reduce((n, p) => n + p.before.data.byteLength * 2, 0),
    });
    redo.current = [];
    let bytes = undo.current.reduce((n, edit) => n + edit.bytes, 0);
    while (bytes > HISTORY_BYTES && undo.current.length > 1)
      bytes -= undo.current.shift()!.bytes;
    revision.current = after;
    update();
  };
  const travel = (forward: boolean) => {
    if (locked || stroke.current) return;
    const edit = (forward ? redo : undo).current.pop();
    if (!edit) return;
    const ctx = canvas.current!.getContext("2d")!;
    for (const p of edit.patches)
      ctx.putImageData(forward ? p.after : p.before, p.x, p.y);
    (forward ? undo : redo).current.push(edit);
    revision.current = forward ? edit.after : edit.before;
    update();
  };
  const save = async () => {
    if (locked || stroke.current) return;
    setSaving(true);
    setError("");
    try {
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.current!.toBlob(resolve, "image/png"),
      );
      if (!blob) throw new Error("The painted map could not be encoded.");
      // Our own version navigation must pass the unsaved-changes guard.
      setDirty(false);
      await onSave(
        new File([blob], `${role}-painted.png`, { type: "image/png" }),
      );
      savedRevision.current = revision.current;
    } catch (e) {
      setDirty(true);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <section
      className={`texture-painter${expanded ? " is-expanded" : ""}`}
      aria-label="Paint texture map"
      tabIndex={0}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) {
          space.current = false;
          finish();
        }
      }}
      onKeyUp={(e) => {
        if (e.code === "Space") {
          space.current = false;
          e.preventDefault();
        }
      }}
      onKeyDown={(e) => {
        if ((e.target as HTMLElement).matches("input, select, textarea"))
          return;
        if (e.key === "Escape" && expanded) {
          setExpanded(false);
          e.preventDefault();
          return;
        }
        if (e.code === "Space") {
          space.current = true;
          e.preventDefault();
          return;
        }
        if (
          (e.ctrlKey || e.metaKey) &&
          ["z", "y"].includes(e.key.toLowerCase())
        ) {
          e.preventDefault();
          travel(e.shiftKey || e.key.toLowerCase() === "y");
          return;
        }
        if (e.ctrlKey || e.metaKey || e.altKey) return;
        const keys: Record<string, Tool> = {
          b: "brush",
          e: "eraser",
          i: "picker",
          h: "hand",
        };
        if (keys[e.key.toLowerCase()]) {
          setTool(keys[e.key.toLowerCase()]);
          e.preventDefault();
        }
        if (e.key === "[" || e.key === "]") {
          setBrush((n) =>
            Math.max(1, Math.min(500, n + (e.key === "[" ? -4 : 4))),
          );
          e.preventDefault();
        }
        if (["+", "=", "-"].includes(e.key)) {
          changeZoom(zoom * (e.key === "-" ? 0.8 : 1.25));
          e.preventDefault();
        }
        if (e.key === "0") {
          fit();
          e.preventDefault();
        }
      }}
    >
      <div
        className="toolbar texture-paint-tools"
        role="group"
        aria-label="Painting tools"
      >
        {(
          [
            ["brush", "Brush", "B"],
            ["eraser", "Eraser", "E"],
            ["picker", "Pick colour", "I"],
            ["hand", "Hand", "H"],
          ] as const
        ).map(([id, label, key]) => (
          <button
            key={id}
            aria-pressed={tool === id}
            title={`${label} (${key})`}
            disabled={locked}
            onClick={() => setTool(id)}
          >
            {label}
          </button>
        ))}
        <button
          disabled={locked || !undo.current.length}
          onClick={() => travel(false)}
          title="Undo (Ctrl+Z)"
        >
          Undo stroke
        </button>
        <button
          disabled={locked || !redo.current.length}
          onClick={() => travel(true)}
          title="Redo (Ctrl+Shift+Z)"
        >
          Redo stroke
        </button>
        <button className="primary" disabled={!dirty || locked} onClick={save}>
          {saving ? "Saving map…" : "Save painted map"}
        </button>
      </div>
      <fieldset className="toolbar texture-brush-settings" disabled={locked}>
        <label>
          Brush colour
          <input
            type="color"
            value={color}
            onChange={(e) => setColor(e.target.value)}
          />
        </label>
        <label>
          Brush shape
          <select value={shape} onChange={(e) => setShape(e.target.value)}>
            <option value="round">Round</option>
            <option value="square">Square</option>
          </select>
        </label>
        <label>
          Brush size <output>{brush} px</output>
          <input
            aria-label="Brush size"
            type="range"
            min={1}
            max={500}
            value={brush}
            onChange={(e) => setBrush(Number(e.target.value))}
          />
        </label>
        <label>
          Opacity <output>{Math.round(opacity * 100)}%</output>
          <input
            aria-label="Opacity"
            type="range"
            min={0.05}
            max={1}
            step={0.05}
            value={opacity}
            onChange={(e) => setOpacity(Number(e.target.value))}
          />
        </label>
        <label>
          Hardness <output>{Math.round(hardness * 100)}%</output>
          <input
            aria-label="Hardness"
            type="range"
            disabled={shape === "square"}
            min={0}
            max={1}
            step={0.05}
            value={hardness}
            onChange={(e) => setHardness(Number(e.target.value))}
          />
        </label>
      </fieldset>
      <div
        className="toolbar texture-paint-navigation"
        role="group"
        aria-label="Canvas zoom"
      >
        <button
          disabled={!ready}
          aria-label="Zoom out"
          onClick={() => changeZoom(zoom / 1.25)}
        >
          −
        </button>
        <output aria-label="Zoom level">{Math.round(zoom * 100)}%</output>
        <button
          disabled={!ready}
          aria-label="Zoom in"
          onClick={() => changeZoom(zoom * 1.25)}
        >
          +
        </button>
        <button disabled={!ready} onClick={fit}>
          Fit map
        </button>
        <button disabled={!ready} onClick={() => changeZoom(1)}>
          100%
        </button>
        <button aria-pressed={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? "Restore painter" : "Expand painter"}
        </button>
        <span className="muted">
          {size[0]} × {size[1]} px
        </span>
      </div>
      <div
        ref={viewport}
        className="texture-paint-viewport"
        aria-label="Painting viewport"
        tabIndex={0}
        onContextMenu={(e) => e.preventDefault()}
        onPointerDown={(e) => {
          if (locked || ![0, 1].includes(e.button)) return;
          e.preventDefault();
          viewport.current!.focus({ preventScroll: true });
          viewport.current!.setPointerCapture(e.pointerId);
          if (tool === "hand" || space.current || e.button === 1) {
            const v = viewport.current!;
            pan.current = {
              x: e.clientX,
              y: e.clientY,
              left: v.scrollLeft,
              top: v.scrollTop,
            };
            return;
          }
          if (e.target !== canvas.current) return;
          const p = pointAt(e.clientX, e.clientY);
          if (tool === "picker" || e.altKey) {
            const c = canvas.current!,
              ctx = c.getContext("2d")!;
            const rgba = ctx.getImageData(
              Math.max(0, Math.min(c.width - 1, Math.floor(p[0]))),
              Math.max(0, Math.min(c.height - 1, Math.floor(p[1]))),
              1,
              1,
            ).data;
            setColor(
              `#${[...rgba]
                .slice(0, 3)
                .map((v) => v.toString(16).padStart(2, "0"))
                .join("")}`,
            );
            return;
          }
          stroke.current = { last: p, patches: new Map() };
          setDirty(true);
          stamp(...p, e.pointerType === "pen" ? e.pressure : 1);
        }}
        onPointerMove={(e) => {
          if (pan.current) {
            const p = pan.current,
              v = viewport.current!;
            v.scrollLeft = p.left - (e.clientX - p.x);
            v.scrollTop = p.top - (e.clientY - p.y);
          } else draw(e);
        }}
        onPointerUp={finish}
        onPointerCancel={finish}
        onLostPointerCapture={finish}
      >
        <div
          className="texture-paint-stage"
          style={{ width: size[0] * zoom + 48, height: size[1] * zoom + 48 }}
        >
          <canvas
            ref={canvas}
            aria-label="Texture painting canvas"
            className="texture-paint-canvas"
            style={{
              width: size[0] * zoom,
              height: size[1] * zoom,
              cursor:
                tool === "hand"
                  ? "grab"
                  : tool === "picker"
                    ? "copy"
                    : "crosshair",
              imageRendering: zoom >= 2 ? "pixelated" : "auto",
            }}
          />
        </div>
      </div>
      <p className="muted texture-paint-help">
        Wheel: zoom · Space/middle-drag: pan · Alt-click: pick colour · [ / ]:
        size · B/E/I/H: tools · Ctrl+Z / Ctrl+Shift+Z: undo/redo. Save creates a
        version. Eraser removes opacity (requires a transparent material alpha
        mode).
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
