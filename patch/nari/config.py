from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MODELS = ROOT / "models"
VOICES = ROOT / "voices"
MEMORY_DB = DATA / "nari.db"
SETTINGS_FILE = DATA / "settings.json"
PEOPLE_DIR = DATA / "people"

OLLAMA_URL = os.getenv("NARI_OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_TEXT_MODEL = "qwen3:1.7b"
DEFAULT_TEXT_MODEL_BALANCED = "qwen3:4b"
DEFAULT_VISION_MODEL = "qwen3-vl:2b"
DEFAULT_VOICE = "es_MX-claude-high"
WAKE_WORD = "nari"
END_SILENCE_SECONDS = 2.8
MAX_UTTERANCE_SECONDS = 12.0
SCREEN_FPS = 30
VISION_INTERVAL = 0.20
OFFICIAL_UPDATE_REPO = "zilafeindert/NARI"

def load_settings() -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    base = {
        "text_model": DEFAULT_TEXT_MODEL,
        "balanced_model": DEFAULT_TEXT_MODEL_BALANCED,
        "vision_model": DEFAULT_VISION_MODEL,
        "voice": DEFAULT_VOICE,
        "wake_word": WAKE_WORD,
        "silence_seconds": END_SILENCE_SECONDS,
        "max_utterance_seconds": MAX_UTTERANCE_SECONDS,
        "screen_fps": 20,
        "vision_interval": 0.14,
        "game_analysis_width": 640,
        "temporal_frames": 2,
        "talk_when_idle": False,
        "game_autonomy": False,
        "recognize_people": False,
        "show_live_preview": True,
        "low_latency_mode": True,
        "voice_enabled": True,
        "fast_mode": True,
        "game_profile": "generic",
        "game_inference_fps": 6.0,
        "game_max_actions": 2,
        "update_source": "github",
        "github_repo": OFFICIAL_UPDATE_REPO,
        "update_check_on_start": True,
        "update_check_delay_seconds": 3,
        "microphone_device": None,
        "learning_enabled": True,
        "learning_exploration": 0.08,
        "performance_profile_version": 5,
    }
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                base.update(stored)
        except Exception:
            pass
    # Migración de rendimiento: reemplaza ajustes heredados lentos por el
    # perfil de baja latencia de NARI, una sola vez.
    try:
        if int(base.get("performance_profile_version", 0) or 0) < 5:
            base["screen_fps"] = 20
            base["vision_interval"] = 0.14
            base["game_analysis_width"] = 640
            base["temporal_frames"] = 2
            base["game_inference_fps"] = 6.0
            base["game_max_actions"] = 1
            base["learning_exploration"] = 0.08
            base["performance_profile_version"] = 5
    except Exception:
        base["performance_profile_version"] = 3

    # Never retain an empty repo after an upgrade.
    if not str(base.get("github_repo", "")).strip():
        base["github_repo"] = OFFICIAL_UPDATE_REPO
    SETTINGS_FILE.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    return base

def save_settings(settings: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
