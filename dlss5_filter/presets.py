"""Curated DLSS 5 filter presets and JSON profile management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "Realista & Eficiente (FP8 0.70x)": {
        "enabled": 1,
        "applyModel": 1,
        "passes": 1,
        "preset": 2,
        "style": 1,
        "autoMask": 1,
        "intensity": 0.80,
        "localTone": 0.90,
        "localStructure": 0.95,
        "skinStructure": -1.0,
        "sharpness": 0.15,
        "compositionBypass": 0,
        "transfer": 1,
        "transferStrength": 0.85,
        "colourStrength": 0.90,
        "maxRatio": 2.0,
        "reversibleMode": 0,
        "workingScale": 0.70,
        "mvecEnabled": 1,
        "mvecQuality": 1,
        "compareMode": 0,
    },
    "Natural Sutil (Recomendado)": {
        "enabled": 1,
        "applyModel": 1,
        "passes": 1,
        "style": 1,
        "autoMask": 1,
        "intensity": 0.80,
        "localTone": 0.90,
        "localStructure": 0.95,
        "skinStructure": -1.0,
        "sharpness": 0.15,
        "compositionBypass": 0,
        "transferStrength": 0.85,
        "colourStrength": 0.90,
        "maxRatio": 2.0,
        "reversibleMode": 0,
        "workingScale": 1.0,
        "mvecEnabled": 1,
        "mvecQuality": 1,
        "compareMode": 0,
    },
    "Padrão DLSS 5": {
        "enabled": 1,
        "applyModel": 1,
        "passes": 1,
        "style": 0,
        "autoMask": 1,
        "intensity": 1.0,
        "localTone": 1.0,
        "localStructure": 1.0,
        "skinStructure": -1.0,
        "sharpness": 0.25,
        "compositionBypass": 1,
        "transferStrength": 1.0,
        "colourStrength": 1.0,
        "maxRatio": 2.0,
        "reversibleMode": 0,
        "workingScale": 1.0,
        "mvecEnabled": 1,
        "mvecQuality": 1,
        "compareMode": 0,
    },
    "Cinematográfico": {
        "enabled": 1,
        "applyModel": 1,
        "passes": 1,
        "style": 2,
        "autoMask": 1,
        "intensity": 1.15,
        "localTone": 1.15,
        "localStructure": 1.10,
        "skinStructure": 0.85,
        "sharpness": 0.20,
        "compositionBypass": 0,
        "transferStrength": 1.05,
        "colourStrength": 1.0,
        "maxRatio": 2.2,
        "reversibleMode": 0,
        "workingScale": 1.0,
        "mvecEnabled": 1,
        "mvecQuality": 2,
        "compareMode": 0,
    },
    "Multi-Pass Detalhe (2x)": {
        "enabled": 1,
        "applyModel": 1,
        "passes": 2,
        "style": 1,
        "autoMask": 1,
        "intensity": 0.85,
        "localTone": 0.95,
        "localStructure": 1.05,
        "skinStructure": -1.0,
        "sharpness": 0.20,
        "compositionBypass": 0,
        "transferStrength": 0.95,
        "colourStrength": 0.95,
        "maxRatio": 2.0,
        "sdr16Multipass": 1,
        "reversibleMode": 0,
        "workingScale": 1.0,
        "mvecEnabled": 1,
        "mvecQuality": 2,
        "compareMode": 0,
    },
    "Comparação 50/50 (Wipe)": {
        "enabled": 1,
        "applyModel": 1,
        "compareMode": 2,
        "compareSplit": 0.5,
        "compareSwap": 0,
    },
    "Original Limpo (A/B Off)": {
        "enabled": 1,
        "applyModel": 0,
        "compareMode": 0,
    },
}


def profiles_dir() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    d = cfg / "dlss5-filter" / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_user_profiles() -> list[str]:
    return sorted(p.stem for p in profiles_dir().glob("*.json"))


def save_user_profile(name: str, settings: dict[str, Any]) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in " _-.").strip() or "custom"
    target = profiles_dir() / f"{safe}.json"
    target.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    return target


def load_user_profile(name: str) -> dict[str, Any]:
    target = profiles_dir() / f"{name}.json"
    return json.loads(target.read_text(encoding="utf-8"))
