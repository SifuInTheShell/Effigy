// One EventSource for the whole page (browsers allow 6 HTTP/1.1 connections per origin).

type Handler = (event: string, data: any) => void;
const handlers = new Set<Handler>();
const NAMES = ["progress", "log", "job", "version_created", "versions_deleted", "asset_deleted", "gpu", "gpu_load", "thumb"];
const emit = (name: string, data: any) => handlers.forEach((h) => h(name, data));

const source = new EventSource("/events");
for (const name of NAMES) source.addEventListener(name, (e) => emit(name, JSON.parse((e as MessageEvent).data)));
// the stream's own state as a pseudo-event: events sent while it was down are lost, the App reconciles
source.addEventListener("open", () => emit("connection", { ok: true }));
source.addEventListener("error", () => emit("connection", { ok: false }));

export const connected = () => source.readyState === EventSource.OPEN;

export function onEvent(h: Handler): () => void {
  handlers.add(h);
  return () => handlers.delete(h);
}
