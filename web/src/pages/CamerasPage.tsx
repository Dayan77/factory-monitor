import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import { camerasApi } from "@/services/api";
import { useCameras, useCreateCamera, useUpdateCamera, useDeleteCamera } from "@/hooks/useQueries";
import { useMachines } from "@/hooks/useQueries";
import type { Camera } from "@/services/types";
import { useAuthStore } from "@/store/auth";

// ── HLS Player ────────────────────────────────────────────────────

function HlsPlayer({ src, label }: { src: string; label: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef   = useRef<Hls | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    setError(false);
    const video = videoRef.current;
    if (!video) return;

    if (Hls.isSupported()) {
      const hls = new Hls({ enableWorker: true, lowLatencyMode: true });
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, (_e, data) => {
        if (data.fatal) setError(true);
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Native HLS (Safari)
      video.src = src;
    } else {
      setError(true);
    }

    return () => {
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [src]);

  if (error) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gray-900 rounded-lg text-gray-500 text-sm">
        <div className="text-center space-y-1">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="size-8 mx-auto text-gray-600">
            <path d="M15 10l4.553-2.069A1 1 0 0 1 21 8.87v6.26a1 1 0 0 1-1.447.899L15 14" />
            <rect x="2" y="6" width="13" height="12" rx="2" />
            <line x1="2" y1="2" x2="22" y2="22" />
          </svg>
          <p>Stream indisponível</p>
        </div>
      </div>
    );
  }

  return (
    <video
      ref={videoRef}
      autoPlay
      muted
      playsInline
      className="w-full h-full object-cover rounded-lg bg-black"
      aria-label={label}
    />
  );
}

// ── Camera Form Modal ─────────────────────────────────────────────

interface CameraFormProps {
  initial?: Camera | null;
  onClose: () => void;
}

