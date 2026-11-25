"""Binary shared-memory bridge matching bmitch87/DLSS5VKLayer common/shm_protocol.h (v20)."""

from __future__ import annotations

import ctypes
import mmap
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SHM_MAGIC = 0x32524E47  # 'GNR2'
SHM_VERSION = 20
SHM_HEADER_BYTES = 65536
SHM_MAX_W = 7680
SHM_MAX_H = 4320
SHM_MAX_FRAME = SHM_MAX_W * SHM_MAX_H * 8
SHM_TOTAL_BYTES = SHM_HEADER_BYTES + SHM_MAX_FRAME * 2
SHM_MAX_PASSES = 30
REASON_BYTES = 192
NAME_BYTES = 128

HELPER_STATES = {
    0: "Iniciando",
    1: "Sem suporte Vulkan NVX",
    2: "Sem nvngx_dlssnr.dll",
    3: "Falha no modelo",
    4: "Rodando",
    5: "Parado",
}

STYLES = {0: "Padrão (Default)", 1: "Natural", 2: "Cinematográfico"}
NR_MODELS = {
    0: "Automático (padrão da DLL)",
    1: "Original",
    2: "Otimizado FP8 (menor/mais rápido 310.9.16)",
}
HDR_MODES = {0: "Auto", 1: "Desligado (8-bit)", 2: "Forçar Float16"}
HDR_KINDS = {0: "SDR 8-bit", 1: "HDR Linear FP16", 2: "HDR10 PQ"}
PROXY_FORMATS = {0: "Desconhecido", 1: "RGBA8 (SDR)", 2: "RGBA16F (HDR)"}
MVEC_QUALITIES = {0: "Rápido (Fast)", 1: "Balanceado", 2: "Qualidade"}
MVEC_SCALE_MODES = {0: "Normalizado", 1: "Pixels", 2: "UV 0..1"}
MVEC_PIXEL_SIZES = {0: "1 px", 1: "2 px", 2: "4 px", 3: "8 px"}
TRANSFER_MODES = {0: "Clássico", 1: "Matched Residual", 2: "Nativo + Edição"}
REVERSIBLE_MODES = {
    0: "Soft Knee (Padrão)",
    1: "Neutwo + Composição",
    2: "Neutwo Replace",
    3: "Hybrid + Composição",
    4: "Hybrid Replace",
}
DOWNSCALERS = {
    1: "Bicubic",
    2: "Catmull-Rom",
    3: "Lanczos 2",
    4: "Lanczos 3",
    5: "Kaiser 2",
    6: "Kaiser 3",
    7: "Magic",
}
COLOUR_MODES = {0: "Auto", 1: "Display-referred (SDR)", 2: "Linear HDR"}
COMPARE_MODES = {0: "Desligado", 1: "Lado a Lado", 2: "Cortina (Wipe)"}

# Fields that trigger NGX feature rebuild at create time when modified.
CREATE_TIME_FIELDS = frozenset(
    {
        "preset",
        "style",
        "autoMask",
        "intensityBits",
        "localToneBits",
        "localStructureBits",
        "skinStructureBits",
        "passes",
    }
)


def float_to_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


def bits_to_float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", int(bits) & 0xFFFFFFFF))[0]


class PassControl(ctypes.Structure):
    _fields_ = [
        ("overrideMask", ctypes.c_uint32),
        ("intensityBits", ctypes.c_uint32),
        ("localToneBits", ctypes.c_uint32),
        ("localStructureBits", ctypes.c_uint32),
        ("skinStructureBits", ctypes.c_uint32),
        ("sharpnessBits", ctypes.c_uint32),
        ("style", ctypes.c_uint32),
        ("preset", ctypes.c_uint32),
        ("autoMask", ctypes.c_uint32),
    ]


