from __future__ import annotations
import threading,time
from collections import deque
import ctypes
from ctypes import wintypes
import sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageTk
try: import mss
except Exception: mss=None
try: import cv2
except Exception: cv2=None

class ScreenVideo:
    def __init__(self,fps=30,analysis_width=960):
        self.fps=float(fps); self.analysis_width=int(analysis_width); self.running=False
        self.latest_frame=None; self.history=deque(maxlen=8)
        self.latest_game_frame=None; self.game_history=deque(maxlen=8)
        self.target_hwnd=None
        self.lock=threading.Lock(); self.thread=None; self.sct=None
        self.monitor_left=0; self.monitor_top=0
    def start(self):
        if mss is None: raise RuntimeError("Falta mss")
        self.running=True; self.thread=threading.Thread(target=self._loop,daemon=True); self.thread.start()
    def set_target_window(self, hwnd):
        try:
            self.target_hwnd = int(hwnd) if hwnd else None
        except Exception:
            self.target_hwnd = None
    def clear_target_window(self):
        self.target_hwnd = None
        with self.lock:
            self.latest_game_frame = None
            self.game_history.clear()
    def stop(self): self.running=False
    def _client_region(self):
        if sys.platform != "win32" or not self.target_hwnd:
            return None
        try:
            class POINT(ctypes.Structure):
                _fields_=[("x",wintypes.LONG),("y",wintypes.LONG)]
            rect=wintypes.RECT()
            hwnd=wintypes.HWND(self.target_hwnd)
            if not ctypes.windll.user32.GetClientRect(hwnd,ctypes.byref(rect)):
                return None
            pt=POINT(int(rect.left),int(rect.top))
            if not ctypes.windll.user32.ClientToScreen(hwnd,ctypes.byref(pt)):
                return None
            left=max(0,int(pt.x)-int(self.monitor_left))
            top=max(0,int(pt.y)-int(self.monitor_top))
            width=max(1,int(rect.right-rect.left))
            height=max(1,int(rect.bottom-rect.top))
            return left,top,width,height
        except Exception:
            return None

    @staticmethod
    def _resize(frame,max_width):
        if frame is None or not max_width or frame.shape[1] <= int(max_width):
            return frame
        ratio=float(max_width)/float(frame.shape[1])
        if cv2 is not None:
            return cv2.resize(frame,(int(max_width),max(1,int(frame.shape[0]*ratio))),interpolation=cv2.INTER_AREA)
        return frame

    def _loop(self):
        self.sct=mss.mss(); monitor=self.sct.monitors[0]
        self.monitor_left=int(monitor.get("left",0)); self.monitor_top=int(monitor.get("top",0))
        period=1/max(1,self.fps)
        while self.running:
            t=time.perf_counter()
            raw=np.array(self.sct.grab(monitor))[:,:,:3][:,:,::-1]
            region=self._client_region()
            game=None
            if region:
                x,y,w,h=region
                x2=min(raw.shape[1],x+w); y2=min(raw.shape[0],y+h)
                if x < x2 and y < y2:
                    game=raw[y:y2,x:x2].copy()
            frame=self._resize(raw,self.analysis_width)
            game_frame=self._resize(game,self.analysis_width) if game is not None else None
            with self.lock:
                self.latest_frame=frame
                self.history.append((time.time(), frame.copy()))
                if game_frame is not None:
                    self.latest_game_frame=game_frame
                    self.game_history.append((time.time(), game_frame.copy()))
            time.sleep(max(0,period-(time.perf_counter()-t)))
    def latest(self):
        with self.lock:return None if self.latest_frame is None else self.latest_frame.copy()
    def latest_game(self):
        with self.lock:
            if self.latest_game_frame is None:
                return None if self.latest_frame is None else self.latest_frame.copy()
            return self.latest_game_frame.copy()
    def image_bytes(self,count=1,max_width=768):
        with self.lock:
            rows=list(self.game_history) if self.game_history else list(self.history)
        if not rows:
            return [],None

        n=max(1,min(4,int(count or 1)))
        if n==1:
            selected=[rows[-1]]
        else:
            step=max(1,(len(rows)-1)//max(1,n-1))
            idxs=[max(0,len(rows)-1-step*(n-1-i)) for i in range(n)]
            selected=[rows[i] for i in idxs]

        import io,base64
        encoded=[]
        for _,frame in selected:
            im=Image.fromarray(frame).convert("RGB")
            if max_width and im.width>int(max_width):
                ratio=float(max_width)/float(im.width)
                im=im.resize((int(max_width),max(1,int(im.height*ratio))),Image.Resampling.BILINEAR)
            b=io.BytesIO()
            im.save(b,format="JPEG",quality=60,optimize=True)
            encoded.append(base64.b64encode(b.getvalue()).decode())
        return encoded,rows[-1][0]
    def tk_image(self,max_w=790):
        f=self.latest()
        if f is None:return None
        im=Image.fromarray(f).convert("RGB")
        if im.width>max_w:
            ratio=max_w/im.width; im=im.resize((max_w,int(im.height*ratio)))
        return ImageTk.PhotoImage(im)

class PeopleVision:
    def __init__(self,models_dir):
        self.models_dir=Path(models_dir); self.running=False; self.cap=None; self.frame=None; self.lock=threading.Lock()
    def start(self,index=0):
        if cv2 is None:return False
        self.cap=cv2.VideoCapture(index)
        if not self.cap.isOpened():return False
        self.running=True; threading.Thread(target=self._loop,daemon=True).start(); return True
    def stop(self):
        self.running=False
        if self.cap:
            try:self.cap.release()
            except:pass
    def _loop(self):
        while self.running and self.cap:
            ok,frame=self.cap.read()
            if ok:
                frame=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                with self.lock:self.frame=frame
            time.sleep(0.03)
    def current_frame(self):
        with self.lock:
            return (None,None) if self.frame is None else (self.frame.copy(),None)
    def enroll(self,name):
        frame,_=self.current_frame()
        if frame is None:return None
        gray=cv2.cvtColor(frame,cv2.COLOR_RGB2GRAY)
        return np.array([float(gray.mean()),float(gray.std()),float(gray.shape[0]),float(gray.shape[1])],dtype=np.float32)
    def match(self,embeddings):
        frame,_=self.current_frame()
        if frame is None or not embeddings:return []
        gray=cv2.cvtColor(frame,cv2.COLOR_RGB2GRAY); feat=np.array([float(gray.mean()),float(gray.std()),float(gray.shape[0]),float(gray.shape[1])],dtype=np.float32)
        out=[]
        for name,emb in embeddings.items():
            if len(emb)==len(feat) and np.linalg.norm(feat-emb)<80: out.append(name)
        return out
