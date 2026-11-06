import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { Viewer, VIEW_MODES, type ViewMode } from "../viewer/Viewer";
import { api, type TextureMaterial, type TextureRole } from "../api";

export const mapUrl = (
  asset: string,
  version: string,
  material: number,
  role: TextureRole,
) =>
  `/api/assets/${asset}/versions/${version}/textures/image?material=${material}&role=${role}`;

export function ModelPreview({
  url,
  before,
}: {
  url: string;
  before?: string;
}) {
  const left = useRef<HTMLCanvasElement>(null),
    right = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState("");
  const [clips, setClips] = useState<string[]>([]);
  const viewers = useRef<Viewer[]>([]);
  useEffect(() => {
    let dead = false,
      unlink = () => {};
    setError("");
    const list = [
      new Viewer(left.current!),
      ...(before ? [new Viewer(right.current!)] : []),
    ];
    viewers.current = list;
    window.__viewer = list[0];
    Promise.all(list.map((viewer, i) => viewer.load(i === 0 ? url : before!)))
      .then((stats) => {
        if (dead) return;
        setClips(stats[0]?.clips ?? []);
        if (list.length === 2) unlink = list[0].link(list[1]);
      })
      .catch((e) => !dead && setError(String(e)));
    return () => {
      dead = true;
      unlink();
      list.forEach((v) => v.dispose());
      viewers.current = [];
      if (window.__viewer === list[0]) window.__viewer = undefined;
    };
  }, [url, before]);
  return (
    <div className="texture-model-preview">
      <div className={`texture-model-canvases ${before ? "comparing" : ""}`}>
        <figure>
          <canvas ref={left} aria-label="Current textured model" />
          <figcaption>Current version</figcaption>
        </figure>
        {before && (
          <figure>
            <canvas ref={right} aria-label="Previous textured model" />
            <figcaption>Comparison · linked camera</figcaption>
          </figure>
        )}
      </div>
      <label className="texture-playback">
        Inspect channel
        <select
          aria-label="Texture inspect channel"
          onChange={(e) =>
            viewers.current.forEach((viewer) =>
              viewer.setViewMode(e.target.value as ViewMode),
            )
          }
        >
          {VIEW_MODES.map((mode) => (
            <option key={mode} value={mode}>
              {mode === "pbr"
                ? "Material lighting"
                : mode === "uv"
                  ? "UV checker"
                  : mode}
            </option>
          ))}
        </select>
      </label>
      {clips.length > 0 && (
        <label className="texture-playback">
          Preview animation
          <select
            aria-label="Texture animation"
            onChange={(e) =>
              viewers.current.forEach((v) => {
                v.setClip(e.target.value);
                v.play();
              })
            }
          >
            <option value="">Rest pose</option>
            {clips.map((clip) => (
              <option key={clip}>{clip}</option>
            ))}
          </select>
        </label>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function MapPreview({
  asset,
  version,
  material,
  role,
  width,
  height,
  model,
}: {
  asset: string;
  version: string;
  material: number;
  role: TextureRole;
  width: number;
  height: number;
  model: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [overlay, setOverlay] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (!overlay) return;
    let live = true;
    setError("");
    api.textureUV(asset, version, material).then(
      ({ triangles }) => {
        if (!live || !canvas.current) return;
        const c = canvas.current;
        c.width = Math.min(width, 2048);
        c.height = Math.round((c.width * height) / width);
        const ctx = c.getContext("2d")!;
        ctx.clearRect(0, 0, c.width, c.height);
        ctx.strokeStyle = "#ffe09b";
        ctx.lineWidth = 0.7;
        ctx.beginPath();
        for (const triangle of triangles) {
          ctx.moveTo(triangle[0][0] * c.width, triangle[0][1] * c.height);
          for (const uv of [...triangle.slice(1), triangle[0]])
            ctx.lineTo(uv[0] * c.width, uv[1] * c.height);
        }
        ctx.stroke();
      },
      (e) => live && setError(String(e)),
    );
    return () => {
      live = false;
    };
  }, [overlay, asset, version, material, width, height]);
  return (
    <figure className="texture-map-figure">
      <div className="texture-map-image">
        <img
          src={mapUrl(asset, version, material, role)}
          alt={`${{ basecolor: "Base colour", normal: "Normal", roughness: "Roughness", metallic: "Metallic", ao: "Ambient occlusion", height: "Height", emissive: "Emission" }[role]} map`}
        />
        {overlay && (
          <canvas
            ref={canvas}
            className="texture-uv-overlay"
            aria-label="UV layout overlay"
          />
        )}
      </div>
      <figcaption>
        Current · {width} × {height}
        {model && (
          <label className="check">
            <input
              type="checkbox"
              checked={overlay}
              onChange={(e) => setOverlay(e.target.checked)}
            />{" "}
            UV0 overlay
          </label>
        )}
      </figcaption>
      {error && <p className="error">{error}</p>}
    </figure>
  );
}

export function MaterialPreview({
  asset,
  version,
  material,
}: {
  asset: string;
  version: string;
  material: TextureMaterial;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [shape, setShape] = useState("sphere"),
    [repeat, setRepeat] = useState(2),
    [rotation, setRotation] = useState(0),
    [moving, setMoving] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    let dead = false;
    setError("");
    const renderer = new THREE.WebGLRenderer({
      canvas: canvas.current!,
      antialias: true,
      alpha: true,
    });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer),
      room = new RoomEnvironment();
    const environment = pmrem.fromScene(room, 0.04);
    scene.environment = environment.texture;
    const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 100);
    camera.position.set(0, 0.4, 4.5);
    const controls = new OrbitControls(camera, canvas.current!);
    const light = new THREE.DirectionalLight(0xffffff, 3);
    light.position.set(2, 3, 4);
    scene.add(light);
    scene.add(new THREE.AmbientLight(0xffffff, 0.25));
    const values = material.settings;
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color().fromArray(values.baseColorFactor ?? [1, 1, 1]),
      roughness: values.roughnessFactor ?? 0.8,
      metalness: values.metallicFactor ?? 0,
      side: THREE.DoubleSide,
    });
    mat.opacity = values.baseColorFactor?.[3] ?? 1;
    mat.transparent = values.alphaMode === "BLEND";
    mat.alphaTest =
      values.alphaMode === "MASK" ? (values.alphaCutoff ?? 0.5) : 0;
    const geometry =
      shape === "sphere"
        ? new THREE.SphereGeometry(1, 64, 48)
        : shape === "cube"
          ? new THREE.BoxGeometry(1.6, 1.6, 1.6, 1, 1, 1)
          : new THREE.PlaneGeometry(2.5, 2.5);
    const model = new THREE.Mesh(geometry, mat);
    scene.add(model);
    const loader = new THREE.TextureLoader(),
      textures: THREE.Texture[] = [];
    const slots = {
      basecolor: "map",
      roughness: "roughnessMap",
      metallic: "metalnessMap",
      normal: "normalMap",
      ao: "aoMap",
      emissive: "emissiveMap",
    } as const;
    for (const [role, slot] of Object.entries(slots)) {
      if (!material.maps[role as TextureRole]) continue;
      const texture = loader.load(
        mapUrl(asset, version, material.index, role as TextureRole),
        () => {
          if (dead) return;
          texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
          texture.repeat.set(repeat, repeat);
          texture.center.set(0.5, 0.5);
          texture.rotation = (rotation * Math.PI) / 180;
          texture.colorSpace = ["basecolor", "emissive"].includes(role)
            ? THREE.SRGBColorSpace
            : THREE.NoColorSpace;
          mat[slot] = texture;
          mat.needsUpdate = true;
        },
        undefined,
        () => !dead && setError("A material map could not be loaded."),
      );
      textures.push(texture);
    }
    mat.emissive.fromArray(values.emissiveFactor ?? [0, 0, 0]);
    const resize = () => {
      if (!canvas.current) return;
      const surface = canvas.current;
      const workspace = surface.closest(".texture-workspace");
      const activity = document.querySelector('footer[aria-label="Activity"]');
      const toolbar = surface.parentElement?.querySelector(".toolbar");
      const foot = workspace?.querySelector(".texture-foot");
      if (activity && toolbar) {
        const available =
          activity.getBoundingClientRect().top -
          surface.getBoundingClientRect().top -
          toolbar.getBoundingClientRect().height -
          (foot?.getBoundingClientRect().height ?? 0) -
          12;
        const height = `${Math.max(128, Math.min(440, Math.floor(available)))}px`;
        if (surface.style.height !== height) surface.style.height = height;
      }
      const { width, height } = canvas.current.getBoundingClientRect();
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(height, 1);
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas.current!);
    const workspace = canvas.current!.closest(".texture-workspace");
    for (const element of [
      workspace,
      document.querySelector('footer[aria-label="Activity"]'),
      workspace?.querySelector(".texture-active"),
    ]) {
      if (element) observer.observe(element);
    }
    resize();
    renderer.setAnimationLoop((time) => {
      if (moving)
        light.position.set(
          Math.cos(time / 1500) * 4,
          3,
          Math.sin(time / 1500) * 4,
        );
      controls.update();
      renderer.render(scene, camera);
    });
    return () => {
      dead = true;
      observer.disconnect();
      renderer.setAnimationLoop(null);
      controls.dispose();
      geometry.dispose();
      mat.dispose();
      textures.forEach((t) => t.dispose());
      environment.dispose();
      room.dispose();
      pmrem.dispose();
      renderer.dispose();
    };
  }, [asset, version, material.index, shape, repeat, rotation, moving]);
  return (
    <div className="texture-surface">
      <canvas ref={canvas} aria-label="Standalone material preview" />
      <div className="toolbar">
        <label>
          Surface
          <select value={shape} onChange={(e) => setShape(e.target.value)}>
            <option value="sphere">Sphere</option>
            <option value="plane">Plane</option>
            <option value="cube">Cube</option>
          </select>
        </label>
        <label>
          Repeat
          <input
            type="number"
            min={1}
            max={12}
            value={repeat}
            onChange={(e) =>
              setRepeat(Math.max(1, Math.min(12, Number(e.target.value) || 1)))
            }
          />
        </label>
        <label>
          Rotation
          <input
            type="range"
            min={-180}
            max={180}
            value={rotation}
            onChange={(e) => setRotation(Number(e.target.value))}
          />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={moving}
            onChange={(e) => setMoving(e.target.checked)}
          />{" "}
          Moving light
        </label>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
