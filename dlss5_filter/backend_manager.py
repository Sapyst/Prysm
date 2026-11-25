"""Backend provisioning and lifecycle manager synthesizing DLSS5VKLayer + RHI neural DLL."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VKLAYER_RELEASE = {
    "version": "0.3.2-1",
    "filename": "dlssnr-0.3.2-1-linux-x86_64.tar.gz",
    "url": "https://github.com/bmitch87/DLSS5VKLayer/releases/download/0.3.2-1/dlssnr-0.3.2-1-linux-x86_64.tar.gz",
    "sha256": "2a5df946ff7054c2c6cdfaffad5c6833bbaaf484c9991affd97a7abf08404ffd",
}

NEURAL_DLL_RELEASE = {
    "version": "310.9.16",
    "filename": "nvngx_dlssnr_310.9.16.zip",
    "url": "https://github.com/RankFTW/rhi-repo/releases/download/dlssnr-310.9.16/nvngx_dlssnr_310.9.16.zip",
    "zip_sha256": "dbeb1f2790b5c621887db99b1a56ac8bd5cb35207e8c074cbb806710f7460cab",
    "dll_sha256": "cdfa6c1ec784867182164c7836f324f087b5c9f374d0c9fe46f54999feabf3ed",
}


@dataclass
class RunnerInfo:
    name: str
    runner_type: str  # 'proton' | 'wine'
    path: str
    recommended: bool
    note: str


@dataclass
class BackendStatus:
    vklayer_installed: bool
    layer64_ok: bool
    layer32_ok: bool
    manifest64_ok: bool
    manifest32_ok: bool
    helper_bin_ok: bool
    helper_exe_ok: bool
    neural_dll_ok: bool
    neural_dll_path: Path
    neural_dll_size_mb: float
    neural_dll_sha256_prefix: str
    configured_runner_type: str
    configured_runner_path: str
    ptrace_scope: int | None
    gpu_name: str
    driver_version: str


def project_cache_dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def xdg_dirs() -> dict[str, Path]:
    home = Path.home()
    return {
        "bin": home / ".local" / "bin",
        "lib": home / ".local" / "lib" / "dlssnr",
        "data": Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "dlssnr",
        "config": Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "dlssnr",
        "state": Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state") / "dlssnr",
        "vulkan_layers": Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
        / "vulkan"
        / "implicit_layer.d",
        "env_conf": Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        / "environment.d"
        / "dlssnr.conf",
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_runners() -> list[RunnerInfo]:
    """Find Proton / Wine runners, prioritizing DW-Proton and Proton-CachyOS over Proton-GE."""
    runners: list[RunnerInfo] = []
    search_roots = [
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        / "Steam"
        / "compatibilitytools.d",
        Path.home() / ".var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d",
        Path("/usr/share/steam/compatibilitytools.d"),
        Path("/usr/local/share/steam/compatibilitytools.d"),
    ]
    seen: set[str] = set()
    for root in search_roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            proton = entry / "proton"
            if proton.is_file() and os.access(proton, os.X_OK):
                resolved = str(proton.resolve())
                if resolved in seen:
                    continue
                seen.add(resolved)
                lname = entry.name.lower()
                if "ge" in lname:
                    rec = False
                    note = "Aviso: Proton-GE 11 pode causar stack-overflow no helper (use para jogos, evite no helper)"
                elif "dw-proton" in lname or "cachyos" in lname:
                    rec = True
                    note = "Recomendado para helper e jogos"
                else:
                    rec = True
                    note = "Compatível"
                runners.append(RunnerInfo(entry.name, "proton", str(proton), rec, note))

    # Sort recommended runners first, DW-Proton / CachyOS at top.
    def _rank(r: RunnerInfo) -> tuple[int, str]:
        lname = r.name.lower()
        if "dw-proton" in lname:
            return (0, lname)
        if "cachyos" in lname:
            return (1, lname)
        if r.recommended:
            return (2, lname)
        return (3, lname)

    runners.sort(key=_rank)

    wine_bin = shutil.which("wine")
    if wine_bin:
        runners.append(
            RunnerInfo("Wine (Sistema)", "wine", wine_bin, True, "Fallback usando Wine do sistema")
        )
    return runners


def read_dlssnr_config() -> dict[str, str]:
    cfg_file = xdg_dirs()["config"] / "config.ini"
    out: dict[str, str] = {}
    if not cfg_file.exists():
        return out
    for line in cfg_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def set_helper_runner(runner_type: str, runner_path: str) -> None:
    dirs = xdg_dirs()
    dirs["config"].mkdir(parents=True, exist_ok=True)
    cfg_file = dirs["config"] / "config.ini"
    cfg = read_dlssnr_config()
    cfg["runner_type"] = runner_type
    cfg["runner_path"] = runner_path
    cfg.setdefault("binaries", str(dirs["data"] / "binaries"))
    uid = os.environ.get("DLSSNR_UID") or str(os.getuid())
    cfg.setdefault("shm", f"/tmp/dlssnr-{uid}/shm.bin")
    cfg.setdefault("log", str(dirs["state"] / "helper.log"))
    lines = [f"{k}={v}" for k, v in cfg.items()]
    cfg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _detect_gpu() -> tuple[str, str]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            text=True,
            timeout=3,
        ).strip()
        if out:
            parts = [p.strip() for p in out.splitlines()[0].split(",", 1)]
            return parts[0], (parts[1] if len(parts) > 1 else "")
    except Exception:
        pass
    return "Desconhecida", ""


def _read_ptrace_scope() -> int | None:
    p = Path("/proc/sys/kernel/yama/ptrace_scope")
    try:
        return int(p.read_text().strip())
    except Exception:
        return None


def get_backend_status() -> BackendStatus:
    dirs = xdg_dirs()
    layer64 = (dirs["lib"] / "layer" / "libVkLayer_NV_dlssnr.so").exists()
    layer32 = (dirs["lib"] / "layer32" / "libVkLayer_NV_dlssnr.so").exists()
    m64 = (dirs["vulkan_layers"] / "VK_LAYER_NV_dlssnr.x86_64.json").exists()
    m32 = (dirs["vulkan_layers"] / "VK_LAYER_NV_dlssnr.i686.json").exists()
    helper_bin = (dirs["bin"] / "dlssnr-helper").exists()
    helper_exe = (dirs["lib"] / "helper" / "dlssnr_helper.exe").exists()

    dll_path = dirs["data"] / "binaries" / "nvngx_dlssnr.dll"
    dll_ok = dll_path.is_file()
    dll_mb = dll_path.stat().st_size / (1024 * 1024) if dll_ok else 0.0
    dll_sha = sha256_file(dll_path)[:16] if dll_ok else ""

    cfg = read_dlssnr_config()
    gpu_name, driver_ver = _detect_gpu()

    return BackendStatus(
        vklayer_installed=all([layer64, m64, helper_bin, helper_exe]),
        layer64_ok=layer64,
        layer32_ok=layer32,
        manifest64_ok=m64,
        manifest32_ok=m32,
        helper_bin_ok=helper_bin,
        helper_exe_ok=helper_exe,
        neural_dll_ok=dll_ok,
        neural_dll_path=dll_path,
        neural_dll_size_mb=dll_mb,
        neural_dll_sha256_prefix=dll_sha,
        configured_runner_type=cfg.get("runner_type", ""),
        configured_runner_path=cfg.get("runner_path", ""),
        ptrace_scope=_read_ptrace_scope(),
        gpu_name=gpu_name,
        driver_version=driver_ver,
    )


def _download_with_sha256(
    url: str, dest: Path, expected_sha256: str, log: Callable[[str], None]
) -> Path:
    if dest.exists() and sha256_file(dest) == expected_sha256:
        log(f"Usando cache verificado: {dest.name}")
        return dest
    log(f"Baixando {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "DLSS5-Filter-Studio/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=180) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    got = sha256_file(tmp)
    if got != expected_sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"SHA-256 inválido para {dest.name}: esperado {expected_sha256}, obtido {got}")
    tmp.replace(dest)
    log(f"Download verificado (SHA-256 OK): {dest.name}")
    return dest


def install_vklayer(log: Callable[[str], None] = print) -> None:
    dirs = xdg_dirs()
    tarball = project_cache_dir() / VKLAYER_RELEASE["filename"]
    _download_with_sha256(VKLAYER_RELEASE["url"], tarball, VKLAYER_RELEASE["sha256"], log)

    had_env_conf = dirs["env_conf"].exists()
    with tempfile.TemporaryDirectory(prefix="dlssnr-inst-") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(tmp_path)
        install_sh = next(tmp_path.glob("*/install.sh"), None)
        if install_sh is None:
            raise RuntimeError("install.sh não encontrado no pacote DLSS5VKLayer")
        subprocess.run(["bash", str(install_sh), "--user"], check=True)

    # Keep the implicit layer strictly gated on VKLayer_DLSS5=1 rather than forcing VK_INSTANCE_LAYERS.
    if not had_env_conf and dirs["env_conf"].exists():
        dirs["env_conf"].unlink(missing_ok=True)

    helper_cli = dirs["bin"] / "dlssnr-helper"
    if not (dirs["config"] / "config.ini").exists():
        subprocess.run([str(helper_cli), "init"], check=True)

    # Prefer DW-Proton / Proton-CachyOS over Proton-GE because GE-Proton 11 stack-overflows in helper load.
    runners = discover_runners()
    rec = next((r for r in runners if r.recommended), None)
    if rec:
        set_helper_runner(rec.runner_type, rec.path)
        log(f"Runner do helper configurado para: {rec.name} ({rec.path})")


def install_neural_dll_from_git(log: Callable[[str], None] = print) -> Path:
    dirs = xdg_dirs()
    zip_path = project_cache_dir() / NEURAL_DLL_RELEASE["filename"]
    _download_with_sha256(
        NEURAL_DLL_RELEASE["url"], zip_path, NEURAL_DLL_RELEASE["zip_sha256"], log
    )
    with tempfile.TemporaryDirectory(prefix="dlssnr-dll-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_path)
        dll = tmp_path / "nvngx_dlssnr.dll"
        if not dll.exists():
            raise RuntimeError("nvngx_dlssnr.dll não encontrada dentro do ZIP")
        got = sha256_file(dll)
        if got != NEURAL_DLL_RELEASE["dll_sha256"]:
            raise RuntimeError(f"SHA-256 inválido da nvngx_dlssnr.dll: {got}")
        helper_cli = dirs["bin"] / "dlssnr-helper"
        if helper_cli.exists():
            subprocess.run([str(helper_cli), "import-binaries", str(tmp_path)], check=True)
        else:
            dest_dir = dirs["data"] / "binaries"
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dll, dest_dir / "nvngx_dlssnr.dll")
    dest_dll = dirs["data"] / "binaries" / "nvngx_dlssnr.dll"
    log(f"nvngx_dlssnr.dll {NEURAL_DLL_RELEASE['version']} instalada em {dest_dll}")
    return dest_dll


def import_local_neural_dir(src_dir: Path, log: Callable[[str], None] = print) -> Path:
    dll = src_dir / "nvngx_dlssnr.dll"
    if not dll.is_file():
        raise FileNotFoundError(f"nvngx_dlssnr.dll não encontrada em {src_dir}")
    helper_cli = xdg_dirs()["bin"] / "dlssnr-helper"
    if helper_cli.exists():
        subprocess.run([str(helper_cli), "import-binaries", str(src_dir)], check=True)
    else:
        dest_dir = xdg_dirs()["data"] / "binaries"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dll, dest_dir / "nvngx_dlssnr.dll")
    dest_dll = xdg_dirs()["data"] / "binaries" / "nvngx_dlssnr.dll"
    log(f"DLL local importada para {dest_dll}")
    return dest_dll


def find_local_backup_dll_dirs() -> list[Path]:
    candidates = [
        Path("/mnt/d/Backup-Programas/CAPTURA E VIEWERS/VideoGameCapture 0.0.13"),
    ]
    return [p for p in candidates if (p / "nvngx_dlssnr.dll").is_file()]


def run_helper_cmd(cmd: str, verbose: bool = False) -> tuple[int, str]:
    helper_cli = xdg_dirs()["bin"] / "dlssnr-helper"
    if not helper_cli.exists():
        return 1, "dlssnr-helper não está instalado. Instale o backend primeiro."
    env = os.environ.copy()
    if verbose:
        env["DLSSNR_VERBOSE"] = "1"
        env["DLSSNR_TIME"] = "1"
    proc = subprocess.run(
        [str(helper_cli), cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        timeout=120,
    )
    return proc.returncode, proc.stdout.strip()


def read_helper_log(tail_lines: int = 120) -> str:
    log_file = xdg_dirs()["state"] / "helper.log"
    if not log_file.exists():
        return "(helper.log ainda não existe)"
    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail_lines:])
