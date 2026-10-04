from __future__ import annotations
import os
import subprocess
import threading
from pathlib import Path

class TTS:
    def __init__(self,voice_file=None,enabled=True):
        self.enabled=bool(enabled)
        self.on_speaking=None
        self.on_error=None
        self.voice_file=Path(voice_file) if voice_file else None
        self._lock=threading.Lock()
        self._process=None

    def stop(self):
        with self._lock:
            proc=self._process
            self._process=None
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _say_windows(self,text):
        script=r'''
Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices=$s.GetInstalledVoices()
$v=$voices | Where-Object { $_.VoiceInfo.Culture.Name -like "es-*" -and $_.VoiceInfo.Gender -eq "Female" -and $_.VoiceInfo.Enabled } | Select-Object -First 1
if(-not $v){ $v=$voices | Where-Object { $_.VoiceInfo.Culture.Name -like "es-*" } | Select-Object -First 1 }
if(-not $v){ $v=$voices | Where-Object { $_.VoiceInfo.Enabled } | Select-Object -First 1 }
if($v){ $s.SelectVoice($v.VoiceInfo.Name) }
$s.Rate=3
$s.Volume=100
$s.Speak($env:NARI_TTS_TEXT)
$s.Dispose()
'''
        env=os.environ.copy()
        env["NARI_TTS_TEXT"]=str(text)
        try:
            proc=subprocess.Popen(
                ["powershell","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
            )
            with self._lock:
                self._process=proc
            try:
                stdout,stderr=proc.communicate(timeout=40)
                return proc.returncode,(stderr or "").strip()
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                return -1,"la síntesis tardó demasiado"
            finally:
                with self._lock:
                    if self._process is proc:
                        self._process=None
        except Exception as exc:
            return -1,str(exc)

    def say(self,text):
        if not self.enabled or not str(text).strip():
            return
        value=" ".join(str(text).strip().split())
        def run():
            with self._lock:
                if self.on_speaking:self.on_speaking(True)
                try:
                    code,err=self._say_windows(value)
                    if code!=0 and self.on_error:
                        self.on_error("voz Windows: "+(err[:160] or "falló la síntesis"))
                finally:
                    if self.on_speaking:self.on_speaking(False)
        threading.Thread(target=run,daemon=True,name="NARI-TTS").start()
