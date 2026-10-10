from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np


class JJSObjectDetector:
    """Optional local YOLO detector for Roblox/JJS avatars.

    Uses a locally supplied fine-tuned model when present; otherwise falls back
    to an Ultralytics COCO-pretrained person detector. The VLM remains active as
    a semantic fallback because generic COCO weights are not trained specifically
    on stylized Roblox avatars.
    """

    HUMAN_NAMES = {
        "person", "human", "player", "avatar", "enemy", "opponent",
        "rival", "dummy", "character", "roblox_player",
    }

    def __init__(self, model_dir: Path, data_dir: Path):
        self.model_dir = Path(model_dir)
        self.data_dir = Path(data_dir)
        self.model = None
        self.model_name = ""
        self.status = "no iniciado"
        self.error = ""
        self.last_init_try = 0.0
        self.lock = threading.RLock()

    def _model_candidates(self):
        return (
            self.model_dir / "jjs_best.pt",
            self.model_dir / "jjs_enemy.pt",
            self.data_dir / "models" / "best.pt",
            self.data_dir / "best.pt",
            self.model_dir / "best.pt",
            self.model_dir / "yolo26n.pt",
            self.model_dir / "yolov8n.pt",
            self.model_dir / "yolov5s.pt",
        )

    def initialize(self, force: bool = False) -> bool:
        with self.lock:
            if self.model is not None:
                return True
            now = time.monotonic()
            if not force and now - self.last_init_try < 12.0:
                return False
            self.last_init_try = now
            try:
                from ultralytics import YOLO
            except Exception as exc:
                self.status = "YOLO no instalado; usando VLM"
                self.error = f"Import ultralytics: {exc}"
                return False

            try:
                candidate = next((p for p in self._model_candidates() if p.is_file()), None)
                # yolov8n.pt is a compact person detector and is auto-downloaded
                # by Ultralytics when the optional detector has been installed.
                model_ref = str(candidate) if candidate else "yolov8n.pt"
                self.status = "cargando modelo"
                self.model = YOLO(model_ref)
                self.model_name = Path(model_ref).name if candidate else "yolov8n.pt (COCO)"
                self.status = "activo: " + self.model_name
                self.error = ""
                return True
            except Exception as exc:
                self.model = None
                self.status = "falló carga YOLO; usando VLM"
                self.error = str(exc)[:240]
                return False

    @staticmethod
    def _clip(value, low=0.0, high=1.0):
        return max(low, min(high, float(value)))

    def detect(self, frame, previous=None):
        """Return the best likely opponent with normalized center and range estimate."""
        if frame is None or getattr(frame, "ndim", 0) != 3:
            return None
        if self.model is None and not self.initialize():
            return None

        arr = np.asarray(frame)
        if arr.shape[0] < 80 or arr.shape[1] < 120:
            return None
        height, width = arr.shape[:2]

        try:
            # ScreenVideo stores RGB; Ultralytics expects OpenCV/BGR arrays.
            bgr = arr[:, :, ::-1].copy()
            with self.lock:
                results = self.model.predict(
                    source=bgr,
                    imgsz=640,
                    conf=0.18,
                    iou=0.50,
                    max_det=20,
                    verbose=False,
                )
            if not results:
                return None
            result = results[0]
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                return None

            names = getattr(result, "names", {}) or {}
            recognized_ids = set()
            if isinstance(names, dict):
                for class_id, class_name in names.items():
                    if str(class_name).strip().lower() in self.HUMAN_NAMES:
                        recognized_ids.add(int(class_id))
            elif isinstance(names, (tuple, list)):
                for class_id, class_name in enumerate(names):
                    if str(class_name).strip().lower() in self.HUMAN_NAMES:
                        recognized_ids.add(class_id)
            # COCO's only human class is person (ID 0).
            if not recognized_ids:
                recognized_ids = {0}

            prev_x = prev_y = prev_d = None
            if isinstance(previous, dict):
                try:
                    prev_x = float(previous["center_x"])
                    prev_y = float(previous["center_y"])
                    prev_d = float(previous.get("distance", 0.75))
                except Exception:
                    prev_x = prev_y = prev_d = None

            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            classes = boxes.cls.cpu().numpy().astype(int)
            candidates = []

            for coords, conf, class_id in zip(xyxy, confs, classes):
                if int(class_id) not in recognized_ids:
                    continue
                x1, y1, x2, y2 = [float(v) for v in coords]
                x1 = max(0.0, min(float(width - 1), x1))
                x2 = max(0.0, min(float(width), x2))
                y1 = max(0.0, min(float(height - 1), y1))
                y2 = max(0.0, min(float(height), y2))
                bw, bh = x2 - x1, y2 - y1
                if bw < 7 or bh < 14:
                    continue
                h_ratio = bh / max(1.0, float(height))
                w_ratio = bw / max(1.0, float(width))
                area_ratio = (bw * bh) / max(1.0, float(width * height))
                aspect = bw / max(1.0, bh)
                if h_ratio < 0.018 or area_ratio < 0.00035:
                    continue
                if aspect < 0.12 or aspect > 1.55:
                    continue
                cx = ((x1 + x2) * 0.5) / float(width)
                cy = ((y1 + y2) * 0.5) / float(height)
                bottom = y2 / float(height)
                distance = self._clip(1.0 - h_ratio / 0.60, 0.06, 0.98)

                track_dist = None
                if prev_x is not None:
                    track_dist = ((cx - prev_x) ** 2 + (cy - prev_y) ** 2) ** 0.5

                # In third-person games, the local avatar is usually large and
                # occupies the bottom-center of the image. Reject that region
                # unless it is already the tracked target, reducing self-lock.
                likely_self = (
                    0.27 <= cx <= 0.73
                    and bottom >= 0.84
                    and h_ratio >= 0.17
                    and (track_dist is None or track_dist > 0.24)
                )
                if likely_self:
                    continue

                centrality = 1.0 - min(1.0, abs(cx - 0.5) / 0.65)
                not_self_vertical = 1.0 - self._clip((cy - 0.48) / 0.60)
                size_score = self._clip(h_ratio / 0.35)
                track_bonus = (
                    0.24 * max(0.0, 1.0 - track_dist / 0.35)
                    if track_dist is not None else 0.0
                )
                score = (
                    0.56 * float(conf)
                    + 0.18 * centrality
                    + 0.14 * not_self_vertical
                    + 0.12 * size_score
                    + track_bonus
                )
                candidates.append({
                    "kind": "human",
                    "source": "yolo-person",
                    "center_x": self._clip(cx),
                    "center_y": self._clip(cy),
                    "x1": self._clip(x1 / width),
                    "y1": self._clip(y1 / height),
                    "x2": self._clip(x2 / width),
                    "y2": self._clip(y2 / height),
                    "width_ratio": self._clip(w_ratio),
                    "height_ratio": self._clip(h_ratio),
                    "distance": distance,
                    "distance_delta": (
                        distance - prev_d if prev_d is not None else None
                    ),
                    "aim_alignment_delta": (
                        (((prev_x - 0.5) ** 2 + (prev_y - 0.5) ** 2) ** 0.5)
                        - (((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5)
                        if prev_x is not None else None
                    ),
                    "confidence": self._clip(float(conf)),
                    "score": score,
                    "class_name": str(names.get(int(class_id), "person")) if isinstance(names, dict) else "person",
                })

            if not candidates:
                return None
            candidates.sort(key=lambda item: item["score"], reverse=True)
            best = candidates[0]
            # A tracked target gets hysteresis, but a clearly better new detection
            # may replace it to avoid following an unrelated avatar forever.
            if previous is not None and prev_x is not None:
                near = [c for c in candidates if ((c["center_x"] - prev_x) ** 2 + (c["center_y"] - prev_y) ** 2) ** 0.5 < 0.28]
                if near:
                    near.sort(key=lambda item: item["score"], reverse=True)
                    tracked = near[0]
                    if tracked["score"] >= best["score"] * 0.72:
                        best = tracked
            best["candidate_count"] = len(candidates)
            best.pop("score", None)
            return best
        except Exception as exc:
            self.error = str(exc)[:240]
            self.status = "error detección; usando VLM"
            return None