class ShmHeader(ctypes.Structure):
    _fields_ = [
        # 0..43
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("seq_req", ctypes.c_uint32),
        ("seq_resp", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("format", ctypes.c_uint32),
        ("quit", ctypes.c_uint32),
        ("heartbeat", ctypes.c_uint32),
        ("controlSeq", ctypes.c_uint32),
        ("tuningSeq", ctypes.c_uint32),
        # 44..87 (model)
        ("enabled", ctypes.c_uint32),
        ("passes", ctypes.c_uint32),
        ("unlockPasses", ctypes.c_uint32),
        ("preset", ctypes.c_uint32),
        ("style", ctypes.c_uint32),
        ("autoMask", ctypes.c_uint32),
        ("intensityBits", ctypes.c_uint32),
        ("localToneBits", ctypes.c_uint32),
        ("localStructureBits", ctypes.c_uint32),
        ("skinStructureBits", ctypes.c_uint32),
        ("sharpnessBits", ctypes.c_uint32),
        # 88..175 (composition)
        ("transferStrengthBits", ctypes.c_uint32),
        ("colourStrengthBits", ctypes.c_uint32),
        ("maxRatioBits", ctypes.c_uint32),
        ("transfer", ctypes.c_uint32),
        ("debugView", ctypes.c_uint32),
        ("debugScaleBits", ctypes.c_uint32),
        ("whitePointBits", ctypes.c_uint32),
        ("whitePointScaleBits", ctypes.c_uint32),
        ("whitePointSource", ctypes.c_uint32),
        ("whitePointTrimBits", ctypes.c_uint32),
        ("workingScaleBits", ctypes.c_uint32),
        ("compareMode", ctypes.c_uint32),
        ("compareSplitBits", ctypes.c_uint32),
        ("compareZoomBits", ctypes.c_uint32),
        ("compareSwap", ctypes.c_uint32),
        ("colourMode", ctypes.c_uint32),
        ("captureRequest", ctypes.c_uint32),
        ("toggleKey", ctypes.c_uint32),
        ("reversibleMode", ctypes.c_uint32),
        ("applyModel", ctypes.c_uint32),
        ("holdFrame", ctypes.c_uint32),
        ("scalingDownscaler", ctypes.c_uint32),
        # 176..215 (helper status)
        ("helperState", ctypes.c_uint32),
        ("modelUp", ctypes.c_uint32),
        ("helperFramesLo", ctypes.c_uint32),
        ("helperFramesHi", ctypes.c_uint32),
        ("helperEvalMsBits", ctypes.c_uint32),
        ("helperUploadMsBits", ctypes.c_uint32),
        ("helperReadbackMsBits", ctypes.c_uint32),
        ("helperVramMB", ctypes.c_uint32),
        ("helperFeatures", ctypes.c_uint32),
        ("helperPassCeiling", ctypes.c_uint32),
        # 216..255 (layer status)
        ("layerPid", ctypes.c_uint32),
        ("layerFramesLo", ctypes.c_uint32),
        ("layerFramesHi", ctypes.c_uint32),
        ("layerWidth", ctypes.c_uint32),
        ("layerHeight", ctypes.c_uint32),
        ("layerFormat", ctypes.c_uint32),
        ("layerCompositionUp", ctypes.c_uint32),
        ("layerMsBits", ctypes.c_uint32),
        ("layerMeasuredWhiteBits", ctypes.c_uint32),
        ("layerHeartbeat", ctypes.c_uint32),
        # 256..779 (strings)
        ("helperReasonSeq", ctypes.c_uint32),
        ("helperReason", ctypes.c_char * REASON_BYTES),
        ("layerReasonSeq", ctypes.c_uint32),
        ("layerReason", ctypes.c_char * REASON_BYTES),
        ("gameNameSeq", ctypes.c_uint32),
        ("gameName", ctypes.c_char * NAME_BYTES),
        # 780..1859 (per-pass overrides)
        ("pass_ctrl", PassControl * SHM_MAX_PASSES),
        # 1860..1967 (tail)
        ("mvecEnabled", ctypes.c_uint32),
        ("mvecScaleMode", ctypes.c_uint32),
        ("mvecQuality", ctypes.c_uint32),
        ("seq_ok", ctypes.c_uint32),
        ("compositionBypass", ctypes.c_uint32),
        ("rebuildSettleMs", ctypes.c_uint32),
        ("answeredW", ctypes.c_uint32),
        ("answeredH", ctypes.c_uint32),
        ("proxyExportSeq", ctypes.c_uint32),
        ("proxyPid", ctypes.c_uint32),
        ("proxyFd", ctypes.c_uint32),
        ("proxyGen", ctypes.c_uint32),
        ("answerExportSeq", ctypes.c_uint32),
        ("answerPid", ctypes.c_uint32),
        ("answerFd", ctypes.c_uint32),
        ("answerGen", ctypes.c_uint32),
        ("layerProxySeq", ctypes.c_uint32),
        ("layerAnswerSeq", ctypes.c_uint32),
        ("hdrMode", ctypes.c_uint32),
        ("hdrDetected", ctypes.c_uint32),
        ("hdrActive", ctypes.c_uint32),
        ("proxyFormat", ctypes.c_uint32),
        ("hdrEncode", ctypes.c_uint32),
        ("colourTrustPercent", ctypes.c_uint32),
        ("ratioSmoothPercent", ctypes.c_uint32),
        ("sdr16Multipass", ctypes.c_uint32),
        ("mvecPixelSize", ctypes.c_uint32),
    ]


# Enforce exact binary compatibility with DLSS5VKLayer's static_asserts.
assert ctypes.sizeof(ShmHeader) == 1968, f"Unexpected ShmHeader size: {ctypes.sizeof(ShmHeader)}"
assert ShmHeader.enabled.offset == 44
assert ShmHeader.transferStrengthBits.offset == 88
assert ShmHeader.helperState.offset == 176
assert ShmHeader.pass_ctrl.offset == 780
assert ShmHeader.mvecEnabled.offset == 1860


def default_shm_path() -> Path:
    uid = os.environ.get("DLSSNR_UID") or str(os.getuid())
    env_shm = os.environ.get("DLSSNR_SHM")
    if env_shm:
        return Path(env_shm)
    return Path(f"/tmp/dlssnr-{uid}/shm.bin")


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    return Path(f"/proc/{pid}").exists()


@dataclass
class ShmSnapshot:
    initialised: bool
    magic: int
    version: int
    helper_state: int
    helper_state_label: str
    model_up: bool
    heartbeat: int
    seq_req: int
    seq_resp: int
    seq_ok: int
    helper_frames: int
    helper_eval_ms: float
    helper_upload_ms: float
    helper_readback_ms: float
    helper_vram_mb: int
    helper_features: int
    helper_pass_ceiling: int
    layer_pid: int
    layer_alive: bool
    layer_frames: int
    layer_width: int
    layer_height: int
    layer_ms: float
    layer_composition_up: bool
    game_name: str
    helper_reason: str
    layer_reason: str
    hdr_mode: int
    hdr_detected: int
    hdr_active: int
    proxy_format: int
    dmabuf_active: bool
    width: int
    height: int
    settings: dict[str, Any]


class ShmBridge:
    """Direct mmap accessor to DLSS5VKLayer's shared-memory header."""

    def __init__(self, shm_path: Path | None = None) -> None:
        self.path = shm_path or default_shm_path()
        self._fd: int | None = None
        self._mm: mmap.mmap | None = None
        self._hdr: ShmHeader | None = None

    def close(self) -> None:
        self._hdr = None
        if self._mm is not None:
            try:
                self._mm.close()
            except Exception:
                pass
            self._mm = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def _ensure_mapped(self, create: bool = False) -> ShmHeader | None:
        if self._hdr is not None:
            return self._hdr
        if not self.path.exists():
            if not create:
                return None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.ftruncate(fd, SHM_TOTAL_BYTES)
        else:
            st = self.path.stat()
            if st.st_size < SHM_TOTAL_BYTES:
                if not create:
                    return None
                fd = os.open(self.path, os.O_RDWR, 0o600)
                os.ftruncate(fd, SHM_TOTAL_BYTES)
            else:
                fd = os.open(self.path, os.O_RDWR, 0o600)
        mm = mmap.mmap(fd, SHM_HEADER_BYTES, access=mmap.ACCESS_WRITE)
        self._fd = fd
        self._mm = mm
        self._hdr = ShmHeader.from_buffer(mm)
        if create and not self.is_initialised(self._hdr):
            self.init_defaults()
        return self._hdr

    @staticmethod
    def is_initialised(hdr: ShmHeader) -> bool:
        return hdr.magic == SHM_MAGIC and hdr.version == SHM_VERSION

    def init_defaults(self) -> None:
        hdr = self._ensure_mapped(create=True)
        if hdr is None:
            return
        ctypes.memset(ctypes.addressof(hdr), 0, ctypes.sizeof(ShmHeader))
        hdr.magic = SHM_MAGIC
        hdr.version = SHM_VERSION
        hdr.helperState = 5
        hdr.format = 1
        hdr.passes = 1
        hdr.enabled = 1
        hdr.autoMask = 1
        hdr.intensityBits = float_to_bits(1.0)
        hdr.localToneBits = float_to_bits(1.0)
        hdr.localStructureBits = float_to_bits(1.0)
        hdr.skinStructureBits = float_to_bits(-1.0)
        hdr.sharpnessBits = float_to_bits(0.0)
        hdr.transferStrengthBits = float_to_bits(1.0)
        hdr.colourStrengthBits = float_to_bits(1.0)
        hdr.maxRatioBits = float_to_bits(2.0)
        hdr.transfer = 1
        hdr.debugScaleBits = float_to_bits(1.0)
        hdr.whitePointBits = float_to_bits(1.0)
        hdr.whitePointScaleBits = float_to_bits(1.0)
        hdr.whitePointTrimBits = float_to_bits(1.0)
        hdr.whitePointSource = 0
        hdr.workingScaleBits = float_to_bits(1.0)
        hdr.compareSplitBits = float_to_bits(0.5)
        hdr.compareZoomBits = float_to_bits(1.0)
        hdr.colourMode = 0
        hdr.reversibleMode = 0
        hdr.applyModel = 1
        hdr.holdFrame = 0
        hdr.scalingDownscaler = 4
        hdr.hdrMode = 0
        hdr.hdrDetected = 0
        hdr.hdrActive = 0
        hdr.proxyFormat = 1
        hdr.mvecEnabled = 1
        hdr.mvecScaleMode = 1
        hdr.mvecQuality = 1
        hdr.mvecPixelSize = 2
        hdr.compositionBypass = 1
        hdr.rebuildSettleMs = 100
        hdr.colourTrustPercent = 200
        hdr.ratioSmoothPercent = 100
        hdr.sdr16Multipass = 0
        for i in range(SHM_MAX_PASSES):
            hdr.pass_ctrl[i].overrideMask = 0
            hdr.pass_ctrl[i].intensityBits = float_to_bits(1.0)
            hdr.pass_ctrl[i].localToneBits = float_to_bits(1.0)
            hdr.pass_ctrl[i].localStructureBits = float_to_bits(1.0)
            hdr.pass_ctrl[i].skinStructureBits = float_to_bits(-1.0)
            hdr.pass_ctrl[i].sharpnessBits = float_to_bits(0.0)
            hdr.pass_ctrl[i].style = 0
            hdr.pass_ctrl[i].preset = 0
            hdr.pass_ctrl[i].autoMask = 1

    @staticmethod
    def _decode_cstr(raw: bytes) -> str:
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()

    def read_snapshot(self) -> ShmSnapshot | None:
        hdr = self._ensure_mapped(create=False)
        if hdr is None:
            return None
        init = self.is_initialised(hdr)
        if not init:
            return ShmSnapshot(
                initialised=False,
                magic=hdr.magic,
                version=hdr.version,
                helper_state=5,
                helper_state_label="Não inicializado",
                model_up=False,
                heartbeat=0,
                seq_req=0,
                seq_resp=0,
                seq_ok=0,
                helper_frames=0,
                helper_eval_ms=0.0,
                helper_upload_ms=0.0,
                helper_readback_ms=0.0,
                helper_vram_mb=0,
                helper_features=0,
                helper_pass_ceiling=SHM_MAX_PASSES,
                layer_pid=0,
                layer_alive=False,
                layer_frames=0,
                layer_width=0,
                layer_height=0,
                layer_ms=0.0,
                layer_composition_up=False,
                game_name="",
                helper_reason="",
                layer_reason="",
                hdr_mode=0,
                hdr_detected=0,
                hdr_active=0,
                proxy_format=1,
                dmabuf_active=False,
                width=0,
                height=0,
                settings={},
            )

        helper_frames = (int(hdr.helperFramesHi) << 32) | int(hdr.helperFramesLo)
        layer_frames = (int(hdr.layerFramesHi) << 32) | int(hdr.layerFramesLo)
        layer_pid = int(hdr.layerPid)
        dmabuf = bool(hdr.layerProxySeq != 0 and hdr.layerProxySeq == hdr.proxyExportSeq)

        settings = {
            "enabled": int(hdr.enabled),
            "applyModel": int(hdr.applyModel),
            "holdFrame": int(hdr.holdFrame),
            "passes": max(1, min(SHM_MAX_PASSES, int(hdr.passes) or 1)),
            "style": int(hdr.style),
            "preset": int(hdr.preset),
            "autoMask": int(hdr.autoMask),
            "intensity": bits_to_float(hdr.intensityBits),
            "localTone": bits_to_float(hdr.localToneBits),
            "localStructure": bits_to_float(hdr.localStructureBits),
            "skinStructure": bits_to_float(hdr.skinStructureBits),
            "sharpness": bits_to_float(hdr.sharpnessBits),
            "compositionBypass": int(hdr.compositionBypass),
            "transferStrength": bits_to_float(hdr.transferStrengthBits),
            "colourStrength": bits_to_float(hdr.colourStrengthBits),
            "maxRatio": bits_to_float(hdr.maxRatioBits),
            "transfer": int(hdr.transfer),
            "reversibleMode": int(hdr.reversibleMode),
            "workingScale": bits_to_float(hdr.workingScaleBits),
            "scalingDownscaler": int(hdr.scalingDownscaler),
            "colourMode": int(hdr.colourMode),
            "hdrMode": int(hdr.hdrMode),
            "sdr16Multipass": int(hdr.sdr16Multipass),
            "colourTrustPercent": int(hdr.colourTrustPercent),
            "ratioSmoothPercent": int(hdr.ratioSmoothPercent),
            "mvecEnabled": int(hdr.mvecEnabled),
            "mvecQuality": int(hdr.mvecQuality),
            "mvecScaleMode": int(hdr.mvecScaleMode),
            "mvecPixelSize": int(hdr.mvecPixelSize),
            "compareMode": int(hdr.compareMode),
            "compareSplit": bits_to_float(hdr.compareSplitBits),
            "compareZoom": bits_to_float(hdr.compareZoomBits),
            "compareSwap": int(hdr.compareSwap),
            "debugView": int(hdr.debugView),
            "debugScale": bits_to_float(hdr.debugScaleBits),
        }

        return ShmSnapshot(
            initialised=True,
            magic=int(hdr.magic),
            version=int(hdr.version),
            helper_state=int(hdr.helperState),
            helper_state_label=HELPER_STATES.get(int(hdr.helperState), f"Estado {hdr.helperState}"),
            model_up=bool(hdr.modelUp),
            heartbeat=int(hdr.heartbeat),
            seq_req=int(hdr.seq_req),
            seq_resp=int(hdr.seq_resp),
            seq_ok=int(hdr.seq_ok),
            helper_frames=helper_frames,
            helper_eval_ms=bits_to_float(hdr.helperEvalMsBits),
            helper_upload_ms=bits_to_float(hdr.helperUploadMsBits),
            helper_readback_ms=bits_to_float(hdr.helperReadbackMsBits),
            helper_vram_mb=int(hdr.helperVramMB),
            helper_features=int(hdr.helperFeatures),
            helper_pass_ceiling=int(hdr.helperPassCeiling) or SHM_MAX_PASSES,
            layer_pid=layer_pid,
            layer_alive=_pid_alive(layer_pid),
            layer_frames=layer_frames,
            layer_width=int(hdr.layerWidth),
            layer_height=int(hdr.layerHeight),
            layer_ms=bits_to_float(hdr.layerMsBits),
            layer_composition_up=bool(hdr.layerCompositionUp),
            game_name=self._decode_cstr(bytes(hdr.gameName)),
            helper_reason=self._decode_cstr(bytes(hdr.helperReason)),
            layer_reason=self._decode_cstr(bytes(hdr.layerReason)),
            hdr_mode=int(hdr.hdrMode),
            hdr_detected=int(hdr.hdrDetected),
            hdr_active=int(hdr.hdrActive),
            proxy_format=int(hdr.proxyFormat),
            dmabuf_active=dmabuf,
            width=int(hdr.width),
            height=int(hdr.height),
            settings=settings,
        )

    def apply_settings(self, updates: dict[str, Any]) -> None:
        """Apply one or more settings to ShmHeader, bumping controlSeq (and tuningSeq if needed)."""
        hdr = self._ensure_mapped(create=True)
        if hdr is None:
            return
        if not self.is_initialised(hdr):
            self.init_defaults()

        mapping: dict[str, tuple[str, bool]] = {
            "enabled": ("enabled", False),
            "applyModel": ("applyModel", False),
            "holdFrame": ("holdFrame", False),
            "passes": ("passes", False),
            "style": ("style", False),
            "preset": ("preset", False),
            "autoMask": ("autoMask", False),
            "intensity": ("intensityBits", True),
            "localTone": ("localToneBits", True),
            "localStructure": ("localStructureBits", True),
            "skinStructure": ("skinStructureBits", True),
            "sharpness": ("sharpnessBits", True),
            "compositionBypass": ("compositionBypass", False),
            "transferStrength": ("transferStrengthBits", True),
            "colourStrength": ("colourStrengthBits", True),
            "maxRatio": ("maxRatioBits", True),
            "transfer": ("transfer", False),
            "reversibleMode": ("reversibleMode", False),
            "workingScale": ("workingScaleBits", True),
            "scalingDownscaler": ("scalingDownscaler", False),
            "colourMode": ("colourMode", False),
            "hdrMode": ("hdrMode", False),
            "sdr16Multipass": ("sdr16Multipass", False),
            "colourTrustPercent": ("colourTrustPercent", False),
            "ratioSmoothPercent": ("ratioSmoothPercent", False),
            "mvecEnabled": ("mvecEnabled", False),
            "mvecQuality": ("mvecQuality", False),
            "mvecScaleMode": ("mvecScaleMode", False),
            "mvecPixelSize": ("mvecPixelSize", False),
            "compareMode": ("compareMode", False),
            "compareSplit": ("compareSplitBits", True),
            "compareZoom": ("compareZoomBits", True),
            "compareSwap": ("compareSwap", False),
            "debugView": ("debugView", False),
            "debugScale": ("debugScaleBits", True),
        }

        needs_tuning = False
        changed = False
        for key, val in updates.items():
            if key not in mapping:
                continue
            field_name, is_float = mapping[key]
            encoded = float_to_bits(float(val)) if is_float else max(0, int(val))
            if getattr(hdr, field_name) != encoded:
                setattr(hdr, field_name, encoded)
                changed = True
                if field_name in CREATE_TIME_FIELDS:
                    needs_tuning = True

        if changed:
            hdr.controlSeq = (int(hdr.controlSeq) + 1) & 0xFFFFFFFF
            if needs_tuning:
                hdr.tuningSeq = (int(hdr.tuningSeq) + 1) & 0xFFFFFFFF

    def reset_settings(self) -> None:
        shmctl = Path.home() / ".local" / "bin" / "dlssnr-shmctl"
        if shmctl.exists() and self.path.exists():
            subprocess.run([str(shmctl), str(self.path), "reset"], check=False)
        else:
            self.init_defaults()

    def capture_frames(self, count: int = 1) -> None:
        hdr = self._ensure_mapped(create=True)
        if hdr is None:
            return
        hdr.captureRequest = max(1, int(count))
        hdr.controlSeq = (int(hdr.controlSeq) + 1) & 0xFFFFFFFF
