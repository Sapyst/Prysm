"""In-game floating Wayland overlay (GTK4 + Gtk4LayerShell) for DLSS 5 Filter."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

# libgtk4-layer-shell must be loaded before libwayland-client / GTK4 initializes.
ctypes.CDLL("libgtk4-layer-shell.so")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell  # noqa: E402

from dlss5_filter.backend_manager import read_dlssnr_config, run_helper_cmd  # noqa: E402
from dlss5_filter.presets import BUILTIN_PRESETS  # noqa: E402
from dlss5_filter.shm_bridge import ShmBridge  # noqa: E402

HOTKEY_CHOICES = {
    "Prior": "Page Up",
    "Delete": "Delete",
}

OVERLAY_CSS = b"""
window.dlss5-overlay {
    background-color: rgba(0, 0, 0, 0.18);
}
.overlay-card {
    background-color: rgba(14, 18, 24, 0.95);
    border: 1px solid rgba(140, 224, 0, 0.55);
    border-radius: 14px;
    padding: 14px;
    color: #e8eef6;
    font-size: 13px;
}
.overlay-title {
    color: #8ce000;
    font-weight: 800;
    font-size: 15px;
    letter-spacing: 0.4px;
}
.status-pill {
    background-color: #1f2935;
    border-radius: 9px;
    padding: 3px 9px;
    font-size: 11px;
    color: #b7c6d8;
}
.status-pill-ok {
    background-color: rgba(140, 224, 0, 0.16);
    color: #9ff01a;
    border: 1px solid rgba(140, 224, 0, 0.35);
}
.section-lbl {
    color: #8ea3b8;
    font-size: 11px;
    font-weight: 700;
    margin-top: 4px;
}
.tele-lbl {
    color: #9db0c4;
    font-size: 11px;
}
.seg-btn {
    background-color: #1c242f;
    color: #d6e0ec;
    border: 1px solid #2c3847;
    border-radius: 7px;
    padding: 4px 10px;
    min-height: 26px;
}
.seg-btn:hover {
    background-color: #263140;
}
.seg-btn:checked, .seg-active {
    background-color: rgba(140, 224, 0, 0.22);
    color: #b7ff3c;
    border: 1px solid #8ce000;
    font-weight: 700;
}
.close-btn {
    background-color: #202834;
    color: #cbd5e1;
    border: 1px solid #323f52;
    border-radius: 7px;
    padding: 2px 9px;
    min-height: 24px;
}
.danger-btn {
    background-color: rgba(200, 55, 55, 0.22);
    color: #ff9a9a;
    border: 1px solid rgba(220, 70, 70, 0.5);
    border-radius: 7px;
    padding: 4px 10px;
}
scale {
    padding: 4px 0;
}
scale trough {
    background-color: #25303d;
    min-height: 6px;
    border-radius: 3px;
}
scale highlight {
    background-color: #8ce000;
    border-radius: 3px;
}
scale slider {
    background-color: #e8eef6;
    margin: -4px 0;
    min-width: 14px;
    min-height: 14px;
    border-radius: 7px;
}
"""


def overlay_sock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return Path(runtime) / f"dlss5-overlay-{os.getuid()}.sock"


def overlay_cfg_path() -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dlss5-filter"
    cfg.mkdir(parents=True, exist_ok=True)
    return cfg / "overlay.json"


def load_overlay_cfg() -> dict[str, Any]:
    p = overlay_cfg_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"hotkey": "Prior"}


def save_overlay_cfg(data: dict[str, Any]) -> None:
    overlay_cfg_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _resolve_shm_bridge() -> ShmBridge:
    env_shm = os.environ.get("DLSSNR_SHM")
    if env_shm:
        return ShmBridge(Path(env_shm))
    cfg_shm = read_dlssnr_config().get("shm")
    if cfg_shm:
        return ShmBridge(Path(cfg_shm))
    return ShmBridge()


def _overlay_cli_path() -> str:
    local_bin = Path.home() / ".local/bin/dlss5-overlay"
    if local_bin.exists():
        return str(local_bin)
    return str(Path(__file__).resolve().parent.parent / "dlss5-overlay")


def _hypr_available() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def _hypr_bind(key: str) -> None:
    if not _hypr_available() or key not in HOTKEY_CHOICES:
        return
    cmd_str = f", {key}, exec, {_overlay_cli_path()} --toggle"
    subprocess.run(["hyprctl", "keyword", "bind", cmd_str], capture_output=True, text=True)


def _hypr_unbind(key: str) -> None:
    if not _hypr_available() or key not in HOTKEY_CHOICES:
        return
    subprocess.run(["hyprctl", "keyword", "unbind", f", {key}"], capture_output=True, text=True)


def send_overlay_command(cmd: str, timeout: float = 0.6) -> bool:
    sock_p = overlay_sock_path()
    if not sock_p.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock_p))
            s.sendall(cmd.encode("utf-8"))
        return True
    except OSError:
        return False


class OverlayWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, hotkey: str, start_visible: bool) -> None:
        super().__init__(application=app, title="DLSS 5 Overlay")
        self.add_css_class("dlss5-overlay")
        self.set_resizable(False)
        self.set_default_size(400, -1)

        self.shm = _resolve_shm_bridge()
        self.hotkey = hotkey if hotkey in HOTKEY_CHOICES else "Prior"
        self._syncing = False
        self._sliders: dict[str, tuple[Gtk.Scale, Gtk.Label, str]] = {}
        self._pass_buttons: dict[int, Gtk.ToggleButton] = {}
        self._hotkey_buttons: dict[str, Gtk.ToggleButton] = {}

        self._setup_layer_shell()
        self._build_ui()
        self._setup_escape_key()

        self._apply_visible(start_visible)
        GLib.timeout_add(450, self._on_tick)

    def _setup_layer_shell(self) -> None:
        Gtk4LayerShell.init_for_window(self)
        Gtk4LayerShell.set_namespace(self, "dlss5-overlay")
        Gtk4LayerShell.set_layer(self, Gtk4LayerShell.Layer.OVERLAY)
        for edge in (
            Gtk4LayerShell.Edge.TOP,
            Gtk4LayerShell.Edge.BOTTOM,
            Gtk4LayerShell.Edge.LEFT,
            Gtk4LayerShell.Edge.RIGHT,
        ):
            Gtk4LayerShell.set_anchor(self, edge, True)
        Gtk4LayerShell.set_exclusive_zone(self, -1)

    def _setup_escape_key(self) -> None:
        ctrl = Gtk.EventControllerKey()
        ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(ctrl)

    def _on_key_pressed(self, _ctrl: Gtk.EventControllerKey, keyval: int, _kc: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._apply_visible(False)
            return True
        return False

    def _apply_visible(self, visible: bool) -> None:
        if visible:
            self._sync_from_shm()
            Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            self.set_visible(True)
            self.present()
        else:
            Gtk4LayerShell.set_keyboard_mode(self, Gtk4LayerShell.KeyboardMode.NONE)
            self.set_visible(False)

    def toggle_visible(self) -> None:
        self._apply_visible(not self.get_visible())

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("overlay-card")
        card.set_size_request(390, -1)
        card.set_halign(Gtk.Align.START)
        card.set_valign(Gtk.Align.START)
        card.set_margin_top(22)
        card.set_margin_start(22)
        self.set_child(card)

        # Header
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_p = Path(__file__).resolve().parent.parent / "assets" / "prysm.png"
        if icon_p.is_file():
            img = Gtk.Image.new_from_file(str(icon_p))
            img.set_pixel_size(22)
            hdr.append(img)
        title = Gtk.Label(label="PRYSM OVERLAY")
        title.add_css_class("overlay-title")
        title.set_xalign(0.0)
        title.set_hexpand(True)
        hdr.append(title)

        self.lbl_status = Gtk.Label(label="--")
        self.lbl_status.add_css_class("status-pill")
        hdr.append(self.lbl_status)

        btn_hide = Gtk.Button(label="✕")
        btn_hide.add_css_class("close-btn")
        btn_hide.set_tooltip_text("Ocultar overlay (Esc ou atalho)")
        btn_hide.connect("clicked", lambda *_: self._apply_visible(False))
        hdr.append(btn_hide)
        card.append(hdr)

        self.lbl_tele = Gtk.Label(label="Aguardando jogo Vulkan/Proton...")
        self.lbl_tele.add_css_class("tele-lbl")
        self.lbl_tele.set_xalign(0.0)
        card.append(self.lbl_tele)

        # Master switches
        sw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.sw_enabled = Gtk.Switch()
        self.sw_enabled.connect("notify::active", self._on_master_toggled)
        lbl_en = Gtk.Label(label="Filtro Ativo")
        sw_box1 = Gtk.Box(spacing=6)
        sw_box1.append(self.sw_enabled)
        sw_box1.append(lbl_en)
        sw_row.append(sw_box1)

        self.sw_wipe = Gtk.Switch()
        self.sw_wipe.connect("notify::active", self._on_wipe_toggled)
        lbl_wp = Gtk.Label(label="Cortina A/B (50/50)")
        sw_box2 = Gtk.Box(spacing=6)
        sw_box2.append(self.sw_wipe)
        sw_box2.append(lbl_wp)
        sw_row.append(sw_box2)
        card.append(sw_row)

        # Quick passes row (1x, 2x, 3x, 5x)
        lbl_p = Gtk.Label(label="PASSAGENS NEURAIS")
        lbl_p.add_css_class("section-lbl")
        lbl_p.set_xalign(0.0)
        card.append(lbl_p)

        pass_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for p in (1, 2, 3, 5):
            btn = Gtk.ToggleButton(label=f"{p}x")
            btn.add_css_class("seg-btn")
            btn.set_hexpand(True)
            btn.connect("toggled", self._on_pass_clicked, p)
            self._pass_buttons[p] = btn
            pass_row.append(btn)
        card.append(pass_row)

        # Preset selector
        lbl_pr = Gtk.Label(label="PRESET RÁPIDO")
        lbl_pr.add_css_class("section-lbl")
        lbl_pr.set_xalign(0.0)
        card.append(lbl_pr)

        self._preset_names = list(BUILTIN_PRESETS.keys())
        self.dd_preset = Gtk.DropDown.new_from_strings(self._preset_names)
        self.dd_preset.connect("notify::selected", self._on_preset_selected)
        card.append(self.dd_preset)

        # Live sliders
        lbl_sl = Gtk.Label(label="AJUSTES AO VIVO")
        lbl_sl.add_css_class("section-lbl")
        lbl_sl.set_xalign(0.0)
        card.append(lbl_sl)

        self._add_slider(card, "intensity", "Intensidade", 0.0, 1.5, 0.05, "{:.2f}", 0.80)
        self._add_slider(card, "workingScale", "Escala Neural", 0.50, 1.00, 0.05, "{:.2f}x", 0.70)
        self._add_slider(card, "sharpness", "Nitidez", 0.0, 1.0, 0.05, "{:.2f}", 0.15)
        self._add_slider(card, "localStructure", "Estrutura Local", 0.5, 1.5, 0.05, "{:.2f}", 0.95)
        self._add_slider(card, "localTone", "Tom Local", 0.5, 1.5, 0.05, "{:.2f}", 0.90)
        self._add_slider(card, "compareSplit", "Divisão Cortina", 0.05, 0.95, 0.02, "{:.0%}", 0.50)

        # Model & style dropdowns
        opt_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.dd_model = Gtk.DropDown.new_from_strings(["Auto", "Original", "FP8 Otimizado"])
        self.dd_model.set_hexpand(True)
        self.dd_model.connect("notify::selected", lambda dd, *_: self._write_shm({"preset": dd.get_selected()}))

        self.dd_style = Gtk.DropDown.new_from_strings(["Padrão", "Natural", "Cinematográfico"])
        self.dd_style.set_hexpand(True)
        self.dd_style.connect("notify::selected", lambda dd, *_: self._write_shm({"style": dd.get_selected()}))
        opt_row.append(self.dd_model)
        opt_row.append(self.dd_style)
        card.append(opt_row)

        # Footer: hotkey selector + helper/quit actions
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        card.append(sep)

        foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hk_lbl = Gtk.Label(label="Tecla:")
        hk_lbl.add_css_class("tele-lbl")
        foot.append(hk_lbl)

        for key_id, key_label in HOTKEY_CHOICES.items():
            hb = Gtk.ToggleButton(label=key_label)
            hb.add_css_class("seg-btn")
            hb.set_active(key_id == self.hotkey)
            hb.connect("toggled", self._on_hotkey_selected, key_id)
            self._hotkey_buttons[key_id] = hb
            foot.append(hb)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        foot.append(spacer)

        btn_quit = Gtk.Button(label="Encerrar")
        btn_quit.add_css_class("danger-btn")
        btn_quit.connect("clicked", lambda *_: self.get_application().quit())
        foot.append(btn_quit)
        card.append(foot)

    def _add_slider(
        self,
        parent: Gtk.Box,
        key: str,
        title: str,
        lo: float,
        hi: float,
        step: float,
        fmt: str,
        default: float,
    ) -> None:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        lbl = Gtk.Label(label=title)
        lbl.set_size_request(112, -1)
        lbl.set_xalign(0.0)

        adj = Gtk.Adjustment(value=default, lower=lo, upper=hi, step_increment=step, page_increment=step * 2)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        scale.set_draw_value(False)
        scale.set_hexpand(True)

        val_lbl = Gtk.Label(label=fmt.format(default))
        val_lbl.set_size_request(48, -1)
        val_lbl.set_xalign(1.0)

        debounce_id = [0]

        def on_val(s: Gtk.Scale) -> None:
            v = s.get_value()
            val_lbl.set_text(fmt.format(v))
            if self._syncing:
                return
            if key == "workingScale":
                if debounce_id[0]:
                    GLib.source_remove(debounce_id[0])
                def _commit(val: float = v) -> bool:
                    debounce_id[0] = 0
                    self._write_shm({key: val})
                    return False
                debounce_id[0] = GLib.timeout_add(350, _commit)
            else:
                self._write_shm({key: v})

        scale.connect("value-changed", on_val)
        row.append(lbl)
        row.append(scale)
        row.append(val_lbl)
        parent.append(row)
        self._sliders[key] = (scale, val_lbl, fmt)

    # ------------------------------------------------------------------ SHM sync
    def _write_shm(self, updates: dict[str, Any]) -> None:
        if self._syncing:
            return
        try:
            self.shm.apply_settings(updates)
        except Exception as exc:
            self.lbl_tele.set_text(f"Erro SHM: {exc}")

    def _sync_from_shm(self) -> None:
        snap = self.shm.read_snapshot()
        if not snap or not snap.initialised:
            return
        self._syncing = True
        try:
            st = snap.settings
            self.sw_enabled.set_active(bool(st.get("enabled", 1) and st.get("applyModel", 1)))
            self.sw_wipe.set_active(int(st.get("compareMode", 0)) != 0)
            cur_passes = int(st.get("passes", 1))
            for p, btn in self._pass_buttons.items():
                btn.set_active(p == cur_passes)
            for key, (scale, lbl, fmt) in self._sliders.items():
                if key in st:
                    val = float(st[key])
                    scale.set_value(val)
                    lbl.set_text(fmt.format(val))
            self.dd_model.set_selected(min(2, max(0, int(st.get("preset", 0)))))
            self.dd_style.set_selected(min(2, max(0, int(st.get("style", 0)))))
        finally:
            self._syncing = False

    def _on_tick(self) -> bool:
        if not self.get_visible():
            return True
        snap = self.shm.read_snapshot()
        if not snap or not snap.initialised:
            self.lbl_status.set_text("Helper Off")
            self.lbl_status.remove_css_class("status-pill-ok")
            self.lbl_tele.set_text("Inicie o jogo com dlss5-run ou inicie o helper.")
            return True

        if snap.helper_state == 4:
            self.lbl_status.set_text("Ativo")
            self.lbl_status.add_css_class("status-pill-ok")
        else:
            self.lbl_status.set_text(snap.helper_state_label)
            self.lbl_status.remove_css_class("status-pill-ok")

        res = f"{snap.width}x{snap.height}" if snap.width and snap.height else f"{snap.layer_width}x{snap.layer_height}"
        game = snap.game_name or ("Conectado" if snap.layer_alive else "Aguardando jogo")
        self.lbl_tele.set_text(
            f"{game} · {res} · layer {snap.layer_ms:.1f} ms · eval {snap.helper_eval_ms:.1f} ms · frames {snap.layer_frames}"
        )
        return True

    # ------------------------------------------------------------------ Actions
    def _on_master_toggled(self, sw: Gtk.Switch, _pspec: Any) -> None:
        if self._syncing:
            return
        on = 1 if sw.get_active() else 0
        self._write_shm({"enabled": on, "applyModel": on})

    def _on_wipe_toggled(self, sw: Gtk.Switch, _pspec: Any) -> None:
        if self._syncing:
            return
        self._write_shm({"compareMode": 2 if sw.get_active() else 0})

    def _on_pass_clicked(self, btn: Gtk.ToggleButton, passes: int) -> None:
        if self._syncing or not btn.get_active():
            return
        self._syncing = True
        try:
            for p, other in self._pass_buttons.items():
                if p != passes:
                    other.set_active(False)
        finally:
            self._syncing = False
        self._write_shm({"passes": passes, "sdr16Multipass": 1 if passes > 1 else 0})

    def _on_preset_selected(self, dd: Gtk.DropDown, _pspec: Any) -> None:
        if self._syncing:
            return
        idx = dd.get_selected()
        if 0 <= idx < len(self._preset_names):
            name = self._preset_names[idx]
            self.shm.apply_settings(BUILTIN_PRESETS[name])
            self._sync_from_shm()

    def _on_hotkey_selected(self, btn: Gtk.ToggleButton, key_id: str) -> None:
        if self._syncing or not btn.get_active():
            return
        old = self.hotkey
        if old == key_id:
            return
        self._syncing = True
        try:
            for k, other in self._hotkey_buttons.items():
                if k != key_id:
                    other.set_active(False)
        finally:
            self._syncing = False
        _hypr_unbind(old)
        _hypr_bind(key_id)
        self.hotkey = key_id
        cfg = load_overlay_cfg()
        cfg["hotkey"] = key_id
        save_overlay_cfg(cfg)


class OverlayApp(Gtk.Application):
    def __init__(self, hotkey: str, start_visible: bool, ensure_helper: bool) -> None:
        super().__init__(application_id="com.dlss5filter.Overlay")
        self.hotkey = hotkey
        self.start_visible = start_visible
        self.ensure_helper = ensure_helper
        self.win: OverlayWindow | None = None
        self.sock: socket.socket | None = None

    def do_activate(self) -> None:
        if self.win is not None:
            self.win.toggle_visible()
            return

        if self.ensure_helper:
            snap = _resolve_shm_bridge().read_snapshot()
            if not snap or snap.helper_state != 4:
                run_helper_cmd("start")

        provider = Gtk.CssProvider()
        provider.load_from_data(OVERLAY_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.win = OverlayWindow(self, hotkey=self.hotkey, start_visible=self.start_visible)
        self._start_ipc_socket()
        _hypr_bind(self.win.hotkey)

        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._on_sigusr1)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_term)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_term)

    def _on_sigusr1(self) -> bool:
        if self.win:
            self.win.toggle_visible()
        return True

    def _on_term(self) -> bool:
        self.quit()
        return False

    def _start_ipc_socket(self) -> None:
        sp = overlay_sock_path()
        sp.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sp))
        srv.listen(4)
        srv.setblocking(False)
        self.sock = srv
        GLib.io_add_watch(srv.fileno(), GLib.IO_IN, self._on_ipc_accept)

    def _on_ipc_accept(self, _fd: int, _cond: GLib.IOCondition) -> bool:
        if not self.sock:
            return False
        try:
            conn, _ = self.sock.accept()
            with conn:
                cmd = conn.recv(64).decode("utf-8", "ignore").strip()
            if cmd == "toggle" and self.win:
                self.win.toggle_visible()
            elif cmd == "show" and self.win:
                self.win._apply_visible(True)
            elif cmd == "hide" and self.win:
                self.win._apply_visible(False)
            elif cmd == "quit":
                self.quit()
        except OSError:
            pass
        return True

    def do_shutdown(self) -> None:
        if self.win:
            _hypr_unbind(self.win.hotkey)
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        overlay_sock_path().unlink(missing_ok=True)
        Gtk.Application.do_shutdown(self)


def main(argv: list[str] | None = None) -> int:
    cfg = load_overlay_cfg()
    default_key = cfg.get("hotkey") if cfg.get("hotkey") in HOTKEY_CHOICES else "Prior"

    ap = argparse.ArgumentParser(prog="dlss5-overlay", description="Overlay in-game do DLSS 5 Filter (Page Up / Delete)")
    ap.add_argument("--daemon", action="store_true", help="Iniciar oculto em segundo plano aguardando atalho")
    ap.add_argument("--show", action="store_true", help="Abrir já visível na tela")
    ap.add_argument("--toggle", action="store_true", help="Alternar visibilidade do overlay ativo (ou iniciar)")
    ap.add_argument("--quit", action="store_true", help="Encerrar overlay em execução")
    ap.add_argument("--key", choices=list(HOTKEY_CHOICES.keys()), default=default_key, help="Tecla de atalho (Prior=Page Up, Delete=Del)")
    ap.add_argument("--no-helper", action="store_true", help="Não tentar iniciar dlssnr-helper automaticamente")
    args = ap.parse_args(argv)

    if args.quit:
        sent = send_overlay_command("quit")
        if not sent:
            _hypr_unbind(args.key)
        return 0

    if args.toggle:
        if send_overlay_command("toggle"):
            return 0
        args.show = True

    if args.show and send_overlay_command("show"):
        return 0

    if args.key != cfg.get("hotkey"):
        cfg["hotkey"] = args.key
        save_overlay_cfg(cfg)

    start_visible = bool(args.show) or (not args.daemon and not args.toggle)
    app = OverlayApp(hotkey=args.key, start_visible=start_visible, ensure_helper=not args.no_helper)
    return app.run(None)


if __name__ == "__main__":
    raise SystemExit(main())
