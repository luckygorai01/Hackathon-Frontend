export type SampleAddress = {
  label: string;
  address: string;
};

export type EvidenceItem = {
  label: string;
  value: string;
};

export type Candidate = {
  name: string;
  kind: string;
  latitude: number;
  longitude: number;
  distance_m: number;
  similarity: number;
  score: number;
  source: string;
};

export type ResolveResponse = {
  original_address: string;
  normalized_address: string;
  extracted: {
    pincode: string | null;
    tokens: string[];
    has_coordinates: boolean;
  };
  confidence: number;
  confidence_label: 'low' | 'medium' | 'high';
  low_confidence: boolean;
  chosen_point: {
    latitude: number;
    longitude: number;
    source: string;
    name?: string;
    kind?: string;
    pincode?: string | null;
    office_name?: string | null;
    district?: string | null;
    state?: string | null;
  } | null;
  candidates: Candidate[];
  evidence: EvidenceItem[];
  audit: {
    duration_ms: number;
    raw_address_retained: boolean;
    corrections: Array<{ field: string; original: string; normalized: string }>;
    evidence_count: number;
  };
  self_check: string[];
};

export type HealthResponse = {
  status: string;
  dataset?: {
    dataset_path: string;
    rows: number;
    columns: number;
    resolved_columns: Record<string, string | null>;
    unique_pincodes: number;
  };
  error?: string | null;
};
