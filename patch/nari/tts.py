from __future__ import annotations
import subprocess, tempfile, threading
from pathlib import Path

class TTS:
    def __init__(self,voice_file=None,enabled=True):
        self.enabled=bool(enabled); self.on_speaking=None
        self.voice_file=Path(voice_file) if voice_file else None

    def stop(self): pass

    def _say_windows(self,text):
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$voices=$s.GetInstalledVoices(); "
            "$v=$voices | Where-Object { $_.VoiceInfo.Gender -eq 'Female' -and $_.VoiceInfo.Culture.Name -like 'es-*' } | Select-Object -First 1; "
            "if($v){$s.SelectVoice($v.VoiceInfo.Name)}; "
            "$s.Rate=1; $s.Volume=100; "
            "$s.Speak($args[0]);"
        )
        subprocess.run(["powershell","-NoProfile","-Command",script,"--",text],check=False,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))

    def say(self,text):
        if not self.enabled or not str(text).strip(): return
        def run():
            if self.on_speaking: self.on_speaking(True)
            try:self._say_windows(str(text))
            finally:
                if self.on_speaking:self.on_speaking(False)
        threading.Thread(target=run,daemon=True).start()
