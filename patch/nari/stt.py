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
    WAKE_ALIASES = {"nari","nary","narí","narii","nariy"}

    def __init__(self, model_dir, wake_word, silence_seconds, max_seconds, on_command, on_status, device=None):
        self.model_dir=Path(model_dir)
        self.wake_word=str(wake_word or "nari").lower().strip()
        self.silence_seconds=float(silence_seconds)
        self.max_seconds=float(max_seconds)
        self.on_command=on_command
        self.on_status=on_status
        self.device=device
        self.running=False
        self.thread=None
        self.q=queue.Queue(maxsize=300)
        self.model=None
        self.ignore=False

    def set_ignore(self, value):
        self.ignore=bool(value)

    def start(self):
        if self.running: return
        if sd is None: raise RuntimeError("sounddevice no esta instalado")
        if Model is None: raise RuntimeError("Vosk no esta instalado")
        if not self.model_dir.exists(): raise FileNotFoundError(f"No existe el modelo de voz: {self.model_dir}")

        try:
            if self.device is None:
                default_in=sd.default.device[0]
                if default_in is not None and default_in >= 0:
                    self.device=int(default_in)
            elif isinstance(self.device,str):
                matches=[]
                for i,d in enumerate(sd.query_devices()):
                    if d.get("max_input_channels",0)>0 and self.device.lower() in str(d.get("name","")).lower():
                        matches.append(i)
                if matches: self.device=matches[0]
        except Exception:
            pass

        self.model=Model(str(self.model_dir))
        self.running=True
        self.thread=threading.Thread(target=self._loop,name="NARI-voice",daemon=True)
        self.thread.start()
        try:
            name=sd.query_devices(self.device)["name"] if self.device is not None else "dispositivo predeterminado"
        except Exception:
            name="dispositivo predeterminado"
        self.on_status(f'🎙 Escuchando «{self.wake_word}» • {name}')

    def stop(self):
        self.running=False
        self.on_status("🔇 Micrófono apagado")

    def _cb(self,indata,frames,time_info,status):
        if not self.running: return
        try:
            self.q.put_nowait(bytes(indata))
        except queue.Full:
            try:
                self.q.get_nowait()
                self.q.put_nowait(bytes(indata))
            except Exception:
                pass

    @staticmethod
    def _clean(text):
        return " ".join(str(text or "").lower().strip().split())

    def _wake_match(self,word):
        word=self._clean(word)
        if not word: return False
        if word in self.WAKE_ALIASES: return True
        return difflib.SequenceMatcher(None,word,self.wake_word).ratio() >= 0.67

    def _contains_wake(self,text):
        return any(self._wake_match(w) for w in self._clean(text).split())

    def _remove_wake(self,text):
        out=[]; removed=False
        for w in self._clean(text).split():
            if not removed and self._wake_match(w):
                removed=True
                continue
            out.append(w)
        return " ".join(out).strip(" ,.!?¿¡")

    @staticmethod
    def _result(rec, partial=False):
        try:
            obj=json.loads(rec.PartialResult() if partial else rec.FinalResult())
            return " ".join(str(obj.get("partial" if partial else "text","")).split()).lower()
        except Exception:
            return ""

    def _input_candidates(self):
        if sd is None:
            return []
        candidates=[]
        if self.device is not None:
            try:
                idx=int(self.device)
                if idx>=0:
                    candidates.append(idx)
            except Exception:
                pass
        try:
            default_in=sd.default.device[0]
            if default_in is not None and int(default_in)>=0 and int(default_in) not in candidates:
                candidates.append(int(default_in))
        except Exception:
            pass
        try:
            for i,d in enumerate(sd.query_devices()):
                if int(d.get("max_input_channels",0) or 0)>0 and i not in candidates:
                    candidates.append(i)
        except Exception:
            pass
        return candidates

    def _open_stream(self):
        errors=[]
        for device in self._input_candidates():
            try:
                info=sd.query_devices(device)
                default_rate=float(info.get("default_samplerate",16000) or 16000)
                rates=[]
                for rate in (16000, int(round(default_rate)), 48000, 44100):
                    if rate>0 and rate not in rates:
                        rates.append(rate)
                for rate in rates:
                    try:
                        stream=sd.RawInputStream(
                            samplerate=rate,
                            blocksize=max(800,int(rate/10)),
                            dtype="int16",
                            channels=1,
                            callback=self._cb,
                            device=device,
                        )
                        self.device=int(device)
                        return stream, int(rate), str(info.get("name",device))
                    except Exception as exc:
                        errors.append(f"{info.get('name',device)} @ {rate}Hz: {exc}")
            except Exception as exc:
                errors.append(f"dispositivo {device}: {exc}")
        detail=" | ".join(errors[-5:])
        raise RuntimeError("No pude abrir ningún micrófono." + ((" " + detail) if detail else ""))

    @staticmethod
    def _resample(data, source_rate, target_rate=16000):
        if int(source_rate)==int(target_rate):
            return data
        try:
            src=np.frombuffer(data,dtype=np.int16)
            if src.size==0:
                return data
            n=max(1,int(round(src.size*float(target_rate)/float(source_rate))))
            x=np.linspace(0,1,src.size,endpoint=False)
            xp=np.linspace(0,1,n,endpoint=False)
            out=np.interp(xp,x,src.astype(np.float32))
            return np.clip(out,-32768,32767).astype(np.int16).tobytes()
        except Exception:
            return data

    def device_summary(self):
        if sd is None:
            return []
        out=[]
        try:
            for i,d in enumerate(sd.query_devices()):
                if int(d.get("max_input_channels",0) or 0)>0:
                    out.append({
                        "id": i,
                        "name": str(d.get("name","")),
                        "default_samplerate": float(d.get("default_samplerate",0) or 0),
                    })
        except Exception:
            pass
        return out

    def _loop(self):
        stream=None
        try:
            stream, actual_rate, device_name = self._open_stream()
            self.on_status(f"🎙 Mic activo • {device_name} • {actual_rate} Hz")
            idle_rec=KaldiRecognizer(self.model,16000)
            command_rec=None
            active=False
            started=0.0
            last_speech=0.0
            last_ui=0.0
            while self.running:
                try:
                    data=self.q.get(timeout=0.15)
                except queue.Empty:
                    continue

                data=self._resample(data, actual_rate, 16000)

                if self.ignore:
                    continue

                now=time.monotonic()

                if not active:
                    accepted=idle_rec.AcceptWaveform(data)
                    final=self._result(idle_rec,False) if accepted else ""
                    partial=self._result(idle_rec,True) if not accepted else ""
                    if partial and now-last_ui>0.35:
                        self.on_status(f"🎙 {partial[-80:]}")
                        last_ui=now
                    candidate=final or partial
                    if self._contains_wake(candidate):
                        active=True
                        started=now
                        last_speech=now
                        command_rec=KaldiRecognizer(self.model,16000)
                        command_rec.AcceptWaveform(data)
                        command_text=self._remove_wake(self._result(command_rec,True))
                        if command_text:
                            last_speech=now
                        self.on_status("🎙 NARI activada • habla ahora")
                        idle_rec=KaldiRecognizer(self.model,16000)
                    continue

                command_rec.AcceptWaveform(data)
                partial=self._remove_wake(self._result(command_rec,True))
                if partial:
                    last_speech=now
                    if now-last_ui>0.35:
                        self.on_status(f"🗣 {partial[-80:]}")
                        last_ui=now

                finished=(now-started)>=self.max_seconds or (last_speech>0 and now-last_speech>=self.silence_seconds)
                if finished:
                    text=self._remove_wake(self._result(command_rec,False))
                    active=False
                    command_rec=None
                    idle_rec=KaldiRecognizer(self.model,16000)
                    if text:
                        self.on_command(text)
                    else:
                        self.on_status("✨ Sí, aquí estoy")
                    self.on_status(f'🎙 Escuchando «{self.wake_word}»')
        except Exception as exc:
            self.on_status("❌ Voz: "+str(exc))
            self.running=False
        finally:
            if stream is not None:
                try: stream.stop()
                except Exception: pass
                try: stream.close()
                except Exception: pass
