from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

import numpy as np


DEFAULT_ACTIONS = {
    "jjs": (
        "hold_w",
        "hold_a",
        "hold_d",
        "hold_s",
        "m1",
        "press_1",
        "press_2",
        "press_3",
        "press_4",
        "dash_q",
        "block_f",
        "special_r",
        "awaken_g",
        "double_tap_w",
        "toggle_shift_lock",
        "look_left",
        "look_right",
        "look_up",
        "look_down",
        "jump",
        "wait",
    ),
    "roblox": (
        "hold_w",
        "hold_a",
        "hold_d",
        "hold_s",
        "jump",
        "sprint_forward",
        "interact",
        "toggle_shift_lock",
        "press_e",
        "press_q",
        "press_r",
        "press_f",
        "press_1",
        "press_2",
        "press_3",
        "look_left",
        "look_right",
        "look_up",
        "look_down",
        "click",
        "wait",
    ),
    "generic": (
        "hold_w",
        "hold_a",
        "hold_d",
        "look_left",
        "look_right",
        "space",
        "click",
        "wait",
    ),
    "limbus": (
        "click_center",
        "click_left",
        "click_right",
        "space",
        "wait",
        "look_left",
        "look_right",
    ),
}


class GameLearner:
    """Aprendizaje persistente tipo reinforcement/bandit, sin reentrenar los pesos del LLM.

    Aprende que acciones tienden a funcionar en estados visuales parecidos y
    castiga acciones que dejan al juego estancado. Todo queda en la memoria local.
    """

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=8.0)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        self._init()

    def _init(self):
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS game_policy(
                profile TEXT NOT NULL,
                state TEXT NOT NULL,
                action TEXT NOT NULL,
                trials INTEGER NOT NULL DEFAULT 0,
                value REAL NOT NULL DEFAULT 0,
                updated REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(profile,state,action)
            );

            CREATE TABLE IF NOT EXISTS game_experiences(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL,
                state TEXT NOT NULL,
                action TEXT NOT NULL,
                reward REAL NOT NULL,
                source TEXT NOT NULL,
                note TEXT DEFAULT '',
                created REAL NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS game_sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL,
                started REAL NOT NULL,
                ended REAL,
                total_reward REAL NOT NULL DEFAULT 0,
                experiences INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.db.commit()
        self.session_id = None
        self.profile = ""
        self.total_reward = 0.0
        self.experiences = 0
        self._last_action = None
        self._last_state = None
        self._last_action_ts = 0.0

    def start_session(self, profile: str) -> int:
        self.profile = str(profile or "generic")
        self.total_reward = 0.0
        self.experiences = 0
        cur = self.db.execute(
            "INSERT INTO game_sessions(profile,started) VALUES(?,?)",
            (self.profile, time.time()),
        )
        self.db.commit()
        self.session_id = int(cur.lastrowid)
        self._last_action = None
        self._last_state = None
        self._last_action_ts = 0.0
        return self.session_id

    def end_session(self):
        if self.session_id is not None:
            self.db.execute(
                "UPDATE game_sessions SET ended=?,total_reward=?,experiences=? WHERE id=?",
                (time.time(), self.total_reward, self.experiences, self.session_id),
            )
            self.db.commit()
        self.session_id = None

    @staticmethod
    def state_key(frame, profile="generic") -> str:
        if frame is None:
            return f"{profile}:none"
        try:
            arr = np.asarray(frame)
            if arr.ndim == 3:
                gray = arr.astype(np.float32).mean(axis=2)
            else:
                gray = arr.astype(np.float32)

            h, w = gray.shape[:2]
            if h > 48 or w > 64:
                ys = np.linspace(0, h - 1, 48).astype(np.int32)
                xs = np.linspace(0, w - 1, 64).astype(np.int32)
                gray = gray[np.ix_(ys, xs)]

            mean = float(gray.mean())
            std = float(gray.std())
            dark = float((gray < 55).mean())
            bright = float((gray > 190).mean())

            # Cuantización deliberada: permite generalizar entre fotogramas parecidos.
            return (
                f"{profile}:"
                f"m{int(mean // 16):02d}:"
                f"s{int(std // 16):02d}:"
                f"d{int(dark * 10):02d}:"
                f"b{int(bright * 10):02d}:"
                f"q{int((w / max(h,1)) * 10):02d}"
            )
        except Exception:
            return f"{profile}:unknown"

    @staticmethod
    def frame_reward(previous_frame, current_frame) -> float:
        """Recompensa base por cambio visual útil, con penalización por estancamiento."""
        if previous_frame is None or current_frame is None:
            return 0.0
        try:
            a = np.asarray(previous_frame).astype(np.float32)
            b = np.asarray(current_frame).astype(np.float32)
            if a.ndim == 3:
                a = a.mean(axis=2)
            if b.ndim == 3:
                b = b.mean(axis=2)
            h = min(a.shape[0], b.shape[0])
            w = min(a.shape[1], b.shape[1])
            if h == 0 or w == 0:
                return 0.0
            a = a[:h, :w]
            b = b[:h, :w]
            # Reduce el tamaño antes de comparar para evitar ruido de píxeles.
            ys = np.linspace(0, h - 1, min(48, h)).astype(np.int32)
            xs = np.linspace(0, w - 1, min(64, w)).astype(np.int32)
            da = a[np.ix_(ys, xs)]
            db = b[np.ix_(ys, xs)]
            diff = float(np.abs(da - db).mean()) / 255.0
            # 0.02 ~ casi congelado; 0.15+ ~ cambio fuerte.
            reward = (diff - 0.02) * 3.8
            return max(-0.45, min(0.55, reward))
        except Exception:
            return 0.0

    @staticmethod
    def strict_reward(
        raw_visual_reward: float,
        action: dict,
        action_effect: float = 0.0,
        progress_delta: float = 0.0,
        repeat_count: int = 0,
        stagnation: int = 0,
    ) -> float:
        """Recompensa estricta: el cambio visual por si solo no cuenta como exito."""
        raw = max(-0.45, min(0.55, float(raw_visual_reward)))
        effect = max(-1.0, min(1.0, float(action_effect or 0.0)))
        progress = max(-1.0, min(1.0, float(progress_delta or 0.0)))
        key = GameLearner.action_key(action)
        score = (0.25 * raw) + (0.55 * effect) + (0.20 * progress)

        # Girar la camara puede producir mucho cambio visual sin acercarse
        # al objetivo. Necesita evidencia explicita de que ayudo.
        if key in {"look_left", "look_right"}:
            score *= 0.30
            if effect < 0.15 and progress < 0.15:
                score -= 0.08

        if key == "wait":
            score -= 0.07

        if key.startswith("hold_") and raw > 0.025 and effect >= 0:
            score += 0.035

        if effect >= 0.45:
            score += 0.14
        elif effect <= -0.45:
            score -= 0.18

        if progress >= 0.30:
            score += 0.16
        elif progress <= -0.30:
            score -= 0.20

        if repeat_count >= 2:
            score -= min(0.25, 0.07 * repeat_count)

        if stagnation >= 3:
            score -= min(0.20, 0.04 * (stagnation - 2))

        return max(-1.0, min(1.0, score))

    @staticmethod
    def jjs_reward(
        raw_visual_reward: float,
        action: dict,
        result: dict | None = None,
        repeat_count: int = 0,
        stagnation: int = 0,
    ) -> float:
        """Recompensa especifica de combate para Jujutsu Shenanigans.

        La señal visual queda como apoyo. Los eventos de combate estimados por
        vision tienen prioridad para que atacar, bloquear y acercarse aprendan
        como conductas utiles, mientras que fallar, recibir dano o morir se
        castiga.
        """
        result = result or {}
        raw = max(-0.45, min(0.55, float(raw_visual_reward or 0.0)))
        key = GameLearner.action_key(action)

        def num(name, default=0.0):
            try:
                return max(-1.0, min(1.0, float(result.get(name, default) or default)))
            except Exception:
                return float(default)

        hit = bool(result.get("hit_confirmed", False))
        block = bool(result.get("block_success", False))
        ko = bool(result.get("ko_confirmed", False) or result.get("death_or_ko", False))
        whiff = bool(result.get("ability_whiff", False))
        cooldown = bool(result.get("cooldown_active", False))
        enemy_hp_delta = num("enemy_health_delta")
        player_hp_delta = num("player_health_delta")
        distance_delta = num("target_distance_delta")
        align_delta = num("aim_alignment_delta")

        score = 0.06 * raw

        if enemy_hp_delta < -0.02:
            score += min(0.85, abs(enemy_hp_delta) * 0.85)
        if hit:
            score += 0.34
        if block:
            score += 0.24
        if distance_delta < -0.05:
            score += min(0.10, abs(distance_delta) * 0.12)
        if align_delta > 0.05:
            score += min(0.12, align_delta * 0.14)

        if player_hp_delta < -0.02:
            score -= min(0.80, abs(player_hp_delta) * 0.90)
        if ko:
            score += 1.0 if enemy_hp_delta <= 0 else 0.65

        if whiff:
            score -= 0.20
        if cooldown and key in {
            "m1", "press_1", "press_2", "press_3", "press_4",
            "press_r", "press_g", "dash_q", "block_f"
        }:
            score -= 0.14

        if key in {"look_left", "look_right", "camera_turn", "camera_drag"}:
            score *= 0.35
            if align_delta <= 0.05:
                score -= 0.03

        if key == "wait":
            score -= 0.06

        if repeat_count >= 2:
            score -= min(0.26, 0.075 * repeat_count)

        if stagnation >= 3:
            score -= min(0.18, 0.035 * (stagnation - 2))

        return max(-1.0, min(1.0, score))

    @staticmethod
    def action_key(action: dict) -> str:
        if not isinstance(action, dict):
            return "none"
        kind = str(action.get("type", "")).lower().strip()
        if kind == "hold":
            return f"hold_{str(action.get('key','w')).lower()}"
        if kind == "press":
            key = str(action.get("key", "")).lower()
            if key == "space":
                return "space"
            return f"press_{key or 'unknown'}"
        if kind in {"mouse_move_rel","camera_drag","camera_turn","camera_key_turn"}:
            dx = float(action.get("dx", 0) or 0)
            dy = float(action.get("dy", 0) or 0)
            if abs(dx) > abs(dy):
                return "look_right" if dx > 0 else "look_left"
        if kind in {"click", "double_click"}:
            return "click"
        if kind == "wait":
            return "wait"
        return kind or "unknown"

    @staticmethod
    def action_from_key(key: str, profile: str, cycle: int = 0) -> dict:
        key = str(key or "wait")
        if key == "m1":
            return {"type": "m1", "seconds": 0.055}
        if key == "block_f":
            return {"type": "block", "seconds": 0.35}
        if key == "dash_q":
            return {"type": "press", "key": "q"}
        if key == "special_r":
            return {"type": "press", "key": "r"}
        if key == "awaken_g":
            return {"type": "press", "key": "g"}
        if key == "double_tap_w":
            return {"type": "double_tap_w"}
        if key == "hold_w":
            return {"type": "hold", "key": "w", "seconds": 0.50}
        if key == "hold_a":
            return {"type": "hold", "key": "a", "seconds": 0.35}
        if key == "hold_d":
            return {"type": "hold", "key": "d", "seconds": 0.35}
        if key == "hold_s":
            return {"type": "hold", "key": "s", "seconds": 0.30}
        if key == "jump":
            return {"type": "press", "key": "space"}
        if key == "sprint_forward":
            return {"type": "keys", "keys": ["shift","w"], "seconds": 0.55}
        if key == "interact":
            return {"type": "press", "key": "e"}
        if key == "toggle_shift_lock":
            return {"type": "toggle_shift_lock"}
        if key.startswith("press_") and len(key) == 7:
            return {"type": "press", "key": key[-1]}
        if key == "look_left":
            return {"type": "camera_turn", "dx": -145, "dy": 0, "seconds": 0.08}
        if key == "look_right":
            return {"type": "camera_turn", "dx": 145, "dy": 0, "seconds": 0.08}
        if key == "look_up":
            return {"type": "camera_turn", "dx": 0, "dy": -95, "seconds": 0.07}
        if key == "look_down":
            return {"type": "camera_turn", "dx": 0, "dy": 95, "seconds": 0.07}
        if key == "click":
            return {"type": "click", "x": 500, "y": 500, "normalized": True}
        if key == "click_center":
            return {"type": "click", "x": 500, "y": 520, "normalized": True}
        if key == "click_left":
            return {"type": "click", "x": 350, "y": 520, "normalized": True}
        if key == "click_right":
            return {"type": "click", "x": 650, "y": 520, "normalized": True}
        return {"type": "wait", "seconds": 0.12}

    def _rows(self, profile: str, state: str):
        return self.db.execute(
            "SELECT action,trials,value FROM game_policy "
            "WHERE profile=? AND state=? ORDER BY value DESC, trials DESC",
            (profile, state),
        ).fetchall()

    def _ensure_actions(self, profile: str, state: str):
        actions = DEFAULT_ACTIONS.get(profile, DEFAULT_ACTIONS["generic"])
        for action in actions:
            self.db.execute(
                "INSERT OR IGNORE INTO game_policy(profile,state,action) VALUES(?,?,?)",
                (profile, state, action),
            )
        self.db.commit()

    def scores(self, profile: str, state: str, limit: int = 6):
        self._ensure_actions(profile, state)
        rows = self._rows(profile, state)
        return [
            {"action": str(r["action"]), "trials": int(r["trials"]), "value": float(r["value"])}
            for r in rows[:max(1, int(limit))]
        ]

    def hint(self, profile: str, state: str) -> str:
        scores = self.scores(profile, state, 5)
        learned = ", ".join(
            f"{x['action']}={x['value']:+.2f}({x['trials']})" for x in scores
        )
        if not learned:
            return "Sin experiencia previa: explora y cambia de acción si el entorno no responde."
        return (
            "Memoria de juego: "
            + learned
            + ". Prioriza acciones con valor alto, pero prueba alternativas cuando una acción se estanque."
        )

    def choose(self, profile: str, state: str, cycle: int = 0) -> dict:
        self._ensure_actions(profile, state)

        # Para Roblox, el fallback no debe saltar de tecla en tecla de forma
        # aleatoria. Usa un ciclo de exploración coherente y deja a la visión
        # reemplazarlo cuando tenga una decisión.
        if profile == "roblox":
            sequence = (
                "hold_w",
                "look_right",
                "hold_w",
                "look_left",
                "hold_w",
                "jump",
                "hold_w",
                "sprint_forward",
                "interact",
            )
            chosen = sequence[int(cycle) % len(sequence)]
            self._last_action = chosen
            self._last_state = state
            self._last_action_ts = time.monotonic()
            return self.action_from_key(chosen, profile, cycle)
        rows = self._rows(profile, state)
        if not rows:
            return self.action_from_key("wait", profile, cycle)

        # Exploración fuerte al principio; después decae lentamente.
        epsilon = max(0.10, 0.55 * math.exp(-float(cycle) / 80.0))
        untried = [r for r in rows if int(r["trials"]) == 0]

        if untried:
            idx = int(cycle) % len(untried)
            chosen = untried[idx]["action"]
        else:
            # UCB sencillo: explora acciones con poca muestra y alto valor.
            total = max(1, sum(int(r["trials"]) for r in rows))
            best = None
            best_score = -10**9
            for r in rows:
                trials = max(1, int(r["trials"]))
                value = float(r["value"])
                bonus = 0.55 * math.sqrt(math.log(total + 1) / trials)
                score = value + bonus
                if score > best_score:
                    best_score = score
                    best = r
            chosen = str(best["action"] if best else rows[0]["action"])

            # Determinismo con semilla derivada del ciclo evita depender de random global.
            if (cycle * 37 + len(state)) % 100 < int(epsilon * 100):
                chosen = str(rows[(cycle + 1) % len(rows)]["action"])

        self._last_action = chosen
        self._last_state = state
        self._last_action_ts = time.monotonic()
        return self.action_from_key(chosen, profile, cycle)

    def record(
        self,
        profile: str,
        state: str,
        action: dict,
        reward: float,
        source: str = "vision",
        note: str = "",
    ):
        key = self.action_key(action)
        reward = max(-1.0, min(1.0, float(reward)))
        if not key or key == "unknown":
            return

        self._ensure_actions(profile, state)
        row = self.db.execute(
            "SELECT trials,value FROM game_policy WHERE profile=? AND state=? AND action=?",
            (profile, state, key),
        ).fetchone()

        if row:
            trials = int(row["trials"])
            old = float(row["value"])
            alpha = 0.28 if trials < 12 else 0.16
            value = old + alpha * (reward - old)
            self.db.execute(
                "UPDATE game_policy SET trials=?,value=?,updated=? WHERE profile=? AND state=? AND action=?",
                (trials + 1, value, time.time(), profile, state, key),
            )
        else:
            self.db.execute(
                "INSERT INTO game_policy(profile,state,action,trials,value,updated) VALUES(?,?,?,?,?,?)",
                (profile, state, key, 1, reward, time.time()),
            )

        self.db.execute(
            "INSERT INTO game_experiences(profile,state,action,reward,source,note) VALUES(?,?,?,?,?,?)",
            (profile, state, key, reward, source, str(note)[:500]),
        )

        self.total_reward += reward
        self.experiences += 1
        self.db.commit()

        if self.session_id is not None:
            self.db.execute(
                "UPDATE game_sessions SET total_reward=?,experiences=? WHERE id=?",
                (self.total_reward, self.experiences, self.session_id),
            )
            self.db.commit()

    def remember_transition(self, profile: str, state: str, action: dict):
        self._last_state = state
        self._last_action = self.action_key(action)
        self._last_action_ts = time.monotonic()

    def stats(self, profile: str | None = None) -> dict:
        where = ""
        args = ()
        if profile:
            where = " WHERE profile=?"
            args = (str(profile),)
        row = self.db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(reward),0) AS total, "
            "COALESCE(AVG(reward),0) AS avg FROM game_experiences" + where,
            args,
        ).fetchone()
        return {
            "experiences": int(row["n"] or 0),
            "total_reward": float(row["total"] or 0),
            "avg_reward": float(row["avg"] or 0),
        }

    def status(self, profile: str, state: str) -> str:
        s = self.stats(profile)
        scores = self.scores(profile, state, 3)
        best = scores[0]["action"] if scores else "—"
        return (
            f"Aprendizaje • {s['experiences']} experiencias • "
            f"recompensa media {s['avg_reward']:+.2f} • mejor local: {best}"
        )

    def close(self):
        try:
            self.db.close()
        except Exception:
            pass