function CameraFormModal({ initial, onClose }: CameraFormProps) {
  const { data: machines = [] } = useMachines();
  const createCamera = useCreateCamera();
  const updateCamera = useUpdateCamera();

  const [form, setForm] = useState({
    name:        initial?.name        ?? "",
    rtsp_url:    initial?.rtsp_url    ?? "",
    description: initial?.description ?? "",
    machine_id:  initial?.machine_id  ?? "",
    is_active:   initial?.is_active   ?? true,
  });

  function handleChange(e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) {
    const { name, value, type } = e.target;
    setForm((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? (e.target as HTMLInputElement).checked : value,
    }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const payload = {
      ...form,
      machine_id: form.machine_id || null,
    };
    if (initial) {
      await updateCamera.mutateAsync({ id: initial.id, body: payload });
    } else {
      await createCamera.mutateAsync(payload);
    }
    onClose();
  }

  const isPending = createCamera.isPending || updateCamera.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-xl bg-gray-900 border border-gray-700 shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-700 px-6 py-4">
          <h2 className="text-base font-semibold text-white">
            {initial ? "Editar câmera" : "Nova câmera"}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-5">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 p-6">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Nome *</label>
            <input
              name="name"
              value={form.name}
              onChange={handleChange}
              required
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-white focus:border-brand-500 focus:outline-none"
              placeholder="Câmera Linha 1 — Entrada"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">URL RTSP *</label>
            <input
              name="rtsp_url"
              value={form.rtsp_url}
              onChange={handleChange}
              required
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-white focus:border-brand-500 focus:outline-none font-mono"
              placeholder="rtsp://192.168.1.100:554/stream1"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Descrição</label>
            <input
              name="description"
              value={form.description}
              onChange={handleChange}
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-white focus:border-brand-500 focus:outline-none"
              placeholder="Opcional"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Máquina vinculada</label>
            <select
              name="machine_id"
              value={form.machine_id}
              onChange={handleChange}
              className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-white focus:border-brand-500 focus:outline-none"
            >
              <option value="">— Nenhuma —</option>
              {machines.map((m) => (
                <option key={m.id} value={m.id}>{m.name}</option>
              ))}
            </select>
          </div>

          {initial && (
            <label className="flex items-center gap-2 text-sm text-gray-300">
              <input
                type="checkbox"
                name="is_active"
                checked={form.is_active}
                onChange={handleChange}
                className="accent-brand-500"
              />
              Câmera ativa
            </label>
          )}

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-lg border border-gray-700 py-2 text-sm text-gray-400 hover:bg-gray-800"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="flex-1 rounded-lg bg-brand-500 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {isPending ? "Salvando…" : "Salvar"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Camera Card ───────────────────────────────────────────────────

interface CameraCardProps {
  camera: Camera;
  machineName?: string;
  onEdit: (cam: Camera) => void;
  onDelete: (cam: Camera) => void;
}

function CameraCard({ camera, machineName, onEdit, onDelete }: CameraCardProps) {
  const [viewing, setViewing] = useState(false);
  const hlsSrc = camerasApi.hlsUrl(camera.id);

  return (
    <div className="rounded-xl border border-gray-700 bg-gray-800 overflow-hidden flex flex-col">
      {/* Stream area */}
      <div className="relative aspect-video bg-gray-900">
        {viewing ? (
          <HlsPlayer src={hlsSrc} label={camera.name} />
        ) : (
          <button
            onClick={() => setViewing(true)}
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-gray-500 hover:text-gray-300 transition-colors group"
          >
            <div className="size-14 rounded-full border-2 border-gray-600 group-hover:border-gray-400 flex items-center justify-center transition-colors">
              <svg viewBox="0 0 24 24" fill="currentColor" className="size-7 ml-0.5">
                <path d="M8 5v14l11-7z" />
              </svg>
            </div>
            <span className="text-xs">Clique para visualizar</span>
          </button>
        )}

        {/* Active badge */}
        <div className={`absolute top-2 right-2 size-2.5 rounded-full ${camera.is_active ? "bg-green-400" : "bg-gray-500"}`} />
      </div>

      {/* Info */}
      <div className="flex items-start justify-between gap-3 p-3">
        <div className="min-w-0">
          <p className="text-sm font-medium text-white truncate">{camera.name}</p>
          {machineName && (
            <p className="text-xs text-gray-500 truncate mt-0.5">{machineName}</p>
          )}
          {camera.description && (
            <p className="text-xs text-gray-600 truncate mt-0.5">{camera.description}</p>
          )}
        </div>

        <div className="flex gap-1 shrink-0">
          <button
            onClick={() => onEdit(camera)}
            className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-700 hover:text-gray-300 transition-colors"
            title="Editar"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-4">
              <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
            </svg>
          </button>
          <button
            onClick={() => onDelete(camera)}
            className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-700 hover:text-red-400 transition-colors"
            title="Excluir"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="size-4">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6l-1 14H6L5 6" />
              <path d="M10 11v6M14 11v6" />
              <path d="M9 6V4h6v2" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────

export default function CamerasPage() {
  const { user } = useAuthStore();
  const { data: cameras = [], isLoading } = useCameras();
  const { data: machines = [] } = useMachines();
  const deleteCamera = useDeleteCamera();

  const [showForm, setShowForm]         = useState(false);
  const [editing, setEditing]           = useState<Camera | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Camera | null>(null);

  const canEdit = user?.role === "admin" || user?.role === "manager" || user?.role === "superadmin";

  const machineMap = Object.fromEntries(machines.map((m) => [m.id, m.name]));

  function handleEdit(cam: Camera) {
    setEditing(cam);
    setShowForm(true);
  }

  function handleCloseForm() {
    setShowForm(false);
    setEditing(null);
  }

  async function handleConfirmDelete() {
    if (!confirmDelete) return;
    await deleteCamera.mutateAsync(confirmDelete.id);
    setConfirmDelete(null);
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-white">Câmeras IP</h1>
          <p className="text-sm text-gray-500 mt-0.5">Visualização ao vivo das câmeras da fábrica</p>
        </div>
        {canEdit && (
          <button
            onClick={() => { setEditing(null); setShowForm(true); }}
            className="flex items-center gap-2 rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 transition-colors"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="size-4">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Nova câmera
          </button>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-gray-700 bg-gray-800 aspect-video animate-pulse" />
          ))}
        </div>
      ) : cameras.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-gray-700 bg-gray-800/40 py-20 text-center">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="size-12 text-gray-600 mb-3">
            <path d="M15 10l4.553-2.069A1 1 0 0 1 21 8.87v6.26a1 1 0 0 1-1.447.899L15 14" />
            <rect x="2" y="6" width="13" height="12" rx="2" />
          </svg>
          <p className="text-gray-400 font-medium">Nenhuma câmera cadastrada</p>
          <p className="text-gray-600 text-sm mt-1">
            {canEdit ? "Clique em \"Nova câmera\" para adicionar." : "Aguarde um administrador cadastrar câmeras."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {cameras.map((cam) => (
            <CameraCard
              key={cam.id}
              camera={cam}
              machineName={cam.machine_id ? machineMap[cam.machine_id] : undefined}
              onEdit={handleEdit}
              onDelete={setConfirmDelete}
            />
          ))}
        </div>
      )}

      {/* Form modal */}
      {showForm && (
        <CameraFormModal initial={editing} onClose={handleCloseForm} />
      )}

      {/* Delete confirm modal */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-xl bg-gray-900 border border-gray-700 p-6 shadow-2xl space-y-4">
            <p className="text-white font-medium">Excluir câmera?</p>
            <p className="text-sm text-gray-400">
              "{confirmDelete.name}" será removida permanentemente.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setConfirmDelete(null)}
                className="flex-1 rounded-lg border border-gray-700 py-2 text-sm text-gray-400 hover:bg-gray-800"
              >
                Cancelar
              </button>
              <button
                onClick={handleConfirmDelete}
                disabled={deleteCamera.isPending}
                className="flex-1 rounded-lg bg-red-500 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50"
              >
                {deleteCamera.isPending ? "Excluindo…" : "Excluir"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
