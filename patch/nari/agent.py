from __future__ import annotations
import json
import re
import time
import threading
import requests
from .config import OLLAMA_URL, load_settings
from .personality import prompt_for, random_micro_shift
from .learner import GameLearner
from .decision import DecisionCore

GAME_PROFILES = {
    "generic": "Entorno interactivo generico. Observa el fotograma y decide una accion util.",
    "roblox": "Roblox: usa WASD, mouse, espacio y clic. Observa la interfaz antes de actuar.",
    "limbus": "Limbus Company: lee la interfaz y usa acciones de combate prudentes.",
}
ACTION_HINT = (
    "Devuelve SOLO JSON valido con reply y actions. Cada action tiene type. "
    "Acciones: click, double_click, move, drag, press, hold, keys, key_down, key_up, "
    "type, scroll, wait, open_url, open_app, remember, social_update, self_update, "
    "drive_update, private_note, done. Coordenadas 0..1000. reward=-1..1, progress=0..1, observation=texto corto."
)

class Agent:
    def __init__(self, memory, computer, web, on_status=None, on_token=None):
        self.memory = memory
        self.computer = computer
        self.web = web
        self.on_status = on_status or (lambda s: None)
        self.on_token = on_token or (lambda s: None)
        self.settings = load_settings()
        self.history = []
        self.stop_event = threading.Event()
        self._models_cache = []
        self._models_cache_ts = 0.0
        self.game_learner = GameLearner(self.memory.path)
        self.decisions = DecisionCore(self.memory.path)

    def set_settings(self, settings):
        self.settings = settings or {}

    def stop(self):
        self.stop_event.set()
        try:
            self.computer.stop()
        except Exception:
            pass

    def reset_stop(self):
        self.stop_event.clear()
        try:
            self.computer.clear_stop()
        except Exception:
            pass

    def _context(self):
        return prompt_for(
            self.memory.self_state(),
            self.memory.drives(),
            self.memory.people(),
            self.memory.recall("", 8),
            self.memory.recent_episodes(5),
        )

    def _available_models(self):
        now = time.monotonic()
        if now - self._models_cache_ts < 8:
            return self._models_cache
        try:
            r = requests.get(OLLAMA_URL + "/api/tags", timeout=3)
            r.raise_for_status()
            models = [str(x.get("name", "")) for x in r.json().get("models", [])]
            self._models_cache = models
            self._models_cache_ts = now
            return models
        except Exception:
            return []

    def _pick_text_model(self):
        configured = str(self.settings.get("text_model", "qwen3:1.7b")).strip()
        models = self._available_models()
        if not models or configured in models:
            return configured
        base = configured.split(":")[0]
        for m in models:
            if m.split(":")[0] == base:
                return m
        for candidate in ("qwen3:1.7b", "llama3.2:1b", "gemma3:1b", "qwen3:4b", "llama3.2:3b"):
            if candidate in models:
                return candidate
        return models[0] if models else configured

    def _pick_vision_model(self):
        configured = str(self.settings.get("vision_model", "qwen3-vl:2b")).strip()
        models = self._available_models()
        if not models or configured in models:
            return configured
        hints = ("vl", "vision", "llava", "minicpm-v")
        for m in models:
            if any(h in m.lower() for h in hints):
                return m
        return configured

    def _call(self, messages, model, timeout=15, num_predict=72, ctx=1536):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": "60m",
            "options": {
                "temperature": 0.25,
                "num_ctx": ctx,
                "num_predict": num_predict,
                "top_p": 0.85,
            },
        }
        r = requests.post(OLLAMA_URL + "/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        return str(msg.get("content", "") or data.get("response", "") or "")

    @staticmethod
    def _clean_visible_reply(value):
        text = re.sub(r'<think>.*?</think>', '', str(value or ''), flags=re.I | re.S).strip()
        text = re.sub(r'^\s*```(?:json|python|javascript|js|text)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```\s*$', '', text)
        code_markers = ('def ', 'import ', 'from ', 'class ', 'self.', 'async ', 'except ', 'Traceback', '```')
        lines = [x for x in text.splitlines() if x.strip()]
        marker_hits = sum(any(m in line for m in code_markers) for line in lines)
        if len(lines) >= 5 and marker_hits >= 3:
            return 'Tuve un problema interpretando mi respuesta. Inténtalo de nuevo.'
        if len(text) > 900:
            text = text[:880].rsplit(' ', 1)[0] + '…'
        return text.strip()

    @staticmethod
    def _parse(raw):
        text = re.sub(r'<think>.*?</think>', '', str(raw or ''), flags=re.I | re.S).strip()
        text = re.sub(r'^json\s*', '', text, flags=re.I)
        if not text:
            return {'reply': '', 'actions': []}
        try:
            value = json.loads(text)
        except Exception:
            a, b = text.find('{'), text.rfind('}')
            if a >= 0 and b > a:
                try:
                    value = json.loads(text[a:b + 1])
                except Exception:
                    return {'reply': 'No pude interpretar la respuesta del modelo.', 'actions': []}
            else:
                return {'reply': 'No pude interpretar la respuesta del modelo.', 'actions': []}
        if not isinstance(value, dict):
            return {'reply': '', 'actions': []}
        if 'reply' in value:
            value['reply'] = Agent._clean_visible_reply(value.get('reply'))
        return value
    @staticmethod
    def _normalize(result):
        if not isinstance(result, dict):
            return {"reply": "", "actions": []}
        actions = result.get("actions", [])
        if isinstance(actions, dict):
            actions = [actions]
        if not isinstance(actions, list):
            actions = []
        actions = [a for a in actions if isinstance(a, dict)]
        if not actions and isinstance(result.get("action"), dict):
            actions = [result["action"]]
        if not actions and result.get("key"):
            actions = [{"type": "press", "key": str(result["key"])}]
        if not actions and "x" in result and "y" in result:
            actions = [{"type": "click", "x": result["x"], "y": result["y"], "normalized": True, "button": str(result.get("button", "left"))}]
        out = {
            "reply": str(result.get("reply", result.get("message", ""))).strip(),
            "actions": actions,
        }
        for key in ("reward", "progress", "observation", "done", "confidence", "plan", "decision_note", "mode"):
            if key in result:
                try:
                    if key in {"reward", "progress", "confidence"}:
                        out[key] = float(result[key])
                    else:
                        out[key] = result[key]
                except Exception:
                    pass
        return out

    def _apply_memory_actions(self, result):
        clean = []
        for action in result.get("actions", []):
            kind = str(action.get("type", "")).lower()
            if kind == "remember":
                self.memory.remember(str(action.get("text", "")), str(action.get("kind", "general")), 0.6)
            elif kind == "social_update":
                self.memory.adjust_person(str(action.get("person", "")), action.get("deltas", {}), str(action.get("note", "")))
            elif kind == "self_update":
                self.memory.adjust_self(action.get("changes", {}))
            elif kind == "drive_update":
                self.memory.adjust_drive(str(action.get("drive", "")), float(action.get("delta", 0)), str(action.get("reason", "")))
            elif kind == "private_note":
                self.memory.add_private_note(str(action.get("text", "")))
            else:
                clean.append(action)
        return {"reply": str(result.get("reply", "")).strip(), "actions": clean}

    def chat(self, text, automation_allowed=False):
        self.reset_stop()
        text = str(text or "").strip()
        model = self._pick_text_model()
        system = (
            "/no_think\n"
            "Responde rapido y natural. NO muestres razonamiento interno. "
            "Tu respuesta visible debe ser breve, clara y directa. "
            + self._context() + "\n\n" + ACTION_HINT
        )
        if not automation_allowed:
            system += "\nAutonomia de PC desactivada."
        messages = [{"role": "system", "content": system}] + self.history[-3:] + [{"role": "user", "content": text}]
        try:
            result = self._normalize(self._parse(self._call(messages, model, 10, 56, 1024)))
            if not result.get("reply"):
                retry = [
                    {"role": "system", "content": "/no_think Responde directamente en español. No expliques tu razonamiento."},
                    {"role": "user", "content": text},
                ]
                raw = self._call(retry, model, 10, 48, 1024)
                result = {"reply": str(raw).strip(), "actions": []}
        except Exception as exc:
            return {
                "reply": f"Ollama no pudo responder usando {model}. Abre Ollama y verifica que el modelo este instalado.",
                "actions": [],
                "error": str(exc),
            }
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": result.get("reply", "")}]
        return self._apply_memory_actions(result)

    def vision(self, goal, images_b64, profile="generic", previous_action="", state_key=""):
        model = self._pick_vision_model()
        hint = self.game_learner.hint(profile, state_key or (str(profile) + ":none"))
        if profile == "roblox":
            controls = (
                "Roblox: W/A/S/D mover; SPACE saltar; SHIFT puede activar Shift Lock "
                "si la experiencia lo permite; SHIFT+W correr; E interactuar; "
                "Q/R/F/1/2/3 son teclas situacionales; click UI; "
                "camera_drag = RMB mantenido + movimiento relativo."
            )
        else:
            controls = GAME_PROFILES.get(profile, GAME_PROFILES["generic"])

        system = (
            "/no_think\n"
            "Eres un agente de juego en tiempo real. Observa solo la imagen actual. "
            "Toma una decisión deliberada, pero no expliques el razonamiento interno. "
            "Devuelve un plan corto de 1 a 3 acciones ordenadas que tengan sentido juntas. "
            "JSON: {actions:[...],plan:"...",decision_note:"...",observation:"...",confidence:0.0}. "
            "plan y decision_note deben ser resúmenes breves de la decisión, no cadena de pensamiento. "
            "No devuelvas código. Evita cambiar de dirección sin una razón visible. "
            "Mantén una acción de movimiento durante un tramo razonable. "
            + controls +
            "\nOBJETIVO: " + str(goal) +
            "\nACCION ANTERIOR: " + (previous_action or "ninguna") +
            "\nAPRENDIZAJE: " + hint[:450]
        )

        msg = {
            "role": "user",
            "content": "Mira el fotograma y decide el siguiente pequeño plan.",
            "images": images_b64,
        }

        try:
            result = self._normalize(self._parse(self._call(
                [{"role":"system","content":system},msg],
                model, 4.5, 56, 896
            )))

            if profile == "roblox":
                fixed=[]
                for action in result.get("actions",[])[:3]:
                    if not isinstance(action,dict):
                        continue
                    kind=str(action.get("type","")).lower()
                    if kind=="mouse_move_rel":
                        dx=int(float(action.get("dx",0) or 0))
                        dy=int(float(action.get("dy",0) or 0))
                        fixed.append({
                            "type":"camera_drag",
                            "dx":max(-700,min(700,dx*4)),
                            "dy":max(-450,min(450,dy*4)),
                            "seconds":0.11
                        })
                    elif kind in {"camera_drag","camera_turn"}:
                        action["dx"]=max(-750,min(750,int(float(action.get("dx",0) or 0))))
                        action["dy"]=max(-500,min(500,int(float(action.get("dy",0) or 0))))
                        action["seconds"]=max(0.07,min(0.22,float(action.get("seconds",0.11))))
                        fixed.append(action)
                    elif kind=="hold" and str(action.get("key","")).lower() in {"w","a","d","s"}:
                        action["seconds"]=max(0.45,min(1.0,float(action.get("seconds",0.65))))
                        fixed.append(action)
                    elif kind=="keys":
                        keys={str(x).lower() for x in action.get("keys",[])}
                        if {"shift","w"} <= keys:
                            action["keys"]=["shift","w"]
                            action["seconds"]=0.50
                            fixed.append(action)
                    elif kind in {"press","click"}:
                        fixed.append(action)
                    elif kind in {"mouse_button_down","mouse_button_up"}:
                        fixed.append(action)
                result["actions"]=fixed[:3]
            return result
        except Exception as exc:
            return {"reply":"","actions":[],"error":str(exc),"model":model}

    def decision_record(self, context, goal, result, decision):
        try:
            self.decisions.record(
                context=context,
                goal=goal,
                observation=result.get("observation",""),
                plan=result.get("plan",""),
                decision=decision,
                confidence=float(result.get("confidence",0.0) or 0.0),
            )
        except Exception:
            pass

    def decision_latest(self, context=None):
        try:
            return self.decisions.latest(context)
        except Exception:
            return {}

    def decision_recent(self, limit=15, context=None):
        try:
            return self.decisions.recent(limit, context)
        except Exception:
            return []

    def game_state_key(self, frame, profile="generic"):
        return self.game_learner.state_key(frame, profile)

    def game_fallback_action(self, profile, frame, cycle):
        state = self.game_state_key(frame, profile)
        return self.game_learner.choose(profile, state, cycle), state

    def game_record(self, profile, state, action, reward, source="vision", note=""):
        self.game_learner.record(profile, state, action, reward, source, note)

    def game_remember_transition(self, profile, state, action):
        self.game_learner.remember_transition(profile, state, action)

    def game_hint(self, profile, state):
        return self.game_learner.hint(profile, state)

    def game_stats(self, profile):
        return self.game_learner.stats(profile)

    def execute_actions(self, result, autonomy_allowed=False):
        if not autonomy_allowed:
            return "autonomia apagada"
        logs = []
        for action in result.get("actions", [])[:int(self.settings.get("game_max_actions", 4))]:
            if self.stop_event.is_set():
                break
            kind = str(action.get("type", "")).lower()
            if kind == "search_web":
                try:
                    logs.append("web:" + " | ".join(x.get("title", "") for x in self.web(str(action.get("query", "")))[:3]))
                except Exception as exc:
                    logs.append("web error:" + str(exc))
                continue
            if kind in {"remember", "social_update", "self_update", "drive_update", "private_note", "done"}:
                continue
            logs.append(kind + ":" + str(self.computer.act(action)))
        try:
            self.memory.adjust_self(random_micro_shift())
        except Exception:
            pass
        return " | ".join(logs)
