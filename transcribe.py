import argparse
import ctypes
import ctypes.wintypes
import json
import logging
import os
import tempfile
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

from foundry_local_sdk import Configuration, FoundryLocalManager


SAMPLE_RATE = 16000
CHANNELS = 1
MIN_HOLD_MS = 120

SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_PATH = SCRIPT_DIR / "transcription_history.jsonl"
APP_LOG_PATH = SCRIPT_DIR / "transcribe.log"
CONFIG_PATH = SCRIPT_DIR / "transcribe_config.json"

VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LWIN = 0x5B
VK_RWIN = 0x5C

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT_MSG = 0x0012

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.wintypes.LPARAM,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
)


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    ctypes.wintypes.HINSTANCE,
    ctypes.wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p

user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.wintypes.LPARAM

user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(ctypes.wintypes.MSG),
    ctypes.wintypes.HWND,
    ctypes.wintypes.UINT,
    ctypes.wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
user32.TranslateMessage.restype = ctypes.wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG)]
user32.DispatchMessageW.restype = ctypes.wintypes.LPARAM

user32.PostThreadMessageW.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL

user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
user32.OpenClipboard.restype = ctypes.wintypes.BOOL

user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = ctypes.wintypes.BOOL

user32.SetClipboardData.argtypes = [ctypes.wintypes.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p

user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = ctypes.wintypes.BOOL

user32.keybd_event.argtypes = [
    ctypes.wintypes.BYTE,
    ctypes.wintypes.BYTE,
    ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.c_ulong),
]
user32.keybd_event.restype = None

kernel32.GlobalAlloc.argtypes = [ctypes.wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p

kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p

kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )


