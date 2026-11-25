"""Tests for ShmHeader binary compatibility, ShmBridge round-trips, presets, and backend discovery."""

from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dlss5_filter.backend_manager import discover_runners, get_backend_status
from dlss5_filter.optiscaler_manager import (
    get_presr_status,
    install_presr_in_game,
    uninstall_presr_from_game,
    update_dlssnr_ini,
)
from dlss5_filter.presets import BUILTIN_PRESETS, load_user_profile, save_user_profile
from dlss5_filter.shm_bridge import SHM_MAGIC, SHM_VERSION, ShmBridge, ShmHeader, bits_to_float, float_to_bits


class TestShmProtocolAndBackend(unittest.TestCase):
    def test_shm_header_binary_layout(self) -> None:
        self.assertEqual(ctypes.sizeof(ShmHeader), 1968)
        self.assertEqual(ShmHeader.enabled.offset, 44)
        self.assertEqual(ShmHeader.transferStrengthBits.offset, 88)
        self.assertEqual(ShmHeader.helperState.offset, 176)
        self.assertEqual(ShmHeader.pass_ctrl.offset, 780)
        self.assertEqual(ShmHeader.mvecEnabled.offset, 1860)

    def test_float_bit_conversion(self) -> None:
        for val in (-1.0, 0.0, 0.25, 1.0, 1.75):
            self.assertAlmostEqual(bits_to_float(float_to_bits(val)), val, places=6)

    def test_shm_bridge_roundtrip_and_tuning_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shm_file = Path(tmp) / "shm.bin"
            bridge = ShmBridge(shm_file)
            try:
                bridge.init_defaults()
                snap0 = bridge.read_snapshot()
                self.assertIsNotNone(snap0)
                assert snap0 is not None
                self.assertTrue(snap0.initialised)
                self.assertEqual(snap0.magic, SHM_MAGIC)
                self.assertEqual(snap0.version, SHM_VERSION)

                hdr = bridge._ensure_mapped(create=False)
                assert hdr is not None
                c0 = int(hdr.controlSeq)
                t0 = int(hdr.tuningSeq)

                # Evaluate-time setting (sharpness) should bump controlSeq only, NOT tuningSeq.
                bridge.apply_settings({"sharpness": 0.42})
                self.assertEqual(int(hdr.controlSeq), c0 + 1)
                self.assertEqual(int(hdr.tuningSeq), t0)

                # Create-time setting (style) should bump both controlSeq and tuningSeq.
                bridge.apply_settings({"style": 2, "intensity": 1.25})
                self.assertEqual(int(hdr.controlSeq), c0 + 2)
                self.assertEqual(int(hdr.tuningSeq), t0 + 1)

                snap1 = bridge.read_snapshot()
                assert snap1 is not None
                self.assertEqual(snap1.settings["style"], 2)
                self.assertAlmostEqual(snap1.settings["sharpness"], 0.42, places=5)
                self.assertAlmostEqual(snap1.settings["intensity"], 1.25, places=5)
            finally:
                bridge.close()

    def test_presets_all_apply_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bridge = ShmBridge(Path(tmp) / "shm.bin")
            try:
                bridge.init_defaults()
                for name, values in BUILTIN_PRESETS.items():
                    bridge.apply_settings(values)
                    snap = bridge.read_snapshot()
                    assert snap is not None
                    for k, v in values.items():
                        if isinstance(v, float):
                            self.assertAlmostEqual(float(snap.settings[k]), v, places=5, msg=name)
                        else:
                            self.assertEqual(int(snap.settings[k]), int(v), msg=name)
            finally:
                bridge.close()

    def test_backend_status_and_runners(self) -> None:
        runners = discover_runners()
        self.assertGreater(len(runners), 0)
        st = get_backend_status()
        self.assertTrue(st.vklayer_installed)
        self.assertTrue(st.neural_dll_ok)

    def test_presr_install_live_update_and_uninstall(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            game_dir = Path(tmp)
            (game_dir / "game.exe").write_bytes(b"MZ")
            st = install_presr_in_game(game_dir, hook_dll="winmm.dll", api_mode="dx12", log=lambda _m: None)
            self.assertTrue(st.installed)
            self.assertTrue(st.has_neural_dll)
            self.assertTrue((game_dir / "winmm.dll").is_file())
            self.assertEqual(st.dlssnr_keys.get("RunBeforeSR"), "true")
            self.assertEqual(st.dlssnr_keys.get("SpatialCompression"), "true")
            self.assertEqual(st.dlssnr_keys.get("Preset"), "2")

            update_dlssnr_ini(game_dir, {"Intensity": "0.65", "WorkingScale": "0.80"})
            st2 = get_presr_status(game_dir)
            self.assertEqual(st2.dlssnr_keys.get("Intensity"), "0.65")
            self.assertEqual(st2.dlssnr_keys.get("WorkingScale"), "0.80")

            uninstall_presr_from_game(game_dir, log=lambda _m: None)
            self.assertFalse((game_dir / "winmm.dll").exists())
            self.assertFalse((game_dir / "OptiScaler.ini").exists())
            self.assertFalse((game_dir / "nvngx_dlssnr.dll").exists())
            self.assertTrue((game_dir / "game.exe").is_file())


if __name__ == "__main__":
    unittest.main()
