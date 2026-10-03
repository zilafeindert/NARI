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

    def stop(self):
        return

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
            result=subprocess.run(
                ["powershell","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-Command",script],
                check=False,
                capture_output=True,
                text=True,
                env=env,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                timeout=40,
            )
            return result.returncode,(result.stderr or "").strip()
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
