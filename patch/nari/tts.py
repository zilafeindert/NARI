from __future__ import annotations
import subprocess, threading
from pathlib import Path

class TTS:
    def __init__(self,voice_file=None,enabled=True):
        self.enabled=bool(enabled)
        self.on_speaking=None
        self.voice_file=Path(voice_file) if voice_file else None
        self._lock=threading.Lock()
        self._last_voice=""

    def stop(self):
        # La síntesis se ejecuta en un proceso separado; no dejamos procesos
        # huérfanos desde NARI.
        return

    def _say_windows(self,text):
        script = r'''
param([string]$Text)
Add-Type -AssemblyName System.Speech
$s=New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices=$s.GetInstalledVoices()
$v=$voices | Where-Object { $_.VoiceInfo.Culture.Name -like "es-*" -and $_.VoiceInfo.Enabled } | Select-Object -First 1
if(-not $v){ $v=$voices | Where-Object { $_.VoiceInfo.Culture.Name -like "es-*" } | Select-Object -First 1 }
if(-not $v){ $v=$voices | Select-Object -First 1 }
if($v){ $s.SelectVoice($v.VoiceInfo.Name) }
$s.Rate=2
$s.Volume=100
$s.Speak($Text)
$s.Dispose()
'''
        try:
            result=subprocess.run(
                ["powershell","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass",
                 "-Command",script,"--",str(text)],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                timeout=45,
            )
            return result.returncode, (result.stderr or "").strip()
        except Exception as exc:
            return -1, str(exc)

    def say(self,text):
        if not self.enabled or not str(text).strip():
            return
        value=str(text).strip()
        def run():
            with self._lock:
                if self.on_speaking: self.on_speaking(True)
                try:
                    code,err=self._say_windows(value)
                    if code!=0:
                        try:
                            self.on_speaking(False)
                            if self.on_speaking: self.on_speaking(True)
                            # Segundo intento muy simple por si una versión de
                            # PowerShell rechazó parámetros del primer proceso.
                            fallback='Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Rate=2; $s.Volume=100; $s.Speak([Console]::In.ReadToEnd());'
                            subprocess.run(
                                ["powershell","-NoProfile","-NonInteractive","-Command",fallback],
                                input=value,
                                text=True,
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                                timeout=45,
                            )
                        except Exception:
                            pass
                finally:
                    if self.on_speaking: self.on_speaking(False)
        threading.Thread(target=run,daemon=True,name="NARI-TTS").start()
