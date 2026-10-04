from __future__ import annotations
import ctypes
from ctypes import wintypes
import subprocess
import time
import webbrowser
from pathlib import Path
import sys

try:
    import pyautogui
except Exception:
    pyautogui = None

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    SW_MINIMIZE = 6
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    MOUSEEVENTF_MOVE = 0x0001
    # Evita que Windows fusione varios deltas; ayuda con cámaras que leen movimiento relativo.
    MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    EXTRA_INFO_T = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", EXTRA_INFO_T),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", EXTRA_INFO_T),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT

NAMED_VK = {
    "backspace":0x08,"tab":0x09,"enter":0x0D,"shift":0x10,"ctrl":0x11,"alt":0x12,
    "esc":0x1B,"escape":0x1B,"space":0x20,"pageup":0x21,"pagedown":0x22,
    "end":0x23,"home":0x24,"left":0x25,"up":0x26,"right":0x27,"down":0x28,
    "insert":0x2D,"delete":0x2E,"f1":0x70,"f2":0x71,"f3":0x72,"f4":0x73,
    "f5":0x74,"f6":0x75,"f7":0x76,"f8":0x77,"f9":0x78,"f10":0x79,
    "f11":0x7A,"f12":0x7B,
}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    NAMED_VK[c] = 0x41 + i
for i, c in enumerate("0123456789"):
    NAMED_VK[c] = 0x30 + i