def append_transcript_log(ts: str, text: str) -> None:
    try:
        with open(APP_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{ts}\t{text}\n")
    except OSError:
        logging.exception("Failed writing transcript log")


def create_mic_icon(recording: bool = False) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (220, 50, 50, 255) if recording else (55, 130, 240, 255)

    draw.rounded_rectangle((22, 10, 42, 34), radius=10, fill=color)
    draw.rounded_rectangle((29, 30, 35, 48), radius=3, fill=color)
    draw.rounded_rectangle((20, 46, 44, 52), radius=2, fill=color)
    return img


def set_clipboard_text(text: str) -> None:
    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        if not user32.EmptyClipboard():
            raise OSError("EmptyClipboard failed")

        data = text.encode("utf-16-le") + b"\x00\x00"
        h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h_global:
            raise OSError("GlobalAlloc failed")

        p_global = kernel32.GlobalLock(h_global)
        if not p_global:
            raise OSError("GlobalLock failed")
        try:
            ctypes.memmove(p_global, data, len(data))
        finally:
            kernel32.GlobalUnlock(h_global)

        if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
            raise OSError("SetClipboardData failed")
        h_global = None
    finally:
        user32.CloseClipboard()


def simulate_ctrl_v() -> None:
    vk_ctrl = 0x11
    vk_v = 0x56

    time.sleep(0.08)
    user32.keybd_event(vk_ctrl, 0, 0, None)
    user32.keybd_event(vk_v, 0, 0, None)
    time.sleep(0.03)
    user32.keybd_event(vk_v, 0, KEYEVENTF_KEYUP, None)
    user32.keybd_event(vk_ctrl, 0, KEYEVENTF_KEYUP, None)


def beep_async(freq: int, duration_ms: int) -> None:
    def _beep() -> None:
        try:
            kernel32.Beep(freq, duration_ms)
        except OSError:
            pass

    threading.Thread(target=_beep, daemon=True).start()


class FoundryTranscribeTrayApp:
    def __init__(
        self,
        model_name: str = "whisper-tiny",
        auto_paste: bool = True,
        mic_index: int | None = None,
        force_select_mic: bool = False,
    ):
        self.model_name = model_name
        self.auto_paste = auto_paste

        self.recording = False
        self.audio_chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

        self._ctrl_held = False
        self._win_held = False
        self._both_held_since = 0.0
        self._hook_enabled = True

        self._hook_thread_id: int | None = None
        self._hook_callback_ref: Any = None
        self._tray: Any = None

        self._lock = threading.Lock()
        self._audio_client = None
        self._speech_model = None
        self._manager = None
        self._model_switch_in_progress = False
        self._available_models: list[str] = []

        self._config = self._load_config()
        self._input_device: int | None = None
        self._initial_mic_override = mic_index
        self._force_select_mic = force_select_mic
        self._initial_model_override = model_name
        self.model_name = "whisper-tiny"

    def _resolve_model_name(self) -> None:
        if self._initial_model_override is not None:
            self.model_name = self._initial_model_override
            logging.info("Using CLI model override: %s", self.model_name)
            return

        configured = self._config.get("model_name")
        if isinstance(configured, str) and configured.strip():
            self.model_name = configured.strip()
            logging.info("Using configured model: %s", self.model_name)

    def _discover_whisper_models(self) -> list[str]:
        if self._manager is None:
            return [self.model_name]

        candidates = {
            "whisper-tiny",
            "whisper-base",
            "whisper-small",
            "whisper-medium",
            "whisper-large",
            self.model_name,
        }

        catalog = self._manager.catalog
        for attr_name in ("models", "model_ids"):
            raw = getattr(catalog, attr_name, None)
            if isinstance(raw, dict):
                for key in raw.keys():
                    if isinstance(key, str) and "whisper" in key.lower():
                        candidates.add(key)
            elif isinstance(raw, (list, tuple, set)):
                for value in raw:
                    if isinstance(value, str) and "whisper" in value.lower():
                        candidates.add(value)

        model_ids: list[str] = []
        for candidate in sorted(candidates):
            try:
                catalog.get_model(candidate)
                model_ids.append(candidate)
            except (KeyError, RuntimeError, ValueError, TypeError):
                continue

        return model_ids or [self.model_name]

    @staticmethod
    def _load_config() -> dict:
        if not CONFIG_PATH.exists():
            return {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            logging.exception("Failed to read config file")
            return {}

    def _save_config(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._config, fh, indent=2)
        except OSError:
            logging.exception("Failed to write config file")

    @staticmethod
    def _list_input_devices() -> list[tuple[int, str, int]]:
        devices = sd.query_devices()
        inputs: list[tuple[int, str, int]] = []
        for idx, dev in enumerate(devices):
            max_input = int(dev.get("max_input_channels", 0))
            if max_input > 0:
                name = str(dev.get("name", f"Input {idx}"))
                inputs.append((idx, name, max_input))
        return inputs

    def _is_valid_input_device(self, index: int) -> bool:
        return any(idx == index for idx, _name, _channels in self._list_input_devices())

    def _prompt_for_mic(self) -> int:
        inputs = self._list_input_devices()
        if not inputs:
            raise RuntimeError("No input microphones were found")

        print("\nSelect microphone for Foundry Transcribe:\n")
        for idx, name, max_channels in inputs:
            print(f"  [{idx}] {name} (max input channels: {max_channels})")

        while True:
            choice = input("\nEnter microphone index: ").strip()
            if not choice:
                print("Please enter a numeric device index.")
                continue
            try:
                selected = int(choice)
            except ValueError:
                print("Invalid value. Enter one of the listed indices.")
                continue

            if any(idx == selected for idx, _name, _channels in inputs):
                return selected

            print("Unknown index. Enter one of the listed indices.")

    def _resolve_input_device(self) -> None:
        selected_device: int | None = None
        persist_selection = False

        if self._initial_mic_override is not None:
            if not self._is_valid_input_device(self._initial_mic_override):
                raise ValueError(
                    f"Mic index {self._initial_mic_override} is not a valid input device"
                )
            selected_device = self._initial_mic_override
            logging.info("Using CLI mic override: %s", selected_device)

        if self._force_select_mic:
            selected_device = self._prompt_for_mic()
            persist_selection = True
            logging.info("Using selected mic from --select-mic: %s", selected_device)

        if selected_device is None:
            configured = self._config.get("mic_index")
            if isinstance(configured, int) and self._is_valid_input_device(configured):
                selected_device = configured
                logging.info("Using configured mic: %s", selected_device)
            else:
                selected_device = self._prompt_for_mic()
                persist_selection = True
                logging.info("Using newly selected mic: %s", selected_device)

        self._input_device = selected_device
        if persist_selection:
            self._config["mic_index"] = selected_device
            self._save_config()

    def _initialize_foundry(self) -> None:
        logging.info("Initializing Foundry Local SDK")
        config = Configuration(app_name="foundry_local_whisper_tray")
        FoundryLocalManager.initialize(config)
        self._manager = FoundryLocalManager.instance

        current_ep = ""

        def ep_progress(ep_name: str, percent: float) -> None:
            nonlocal current_ep
            if ep_name != current_ep:
                current_ep = ep_name
            logging.info("EP %s %.1f%%", ep_name, percent)

        self._manager.download_and_register_eps(progress_callback=ep_progress)
        self._available_models = self._discover_whisper_models()
        if self.model_name not in self._available_models and self._available_models:
            fallback = self._available_models[0]
            logging.warning(
                "Configured model '%s' not available, falling back to '%s'",
                self.model_name,
                fallback,
            )
            self.model_name = fallback

        self._load_speech_model(self.model_name)
        logging.info("Foundry model loaded: %s", self.model_name)

    def _load_speech_model(self, model_name: str) -> None:
        if self._manager is None:
            raise RuntimeError("Foundry manager not initialized")

        speech_model = self._manager.catalog.get_model(model_name)
        speech_model.download(
            lambda p: logging.info("Downloading %s %.1f%%", model_name, p)
        )
        speech_model.load()
        self._speech_model = speech_model
        self._audio_client = speech_model.get_audio_client()
        self.model_name = model_name

    def _start_recording(self) -> None:
        if self.recording:
            return

        self.recording = True
        self.audio_chunks = []

        def _on_audio(indata, _frames, _time, status):
            if status:
                logging.warning("Audio status: %s", status)
            self.audio_chunks.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=self._input_device,
            callback=_on_audio,
        )
        self._stream.start()
        self._refresh_icon()
        beep_async(600, 80)
        logging.info("Recording started (hold Ctrl+Win)")

    def _stop_recording(self) -> None:
        if not self.recording:
            return

        self.recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._refresh_icon()
        beep_async(900, 90)

        if not self.audio_chunks:
            logging.info("No audio captured")
            return

        audio = np.concatenate(self.audio_chunks, axis=0).squeeze()
        self.audio_chunks = []
        duration_s = len(audio) / SAMPLE_RATE

        peak = float(np.max(np.abs(audio)))
        logging.info("Captured %.2fs audio (peak %.4f)", duration_s, peak)
        if peak < 0.001:
            logging.warning("Captured audio appears near-silent")

        threading.Thread(
            target=self._transcribe,
            args=(audio, duration_s),
            daemon=True,
        ).start()

    def _transcribe(self, audio: np.ndarray, duration_s: float) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        t0 = time.monotonic()

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)

            audio_16 = np.clip(audio, -1.0, 1.0)
            audio_16 = (audio_16 * 32767).astype(np.int16)

            wf: Any = wave.open(str(tmp_path), "wb")
            try:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_16.tobytes())
            finally:
                wf.close()

            response = self._audio_client.transcribe(str(tmp_path))
            text = response.text.strip() if hasattr(response, "text") else str(response).strip()
            elapsed = time.monotonic() - t0

            if text:
                logging.info("Transcription: %s", text)
                append_transcript_log(ts, text)
                set_clipboard_text(text)
                if self.auto_paste:
                    simulate_ctrl_v()
                beep_async(1200, 80)
                self._append_history(
                    {
                        "ts": ts,
                        "text": text,
                        "audio_s": round(duration_s, 2),
                        "latency_s": round(elapsed, 2),
                        "status": "ok",
                    }
                )
            else:
                logging.info("No speech detected")
                beep_async(420, 200)
                self._append_history(
                    {
                        "ts": ts,
                        "text": "",
                        "audio_s": round(duration_s, 2),
                        "latency_s": round(elapsed, 2),
                        "status": "empty",
                    }
                )
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            elapsed = time.monotonic() - t0
            logging.exception("Transcription failed")
            beep_async(300, 250)
            self._append_history(
                {
                    "ts": ts,
                    "text": "",
                    "audio_s": round(duration_s, 2),
                    "latency_s": round(elapsed, 2),
                    "status": "error",
                    "error": str(exc),
                }
            )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _append_history(entry: dict) -> None:
        try:
            with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logging.exception("Failed writing transcription history")

    def _refresh_icon(self) -> None:
        if self._tray is None:
            return

        self._tray.icon = create_mic_icon(self.recording)
        if self.recording:
            self._tray.title = "Foundry Transcribe | Recording..."
        elif not self._hook_enabled:
            self._tray.title = "Foundry Transcribe | Paused"
        else:
            self._tray.title = "Foundry Transcribe | Hold Ctrl+Win to dictate"

    def _is_ctrl(self, vk: int) -> bool:
        return vk in (VK_LCONTROL, VK_RCONTROL)

    def _is_win(self, vk: int) -> bool:
        return vk in (VK_LWIN, VK_RWIN)

    def _on_key_event(self, vk: int, is_down: bool) -> bool:
        changed = False

        if self._is_ctrl(vk):
            if self._ctrl_held != is_down:
                self._ctrl_held = is_down
                changed = True

        if self._is_win(vk):
            if self._win_held != is_down:
                self._win_held = is_down
                changed = True

        if not changed:
            return False

        both_held = self._ctrl_held and self._win_held

        if self._hook_enabled:
            if both_held and not self.recording:
                self._both_held_since = time.monotonic()
                with self._lock:
                    self._start_recording()
            elif not both_held and self.recording:
                hold_ms = (time.monotonic() - self._both_held_since) * 1000
                with self._lock:
                    if hold_ms < MIN_HOLD_MS:
                        self.recording = False
                        if self._stream is not None:
                            self._stream.stop()
                            self._stream.close()
                            self._stream = None
                        self.audio_chunks = []
                        self._refresh_icon()
                        logging.info("Tap too short (%.0fms), discarded", hold_ms)
                    else:
                        self._stop_recording()

        if self._is_win(vk) and is_down and self._ctrl_held:
            return True

        return False

    def _hook_proc(self, n_code, w_param, l_param):
        try:
            if n_code >= 0:
                kb = ctypes.cast(
                    l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)
                ).contents
                vk = kb.vkCode
                is_down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self._is_ctrl(vk) or self._is_win(vk):
                    suppress = self._on_key_event(vk, is_down)
                    if suppress:
                        return 1
        except (ctypes.ArgumentError, OSError, RuntimeError, ValueError):  # noqa: BLE001
            logging.exception("Keyboard hook error")

        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _hook_loop(self) -> None:
        self._hook_thread_id = kernel32.GetCurrentThreadId()
        self._hook_callback_ref = HOOKPROC(self._hook_proc)

        hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_callback_ref,
            None,
            0,
        )

        if not hook:
            err = ctypes.get_last_error()
            logging.error("SetWindowsHookExW failed: %s", err)
            return

        logging.info("Keyboard hook installed")

        msg = ctypes.wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                break
            if ret == -1:
                err = ctypes.get_last_error()
                logging.error("GetMessageW error: %s", err)
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnhookWindowsHookEx(hook)

    def _menu_toggle_hook(self, _icon, _item) -> None:
        self._hook_enabled = not self._hook_enabled
        if not self._hook_enabled and self.recording:
            with self._lock:
                self._stop_recording()
        state = "enabled" if self._hook_enabled else "disabled"
        logging.info("Dictation %s", state)
        self._refresh_icon()
        self._rebuild_menu()

    def _menu_auto_paste(self, _icon, _item) -> None:
        self.auto_paste = not self.auto_paste
        logging.info("Auto-paste %s", "on" if self.auto_paste else "off")
        self._rebuild_menu()

    def _menu_open_history(self, _icon, _item) -> None:
        if HISTORY_PATH.exists():
            os.startfile(str(HISTORY_PATH))
        else:
            logging.info("No history file yet")

    def _menu_open_app_log(self, _icon, _item) -> None:
        if APP_LOG_PATH.exists():
            os.startfile(str(APP_LOG_PATH))
        else:
            logging.info("No transcript log yet")

    def _menu_select_model(self, model_name: str) -> None:
        if model_name == self.model_name or self._model_switch_in_progress:
            return

        self._model_switch_in_progress = True
        self._rebuild_menu()

        def _switch() -> None:
            old_model_name = self.model_name
            try:
                with self._lock:
                    if self.recording:
                        logging.info("Cannot switch models while recording")
                        return

                    if self._speech_model is not None:
                        self._speech_model.unload()

                    self._load_speech_model(model_name)
                    self._config["model_name"] = self.model_name
                    self._save_config()
                    logging.info("Switched to model: %s", self.model_name)
                    beep_async(1100, 80)
            except (OSError, RuntimeError, ValueError, TypeError):
                logging.exception("Model switch failed")
                try:
                    self._load_speech_model(old_model_name)
                except (OSError, RuntimeError, ValueError, TypeError):
                    logging.exception("Failed to reload previous model: %s", old_model_name)
            finally:
                self._model_switch_in_progress = False
                self._rebuild_menu()

        threading.Thread(target=_switch, daemon=True).start()

    def _menu_select_mic(self, device_index: int) -> None:
        if device_index == self._input_device:
            return

        if self.recording:
            logging.info("Cannot switch microphone while recording")
            return

        if not self._is_valid_input_device(device_index):
            logging.warning("Selected mic %s is no longer available", device_index)
            self._rebuild_menu()
            return

        self._input_device = device_index
        self._config["mic_index"] = device_index
        self._save_config()
        logging.info("Switched microphone to: %s", device_index)
        self._refresh_icon()
        self._rebuild_menu()

    def _build_mic_submenu(self) -> pystray.Menu:
        inputs = self._list_input_devices()
        if not inputs:
            return pystray.Menu(
                pystray.MenuItem("No microphones found", lambda *_: None, enabled=False)
            )

        def _make_select_action(device_index: int):
            def _action(_icon, _item):
                self._menu_select_mic(device_index)

            return _action

        def _make_checked_action(device_index: int):
            def _checked(_item):
                return self._input_device == device_index

            return _checked

        items = []
        for idx, name, _channels in inputs:
            items.append(
                pystray.MenuItem(
                    f"[{idx}] {name}",
                    _make_select_action(idx),
                    checked=_make_checked_action(idx),
                    enabled=lambda _item: not self.recording,
                )
            )
        return pystray.Menu(*items)

    def _build_model_submenu(self) -> pystray.Menu:
        if not self._available_models:
            return pystray.Menu(pystray.MenuItem("No models found", lambda *_: None, enabled=False))

        def _make_select_action(model_name: str):
            def _action(_icon, _item):
                self._menu_select_model(model_name)

            return _action

        def _make_checked_action(model_name: str):
            def _checked(_item):
                return self.model_name == model_name

            return _checked

        items = []
        for name in self._available_models:
            items.append(
                pystray.MenuItem(
                    name,
                    _make_select_action(name),
                    checked=_make_checked_action(name),
                    enabled=lambda _item: not self._model_switch_in_progress,
                )
            )
        return pystray.Menu(*items)

    def _menu_quit(self, icon, _item) -> None:
        logging.info("Shutting down")
        self._hook_enabled = False

        if self.recording:
            with self._lock:
                self._stop_recording()

        if self._hook_thread_id is not None:
            user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT_MSG, 0, 0)

        if self._speech_model is not None:
            try:
                self._speech_model.unload()
            except (OSError, RuntimeError):  # noqa: BLE001
                logging.exception("Model unload failed")

        icon.stop()

    def _build_menu(self) -> pystray.Menu:
        state_label = "Active (Hold Ctrl+Win)" if self._hook_enabled else "Paused"
        toggle_label = "Pause Dictation" if self._hook_enabled else "Resume Dictation"
        model_label = f"Model: {self.model_name}"
        if self._input_device is None:
            mic_label = "Mic: system default"
        else:
            try:
                mic_name = sd.query_devices(self._input_device)["name"]
            except (sd.PortAudioError, IndexError, KeyError, TypeError):  # noqa: BLE001
                mic_name = "unknown"
            mic_label = f"Mic: [{self._input_device}] {mic_name}"

        return pystray.Menu(
            pystray.MenuItem(state_label, lambda *_: None, enabled=False),
            pystray.MenuItem(toggle_label, self._menu_toggle_hook),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(model_label, lambda *_: None, enabled=False),
            pystray.MenuItem("Whisper model", self._build_model_submenu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(mic_label, lambda *_: None, enabled=False),
            pystray.MenuItem("Microphone", self._build_mic_submenu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Auto-paste at cursor",
                self._menu_auto_paste,
                checked=lambda _: self.auto_paste,
            ),
            pystray.MenuItem("Open History", self._menu_open_history),
            pystray.MenuItem("Open App Log", self._menu_open_app_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._menu_quit),
        )

    def _rebuild_menu(self) -> None:
        if self._tray is not None:
            self._tray.menu = self._build_menu()
            self._tray.update_menu()

    def run(self) -> None:
        self._resolve_model_name()
        self._resolve_input_device()
        self._initialize_foundry()

        threading.Thread(target=self._hook_loop, daemon=True).start()

        self._tray = pystray.Icon(
            name="foundry_transcribe",
            icon=create_mic_icon(False),
            title="Foundry Transcribe | Hold Ctrl+Win to dictate",
            menu=self._build_menu(),
        )

        logging.info("Ready. Hold Ctrl+Win to dictate.")
        self._tray.run()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Foundry Local tray dictation app using Ctrl+Win hold-to-talk",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Foundry speech model name (default: whisper-tiny)",
    )
    parser.add_argument(
        "--no-auto-paste",
        action="store_true",
        help="Only copy to clipboard, do not send Ctrl+V",
    )
    parser.add_argument(
        "--mic-index",
        type=int,
        default=None,
        help="Temporarily override configured microphone index for this launch",
    )
    parser.add_argument(
        "--select-mic",
        action="store_true",
        help="Ask for a new microphone and save it to config",
    )
    args = parser.parse_args()

    configure_logging()
    app = FoundryTranscribeTrayApp(
        model_name=args.model_name,
        auto_paste=not args.no_auto_paste,
        mic_index=args.mic_index,
        force_select_mic=args.select_mic,
    )
    app.run()


if __name__ == "__main__":
    main()
