"""PyQt6 desktop interface for DLSS5 Filter Studio."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from .backend_manager import (
    discover_runners,
    find_local_backup_dll_dirs,
    get_backend_status,
    import_local_neural_dir,
    install_neural_dll_from_git,
    install_vklayer,
    read_dlssnr_config,
    read_helper_log,
    run_helper_cmd,
    set_helper_runner,
    xdg_dirs,
)
from .game_launcher import (
    add_recent_game,
    discover_steam_games,
    launch_native_vulkan,
    launch_proton_game,
    load_recent_games,
)
from .optiscaler_manager import (
    HOOK_OPTIONS,
    PRESR_PROFILES,
    get_presr_status,
    install_presr_in_game,
    launch_proton_presr_game,
    uninstall_presr_from_game,
    update_dlssnr_ini,
)
from .presets import BUILTIN_PRESETS, list_user_profiles, load_user_profile, save_user_profile
from .shm_bridge import (
    COLOUR_MODES,
    COMPARE_MODES,
    DOWNSCALERS,
    HDR_KINDS,
    HDR_MODES,
    MVEC_PIXEL_SIZES,
    MVEC_QUALITIES,
    MVEC_SCALE_MODES,
    NR_MODELS,
    PROXY_FORMATS,
    REVERSIBLE_MODES,
    STYLES,
    TRANSFER_MODES,
    ShmBridge,
    ShmSnapshot,
)

DARK_QSS = """
QMainWindow, QWidget {
    background-color: #11151b;
    color: #e6edf3;
    font-size: 13px;
}
QTabWidget::pane {
    border: 1px solid #26303d;
    background-color: #151a22;
    border-radius: 6px;
}
QTabBar::tab {
    background-color: #1b222c;
    color: #9fb0c3;
    padding: 8px 16px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected {
    background-color: #222b38;
    color: #8ce000;
    font-weight: bold;
    border-bottom: 2px solid #76b900;
}
QGroupBox {
    border: 1px solid #26303d;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: bold;
    color: #b7c8da;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QPushButton {
    background-color: #212a36;
    border: 1px solid #323f50;
    border-radius: 5px;
    padding: 6px 12px;
    color: #e6edf3;
}
QPushButton:hover {
    background-color: #2a3646;
    border-color: #4a5d75;
}
QPushButton:pressed {
    background-color: #19202a;
}
QPushButton#primaryBtn {
    background-color: #5b9300;
    border: 1px solid #76b900;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#primaryBtn:hover {
    background-color: #6cad00;
}
QPushButton#dangerBtn {
    background-color: #6b2429;
    border: 1px solid #a13a42;
    color: #ffffff;
}
QPushButton#dangerBtn:hover {
    background-color: #842d33;
}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTableWidget {
    background-color: #0e1217;
    border: 1px solid #2b3645;
    border-radius: 4px;
    padding: 4px 8px;
    color: #e6edf3;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #232d3a;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #76b900;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #5b9300;
    border-radius: 3px;
}
QHeaderView::section {
    background-color: #1b222c;
    color: #b7c8da;
    padding: 5px;
    border: 1px solid #26303d;
}
"""


class WorkerThread(QThread):
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)
    log_line = pyqtSignal(str)

    def __init__(self, fn: Callable[[Callable[[str], None]], str]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            msg = self._fn(lambda line: self.log_line.emit(line))
            self.finished_ok.emit(msg or "Concluído.")
        except Exception as exc:
            self.finished_err.emit(str(exc))


class FloatSliderRow(QWidget):
    """Horizontal slider mapped to a float range with live numeric readout."""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        minimum: float,
        maximum: float,
        step: float = 0.01,
        decimals: int = 2,
        suffix: str = "",
        special_min_label: str | None = None,
    ) -> None:
        super().__init__()
        self._min = minimum
        self._max = maximum
        self._step = step
        self._decimals = decimals
        self._suffix = suffix
        self._special_min_label = special_min_label
        self._steps = max(1, int(round((maximum - minimum) / step)))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, self._steps)
        self.label = QLabel()
        self.label.setMinimumWidth(78)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)

        self.slider.valueChanged.connect(self._on_int_changed)
        self.set_value(minimum)

    def _to_float(self, idx: int) -> float:
        return self._min + idx * self._step

    def _update_label(self, val: float) -> None:
        if self._special_min_label and abs(val - self._min) < (self._step * 0.5):
            self.label.setText(self._special_min_label)
        else:
            self.label.setText(f"{val:.{self._decimals}f}{self._suffix}")

    def _on_int_changed(self, idx: int) -> None:
        val = self._to_float(idx)
        self._update_label(val)
        self.valueChanged.emit(val)

    def value(self) -> float:
        return self._to_float(self.slider.value())

    def set_value(self, val: float) -> None:
        clamped = max(self._min, min(self._max, float(val)))
        idx = int(round((clamped - self._min) / self._step))
        self.slider.blockSignals(True)
        self.slider.setValue(idx)
        self.slider.blockSignals(False)
        self._update_label(self._to_float(idx))


class DLSS5FilterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Prysm — Neural Rendering Studio (Vulkan & Proton)")
        self.resize(980, 760)
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "prysm.png"
        if icon_path.is_file():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_path)))
        cfg_shm = os.environ.get("DLSSNR_SHM") or read_dlssnr_config().get("shm")
        self.shm = ShmBridge(Path(cfg_shm)) if cfg_shm else ShmBridge()
        self._gui_pid_file = self.shm.path.parent / "gui.pid"
        self.shm.path.parent.mkdir(parents=True, exist_ok=True)
        self._gui_pid_file.write_text(str(os.getpid()), encoding="utf-8")
        self._syncing_ui = False
        self._worker: WorkerThread | None = None
        self._runners = discover_runners()
        self._steam_games = []

        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        main_layout.addWidget(self._build_header_bar())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_filter_tab(), "Filtro Neural (Ao Vivo)")
        self.tabs.addTab(self._build_tutorial_tab(), "Como Usar (Steam)")
        self.tabs.addTab(self._build_games_tab(), "Jogos (Vulkan & Proton)")
        self.tabs.addTab(self._build_presr_tab(), "Pre-SR Realista (OptiScaler-NR)")
        self.tabs.addTab(self._build_diag_tab(), "Motor, DLL & Diagnóstico")
        main_layout.addWidget(self.tabs, 1)

        self._refresh_diagnostics()
        self._refresh_steam_games()
        self._refresh_recent_games()
        self._poll_shm()

        self._timer = QTimer(self)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._poll_shm)
        self._timer.start()

    # ------------------------------------------------------------------ header
    def _build_header_bar(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #12171f; border: 1px solid #263644; border-radius: 10px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "prysm.png"
        if icon_path.is_file():
            from PyQt6.QtGui import QPixmap
            lbl_icon = QLabel()
            lbl_icon.setStyleSheet("border: none;")
            lbl_icon.setPixmap(QPixmap(str(icon_path)).scaled(32, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            top.addWidget(lbl_icon)
        title = QLabel("PRYSM")
        f = QFont()
        f.setPointSize(15)
        f.setBold(True)
        title.setFont(f)
        title.setStyleSheet("color: #8ce000; letter-spacing: 1px; border: none;")
        top.addWidget(title)

        self.lbl_helper_pill = QLabel("Helper: --")
        self.lbl_helper_pill.setStyleSheet(
            "background: #232c38; padding: 4px 10px; border-radius: 10px; border: none;"
        )
        self.lbl_model_pill = QLabel("Modelo: --")
        self.lbl_model_pill.setStyleSheet(
            "background: #232c38; padding: 4px 10px; border-radius: 10px; border: none;"
        )
        self.lbl_game_pill = QLabel("Jogo: aguardando conexão Vulkan/Proton")
        self.lbl_game_pill.setStyleSheet(
            "background: #232c38; padding: 4px 10px; border-radius: 10px; border: none;"
        )
        top.addWidget(self.lbl_helper_pill)
        top.addWidget(self.lbl_model_pill)
        top.addWidget(self.lbl_game_pill, 1)

        self.btn_start_helper = QPushButton("Iniciar Filtro")
        self.btn_start_helper.setObjectName("primaryBtn")
        self.btn_start_helper.clicked.connect(self._on_start_helper)
        self.btn_overlay = QPushButton("Overlay (Page Up)")
        self.btn_overlay.setToolTip("Abrir ou ativar o menu overlay flutuante (tecla Page Up / Delete)")
        self.btn_overlay.clicked.connect(self._on_toggle_overlay)
        self.btn_stop_helper = QPushButton("Parar")
        self.btn_stop_helper.setObjectName("dangerBtn")
        self.btn_stop_helper.clicked.connect(self._on_stop_helper)
        top.addWidget(self.btn_start_helper)
        top.addWidget(self.btn_overlay)
        top.addWidget(self.btn_stop_helper)
        layout.addLayout(top)

        bottom = QHBoxLayout()
        self.lbl_telemetry = QLabel("Telemetria: --")
        self.lbl_telemetry.setStyleSheet("color: #9fb0c3; border: none; font-size: 12px;")
        bottom.addWidget(self.lbl_telemetry, 1)

        self.chk_enabled = QCheckBox("Filtro Ativo")
        self.chk_enabled.setStyleSheet("border: none; font-weight: bold;")
        self.chk_enabled.toggled.connect(lambda c: self._write_shm({"enabled": int(c)}))

        self.chk_ab = QCheckBox("Aplicar Edição (A/B)")
        self.chk_ab.setStyleSheet("border: none;")
        self.chk_ab.toggled.connect(lambda c: self._write_shm({"applyModel": int(c)}))

        self.chk_hold = QCheckBox("Congelar Frame")
        self.chk_hold.setStyleSheet("border: none;")
        self.chk_hold.toggled.connect(lambda c: self._write_shm({"holdFrame": int(c)}))

        bottom.addWidget(self.chk_enabled)
        bottom.addWidget(self.chk_ab)
        bottom.addWidget(self.chk_hold)
        layout.addLayout(bottom)
        return frame

    # -------------------------------------------------------------- tab 1: live filter
    def _build_filter_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(10)

        # Presets strip
        preset_box = QGroupBox("Presets Rápidos & Perfis")
        p_layout = QVBoxLayout(preset_box)
        btn_row = QHBoxLayout()
        for pname in BUILTIN_PRESETS:
            b = QPushButton(pname)
            b.clicked.connect(lambda _, n=pname: self._apply_preset(BUILTIN_PRESETS[n]))
            btn_row.addWidget(b)
        p_layout.addLayout(btn_row)

        user_row = QHBoxLayout()
        self.cmb_user_profiles = QComboBox()
        self._reload_user_profiles()
        btn_load_prof = QPushButton("Carregar Perfil")
        btn_load_prof.clicked.connect(self._on_load_profile)
        btn_save_prof = QPushButton("Salvar Perfil Atual...")
        btn_save_prof.clicked.connect(self._on_save_profile)
        btn_reset = QPushButton("Restaurar Padrões")
        btn_reset.clicked.connect(self._on_reset_settings)
        btn_capture = QPushButton("Capturar Frame (Antes/Depois)")
        btn_capture.clicked.connect(lambda: self.shm.capture_frames(1))
        user_row.addWidget(QLabel("Perfil salvo:"))
        user_row.addWidget(self.cmb_user_profiles, 1)
        user_row.addWidget(btn_load_prof)
        user_row.addWidget(btn_save_prof)
        user_row.addWidget(btn_reset)
        user_row.addWidget(btn_capture)
        p_layout.addLayout(user_row)
        layout.addWidget(preset_box)

        # Neural model
        model_box = QGroupBox("Modelo Neural (nvngx_dlssnr.dll)")
        mf = QFormLayout(model_box)

        self.cmb_preset = QComboBox()
        for k, v in NR_MODELS.items():
            self.cmb_preset.addItem(v, k)
        self.cmb_preset.currentIndexChanged.connect(
            lambda: self._write_shm({"preset": self.cmb_preset.currentData()})
        )

        self.cmb_style = QComboBox()
        for k, v in STYLES.items():
            self.cmb_style.addItem(v, k)
        self.cmb_style.currentIndexChanged.connect(
            lambda: self._write_shm({"style": self.cmb_style.currentData()})
        )

        self.spn_passes = QSpinBox()
        self.spn_passes.setRange(1, 10)
        self.spn_passes.valueChanged.connect(lambda v: self._write_shm({"passes": v}))

        self.sld_intensity = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_intensity.valueChanged.connect(lambda v: self._write_shm({"intensity": v}))

        self.sld_tone = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_tone.valueChanged.connect(lambda v: self._write_shm({"localTone": v}))

        self.sld_structure = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_structure.valueChanged.connect(lambda v: self._write_shm({"localStructure": v}))

        self.sld_skin = FloatSliderRow(-1.0, 2.0, 0.05, special_min_label="Auto (-1)")
        self.sld_skin.valueChanged.connect(lambda v: self._write_shm({"skinStructure": v}))

        self.sld_sharpness = FloatSliderRow(0.0, 1.0, 0.02)
        self.sld_sharpness.valueChanged.connect(lambda v: self._write_shm({"sharpness": v}))

        self.chk_automask = QCheckBox("AutoMask (proteção automática de pele/UI)")
        self.chk_automask.toggled.connect(lambda c: self._write_shm({"autoMask": int(c)}))

        mf.addRow("Rede Neural (Preset):", self.cmb_preset)
        mf.addRow("Estilo do Modelo:", self.cmb_style)
        mf.addRow("Passadas (Multi-Pass):", self.spn_passes)
        mf.addRow("Intensidade:", self.sld_intensity)
        mf.addRow("Tom Local:", self.sld_tone)
        mf.addRow("Estrutura Local:", self.sld_structure)
        mf.addRow("Estrutura de Pele:", self.sld_skin)
        mf.addRow("Nitidez (Sharpness):", self.sld_sharpness)
        mf.addRow("", self.chk_automask)
        layout.addWidget(model_box)

        # Composition & HDR
        comp_box = QGroupBox("Composição, Escala & HDR")
        cf = QFormLayout(comp_box)

        self.chk_bypass = QCheckBox("Bypass da Composição (saída direta do modelo)")
        self.chk_bypass.toggled.connect(lambda c: self._write_shm({"compositionBypass": int(c)}))

        self.sld_transfer_str = FloatSliderRow(0.0, 2.5, 0.05)
        self.sld_transfer_str.valueChanged.connect(
            lambda v: self._write_shm({"transferStrength": v})
        )

        self.sld_colour_str = FloatSliderRow(0.0, 2.5, 0.05)
        self.sld_colour_str.valueChanged.connect(lambda v: self._write_shm({"colourStrength": v}))

        self.sld_guard = FloatSliderRow(1.0, 8.0, 0.1)
        self.sld_guard.valueChanged.connect(lambda v: self._write_shm({"maxRatio": v}))

        self.cmb_transfer = QComboBox()
        for k, v in TRANSFER_MODES.items():
            self.cmb_transfer.addItem(v, k)
        self.cmb_transfer.currentIndexChanged.connect(
            lambda: self._write_shm({"transfer": self.cmb_transfer.currentData()})
        )

        self.cmb_reversible = QComboBox()
        for k, v in REVERSIBLE_MODES.items():
            self.cmb_reversible.addItem(v, k)
        self.cmb_reversible.currentIndexChanged.connect(
            lambda: self._write_shm({"reversibleMode": self.cmb_reversible.currentData()})
        )

        self.sld_work_scale = FloatSliderRow(0.50, 2.00, 0.05, suffix="x")
        self.sld_work_scale.valueChanged.connect(lambda v: self._write_shm({"workingScale": v}))

        self.cmb_downscaler = QComboBox()
        for k, v in DOWNSCALERS.items():
            self.cmb_downscaler.addItem(v, k)
        self.cmb_downscaler.currentIndexChanged.connect(
            lambda: self._write_shm({"scalingDownscaler": self.cmb_downscaler.currentData()})
        )

        self.cmb_hdr = QComboBox()
        for k, v in HDR_MODES.items():
            self.cmb_hdr.addItem(v, k)
        self.cmb_hdr.currentIndexChanged.connect(
            lambda: self._write_shm({"hdrMode": self.cmb_hdr.currentData()})
        )

        self.cmb_colour_mode = QComboBox()
        for k, v in COLOUR_MODES.items():
            self.cmb_colour_mode.addItem(v, k)
        self.cmb_colour_mode.currentIndexChanged.connect(
            lambda: self._write_shm({"colourMode": self.cmb_colour_mode.currentData()})
        )

        self.chk_sdr16 = QCheckBox("SDR 16-bit entre passadas (reduz banding em Multi-Pass)")
        self.chk_sdr16.toggled.connect(lambda c: self._write_shm({"sdr16Multipass": int(c)}))

        cf.addRow("", self.chk_bypass)
        cf.addRow("Força do Detalhe:", self.sld_transfer_str)
        cf.addRow("Força da Cor:", self.sld_colour_str)
        cf.addRow("Proteção Highlights (Max Ratio):", self.sld_guard)
        cf.addRow("Modo de Transferência:", self.cmb_transfer)
        cf.addRow("Proxy Reversível (RenoDX):", self.cmb_reversible)
        cf.addRow("Escala de Trabalho:", self.sld_work_scale)
        cf.addRow("Filtro Downscaler (>1x):", self.cmb_downscaler)
        cf.addRow("Entrada HDR:", self.cmb_hdr)
        cf.addRow("Espaço de Cor:", self.cmb_colour_mode)
        cf.addRow("", self.chk_sdr16)
        layout.addWidget(comp_box)

        # Motion Vectors & Compare
        mvec_box = QGroupBox("Vetores de Movimento (VK_NV_optical_flow) & Comparação")
        vf = QFormLayout(mvec_box)

        self.chk_mvec = QCheckBox("Estimar vetores de movimento via Optical Flow")
        self.chk_mvec.toggled.connect(lambda c: self._write_shm({"mvecEnabled": int(c)}))

        self.cmb_mvec_q = QComboBox()
        for k, v in MVEC_QUALITIES.items():
            self.cmb_mvec_q.addItem(v, k)
        self.cmb_mvec_q.currentIndexChanged.connect(
            lambda: self._write_shm({"mvecQuality": self.cmb_mvec_q.currentData()})
        )

        self.cmb_mvec_px = QComboBox()
        for k, v in MVEC_PIXEL_SIZES.items():
            self.cmb_mvec_px.addItem(v, k)
        self.cmb_mvec_px.currentIndexChanged.connect(
            lambda: self._write_shm({"mvecPixelSize": self.cmb_mvec_px.currentData()})
        )

        self.cmb_mvec_units = QComboBox()
        for k, v in MVEC_SCALE_MODES.items():
            self.cmb_mvec_units.addItem(v, k)
        self.cmb_mvec_units.currentIndexChanged.connect(
            lambda: self._write_shm({"mvecScaleMode": self.cmb_mvec_units.currentData()})
        )

        self.cmb_compare = QComboBox()
        for k, v in COMPARE_MODES.items():
            self.cmb_compare.addItem(v, k)
        self.cmb_compare.currentIndexChanged.connect(
            lambda: self._write_shm({"compareMode": self.cmb_compare.currentData()})
        )

        self.sld_split = FloatSliderRow(0.0, 1.0, 0.02)
        self.sld_split.valueChanged.connect(lambda v: self._write_shm({"compareSplit": v}))

        self.chk_compare_swap = QCheckBox("Inverter lados da comparação")
        self.chk_compare_swap.toggled.connect(lambda c: self._write_shm({"compareSwap": int(c)}))

        vf.addRow("", self.chk_mvec)
        vf.addRow("Qualidade Optical Flow:", self.cmb_mvec_q)
        vf.addRow("Grade Optical Flow:", self.cmb_mvec_px)
        vf.addRow("Unidade MVec:", self.cmb_mvec_units)
        vf.addRow("Modo Comparação:", self.cmb_compare)
        vf.addRow("Posição Divisória:", self.sld_split)
        vf.addRow("", self.chk_compare_swap)
        layout.addWidget(mvec_box)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    # -------------------------------------------------------------- tab: tutorial
    def _build_tutorial_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setSpacing(12)

        intro = QLabel(
            "<b>Como ativar o Prysm na Steam (sem copiar nenhuma DLL para a pasta do jogo):</b><br>"
            "O Prysm usa uma <i>Vulkan Implicit Layer</i> + wrapper <code>prysm-run</code>, que liga "
            "automaticamente a GPU NVIDIA (PRIME), o motor neural e o overlay in-game (<b>Page Up</b> / <b>Delete</b>)."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #d8e3f0; font-size: 13px;")
        lay.addWidget(intro)

        guide_img = Path(__file__).resolve().parent.parent / "assets" / "tutorial_steam_guide.png"
        if guide_img.is_file():
            from PyQt6.QtGui import QPixmap
            lbl_img = QLabel()
            lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = QPixmap(str(guide_img)).scaledToWidth(880, Qt.TransformationMode.SmoothTransformation)
            lbl_img.setPixmap(pix)
            lbl_img.setStyleSheet("background: #0a0e14; border: 1px solid #263644; border-radius: 10px; padding: 6px;")
            lay.addWidget(lbl_img)

        prysm_run_path = str(Path.home() / ".local/bin/prysm-run")
        cmd_box = QGroupBox("Comandos prontos para colar em Launch Options na Steam")
        cmd_lay = QVBoxLayout(cmd_box)

        rows = [
            ("Recomendado (inicia desligado para carregar rápido; ligue no jogo com Page Up):", f"{prysm_run_path} --off %command%"),
            ("Direto (já inicia com o filtro neural ligado):", f"{prysm_run_path} %command%"),
            ("Com mods REFramework / dinput8.dll (ex: Dragon's Dogma 2):", f'WINEDLLOVERRIDES="dinput8=n,b" {prysm_run_path} --off %command%'),
        ]
        for label_txt, cmd_txt in rows:
            l = QLabel(label_txt)
            l.setStyleSheet("color: #9fb0c3; font-size: 12px;")
            cmd_lay.addWidget(l)
            r = QHBoxLayout()
            edt = QLineEdit(cmd_txt)
            edt.setReadOnly(True)
            btn = QPushButton("Copiar")
            btn.clicked.connect(lambda _checked=False, t=cmd_txt: QApplication.clipboard().setText(t))
            r.addWidget(edt, 1)
            r.addWidget(btn)
            cmd_lay.addLayout(r)
        lay.addWidget(cmd_box)

        tips = QLabel(
            "<b>Atalhos dentro do jogo:</b> <code>Page Up</code> (ou <code>Delete</code>) abre/fecha o "
            "<b>Prysm Overlay</b> com o mouse do sistema liberado; <code>Esc</code> oculta e devolve o foco ao jogo."
        )
        tips.setWordWrap(True)
        tips.setStyleSheet("color: #8ce000; font-size: 12px;")
        lay.addWidget(tips)
        lay.addStretch(1)

        scroll.setWidget(body)
        return scroll

    # -------------------------------------------------------------- tab 2: games
    def _build_games_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        launch_box = QGroupBox("Abrir Qualquer Jogo em Conjunto (Vulkan Nativo ou Proton .exe)")
        lf = QFormLayout(launch_box)

        self.cmb_launch_mode = QComboBox()
        self.cmb_launch_mode.addItem("Windows (.exe) via Proton / Wine", "proton")
        self.cmb_launch_mode.addItem("Linux Vulkan Nativo (binário ou script)", "vulkan")
        self.cmb_launch_mode.currentIndexChanged.connect(self._update_launch_mode_ui)

        exe_row = QHBoxLayout()
        self.edt_exe = QLineEdit()
        self.edt_exe.setPlaceholderText("/caminho/do/jogo.exe ou binário Vulkan...")
        btn_browse_exe = QPushButton("Procurar...")
        btn_browse_exe.clicked.connect(self._on_browse_exe)
        exe_row.addWidget(self.edt_exe, 1)
        exe_row.addWidget(btn_browse_exe)

        self.cmb_game_runner = QComboBox()
        for r in self._runners:
            self.cmb_game_runner.addItem(f"{r.name} ({r.runner_type})", r)

        pfx_row = QHBoxLayout()
        self.edt_prefix = QLineEdit()
        self.edt_prefix.setPlaceholderText("(opcional) Prefixo Proton/Wine; vazio cria automático")
        btn_browse_pfx = QPushButton("Pasta...")
        btn_browse_pfx.clicked.connect(self._on_browse_prefix)
        pfx_row.addWidget(self.edt_prefix, 1)
        pfx_row.addWidget(btn_browse_pfx)

        self.edt_args = QLineEdit()
        self.edt_args.setPlaceholderText("Argumentos opcionais (ex: -vulkan --fullscreen)")

        opt_row = QHBoxLayout()
        self.chk_mangohud = QCheckBox("MangoHud")
        self.chk_verbose_layer = QCheckBox("Log detalhado da Layer")
        opt_row.addWidget(self.chk_mangohud)
        opt_row.addWidget(self.chk_verbose_layer)
        opt_row.addStretch(1)

        btn_launch = QPushButton("▶ Abrir Jogo com Filtro DLSS 5")
        btn_launch.setObjectName("primaryBtn")
        btn_launch.clicked.connect(self._on_launch_game)

        lf.addRow("Tipo de Jogo:", self.cmb_launch_mode)
        lf.addRow("Executável:", exe_row)
        lf.addRow("Runner Proton/Wine:", self.cmb_game_runner)
        lf.addRow("Prefixo Proton:", pfx_row)
        lf.addRow("Argumentos:", self.edt_args)
        lf.addRow("Opções:", opt_row)
        lf.addRow("", btn_launch)
        layout.addWidget(launch_box)

        # Recent games
        recent_box = QGroupBox("Jogos Recentes")
        rl = QHBoxLayout(recent_box)
        self.cmb_recent = QComboBox()
        btn_load_recent = QPushButton("Carregar Selecionado")
        btn_load_recent.clicked.connect(self._on_select_recent)
        rl.addWidget(self.cmb_recent, 1)
        rl.addWidget(btn_load_recent)
        layout.addWidget(recent_box)

        # Steam integration
        steam_box = QGroupBox("Steam — Usar em Conjunto com Qualquer Jogo (Proton ou Nativo)")
        sl = QVBoxLayout(steam_box)

        cmd_row = QHBoxLayout()
        self.edt_steam_cmd = QLineEdit("dlss5-run %command%")
        self.edt_steam_cmd.setReadOnly(True)
        btn_copy_cmd = QPushButton("Copiar Opção de Inicialização")
        btn_copy_cmd.clicked.connect(self._on_copy_steam_cmd)
        btn_copy_env = QPushButton("Copiar só VKLayer_DLSS5=1 %command%")
        btn_copy_env.clicked.connect(self._on_copy_vk_cmd)
        cmd_row.addWidget(QLabel("Steam Launch Options:"))
        cmd_row.addWidget(self.edt_steam_cmd, 1)
        cmd_row.addWidget(btn_copy_cmd)
        cmd_row.addWidget(btn_copy_env)
        sl.addLayout(cmd_row)

        info_lbl = QLabel(
            "Dica: Cole `dlss5-run %command%` nas Propriedades -> Opções de Inicialização do jogo na Steam. "
            "Ele inicia o filtro automaticamente se estiver fechado e deixa este painel controlar o jogo ao vivo."
        )
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #9fb0c3; font-size: 12px;")
        sl.addWidget(info_lbl)

        self.tbl_steam = QTableWidget(0, 3)
        self.tbl_steam.setHorizontalHeaderLabels(["Jogo Steam", "AppID", "Pasta de Instalação"])
        self.tbl_steam.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_steam.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_steam.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tbl_steam.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_steam.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        sl.addWidget(self.tbl_steam, 1)

        steam_btns = QHBoxLayout()
        btn_refresh_steam = QPushButton("Atualizar Biblioteca Steam")
        btn_refresh_steam.clicked.connect(self._refresh_steam_games)
        btn_open_folder = QPushButton("Abrir Pasta do Jogo")
        btn_open_folder.clicked.connect(self._on_open_steam_folder)
        btn_send_presr = QPushButton("Configurar Pre-SR neste Jogo (OptiScaler-NR)")
        btn_send_presr.setObjectName("primaryBtn")
        btn_send_presr.clicked.connect(self._on_send_steam_to_presr)
        steam_btns.addWidget(btn_refresh_steam)
        steam_btns.addWidget(btn_open_folder)
        steam_btns.addWidget(btn_send_presr)
        steam_btns.addStretch(1)
        sl.addLayout(steam_btns)

        layout.addWidget(steam_box, 1)
        self._update_launch_mode_ui()
        return w

    # -------------------------------------------------------------- tab 3: Pre-SR (OptiScaler-NR)
    def _build_presr_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        desc = QLabel(
            "Para jogos Proton/Windows que JÁ possuem DLSS, FSR2/3 ou XeSS: instala o OptiScaler-NR v0.8.93 "
            "na pasta do executável real. Ele roda o DLSS 5 ANTES do upscaling (resolução interna reduzida + "
            "Depth/Motion Vectors reais + sem HUD), com Compressão Espacial Periférica (80% centro / 90% área) "
            "e modelo FP8 otimizado (310.9.16). Não use junto com VKLayer_DLSS5=1."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #9fb0c3; font-size: 12px;")
        layout.addWidget(desc)

        inst_box = QGroupBox("Instalar / Gerenciar Pre-SR na Pasta do Jogo")
        inf = QFormLayout(inst_box)

        dir_row = QHBoxLayout()
        self.edt_presr_dir = QLineEdit()
        self.edt_presr_dir.setPlaceholderText("Pasta onde está o .exe real do jogo (ex: .../bin/x64 ou .../Win64)")
        self.edt_presr_dir.textChanged.connect(self._refresh_presr_status)
        btn_presr_browse = QPushButton("Procurar Pasta...")
        btn_presr_browse.clicked.connect(self._on_browse_presr_dir)
        btn_presr_exe = QPushButton("Selecionar .exe...")
        btn_presr_exe.clicked.connect(self._on_browse_presr_exe)
        dir_row.addWidget(self.edt_presr_dir, 1)
        dir_row.addWidget(btn_presr_browse)
        dir_row.addWidget(btn_presr_exe)

        self.cmb_presr_api = QComboBox()
        self.cmb_presr_api.addItem("DirectX 12 (Proton vkd3d-proton)", "dx12")
        self.cmb_presr_api.addItem("DirectX 11 -> Bridge D3D12 (ffx_12)", "dx11")
        self.cmb_presr_api.addItem("Vulkan Nativo Windows", "vulkan")

        self.cmb_presr_hook = QComboBox()
        for dll, label in HOOK_OPTIONS:
            self.cmb_presr_hook.addItem(label, dll)

        self.cmb_presr_profile = QComboBox()
        for pname in PRESR_PROFILES:
            self.cmb_presr_profile.addItem(pname, pname)

        self.lbl_presr_status = QLabel("Selecione a pasta do executável do jogo.")
        self.lbl_presr_status.setStyleSheet("color: #9fb0c3;")

        btn_row = QHBoxLayout()
        btn_presr_install = QPushButton("Instalar / Aplicar Pre-SR Realista")
        btn_presr_install.setObjectName("primaryBtn")
        btn_presr_install.clicked.connect(self._on_install_presr)
        btn_presr_remove = QPushButton("Remover Pre-SR (Restaurar Backup)")
        btn_presr_remove.setObjectName("dangerBtn")
        btn_presr_remove.clicked.connect(self._on_remove_presr)
        btn_row.addWidget(btn_presr_install)
        btn_row.addWidget(btn_presr_remove)
        btn_row.addStretch(1)

        steam_row = QHBoxLayout()
        self.edt_presr_steam = QLineEdit()
        self.edt_presr_steam.setReadOnly(True)
        btn_copy_presr_steam = QPushButton("Copiar para Steam")
        btn_copy_presr_steam.clicked.connect(self._on_copy_presr_steam)
        btn_launch_presr = QPushButton("▶ Abrir .exe com Pre-SR")
        btn_launch_presr.clicked.connect(self._on_launch_presr_exe)
        steam_row.addWidget(self.edt_presr_steam, 1)
        steam_row.addWidget(btn_copy_presr_steam)
        steam_row.addWidget(btn_launch_presr)

        inf.addRow("Pasta do Executável:", dir_row)
        inf.addRow("API Gráfica:", self.cmb_presr_api)
        inf.addRow("DLL Hook (Proton):", self.cmb_presr_hook)
        inf.addRow("Perfil Inicial:", self.cmb_presr_profile)
        inf.addRow("Status:", self.lbl_presr_status)
        inf.addRow("", btn_row)
        inf.addRow("Steam Launch Options:", steam_row)
        layout.addWidget(inst_box)

        live_box = QGroupBox("Ajuste Ao Vivo em OptiScaler.ini ([DlssNr] — recarregado a cada 200 ms)")
        lf = QFormLayout(live_box)

        self.chk_presr_enabled = QCheckBox("Enabled (DLSS 5 ativo)")
        self.chk_presr_before_sr = QCheckBox("RunBeforeSR (rodar ANTES do DLSS/FSR/XeSS)")
        self.chk_presr_spatial = QCheckBox("SpatialCompression (compressão periférica 80% centro / 90% área)")
        self.chk_presr_rr = QCheckBox("ResidualAcrossRR (compatível com Ray Reconstruction)")

        self.cmb_presr_model = QComboBox()
        self.cmb_presr_model.addItem("0 - Automático", "0")
        self.cmb_presr_model.addItem("1 - Original", "1")
        self.cmb_presr_model.addItem("2 - Otimizado FP8 (310.9.16)", "2")

        self.cmb_presr_style = QComboBox()
        self.cmb_presr_style.addItem("0 - Padrão", "0")
        self.cmb_presr_style.addItem("1 - Natural (Realista)", "1")
        self.cmb_presr_style.addItem("2 - Cinematográfico", "2")

        self.sld_presr_scale = FloatSliderRow(0.50, 1.50, 0.05, suffix="x")
        self.sld_presr_intensity = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_presr_structure = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_presr_tone = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_presr_transfer = FloatSliderRow(0.0, 2.0, 0.05)
        self.sld_presr_colour = FloatSliderRow(0.0, 2.0, 0.05)

        btn_apply_ini = QPushButton("Aplicar Agora no OptiScaler.ini (Ao Vivo)")
        btn_apply_ini.setObjectName("primaryBtn")
        btn_apply_ini.clicked.connect(self._on_apply_presr_live_ini)

        lf.addRow("", self.chk_presr_enabled)
        lf.addRow("", self.chk_presr_before_sr)
        lf.addRow("", self.chk_presr_spatial)
        lf.addRow("", self.chk_presr_rr)
        lf.addRow("Rede Neural (Preset):", self.cmb_presr_model)
        lf.addRow("Estilo:", self.cmb_presr_style)
        lf.addRow("WorkingScale:", self.sld_presr_scale)
        lf.addRow("Intensity:", self.sld_presr_intensity)
        lf.addRow("LocalStructure:", self.sld_presr_structure)
        lf.addRow("LocalTone:", self.sld_presr_tone)
        lf.addRow("TransferStrength:", self.sld_presr_transfer)
        lf.addRow("ColourStrength:", self.sld_presr_colour)
        lf.addRow("", btn_apply_ini)
        layout.addWidget(live_box)

        layout.addStretch(1)
        scroll.setWidget(w)
        return scroll

    # -------------------------------------------------------------- tab 4: diagnostics
    def _build_diag_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(10)

        status_box = QGroupBox("Componentes Instalados & Hardware")
        sf = QFormLayout(status_box)
        self.lbl_gpu = QLabel("--")
        self.lbl_layer = QLabel("--")
        self.lbl_dll = QLabel("--")
        self.lbl_dmabuf = QLabel("--")
        self.cmb_helper_runner = QComboBox()
        for r in self._runners:
            tag = "★ " if r.recommended else "⚠ "
            self.cmb_helper_runner.addItem(f"{tag}{r.name} — {r.note}", r)

        btn_save_runner = QPushButton("Aplicar Runner no Helper")
        btn_save_runner.clicked.connect(self._on_apply_helper_runner)
        runner_row = QHBoxLayout()
        runner_row.addWidget(self.cmb_helper_runner, 1)
        runner_row.addWidget(btn_save_runner)

        sf.addRow("GPU / Driver:", self.lbl_gpu)
        sf.addRow("DLSS5VKLayer (64/32-bit):", self.lbl_layer)
        sf.addRow("DLL Neural (nvngx_dlssnr.dll):", self.lbl_dll)
        sf.addRow("Transporte dma-buf (ptrace_scope):", self.lbl_dmabuf)
        sf.addRow("Runner do Helper NGX:", runner_row)
        layout.addWidget(status_box)

        actions_row = QHBoxLayout()
        btn_inst_layer = QPushButton("Instalar/Atualizar DLSS5VKLayer (GitHub)")
        btn_inst_layer.clicked.connect(self._on_install_vklayer)
        btn_dl_dll = QPushButton("Baixar nvngx_dlssnr.dll 310.9.16 (GitHub)")
        btn_dl_dll.setObjectName("primaryBtn")
        btn_dl_dll.clicked.connect(self._on_download_dll)
        btn_import_local = QPushButton("Importar DLL Neural Local...")
        btn_import_local.clicked.connect(self._on_import_local_dll)
        btn_native_gui = QPushButton("Abrir GUI Qt Original (dlssnr-gui)")
        btn_native_gui.clicked.connect(self._on_open_native_gui)
        btn_doctor = QPushButton("Rodar Doctor")
        btn_doctor.clicked.connect(self._on_run_doctor)

        actions_row.addWidget(btn_inst_layer)
        actions_row.addWidget(btn_dl_dll)
        actions_row.addWidget(btn_import_local)
        actions_row.addWidget(btn_native_gui)
        actions_row.addWidget(btn_doctor)
        layout.addLayout(actions_row)

        log_box = QGroupBox("Log do Motor (helper.log & Operações)")
        ll = QVBoxLayout(log_box)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("monospace", 10))
        ll.addWidget(self.txt_log, 1)
        log_btns = QHBoxLayout()
        btn_refresh_log = QPushButton("Atualizar Log")
        btn_refresh_log.clicked.connect(lambda: self.txt_log.setPlainText(read_helper_log(160)))
        log_btns.addWidget(btn_refresh_log)
        log_btns.addStretch(1)
        ll.addLayout(log_btns)
        layout.addWidget(log_box, 1)
        return w

    # -------------------------------------------------------------- actions
    def _write_shm(self, updates: dict[str, Any]) -> None:
        if self._syncing_ui:
            return
        try:
            self.shm.apply_settings(updates)
        except Exception as exc:
            self.txt_log.appendPlainText(f"[erro shm] {exc}")

    def _apply_preset(self, preset: dict[str, Any]) -> None:
        self.shm.apply_settings(preset)
        self._poll_shm()

    def _reload_user_profiles(self) -> None:
        self.cmb_user_profiles.clear()
        for name in list_user_profiles():
            self.cmb_user_profiles.addItem(name)

    def _on_save_profile(self) -> None:
        snap = self.shm.read_snapshot()
        if not snap or not snap.initialised:
            QMessageBox.information(self, "Aviso", "Inicie o filtro ou ajuste um controle primeiro.")
            return
        name, ok = QInputDialog.getText(self, "Salvar Perfil", "Nome do perfil:")
        if ok and name.strip():
            save_user_profile(name.strip(), snap.settings)
            self._reload_user_profiles()

    def _on_load_profile(self) -> None:
        name = self.cmb_user_profiles.currentText()
        if not name:
            return
        self._apply_preset(load_user_profile(name))

    def _on_reset_settings(self) -> None:
        self.shm.reset_settings()
        self._poll_shm()

    def _run_async(self, fn: Callable[[Callable[[str], None]], str]) -> None:
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Ocupado", "Aguarde a operação atual terminar.")
            return
        self._worker = WorkerThread(fn)
        self._worker.log_line.connect(lambda l: self.txt_log.appendPlainText(l))
        self._worker.finished_ok.connect(self._on_worker_ok)
        self._worker.finished_err.connect(self._on_worker_err)
        self._worker.start()

    def _on_worker_ok(self, msg: str) -> None:
        self.txt_log.appendPlainText(msg)
        self._refresh_diagnostics()
        self._poll_shm()

    def _on_worker_err(self, err: str) -> None:
        self.txt_log.appendPlainText(f"[ERRO] {err}")
        QMessageBox.critical(self, "Erro", err)
        self._refresh_diagnostics()

    def _on_start_helper(self) -> None:
        self.btn_start_helper.setEnabled(False)

        def task(_log: Callable[[str], None]) -> str:
            rc, out = run_helper_cmd("start")
            if rc != 0:
                raise RuntimeError(out or "Falha ao iniciar helper")
            return out

        def done(msg: str) -> None:
            self.btn_start_helper.setEnabled(True)
            self._on_worker_ok(msg)

        def fail(err: str) -> None:
            self.btn_start_helper.setEnabled(True)
            self._on_worker_err(err)

        self._worker = WorkerThread(task)
        self._worker.finished_ok.connect(done)
        self._worker.finished_err.connect(fail)
        self._worker.start()

    def _on_stop_helper(self) -> None:
        rc, out = run_helper_cmd("stop")
        self.txt_log.appendPlainText(out or f"stop rc={rc}")
        self._poll_shm()

    def _on_toggle_overlay(self) -> None:
        import subprocess
        overlay_bin = Path(__file__).resolve().parent.parent / "dlss5-overlay"
        subprocess.Popen(
            [str(overlay_bin), "--toggle"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _on_install_vklayer(self) -> None:
        self.tabs.setCurrentIndex(3)
        self._run_async(lambda log: (install_vklayer(log), "DLSS5VKLayer instalada.")[1])

    def _on_download_dll(self) -> None:
        self.tabs.setCurrentIndex(3)
        self._run_async(
            lambda log: f"DLL neural instalada: {install_neural_dll_from_git(log)}"
        )

    def _on_import_local_dll(self) -> None:
        backups = find_local_backup_dll_dirs()
        start_dir = str(backups[0]) if backups else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Selecione a pasta contendo nvngx_dlssnr.dll", start_dir
        )
        if not chosen:
            return
        try:
            dest = import_local_neural_dir(Path(chosen))
            QMessageBox.information(self, "Sucesso", f"DLL importada para:\n{dest}")
            self._refresh_diagnostics()
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def _on_apply_helper_runner(self) -> None:
        r = self.cmb_helper_runner.currentData()
        if not r:
            return
        set_helper_runner(r.runner_type, r.path)
        self.txt_log.appendPlainText(f"Runner do helper alterado para: {r.name} ({r.path})")
        self._refresh_diagnostics()

    def _on_run_doctor(self) -> None:
        rc, out = run_helper_cmd("doctor")
        self.txt_log.setPlainText(out + f"\n\n[exit={rc}]")

    def _on_open_native_gui(self) -> None:
        gui_bin = xdg_dirs()["bin"] / "dlssnr-gui"
        if gui_bin.exists():
            subprocess.Popen([str(gui_bin)], start_new_session=True)
        else:
            QMessageBox.warning(self, "Não encontrado", f"{gui_bin} não encontrado.")

    # -------------------------------------------------------------- games tab
    def _update_launch_mode_ui(self) -> None:
        is_proton = self.cmb_launch_mode.currentData() == "proton"
        self.cmb_game_runner.setEnabled(is_proton)
        self.edt_prefix.setEnabled(is_proton)

    def _on_browse_exe(self) -> None:
        filt = "Executáveis (*.exe *.sh *.AppImage);;Todos (*)"
        file, _ = QFileDialog.getOpenFileName(self, "Selecionar Jogo / Executável", str(Path.home()), filt)
        if file:
            self.edt_exe.setText(file)
            if file.lower().endswith(".exe"):
                self.cmb_launch_mode.setCurrentIndex(0)

    def _on_browse_prefix(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta de Prefixo Proton/Wine", str(Path.home()))
        if d:
            self.edt_prefix.setText(d)

    def _on_launch_game(self) -> None:
        exe = self.edt_exe.text().strip()
        if not exe:
            QMessageBox.warning(self, "Faltando", "Informe o executável do jogo.")
            return
        mode = self.cmb_launch_mode.currentData()
        args = self.edt_args.text().strip()
        mango = self.chk_mangohud.isChecked()
        verb = self.chk_verbose_layer.isChecked()
        try:
            if mode == "proton":
                runner = self.cmb_game_runner.currentData()
                if runner is None:
                    raise RuntimeError("Nenhum runner Proton/Wine encontrado.")
                pfx_text = self.edt_prefix.text().strip()
                pfx = Path(pfx_text) if pfx_text else None
                proc, log_path = launch_proton_game(
                    Path(exe), runner, pfx, args=args, use_mangohud=mango, verbose_layer=verb
                )
            else:
                proc, log_path = launch_native_vulkan(
                    exe, args=args, use_mangohud=mango, verbose_layer=verb
                )
            add_recent_game(
                {
                    "mode": mode,
                    "exe": exe,
                    "args": args,
                    "prefix": self.edt_prefix.text().strip(),
                    "name": Path(exe).name,
                }
            )
            self._refresh_recent_games()
            self.txt_log.appendPlainText(f"Jogo iniciado (PID {proc.pid}). Log: {log_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao iniciar jogo", str(exc))

    def _refresh_recent_games(self) -> None:
        self.cmb_recent.clear()
        for item in load_recent_games():
            self.cmb_recent.addItem(f"{item.get('name', '?')} ({item.get('mode', '?')})", item)

    def _on_select_recent(self) -> None:
        item = self.cmb_recent.currentData()
        if not item:
            return
        self.edt_exe.setText(item.get("exe", ""))
        self.edt_args.setText(item.get("args", ""))
        self.edt_prefix.setText(item.get("prefix", ""))
        idx = 0 if item.get("mode") == "proton" else 1
        self.cmb_launch_mode.setCurrentIndex(idx)

    def _refresh_steam_games(self) -> None:
        self._steam_games = discover_steam_games()
        self.tbl_steam.setRowCount(len(self._steam_games))
        for row, g in enumerate(self._steam_games):
            self.tbl_steam.setItem(row, 0, QTableWidgetItem(g.name))
            self.tbl_steam.setItem(row, 1, QTableWidgetItem(g.appid))
            self.tbl_steam.setItem(row, 2, QTableWidgetItem(str(g.install_dir)))

    def _on_open_steam_folder(self) -> None:
        row = self.tbl_steam.currentRow()
        if row < 0 or row >= len(self._steam_games):
            return
        folder = self._steam_games[row].install_dir
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(folder)], start_new_session=True)

    def _on_copy_steam_cmd(self) -> None:
        QApplication.clipboard().setText("dlss5-run %command%")
        self.statusBar().showMessage("Copiado: dlss5-run %command%", 3000)

    def _on_copy_vk_cmd(self) -> None:
        QApplication.clipboard().setText("VKLayer_DLSS5=1 %command%")
        self.statusBar().showMessage("Copiado: VKLayer_DLSS5=1 %command%", 3000)

    def _on_send_steam_to_presr(self) -> None:
        row = self.tbl_steam.currentRow()
        if row < 0 or row >= len(self._steam_games):
            QMessageBox.information(self, "Selecione um jogo", "Clique em um jogo Steam na tabela primeiro.")
            return
        gdir = self._steam_games[row].install_dir
        self.edt_presr_dir.setText(str(gdir))
        self.tabs.setCurrentIndex(2)

    # -------------------------------------------------------------- Pre-SR actions
    def _on_browse_presr_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pasta do executável real do jogo", str(Path.home()))
        if d:
            self.edt_presr_dir.setText(d)

    def _on_browse_presr_exe(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Selecionar .exe do jogo", str(Path.home()), "Windows EXE (*.exe)")
        if f:
            self._last_presr_exe = Path(f)
            self.edt_presr_dir.setText(str(Path(f).parent))

    def _refresh_presr_status(self) -> None:
        txt = self.edt_presr_dir.text().strip()
        if not txt or not Path(txt).is_dir():
            self.lbl_presr_status.setText("Selecione uma pasta válida onde está o .exe real do jogo.")
            self.edt_presr_steam.setText("")
            return
        st = get_presr_status(Path(txt))
        self.edt_presr_steam.setText(st.steam_launch_option)
        if st.installed:
            self.lbl_presr_status.setText(
                f"Instalado (Hook: {st.hook_dll}, API: {st.api_mode}, DLL Neural: {'OK' if st.has_neural_dll else 'Ausente'})"
            )
            self.lbl_presr_status.setStyleSheet("color: #9cf028;")
            k = st.dlssnr_keys
            self.chk_presr_enabled.setChecked(k.get("Enabled", "true").lower() == "true")
            self.chk_presr_before_sr.setChecked(k.get("RunBeforeSR", "true").lower() == "true")
            self.chk_presr_spatial.setChecked(k.get("SpatialCompression", "false").lower() == "true")
            self.chk_presr_rr.setChecked(k.get("ResidualAcrossRR", "false").lower() == "true")

            def _set_str_combo(combo: QComboBox, val: str) -> None:
                for i in range(combo.count()):
                    if combo.itemData(i) == val:
                        combo.setCurrentIndex(i)
                        return

            _set_str_combo(self.cmb_presr_model, k.get("Preset", "2") if k.get("Preset") != "auto" else "0")
            _set_str_combo(self.cmb_presr_style, k.get("Style", "1") if k.get("Style") != "auto" else "1")

            def _f(key: str, default: float) -> float:
                v = k.get(key, "auto")
                try:
                    return default if v == "auto" else float(v)
                except ValueError:
                    return default

            self.sld_presr_scale.set_value(_f("WorkingScale", 1.0))
            self.sld_presr_intensity.set_value(_f("Intensity", 0.80))
            self.sld_presr_structure.set_value(_f("LocalStructure", 0.95))
            self.sld_presr_tone.set_value(_f("LocalTone", 0.90))
            self.sld_presr_transfer.set_value(_f("TransferStrength", 0.85))
            self.sld_presr_colour.set_value(_f("ColourStrength", 0.90))
        else:
            self.lbl_presr_status.setText("Não instalado nesta pasta.")
            self.lbl_presr_status.setStyleSheet("color: #f2c36b;")

    def _on_install_presr(self) -> None:
        gdir = Path(self.edt_presr_dir.text().strip())
        if not gdir.is_dir():
            QMessageBox.warning(self, "Pasta inválida", "Escolha a pasta onde está o .exe do jogo.")
            return
        hook = self.cmb_presr_hook.currentData()
        api = self.cmb_presr_api.currentData()
        prof_name = self.cmb_presr_profile.currentData()
        profile = PRESR_PROFILES.get(prof_name)
        exe_name = getattr(self, "_last_presr_exe", None)
        exe_filter = exe_name.name if exe_name and exe_name.parent == gdir else "auto"

        def task(log: Callable[[str], None]) -> str:
            st = install_presr_in_game(
                gdir, hook_dll=hook, api_mode=api, exe_name=exe_filter, profile=profile, log=log
            )
            return f"OptiScaler-NR instalado em {st.game_dir}\nSteam: {st.steam_launch_option}"

        def done(msg: str) -> None:
            self._on_worker_ok(msg)
            self._refresh_presr_status()

        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = WorkerThread(task)
        self._worker.log_line.connect(lambda l: self.txt_log.appendPlainText(l))
        self._worker.finished_ok.connect(done)
        self._worker.finished_err.connect(self._on_worker_err)
        self._worker.start()

    def _on_remove_presr(self) -> None:
        gdir = Path(self.edt_presr_dir.text().strip())
        try:
            uninstall_presr_from_game(gdir, log=lambda m: self.txt_log.appendPlainText(m))
            self._refresh_presr_status()
            QMessageBox.information(self, "Removido", f"OptiScaler-NR removido de:\n{gdir}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def _on_copy_presr_steam(self) -> None:
        txt = self.edt_presr_steam.text().strip()
        if txt:
            QApplication.clipboard().setText(txt)
            self.statusBar().showMessage(f"Copiado: {txt}", 3000)

    def _on_apply_presr_live_ini(self) -> None:
        gdir = Path(self.edt_presr_dir.text().strip())
        updates = {
            "Enabled": "true" if self.chk_presr_enabled.isChecked() else "false",
            "RunBeforeSR": "true" if self.chk_presr_before_sr.isChecked() else "false",
            "SpatialCompression": "true" if self.chk_presr_spatial.isChecked() else "false",
            "ResidualAcrossRR": "true" if self.chk_presr_rr.isChecked() else "false",
            "Preset": str(self.cmb_presr_model.currentData()),
            "Style": str(self.cmb_presr_style.currentData()),
            "WorkingScale": f"{self.sld_presr_scale.value():.2f}",
            "Intensity": f"{self.sld_presr_intensity.value():.2f}",
            "LocalStructure": f"{self.sld_presr_structure.value():.2f}",
            "LocalTone": f"{self.sld_presr_tone.value():.2f}",
            "TransferStrength": f"{self.sld_presr_transfer.value():.2f}",
            "ColourStrength": f"{self.sld_presr_colour.value():.2f}",
        }
        try:
            ini_path = update_dlssnr_ini(gdir, updates)
            self.statusBar().showMessage(f"OptiScaler.ini atualizado ao vivo ({ini_path})", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def _on_launch_presr_exe(self) -> None:
        gdir = Path(self.edt_presr_dir.text().strip())
        if not gdir.is_dir():
            return
        exe = getattr(self, "_last_presr_exe", None)
        if not exe or exe.parent != gdir or not exe.is_file():
            f, _ = QFileDialog.getOpenFileName(self, "Selecionar .exe do jogo", str(gdir), "Windows EXE (*.exe)")
            if not f:
                return
            exe = Path(f)
            self._last_presr_exe = exe
        st = get_presr_status(gdir)
        try:
            proc = launch_proton_presr_game(exe, st.hook_dll)
            self.txt_log.appendPlainText(f"Jogo Pre-SR iniciado (PID {proc.pid}) com hook {st.hook_dll}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    # -------------------------------------------------------------- polling
    def _refresh_diagnostics(self) -> None:
        st = get_backend_status()
        self.lbl_gpu.setText(f"{st.gpu_name} (Driver {st.driver_version or '?'})")
        layer_txt = (
            f"OK (64-bit: {'Sim' if st.layer64_ok else 'Não'}, "
            f"32-bit: {'Sim' if st.layer32_ok else 'Não'}, "
            f"Manifests: {'Sim' if st.manifest64_ok and st.manifest32_ok else 'Parcial'})"
            if st.vklayer_installed
            else "Não instalada — clique em 'Instalar/Atualizar DLSS5VKLayer'"
        )
        self.lbl_layer.setText(layer_txt)
        if st.neural_dll_ok:
            self.lbl_dll.setText(
                f"OK — {st.neural_dll_path} ({st.neural_dll_size_mb:.1f} MiB, sha256:{st.neural_dll_sha256_prefix}...)"
            )
        else:
            self.lbl_dll.setText("Ausente — clique em 'Baixar nvngx_dlssnr.dll 310.9.16'")

        if st.ptrace_scope == 0:
            self.lbl_dmabuf.setText("Zero-copy VRAM ativo (ptrace_scope=0)")
        else:
            self.lbl_dmabuf.setText(
                f"ptrace_scope={st.ptrace_scope} (fallback automático via SHM/host-copy; funciona normalmente)"
            )

        # Sync runner combo to configured runner
        if st.configured_runner_path:
            for i in range(self.cmb_helper_runner.count()):
                r = self.cmb_helper_runner.itemData(i)
                if r and r.path == st.configured_runner_path:
                    self.cmb_helper_runner.setCurrentIndex(i)
                    break

        self.txt_log.setPlainText(read_helper_log(140))

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value: int) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.blockSignals(True)
                combo.setCurrentIndex(i)
                combo.blockSignals(False)
                return

    def _poll_shm(self) -> None:
        snap: ShmSnapshot | None = self.shm.read_snapshot()
        if snap is None or not snap.initialised:
            self.lbl_helper_pill.setText("Helper: Parado")
            self.lbl_helper_pill.setStyleSheet(
                "background: #2b2226; color: #f08a8a; padding: 4px 10px; border-radius: 10px; border: none;"
            )
            self.lbl_model_pill.setText("Modelo: --")
            self.lbl_game_pill.setText("Jogo: nenhum anexado")
            self.lbl_telemetry.setText(
                "Telemetria: inicie o filtro e abra qualquer jogo Vulkan ou Proton com VKLayer_DLSS5=1 / dlss5-run."
            )
            return

        if snap.helper_state == 4:
            self.lbl_helper_pill.setText("Helper: Rodando")
            self.lbl_helper_pill.setStyleSheet(
                "background: #1d3315; color: #9cf028; padding: 4px 10px; border-radius: 10px; border: none;"
            )
        else:
            self.lbl_helper_pill.setText(f"Helper: {snap.helper_state_label}")
            self.lbl_helper_pill.setStyleSheet(
                "background: #332919; color: #f2c36b; padding: 4px 10px; border-radius: 10px; border: none;"
            )

        self.lbl_model_pill.setText(f"Modelo: {'Ativo' if snap.model_up else 'Aguardando frame'}")
        if snap.layer_alive:
            gname = snap.game_name or f"PID {snap.layer_pid}"
            res = f"{snap.layer_width}x{snap.layer_height}" if snap.layer_width else "--"
            self.lbl_game_pill.setText(f"Jogo: {gname} ({res})")
            self.lbl_game_pill.setStyleSheet(
                "background: #182f3a; color: #7ad7ff; padding: 4px 10px; border-radius: 10px; border: none;"
            )
        else:
            self.lbl_game_pill.setText("Jogo: aguardando conexão Vulkan/Proton")
            self.lbl_game_pill.setStyleSheet(
                "background: #232c38; color: #9fb0c3; padding: 4px 10px; border-radius: 10px; border: none;"
            )

        hdr_str = HDR_KINDS.get(snap.hdr_detected, "?")
        px_str = PROXY_FORMATS.get(snap.proxy_format, "?")
        dma_str = "dma-buf" if snap.dmabuf_active else "SHM"
        self.lbl_telemetry.setText(
            f"Frames: {snap.helper_frames} | Layer: {snap.layer_ms:.2f} ms | "
            f"Eval: {snap.helper_eval_ms:.2f} ms | VRAM: {snap.helper_vram_mb} MB | "
            f"Saída: {hdr_str} -> {px_str} ({dma_str})"
        )

        # Sync controls with live SHM values unless user is dragging a slider
        self._syncing_ui = True
        try:
            s = snap.settings
            for w, val in [
                (self.chk_enabled, bool(s.get("enabled", 1))),
                (self.chk_ab, bool(s.get("applyModel", 1))),
                (self.chk_hold, bool(s.get("holdFrame", 0))),
                (self.chk_automask, bool(s.get("autoMask", 1))),
                (self.chk_bypass, bool(s.get("compositionBypass", 1))),
                (self.chk_sdr16, bool(s.get("sdr16Multipass", 0))),
                (self.chk_mvec, bool(s.get("mvecEnabled", 1))),
                (self.chk_compare_swap, bool(s.get("compareSwap", 0))),
            ]:
                w.blockSignals(True)
                w.setChecked(val)
                w.blockSignals(False)

            self.spn_passes.blockSignals(True)
            self.spn_passes.setValue(int(s.get("passes", 1)))
            self.spn_passes.blockSignals(False)

            self._set_combo_by_data(self.cmb_preset, int(s.get("preset", 0)))
            self._set_combo_by_data(self.cmb_style, int(s.get("style", 0)))
            self._set_combo_by_data(self.cmb_transfer, int(s.get("transfer", 1)))
            self._set_combo_by_data(self.cmb_reversible, int(s.get("reversibleMode", 0)))
            self._set_combo_by_data(self.cmb_downscaler, int(s.get("scalingDownscaler", 4)))
            self._set_combo_by_data(self.cmb_hdr, int(s.get("hdrMode", 0)))
            self._set_combo_by_data(self.cmb_colour_mode, int(s.get("colourMode", 0)))
            self._set_combo_by_data(self.cmb_mvec_q, int(s.get("mvecQuality", 1)))
            self._set_combo_by_data(self.cmb_mvec_px, int(s.get("mvecPixelSize", 2)))
            self._set_combo_by_data(self.cmb_mvec_units, int(s.get("mvecScaleMode", 1)))
            self._set_combo_by_data(self.cmb_compare, int(s.get("compareMode", 0)))

            for row, key in [
                (self.sld_intensity, "intensity"),
                (self.sld_tone, "localTone"),
                (self.sld_structure, "localStructure"),
                (self.sld_skin, "skinStructure"),
                (self.sld_sharpness, "sharpness"),
                (self.sld_transfer_str, "transferStrength"),
                (self.sld_colour_str, "colourStrength"),
                (self.sld_guard, "maxRatio"),
                (self.sld_work_scale, "workingScale"),
                (self.sld_split, "compareSplit"),
            ]:
                if not row.slider.isSliderDown():
                    row.set_value(float(s.get(key, 1.0)))
        finally:
            self._syncing_ui = False

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            if self._gui_pid_file.exists() and self._gui_pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                self._gui_pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        self.shm.close()
        super().closeEvent(event)


def main() -> int:
    lock_dir = Path.home() / ".cache" / "dlssnr"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = (lock_dir / "prysm-gui.lock").open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            subprocess.run(["hyprctl", "dispatch", "focuswindow", "title:^Prysm"], capture_output=True)
        return 0
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    app = QApplication(sys.argv)
    app.setApplicationName("Prysm")
    app.setStyleSheet(DARK_QSS)
    win = DLSS5FilterWindow()
    win.show()
    try:
        return app.exec()
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
