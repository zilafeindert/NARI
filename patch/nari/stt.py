from __future__ import annotations
import json, queue, threading, time, difflib, shutil, tempfile, zipfile, requests
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
        self.force_command_until=0.0
        self.last_audio_time=0.0
        self.audio_level=0.0

    def set_ignore(self, value):
        self.ignore=bool(value)

    def arm_command(self, seconds=7.0):
        """Activa una captura de voz inmediata sin exigir la palabra de activación."""
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        except Exception:
            pass
        self.force_command_until=time.monotonic()+max(3.0,float(seconds))
        self.last_audio_time=time.monotonic()
        self.on_status("🎙 HABLAR AHORA • tienes unos segundos para hablar")

    def _ensure_model(self):
        if self.model_dir.exists():
            # Un modelo Vosk válido contiene al menos alguno de estos directorios.
            names={p.name for p in self.model_dir.iterdir() if p.is_dir()}
            if {"am","conf","graph"} & names:
                return

        url="https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
        parent=self.model_dir.parent
        parent.mkdir(parents=True,exist_ok=True)
        tmp=Path(tempfile.mkdtemp(prefix="nari_vosk_"))
        archive=tmp/"model.zip"
        extract=tmp/"extract"
        try:
            self.on_status("🎙 Descargando modelo de voz en español (~39 MB)…")
            with requests.get(
                url,
                stream=True,
                timeout=120,
                headers={"User-Agent":"NARI-Vosk-Bootstrap/5.2.10"},
            ) as response:
                response.raise_for_status()
                with archive.open("wb") as fh:
                    for chunk in response.iter_content(1024*1024):
                        if chunk:
                            fh.write(chunk)

            with zipfile.ZipFile(archive) as zf:
                bad=zf.testzip()
                if bad:
                    raise RuntimeError("ZIP del modelo dañado: "+bad)

                root_extract=extract.resolve()
                for member in zf.infolist():
                    target=(extract/member.filename).resolve()
                    try:
                        target.relative_to(root_extract)
                    except ValueError:
                        raise RuntimeError("ZIP del modelo invalido: ruta fuera del destino.")
                    if member.filename.endswith("/"):
                        target.mkdir(parents=True,exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with zf.open(member,"r") as src, target.open("wb") as dst:
                        shutil.copyfileobj(src,dst)

            candidates=[p for p in extract.iterdir() if p.is_dir()]
            if not candidates:
                raise RuntimeError("El ZIP no contiene una carpeta de modelo.")

            source=next(
                (p for p in candidates if p.name=="vosk-model-small-es-0.42"),
                candidates[0],
            )
            if self.model_dir.exists():
                shutil.rmtree(self.model_dir,ignore_errors=True)
            shutil.copytree(source,self.model_dir)
            self.on_status("✅ Modelo de voz instalado automáticamente.")
        finally:
            shutil.rmtree(tmp,ignore_errors=True)


    def start(self):
        if self.running and self.thread and self.thread.is_alive():
            return
        if sd is None: raise RuntimeError("sounddevice no esta instalado")
        if Model is None: raise RuntimeError("Vosk no esta instalado")
        self._ensure_model()

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
            if status:
                self.on_status("⚠️ Audio: "+str(status)[:140])
            raw=bytes(indata)
            self.last_audio_time=time.monotonic()
            try:
                samples=np.frombuffer(raw,dtype=np.int16)
                if samples.size:
                    self.audio_level=float(np.sqrt(np.mean(samples.astype(np.float32)**2)))
            except Exception:
                pass
            self.q.put_nowait(raw)
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
            raw = rec.PartialResult() if partial else rec.Result()
            obj = json.loads(raw)
            return " ".join(str(obj.get("partial" if partial else "text","")).split()).lower()
        except Exception:
            return ""

    @staticmethod
    def _final_result(rec):
        try:
            obj = json.loads(rec.FinalResult())
            return " ".join(str(obj.get("text","")).split()).lower()
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
                            # Bloques cortos = menor latencia al detectar "NARI",
                            # especialmente mientras hay audio de una llamada/juego.
                            blocksize=max(320,int(rate/40)),
                            dtype="int16",
                            channels=1,
                            callback=self._cb,
                            device=device,
                            latency="low",
                        )
                        # Arranque explícito: evita depender del valor por defecto de
                        # sounddevice y garantiza que el callback empiece a recibir audio.
                        stream.start()
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
            try:
                wake_rec=KaldiRecognizer(
                    self.model,16000,
                    '["nari","nary","narí","narii","nariy"]'
                )
            except Exception:
                wake_rec=KaldiRecognizer(self.model,16000)
            command_rec=None
            command_parts=[]
            active=False
            started=0.0
            last_speech=0.0
            last_ui=0.0

            while self.running:
                try:
                    data=self.q.get(timeout=0.15)
                except queue.Empty:
                    now=time.monotonic()
                    if (
                        not active
                        and self.force_command_until > 0
                        and now >= self.force_command_until
                    ):
                        self.force_command_until=0.0
                        self.on_status("❌ No llegó audio del micrófono durante la captura.")
                    elif (
                        not active
                        and self.force_command_until > 0
                        and now-self.last_audio_time > 0.9
                    ):
                        self.on_status("⚠️ Captura armada, pero no están llegando datos del micrófono.")
                    continue

                data=self._resample(data, actual_rate, 16000)

                if self.ignore:
                    continue

                now=time.monotonic()
                direct_mode = (self.force_command_until > 0)

                if not active and direct_mode:
                    active=True
                    started=now
                    last_speech=now
                    command_parts=[]
                    command_rec=KaldiRecognizer(self.model,16000)
                    self.on_status("🎙 Micrófono recibiendo audio • habla ahora")
                    idle_rec=KaldiRecognizer(self.model,16000)
                    # Procesa este mismo bloque: antes se descartaba y podía perder
                    # el inicio de la frase.
                if not active:
                    # Detector dedicado: en llamadas, Discord/Roblox y ruido de
                    # juego evita que el texto general "se coma" la palabra NARI.
                    wake_final=""
                    try:
                        wake_accepted=wake_rec.AcceptWaveform(data)
                        wake_final=self._result(wake_rec,False) if wake_accepted else ""
                        wake_partial=self._result(wake_rec,True) if not wake_accepted else ""
                    except Exception:
                        wake_partial=""
                    if wake_final:
                        candidate_wake=wake_final
                    else:
                        candidate_wake=wake_partial

                    accepted=idle_rec.AcceptWaveform(data)
                    final=self._result(idle_rec,False) if accepted else ""
                    partial=self._result(idle_rec,True) if not accepted else ""

                    if final:
                        self.on_status(f"🎙 Escuché: {final[-90:]}")
                    elif partial and now-last_ui>0.50:
                        self.on_status(f"🎙 …{partial[-80:]}")
                        last_ui=now

                    candidate=" ".join(x for x in (candidate_wake,final,partial) if x).strip()
                    if self._contains_wake(candidate):
                        active=True
                        started=now
                        last_speech=now
                        command_parts=[]
                        command_rec=KaldiRecognizer(self.model,16000)

                        # Conserva palabras pronunciadas junto a "NARI" en el mismo bloque.
                        command_rec.AcceptWaveform(data)
                        carried_final=self._result(command_rec,False)
                        carried_partial=self._result(command_rec,True)
                        carried=" ".join(x for x in (carried_final,carried_partial) if x)
                        carried=self._remove_wake(carried)
                        if carried:
                            command_parts.append(carried)
                            last_speech=now

                        self.on_status("🎙 NARI activada • habla ahora")
                        idle_rec=KaldiRecognizer(self.model,16000)
                    continue

                accepted=command_rec.AcceptWaveform(data)
                if accepted:
                    final=self._result(command_rec,False)
                    if final:
                        cleaned=self._remove_wake(final)
                        if cleaned:
                            command_parts.append(cleaned)
                            last_speech=now
                            self.on_status(f"🗣 {cleaned[-90:]}")
                else:
                    partial=self._remove_wake(self._result(command_rec,True))
                    if partial:
                        last_speech=now
                        if now-last_ui>0.25:
                            self.on_status(f"🗣 {partial[-90:]}")
                            last_ui=now

                direct_finished = self.force_command_until > 0 and now >= self.force_command_until
                finished=(
                    direct_finished
                    or (now-started)>=self.max_seconds
                    or (last_speech>0 and now-last_speech>=self.silence_seconds)
                )

                if finished:
                    tail=self._remove_wake(self._final_result(command_rec))
                    if tail:
                        command_parts.append(tail)

                    text=" ".join(x for x in command_parts if x).strip()
                    active=False
                    command_rec=None
                    command_parts=[]
                    idle_rec=KaldiRecognizer(self.model,16000)
                    try:
                        wake_rec=KaldiRecognizer(self.model,16000,'["nari","nary","narí","narii","nariy"]')
                    except Exception:
                        wake_rec=KaldiRecognizer(self.model,16000)
                    self.force_command_until=0.0

                    if text:
                        self.on_status(f"✅ Entendí: {text[:120]}")
                        self.on_command(text)
                    else:
                        self.on_status("✨ No entendí la frase. Vuelve a decir «NARI» y luego habla.")

                    self.on_status(f'🎙 Escuchando «{self.wake_word}»')

        finally:
            if stream is not None:
                try: stream.stop()
                except Exception: pass
                try: stream.close()
                except Exception: pass
