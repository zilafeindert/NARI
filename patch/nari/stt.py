from __future__ import annotations
import json, queue, threading, time, difflib
from pathlib import Path
import numpy as np

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    from vosk import Model, KaldiRecognizer
except Exception:
    Model = KaldiRecognizer = None


class VoiceListener:
    """Escucha continuamente con Vosk local y VAD adaptativo."""
    WAKE_ALIASES = {"nari", "nary", "nariii", "nariy", "narí", "narié"}

    def __init__(
        self, model_dir: Path, wake_word: str, silence_seconds: float,
        max_seconds: float, on_command, on_status, device=None
    ):
        self.model_dir = Path(model_dir)
        self.wake_word = str(wake_word or "nari").lower().strip()
        self.silence_seconds = float(silence_seconds)
        self.max_seconds = float(max_seconds)
        self.on_command = on_command
        self.on_status = on_status
        self.device = device
        self.running = False
        self.thread = None
        self.q = queue.Queue(maxsize=240)
        self.model = None
        self.noise_floor = 70.0

    def start(self):
        if self.running:
            return
        if sd is None:
            raise RuntimeError("sounddevice no esta instalado")
        if Model is None:
            raise RuntimeError("Vosk no esta instalado")
        if not self.model_dir.exists():
            raise FileNotFoundError(f"No existe el modelo de voz: {self.model_dir}")

        if self.device is not None:
            try:
                devices = sd.query_devices()
                if isinstance(self.device, str):
                    matches = [i for i, d in enumerate(devices) if self.device.lower() in str(d.get("name", "")).lower() and d.get("max_input_channels", 0) > 0]
                    if matches:
                        self.device = matches[0]
            except Exception:
                pass

        self.model = Model(str(self.model_dir))
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="NARI-voice", daemon=True)
        self.thread.start()
        self.on_status(f'🎙 Escuchando «{self.wake_word}»')

    def stop(self):
        self.running = False
        self.on_status("🔇 Micrófono apagado")

    def _cb(self, indata, frames, time_info, status):
        if not self.running:
            return
        try:
            self.q.put_nowait(bytes(indata))
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(bytes(indata))
            except queue.Empty:
                pass

    @staticmethod
    def _clean(text):
        out = []
        for w in " ".join(str(text).lower().strip().split()).split():
            if w in {"z", "zet", "ze", "zz"}:
                continue
            if w in {"eh", "em", "mm", "mmm"}:
                continue
            out.append(w)
        return " ".join(out)

    def _wake_match(self, token):
        token = self._clean(token)
        if not token:
            return False
        if token in self.WAKE_ALIASES:
            return True
        return difflib.SequenceMatcher(None, token, self.wake_word).ratio() >= 0.68

    def _contains_wake(self, text):
        return any(self._wake_match(w) for w in self._clean(text).split())

    def _remove_wake(self, text):
        words = []
        removed = False
        for token in self._clean(text).split():
            if not removed and self._wake_match(token):
                removed = True
                continue
            words.append(token)
        return " ".join(words).strip(" ,.!?¿¡")

    @staticmethod
    def _energy(raw):
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(x * x))) if x.size else 0.0

    def _transcribe(self, chunks):
        rec = KaldiRecognizer(self.model, 16000)
        for chunk in chunks:
            rec.AcceptWaveform(chunk)
        try:
            return self._clean(json.loads(rec.FinalResult()).get("text", ""))
        except Exception:
            return ""

    def _loop(self):
        try:
            kwargs = {
                "samplerate": 16000,
                "blocksize": 1600,
                "dtype": "int16",
                "channels": 1,
                "callback": self._cb,
            }
            if self.device is not None:
                kwargs["device"] = self.device

            with sd.RawInputStream(**kwargs):
                listening = False
                utterance = []
                started = 0.0
                last_voice = 0.0
                rec = None
                wake_seen = False
                last_status = 0.0

                while self.running:
                    try:
                        data = self.q.get(timeout=0.15)
                    except queue.Empty:
                        continue

                    now = time.monotonic()
                    level = self._energy(data)

                    if not listening:
                        self.noise_floor = 0.98 * self.noise_floor + 0.02 * level

                    threshold = max(55.0, min(240.0, self.noise_floor * 1.65 + 28.0))
                    active = level > threshold

                    if active and not listening:
                        listening = True
                        started = now
                        last_voice = now
                        utterance = []
                        wake_seen = False
                        rec = KaldiRecognizer(self.model, 16000)

                    if not listening:
                        continue

                    utterance.append(data)
                    rec.AcceptWaveform(data)

                    try:
                        partial = self._clean(json.loads(rec.PartialResult()).get("partial", ""))
                    except Exception:
                        partial = ""

                    if partial and self._contains_wake(partial):
                        wake_seen = True

                    if partial and now - last_status > 0.4:
                        self.on_status("🗣 " + partial[-90:])
                        last_status = now

                    if active:
                        last_voice = now

                    timed_out = (now - started) >= self.max_seconds
                    silence = (now - last_voice) >= self.silence_seconds

                    if timed_out or silence:
                        text = self._transcribe(utterance)
                        if wake_seen or self._contains_wake(text):
                            command = self._remove_wake(text)
                            if command:
                                self.on_command(command)
                            else:
                                self.on_status("✨ Sí, aquí estoy")
                        listening = False
                        utterance = []
                        rec = None
                        wake_seen = False
                        self.on_status(f'🎙 Escuchando «{self.wake_word}»')
        except Exception as exc:
            self.on_status(f"❌ Voz: {exc}")
            self.running = False