class Computer:
    def __init__(self, root):
        self.root = Path(root)
        self.stop_event = False
        self.host_hwnd = None
        self.target_hwnd = None
        self.target_title = ""
        self.shift_lock_active = False
        if pyautogui:
            pyautogui.PAUSE = 0.005
            pyautogui.FAILSAFE = True

    def set_host_window(self, hwnd):
        self.host_hwnd = int(hwnd)

    def stop(self):
        self.stop_event = True

    def clear_stop(self):
        self.stop_event = False

    def release_all(self):
        for key in ("w","a","s","d","shift","space","ctrl","alt"):
            try:
                self._key_up(key)
            except Exception:
                pass
        if IS_WINDOWS:
            for button in ("left","right"):
                try:
                    self._mouse_button(button, False)
                except Exception:
                    pass

    def clear_target(self):
        self.target_hwnd = None
        self.target_title = ""
        self.shift_lock_active = False

    def _windows(self):
        if not IS_WINDOWS:
            return []
        items = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            if self.host_hwnd and int(hwnd) == self.host_hwnd:
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value.strip()
                if title:
                    items.append((int(hwnd), title))
            return True

        user32.EnumWindows(EnumWindowsProc(cb), 0)
        return items

    def _window_title(self, hwnd):
        if not IS_WINDOWS or not hwnd:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        try:
            user32.GetWindowTextW(hwnd, buf, 512)
            return buf.value.strip()
        except Exception:
            return ""

    def find_game(self, profile="generic"):
        keywords = {
            "roblox":["roblox"],
            "jjs":["roblox","jujutsu shenanigans"],
            "limbus":["limbus company","limbus"],
            "generic":["roblox","limbus company","limbus"]
        }.get(profile, [])
        for hwnd, title in self._windows():
            low = title.lower()
            if not keywords or any(k in low for k in keywords):
                return hwnd, title
        return None, ""

    def find_game_window(self, profile="generic"):
        hwnd, _ = self.find_game(profile)
        return hwnd

    def focus_window(self, hwnd):
        if not IS_WINDOWS or not hwnd:
            return False
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.10)
            ok = int(user32.GetForegroundWindow()) == int(hwnd)
            if ok:
                self.target_hwnd = int(hwnd)
                self.target_title = self._window_title(hwnd)
            return ok
        except Exception:
            return False

    def focus_game(self, profile="generic"):
        hwnd, title = self.find_game(profile)
        if not hwnd:
            return False, ""
        if self.focus_window(hwnd):
            self.target_title = title
            return True, title
        return False, title

    def focus_title(self, title):
        title = str(title or "").strip()
        if not title:
            return False
        for hwnd, window_title in self._windows():
            if window_title.lower() == title.lower() or title.lower() in window_title.lower():
                return self.focus_window(hwnd)
        return False

    def track_foreground(self):
        if not IS_WINDOWS:
            return
        try:
            hwnd = int(user32.GetForegroundWindow())
            if not hwnd or hwnd == int(self.host_hwnd or 0):
                return
            # Mientras existe un objetivo de juego, no sustituirlo por otra ventana.
            if self.target_hwnd:
                return
            self.target_hwnd = hwnd
            self.target_title = self._window_title(hwnd)
        except Exception:
            pass

    def keep_target_focused(self):
        if self.target_hwnd:
            try:
                if int(user32.GetForegroundWindow()) != int(self.target_hwnd):
                    self.focus_window(self.target_hwnd)
                    return
            except Exception:
                pass
        if self.target_title:
            self.focus_title(self.target_title)

    def minimize_host(self):
        if IS_WINDOWS and self.host_hwnd:
            try:
                user32.ShowWindow(self.host_hwnd, SW_MINIMIZE)
            except Exception:
                pass

    def _vk(self, key):
        k = str(key).lower().strip()
        if k in NAMED_VK:
            return NAMED_VK[k]
        if len(k) == 1 and IS_WINDOWS:
            value = int(user32.VkKeyScanW(ord(k)) & 0xFF)
            if value:
                return value
        raise ValueError(f"tecla no soportada: {key}")

    def _win_key(self, key, down):
        if not IS_WINDOWS:
            return
        vk = self._vk(key)
        scan = int(user32.MapVirtualKeyW(vk, 0))
        if scan:
            flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
            inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
                wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0
            ))
        else:
            flags = 0 if down else KEYEVENTF_KEYUP
            inp = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
                wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0
            ))
        sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if sent != 1:
            raise RuntimeError(f"SendInput fallo para {key}")

    def _key_down(self, key):
        try:
            self._win_key(key, True)
        except Exception:
            if pyautogui:
                pyautogui.keyDown(key)

    def _key_up(self, key):
        try:
            self._win_key(key, False)
        except Exception:
            if pyautogui:
                pyautogui.keyUp(key)

    def _mouse_button(self, button, down):
        if not IS_WINDOWS:
            return
        b=str(button).lower()
        if b=="right":
            flag=MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP
        elif b=="left":
            flag=MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
        else:
            raise ValueError(f"botón no soportado: {button}")
        inp=INPUT(type=0,mi=MOUSEINPUT(dx=0,dy=0,mouseData=0,dwFlags=flag,time=0,dwExtraInfo=0))
        sent=user32.SendInput(1,ctypes.byref(inp),ctypes.sizeof(INPUT))
        if sent!=1:
            raise RuntimeError(f"SendInput fallo para botón {button}")

    def _mouse_move_rel(self, dx, dy):
        dx, dy = int(dx), int(dy)
        if IS_WINDOWS:
            # Ruta principal: SendInput con movimiento relativo y NOCOALESCE.
            # Roblox suele consumir el delta del mouse a traves de su capa de input;
            # usar la API moderna evita que Windows fusione los pequeños pasos del giro.
            try:
                inp = INPUT(
                    type=0,
                    mi=MOUSEINPUT(
                        dx=dx,
                        dy=dy,
                        mouseData=0,
                        dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE,
                        time=0,
                        dwExtraInfo=0,
                    )
                )
                sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
                if sent == 1:
                    return
            except Exception:
                pass

            # Compatibilidad con aplicaciones que consumen la ruta legacy.
            try:
                user32.mouse_event(MOUSEEVENTF_MOVE, dx, dy, 0, 0)
                return
            except Exception:
                pass

            if pyautogui:
                try:
                    pyautogui.moveRel(dx, dy, duration=0)
                    return
                except Exception:
                    pass

            try:
                class POINT(ctypes.Structure):
                    _fields_=[("x",wintypes.LONG),("y",wintypes.LONG)]
                pt=POINT()
                if user32.GetCursorPos(ctypes.byref(pt)):
                    user32.SetCursorPos(int(pt.x+dx),int(pt.y+dy))
                    return
            except Exception:
                pass
            raise RuntimeError("No pude enviar movimiento relativo del ratón")
        elif pyautogui:
            pyautogui.moveRel(dx,dy,duration=0)

    def _cursor_inside_target(self):
        """Comprueba si el puntero ya esta dentro del viewport del juego."""
        if not IS_WINDOWS or not self.target_hwnd:
            return False
        try:
            class POINT(ctypes.Structure):
                _fields_=[("x",wintypes.LONG),("y",wintypes.LONG)]
            rect=wintypes.RECT()
            hwnd=wintypes.HWND(self.target_hwnd)
            if not user32.GetClientRect(hwnd,ctypes.byref(rect)):
                return False
            origin=POINT(int(rect.left),int(rect.top))
            if not user32.ClientToScreen(hwnd,ctypes.byref(origin)):
                return False
            cursor=POINT()
            if not user32.GetCursorPos(ctypes.byref(cursor)):
                return False
            left=int(origin.x)
            top=int(origin.y)
            right=left+int(rect.right-rect.left)
            bottom=top+int(rect.bottom-rect.top)
            return left<=int(cursor.x)<right and top<=int(cursor.y)<bottom
        except Exception:
            return False

    def _center_cursor_in_target(self):
        """Coloca el cursor dentro del viewport del juego antes de un RMB drag."""
        if not IS_WINDOWS or not self.target_hwnd:
            return False
        try:
            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            rect = wintypes.RECT()
            if not user32.GetClientRect(wintypes.HWND(self.target_hwnd), ctypes.byref(rect)):
                return False

            pt = POINT(
                int((rect.left + rect.right) / 2),
                int((rect.top + rect.bottom) / 2),
            )
            if not user32.ClientToScreen(
                wintypes.HWND(self.target_hwnd), ctypes.byref(pt)
            ):
                return False

            return bool(user32.SetCursorPos(int(pt.x), int(pt.y)))
        except Exception:
            return False

    def toggle_shift_lock(self):
        """Alterna el Shift Lock de Roblox cuando la experiencia lo permite."""
        self.keep_target_focused()
        self._win_key("shift", True)
        self._win_key("shift", False)
        self.shift_lock_active = not self.shift_lock_active
        return self.shift_lock_active

    def camera_turn(self, dx, dy=0, seconds=0.06):
        """Gira la cámara con RMB, incluso si Shift Lock esta activo."""
        self.camera_drag(dx, dy, seconds)

    def camera_drag(self, dx, dy=0, seconds=0.08):
        self.keep_target_focused()
        # Recentrar solo si el puntero realmente esta fuera del viewport.
        if not self._cursor_inside_target():
            self._center_cursor_in_target()

        total=max(0.080, min(0.180, float(seconds)))
        # Una trayectoria de 8-14 pasos con easing evita tanto saltos como
        # el aspecto robotico de pequeños movimientos independientes.
        steps=max(8, min(14, int(round(total*78))))
        dx=float(dx)
        dy=float(dy)

        self._mouse_button("right", True)
        try:
            time.sleep(0.025)

            last_x=0.0
            last_y=0.0
            for i in range(1,steps+1):
                if self.stop_event:
                    break

                t=i/float(steps)
                # Smoothstep: empieza y termina suavemente.
                eased=t*t*(3.0-2.0*t)
                target_x=dx*eased
                target_y=dy*eased

                mx=int(round(target_x-last_x))
                my=int(round(target_y-last_y))
                last_x=target_x
                last_y=target_y

                if mx or my:
                    self._mouse_move_rel(mx,my)
                time.sleep(total/steps)
        finally:
            self._mouse_button("right", False)
            time.sleep(0.006)

    def act(self, action):
        if self.stop_event:
            return "DETENIDO"
        if not isinstance(action, dict):
            return "accion invalida"

        self.keep_target_focused()
        t = str(action.get("type", "")).lower()

        try:
            if t == "m1":
                self._mouse_button("left", True)
                try:
                    time.sleep(max(0.025, min(0.16, float(action.get("seconds", 0.055) or 0.055))))
                finally:
                    self._mouse_button("left", False)
            elif t == "block":
                key = "f"
                self._key_down(key)
                try:
                    time.sleep(max(0.08, min(1.20, float(action.get("seconds", 0.35) or 0.35))))
                finally:
                    self._key_up(key)
            elif t == "double_tap_w":
                self._key_down("w")
                self._key_up("w")
                time.sleep(0.07)
                self._key_down("w")
                self._key_up("w")
            elif t == "press":
                key = str(action.get("key", ""))
                self._key_down(key)
                self._key_up(key)
            elif t == "hold":
                key = str(action.get("key", "w"))
                self._key_down(key)
                time.sleep(min(3.0, max(0.03, float(action.get("seconds",0.20)))))
                self._key_up(key)
            elif t == "key_down":
                self._key_down(str(action.get("key","")))
            elif t == "key_up":
                self._key_up(str(action.get("key","")))
            elif t == "keys":
                keys = [str(x) for x in action.get("keys", [])]
                for key in keys:
                    self._key_down(key)
                time.sleep(min(3.0, max(0.03, float(action.get("seconds",0.20)))))
                for key in reversed(keys):
                    self._key_up(key)
            elif t in {"click","double_click","move","drag"}:
                if pyautogui is None:
                    return "pyautogui no disponible"
                sw, sh = pyautogui.size()
                def xy(x, y, normalized=True):
                    if normalized:
                        return (
                            max(0, min(sw-1, int(round(float(x)/1000*(sw-1))))),
                            max(0, min(sh-1, int(round(float(y)/1000*(sh-1)))))
                        )
                    return int(x), int(y)
                x,y=xy(action.get("x",500),action.get("y",500),bool(action.get("normalized",True)))
                if t=="click":
                    pyautogui.click(x,y,button=str(action.get("button","left")))
                elif t=="double_click":
                    pyautogui.doubleClick(x,y,interval=0.04)
                elif t=="move":
                    pyautogui.moveTo(x,y,duration=0)
                else:
                    x2,y2=xy(action.get("x2",x),action.get("y2",y),bool(action.get("normalized",True)))
                    pyautogui.moveTo(x,y,duration=0)
                    pyautogui.dragTo(x2,y2,duration=min(1.2,max(0.01,float(action.get("duration",0.15)))))
            elif t == "type":
                text = str(action.get("text",""))
                if pyautogui:
                    try: pyautogui.write(text, interval=0)
                    except Exception:
                        import pyperclip
                        pyperclip.copy(text)
                        pyautogui.hotkey("ctrl","v")
            elif t == "hotkey":
                keys=[str(x) for x in action.get("keys",[])]
                for key in keys: self._key_down(key)
                for key in reversed(keys): self._key_up(key)
            elif t == "camera_key_turn":
                # Respaldo para Roblox cuando el movimiento de mouse sintetico no se consume.
                dx = int(action.get("dx", 0) or 0)
                dy = int(action.get("dy", 0) or 0)
                if abs(dy) > abs(dx):
                    direction = "down" if dy > 0 else "up"
                else:
                    direction = "right" if dx > 0 else "left"
                self._key_down(direction)
                try:
                    time.sleep(max(0.035, min(0.12, float(action.get("seconds", 0.07) or 0.07))))
                finally:
                    self._key_up(direction)
            elif t in {"mouse_move_rel","camera_drag","camera_turn"}:
                dx=int(action.get("dx",0)); dy=int(action.get("dy",0))
                if t == "camera_turn":
                    self.camera_turn(dx,dy,float(action.get("seconds",0.10)))
                elif t == "camera_drag":
                    self.camera_drag(dx,dy,float(action.get("seconds",0.18)))
                else:
                    self._mouse_move_rel(dx,dy)
            elif t == "toggle_shift_lock":
                active=self.toggle_shift_lock()
                return "Shift Lock " + ("activado" if active else "desactivado")
            elif t == "mouse_button_down":
                self._mouse_button(str(action.get("button","right")),True)
            elif t == "mouse_button_up":
                self._mouse_button(str(action.get("button","right")),False)
            elif t == "scroll":
                if pyautogui: pyautogui.scroll(int(action.get("amount",0)))
            elif t == "wait":
                time.sleep(min(2.0,max(0.005,float(action.get("seconds",0.05)))))
            elif t == "open_url":
                webbrowser.open(str(action.get("url","")),new=2)
            elif t in {"open_app","launch"}:
                cmd=str(action.get("command",""))
                if not cmd:
                    return "sin comando"
                subprocess.Popen(cmd,shell=True,cwd=str(self.root))
            else:
                return "accion no soportada: " + t
            return "OK"
        except Exception as exc:
            return "ERROR: " + str(exc)

    def test_key(self, key="w"):
        if not self.target_hwnd:
            for profile in ("roblox","limbus","generic"):
                ok,title=self.focus_game(profile)
                if ok:
                    break
        if not self.target_hwnd:
            return False, "No encontré una ventana de juego."
        try:
            self.focus_window(self.target_hwnd)
            self._key_down(key)
            time.sleep(0.60)
            self._key_up(key)
            return True, f"Tecla {str(key).upper()} mantenida 0.6 s"
        except Exception as exc:
            return False, "No pude enviar la tecla: " + str(exc)
