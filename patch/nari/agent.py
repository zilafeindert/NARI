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
from .game_brain import GameBrain

GAME_PROFILES = {
    "generic": "Entorno interactivo generico. Observa el fotograma y decide una accion util.",
    "roblox": "Roblox: usa WASD, mouse, espacio y clic. Observa la interfaz antes de actuar.",
    "jjs": "Jujutsu Shenanigans: battleground de combate. Prioriza leer HUD, objetivo, distancia, ataques, dash y defensa.",
    "limbus": "Limbus Company: lee la interfaz y usa acciones de combate prudentes.",
}
ACTION_HINT = (
    "Devuelve SOLO JSON valido con reply y actions. Cada action tiene type. "
    "Acciones: click, double_click, move, drag, press, hold, keys, key_down, key_up, "
    "camera_drag, camera_turn, toggle_shift_lock, mouse_button_down, mouse_button_up, "
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
        self.game_brain = GameBrain(self.game_learner)

    def set_settings(self, settings):
        self.settings = settings or {}
        try:
            self.game_brain.exploration_rate=max(
                0.0,
                min(0.30,float(self.settings.get("learning_exploration",0.06) or 0.06))
            )
        except Exception:
            self.game_brain.exploration_rate=0.06

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
        if models:
            # Para control en tiempo real se respeta el modelo configurado.
            # No se fuerza la variante 4B porque aumenta mucho la latencia.
            if configured in models:
                return configured
        hints = ("vl", "vision", "llava", "minicpm-v")
        for m in models:
            if any(h in m.lower() for h in hints):
                return m
        return configured

    def _call(self, messages, model, timeout=15, num_predict=72, ctx=1536, think=False):
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": bool(think),
            "format": "json",
            "keep_alive": "60m",
            "options": {
                "temperature": 0.20,
                "num_ctx": ctx,
                "num_predict": num_predict,
                "top_p": 0.82,
            },
        }
        r = requests.post(OLLAMA_URL + "/api/chat", json=payload, timeout=timeout)
        # Algunos modelos/versions de Ollama no aceptan think=true. En ese
        # caso se reintenta automaticamente sin pensamiento extendido.
        if r.status_code >= 400 and think:
            payload["think"] = False
            r = requests.post(OLLAMA_URL + "/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        # Ollama puede devolver el razonamiento interno separado de content.
        # Nunca se mezcla con la respuesta visible de NARI.
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
        for key in (
            "reward", "progress", "progress_delta", "action_effect",
            "observation", "goal_state", "done", "confidence", "plan",
            "decision_note", "mode",
            "hit_confirmed", "block_success", "ko_confirmed", "death_or_ko",
            "ability_whiff", "cooldown_active", "target_visible",
            "target_center_x", "target_center_y", "target_distance", "target_distance_delta", "target_name", "target_is_dummy",
            "enemy_health_delta", "player_health_delta", "aim_alignment_delta",
            "target_stunned", "target_blocking", "opponent_attacking",
            "player_stunned", "player_ragdolled", "ability_confirmed",
            "player_dead", "target_health_percent", "player_health_percent"
        ):
            if key in result:
                try:
                    if key in {"reward", "progress", "confidence"}:
                        out[key] = float(result[key])
                    elif key in {
                        "done", "hit_confirmed", "block_success", "ko_confirmed", "death_or_ko",
                        "ability_whiff", "cooldown_active", "target_visible", "target_is_dummy",
                        "target_stunned", "target_blocking", "opponent_attacking",
                        "player_stunned", "player_ragdolled", "ability_confirmed", "player_dead"
                    }:
                        value=result[key]
                        if isinstance(value,str):
                            out[key]=value.strip().lower() in {"true","1","yes","si","sí"}
                        else:
                            out[key]=bool(value)
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

    @staticmethod
    def _normalize_jjs_target(result):
        if not isinstance(result,dict):
            return result

        name=str(result.get("target_name","") or "").strip().lower()
        obs=str(result.get("observation","") or "").strip().lower()
        note=str(result.get("decision_note","") or "").strip().lower()
        blob=" ".join((name,obs,note))

        dummy=(
            bool(result.get("target_is_dummy",False))
            or "dummy" in name
            or "dummy" in obs
            or "dummy" in note
        )

        enemy_terms=(
            "enemy","opponent","rival","foe","hostile","adversary",
            "enemigo","oponente","adversario","jugador enemigo","enemy player"
        )
        human_explicit=(not dummy and any(term in blob for term in enemy_terms))

        # Recover common model outputs when the strict scalar fields were omitted.
        if "target_center_x" not in result or "target_center_y" not in result:
            center=result.get("target_center")
            try:
                if isinstance(center,(list,tuple)) and len(center)>=2:
                    cx=float(center[0]); cy=float(center[1])
                    if cx>1.0 or cy>1.0:
                        cx/=1000.0; cy/=1000.0
                    result["target_center_x"]=max(0.0,min(1.0,cx))
                    result["target_center_y"]=max(0.0,min(1.0,cy))
                elif isinstance(center,dict):
                    cx=float(center.get("x",0.5)); cy=float(center.get("y",0.5))
                    if cx>1.0 or cy>1.0:
                        cx/=1000.0; cy/=1000.0
                    result["target_center_x"]=max(0.0,min(1.0,cx))
                    result["target_center_y"]=max(0.0,min(1.0,cy))
            except Exception:
                pass

        if "target_center_x" not in result or "target_center_y" not in result:
            box=result.get("bbox") or result.get("target_bbox") or result.get("bounding_box")
            try:
                if isinstance(box,(list,tuple)) and len(box)>=4:
                    x1,y1,x2,y2=[float(v) for v in box[:4]]
                    if max(abs(x1),abs(y1),abs(x2),abs(y2))>1.0:
                        x1/=1000.0; y1/=1000.0; x2/=1000.0; y2/=1000.0
                    result["target_center_x"]=max(0.0,min(1.0,(x1+x2)/2.0))
                    result["target_center_y"]=max(0.0,min(1.0,(y1+y2)/2.0))
            except Exception:
                pass

        if dummy or human_explicit:
            result["target_visible"]=True
            result["target_is_dummy"]=bool(dummy)
            if dummy:
                result["target_name"]="Dummy"
            elif not str(result.get("target_name","") or "").strip():
                result["target_name"]="enemy"
            result.setdefault("target_center_x",0.5)
            result.setdefault("target_center_y",0.5)
            try:
                result["confidence"]=max(0.55,float(result.get("confidence",0.0) or 0.0))
            except Exception:
                result["confidence"]=0.55
        return result

    def jjs_target_scan(self, images_b64):
        """Escaner VLM compacto exclusivo para adquirir rivales en JJS."""
        model=self._pick_vision_model()
        system=(
            "/no_think "
            "Eres un detector visual de objetivos para Jujutsu Shenanigans. "
            "Responde SOLO JSON, sin explicaciones. "
            "Ignora HUD, texto, decoracion y el avatar propio del jugador. "
            "Busca el avatar humano enemigo mas claro y cercano dentro de la arena. "
            "El enemigo es cualquier otro personaje humano visible distinto del avatar propio. "
            "Si hay un enemigo visible, target_visible DEBE ser true, target_is_dummy=false, "
            "target_name debe ser enemy y target_center_x/y deben indicar el centro aproximado del torso en 0..1. "
            "El Dummy solo es target_is_dummy=true cuando el cuadrado verde sobre su cabeza sea visible. "
            "Si no hay ningun objetivo identificable, target_visible=false. "
            'Usa SOLO este JSON minimo: {"target_visible":false,"target_is_dummy":false,'
            '"target_center_x":0.5,"target_center_y":0.5,"confidence":0.0}.'
        )
        msg={
            "role":"user",
            "content":(
                "Adquiere UN solo objetivo. Primero busca un enemigo humano visible distinto del avatar propio. "
                "Si no existe enemigo humano pero ves el Dummy verde, marca target_is_dummy=true. "
                "Si hay enemigo, target_visible=true y marca el centro de su torso. "
                "No describas la escena y no generes acciones."
            ),
            "images":images_b64[-1:],
        }
        try:
            raw=self._call(
                [{"role":"system","content":system},msg],
                model, 2.6, 96, 896, think=False
            )
        except Exception:
            raw=self._call(
                [{"role":"system","content":system},msg],
                model, 2.0, 80, 768, think=False
            )
        result=self._normalize(self._parse(raw))
        result=self._normalize_jjs_target(result)
        result["actions"]=[]
        result["model"]=model
        return result

    def vision(self, goal, images_b64, profile="generic", previous_action="", state_key=""):
        """Percepcion visual + deliberacion de juego.

        El VLM puede proponer una primera accion y una alternativa. El ejecutivo
        local elige una sola y bloquea bucles de repeticion. El razonamiento
        extendido, cuando Ollama lo devuelve, se mantiene fuera de la interfaz.
        """
        model = self._pick_vision_model()
        brain = self.game_brain
        hint = self.game_learner.hint(profile, state_key or (str(profile) + ":none"))

        if profile == "jjs":
            controls = (
                "Jujutsu Shenanigans en PC: M1/click izquierdo = combo basico; 1/2/3/4 = "
                "habilidades; Q = dash y tambien escape de stun/ragdoll; F = bloquear; R = especial; "
                "G = awakening; W+W = sprint; Shift = Shift Lock. El combate debe seguir un ciclo: "
                "buscar objetivo -> centrar -> entrar en rango -> confirmar M1 -> extender solo si hay "
                "impacto -> defender o reposicionarse -> volver a confirmar. Q no se gasta sin motivo: "
                "guardalo para escapar de stun/ragdoll o para un cierre/retirada justificados. "
                "El bloqueo depende de estar mirando al rival; no bloquees indefinidamente. "
                "Cuando el rival esta bloqueando, no repitas M1 sin una lectura que justifique romper guardia. "
                "Cuando el rival esta atacando, prioriza bloquear, girar para mantenerlo enfrente o usar Q "
                "si estas en stun/ragdoll. Usa 1-4/R solo cuando la imagen muestre una apertura o cuando "
                "la accion anterior haya confirmado una ruta valida. El Dummy tiene un cuadrado verde sobre "
                "la cabeza; usa marcador, nombre o silueta como evidencia, pero exige consistencia temporal. "
                "Marca target_is_dummy=true solo con evidencia visible. El rival humano puede no tener marcador: "
                "usa su silueta, centro corporal y cambios temporales. Si el objetivo esta fuera de centro, "
                "gira poco hacia el. No ataques al aire ni ejecutes combos completos por anticipado. "
                "En JJS NO devuelvas camera_turn/camera_drag/camera_key_turn; el controlador local "
                "es el unico responsable de mover la camara. Concentrate en identificar estado, "
                "apertura, ataque, defensa, stun, cooldown y rango."
            )
        elif profile == "roblox":
            controls = (
                "Roblox: W/A/S/D mover; SPACE saltar; SHIFT+W correr; E/Q/R/F/1/2/3 "
                "son acciones contextuales; click izquierdo para UI; Shift puede alternar "
                "Shift Lock cuando la experiencia lo permite; camera_turn usa movimiento "
                "relativo y debe ser pequeno; no existe un Control Lock universal de Roblox."
            )
        else:
            controls = GAME_PROFILES.get(profile, GAME_PROFILES["generic"])

        system = (
            "Eres la inteligencia de control de un videojuego en tiempo real. "
            "Analiza el fotograma mas reciente y, cuando se entreguen varios, comparalos "
            "en orden temporal para detectar movimiento, cambios de camara y progreso. "
            "Antes de responder, delibera internamente sobre objetivo, obstaculo y riesgo. "
            "NO imprimas esa cadena de pensamiento. Entrega solo un resumen operativo. "
            "Tu trabajo es tomar decisiones utiles, variadas y verificables; no repitas "
            "automaticamente la ultima accion. "
            "Devuelve SOLO JSON valido y compacto. En JJS prioriza adquisicion de objetivo sobre narracion. "
            '{"target_visible":false,"target_is_dummy":false,"target_name":"","target_center_x":0.5,'
            '"target_center_y":0.5,"confidence":0.0,"target_distance":0.75,'
            '"opponent_attacking":false,"target_stunned":false,"target_blocking":false,'
            '"player_stunned":false,"player_ragdolled":false,"player_dead":false,'
            '"hit_confirmed":false,"ability_whiff":false,"cooldown_active":false,'
            '"target_distance_delta":0.0,"aim_alignment_delta":0.0,'
            '"actions":[{"type":"..."}],"observation":"...","decision_note":"..."}. '
            "actions debe contener 1 o 2 acciones candidatas ordenadas por preferencia; "
            "el controlador local ejecutara SOLO UNA. "
            "Acciones permitidas: hold(key=w/a/s/d,seconds), block(seconds), m1(seconds), advance_m1(seconds), "
            "double_tap_w, keys(keys=[shift,w],seconds), press(key=space/e/q/r/f/g/1/2/3/4), "
            "camera_turn(dx,dy,seconds), camera_drag(dx,dy,seconds), camera_key_turn(dx,dy,seconds), "
            "toggle_shift_lock, "
            "click(x,y,normalized=true), wait(seconds). "
            "Haz microacciones: mover 0.10-0.38 s; camara 0.035-0.10 s; giros cortos y visibles. "
            "No uses un giro grande para buscar a ciegas. Para seguir un objetivo visible, "
            "elige la direccion que lo acerque. Si no hay objetivo visible, explora: mover, "
            "reorientar, cambiar lateral, saltar o interactuar de forma controlada. "
            "Despues de una accion sin progreso, la siguiente decision debe ser diferente "
            "salvo que exista evidencia clara de que repetirla es correcto. "
            "No hagas clic si no identificas un elemento interactivo. "
            + controls +
            "\nOBJETIVO: " + str(goal) +
            "\nACCION ANTERIOR: " + (previous_action or "ninguna") +
            "\nHISTORIAL OPERATIVO: " + brain.history_text(8) +
            "\nSIN PROGRESO: " + str(brain.no_progress_count) +
            "\nATASCO: " + str(brain.stuck_count) +
            "\nMEMORIA DE APRENDIZAJE: " + hint[:600]
        )

        msg = {
            "role":"user",
            "content":(
                "Observa las imagenes. Determina que intenta conseguir el jugador, "
                "que elemento visible es relevante y propone hasta dos microacciones. "
                "La primera debe ser la mejor; la segunda una alternativa util y distinta. "
                "Evalua tambien la accion anterior. "
                "En JJS solo necesitas esta telemetria esencial: target_visible, target_is_dummy, target_name, "
                "target_center_x/y, target_distance, confidence, opponent_attacking, target_stunned, target_blocking, "
                "player_stunned, player_ragdolled, player_dead, hit_confirmed, ability_whiff, cooldown_active, "
                "target_distance_delta, aim_alignment_delta y actions. No inventes datos faltantes. "
                "Si aparece cualquier avatar/personaje enemigo distinto del jugador propio, target_visible DEBE ser true, "
                "target_is_dummy=false y target_name debe ser enemy/opponent/foe; usa el centro aproximado del torso en 0..1. "
                "Si no conoces la identidad exacta, eso NO es razon para devolver target_visible=false. "
                "Ignora el avatar propio, HUD, texto y decoracion. Si hay varios enemigos, elige el enemigo vivo mas claro y cercano. "
                "target_is_dummy=true solo con evidencia del Dummy. target_center_y: 0=arriba y 1=abajo. "
                "target_distance: 0=muy cerca y 1=muy lejos. Da prioridad absoluta a adquirir un objetivo antes que a describir la escena."
            ),
            "images":images_b64,
        }

        try:
            # Control de baja latencia: usa solo los fotogramas mas recientes.
            vision_images = images_b64[-1:] if profile != "jjs" else images_b64[-2:]
            msg["images"] = vision_images
            try:
                raw=self._call(
                    [{"role":"system","content":system},msg],
                    model, 3.5 if profile=="jjs" else 5.5,
                    96 if profile=="jjs" else 72,
                    1024, think=False
                )
            except Exception as first_error:
                # Recuperacion: algunos builds/modelos visuales fallan con varias
                # imagenes. Reintenta una sola imagen antes de declarar perdida de percepcion.
                msg["images"] = images_b64[-1:]
                raw=self._call(
                    [{"role":"system","content":system},msg],
                    model, 2.8 if profile=="jjs" else 4.0,
                    80 if profile=="jjs" else 56,
                    896 if profile=="jjs" else 1024, think=False
                )

            result=self._normalize(self._parse(raw))
            if profile=="jjs":
                result=self._normalize_jjs_target(result)

            fixed=[]
            for action in (result.get("actions") or [])[:2]:
                if isinstance(action,dict):
                    fixed.append(brain.validate_action(action, commit=False))
            result["actions"]=fixed[:2]
            result["reply"]=Agent._clean_visible_reply(result.get("reply",""))
            result["model"]=model
            return result
        except Exception as exc:
            return {
                "reply":"",
                "actions":[],
                "observation":"",
                "goal_state":"",
                "decision_note":"fallo de percepcion; usar recuperacion",
                "confidence":0.0,
                "error":str(exc),
                "model":model,
            }

    def autonomous_reflection(self):
        """Genera una reflexión operativa breve para la memoria privada."""
        model = self._pick_text_model()
        system = (
            "/no_think\n"
            "Escribe una reflexión privada muy breve en español. "
            "No hagas filosofía ni afirmes conciencia. "
            "Resume una observación, una idea o una pequeña meta que NARI podría considerar. "
            "Máximo 35 palabras y sin código."
        )
        prompt = (
            "Estado actual: " + self._context() +
            "\n¿Qué observación o idea concreta debería conservar como pensamiento privado?"
        )
        try:
            raw=self._call(
                [{"role":"system","content":system},{"role":"user","content":prompt}],
                model, 8, 48, 768
            )
            value=self._clean_visible_reply(raw)
            if value:
                self.decisions.record(
                    context="autonomous",
                    goal="Reflexión privada",
                    observation="",
                    plan=value,
                    decision="reflexión",
                    confidence=0.55,
                )
            return value
        except Exception:
            return ""

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

    def game_reset(self, goal, profile):
        self.game_brain.reset(goal, profile)

    def game_feedback(self, observation, action, reward, confidence=0.0, note="", goal_state=""):
        self.game_brain.feedback(observation, action, reward, confidence, note, goal_state)

    def game_validate_action(self, action):
        return self.game_brain.validate_action(action)

    def game_strict_reward(self, raw_visual_reward, action, result=None, repeat_count=0, stagnation=0):
        result = result or {}
        return self.game_learner.strict_reward(
            raw_visual_reward,
            action,
            float(result.get("action_effect", 0.0) or 0.0),
            float(result.get("progress_delta", 0.0) or 0.0),
            int(repeat_count),
            int(stagnation),
        )

    def game_jjs_reward(self, raw_visual_reward, action, result=None, repeat_count=0, stagnation=0):
        result = result or {}
        return self.game_learner.jjs_reward(
            raw_visual_reward,
            action,
            result,
            int(repeat_count),
            int(stagnation),
        )

    def game_choose_action(self, actions, confidence=0.0, cycle=0, frame=None, state_key=""):
        # El ejecutivo local combina la propuesta visual con el valor aprendido.
        chosen, source = self.game_brain.arbitrate(
            actions, confidence, cycle, state_key=state_key
        )
        return chosen, source

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
