export const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000';

export type RunInfo = {
  id: string;
  status: string;
  created_at: string;
  updated_at: string;
  exit_reason?: string | null;
  summary_available: boolean;
  results_available: boolean;
};

export async function createRun(prompt: string, max_iterations?: number, batch_size?: number): Promise<RunInfo> {
  const res = await fetch(`${API_BASE}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, max_iterations, batch_size })
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function listRuns(): Promise<{ runs: RunInfo[] }> {
  const res = await fetch(`${API_BASE}/runs`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getRun(id: string): Promise<RunInfo> {
  const res = await fetch(`${API_BASE}/runs/${id}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function openEventStream(id: string, onEvent: (evt: any) => void): () => void {
  const url = `${API_BASE}/runs/${id}/events`;
  const es = new EventSource(url);
  es.onmessage = (e) => {
    try { onEvent(JSON.parse(e.data)); } catch {}
  };
  es.onerror = () => { /* browser will retry */ };
  return () => es.close();
}
