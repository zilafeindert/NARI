from __future__ import annotations

import time
from collections import deque


class GameBrain:
    """Ejecutivo de juego con memoria corta, diversidad y recuperacion."""

    def __init__(self, learner):
        self.learner = learner
        self.reset("", "generic")

    def reset(self, goal: str, profile: str):
        self.goal = str(goal or "").strip()
        self.profile = str(profile or "generic")
        self.recent = deque(maxlen=12)
        self.last_observation = ""
        self.last_goal_state = ""
        self.last_action_key = ""
        self.last_confidence = 0.0
        self.last_reward = 0.0
        self.stuck_count = 0
        self.no_progress_count = 0
        self.action_count = 0
        self.last_camera_ts = 0.0
        self.last_shift_toggle_ts = -10.0
        self.last_action_ts = 0.0
        self.exploration_cursor = 0

    def history_text(self, limit: int = 8) -> str:
        rows = list(self.recent)[-max(1, int(limit)):]
        if not rows:
            return "sin historial"
        parts = []
        for i, row in enumerate(rows, 1):
            parts.append(
                f"{i}. {row.get('action','?')} "
                f"r={float(row.get('reward',0.0)):+.2f} "
                f"c={float(row.get('confidence',0.0)):.2f} "
                f"{str(row.get('note',''))[:100]}"
            )
        return " | ".join(parts)

    def recent_keys(self, n=5):
        return [str(x.get("action","")) for x in list(self.recent)[-max(1, int(n)):]]

    def feedback(self, observation: str, action: dict, reward: float,
                 confidence: float = 0.0, note: str = "", goal_state: str = ""):
        key = self.learner.action_key(action)
        reward = max(-1.0, min(1.0, float(reward)))
        self.last_observation = str(observation or "")[:400]
        self.last_goal_state = str(goal_state or self.last_goal_state)[:250]
        self.last_action_key = str(key or "")
        self.last_confidence = max(0.0, min(1.0, float(confidence or 0.0)))
        self.last_reward = reward
        self.last_action_ts = time.monotonic()
        self.action_count += 1

        if reward < 0.025:
            self.no_progress_count += 1
        else:
            self.no_progress_count = max(0, self.no_progress_count - 2)

        same = 0
        for row in reversed(self.recent):
            if row.get("action") == self.last_action_key:
                same += 1
            else:
                break
        self.stuck_count = min(9, max(0, same - 1, self.stuck_count))
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

        if kind == "m1":
            return {"type": "m1", "seconds": max(0.02, min(0.09, float(out.get("seconds", 0.045) or 0.045)))}

        if kind == "block":
            return {"type": "block", "seconds": max(0.08, min(0.60, float(out.get("seconds", 0.22) or 0.22)))}

        if kind == "double_tap_w":
            return {"type": "double_tap_w"}

        if kind == "hold":
            key = str(out.get("key", "w")).lower()
            if key not in {"w", "a", "d", "s"}:
                return {"type": "wait", "seconds": 0.08}
            seconds = float(out.get("seconds", 0.22) or 0.22)
            return {
                "type": "hold",
                "key": key,
                "seconds": max(0.10, min(0.38, seconds)),
            }

        if kind == "keys":
            keys = [str(x).lower() for x in out.get("keys", [])]
            if {"shift", "w"} <= set(keys):
                return {"type": "keys", "keys": ["shift", "w"], "seconds": 0.38}
            return {"type": "wait", "seconds": 0.08}

        if kind == "press":
            key = str(out.get("key", "")).lower()
            allowed = {"space", "e", "q", "r", "f", "g", "1", "2", "3", "4", "esc"}
            if key not in allowed:
                return {"type": "wait", "seconds": 0.08}
            return {"type": "press", "key": key}

        if kind in {"camera_turn", "camera_drag"}:
            now = time.monotonic()
            if now - self.last_camera_ts < 0.045:
                return {"type": "wait", "seconds": 0.08}
            # Limitamos deliberadamente el giro para que un error visual no
            # convierta una microdecision en un giro gigantesco.
            dx = max(-250, min(250, int(float(out.get("dx", 0) or 0))))
            dy = max(-170, min(170, int(float(out.get("dy", 0) or 0))))
            if dx == 0 and dy == 0:
                return {"type": "wait", "seconds": 0.08}
            seconds = max(0.035, min(0.10, float(out.get("seconds", 0.055) or 0.055)))
            self.last_camera_ts = now
            return {
                "type": "camera_turn",
                "dx": dx,
                "dy": dy,
                "seconds": seconds,
            }

        if kind == "toggle_shift_lock":
            now = time.monotonic()
            if now - self.last_shift_toggle_ts < 8.0:
                return {"type": "wait", "seconds": 0.08}
            self.last_shift_toggle_ts = now
            return {"type": "toggle_shift_lock"}

        if kind == "click":
            x = max(0, min(1000, int(float(out.get("x", 500) or 500))))
            y = max(0, min(1000, int(float(out.get("y", 500) or 500))))
            button = str(out.get("button", "left")).lower()
            if button not in {"left", "right"}:
                button = "left"
            return {"type": "click", "x": x, "y": y, "normalized": True, "button": button}

        if kind == "wait":
            return {"type": "wait", "seconds": max(0.035, min(0.18, float(out.get("seconds", 0.06) or 0.06)))}

        return {"type": "wait", "seconds": 0.08}

    def _is_bad_repeat(self, key: str, confidence: float) -> bool:
        recent = self.recent_keys(4)
        if not recent:
            return False
        consecutive = 0
        for x in reversed(recent):
            if x == key:
                consecutive += 1
            else:
                break
        if consecutive >= 2:
            # Una accion puede repetirse cuando realmente esta funcionando,
            # pero exigimos mucha confianza y evidencia positiva.
            return not (consecutive == 2 and confidence >= 0.82 and self.last_reward > 0.08)
        if recent.count(key) >= 3:
            return True
        return False

    def _novelty_candidates(self, cycle: int):
        idx = int(cycle) + int(self.exploration_cursor)
        self.exploration_cursor += 1
        if self.profile == "jjs":
            pool = [
                {"type": "hold", "key": "w", "seconds": 0.20},
                {"type": "hold", "key": "a", "seconds": 0.18},
                {"type": "hold", "key": "d", "seconds": 0.18},
                {"type": "m1", "seconds": 0.045},
                {"type": "press", "key": "q"},
                {"type": "block", "seconds": 0.30},
                {"type": "press", "key": "1"},
                {"type": "press", "key": "2"},
                {"type": "press", "key": "3"},
                {"type": "press", "key": "4"},
                {"type": "press", "key": "r"},
                {"type": "press", "key": "g"},
                {"type": "double_tap_w"},
                {"type": "camera_turn", "dx": -180, "dy": 0, "seconds": 0.055},
                {"type": "camera_turn", "dx": 180, "dy": 0, "seconds": 0.055},
                {"type": "camera_turn", "dx": 0, "dy": -110, "seconds": 0.05},
                {"type": "camera_turn", "dx": 0, "dy": 110, "seconds": 0.05},
                {"type": "press", "key": "space"},
                {"type": "wait", "seconds": 0.10},
            ]
            for offset in range(len(pool)):
                action = pool[(idx + offset) % len(pool)]
                key = self.learner.action_key(action)
                if not self._is_bad_repeat(key, 0.0):
                    return self.validate_action(action)
            return self.validate_action(pool[idx % len(pool)])

        if self.profile == "roblox":
            pool = [
                {"type": "hold", "key": "w", "seconds": 0.32},
                {"type": "hold", "key": "a", "seconds": 0.28},
                {"type": "hold", "key": "d", "seconds": 0.28},
                {"type": "press", "key": "space"},
                {"type": "keys", "keys": ["shift", "w"], "seconds": 0.36},
                {"type": "press", "key": "e"},
                {"type": "press", "key": "q"},
                {"type": "press", "key": "r"},
                {"type": "camera_turn", "dx": -145, "dy": 0, "seconds": 0.08},
                {"type": "camera_turn", "dx": 145, "dy": 0, "seconds": 0.08},
                {"type": "camera_turn", "dx": 0, "dy": -95, "seconds": 0.07},
                {"type": "camera_turn", "dx": 0, "dy": 95, "seconds": 0.07},
                {"type": "wait", "seconds": 0.10},
            ]
            # Busqueda circular por una accion que no aparezca recientemente.
            for offset in range(len(pool)):
                action = pool[(idx + offset) % len(pool)]
                key = self.learner.action_key(action)
                if not self._is_bad_repeat(key, 0.0):
                    return self.validate_action(action)
            return self.validate_action(pool[idx % len(pool)])

        return {"type": "wait", "seconds": 0.10}

    def arbitrate(self, actions, confidence=0.0, cycle=0):
        """Elige una sola accion evitando bucles pobres del VLM."""
        valid = []
        for action in actions or []:
            if not isinstance(action, dict):
                continue
            checked = self.validate_action(action)
            key = self.learner.action_key(checked)
            if key in {"unknown", "wait"} and self.stuck_count < 2:
                continue
            valid.append((checked, key))

        if valid:
            # El primer candidato es la intencion del modelo. Solo la sustituimos
            # cuando cae en un bucle claro o cuando el historial exige exploracion.
            for action, key in valid:
                if not self._is_bad_repeat(key, float(confidence or 0.0)):
                    return action, "modelo"
            return self._novelty_candidates(cycle), "diversidad"

        return self._novelty_candidates(cycle), "recuperacion"

    def fallback(self, cycle: int = 0) -> dict:
        return self._novelty_candidates(cycle)
