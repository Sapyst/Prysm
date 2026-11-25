"""Game discovery and unified Vulkan/Proton launcher with DLSS 5 filter active."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .backend_manager import RunnerInfo, discover_runners, run_helper_cmd, xdg_dirs
from .shm_bridge import ShmBridge


@dataclass
class SteamGame:
    appid: str
    name: str
    install_dir: Path
    compat_data: Path | None


def _steam_roots() -> list[Path]:
    candidates = [
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Steam",
        Path.home() / ".steam" / "steam",
        Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        if c.is_dir():
            r = str(c.resolve())
            if r not in seen:
                seen.add(r)
                out.append(c)
    return out


def discover_steam_games() -> list[SteamGame]:
    games: list[SteamGame] = []
    seen_appids: set[str] = set()

    for root in _steam_roots():
        lib_dirs = [root / "steamapps"]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s*"([^"]+)"', text):
                p = Path(m.group(1).replace("\\\\", "\\")) / "steamapps"
                if p.is_dir() and p not in lib_dirs:
                    lib_dirs.append(p)

        for steamapps in lib_dirs:
            if not steamapps.is_dir():
                continue
            for acf in sorted(steamapps.glob("appmanifest_*.acf")):
                content = acf.read_text(encoding="utf-8", errors="replace")
                m_id = re.search(r'"appid"\s*"(\d+)"', content)
                m_name = re.search(r'"name"\s*"([^"]+)"', content)
                m_dir = re.search(r'"installdir"\s*"([^"]+)"', content)
                if not (m_id and m_name and m_dir):
                    continue
                appid = m_id.group(1)
                if appid in seen_appids or "Proton" in m_name.group(1) or "Steam Linux Runtime" in m_name.group(1):
                    continue
                seen_appids.add(appid)
                compat = steamapps / "compatdata" / appid
                games.append(
                    SteamGame(
                        appid=appid,
                        name=m_name.group(1),
                        install_dir=steamapps / "common" / m_dir.group(1),
                        compat_data=compat if compat.is_dir() else None,
                    )
                )

    games.sort(key=lambda g: g.name.lower())
    return games


def default_game_prefix_dir(exe_path: Path) -> Path:
    data = xdg_dirs()["data"] / "game_prefixes"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", exe_path.stem)[:48] or "game"
    p = data / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_helper_running() -> tuple[bool, str]:
    snap = ShmBridge().read_snapshot()
    if snap and snap.initialised and snap.helper_state == 4:
        return True, "Helper já está ativo"
    rc, out = run_helper_cmd("start")
    return rc == 0, out


def _apply_dlss5_game_env(env: dict[str, str], verbose_layer: bool) -> dict[str, str]:
    env["VKLayer_DLSS5"] = "1"
    if env.get("DLSS5_NO_PRIME") != "1":
        env.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")
        env.setdefault("__VK_LAYER_NV_optimus", "NVIDIA_only")
        env.setdefault("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
    if verbose_layer:
        env["DLSSNR_VERBOSE"] = "1"
    return env


def launch_native_vulkan(
    executable: str,
    args: str = "",
    workdir: str = "",
    use_mangohud: bool = False,
    verbose_layer: bool = False,
    auto_start_helper: bool = True,
) -> tuple[subprocess.Popen[bytes], str]:
    if auto_start_helper:
        ok, msg = ensure_helper_running()
        if not ok:
            raise RuntimeError(f"Falha ao iniciar o helper DLSS 5:\n{msg}")

    env = _apply_dlss5_game_env(os.environ.copy(), verbose_layer)

    cmd: list[str] = []
    if use_mangohud:
        cmd.append("mangohud")
    cmd.append(executable)
    if args.strip():
        cmd.extend(shlex.split(args))

    cwd = workdir.strip() or (str(Path(executable).parent) if Path(executable).exists() else None)
    log_path = xdg_dirs()["state"] / "last_game.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("wb")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return proc, str(log_path)


def launch_proton_game(
    exe_path: Path,
    runner: RunnerInfo,
    prefix_dir: Path | None = None,
    args: str = "",
    use_mangohud: bool = False,
    verbose_layer: bool = False,
    auto_start_helper: bool = True,
) -> tuple[subprocess.Popen[bytes], str]:
    if auto_start_helper:
        ok, msg = ensure_helper_running()
        if not ok:
            raise RuntimeError(f"Falha ao iniciar o helper DLSS 5:\n{msg}")

    pfx = prefix_dir or default_game_prefix_dir(exe_path)
    pfx.mkdir(parents=True, exist_ok=True)

    env = _apply_dlss5_game_env(os.environ.copy(), verbose_layer)
    env["PROTON_ENABLE_NVAPI"] = "1"
    env["DXVK_ENABLE_NVAPI"] = "1"

    if runner.runner_type == "proton":
        env["STEAM_COMPAT_DATA_PATH"] = str(pfx)
        roots = _steam_roots()
        if roots:
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = str(roots[0])
        cmd = [runner.path, "run", str(exe_path)]
    else:
        env["WINEPREFIX"] = str(pfx)
        cmd = [runner.path, str(exe_path)]

    if use_mangohud:
        cmd = ["mangohud", *cmd]
    if args.strip():
        cmd.extend(shlex.split(args))

    log_path = xdg_dirs()["state"] / "last_game.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("wb")
    proc = subprocess.Popen(
        cmd,
        cwd=str(exe_path.parent),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return proc, str(log_path)


def _recent_file() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dlss5-filter"
    cfg.mkdir(parents=True, exist_ok=True)
    return cfg / "recent_games.json"


def load_recent_games() -> list[dict[str, str]]:
    f = _recent_file()
    if not f.is_file():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def add_recent_game(entry: dict[str, str]) -> None:
    items = [x for x in load_recent_games() if x.get("exe") != entry.get("exe")]
    items.insert(0, entry)
    _recent_file().write_text(json.dumps(items[:15], indent=2), encoding="utf-8")


def default_game_runner() -> RunnerInfo | None:
    runners = discover_runners()
    return runners[0] if runners else None
