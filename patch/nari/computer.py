from __future__ import annotations
import ctypes
from ctypes import wintypes
import time
from pathlib import Path
try:
    import pyautogui
except Exception:
    pyautogui=None

class Computer:
    def __init__(self,data_dir):
        self.data_dir=Path(data_dir); self.stop_event=False; self.host_hwnd=None; self.target_title=""
        if pyautogui:
            pyautogui.PAUSE=0.01
            pyautogui.FAILSAFE=False

    def set_host_window(self,hwnd): self.host_hwnd=int(hwnd)
    def stop(self): self.stop_event=True
    def clear_stop(self): self.stop_event=False

    def _windows(self):
        items=[]
        user32=ctypes.windll.user32
        EnumWindowsProc=ctypes.WINFUNCTYPE(ctypes.c_bool,wintypes.HWND,wintypes.LPARAM)
        def cb(hwnd,_):
            if not user32.IsWindowVisible(hwnd): return True
            n=user32.GetWindowTextLengthW(hwnd)
            if n:
                buf=ctypes.create_unicode_buffer(n+1); user32.GetWindowTextW(hwnd,buf,n+1)
                title=buf.value.strip()
                if title: items.append((hwnd,title))
            return True
        user32.EnumWindows(EnumWindowsProc(cb),0)
        return items

    def find_game(self,profile):
        keys={"roblox":["Roblox"],"limbus":["Limbus","LIMBUS"],"generic":["Roblox","Limbus","LIMBUS"]}.get(profile,[])
        for hwnd,title in self._windows():
            if any(k.lower() in title.lower() for k in keys): return hwnd,title
        return None,""

    def focus_game(self,profile):
        hwnd,title=self.find_game(profile)
        if not hwnd: return False,""
        try:
            user32=ctypes.windll.user32
            user32.ShowWindow(hwnd,9); user32.SetForegroundWindow(hwnd); time.sleep(0.08)
            self.target_title=title; return True,title
        except Exception: return False,title

    def minimize_host(self):
        if self.host_hwnd:
            try: ctypes.windll.user32.ShowWindow(self.host_hwnd,6)
            except Exception: pass

    def track_foreground(self):
        if not self.target_title:
            return
        try:
            user32=ctypes.windll.user32
            hwnd=user32.GetForegroundWindow()
            n=user32.GetWindowTextLengthW(hwnd)
            buf=ctypes.create_unicode_buffer(n+1)
            user32.GetWindowTextW(hwnd,buf,n+1)
            current=buf.value.strip().lower()
            if self.target_title.lower() not in current:
                self.focus_title(self.target_title)
        except Exception:
            pass

    def focus_title(self,title):
        title=str(title or "").strip()
        if not title: return False
        for hwnd,window_title in self._windows():
            if window_title.strip().lower()==title.lower() or title.lower() in window_title.lower():
                try:
                    user32=ctypes.windll.user32
                    user32.ShowWindow(hwnd,9)
                    user32.SetForegroundWindow(hwnd)
                    time.sleep(0.04)
                    self.target_title=window_title
                    return True
                except Exception:
                    return False
        return False

    def _key(self,key,down=True):
        if pyautogui is None: return False
        try:
            if down: pyautogui.keyDown(key)
            else: pyautogui.keyUp(key)
            return True
        except: return False

    def act(self,a):
        if self.stop_event: return "stopped"
        if self.target_title:
            self.focus_title(self.target_title)
        t=str(a.get("type","")).lower()
        try:
            if t=="press": return "ok" if self._key(str(a.get("key","")),True) and self._key(str(a.get("key","")),False) else "failed"
            if t=="key_down": return "ok" if self._key(str(a.get("key","")),True) else "failed"
            if t=="key_up": return "ok" if self._key(str(a.get("key","")),False) else "failed"
            if t=="hold":
                k=str(a.get("key","w")); self._key(k,True); time.sleep(min(2.0,max(0.01,float(a.get("seconds",0.2))))); self._key(k,False); return "ok"
            if t=="keys":
                keys=[str(x) for x in a.get("keys",[])]
                for k in keys:self._key(k,True)
                time.sleep(min(2.0,max(0.01,float(a.get("seconds",0.2)))))
                for k in reversed(keys):self._key(k,False)
                return "ok"
            if pyautogui is None:return "pyautogui unavailable"
            if t in {"click","double_click","move","drag"}:
                x=float(a.get("x",500)); y=float(a.get("y",500))
                if a.get("normalized"): 
                    x=x*pyautogui.size().width/1000; y=y*pyautogui.size().height/1000
                if t=="click": pyautogui.click(x=int(x),y=int(y),button=a.get("button","left"))
                elif t=="double_click": pyautogui.doubleClick(x=int(x),y=int(y),button=a.get("button","left"),interval=0.08)
                elif t=="move": pyautogui.moveTo(x=int(x),y=int(y),duration=0.03)
                else:
                    x1=float(a.get("x1",x)); y1=float(a.get("y1",y))
                    if a.get("normalized"): x1=x1*pyautogui.size().width/1000; y1=y1*pyautogui.size().height/1000
                    pyautogui.moveTo(int(x),int(y)); pyautogui.dragTo(int(x1),int(y1),duration=float(a.get("duration",0.15)))
                return "ok"
            if t=="type": pyautogui.write(str(a.get("text","")),interval=0.005); return "ok"
            if t=="scroll": pyautogui.scroll(int(a.get("amount",0))); return "ok"
            if t=="wait": time.sleep(min(2,max(0.01,float(a.get("seconds",0.2))))); return "ok"
            return "ignored:"+t
        except Exception as e: return "error:"+str(e)

    def test_key(self,key="w"):
        if not self.target_title:
            for profile in ("roblox","limbus","generic"):
                ok,title=self.focus_game(profile)
                if ok: break
        ok=self._key(key,True) and self._key(key,False)
        return ok, f"Tecla {key.upper()} enviada" if ok else f"No pude enviar {key.upper()}. pyautogui={pyautogui is not None}"
