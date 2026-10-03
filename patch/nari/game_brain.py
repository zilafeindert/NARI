from __future__ import annotations

import time
from collections import deque


class GameBrain:
    """Controlador cerrado de juego.

    Mantiene un estado operativo corto entre observaciones para que NARI no
    dependa de una secuencia fija. No guarda cadenas de pensamiento del modelo:
    solo conserva observaciones, acciones, confianza y resultados resumidos.
    """

    def __init__(self, learner):
        self.learner = learner
        self.reset("", "generic")

    def reset(self, goal: str, profile: str):
        self.goal = str(goal or "").strip()
        self.profile = str(profile or "generic")
        self.recent = deque(maxlen=8)
        self.last_observation = ""
        self.last_action_key = ""
        self.last_confidence = 0.0
        self.last_reward = 0.0
        self.stuck_count = 0
        self.no_progress_count = 0
        self.action_count = 0
        self.last_camera_ts = 0.0
        self.last_shift_toggle_ts = 0.0
        self.last_action_ts = 0.0

    def history_text(self, limit: int = 6) -> str:
        rows = list(self.recent)[-max(1, int(limit)):]
        if not rows:
            return "sin historial"
        parts = []
        for i, row in enumerate(rows, 1):
            parts.append(
                f"{i}. accion={row.get('action','?')} "
                f"recompensa={float(row.get('reward',0.0)):+.2f} "
                f"confianza={float(row.get('confidence',0.0)):.2f} "
                f"nota={str(row.get('note',''))[:90]}"
            )
        return " | ".join(parts)

    def feedback(self, observation: str, action: dict, reward: float,
                 confidence: float = 0.0, note: str = ""):
        key = self.learner.action_key(action)
        reward = max(-1.0, min(1.0, float(reward)))
        self.last_observation = str(observation or "")[:350]
        self.last_action_key = str(key or "")
        self.last_confidence = float(confidence or 0.0)
        self.last_reward = reward
        self.last_action_ts = time.monotonic()
        self.action_count += 1
        if reward < 0.025:
            self.no_progress_count += 1
        else:
            self.no_progress_count = max(0, self.no_progress_count - 1)

        same = 0
        for row in reversed(self.recent):
            if row.get("action") == self.last_action_key:
                same += 1
            else:
                break
        self.stuck_count = min(9, max(self.stuck_count, same - 1))
        if reward > 0.08:
            self.stuck_count = max(0, self.stuck_count - 1)

        self.recent.append({
            "action": self.last_action_key,
            "reward": reward,
            "confidence": self.last_confidence,
            "note": str(note or "")[:180],
        })

    def validate_action(self, action: dict) -> dict:
        if not isinstance(action, dict):
            return {"type": "wait", "seconds": 0.08}

        out = dict(action)
        kind = str(out.get("type", "")).lower().strip()

        if kind == "hold":
            key = str(out.get("key", "w")).lower()
            if key not in {"w", "a", "s", "d"}:
                return {"type": "wait", "seconds": 0.08}
            seconds = float(out.get("seconds", 0.38) or 0.38)
            return {
                "type": "hold",
                "key": key,
                "seconds": max(0.20, min(0.62, seconds)),
            }

        if kind == "keys":
            keys = [str(x).lower() for x in out.get("keys", [])]
            if {"shift", "w"} <= set(keys):
                return {"type": "keys", "keys": ["shift", "w"], "seconds": 0.42}
            return {"type": "wait", "seconds": 0.08}

        if kind == "press":
            key = str(out.get("key", "")).lower()
            allowed = {"space", "e", "q", "r", "f", "1", "2", "3", "esc"}
            if key not in allowed:
                return {"type": "wait", "seconds": 0.08}
            return {"type": "press", "key": key}

        if kind in {"camera_turn", "camera_drag"}:
            now = time.monotonic()
            if now - self.last_camera_ts < 0.10:
                return {"type": "wait", "seconds": 0.08}
            dx = max(-520, min(520, int(float(out.get("dx", 0) or 0))))
            dy = max(-360, min(360, int(float(out.get("dy", 0) or 0))))
            if dx == 0 and dy == 0:
                return {"type": "wait", "seconds": 0.08}
            seconds = max(0.06, min(0.18, float(out.get("seconds", 0.10) or 0.10)))
            self.last_camera_ts = now
            return {
                "type": "camera_turn",
                "dx": dx,
                "dy": dy,
                "seconds": seconds,
            }

        if kind == "toggle_shift_lock":
            now = time.monotonic()
            if now - self.last_shift_toggle_ts < 5.0:
                return {"type": "wait", "seconds": 0.08}
            self.last_shift_toggle_ts = now
            return {"type": "toggle_shift_lock"}

        if kind == "click":
            x = max(0, min(1000, int(float(out.get("x", 500) or 500))))
            y = max(0, min(1000, int(float(out.get("y", 500) or 500))))
            return {
                "type": "click",
                "x": x,
                "y": y,
                "normalized": True,
                "button": str(out.get("button", "left")).lower(),
            }

        if kind == "wait":
            return {"type": "wait", "seconds": max(0.05, min(0.30, float(out.get("seconds", 0.10) or 0.10)))}

        return {"type": "wait", "seconds": 0.08}

    def fallback(self, cycle: int = 0) -> dict:
        # Solo se usa cuando la percepcion no produce una accion ejecutable.
        # Evita aleatoriedad: primero busca progreso, despues intenta reorientar.
        if self.profile == "roblox":
            if self.no_progress_count >= 3 or self.stuck_count >= 2:
                return self.validate_action({
                    "type": "camera_turn",
                    "dx": -360 if int(cycle) % 2 == 0 else 360,
                    "dy": 0,
                    "seconds": 0.10,
                })
            return self.validate_action({
                "type": "hold",
                "key": "w",
                "seconds": 0.38,
            })
        return {"type": "wait", "seconds": 0.10}
