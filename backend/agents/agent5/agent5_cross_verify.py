#!/usr/bin/env python3
"""
Agent 5 — Cross-Verify (BeSt primary + CadGIS fallback)
=======================================================
Compares Agent 4 vision counts with the register classifier:

  1. Check 2 — BeSt Address API (primary)
  2. Check 3 — CadGIS dwelling_count (fallback when BeSt unclear/zero/error)

Then applies the report §9.4 matrix:

  vision N == register N     → Accept, High
  vision 0, register has N    → FROM_REGISTER
  vision N != register N     → MISMATCH → review
  NO_STREETVIEW              → register + NO_IMAGERY
  vision N, register UNKNOWN → Accept vision, Medium

Cache files default to the same Check 2/3 caches you already built
(linked under agent5/cache/ → check 2 and 3 new/.../cache/), matching
how Agent 2 reuses CadGIS data. Override with --best-cache / --cadgis-cache.

All register-client code lives inside this agent folder for debugging.

Usage
-----
    python agent5_cross_verify.py \\
        --detect-json ../output/agent4_detect.json \\
        --out-dir ../output \\
        --offline

    python agent5_cross_verify.py \\
        --detect-json ../output/agent4_detect.json \\
        --out-dir ../output
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from api_cache import CACHE_TTL_SECONDS, cached_urlopen_bytes

# ─────────────────────────────────────────────
#  Paths / defaults (link to Check 2/3 caches)
# ─────────────────────────────────────────────
AGENT5_DIR = Path(__file__).resolve().parent
# backend/agents/agent5 → repo root is parents[2]
REPO_ROOT = AGENT5_DIR.parents[2]
PROJECT_ROOT = REPO_ROOT
CHECK23_CACHE = (
    REPO_ROOT
    / "check 2 and 3 new"
    / "check 2 and 3"
    / "cache"
)
LOCAL_CACHE = AGENT5_DIR / "cache"

DEFAULT_BEST_CACHE = (
    CHECK23_CACHE / "best_api_cache.json"
    if (CHECK23_CACHE / "best_api_cache.json").exists()
    else LOCAL_CACHE / "best_api_cache.json"
)
DEFAULT_CADGIS_CACHE = (
    CHECK23_CACHE / "cadgis_dwelling_cache.json"
    if (CHECK23_CACHE / "cadgis_dwelling_cache.json").exists()
    else LOCAL_CACHE / "cadgis_dwelling_cache.json"
)

BEST_API_BASE_URL = os.environ.get(
    "BEST_API_BASE_URL",
    "https://best.pr.fedservices.be/api/opendata/best/v1/belgianAddress/v2",
)
BEST_API_TIMEOUT_SEC = float(os.environ.get("BEST_API_TIMEOUT_SEC", "45"))
CADGIS_ADDRESS_QUERY_URL = (
    "https://ccff02.minfin.fgov.be/geoservices/arcgis/rest/services/"
    "WMS/Cadastral_Layers/MapServer/9/query"
)
CADGIS_TIMEOUT_SEC = float(os.environ.get("CADGIS_TIMEOUT_SEC", "60"))
PREFER_LIVE_CADGIS = os.environ.get("PREFER_LIVE_CADGIS", "false").lower() in (
    "1", "true", "yes",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent5")


def _fresh_cache_entry(entry: Optional[dict]) -> bool:
    """Legacy file entries without a timestamp are stale by design."""
    if not entry or not entry.get("queried_at"):
        return False
    try:
        queried_at = datetime.fromisoformat(str(entry["queried_at"]).replace("Z", "+00:00"))
        if queried_at.tzinfo is None:
            queried_at = queried_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - queried_at).total_seconds() < CACHE_TTL_SECONDS
    except (TypeError, ValueError):
        return False


# ─────────────────────────────────────────────
#  Text helpers
# ─────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _norm_street(text: str) -> str:
    text = _strip_accents(text or "").lower().strip()
    text = text.replace(".", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    if text.endswith(".0"):
        head = text[:-2]
        if head.isdigit():
            return head
    return text


def normalize_street_name(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"[._/\\-]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _streets_match(query_street: str, api_street: str) -> bool:
    q = _norm_street(query_street)
    a = _norm_street(api_street)
    if not q or not a:
        return False
    if q == a:
        return True
    q_tokens, a_tokens = q.split(), a.split()
    if q_tokens and a_tokens and q_tokens[-1] == a_tokens[-1]:
        if len(q_tokens) == 1 or len(a_tokens) == 1:
            return q in a or a in q
        return all(
            any(at.startswith(qt) or qt.startswith(at) for at in a_tokens[:-1])
            for qt in q_tokens[:-1]
            if len(qt) >= 2
        )
    return q in a or a in q


def _sql_escape(value: str) -> str:
    return value.replace("'", "''")


# ─────────────────────────────────────────────
#  BeSt client (Check 2) — self-contained
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class AddressQuery:
    postcode: str
    house_number: str
    street_name: str = ""

    def cache_key(self) -> str:
        return "|".join(
            [
                self.postcode.strip().lower(),
                self.street_name.strip().lower(),
                self.house_number.strip().lower(),
            ]
        )


@dataclass
class BestLookupResult:
    record_count: Optional[int]
    status: str  # ok | zero | unclear | error
    message: str = ""
    from_cache: bool = False


class BestApiClient:
    def __init__(
        self,
        cache_path: Optional[Path] = None,
        base_url: Optional[str] = None,
        timeout: float = BEST_API_TIMEOUT_SEC,
        workers: int = 4,
        use_api_cache: bool = True,
    ) -> None:
        self.base_url = (base_url or BEST_API_BASE_URL).rstrip("/")
        self.cache_path = Path(cache_path or DEFAULT_BEST_CACHE)
        self.timeout = timeout
        self.workers = workers
        self.use_api_cache = use_api_cache
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.use_api_cache:
            return
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
                logger.info(
                    "BeSt cache: %d entries (%s)", len(self._cache), self.cache_path
                )
            except Exception as exc:
                logger.warning("BeSt cache unreadable: %s", exc)
                self._cache = {}

    def save_cache(self) -> None:
        if not self.use_api_cache:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _store(self, key: str, result: BestLookupResult) -> None:
        if not self.use_api_cache:
            return
        with self._lock:
            self._cache[key] = {
                "record_count": result.record_count,
                "status": result.status,
                "message": result.message,
                "queried_at": datetime.now(timezone.utc).isoformat(),
            }

    def lookup(self, query: AddressQuery, use_network: bool = True) -> BestLookupResult:
        key = query.cache_key()
        with self._lock:
            cached = self._cache.get(key)
        # Offline mode may intentionally use a manually seeded legacy cache.
        if self.use_api_cache and (
            _fresh_cache_entry(cached) or (not use_network and cached is not None)
        ):
            return BestLookupResult(
                cached.get("record_count"),
                cached.get("status", "unclear"),
                cached.get("message", ""),
                from_cache=True,
            )
        if not use_network:
            return BestLookupResult(None, "unclear", "Offline; not in BeSt cache")
        if not query.postcode or not query.house_number:
            result = BestLookupResult(None, "unclear", "Missing postcode or house")
            self._store(key, result)
            return result
        result = self._resolve(query)
        self._store(key, result)
        return result

    def _http_get(self, params: dict) -> dict:
        url = f"{self.base_url}/addresses?{urllib.parse.urlencode(params)}"
        raw = cached_urlopen_bytes(
            url,
            service="best-address",
            timeout=self.timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "MailboxPipeline/Agent5",
            },
            use_cache=self.use_api_cache,
        )
        return json.loads(raw.decode("utf-8"))

    def _query_total(
        self, postcode: str, house_number: str, street_name: str = ""
    ) -> Tuple[Optional[int], str]:
        params = {
            "postCode": postcode,
            "houseNumber": house_number,
            "firstOfBuilding": "false",
            "pageSize": "1",
            "page": "1",
            "status": "current",
        }
        if street_name:
            params["streetName"] = street_name
        try:
            payload = self._http_get(params)
        except Exception as exc:
            return None, str(exc)
        total = payload.get("total")
        if total is None:
            total = len(payload.get("items") or [])
        try:
            return int(total), "ok"
        except (TypeError, ValueError):
            return None, f"Invalid total: {total!r}"

    def _count_filtered_by_street(
        self, postcode: str, house_number: str, street_name: str
    ) -> Tuple[Optional[int], str]:
        params = {
            "postCode": postcode,
            "houseNumber": house_number,
            "firstOfBuilding": "false",
            "pageSize": "250",
            "page": "1",
            "status": "current",
        }
        try:
            payload = self._http_get(params)
        except Exception as exc:
            return None, str(exc)
        items = payload.get("items") or []

        def _extract_street(item: dict) -> str:
            street = item.get("hasStreetName") or {}
            name = street.get("name") or {}
            for lang in ("nl", "fr", "de"):
                if name.get(lang):
                    return str(name[lang])
            return ""

        matched = [
            item for item in items if _streets_match(street_name, _extract_street(item))
        ]
        return len(matched), "filtered"

    def _resolve(self, query: AddressQuery) -> BestLookupResult:
        post, house, street = query.postcode, query.house_number, query.street_name
        if street:
            count, meta = self._query_total(post, house, street)
            if count is None:
                return BestLookupResult(None, "error", meta)
            if count > 0:
                return BestLookupResult(count, "ok", "BeSt API OK (street match)")
            filtered, fmeta = self._count_filtered_by_street(post, house, street)
            if filtered is None:
                return BestLookupResult(None, "error", fmeta)
            if filtered > 0:
                return BestLookupResult(filtered, "ok", f"BeSt OK (street filter)")

        count, meta = self._query_total(post, house, "")
        if count is None:
            return BestLookupResult(None, "error", meta)
        if not street and count > 20:
            return BestLookupResult(
                count, "unclear", f"Ambiguous without street (total={count})"
            )
        if count > 0:
            if street:
                return BestLookupResult(
                    0, "zero", f"No BeSt records for street '{street}'"
                )
            return BestLookupResult(count, "ok", "BeSt API OK (postcode+house)")

        m = re.match(r"^(\d+)", house)
        if m and m.group(1) != house:
            alt = m.group(1)
            count, meta = self._query_total(post, alt, street or "")
            if count and count > 0:
                return BestLookupResult(count, "ok", f"BeSt OK (house {house}->{alt})")
        return BestLookupResult(0, "zero", "No BeSt records found")


# ─────────────────────────────────────────────
#  CadGIS client (Check 3) — self-contained
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class AddressKey:
    postcode: str
    house_number: str
    street_name: str = ""

    def cache_key(self) -> str:
        return "|".join(
            [
                self.postcode.strip().lower(),
                self.street_name.strip().lower(),
                self.house_number.strip().lower(),
            ]
        )


@dataclass
class DwellingResult:
    dwelling_count: Optional[int]
    status: str  # ok | unknown | error
    message: str = ""
    source: str = ""
    from_cache: bool = False


def filter_exact_street_matches(input_street: str, features: list) -> list:
    target = normalize_street_name(input_street)
    if not target:
        return list(features or [])
    retained = []
    for feature in features or []:
        attrs = feature.get("attributes") or {}
        candidates = []
        for key in ("StreetNameDUT", "StreetNameFRE", "StreetName", "street_name"):
            if attrs.get(key) is not None:
                candidates.append(str(attrs[key]))
        if target in [normalize_street_name(v) for v in candidates]:
            retained.append(feature)
    return retained


class CadgisDwellingClient:
    def __init__(
        self,
        cache_path: Optional[Path] = None,
        use_network: bool = True,
        prefer_live: Optional[bool] = None,
        workers: int = 3,
        use_api_cache: bool = True,
    ) -> None:
        self.cache_path = Path(cache_path or DEFAULT_CADGIS_CACHE)
        self.use_network = use_network
        self.prefer_live = PREFER_LIVE_CADGIS if prefer_live is None else prefer_live
        self.workers = workers
        self.use_api_cache = use_api_cache
        self._cache: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.use_api_cache:
            return
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
                logger.info(
                    "CadGIS cache: %d entries (%s)", len(self._cache), self.cache_path
                )
            except Exception as exc:
                logger.warning("CadGIS cache unreadable: %s", exc)
                self._cache = {}

    def save_cache(self) -> None:
        if not self.use_api_cache:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _store(self, key: str, result: DwellingResult) -> None:
        if not self.use_api_cache:
            return
        with self._lock:
            self._cache[key] = {
                "dwelling_count": result.dwelling_count,
                "status": result.status,
                "message": result.message,
                "source": result.source,
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "cached": True,
            }

    def lookup(self, address: AddressKey) -> DwellingResult:
        key = address.cache_key()
        with self._lock:
            cached = self._cache.get(key)
        if self.use_api_cache and (
            _fresh_cache_entry(cached) or (not self.use_network and cached is not None)
        ) and not self.prefer_live:
            return DwellingResult(
                cached.get("dwelling_count"),
                cached.get("status", "unknown"),
                cached.get("message", ""),
                source=cached.get("source", "cache"),
                from_cache=True,
            )

        if self.prefer_live and self.use_network:
            try:
                result = self._query_cadgis(address)
                self._store(key, result)
                return result
            except Exception as exc:
                logger.warning("CadGIS live failed: %s", exc)

        with self._lock:
            cached = self._cache.get(key)
        if self.use_api_cache and (
            _fresh_cache_entry(cached) or (not self.use_network and cached is not None)
        ):
            return DwellingResult(
                cached.get("dwelling_count"),
                cached.get("status", "unknown"),
                cached.get("message", ""),
                source="cache",
                from_cache=True,
            )

        if not self.use_network:
            return DwellingResult(None, "unknown", "Offline; not in CadGIS cache")

        result = self._query_cadgis(address)
        self._store(key, result)
        return result

    def _arcgis_fetch_features(self, where: str) -> list:
        params = {
            "where": where,
            "outFields": "StreetNameDUT,StreetNameFRE,HouseNumber,BoxNumber,PostCode,Status",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": "200",
        }
        url = CADGIS_ADDRESS_QUERY_URL + "?" + urllib.parse.urlencode(params)
        raw = cached_urlopen_bytes(
            url,
            service="cadgis-address",
            timeout=CADGIS_TIMEOUT_SEC,
            headers={"User-Agent": "MailboxPipeline/Agent5"},
            use_cache=self.use_api_cache,
        )
        payload = json.loads(raw.decode("utf-8"))
        if "error" in payload:
            raise RuntimeError(str(payload["error"]))
        return payload.get("features") or []

    def _query_cadgis(self, address: AddressKey) -> DwellingResult:
        post = _sql_escape(normalize_text(address.postcode))
        house = _sql_escape(normalize_text(address.house_number))
        street = normalize_text(address.street_name)
        exact_street = normalize_street_name(street)

        where_parts = [
            f"PostCode='{post}'",
            f"HouseNumber='{house}'",
            "(Status='Current' OR Status IS NULL OR Status='')",
        ]
        if exact_street:
            where_parts.append(
                f"(StreetNameDUT = '{_sql_escape(street)}' OR "
                f"StreetNameFRE = '{_sql_escape(street)}')"
            )
        where = " AND ".join(where_parts)

        try:
            features = self._arcgis_fetch_features(where)
        except Exception as exc:
            return DwellingResult(None, "error", f"CadGIS query failed: {exc}", "CadGIS")

        if not features and exact_street:
            where_no_street = " AND ".join(
                [
                    f"PostCode='{post}'",
                    f"HouseNumber='{house}'",
                    "(Status='Current' OR Status IS NULL OR Status='')",
                ]
            )
            try:
                features = self._arcgis_fetch_features(where_no_street)
            except Exception as exc:
                return DwellingResult(
                    None, "error", f"CadGIS fallback failed: {exc}", "CadGIS"
                )

        filtered = filter_exact_street_matches(street, features)
        final_count = len(filtered)
        if final_count <= 0:
            return DwellingResult(
                0, "unknown", "No exact CadGIS street match", source="CadGIS"
            )
        return DwellingResult(
            final_count,
            "ok",
            f"CadGIS Address layer: {final_count} exact dwelling(s)",
            source="CadGIS",
        )


# ─────────────────────────────────────────────
#  Register classification (BeSt → CadGIS)
# ─────────────────────────────────────────────

@dataclass
class RegisterResult:
    register_count: Optional[int]
    predicted_type: str
    check_source: str
    best_record_count: Optional[int]
    dwelling_count: Optional[int]
    reason: str
    best_status: str
    cadgis_status: str


def apply_register(best: BestLookupResult, fps: DwellingResult) -> RegisterResult:
    """BeSt primary; CadGIS only when BeSt is 0/unclear/error."""
    if best.status == "ok" and best.record_count is not None:
        if best.record_count == 1:
            return RegisterResult(
                1, "SFU", "BeSt API", 1, None,
                "Check 2 BeSt: 1 record", best.status, fps.status,
            )
        if best.record_count >= 2:
            return RegisterResult(
                best.record_count, "MDU", "BeSt API", best.record_count, None,
                f"Check 2 BeSt: {best.record_count} records", best.status, fps.status,
            )

    if fps.status == "ok" and fps.dwelling_count is not None:
        if fps.dwelling_count == 1:
            return RegisterResult(
                1, "SFU", "CadGIS/FPS", best.record_count, 1,
                f"Check 3 CadGIS after BeSt {best.status}", best.status, fps.status,
            )
        if fps.dwelling_count >= 2:
            return RegisterResult(
                fps.dwelling_count, "MDU", "CadGIS/FPS", best.record_count,
                fps.dwelling_count,
                f"Check 3 CadGIS dwelling_count={fps.dwelling_count}",
                best.status, fps.status,
            )

    return RegisterResult(
        None, "UNKNOWN", "none", best.record_count, fps.dwelling_count,
        f"Inconclusive (BeSt={best.status}, CadGIS={fps.status})",
        best.status, fps.status,
    )


def cross_verify_vision(
    vision_count: Optional[int],
    vision_status: str,
    register: RegisterResult,
    vision_confidence: Optional[str] = None,
) -> Dict[str, Any]:
    """Report §9.4 matrix."""
    flags: List[str] = []
    reg_n = register.register_count

    if vision_status == "NO_STREETVIEW" or "NO_IMAGERY" in (vision_status or ""):
        return {
            "final_count": reg_n,
            "count_source": "register" if reg_n is not None else "none",
            "confidence": "Medium" if reg_n is not None else "Low",
            "flags": ["NO_IMAGERY", "FROM_REGISTER"] if reg_n is not None else ["NO_IMAGERY"],
            "action": "use_register_no_imagery",
        }

    if vision_count is None:
        return {
            "final_count": reg_n,
            "count_source": "register" if reg_n is not None else "none",
            "confidence": "Low",
            "flags": ["NO_VISION"] + (["FROM_REGISTER"] if reg_n is not None else []),
            "action": "use_register_no_vision",
        }

    if reg_n is None or register.predicted_type == "UNKNOWN":
        return {
            "final_count": vision_count,
            "count_source": "vision",
            "confidence": vision_confidence or "Medium",
            "flags": ["REGISTER_UNKNOWN"],
            "action": "accept_vision_register_unknown",
        }

    if vision_count == 0 and reg_n is not None and reg_n > 0:
        return {
            "final_count": reg_n,
            "count_source": "register",
            "confidence": "Medium",
            "flags": ["FROM_REGISTER"],
            "action": "use_register_zero_vision",
        }

    if vision_count == reg_n:
        return {
            "final_count": vision_count,
            "count_source": "both",
            "confidence": "High",
            "flags": [],
            "action": "accept_match",
        }

    return {
        "final_count": None,
        "count_source": "conflict",
        "confidence": "Ambiguous",
        "flags": ["MISMATCH"],
        "action": "review_mismatch",
        "vision_count": vision_count,
        "register_count": reg_n,
    }


# ─────────────────────────────────────────────
#  Pipeline
# ─────────────────────────────────────────────

def process_site(
    site: Dict[str, Any],
    best_client: BestApiClient,
    cadgis_client: CadgisDwellingClient,
    *,
    use_network: bool,
) -> Dict[str, Any]:
    postcode = normalize_text(site.get("postcode"))
    street = normalize_text(site.get("street_norm"))
    house = normalize_text(site.get("hnr_base"))

    q = AddressQuery(postcode, house, street)
    best = best_client.lookup(q, use_network=use_network)

    # CadGIS only when BeSt does not decide (same as Check 2/3 excel flow)
    need_cadgis = not (best.status == "ok" and best.record_count and best.record_count >= 1)
    if need_cadgis:
        fps = cadgis_client.lookup(AddressKey(postcode, house, street))
    else:
        fps = DwellingResult(None, "skipped", "BeSt already decided", source="skipped")

    register = apply_register(best, fps)
    vision_count = site.get("vision_count")
    vision_status = site.get("status") or "OK"
    decision = cross_verify_vision(
        vision_count if isinstance(vision_count, int) else None,
        vision_status,
        register,
        vision_confidence=site.get("confidence"),
    )

    flags = sorted(set(list(site.get("flags") or []) + decision.get("flags", [])))
    status = "OK"
    if "MISMATCH" in flags:
        status = "MISMATCH"
    elif vision_status == "NO_STREETVIEW":
        status = "NO_STREETVIEW"
    elif decision.get("final_count") is None and "MISMATCH" not in flags:
        status = "TBC"

    return {
        "site_id": site.get("site_id"),
        "postcode": postcode,
        "street_norm": street,
        "hnr_base": house,
        "vision_count": vision_count,
        "vision_confidence": site.get("confidence"),
        "vision_status": vision_status,
        "register_count": register.register_count,
        "predicted_type": register.predicted_type,
        "check_source": register.check_source,
        "best_record_count": register.best_record_count,
        "dwelling_count": register.dwelling_count,
        "register_reason": register.reason,
        "best_status": register.best_status,
        "cadgis_status": register.cadgis_status,
        "best_from_cache": best.from_cache,
        "cadgis_from_cache": fps.from_cache,
        "final_count": decision.get("final_count"),
        "count_source": decision.get("count_source"),
        "confidence": decision.get("confidence"),
        "action": decision.get("action"),
        "flags": flags,
        "status": status,
        "mailboxes": site.get("mailboxes") or [],
    }


def run(
    detect_json: Path,
    out_dir: Path,
    *,
    best_cache: Optional[Path] = None,
    cadgis_cache: Optional[Path] = None,
    offline: bool = False,
    max_sites: Optional[int] = None,
    use_api_cache: bool = True,
) -> List[Dict[str, Any]]:
    sites = json.loads(Path(detect_json).read_text(encoding="utf-8"))
    if max_sites is not None:
        sites = sites[:max_sites]

    best_client = BestApiClient(
        cache_path=best_cache or DEFAULT_BEST_CACHE,
        use_api_cache=use_api_cache,
    )
    cadgis_client = CadgisDwellingClient(
        cache_path=cadgis_cache or DEFAULT_CADGIS_CACHE,
        use_network=not offline,
        use_api_cache=use_api_cache,
    )

    results: List[Dict[str, Any]] = []
    total = len(sites)
    for i, site in enumerate(sites):
        res = process_site(
            site, best_client, cadgis_client, use_network=not offline
        )
        results.append(res)
        print(
            f"  [{i+1:>5}/{total}] {res['site_id']}  status={res['status']:12}  "
            f"vision={res.get('vision_count')}  reg={res.get('register_count')}  "
            f"final={res.get('final_count')}  src={res.get('count_source')}"
        )

    if not offline and use_api_cache:
        best_client.save_cache()
        cadgis_client.save_cache()

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent5_cross_verify.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    match = sum(1 for r in results if r.get("action") == "accept_match")
    mismatch = sum(1 for r in results if "MISMATCH" in (r.get("flags") or []))
    from_reg = sum(1 for r in results if "FROM_REGISTER" in (r.get("flags") or []))

    print("\n" + "=" * 60)
    print("  Agent 5 complete")
    print(f"  Sites processed   : {total}")
    print(f"  Accept match      : {match}")
    print(f"  FROM_REGISTER     : {from_reg}")
    print(f"  MISMATCH          : {mismatch}")
    print(f"  BeSt cache        : {best_client.cache_path}")
    print(f"  CadGIS cache      : {cadgis_client.cache_path}")
    print(f"  Result JSON       : {out_path}")
    print("=" * 60 + "\n")
    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Agent 5 - Cross-Verify (BeSt primary, CadGIS fallback).\n"
            "Reads agent4_detect.json, writes agent5_cross_verify.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--detect-json", required=True, help="agent4_detect.json")
    p.add_argument("--out-dir", "-o", default=".")
    p.add_argument(
        "--best-cache", default=None,
        help=f"BeSt cache JSON (default: {DEFAULT_BEST_CACHE})",
    )
    p.add_argument(
        "--cadgis-cache", default=None,
        help=f"CadGIS cache JSON (default: {DEFAULT_CADGIS_CACHE})",
    )
    p.add_argument(
        "--offline", action="store_true",
        help="Use caches only; do not call BeSt / CadGIS live APIs.",
    )
    p.add_argument("--max-sites", type=int, default=None)
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    run(
        detect_json=Path(args.detect_json).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        best_cache=Path(args.best_cache).resolve() if args.best_cache else None,
        cadgis_cache=Path(args.cadgis_cache).resolve() if args.cadgis_cache else None,
        offline=args.offline,
        max_sites=args.max_sites,
    )


if __name__ == "__main__":
    main()
