from __future__ import annotations
import json
import subprocess
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from .agent import Agent
from .computer import Computer
from .config import ROOT, DATA, MODELS, VOICES, MEMORY_DB, load_settings, save_settings
from .memory import Memory
from .personality import SELF_KEYS, PEOPLE_KEYS
from .stt import VoiceListener
from .tts import TTS
from .vision import ScreenVideo, PeopleVision
from .web import search as web_search
from .updater import APP_VERSION, check as check_updates, _is_newer

BG = "#0a0b10"
PANEL = "#121621"
PANEL2 = "#181e2b"
TEXT = "#f2f4f8"
MUTED = "#8c95a8"
ACCENT = "#c86bff"
ACCENT2 = "#72e8ff"
GOOD = "#7debb0"
WARN = "#ffd477"
BAD = "#ff6e93"


class NariApp:
    def __init__(self):
        self.settings = load_settings()
        self.memory = Memory(MEMORY_DB)
        self.computer = Computer(DATA)
        self.screen = ScreenVideo(self.settings.get("screen_fps", 20), analysis_width=int(self.settings.get("game_analysis_width", 768)))
        self.people = PeopleVision(MODELS / "vision")
        self.agent = Agent(self.memory, self.computer, web_search, self._status, self._token)
        self.agent.set_settings(self.settings)
        # Cargar una pequeña ventana del historial persistente para mantener contexto
        # al reiniciar, sin permitir acciones de borrado sobre el historial.
        try:
            self.agent.history = [
                {"role": x["role"], "content": x["text"]}
                for x in self.memory.chat_history(limit=12)
                if x.get("role") in {"user", "assistant"}
            ]
        except Exception:
            self.agent.history = []
        voice_name = self.settings.get("voice", "es_MX-claude-high")
        voice_file = VOICES / f"{voice_name}.onnx"
        self.tts = TTS(voice_file, self.settings.get("voice_enabled", True))
        self.tts.on_error = lambda msg: self._status("❌ Voz: " + str(msg)[:130])
        self.listener = VoiceListener(
            DATA / "vosk-model-small-es-0.42",
            self.settings.get("wake_word", "nari"),
            self.settings.get("silence_seconds", 3.0),
            self.settings.get("max_utterance_seconds", 12.0),
            self._voice_command,
            self._status,
            self.settings.get("microphone_device"),
        )
        self.tts.on_speaking = lambda value: self.listener.set_ignore(value)

        self.root = tk.Tk()
        self._styles()
        self.root.title(f"NARI  •  {APP_VERSION}")
        self.root.geometry("1240x800")
        self.root.minsize(1040, 680)
        self.busy = False
        self.game_running = False
        self.game_cycle = 0
        self.last_game_frame_ts = 0.0
        self.last_game_action_ts = 0.0
        self.last_learning_frame = None
        self.last_learning_state = ""
        self.last_learning_action = None
        self.last_learning_action_label = ""
        self.learning_enabled = bool(self.settings.get("learning_enabled", True))
        self.developer = tk.BooleanVar(value=False)
        self.free_var = tk.BooleanVar(value=self.settings.get("talk_when_idle", False))
        self.people_var = tk.BooleanVar(value=self.settings.get("recognize_people", False))
        self.voice_var = tk.BooleanVar(value=self.settings.get("voice_enabled", True))
        self.auto_var = tk.BooleanVar(value=self.settings.get("game_autonomy", False))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.computer.set_host_window(self.root.winfo_id())
        self.game_infer_lock = threading.Lock()
        self.game_target_title = ""
        self.game_recommended_actions = []
        self.game_recommendation_ts = 0.0
        self.game_action_lock = threading.Lock()
        self.root.bind("<F8>", lambda e: self.emergency_stop())
        self._build_ui()
        self.global_hotkey_running = True
        threading.Thread(target=self._global_hotkey_loop, name="NARI-hotkey", daemon=True).start()

        self.screen.start()
        try:
            self.listener.start()
        except Exception as e:
            self._status(f"⚠ Voz no disponible: {e}")

        self.root.after(33, self._refresh_ui)
        self.root.after(30000, self._idle_tick)
        threading.Thread(target=self._autonomous_reflection_loop, daemon=True, name="NARI-reflection").start()
        self.root.after(int(self.settings.get("update_check_delay_seconds", 8) * 1000), self._auto_update_check)
        self._status(f"NARI {APP_VERSION}  •  lista  •  di «NARI»")

    def _global_hotkey_loop(self):
        if __import__("sys").platform != "win32":
            return
        try:
            import ctypes
            VK_F8 = 0x77
            was_down = False
            while getattr(self, "global_hotkey_running", False):
                down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F8) & 0x8000)
                if down and not was_down and self.game_running:
                    self._emergency_stop_core()
                    try:
                        self.root.after(0, lambda: self._status("🛑 F8 • juego detenido"))
                    except Exception:
                        pass
                was_down = down
                time.sleep(0.035)
        except Exception as exc:
            try: self._status("F8 global: "+str(exc))
            except Exception: pass

    def _styles(self):
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        self.style.configure("TFrame", background=BG)
        self.style.configure("Panel.TFrame", background=PANEL)
        self.style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        self.style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        self.style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 24, "bold"))
        self.style.configure("Accent.TLabel", background=BG, foreground=ACCENT2, font=("Segoe UI", 9, "bold"))
        self.style.configure("TButton", background=PANEL2, foreground=TEXT, borderwidth=0, padding=(12, 8), font=("Segoe UI", 10, "bold"))
        self.style.map("TButton", background=[("active", "#252c3b")])
        self.style.configure("Accent.TButton", background=ACCENT, foreground="#0a0b10")
        self.style.map("Accent.TButton", background=[("active", "#db8fff")])
        self.style.configure("Danger.TButton", background="#39202c", foreground="#ffdbe4")
        self.style.configure("TCheckbutton", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        self.style.configure("TEntry", fieldbackground=PANEL2, foreground=TEXT, insertcolor=TEXT, borderwidth=0, padding=8)
        self.style.configure("TCombobox", fieldbackground=PANEL2, foreground=TEXT, background=PANEL2)

    def _build_ui(self):
        header = ttk.Frame(self.root, padding=(18, 15, 18, 10)); header.pack(fill="x")
        ttk.Label(header, text="NARI", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="LOCAL  •  VIVO  •  APRENDIENDO", style="Accent.TLabel").pack(side="left", padx=14, pady=(9,0))
        self.status_var = tk.StringVar(value="iniciando…")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").pack(side="left", padx=16, pady=(9,0))
        ttk.Button(header, text="DETENER  F8", style="Danger.TButton", command=self.emergency_stop).pack(side="right")

        main = ttk.Frame(self.root); main.pack(fill="both", expand=True, padx=18, pady=(0,18))
        sidebar = ttk.Frame(main, style="Panel.TFrame", width=215); sidebar.pack(side="left", fill="y", padx=(0,12)); sidebar.pack_propagate(False)
        ttk.Label(sidebar, text="NARI", background=PANEL, foreground=ACCENT, font=("Segoe UI", 19, "bold")).pack(anchor="w", padx=16, pady=(18,2))
        ttk.Label(sidebar, text="companion local", background=PANEL, foreground=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0,18))
        for name, cmd in [("Chat", lambda:self._show("chat")), ("Juego", lambda:self._show("game")), ("Personas", lambda:self._show("people")), ("Estado", lambda:self._show("state")), ("Desarrollador", lambda:self._show("dev"))]:
            ttk.Button(sidebar, text=name, command=cmd).pack(fill="x", padx=10, pady=4)
        ttk.Separator(sidebar).pack(fill="x", padx=14, pady=14)
        ttk.Checkbutton(sidebar, text="Voz", variable=self.voice_var, command=self._save_toggles).pack(anchor="w", padx=14, pady=5)
        ttk.Button(sidebar, text="Probar micrófono", command=self._test_microphone).pack(fill="x", padx=10, pady=(0,3))
        ttk.Button(sidebar, text="Probar voz", command=self._test_voice).pack(fill="x", padx=10, pady=(0,3))
        ttk.Button(sidebar, text="🎙 HABLAR AHORA", command=self._talk_now).pack(fill="x", padx=10, pady=(0,5))
        ttk.Checkbutton(sidebar, text="Autonomía PC", variable=self.auto_var, command=self._save_toggles).pack(anchor="w", padx=14, pady=5)
        ttk.Checkbutton(sidebar, text="Modo libre", variable=self.free_var, command=self._save_toggles).pack(anchor="w", padx=14, pady=5)
        ttk.Checkbutton(sidebar, text="Reconocer personas", variable=self.people_var, command=self._save_toggles).pack(anchor="w", padx=14, pady=5)
        ttk.Label(sidebar, text="F8 = parada inmediata", background=PANEL, foreground=MUTED, font=("Segoe UI",8)).pack(anchor="w", padx=14, pady=12)
        ttk.Button(sidebar, text="Buscar actualización", command=self._check_update).pack(fill="x", padx=10, pady=(6,4))

        self.content = ttk.Frame(main, style="Panel.TFrame"); self.content.pack(side="left", fill="both", expand=True)
        self.frames = {}
        self._make_chat(); self._make_game(); self._make_people(); self._make_state(); self._make_dev()
        self._show("chat")

    def _section(self, parent, title, subtitle=None):
        box = ttk.Frame(parent, style="Panel.TFrame", padding=18); box.pack(fill="x")
        ttk.Label(box, text=title, background=PANEL, foreground=TEXT, font=("Segoe UI",16,"bold")).pack(anchor="w")
        if subtitle:
            ttk.Label(box, text=subtitle, background=PANEL, foreground=MUTED, wraplength=820).pack(anchor="w", pady=(5,0))

    def _make_chat(self):
        f = ttk.Frame(self.content, style="Panel.TFrame", padding=16)
        self.frames["chat"] = f
        self._section(
            f,
            "Chat con NARI",
            "Escribe un mensaje y NARI responde. El historial se guarda localmente y es permanente desde la interfaz: no existe un boton ni una funcion de borrado."
        )

        header = tk.Frame(f, bg="#171a21", height=54)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header, text="NARI", bg="#171a21", fg="#f1f3f5",
            font=("Segoe UI", 12, "bold")
        ).pack(side="left", padx=(16,6))

        tk.Label(
            header, text="● en línea", bg="#171a21", fg="#7debb0",
            font=("Segoe UI", 9)
        ).pack(side="left")

        tk.Frame(f, bg="#252a34", height=1).pack(fill="x")

        body = tk.Frame(f, bg="#0f1218")
        body.pack(fill="both", expand=True, pady=(0,10))

        self.chat_canvas = tk.Canvas(
            body, bg="#0f1218", highlightthickness=0, bd=0
        )
        self.chat_scroll = ttk.Scrollbar(
            body, orient="vertical", command=self.chat_canvas.yview
        )
        self.chat_canvas.configure(yscrollcommand=self.chat_scroll.set)
        self.chat_scroll.pack(side="right", fill="y")
        self.chat_canvas.pack(side="left", fill="both", expand=True)

        self.chat_messages = tk.Frame(self.chat_canvas, bg="#0f1218")
        self.chat_window = self.chat_canvas.create_window(
            (0,0), window=self.chat_messages, anchor="nw"
        )

        self.chat_messages.bind(
            "<Configure>",
            lambda e:self.chat_canvas.configure(
                scrollregion=self.chat_canvas.bbox("all")
            )
        )
        self.chat_canvas.bind(
            "<Configure>",
            lambda e:self.chat_canvas.itemconfigure(
                self.chat_window, width=e.width
            )
        )
        self.chat_canvas.bind(
            "<MouseWheel>",
            lambda e:self.chat_canvas.yview_scroll(
                int(-1*(e.delta/120)), "units"
            )
        )

        composer = tk.Frame(f, bg="#121620")
        composer.pack(fill="x")

        self.entry = tk.Entry(
            composer,
            bg="#202631",
            fg="#eef1f5",
            insertbackground="#ffffff",
            relief="flat",
            bd=0,
            font=("Segoe UI", 11)
        )
        self.entry.pack(
            side="left", fill="x", expand=True,
            padx=(12,8), pady=12, ipady=9
        )
        self.entry.bind("<Return>", lambda e:self.send_text())

        tk.Button(
            composer,
            text="Enviar",
            command=self.send_text,
            bg="#5865f2",
            fg="#ffffff",
            activebackground="#4752c4",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            font=("Segoe UI", 10, "bold"),
            padx=18,
            pady=9
        ).pack(side="right", padx=(0,12), pady=12)

        tk.Label(
            f,
            text="Historial permanente • guardado en data/nari.db • sin borrar desde NARI",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8)
        ).pack(anchor="w")

        self._load_chat_history()

    def _load_chat_history(self):
        for child in self.chat_messages.winfo_children():
            child.destroy()
        history = self.memory.chat_history()
        if not history:
            self._append_chat("NARI", "Hola ✨ ¿Qué hacemos?", "nari", persist=False)
            return
        for item in history:
            stamp = time.strftime("%d/%m/%Y %H:%M", time.localtime(float(item["created"])))
            self._append_chat(
                item["speaker"],
                item["text"],
                "me" if item["role"] == "user" else "nari",
                persist=False,
                timestamp=stamp
            )

    def _make_game(self):
        f = ttk.Frame(self.content, style="Panel.TFrame", padding=16); self.frames["game"] = f
        self._section(f, "Juego  /  vídeo en vivo", "NARI mantiene una captura continua del escritorio. El motor de decisión toma fotogramas del flujo vivo, controla teclado/mouse y vuelve a observar. No necesitas tomar capturas manualmente.")
        bar = ttk.Frame(f, style="Panel.TFrame"); bar.pack(fill="x", pady=(0,8))
        ttk.Label(bar, text="Perfil:", background=PANEL, foreground=TEXT).pack(side="left")
        self.game_profile_var = tk.StringVar(value=self.settings.get("game_profile", "generic"))
        self.game_profile = ttk.Combobox(bar, textvariable=self.game_profile_var, state="readonly", values=["generic", "roblox", "jjs", "limbus"], width=12)
        self.game_profile.pack(side="left", padx=(7,10))
        self.game_goal = ttk.Entry(bar); self.game_goal.insert(0, "Juega por tu cuenta y aprende los controles del entorno."); self.game_goal.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="JUGAR", style="Accent.TButton", command=self.start_game).pack(side="left", padx=(8,0))
        ttk.Button(bar, text="PARAR", command=self.stop_game).pack(side="left", padx=(6,0))
        ttk.Button(bar, text="PROBAR W", command=self._test_game_key).pack(side="left", padx=(6,0))
        ttk.Button(bar, text="PROBAR RATÓN", command=self._test_game_mouse).pack(side="left", padx=(6,0))
        ttk.Button(bar, text="PROBAR CÁMARA", command=self._test_game_camera).pack(side="left", padx=(6,0))
        info = ttk.Frame(f, style="Panel.TFrame"); info.pack(fill="x", pady=4)
        self.game_status = tk.StringVar(value="Listo")
        ttk.Label(info, textvariable=self.game_status, background=PANEL, foreground=MUTED).pack(side="left")
        self.game_learning_status = tk.StringVar(value="Aprendizaje: listo")
        ttk.Label(info, textvariable=self.game_learning_status, background=PANEL, foreground=ACCENT2).pack(side="left", padx=(16,0))
        ttk.Label(info, text="   •   F8 detiene todo", background=PANEL, foreground=ACCENT2).pack(side="left")
        self.video_label = ttk.Label(f, background="#080a0e"); self.video_label.pack(fill="both", expand=True, pady=(8,0))

    def _make_people(self):
        f = ttk.Frame(self.content, style="Panel.TFrame", padding=16); self.frames["people"] = f
        self._section(f, "Personas", "Reconocimiento facial local y opcional. Los perfiles y relaciones se quedan en tu PC.")
        row = ttk.Frame(f, style="Panel.TFrame"); row.pack(fill="x", pady=8)
        ttk.Button(row, text="Iniciar cámara", command=self._start_camera).pack(side="left")
        ttk.Button(row, text="Registrar persona", style="Accent.TButton", command=self._enroll_person).pack(side="left", padx=8)
        self.people_video = ttk.Label(f, background="#080a0e"); self.people_video.pack(fill="x", pady=(8,4))
        self.people_list = tk.Listbox(f, bg="#0e1118", fg=TEXT, relief="flat", selectbackground="#2c3342"); self.people_list.pack(fill="both", expand=True)

    def _make_state(self):
        f = ttk.Frame(self.content, style="Panel.TFrame", padding=16); self.frames["state"] = f
        self._section(f, "Estado interno", "Variables persistentes de personalidad simulada. Pueden cambiar por experiencias.")
        self.state_text = tk.Text(f, bg="#0e1118", fg=TEXT, relief="flat", font=("Consolas",10)); self.state_text.pack(fill="both",expand=True,pady=8)

    def _make_dev(self):
        f = ttk.Frame(self.content, style="Panel.TFrame", padding=16); self.frames["dev"] = f
        self._section(f, "Desarrollador", "Puedes observar y cambiar relaciones, memoria privada y configuración. NARI también puede cambiar sus estados mediante experiencias.")
        tabs = ttk.Notebook(f); tabs.pack(fill="both", expand=True)
        emotions = ttk.Frame(tabs, style="Panel.TFrame", padding=10); tabs.add(emotions, text="Personas")
        self.person_choice = ttk.Combobox(emotions, state="readonly", values=[]); self.person_choice.pack(fill="x", pady=(0,8)); self.person_choice.bind("<<ComboboxSelected>>", lambda e:self._load_person_sliders())
        self.slider_frame = ttk.Frame(emotions, style="Panel.TFrame"); self.slider_frame.pack(fill="both",expand=True)
        self.person_status = tk.StringVar(value="Selecciona una persona")
        ttk.Label(emotions,textvariable=self.person_status,background=PANEL,foreground=MUTED).pack(anchor="w",pady=5)
        notes = ttk.Frame(tabs, style="Panel.TFrame", padding=10); tabs.add(notes, text="Privado")
        self.private_text = tk.Text(notes,bg="#0e1118",fg=TEXT,relief="flat",font=("Consolas",9));self.private_text.pack(fill="both",expand=True)
        ttk.Button(notes,text="Actualizar",command=self._refresh_developer).pack(anchor="e",pady=6)
        internal = ttk.Frame(tabs, style="Panel.TFrame", padding=10); tabs.add(internal, text="Interno")
        self.internal_text = tk.Text(internal, bg="#0e1118", fg=TEXT, relief="flat", font=("Consolas",9), wrap="word")
        self.internal_text.pack(fill="both", expand=True)
        system = ttk.Frame(tabs, style="Panel.TFrame", padding=10); tabs.add(system, text="Sistema")
        ttk.Label(system,text="Modelo de texto rápido:",background=PANEL,foreground=TEXT).pack(anchor="w")
        self.model_var = tk.StringVar(value=self.settings.get("text_model","qwen3:1.7b")); ttk.Entry(system,textvariable=self.model_var).pack(fill="x",pady=5)
        ttk.Label(system,text="Modelo visual:",background=PANEL,foreground=TEXT).pack(anchor="w",pady=(8,0))
        self.vision_model_var = tk.StringVar(value=self.settings.get("vision_model","qwen3-vl:2b")); ttk.Entry(system,textvariable=self.vision_model_var).pack(fill="x",pady=5)
        ttk.Label(system,text="Repositorio de actualizaciones (GitHub propietario/repositorio):",background=PANEL,foreground=TEXT).pack(anchor="w",pady=(8,0))
        self.github_repo_var = tk.StringVar(value=self.settings.get("github_repo","zilafeindert/NARI")); ttk.Entry(system,textvariable=self.github_repo_var).pack(fill="x",pady=5)
        ttk.Label(system,text="Fuente oficial: NARI comprueba nuevas versiones automáticamente al iniciar.",background=PANEL,foreground=MUTED,wraplength=760).pack(anchor="w")
        ttk.Button(system,text="Guardar configuración",command=self._save_settings_ui,style="Accent.TButton").pack(anchor="e",pady=10)

    def _show(self, key):
        for frame in self.frames.values(): frame.pack_forget()
        self.frames[key].pack(fill="both", expand=True)
        if key == "people": self._refresh_people()
        if key in {"state","dev"}: self._refresh_developer()

    def _append_chat(self, speaker, text, tag, persist=True, timestamp=None):
        if persist:
            role = "user" if speaker == "Tú" else "assistant"
            try:
                self.memory.add_chat_message(role, speaker, str(text))
            except Exception as exc:
                self._status("Historial: error de guardado")
                return

        def add():
            row = tk.Frame(self.chat_messages, bg="#0f1218")
            row.pack(fill="x", padx=16, pady=(8,1))

            avatar_bg = "#5865f2" if speaker == "Tú" else "#c86bff"
            avatar_text = "T" if speaker == "Tú" else "N"

            avatar = tk.Label(
                row, text=avatar_text,
                bg=avatar_bg, fg="#ffffff",
                font=("Segoe UI", 9, "bold"),
                width=2, height=1
            )
            avatar.pack(side="left", anchor="n", padx=(0,10))

            body = tk.Frame(row, bg="#0f1218")
            body.pack(side="left", fill="x", expand=True)

            top = tk.Frame(body, bg="#0f1218")
            top.pack(fill="x")

            tk.Label(
                top, text=speaker,
                bg="#0f1218", fg="#f1f3f5",
                font=("Segoe UI", 10, "bold")
            ).pack(side="left")

            tk.Label(
                top,
                text="  " + (timestamp or time.strftime("%d/%m/%Y %H:%M")),
                bg="#0f1218", fg="#777f8f",
                font=("Segoe UI", 8)
            ).pack(side="left")

            tk.Label(
                body, text=str(text),
                bg="#0f1218", fg="#d7dbe2",
                justify="left", anchor="w",
                wraplength=760,
                font=("Segoe UI", 10)
            ).pack(fill="x", pady=(2,0))

            self.chat_canvas.update_idletasks()
            self.chat_canvas.configure(
                scrollregion=self.chat_canvas.bbox("all")
            )
            self.chat_canvas.yview_moveto(1.0)

        self.root.after(0, add)

    def _status(self, value):
        try: self.root.after(0, lambda:self.status_var.set(str(value)))
        except Exception: pass

    def _token(self, token):
        pass

    def _run_agent(self, text, from_voice=False):
        if self.busy:
            self._append_chat(
                "NARI",
                "Espera un segundo, todavía estoy respondiendo el mensaje anterior.",
                "nari"
            )
            return
        self.busy = True
        self._append_chat("Tú", text, "me", persist=True)
        self._status("NARI • conectada")
        threading.Thread(
            target=self._agent_thread,
            args=(text,),
            daemon=True
        ).start()

    def _agent_thread(self, text):
        try:
            result = self.agent.chat(text, automation_allowed=self.auto_var.get())
            reply = result.get("reply", "") or "…"
            self._append_chat("NARI", reply, "nari")
            if self.voice_var.get(): self.tts.say(reply)
            log = self.agent.execute_actions(result, self.auto_var.get())
            self._status(("Hecho • " + log[:110]) if log else "🎙 Di «NARI»")
        finally:
            self.busy = False

    def send_text(self):
        text = self.entry.get().strip()
        if not text: return
        self.entry.delete(0,"end")
        self._run_agent(text)

    def _voice_command(self, text):
        text = text.strip()
        if not text:
            self._append_chat("NARI", "¿Mhm? Aquí estoy ✨", "nari")
            if self.voice_var.get(): self.tts.say("¿Mhm? Aquí estoy ✨")
            return
        self._run_agent(text, from_voice=True)

    def _talk_now(self):
        try:
            self.listener.arm_command(7.0)
        except Exception as exc:
            self._status("❌ Voz directa: "+str(exc)[:150])

    def _test_voice(self):
        try:
            self.tts.enabled=True
            self.voice_var.set(True)
            self.settings["voice_enabled"]=True
            save_settings(self.settings)
            self._status("🔊 Probando voz de NARI…")
            self.tts.say("Hola. Soy NARI. Te estoy escuchando.")
        except Exception as exc:
            self._status("❌ Prueba de voz: "+str(exc)[:150])

    def _test_microphone(self):
        try:
            devices=self.listener.device_summary()
            if not devices:
                self._status("❌ No se detectaron entradas de micrófono.")
                return
            names=" | ".join(f"{d['id']}: {d['name']} ({int(d['default_samplerate'])}Hz)" for d in devices[:6])
            self._status("🎙 Entradas detectadas • "+names[:180])
            # Reinicia el listener para aplicar una posible selección/fallback de dispositivo.
            self.listener.stop()
            time.sleep(0.15)
            self.listener.start()
        except Exception as exc:
            self._status("❌ Micrófono: "+str(exc)[:150])

    def _save_toggles(self):
        self.settings["voice_enabled"] = bool(self.voice_var.get())
        self.tts.enabled = bool(self.voice_var.get())
        self.settings["game_autonomy"] = bool(self.auto_var.get())
        self.settings["talk_when_idle"] = bool(self.free_var.get())
        self.settings["recognize_people"] = bool(self.people_var.get())
        save_settings(self.settings)

    def _save_settings_ui(self):
        self.settings["text_model"] = self.model_var.get().strip()
        self.settings["vision_model"] = self.vision_model_var.get().strip()
        self.settings["github_repo"] = self.github_repo_var.get().strip() or "zilafeindert/NARI"
        save_settings(self.settings)
        self.agent.set_settings(self.settings)
        self._status("✅ Configuración guardada")

    def start_game(self):
        if self.game_running:
            return
        profile=self.game_profile_var.get().strip() or "generic"

        self.auto_var.set(True)
        self.settings["game_autonomy"] = True
        self.settings["game_profile"] = profile
        save_settings(self.settings)

        focused,title=self.computer.focus_game(profile)
        if not focused:
            messagebox.showwarning("Juego", "No encontré la ventana del juego. Ábrela y pulsa JUGAR de nuevo.")
            return

        goal=self.game_goal.get().strip() or (
            "Explora el juego, aprende sus controles y completa objetivos visibles."
        )
        if profile == "jjs":
            goal += (
                "
PRIORIDAD JJS: localiza el Dummy de entrenamiento, "
                "mantenlo visible y centrado, acércate y usa M1 cuando esté a distancia de ataque."
            )
        self.game_target_title=title
        self.computer.clear_stop()
        try:
            self.screen.set_target_window(self.computer.target_hwnd)
        except Exception:
            pass
        self.computer.focus_title(title)
        self.computer.minimize_host()
        self.game_running=True
        self.game_cycle=0
        self.last_game_frame_ts=0.0
        self.last_learning_frame=None
        self.last_learning_state=""
        self.last_learning_action=None
        self.last_learning_action_label=""
        self.learning_enabled=bool(self.settings.get("learning_enabled", True))

        try:
            self.agent.game_reset(goal, profile)
        except Exception as exc:
            self._status("⚠ Cerebro de juego: "+str(exc)[:90])

        if self.learning_enabled:
            try:
                self.agent.game_learner.start_session(profile)
                self.game_learning_status.set("Aprendizaje: sesión iniciada")
            except Exception as exc:
                self._status("⚠ Aprendizaje: "+str(exc)[:90])

        with self.game_action_lock:
            self.game_recommended_actions = []
            self.game_recommendation_ts = 0.0

        threading.Thread(target=self._game_loop,args=(profile,),daemon=True).start()
        self.game_status.set(f"ACTIVO • {profile} • foco: {title}")
        self._status(f"🎮 NARI jugando con control cerrado • {profile}")

    def _test_game_key(self):
        profile=self.game_profile_var.get().strip() or "generic"
        self.computer.clear_stop()
        focused,title=self.computer.focus_game(profile)
        if not focused:
            messagebox.showwarning("Entrada", "No encontré la ventana del juego. Abre Roblox/Limbus y vuelve a intentarlo.")
            return
        ok,msg=self.computer.test_key("w")
        self.computer.minimize_host()
        self._status(("✅ " if ok else "❌ ")+msg)
        self.game_status.set(msg)

    def _test_game_mouse(self):
        profile=self.game_profile_var.get().strip() or "generic"
        self.computer.clear_stop()
        focused,title=self.computer.focus_game(profile)
        if not focused:
            messagebox.showwarning("Ratón", "No encontré la ventana del juego.")
            return
        self.computer.focus_window(self.computer.target_hwnd)
        try:
            self.computer.act({"type":"mouse_move_rel","dx":80,"dy":0})
            time.sleep(0.08)
            self.computer.act({"type":"mouse_move_rel","dx":-80,"dy":0})
            self.game_status.set("Ratón: movimiento enviado")
            self._status("🖱️ Movimiento de ratón enviado al juego")
        except Exception as exc:
            self.game_status.set("Ratón: error")
            self._status("❌ Ratón: "+str(exc)[:100])

    def _test_game_camera(self):
        profile=self.game_profile_var.get().strip() or "jjs"
        self.computer.clear_stop()
        focused,title=self.computer.focus_game(profile)
        if not focused:
            messagebox.showwarning("Cámara", "No encontré la ventana del juego.")
            return
        try:
            self.computer.focus_window(self.computer.target_hwnd)
            self.computer.camera_drag(420, 0, 0.10)
            time.sleep(0.12)
            self.game_status.set("Cámara: giro de prueba enviado")
            self._status("🎥 Cámara: giro de 420 px enviado con RMB")
        except Exception as exc:
            self.game_status.set("Cámara: error")
            self._status("❌ Cámara: "+str(exc)[:100])

    def stop_game(self):
        self.game_running=False
        self.computer.stop()
        self.computer.release_all()
        self.computer.clear_stop()
        self.computer.clear_target()
        try:
            self.screen.clear_target_window()
        except Exception:
            pass
        if self.learning_enabled:
            try:
                self.agent.game_learner.end_session()
            except Exception:
                pass
        with self.game_action_lock:
            self.game_recommended_actions=[]
            self.game_recommendation_ts=0.0
        self.last_learning_frame=None
        self.last_learning_state=""
        self.last_learning_action=None
        self.last_learning_action_label=""
        self.game_status.set("Detenido")
        self.game_learning_status.set("Aprendizaje: sesión guardada")
        self._status("🎮 Juego detenido")

    def _game_action_loop(self, profile: str):
        # Compatibilidad: el control real se ejecuta en _game_loop.
        # No se usa una segunda cola de acciones.
        while self.game_running:
            time.sleep(0.25)

    def _game_loop(self, profile: str):
        goal = self.game_goal.get().strip() or "Explora el juego, aprende los controles y completa objetivos visibles."
        previous_action = None
        previous_state = ""
        previous_frame = None
        previous_confidence = 0.0
        previous_observation = ""
        previous_note = ""
        previous_goal_state = ""
        last_decision_ui = 0.0

        while self.game_running:
            if __import__("sys").platform == "win32":
                try:
                    import ctypes
                    if ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000:
                        self._emergency_stop_core()
                        self.root.after(0, lambda: self.game_status.set("DETENIDO • F8"))
                        break
                except Exception:
                    pass

            frame = self.screen.latest_game()
            if frame is None:
                time.sleep(0.03)
                continue

            state = self.agent.game_state_key(frame, profile) if self.learning_enabled else ""

            # El cambio visual bruto se conserva como señal, pero no se usa
            # directamente para entrenar: la vision debe juzgar si la accion
            # realmente ayudo al objetivo.
            raw_reward = 0.0
            reward = 0.0
            if previous_action is not None and previous_frame is not None:
                raw_reward = self.agent.game_learner.frame_reward(previous_frame, frame)

            configured_temporal = max(1, min(4, int(self.settings.get("temporal_frames", 2))))
            temporal = 2 if profile == "jjs" else configured_temporal
            imgs, latest = self.screen.image_bytes(
                temporal,
                max_width=int(self.settings.get("game_analysis_width", 640))
            )
            if not imgs or latest is None:
                time.sleep(0.03)
                continue

            previous_label = self.agent.game_learner.action_key(previous_action) if isinstance(previous_action, dict) else "ninguna"
            try:
                result = self.agent.vision(goal, imgs, profile=profile, previous_action=previous_label, state_key=state)
            except Exception as exc:
                result = {"actions": [], "reply": "", "error": str(exc), "confidence": 0.0, "observation": ""}

            # Recompensa estricta para la accion anterior.
            if previous_action is not None:
                previous_label = self.agent.game_learner.action_key(previous_action)
                recent_keys = self.agent.game_brain.recent_keys(4)
                repeat_count = 0
                for item in reversed(recent_keys):
                    if item == previous_label:
                        repeat_count += 1
                    else:
                        break
                if profile == "jjs":
                    reward = self.agent.game_jjs_reward(
                        raw_reward,
                        previous_action,
                        result,
                        repeat_count,
                        getattr(self.agent.game_brain, "no_progress_count", 0),
                    )
                else:
                    reward = self.agent.game_strict_reward(
                        raw_reward,
                        previous_action,
                        result,
                        repeat_count,
                        getattr(self.agent.game_brain, "no_progress_count", 0),
                    )
                if self.learning_enabled:
                    try:
                        self.agent.game_record(
                            profile, previous_state, previous_action, reward,
                            "strict_closed_loop",
                            f"raw={raw_reward:+.2f}; effect={float(result.get('action_effect',0.0) or 0.0):+.2f}; "
                            f"progress={float(result.get('progress_delta',0.0) or 0.0):+.2f}; "
                            f"hit={bool(result.get('hit_confirmed',False))}; "
                            f"enemy_hp={float(result.get('enemy_health_delta',0.0) or 0.0):+.2f}; "
                            f"player_hp={float(result.get('player_health_delta',0.0) or 0.0):+.2f}",
                        )
                    except Exception:
                        pass
                try:
                    self.agent.game_feedback(
                        previous_observation, previous_action, reward,
                        previous_confidence, previous_note, previous_goal_state
                    )
                except Exception:
                    pass

            candidates = [
                x for x in (result.get("actions") or [])
                if isinstance(x, dict)
                and str(x.get("type","")).lower() not in {"remember","social_update","self_update","drive_update","private_note","done"}
            ][:2]

            # Correccion local de camara para JJS: si el VLM identifica un rival
            # claramente fuera del centro, giramos hacia el rival sin esperar otro ciclo.
            if profile == "jjs" and bool(result.get("target_visible", False)):
                try:
                    target_x = float(result.get("target_center_x", 0.5) or 0.5)
                    target_x = max(0.0, min(1.0, target_x))
                    if abs(target_x - 0.5) > 0.10:
                        dx = int(max(-230, min(230, (target_x - 0.5) * 620)))
                        camera_fix = {
                            "type": "camera_turn",
                            "dx": dx,
                            "dy": 0,
                            "seconds": 0.055,
                        }
                        candidates = [camera_fix] + candidates
                except Exception:
                    pass

            confidence = float(result.get("confidence", 0.0) or 0.0)
            action, source = self.agent.game_choose_action(candidates, confidence, self.game_cycle, frame)

            try:
                exec_result = self.computer.act(action) if self.auto_var.get() else "autonomia apagada"
            except Exception as exc:
                exec_result = "ERROR: " + str(exc)

            decision = self.agent.game_learner.action_key(action)
            decision_note = str(result.get("decision_note", "") or "")
            observation = str(result.get("observation", "") or "")
            goal_state = str(result.get("goal_state", "") or "")

            self.agent.decision_record(profile, goal, result, decision)

            previous_action = action
            previous_state = state
            previous_frame = frame.copy()
            previous_confidence = confidence
            previous_observation = observation
            previous_note = decision_note or source
            previous_goal_state = goal_state

            stats = self.agent.game_stats(profile) if self.learning_enabled else {"experiences":0,"avg_reward":0.0}
            status_text = (
                f"ACTIVO • {profile} • ciclo {self.game_cycle} • accion: {decision} • "
                f"confianza {confidence:.2f} • Δrecompensa {reward:+.2f} • visual {raw_reward:+.2f} • {source}"
            )
            self.root.after(0, lambda s=status_text: self.game_status.set(s))

            if self.learning_enabled:
                learn_text = (
                    f"Aprendizaje • {stats['experiences']} exp • media {stats['avg_reward']:+.2f} • "
                    f"repeticion {getattr(self.agent.game_brain,'stuck_count',0)}"
                )
                self.root.after(0, lambda s=learn_text: self.game_learning_status.set(s))

            now=time.monotonic()
            # El chat muestra decisiones utiles, no errores tecnicos internos del VLM.
            # Los fallos de percepcion quedan en el estado y en el indicador de juego.
            if (observation or decision_note or goal_state) and not result.get("error") and now-last_decision_ui >= 0.65:
                visible=f"🎮 {decision} • {decision_note or observation or goal_state}"
                self._append_chat("NARI", visible[:420], "nari")
                last_decision_ui=now

            if result.get("error"):
                self._status("⚠️ visión: " + str(result["error"])[:100])

            self.game_cycle += 1
            time.sleep(0.025)
    def _refresh_ui(self):
        self.computer.track_foreground()
        if self.settings.get("show_live_preview", True):
            im = self.screen.tk_image(max_w=790)
            if im: self.video_label.configure(image=im); self.video_label.image = im
        if self.people.running:
            cur = self.people.current_frame()
            if cur and cur[0] is not None:
                from PIL import Image, ImageTk
                frame = cur[0]; h,w = frame.shape[:2]; target_w=520; ratio=min(1.0,target_w/max(1,w))
                if ratio < 1:
                    import cv2; frame=cv2.resize(frame,(int(w*ratio),int(h*ratio)))
                im2=ImageTk.PhotoImage(Image.fromarray(frame)); self.people_video.configure(image=im2); self.people_video.image=im2
            if self.settings.get("recognize_people", False):
                names=self.people.match(self.memory.person_embeddings())
                if names: self._status("👤 Visible: " + ", ".join(names))
        self.root.after(33,self._refresh_ui)

    def _start_camera(self):
        if self.people.start(0): self._status("📷 Cámara activa")
        else: messagebox.showerror("Cámara", "No pude abrir la cámara o faltan los modelos faciales correctos.")

    def _enroll_person(self):
        if not self.people.running:
            self._start_camera()
            time.sleep(0.5)
        name=simpledialog.askstring("Registrar persona","Nombre del perfil:")
        if not name: return
        emb=self.people.enroll(name)
        if emb is None: messagebox.showwarning("Persona","No encontré una cara clara."); return
        self.memory.upsert_person(name, emb); self._refresh_people(); self._status(f"👤 {name} registrado")

    def _refresh_people(self):
        self.people_list.delete(0,"end")
        ps=self.memory.people()
        for p in ps:
            self.people_list.insert("end",f"{p['name']}  • afecto {p['affection']:+.2f} • confianza {p['trust']:+.2f} • respeto {p['respect']:+.2f}")
        names=[p['name'] for p in ps]; self.person_choice["values"]=names
        if names and not self.person_choice.get(): self.person_choice.set(names[0])
        self._load_person_sliders()

    def _load_person_sliders(self):
        for child in self.slider_frame.winfo_children(): child.destroy()
        person=self.memory.person(self.person_choice.get())
        if not person: return
        self.emotion_sliders={}
        for k in PEOPLE_KEYS:
            value=tk.DoubleVar(value=float(person.get(k,0)))
            ttk.Label(self.slider_frame,text=k,background=PANEL,foreground=TEXT).pack(anchor="w")
            ttk.Scale(self.slider_frame,from_=-1,to=1,orient="horizontal",variable=value).pack(fill="x",pady=(0,4))
            self.emotion_sliders[k]=value
        ttk.Button(self.slider_frame,text="Aplicar cambios",command=self._apply_person_sliders,style="Accent.TButton").pack(anchor="e",pady=8)
        self.person_status.set(f"Perfil: {person['name']}")

    def _apply_person_sliders(self):
        person=self.person_choice.get(); current=self.memory.person(person)
        if not current:return
        self.memory.adjust_person(person,{k:float(self.emotion_sliders[k].get())-float(current.get(k,0)) for k in PEOPLE_KEYS})
        self._refresh_people(); self._status(f"👤 Perfil actualizado: {person}")

    def _refresh_developer(self):
        state=self.memory.self_state(); drives=self.memory.drives()
        self.state_text.delete("1.0","end"); self.state_text.insert("end","ESTADO\n\n")
        for k,v in state.items(): self.state_text.insert("end",f"{k:14} {v:.3f}\n")
        self.state_text.insert("end","\nIMPULSOS\n\n")
        for n,s,r in drives: self.state_text.insert("end",f"{n:14} {s:.3f}  {r}\n")
        self.private_text.delete("1.0","end"); self.private_text.insert("end","\n".join(self.memory.private_notes(20)))
        try:
            self.internal_text.delete("1.0","end")
            latest=self.agent.decision_latest()
            rows=self.agent.decision_recent(12)
            if latest:
                self.internal_text.insert(
                    "end",
                    "DECISIÓN ACTUAL\n\n"
                    f"Objetivo: {latest.get('goal','')}\n"
                    f"Observación: {latest.get('observation','')}\n"
                    f"Plan: {latest.get('plan','')}\n"
                    f"Acción: {latest.get('decision','')}\n"
                    f"Confianza: {float(latest.get('confidence',0.0)):.2f}\n\n"
                    "HISTORIAL RECIENTE\n\n"
                )
                for row in reversed(rows):
                    stamp=time.strftime("%H:%M:%S",time.localtime(float(row.get('created',time.time()))))
                    self.internal_text.insert(
                        "end",
                        f"[{stamp}] {row.get('decision','')}\n"
                        f"  plan: {row.get('plan','')}\n"
                        f"  obs: {row.get('observation','')}\n\n"
                    )
            else:
                self.internal_text.insert("end","Aún no hay decisiones internas registradas.")
        except Exception as exc:
            self.internal_text.delete("1.0","end")
            self.internal_text.insert("end","No se pudo cargar el estado interno: "+str(exc))
        self._refresh_people()

    def _autonomous_reflection_loop(self):
        # Reflexión privada lenta: no compite con Ollama durante el juego.
        while True:
            try:
                time.sleep(45)
                if getattr(self, "game_running", False) or getattr(self, "busy", False):
                    continue
                value=self.agent.autonomous_reflection()
                if value:
                    self._status("🧠 Pensamiento privado actualizado")
            except Exception:
                time.sleep(10)

    def _idle_tick(self):
        if self.free_var.get() and not self.busy and not self.game_running:
            threading.Thread(target=self._free_agent_tick, daemon=True).start()
        self.root.after(30000,self._idle_tick)

    def _free_agent_tick(self):
        self.memory.adjust_self({"energy":-0.002,"curiosity":0.006})

    def _auto_update_check(self):
        repo=str(self.settings.get("github_repo","zilafeindert/NARI")).strip() or "zilafeindert/NARI"
        def work():
            try:
                info=check_updates(repo=repo)
                latest=str(info.get("version",APP_VERSION))
                if latest and _is_newer(latest, APP_VERSION):
                    self.root.after(0,lambda:self._offer_update(info))
            except Exception:
                self._status("Actualizaciones: sin conexión")
        threading.Thread(target=work,daemon=True).start()

    def _offer_update(self, info):
        latest=str(info.get("version",APP_VERSION)); notes=str(info.get("notes", ""))[:500]
        if messagebox.askyesno("NARI: actualización disponible",f"NARI {latest} está disponible.\n\n{notes}\n\n¿Actualizar?"):
            try:
                marker=DATA/"pending_update.json"
                marker.write_text(json.dumps({"zip_url":info.get("zip_url",""),"version":latest},ensure_ascii=False),encoding="utf-8")
                launcher=ROOT/"NARI_ACTUALIZAR_Y_REINICIAR.bat"
                subprocess.Popen(["cmd","/c",str(launcher)],cwd=str(ROOT),creationflags=getattr(subprocess,"CREATE_NEW_CONSOLE",0))
                self.root.after(400,self.close)
            except Exception as e:
                messagebox.showerror("NARI",str(e))

    def _check_update(self):
        repo=str(self.settings.get("github_repo","zilafeindert/NARI")).strip() or "zilafeindert/NARI"
        self.settings["github_repo"]=repo; save_settings(self.settings)
        threading.Thread(target=self._manual_update_thread,args=(repo,),daemon=True).start()

    def _manual_update_thread(self,repo):
        try:
            info=check_updates(repo=repo); latest=str(info.get("version",APP_VERSION))
            if not _is_newer(latest, APP_VERSION):
                self._status("✅ NARI ya está actualizada"); return
            self.root.after(0,lambda:self._offer_update(info))
        except Exception as e:
            self.root.after(0,lambda:messagebox.showerror("Actualizaciones",str(e)))

    def _emergency_stop_core(self):
        self.game_running=False
        try: self.computer.release_all()
        except Exception: pass
        try: self.agent.stop()
        except Exception: pass
        try: self.computer.stop()
        except Exception: pass
        try:
            if self.learning_enabled:
                self.agent.game_learner.end_session()
        except Exception:
            pass

    def emergency_stop(self):
        self._emergency_stop_core()
        self._status("🛑 Parada inmediata")
        try:
            self.game_status.set("DETENIDO")
            self.game_learning_status.set("Aprendizaje: sesión guardada")
        except Exception:
            pass

    def close(self):
        self.global_hotkey_running=False
        try:self.emergency_stop()
        except Exception:pass
        for obj in (self.listener,self.screen,self.people,self.tts):
            try:obj.stop()
            except Exception:pass
        try:self.root.destroy()
        except Exception:pass

    def run(self):
        self.root.mainloop()
