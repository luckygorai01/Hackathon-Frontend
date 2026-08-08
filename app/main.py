from __future__ import annotations

import math
import os
import re
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

try:
    from unidecode import unidecode
except ImportError:
    def unidecode(value: str) -> str:
        return value

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = APP_ROOT / "data" / "all_india_pincode_directory_2025.csv"
DATASET_PATH = Path(os.getenv("PINCODE_DATASET_PATH", DEFAULT_DATASET_PATH))
OVERPASS_URL = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]
OVERPASS_TIMEOUT_SECONDS = float(os.getenv("OVERPASS_TIMEOUT_SECONDS", "6"))

app = FastAPI(title="Pata API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResolveRequest(BaseModel):
    address: str = Field(min_length=3, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)


class EvidenceItem(BaseModel):
    label: str
    value: str


class LandmarkCandidate(BaseModel):
    name: str
    kind: str
    latitude: float
    longitude: float
    distance_m: float
    similarity: float
    score: float
    source: str


class ResolveResponse(BaseModel):
    original_address: str
    normalized_address: str
    extracted: dict[str, Any]
    confidence: float
    confidence_label: str
    low_confidence: bool
    chosen_point: dict[str, Any] | None
    candidates: list[LandmarkCandidate]
    evidence: list[EvidenceItem]
    audit: dict[str, Any]
    self_check: list[str]


class PincodeRecord(BaseModel):
    pincode: str
    office_name: str | None = None
    district: str | None = None
    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class PincodeStore:
    def __init__(self, dataset_path: Path) -> None:
        self.dataset_path = dataset_path
        self.frame = self._load_frame()
        self.columns = self._resolve_columns(self.frame)
        self.records_by_pincode = self._build_index(self.frame, self.columns)

    @staticmethod
    def _normalize_column(column: str) -> str:
        column = unicodedata.normalize("NFKD", str(column))
        column = re.sub(r"[^0-9a-zA-Z]+", "_", column.strip().lower())
        return column.strip("_")

    def _load_frame(self) -> pd.DataFrame:
        if not self.dataset_path.exists():
            raise FileNotFoundError(
                f"Pincode dataset not found at {self.dataset_path}. Put the CSV there or set PINCODE_DATASET_PATH."
            )
        if self.dataset_path.suffix.lower() != ".csv":
            raise ValueError("This MVP expects the Kaggle mirror CSV file.")
        frame = pd.read_csv(self.dataset_path)
        frame = frame.copy()
        frame.columns = [self._normalize_column(column) for column in frame.columns]
        return frame

    def _resolve_columns(self, frame: pd.DataFrame) -> dict[str, str | None]:
        aliases = {
            "pincode": ["pincode", "pin_code", "postcode", "zip_code"],
            "office_name": ["office_name", "officename", "postoffice", "post_office", "branch_office", "office"],
            "district": ["district", "districtname", "district_name"],
            "state": ["statename", "state", "state_name"],
            "latitude": ["latitude", "lat"],
            "longitude": ["longitude", "lon", "lng"],
        }
        lookup = {self._normalize_column(column): column for column in frame.columns}
        resolved: dict[str, str | None] = {}
        for field, candidates in aliases.items():
            resolved[field] = next((lookup.get(self._normalize_column(candidate)) for candidate in candidates if self._normalize_column(candidate) in lookup), None)
        return resolved

    def _build_index(self, frame: pd.DataFrame, columns: dict[str, str | None]) -> dict[str, list[PincodeRecord]]:
        pincode_column = columns["pincode"]
        if pincode_column is None:
            raise ValueError("Dataset is missing a pincode column.")

        office_column = columns["office_name"]
        district_column = columns["district"]
        state_column = columns["state"]
        latitude_column = columns["latitude"]
        longitude_column = columns["longitude"]

        indexed: dict[str, list[PincodeRecord]] = {}
        for _, row in frame.iterrows():
            pincode_value = str(row.get(pincode_column, "")).strip()
            if not pincode_value or pincode_value.lower() == "nan":
                continue
            record = PincodeRecord(
                pincode=pincode_value,
                office_name=_safe_string(row.get(office_column)) if office_column else None,
                district=_safe_string(row.get(district_column)) if district_column else None,
                state=_safe_string(row.get(state_column)) if state_column else None,
                latitude=_safe_float(row.get(latitude_column)) if latitude_column else None,
                longitude=_safe_float(row.get(longitude_column)) if longitude_column else None,
            )
            indexed.setdefault(pincode_value, []).append(record)
        return indexed

    def lookup(self, pincode: str) -> list[PincodeRecord]:
        return self.records_by_pincode.get(str(pincode).strip(), [])

    def summary(self) -> dict[str, Any]:
        return {
            "dataset_path": str(self.dataset_path),
            "rows": int(self.frame.shape[0]),
            "columns": int(self.frame.shape[1]),
            "resolved_columns": self.columns,
            "unique_pincodes": int(len(self.records_by_pincode)),
        }


class AddressResolver:
    def __init__(self, store: PincodeStore) -> None:
        self.store = store

    @staticmethod
    def normalize_text(text: str) -> str:
        text = unidecode(unicodedata.normalize("NFKD", text))
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def tokenize(text: str) -> list[str]:
        cleaned = unidecode(unicodedata.normalize("NFKD", text))
        cleaned = cleaned.lower()
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
        return [token for token in cleaned.split() if len(token) > 1]

    @staticmethod
    def extract_pincode(text: str) -> str | None:
        match = re.search(r"\b(\d{6})\b", text)
        return match.group(1) if match else None

    def resolve(self, request: ResolveRequest) -> ResolveResponse:
        started_at = time.perf_counter()
        original_address = request.address.strip()
        normalized_address = self.normalize_text(original_address)
        pincode = self.extract_pincode(original_address)
        tokens = self.tokenize(original_address)

        evidence: list[EvidenceItem] = [
            EvidenceItem(label="original_address", value=original_address),
            EvidenceItem(label="normalized_address", value=normalized_address),
        ]
        if pincode:
            evidence.append(EvidenceItem(label="extracted_pincode", value=pincode))

        pincode_records = self.store.lookup(pincode) if pincode else []
        pincode_guess = _best_pincode_record(pincode_records)
        pincode_is_valid = bool(pincode_guess)

        landmarks: list[LandmarkCandidate] = []
        if pincode_guess and pincode_guess.latitude is not None and pincode_guess.longitude is not None:
            landmarks = self._fetch_landmarks(pincode_guess.latitude, pincode_guess.longitude, tuple(tokens))
        elif tokens:
            fallback_records = self._search_reference_rows(tokens, request.city, request.state)
            fallback_choice = _best_fallback_record(fallback_records)
            if fallback_choice is not None and fallback_choice.latitude is not None and fallback_choice.longitude is not None:
                pincode_guess = fallback_choice
                pincode_is_valid = False
                evidence.append(
                    EvidenceItem(
                        label="pincode_fallback",
                        value="The extracted pincode did not match the reference dataset, so the resolver used locality/state evidence instead.",
                    )
                )
                evidence.append(
                    EvidenceItem(
                        label="fallback_match",
                        value=f"Matched {fallback_choice.office_name or fallback_choice.district or fallback_choice.state or fallback_choice.pincode}",
                    )
                )
                landmarks = self._fetch_landmarks(fallback_choice.latitude, fallback_choice.longitude, tuple(tokens))

        best_landmark = max(landmarks, key=lambda item: item.score, default=None)
        chosen_point = None
        confidence = 0.0
        confidence_label = "low"
        low_confidence = True

        if pincode_guess and pincode_guess.latitude is not None and pincode_guess.longitude is not None:
            chosen_point = {
                "latitude": pincode_guess.latitude,
                "longitude": pincode_guess.longitude,
                "source": "pincode_centroid" if pincode_is_valid else "reference_fallback",
                "pincode": pincode_guess.pincode,
                "office_name": pincode_guess.office_name,
                "district": pincode_guess.district,
                "state": pincode_guess.state,
            }
            confidence = 0.55 if pincode_is_valid else 0.38
            if pincode_is_valid:
                evidence.append(EvidenceItem(label="pincode_match", value=f"Matched {pincode_guess.pincode} to pincode centroid"))
            else:
                evidence.append(EvidenceItem(label="reference_row", value="Used the nearest dataset row inferred from district/state/office tokens."))

        if best_landmark:
            chosen_point = {
                "latitude": best_landmark.latitude,
                "longitude": best_landmark.longitude,
                "source": best_landmark.source,
                "name": best_landmark.name,
                "kind": best_landmark.kind,
                "pincode": pincode_guess.pincode if pincode_guess else None,
            }
            confidence = min(0.98, confidence + best_landmark.score)
            evidence.append(EvidenceItem(label="landmark_match", value=f"Matched nearby {best_landmark.kind}: {best_landmark.name}"))

        if request.city:
            evidence.append(EvidenceItem(label="user_city_hint", value=request.city.strip()))
        if request.state:
            evidence.append(EvidenceItem(label="user_state_hint", value=request.state.strip()))

        if confidence >= 0.8:
            confidence_label = "high"
            low_confidence = False
        elif confidence >= 0.55:
            confidence_label = "medium"
            low_confidence = False if pincode_guess and best_landmark else True
        else:
            confidence_label = "low"
            low_confidence = True

        self_check = self._self_check(original_address, pincode, pincode_guess, best_landmark, confidence)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        audit = {
            "duration_ms": duration_ms,
            "raw_address_retained": False,
            "corrections": [
                {"field": "address", "original": original_address, "normalized": normalized_address}
            ],
            "pincode_valid": pincode_is_valid,
            "evidence_count": len(evidence),
        }

        return ResolveResponse(
            original_address=original_address,
            normalized_address=normalized_address,
            extracted={
                "pincode": pincode,
                "tokens": tokens[:12],
                "has_coordinates": bool(pincode_guess and pincode_guess.latitude is not None and pincode_guess.longitude is not None),
            },
            confidence=round(confidence, 3),
            confidence_label=confidence_label,
            low_confidence=low_confidence,
            chosen_point=chosen_point,
            candidates=landmarks[:5],
            evidence=evidence,
            audit=audit,
            self_check=self_check,
        )

    def _self_check(
        self,
        original_address: str,
        pincode: str | None,
        pincode_guess: PincodeRecord | None,
        best_landmark: LandmarkCandidate | None,
        confidence: float,
    ) -> list[str]:
        checks = [
            "Original address is preserved in the audit trail.",
            "No result is silently promoted to high confidence without a pincode or landmark match.",
        ]
        if not pincode:
            checks.append("No pincode was found; this should be shown as low confidence.")
        if pincode and not pincode_guess:
            checks.append("The pincode was extracted but not found in the reference dataset.")
        if best_landmark is None:
            checks.append("No landmark match was strong enough to refine the geocode.")
        if confidence < 0.55:
            checks.append("Confidence is below the medium threshold, so the UI should warn the user.")
        return checks

    def _search_reference_rows(self, tokens: list[str], city_hint: str | None, state_hint: str | None) -> list[PincodeRecord]:
        frame = self.store.frame
        columns = self.store.columns
        pincode_column = columns["pincode"]
        office_column = columns["office_name"]
        district_column = columns["district"]
        state_column = columns["state"]
        latitude_column = columns["latitude"]
        longitude_column = columns["longitude"]

        if pincode_column is None:
            return []

        query_tokens = set(tokens)
        if city_hint:
            query_tokens.update(self.tokenize(city_hint))
        if state_hint:
            query_tokens.update(self.tokenize(state_hint))

        scored_rows: list[tuple[float, PincodeRecord]] = []
        for _, row in frame.iterrows():
            office_name = _safe_string(row.get(office_column)) if office_column else None
            district_name = _safe_string(row.get(district_column)) if district_column else None
            state_name = _safe_string(row.get(state_column)) if state_column else None
            haystack = " ".join(value for value in [office_name, district_name, state_name] if value)
            row_tokens = set(self.tokenize(haystack))
            overlap = len(query_tokens & row_tokens)
            if overlap == 0:
                continue

            score = float(overlap)
            if state_hint and state_name and state_hint.strip().lower() in state_name.strip().lower():
                score += 2.0
            if city_hint and district_name and city_hint.strip().lower() in district_name.strip().lower():
                score += 1.5
            if office_name:
                office_tokens = set(self.tokenize(office_name))
                score += min(1.5, len(query_tokens & office_tokens) * 0.75)

            record = PincodeRecord(
                pincode=str(row.get(pincode_column, "")).strip(),
                office_name=office_name,
                district=district_name,
                state=state_name,
                latitude=_safe_float(row.get(latitude_column)) if latitude_column else None,
                longitude=_safe_float(row.get(longitude_column)) if longitude_column else None,
            )
            scored_rows.append((score, record))

        scored_rows.sort(key=lambda item: item[0], reverse=True)
        return [record for score, record in scored_rows[:10] if score >= 1.0]

    @lru_cache(maxsize=256)
    def _fetch_landmarks(self, latitude: float, longitude: float, tokens_key: tuple[str, ...]) -> list[LandmarkCandidate]:
        tokens = list(tokens_key)
        query = f"""
        [out:json][timeout:10];
        (
          node(around:1500,{latitude},{longitude})[amenity];
          way(around:1500,{latitude},{longitude})[amenity];
          relation(around:1500,{latitude},{longitude})[amenity];
          node(around:1500,{latitude},{longitude})[shop];
          way(around:1500,{latitude},{longitude})[shop];
          relation(around:1500,{latitude},{longitude})[shop];
          node(around:1500,{latitude},{longitude})[tourism];
          way(around:1500,{latitude},{longitude})[tourism];
          relation(around:1500,{latitude},{longitude})[tourism];
        );
        out center tags;
        """
        candidates: list[LandmarkCandidate] = []
        try:
            response = httpx.post(OVERPASS_URL, data={"data": query}, timeout=OVERPASS_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            for element in payload.get("elements", []):
                tags = element.get("tags", {})
                name = tags.get("name") or tags.get("brand") or tags.get("amenity") or tags.get("shop") or tags.get("tourism")
                if not name:
                    continue
                lat = element.get("lat") or element.get("center", {}).get("lat")
                lon = element.get("lon") or element.get("center", {}).get("lon")
                if lat is None or lon is None:
                    continue
                kind = tags.get("amenity") or tags.get("shop") or tags.get("tourism") or "poi"
                distance_m = haversine_m(latitude, longitude, float(lat), float(lon))
                similarity = name_similarity(name, tokens)
                proximity_score = max(0.0, 1.0 - min(distance_m, 1500.0) / 1500.0)
                score = round((similarity * 0.65) + (proximity_score * 0.35), 3)
                if score < 0.15:
                    continue
                candidates.append(
                    LandmarkCandidate(
                        name=str(name),
                        kind=str(kind),
                        latitude=float(lat),
                        longitude=float(lon),
                        distance_m=round(distance_m, 2),
                        similarity=round(similarity, 3),
                        score=score,
                        source="openstreetmap_overpass",
                    )
                )
        except Exception:
            return []

        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:10]


store: PincodeStore | None = None
resolver: AddressResolver | None = None
load_error: str | None = None


def initialize_state() -> None:
    global store, resolver, load_error
    try:
        store = PincodeStore(DATASET_PATH)
        resolver = AddressResolver(store)
        load_error = None
    except Exception as exc:
        store = None
        resolver = None
        load_error = str(exc)


@app.on_event("startup")
def startup() -> None:
    initialize_state()


@app.get("/api/health")
def health() -> JSONResponse:
    if store is None:
        return JSONResponse(status_code=503, content={"status": "booting", "error": load_error})
    return JSONResponse(content={"status": "ok", "dataset": store.summary()})


@app.get("/api/samples")
def sample_addresses() -> list[dict[str, str]]:
    return [
        {
            "label": "Temple landmark",
            "address": "Opposite Ganesh temple, near Civil Lines, Pune 411001",
        },
        {
            "label": "Colony with pincode",
            "address": "A-24, Shivaji Nagar colony, near HDFC Bank, Jaipur 302001",
        },
        {
            "label": "Mixed script",
            "address": "Krishna nagar ke paas, Patel market, Indore 452001",
        },
    ]


@app.get("/api/pincode/{pincode}")
def get_pincode(pincode: str) -> dict[str, Any]:
    if store is None:
        raise HTTPException(status_code=503, detail="Dataset is still loading")
    records = store.lookup(pincode)
    return {
        "pincode": pincode,
        "count": len(records),
        "records": [record.model_dump() for record in records[:20]],
    }


@app.post("/api/resolve", response_model=ResolveResponse)
def resolve_address(request: ResolveRequest) -> ResolveResponse:
    if resolver is None:
        raise HTTPException(status_code=503, detail="Resolver not ready")
    if not request.address.strip():
        raise HTTPException(status_code=400, detail="Address is required")
    return resolver.resolve(request)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Pata API is running"}


def _safe_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() != "nan" else None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return None
        return float(text)
    except Exception:
        return None


def _best_pincode_record(records: list[PincodeRecord]) -> PincodeRecord | None:
    for record in records:
        if record.latitude is not None and record.longitude is not None:
            return record
    return records[0] if records else None


def _best_fallback_record(records: list[PincodeRecord]) -> PincodeRecord | None:
    return _best_pincode_record(records)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def name_similarity(name: str, tokens: list[str]) -> float:
    normalized_name = AddressResolver.tokenize(name)
    if not normalized_name or not tokens:
        return 0.0
    token_set = set(tokens)
    name_set = set(normalized_name)
    overlap = len(token_set & name_set)
    base = overlap / max(len(name_set), 1)
    if overlap == 0:
        joined_tokens = " ".join(tokens)
        joined_name = " ".join(normalized_name)
        base = 0.1 if any(token in joined_name for token in tokens) else 0.0
    return min(1.0, base)
