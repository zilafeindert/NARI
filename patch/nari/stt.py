from __future__ import annotations
import json, queue, time, threading
from pathlib import Path
try:
    import sounddevice as sd
except Exception:
    sd=None
try:
    from vosk import Model, KaldiRecognizer
except Exception:
    Model=KaldiRecognizer=None

class VoiceListener:
    def __init__(self,model_dir,wake_word,silence_seconds,max_seconds,callback,status):
        self.model_dir=Path(model_dir); self.wake_word=wake_word.lower(); self.silence_seconds=float(silence_seconds); self.max_seconds=float(max_seconds)
        self.callback=callback; self.status=status; self.running=False; self.thread=None; self.ignore=False; self.q=queue.Queue(maxsize=40)

    def set_ignore(self,v): self.ignore=bool(v)

    def start(self):
        if sd is None or Model is None: raise RuntimeError("Faltan sounddevice/Vosk")
        if not self.model_dir.exists(): raise RuntimeError("Falta el modelo Vosk: "+str(self.model_dir))
        self.running=True; self.thread=threading.Thread(target=self._loop,daemon=True); self.thread.start()

    def stop(self): self.running=False

    def _loop(self):
        model=Model(str(self.model_dir)); rec=KaldiRecognizer(model,16000); rec.SetWords(True)
        audio_q=self.q
        def cb(indata,frames,time_info,status):
            if self.running:
                try: audio_q.put_nowait(bytes(indata))
                except queue.Full:
                    try: audio_q.get_nowait(); audio_q.put_nowait(bytes(indata))
                    except: pass
        with sd.RawInputStream(samplerate=16000,blocksize=1600,dtype='int16',channels=1,callback=cb):
            buffer=[]; active=False; last_voice=time.monotonic(); heard=False; start=0
            while self.running:
                data=audio_q.get()
                if self.ignore: continue
                rec.AcceptWaveform(data)
                partial=json.loads(rec.PartialResult()).get("partial","").lower()
                final=json.loads(rec.Result()).get("text","").lower()
                text=(final or partial).strip()
                if not active:
                    if self.wake_word in text:
                        active=True; heard=True; start=time.monotonic(); last_voice=time.monotonic(); buffer=[]
                        self.status("🎙 NARI activado…")
                    continue
                if text:
                    cleaned=text.replace(self.wake_word," ").strip()
                    if cleaned: buffer.append(cleaned); last_voice=time.monotonic()
                now=time.monotonic()
                if now-start>self.max_seconds or (heard and now-last_voice>=self.silence_seconds):
                    phrase=" ".join(buffer).strip()
                    active=False; heard=False; buffer=[]
                    self.callback(phrase)
                    rec.Reset()
