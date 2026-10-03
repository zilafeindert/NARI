from __future__ import annotations
import threading,time
from pathlib import Path
import numpy as np
from PIL import Image,ImageTk
try: import mss
except Exception: mss=None
try: import cv2
except Exception: cv2=None

class ScreenVideo:
    def __init__(self,fps=30,analysis_width=960):
        self.fps=float(fps); self.analysis_width=int(analysis_width); self.running=False; self.latest_frame=None; self.lock=threading.Lock(); self.thread=None; self.sct=None
    def start(self):
        if mss is None: raise RuntimeError("Falta mss")
        self.running=True; self.thread=threading.Thread(target=self._loop,daemon=True); self.thread.start()
    def stop(self): self.running=False
    def _loop(self):
        self.sct=mss.mss(); monitor=self.sct.monitors[0]; period=1/max(1,self.fps)
        while self.running:
            t=time.perf_counter()
            frame=np.array(self.sct.grab(monitor))[:,:,:3][:,:,::-1]
            if self.analysis_width and frame.shape[1]>self.analysis_width:
                ratio=self.analysis_width/frame.shape[1]
                frame=cv2.resize(frame,(self.analysis_width,int(frame.shape[0]*ratio)),interpolation=cv2.INTER_AREA) if cv2 is not None else frame
            with self.lock:self.latest_frame=frame
            time.sleep(max(0,period-(time.perf_counter()-t)))
    def latest(self):
        with self.lock:return None if self.latest_frame is None else self.latest_frame.copy()
    def image_bytes(self,count=1):
        frame=self.latest()
        if frame is None:return [],None
        im=Image.fromarray(frame).convert("RGB")
        import io,base64
        b=io.BytesIO(); im.save(b,format="JPEG",quality=68)
        return [base64.b64encode(b.getvalue()).decode()],time.time()
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
