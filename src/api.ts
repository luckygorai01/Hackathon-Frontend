import type { HealthResponse, ResolveResponse, SampleAddress } from './types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>('/api/health');
}

export function getSamples(): Promise<SampleAddress[]> {
  return fetchJson<SampleAddress[]>('/api/samples');
}

export function resolveAddress(payload: {
  address: string;
  city?: string;
  state?: string;
}): Promise<ResolveResponse> {
  return fetchJson<ResolveResponse>('/api/resolve', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
