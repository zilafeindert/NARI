from __future__ import annotations

import random
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UNLABELLED = DATA / "jjs_training" / "unlabelled"
LABELLED = DATA / "jjs_training" / "labelled"
DATASET = DATA / "jjs_training" / "dataset"
MODEL_DIR = ROOT / "models" / "vision"
CUSTOM_MODEL = MODEL_DIR / "jjs_best.pt"


def _cv2():
    try:
        import cv2
        return cv2
    except Exception as exc:
        raise RuntimeError(
            "Falta OpenCV en el entorno de NARI. Reinstala la instalación principal."
        ) from exc


def label():
    """Interactively draw boxes around enemy avatars in F9-captured screenshots."""
    cv2 = _cv2()
    UNLABELLED.mkdir(parents=True, exist_ok=True)
    image_dir = LABELLED / "images"
    label_dir = LABELLED / "labels"
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(
        p for p in UNLABELLED.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not images:
        print(f"No hay capturas en: {UNLABELLED}")
        print("Abre NARI, entra a JJS y pulsa F9 para guardar ejemplos durante una partida.")
        return

    already = {p.stem for p in image_dir.iterdir() if p.is_file()}
    images = [p for p in images if p.stem not in already]
    print("En cada imagen:")
    print("  L = dibujar rectángulos sobre TODOS los enemigos visibles")
    print("  N = guardar como escena sin enemigo")
    print("  S = saltar imagen")
    print("  Q = terminar")
    print("No marques tu propio avatar: las cajas deben cubrir solo rivales o el Dummy.")
    cv2.namedWindow("NARI JJS - etiquetado", cv2.WINDOW_NORMAL)

    try:
        for index, source in enumerate(images, 1):
            frame = cv2.imread(str(source))
            if frame is None:
                print("No se pudo abrir:", source.name)
                continue
            view = frame.copy()
            cv2.putText(
                view, f"{index}/{len(images)} | L: etiquetar | N: sin enemigo | S: saltar | Q: salir",
                (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (40, 230, 240), 2, cv2.LINE_AA
            )
            cv2.imshow("NARI JJS - etiquetado", view)
            key = cv2.waitKey(0) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (ord("s"), ord("S")):
                continue

            boxes = []
            if key in (ord("l"), ord("L")):
                selected = cv2.selectROIs(
                    "Marca cada avatar enemigo y confirma con ENTER; ESC termina",
                    frame,
                    showCrosshair=True,
                    fromCenter=False,
                )
                for box in selected:
                    x, y, w, h = [float(v) for v in box]
                    if w >= 5 and h >= 8:
                        boxes.append((x, y, w, h))
            elif key not in (ord("n"), ord("N")):
                continue

            # Keep the whole frame, including negative scenes, to teach the detector
            # not to confuse HUD, the local avatar, and empty arena regions with enemies.
            out_image = image_dir / source.name
            out_label = label_dir / (source.stem + ".txt")
            h_img, w_img = frame.shape[:2]
            lines = []
            for x, y, w, h in boxes:
                cx = (x + w / 2.0) / max(1.0, float(w_img))
                cy = (y + h / 2.0) / max(1.0, float(h_img))
                nw = w / max(1.0, float(w_img))
                nh = h / max(1.0, float(h_img))
                lines.append(
                    f"0 {max(0,min(1,cx)):.6f} {max(0,min(1,cy)):.6f} "
                    f"{max(0,min(1,nw)):.6f} {max(0,min(1,nh)):.6f}"
                )
            shutil.copy2(source, out_image)
            out_label.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            print(f"Guardado {out_image.name}: {len(lines)} enemigo(s)")
    finally:
        cv2.destroyAllWindows()

    print(f"Etiquetas guardadas en: {LABELLED}")


def _read_labels(path: Path):
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return []


def _prepare_dataset():
    images_src = LABELLED / "images"
    labels_src = LABELLED / "labels"
    image_paths = sorted(
        p for p in images_src.iterdir()
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ) if images_src.exists() else []
    pairs = []
    for image in image_paths:
        label_path = labels_src / (image.stem + ".txt")
        if label_path.exists():
            pairs.append((image, label_path, bool(_read_labels(label_path))))

    positives = [item for item in pairs if item[2]]
    negatives = [item for item in pairs if not item[2]]
    if len(positives) < 20:
        raise RuntimeError(
            f"Solo hay {len(positives)} imágenes con enemigos etiquetados. "
            "Reúne al menos 20; recomendado: 50-150 con distintas distancias, escenarios y animaciones."
        )

    rng = random.Random(42)
    rng.shuffle(positives)
    rng.shuffle(negatives)
    val_count = max(2, int(round(len(positives) * 0.20)))
    validation = positives[:val_count] + negatives[:max(1, int(round(len(negatives) * 0.20)))]
    training = positives[val_count:] + negatives[max(1, int(round(len(negatives) * 0.20))):]

    if not training or not validation:
        raise RuntimeError("No pude formar las particiones de entrenamiento y validación.")

    if DATASET.exists():
        shutil.rmtree(DATASET)
    for split in ("train", "val"):
        (DATASET / "images" / split).mkdir(parents=True, exist_ok=True)
        (DATASET / "labels" / split).mkdir(parents=True, exist_ok=True)

    for split, items in (("train", training), ("val", validation)):
        for image, label_path, _positive in items:
            shutil.copy2(image, DATASET / "images" / split / image.name)
            shutil.copy2(label_path, DATASET / "labels" / split / label_path.name)

    yaml_path = DATASET / "dataset.yaml"
    yaml_root = DATASET.resolve().as_posix().replace("'", "\\'")
    yaml_text = (
        f"path: '{yaml_root}'\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        "  0: enemy\n"
    )
    yaml_path.write_text(yaml_text, encoding="utf-8")
    return yaml_path, len(training), len(validation)


def train():
    try:
        from ultralytics import YOLO
    except Exception as exc:
        raise RuntimeError(
            "Instala primero el detector con NARI_JJS_DETECTOR_INSTALAR.bat."
        ) from exc

    yaml_path, train_count, val_count = _prepare_dataset()
    print(f"Entrenamiento: {train_count} imágenes; validación: {val_count} imágenes.")
    print("Se ajustará el modelo con ejemplos reales etiquetados de tu JJS.")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    runs_dir = DATA / "jjs_training" / "runs"

    model = YOLO("yolov8n.pt")
    result = model.train(
        data=str(yaml_path),
        epochs=70,
        imgsz=640,
        batch=4,
        patience=14,
        workers=0,
        cache=False,
        plots=False,
        project=str(runs_dir),
        name="enemy_detector",
        exist_ok=True,
        verbose=True,
    )

    run_dir = Path(getattr(result, "save_dir", runs_dir / "enemy_detector"))
    best = run_dir / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError(f"El entrenamiento terminó sin producir best.pt: {best}")
    shutil.copy2(best, CUSTOM_MODEL)
    print("\nDetector JJS entrenado y guardado en:")
    print(CUSTOM_MODEL)
    print("Cierra y vuelve a abrir NARI para cargarlo.")
    print("Las decisiones tácticas y la política de acciones siguen aprendiendo aparte.")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Etiquetar capturas reales de JJS y entrenar un detector de enemigos."
    )
    parser.add_argument("command", choices=("label", "train", "label-train"))
    args = parser.parse_args()
    if args.command in {"label", "label-train"}:
        label()
    if args.command in {"train", "label-train"}:
        train()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nERROR:", exc)
        raise SystemExit(1)
