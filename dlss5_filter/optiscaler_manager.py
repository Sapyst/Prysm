"""OptiScaler-NR v0.8.93 (Pre-SR + Peripheral Spatial Compression) per-game manager."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .backend_manager import (
    discover_runners,
    install_neural_dll_from_git,
    project_cache_dir,
    sha256_file,
    xdg_dirs,
)
from .backend_manager import _download_with_sha256

OPTISCALER_NR_RELEASE = {
    "version": "0.8.93",
    "filename": "OptiScaler-NR-v0.8.93.zip",
    "url": "https://github.com/jlrouzies-fr/OptiScaler-DLSSNR-PreSR-Multipass/releases/download/v0.8.93/OptiScaler-NR-v0.8.93.zip",
    "sha256": "8003065d88813b342049b869e8c894f8771c58f3d1a630ddf42fabd5c9d2f5fc",
}

HOOK_OPTIONS = [
    ("dxgi.dll", "dxgi.dll — Padrão DX12 (Proton vkd3d-proton)"),
    ("winmm.dll", "winmm.dll — Recomendado Vulkan / DX11 (não sobrescreve DXVK dxgi.dll)"),
    ("dbghelp.dll", "dbghelp.dll — Alternativa segura (ex: Cyberpunk 2077)"),
    ("version.dll", "version.dll — Alternativa genérica"),
    ("d3d12.dll", "d3d12.dll — Hook direto D3D12"),
]

REALISTIC_PRESR_DEFAULTS: dict[str, str] = {
    "Enabled": "true",
    "RunBeforeSR": "true",
    "ResidualAcrossRR": "true",
    "Passes": "1",
    "Preset": "2",  # Optimized smaller FP8 model in nvngx_dlssnr.dll 310.9.16
    "Style": "1",  # Natural
    "Intensity": "0.80",
    "LocalStructure": "0.95",
    "LocalTone": "0.90",
    "SkinStructure": "-1.0",
    "AutoMask": "true",
    "WorkingScale": "1.0",
    "SpatialCompression": "true",
    "SpatialCenterX": "80",
    "SpatialCenterY": "80",
    "SpatialWorkX": "90",
    "SpatialWorkY": "90",
    "Transfer": "1",
    "TransferStrength": "0.85",
    "ColourStrength": "0.90",
    "MaxRatio": "2.0",
    "ReversibleMode": "0",
}

PRESR_PROFILES: dict[str, dict[str, str]] = {
    "Realista & Eficiente (Pre-SR + FP8 Otimizado + Compressão 80/90)": dict(REALISTIC_PRESR_DEFAULTS),
    "Máxima Eficiência (Pre-SR + WorkingScale 0.75 + Compressão 80/90)": {
        **REALISTIC_PRESR_DEFAULTS,
        "WorkingScale": "0.75",
        "Intensity": "0.85",
    },
    "Fidelidade Máxima (Pre-SR + WorkingScale 1.0 + Sem Compressão)": {
        **REALISTIC_PRESR_DEFAULTS,
        "SpatialCompression": "false",
        "WorkingScale": "1.0",
        "Preset": "0",
        "Intensity": "0.90",
    },
    "Cinematográfico Pre-SR": {
        **REALISTIC_PRESR_DEFAULTS,
        "Style": "2",
        "Intensity": "1.05",
        "LocalTone": "1.10",
        "LocalStructure": "1.05",
        "TransferStrength": "0.95",
    },
}

MANIFEST_NAME = ".dlss5-presr-manifest.json"


@dataclass
class PreSRStatus:
    installed: bool
    game_dir: Path
    hook_dll: str
    api_mode: str
    has_neural_dll: bool
    ini_path: Path
    steam_launch_option: str
    dlssnr_keys: dict[str, str]


def _set_ini_key(text: str, section: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    sec_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=).*$", re.IGNORECASE)

    in_sec = False
    sec_found = False
    sec_end_idx = len(lines)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        m_sec = sec_re.match(stripped)
        if m_sec:
            if in_sec:
                sec_end_idx = idx
                break
            if m_sec.group(1).strip().lower() == section.lower():
                in_sec = True
                sec_found = True
            continue
        if in_sec and not stripped.startswith(";"):
            m_k = key_re.match(line.rstrip("\r\n"))
            if m_k:
                eol = "\r\n" if line.endswith("\r\n") else "\n"
                lines[idx] = f"{m_k.group(1)}{value}{eol}"
                return "".join(lines)

    if sec_found:
        lines.insert(sec_end_idx, f"{key}={value}\n")
        return "".join(lines)

    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")
    lines.append(f"[{section}]\n{key}={value}\n")
    return "".join(lines)


def read_ini_section(ini_path: Path, section: str) -> dict[str, str]:
    if not ini_path.is_file():
        return {}
    out: dict[str, str] = {}
    in_sec = False
    for raw in ini_path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith(";"):
            continue
        if s.startswith("[") and s.endswith("]"):
            in_sec = s[1:-1].strip().lower() == section.lower()
            continue
        if in_sec and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def update_dlssnr_ini(game_dir: Path, updates: dict[str, str]) -> Path:
    ini_path = game_dir / "OptiScaler.ini"
    if not ini_path.is_file():
        raise FileNotFoundError(f"OptiScaler.ini não encontrado em {game_dir}")
    text = ini_path.read_text(encoding="utf-8", errors="replace")
    for k, v in updates.items():
        text = _set_ini_key(text, "DlssNr", k, str(v))
    ini_path.write_text(text, encoding="utf-8")
    return ini_path


def ensure_optiscaler_archive(log: Callable[[str], None] = print) -> Path:
    archive = project_cache_dir() / OPTISCALER_NR_RELEASE["filename"]
    return _download_with_sha256(
        OPTISCALER_NR_RELEASE["url"], archive, OPTISCALER_NR_RELEASE["sha256"], log
    )


def steam_launch_option_for_hook(hook_dll: str) -> str:
    stem = Path(hook_dll).stem
    return f'PROTON_ENABLE_NVAPI=1 WINEDLLOVERRIDES="{stem}=n,b" %command%'


def get_presr_status(game_dir: Path) -> PreSRStatus:
    manifest_file = game_dir / MANIFEST_NAME
    hook_dll = "dxgi.dll"
    api_mode = "dx12"
    installed = False
    if manifest_file.is_file():
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            hook_dll = data.get("hook_dll", "dxgi.dll")
            api_mode = data.get("api_mode", "dx12")
            installed = (game_dir / hook_dll).is_file() and (game_dir / "OptiScaler.ini").is_file()
        except Exception:
            pass
    elif (game_dir / "OptiScaler.ini").is_file():
        for candidate, _ in HOOK_OPTIONS:
            if (game_dir / candidate).is_file():
                hook_dll = candidate
                installed = True
                break

    ini_path = game_dir / "OptiScaler.ini"
    return PreSRStatus(
        installed=installed,
        game_dir=game_dir,
        hook_dll=hook_dll,
        api_mode=api_mode,
        has_neural_dll=(game_dir / "nvngx_dlssnr.dll").is_file(),
        ini_path=ini_path,
        steam_launch_option=steam_launch_option_for_hook(hook_dll),
        dlssnr_keys=read_ini_section(ini_path, "DlssNr"),
    )


def install_presr_in_game(
    game_dir: Path,
    hook_dll: str = "dxgi.dll",
    api_mode: str = "dx12",  # 'dx12' | 'dx11' | 'vulkan'
    exe_name: str = "auto",
    profile: dict[str, str] | None = None,
    log: Callable[[str], None] = print,
) -> PreSRStatus:
    if not game_dir.is_dir():
        raise NotADirectoryError(f"Diretório inválido: {game_dir}")

    archive = ensure_optiscaler_archive(log)

    # Ensure we have nvngx_dlssnr.dll locally
    neural_src = xdg_dirs()["data"] / "binaries" / "nvngx_dlssnr.dll"
    if not neural_src.is_file():
        log("nvngx_dlssnr.dll não encontrada localmente; baixando versão 310.9.16...")
        neural_src = install_neural_dll_from_git(log)

    # Remove previous manifest install if switching hook
    if (game_dir / MANIFEST_NAME).is_file():
        uninstall_presr_from_game(game_dir, log=lambda _m: None)

    created_files: list[str] = []
    backed_up: dict[str, str] = {}

    def _backup_if_exists(target: Path) -> None:
        if target.exists():
            bak = target.with_name(target.name + ".dlss5_bak")
            if not bak.exists():
                shutil.copy2(target, bak)
            backed_up[str(target.relative_to(game_dir))] = str(bak.relative_to(game_dir))

    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if name.startswith(("docs/", "images/", "!!", "tests/")) or name in {
                "setup_windows.bat",
                "setup_linux.sh",
                "CONTRIBUTING.md",
                "SHA256SUMS.txt",
            }:
                continue

            rel_dest = hook_dll if name == "OptiScaler.dll" else name
            dest = game_dir / rel_dest
            dest.parent.mkdir(parents=True, exist_ok=True)
            _backup_if_exists(dest)
            with zf.open(member, "r") as src_f, dest.open("wb") as out_f:
                shutil.copyfileobj(src_f, out_f)
            created_files.append(rel_dest)

    # Copy nvngx_dlssnr.dll beside game exe
    dll_dest = game_dir / "nvngx_dlssnr.dll"
    if not dll_dest.exists():
        shutil.copy2(neural_src, dll_dest)
        created_files.append("nvngx_dlssnr.dll")
        log("nvngx_dlssnr.dll 310.9.16 copiada para a pasta do jogo.")
    else:
        log("nvngx_dlssnr.dll já presente na pasta do jogo (mantida).")

    # Configure OptiScaler.ini for Proton + Pre-SR realistic efficiency
    ini_path = game_dir / "OptiScaler.ini"
    ini_text = ini_path.read_text(encoding="utf-8", errors="replace")

    upscaler_updates = {
        ("Spoofing", "Dxgi"): "false",
        ("Plugins", "LoadAsiPlugins"): "false",
        ("ProcessFilter", "TargetProcessName"): exe_name or "auto",
        ("Log", "LogToFile"): "true",
        ("Log", "LogLevel"): "2",
    }
    if api_mode == "dx11":
        upscaler_updates[("Upscalers", "Dx11Upscaler")] = "ffx_12"
    elif api_mode == "vulkan":
        upscaler_updates[("Upscalers", "VulkanUpscaler")] = "dlss"
    else:
        upscaler_updates[("Upscalers", "Dx12Upscaler")] = "dlss"

    for (sec, k), v in upscaler_updates.items():
        ini_text = _set_ini_key(ini_text, sec, k, v)

    nr_cfg = profile or REALISTIC_PRESR_DEFAULTS
    for k, v in nr_cfg.items():
        ini_text = _set_ini_key(ini_text, "DlssNr", k, v)

    ini_path.write_text(ini_text, encoding="utf-8")

    manifest_data = {
        "version": OPTISCALER_NR_RELEASE["version"],
        "hook_dll": hook_dll,
        "api_mode": api_mode,
        "created_files": created_files,
        "backed_up": backed_up,
    }
    (game_dir / MANIFEST_NAME).write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    log(f"OptiScaler-NR v{OPTISCALER_NR_RELEASE['version']} (Pre-SR) instalado em {game_dir} como {hook_dll}")
    return get_presr_status(game_dir)


def uninstall_presr_from_game(game_dir: Path, log: Callable[[str], None] = print) -> None:
    manifest_file = game_dir / MANIFEST_NAME
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Nenhum manifesto Pre-SR ({MANIFEST_NAME}) encontrado em {game_dir}")

    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    for rel in data.get("created_files", []):
        p = game_dir / rel
        if p.is_file():
            p.unlink(missing_ok=True)
    for rel_target, rel_bak in data.get("backed_up", {}).items():
        bak = game_dir / rel_bak
        target = game_dir / rel_target
        if bak.is_file():
            shutil.move(str(bak), str(target))

    # Clean empty OptiScaler/Licenses dirs if created
    for dname in ("OptiScaler", "Licenses"):
        d = game_dir / dname
        if d.is_dir():
            for root, dirs, files in os.walk(d, topdown=False):
                rp = Path(root)
                if not files and not dirs:
                    rp.rmdir()
    manifest_file.unlink(missing_ok=True)
    log(f"OptiScaler-NR removido de {game_dir}")


def launch_proton_presr_game(
    exe_path: Path,
    hook_dll: str,
    prefix_dir: Path | None = None,
) -> subprocess.Popen[bytes]:
    runners = discover_runners()
    if not runners:
        raise RuntimeError("Nenhum runner Proton/Wine encontrado.")
    runner = runners[0]

    env = os.environ.copy()
    # Explicitly do NOT enable VKLayer_DLSS5 so we never double-process Pre-SR + post-present.
    env.pop("VKLayer_DLSS5", None)
    env["PROTON_ENABLE_NVAPI"] = "1"
    env["DXVK_ENABLE_NVAPI"] = "1"
    stem = Path(hook_dll).stem
    existing_ovr = env.get("WINEDLLOVERRIDES", "")
    ovr = f"{stem}=n,b"
    env["WINEDLLOVERRIDES"] = f"{ovr};{existing_ovr}" if existing_ovr else ovr

    pfx = prefix_dir or (xdg_dirs()["data"] / "game_prefixes" / exe_path.stem)
    pfx.mkdir(parents=True, exist_ok=True)

    if runner.runner_type == "proton":
        env["STEAM_COMPAT_DATA_PATH"] = str(pfx)
        steam_root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Steam"
        if steam_root.is_dir():
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(steam_root)
        cmd = [runner.path, "run", str(exe_path)]
    else:
        env["WINEPREFIX"] = str(pfx)
        cmd = [runner.path, str(exe_path)]

    return subprocess.Popen(cmd, cwd=str(exe_path.parent), env=env, start_new_session=True)
