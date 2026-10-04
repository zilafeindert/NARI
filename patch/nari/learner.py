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
        """Estado visual compacto con informacion espacial y una pista del marcador."""
        if frame is None:
            return f"{profile}:none"
        try:
            arr=np.asarray(frame)
            if arr.ndim==2:
                rgb=np.repeat(arr[...,None],3,axis=2).astype(np.float32)
            else:
                rgb=arr[...,:3].astype(np.float32)

            h,w=rgb.shape[:2]
            if h<8 or w<8:
                return f"{profile}:tiny"

            gray=rgb.mean(axis=2)
            ys=np.linspace(0,h-1,36).astype(np.int32)
            xs=np.linspace(0,w-1,48).astype(np.int32)
            small=gray[np.ix_(ys,xs)]

            mean=float(small.mean())
            std=float(small.std())
            dark=float((small<55).mean())
            bright=float((small>190).mean())

            # Estadisticas espaciales 3x4: evita que escenarios visualmente distintos
            # compartan el mismo estado solo por tener promedio parecido.
            spatial=[]
            for ry in range(3):
                y0=(ry*h)//3
                y1=((ry+1)*h)//3
                for rx in range(4):
                    x0=(rx*w)//4
                    x1=((rx+1)*w)//4
                    cell=gray[y0:y1,x0:x1]
                    spatial.append(int(max(0,min(15,float(cell.mean())/16.0))))

            # Pista barata del verde: porcentaje, centro X/Y. Solo sirve como contexto
            # para el Dummy; no declara por si misma que exista un objetivo.
            r=rgb[:,:,0]
            g=rgb[:,:,1]
            b=rgb[:,:,2]
            green=((g>90)&(g>r*1.18)&(g>b*1.05))
            green_ratio=float(green.mean())
            green_y,green_x=np.where(green)
            if green_x.size:
                gx=float(green_x.mean()/max(1,w))
                gy=float(green_y.mean()/max(1,h))
            else:
                gx=gy=0.5

            return (
                f"{profile}:"
                f"m{int(mean//16):02d}:"
                f"s{int(std//16):02d}:"
                f"d{int(dark*10):02d}:"
                f"b{int(bright*10):02d}:"
                f"sp{''.join(format(x,'x') for x in spatial)}:"
                f"gr{int(min(99.0,green_ratio*1000)):02d}:"
                f"gx{int(gx*20):02d}:"
                f"gy{int(gy*20):02d}"
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
        """Recompensa fuerte y contextual para combate JJS.

        La señal no premia simplemente que cambie la pantalla. Premia conseguir
        ventaja verificable y castiga de forma fuerte las decisiones que empeoran
        la situacion. Asi el bandit aprende diferencias reales entre acercarse,
        confirmar un golpe, defender, fallar y recibir dano.
        """
        result = result or {}
        raw = max(-0.45, min(0.55, float(raw_visual_reward or 0.0)))
        key = GameLearner.action_key(action)

        def num(name, default=0.0):
            try:
                return max(-1.0, min(1.0, float(result.get(name, default) or default)))
            except Exception:
                return float(default)

        def flag(name):
            value=result.get(name,False)
            if isinstance(value,str):
                return value.strip().lower() in {"true","1","yes","si","sí","y"}
            return bool(value)

        hit = flag("hit_confirmed")
        block = flag("block_success")
        ko = flag("ko_confirmed")
        whiff = flag("ability_whiff")
        cooldown = flag("cooldown_active")
        target_visible = flag("target_visible")
        target_stunned = flag("target_stunned")
        target_blocking = flag("target_blocking")
        opponent_attacking = flag("opponent_attacking")
        player_stunned = flag("player_stunned")
        player_ragdolled = flag("player_ragdolled")
        player_dead = flag("player_dead")
        ability_confirmed = flag("ability_confirmed")

        enemy_hp_delta = num("enemy_health_delta")
        player_hp_delta = num("player_health_delta")
        distance_delta = num("target_distance_delta")
        align_delta = num("aim_alignment_delta")
        target_distance = num("target_distance", 0.75)

        score = 0.03 * raw

        # Mantener al rival visible es valioso, pero nunca tanto como una accion de combate.
        if target_visible:
            score += 0.035

        # Apuntar y entrar en rango forman la preparacion del intercambio.
        if align_delta > 0.025:
            score += min(0.28, align_delta * 0.34)
        elif align_delta < -0.08:
            score -= min(0.22, abs(align_delta) * 0.25)

        if distance_delta < -0.025:
            score += min(0.24, abs(distance_delta) * 0.30)
        elif distance_delta > 0.12:
            score -= min(0.18, distance_delta * 0.24)

        if 0.0 <= target_distance <= 0.52 and key.startswith("hold_"):
            score += 0.06

        # El dano confirmado es la señal ofensiva mas fuerte.
        if enemy_hp_delta < -0.01:
            score += min(0.95, abs(enemy_hp_delta) * 1.15)
        if hit:
            score += 0.48
        if ability_confirmed:
            score += 0.24
        if target_stunned:
            score += 0.18

        # Defensa y supervivencia.
        if block:
            score += 0.38
        if opponent_attacking and key == "block_f":
            score += 0.18
        if player_stunned or player_ragdolled:
            if key == "dash_q":
                score += 0.22

        # Penalizacion fuerte por perder vida.
        if player_hp_delta < -0.01:
            score -= min(0.95, abs(player_hp_delta) * 1.25)

        # Ganar el intercambio importa mucho mas que cualquier microseñal visual.
        if ko:
            score += 1.25
        if player_dead:
            score -= 1.35

        # Golpear a alguien que esta bloqueando con M1 es mala informacion:
        # no queremos que el aprendizaje descubra un "autopilot" infinito.
        if key == "m1" and target_blocking and not hit:
            score -= 0.48

        if whiff:
            score -= 0.52
        if cooldown and key in {
            "m1", "press_1", "press_2", "press_3", "press_4",
            "special_r", "awaken_g", "dash_q", "block_f"
        }:
            score -= 0.32

        # La camara solo recibe una señal pequeña y exclusivamente si mejoro el aim.
        if key in {"look_left", "look_right", "look_up", "look_down", "camera_turn", "camera_drag"}:
            if align_delta > 0.02:
                score = 0.05 * raw + min(0.22, align_delta * 0.30)
            else:
                score = -0.08

        if key == "wait":
            score -= 0.10

        # Penaliza bucles. Una repeticion solo se salva cuando hay evidencia de progreso.
        if repeat_count >= 2:
            if hit or enemy_hp_delta < -0.01 or block:
                score -= min(0.10, 0.025 * repeat_count)
            else:
                score -= min(0.38, 0.11 * repeat_count)

        if stagnation >= 2:
            score -= min(0.32, 0.065 * (stagnation - 1))

        # Acciones manifiestamente equivocadas reciben una senal negativa visible.
        if key in {"m1", "press_1", "press_2", "press_3", "press_4", "special_r", "awaken_g"}:
            if not target_visible:
                score -= 0.24
        if key == "dash_q" and target_visible and distance_delta > 0.18:
            score -= 0.10

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
            aliases = {
                "q": "dash_q",
                "r": "special_r",
                "g": "awaken_g",
                "f": "block_f",
                "space": "jump",
            }
            return aliases.get(key, f"press_{key or 'unknown'}")

        if kind == "block":
            return "block_f"

        if kind == "double_tap_w":
            return "double_tap_w"

        if kind in {"mouse_move_rel","camera_drag","camera_turn","camera_key_turn"}:
            dx = float(action.get("dx", 0) or 0)
            dy = float(action.get("dy", 0) or 0)
            if abs(dx) > abs(dy):
                return "look_right" if dx > 0 else "look_left"
            if abs(dy) > 0:
                return "look_down" if dy > 0 else "look_up"
            return "camera"

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
            if profile == "jjs":
                alpha = 0.40 if trials < 12 else 0.22
            else:
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
