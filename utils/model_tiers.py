# -*- coding: utf-8 -*-
"""Dynamic model family clustering and N-tier effort resolution.

Automatically groups models from `agy models` cache (.model_cache.json) or fallback catalog
by model family, orders available reasoning modes by effort rank (1, 2, 3, 4, or N tiers),
and maps continuous complexity or discrete tier requests to the closest valid mode in that family.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from config import BASE_DIR

MODEL_CACHE_FILE = os.path.join(BASE_DIR, ".model_cache.json")

FALLBACK_MODELS_CATALOG: List[str] = [
    "gemini-4-argon-medium\tGemini 4 Argon (Medium)",
    "gemini-4-argon-high\tGemini 4 Argon (High)",
    "gemini-4-argon-low\tGemini 4 Argon (Low)",
    "gemini-4-hydrogen-low\tGemini 4 Hydrogen (Low)",
    "gemini-4-hydrogen-medium\tGemini 4 Hydrogen (Medium)",
    "gemini-4-hydrogen-high\tGemini 4 Hydrogen (High)",
    "gemini-4-hydrogen-xhigh\tGemini 4 Hydrogen (XHigh)",
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)",
    "gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)",
    "claude-opus-5-5-low\tClaude Opus 5.5 (Low)",
    "claude-opus-5-5-medium\tClaude Opus 5.5 (Medium)",
    "claude-opus-5-5-high\tClaude Opus 5.5 (High)",
    "claude-sonnet-5-5-low\tClaude Sonnet 5.5 (Low)",
    "claude-sonnet-5-5-medium\tClaude Sonnet 5.5 (Medium)",
    "claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)",
    "gpt-oss-120b-medium\tGPT-OSS 120B (Medium)",
]

# Ordered numeric reasoning strength weights; easily accommodates future 1..N tier models
EFFORT_RANK_MAP: Dict[str, int] = {
    "none": 0,
    "off": 0,
    "instant": 5,
    "minimal": 10,
    "min": 10,
    "fast": 15,
    "lite": 15,
    "quick": 18,
    "low": 20,
    "medium": 40,
    "med": 40,
    "mid": 40,
    "standard": 40,
    "balanced": 40,
    "normal": 40,
    "high": 60,
    "deep": 65,
    "xhigh": 80,
    "max": 85,
    "ultra": 90,
    "extreme": 95,
}

_XHIGH_RE = re.compile(r"^(x+)high$")
_DISPLAY_PAREN_RE = re.compile(r"^(.+?)\s*\(([^()]+)\)\s*$")

_CATALOG_MTIME_CACHE: Tuple[float, List[str]] = (-1.0, [])


def get_effort_rank(token: str) -> Optional[int]:
    """Return numeric reasoning intensity rank for a suffix/label token, or None if not an effort token."""
    t = re.sub(r"[\s_\-]+", "", (token or "").strip().lower())
    if not t:
        return None
    if t in EFFORT_RANK_MAP:
        return EFFORT_RANK_MAP[t]
    m = _XHIGH_RE.match(t)
    if m:
        return 80 + 5 * (len(m.group(1)) - 1)
    return None


def split_model_id_suffix(model_id: str) -> Tuple[str, Optional[str], Optional[int]]:
    """Split trailing recognized effort suffix from a model ID (e.g. 'gemini-4-hydrogen-xhigh')."""
    mid = (model_id or "").strip().lower()
    if not mid or "-" not in mid:
        return mid, None, None
    stem, tail = mid.rsplit("-", 1)
    rank = get_effort_rank(tail)
    if rank is not None and stem:
        return stem, tail, rank
    return mid, None, None


def has_embedded_effort_suffix(model_name: str) -> bool:
    """Return True if model_name ends with a recognized reasoning effort suffix (e.g. -xhigh, -minimal)."""
    _, token, _ = split_model_id_suffix(model_name)
    return token is not None


def _parse_catalog_line(line: str) -> Tuple[str, str]:
    raw = (line or "").strip()
    if not raw:
        return "", ""
    if "\t" in raw:
        mid, disp = raw.split("\t", 1)
        return mid.strip(), disp.strip()
    parts = raw.split(None, 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return parts[0].strip(), parts[0].strip()


def load_model_catalog(available_models: Optional[Sequence[str]] = None) -> List[str]:
    """Load model catalog from explicit override, disk cache (.model_cache.json), or fallback catalog."""
    global _CATALOG_MTIME_CACHE
    if available_models is not None:
        return [str(m).strip() for m in available_models if str(m).strip()]

    if os.path.exists(MODEL_CACHE_FILE):
        try:
            mtime = os.path.getmtime(MODEL_CACHE_FILE)
            if _CATALOG_MTIME_CACHE[0] == mtime and _CATALOG_MTIME_CACHE[1]:
                return _CATALOG_MTIME_CACHE[1]
            with open(MODEL_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            models = data.get("models") if isinstance(data, dict) else None
            if isinstance(models, list) and models:
                cleaned = [str(m).strip() for m in models if str(m).strip()]
                if cleaned:
                    _CATALOG_MTIME_CACHE = (mtime, cleaned)
                    return cleaned
        except Exception:
            pass

    return list(FALLBACK_MODELS_CATALOG)


def _build_family_clusters(
    catalog_lines: Sequence[str],
) -> Tuple[Dict[str, str], Dict[str, List[Tuple[str, int, str]]]]:
    """Cluster available models into ordered family lists sorted from lowest to highest effort.

    Returns:
        id_to_family: mapping lowercase model_id -> canonical family key
        families: mapping family key -> sorted list of (model_id, rank, effort_token)
    """
    parsed_items: List[Tuple[str, str, str, Optional[str], Optional[int], str, str]] = []
    disp_prefix_counts: Dict[str, int] = {}

    for line in catalog_lines:
        mid, disp = _parse_catalog_line(line)
        if not mid:
            continue
        id_stem, id_tok, id_rank = split_model_id_suffix(mid)
        disp_prefix = ""
        disp_paren = ""
        m_disp = _DISPLAY_PAREN_RE.match(disp)
        if m_disp:
            disp_prefix = m_disp.group(1).strip().lower()
            disp_paren = m_disp.group(2).strip().lower()
            disp_prefix_counts[disp_prefix] = disp_prefix_counts.get(disp_prefix, 0) + 1
        parsed_items.append((mid, disp, id_stem, id_tok, id_rank, disp_prefix, disp_paren))

    id_to_family: Dict[str, str] = {}
    families: Dict[str, List[Tuple[str, int, str]]] = {}
    seen_ids: Dict[str, set] = {}

    for mid, disp, id_stem, id_tok, id_rank, disp_prefix, disp_paren in parsed_items:
        disp_rank = get_effort_rank(disp_paren) if disp_paren else None
        is_multi_disp_family = bool(disp_prefix and disp_prefix_counts.get(disp_prefix, 0) >= 2)

        if id_tok is not None and id_rank is not None:
            family_key = id_stem
            eff_tok = id_tok
            eff_rank = id_rank
        elif disp_prefix and (disp_rank is not None or is_multi_disp_family):
            family_key = f"disp:{disp_prefix}"
            eff_tok = re.sub(r"[\s_]+", "-", disp_paren) if disp_paren else "medium"
            eff_rank = disp_rank if disp_rank is not None else 50
        else:
            family_key = mid.lower()
            eff_tok = id_tok or "medium"
            eff_rank = id_rank if id_rank is not None else 40

        mid_low = mid.lower()
        id_to_family[mid_low] = family_key
        bucket_seen = seen_ids.setdefault(family_key, set())
        if mid_low not in bucket_seen:
            bucket_seen.add(mid_low)
            families.setdefault(family_key, []).append((mid, eff_rank, eff_tok))

    for fkey, items in families.items():
        items.sort(key=lambda x: (x[1], x[0]))

    return id_to_family, families


def get_family_ordered_models(
    base_model: str,
    available_models: Optional[Sequence[str]] = None,
) -> List[Tuple[str, int, str]]:
    """Return sorted [(model_id, rank, effort_token)] for the family containing base_model."""
    clean_base = (base_model or "").strip()
    if not clean_base:
        return []
    catalog = load_model_catalog(available_models)
    id_to_family, families = _build_family_clusters(catalog)
    base_low = clean_base.lower()

    fkey = id_to_family.get(base_low)
    if not fkey:
        stem, _, _ = split_model_id_suffix(base_low)
        if stem in families:
            fkey = stem
    if not fkey:
        return []
    return list(families.get(fkey, []))


def resolve_adaptive_model_dynamic(
    base_model: str,
    adaptive_tier: str,
    complexity_score: Optional[float] = None,
    available_models: Optional[Sequence[str]] = None,
) -> str:
    """Resolve adaptive target model within the same model family across 1, 2, 3, 4, or N tiers."""
    if not adaptive_tier or not base_model:
        return base_model

    tier_tok = re.sub(r"[\s_\-]+", "", adaptive_tier.strip().lower())
    target_rank = get_effort_rank(tier_tok)
    if target_rank is None:
        return base_model

    # 1. Dynamic lookup against real catalog (handles 1-tier, 2-tier, 3-tier, 4-tier Hydrogen, future N-tier)
    tiers = get_family_ordered_models(base_model, available_models=available_models)
    if tiers:
        if len(tiers) == 1:
            return base_model

        # Exact token match first (e.g. 'xhigh' -> gemini-4-hydrogen-xhigh)
        for mid, _, tok in tiers:
            if tok == tier_tok:
                return mid

        # Continuous complexity mapping when available and complexity distinguishes within N >= 4 tiers
        if complexity_score is not None and len(tiers) >= 4:
            norm = max(0.0, min(1.0, float(complexity_score) / 2.0))
            if tier_tok == "low":
                return tiers[0][0]
            if tier_tok == "xhigh" or norm >= 0.86:
                return tiers[-1][0]

        # Nearest effort rank selection across arbitrary available tiers;
        # on exact distance tie (e.g. Medium=40 between Low=20 and High=60 on a 2-tier model), pick higher tier.
        best_mid = min(
            tiers,
            key=lambda item: (abs(item[1] - target_rank), -item[1]),
        )[0]
        return best_mid

    # 2. Generic regex fallback when base_model is an unseen model ID absent from catalog cache
    m = base_model.strip().lower()
    stem, tok, _ = split_model_id_suffix(m)
    if tok is not None and stem:
        if target_rank >= 75 and tok in ("xhigh", "max", "ultra"):
            fb_tok = tier_tok if get_effort_rank(tier_tok) else tok
        elif target_rank >= 60:
            fb_tok = "high"
        elif target_rank >= 30:
            fb_tok = "medium"
        else:
            fb_tok = "low"
        return f"{stem}-{fb_tok}"

    return base_model


def infer_effort_from_model_dynamic(model_name: str, fallback_effort: str = "medium") -> str:
    """Infer valid normalized effort string from a model ID or fallback."""
    if not model_name:
        return fallback_effort
    _, tok, rank = split_model_id_suffix(model_name)
    if tok is not None and rank is not None:
        if rank <= 25:
            return "low"
        if rank <= 50:
            return "medium"
        if rank >= 75:
            return tok if tok in ("xhigh", "max", "ultra") else "xhigh"
        return "high"

    fb = (fallback_effort or "medium").strip().lower()
    return fb if fb in ("low", "medium", "high", "xhigh") else "medium"
