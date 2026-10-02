import os
import io
import json
import uuid
import shutil
import threading
import re
from pathlib import Path

import cv2
import fitz
import numpy as np
import pytesseract
from pytesseract import Output
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

BASE = Path("/tmp/pdf-ai-restorer")
BASE.mkdir(parents=True, exist_ok=True)

JOBS = {}
LOCK = threading.Lock()

app = FastAPI(title="PDF AI Restorer v2")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def b2_client():
    endpoint = os.getenv("B2_ENDPOINT")
    key_id = os.getenv("B2_KEY_ID")
    app_key = os.getenv("B2_APPLICATION_KEY")
    bucket = os.getenv("B2_BUCKET")
    if not all([endpoint, key_id, app_key, bucket]):
        return None
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
    )


def b2_put(path: Path, key: str):
    c = b2_client()
    if not c:
        return
    bucket = os.getenv("B2_BUCKET")
    c.upload_file(str(path), bucket, key)


def b2_get(key: str, path: Path):
    c = b2_client()
    if not c:
        return False
    try:
        bucket = os.getenv("B2_BUCKET")
        c.download_file(bucket, key, str(path))
        return True
    except Exception:
        return False


def save_state(jid):
    d = BASE / jid
    state = JOBS[jid]
    (d/"state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    b2_put(d/"state.json", f"{jid}/state.json")


def load_state(jid):
    d = BASE / jid
    if (d/"state.json").exists():
        return json.loads((d/"state.json").read_text(encoding="utf-8"))
    if b2_get(f"{jid}/state.json", d/"state.json"):
        return json.loads((d/"state.json").read_text(encoding="utf-8"))
    return None


def deskew(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Very light threshold only for angle detection.
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(bw > 0))
    if len(coords) < 500:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.25 or abs(angle) > 4.0:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w/2, h/2), angle, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def restore_page(img):
    """Conservative restoration: preserve color and avoid destroying text."""
    out = img.copy()

    # Estimate whether this is essentially monochrome.
    # We still preserve the 3-channel output for every page.
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    contrast = float(np.std(gray))

    # Mild illumination/contrast correction only when clearly needed.
    if contrast < 42:
        lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.25, tileGridSize=(8, 8))
        l = clahe.apply(l)
        out = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    # Light denoise. Avoid aggressive smoothing of letter edges.
    gray2 = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    noise = float(np.std(gray2 - cv2.GaussianBlur(gray2, (0,0), 1.0)))
    if noise > 10:
        out = cv2.fastNlMeansDenoisingColored(out, None, 2, 2, 7, 21)

    # Gentle unsharp mask.
    blur = cv2.GaussianBlur(out, (0, 0), 0.65)
    out = cv2.addWeighted(out, 1.10, blur, -0.10, 0)

    return deskew(out)


def score_page(img, ocr_text):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    contrast = float(np.std(gray))
    text_len = len((ocr_text or "").strip())
    score = 50
    if contrast >= 45: score += 12
    elif contrast >= 30: score += 5
    if text_len >= 300: score += 22
    elif text_len >= 80: score += 12
    elif text_len < 20: score -= 20
    score = max(0, min(100, int(score)))
    label = "good" if score >= 72 else ("review" if score >= 52 else "ai_candidate")
    return score, label


def ocr_to_pdf_page(pdf, img, lang="ita+eng"):
    """Insert image and an invisible word-level OCR text layer."""
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)

    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=88, optimize=True)
    page = pdf.new_page(width=w, height=h)
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)

    data = pytesseract.image_to_data(
        pil, lang=lang, config="--psm 3",
        output_type=Output.DICT
    )
    words = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1
        if conf < 20:
            continue
        x, y, ww, hh = [int(data[k][i]) for k in ("left","top","width","height")]
        if ww <= 0 or hh <= 0:
            continue
        words.append((txt, fitz.Rect(x, y, x+ww, y+hh), conf))

    # Invisible text using PDF text rendering mode 3.
    for txt, rect, conf in words:
        size = max(5, min(32, rect.height * 0.82))
        try:
            page.insert_textbox(
                rect, txt,
                fontsize=size,
                fontname="helv",
                color=(0,0,0),
                render_mode=3,
                align=fitz.TEXT_ALIGN_LEFT,
            )
        except Exception:
            pass

    return " ".join(x[0] for x in words)


def render_page(src, pno, dpi=160):
    page = src.load_page(pno)
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    else:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return arr


def apply_selected_ai(img):
    # Conservative second pass for now; true SR/deblur model can be plugged in later.
    den = cv2.fastNlMeansDenoisingColored(img, None, 3, 3, 7, 21)
    blur = cv2.GaussianBlur(den, (0,0), 0.8)
    return cv2.addWeighted(den, 1.14, blur, -0.14, 0)


def worker(jid):
    d = BASE / jid
    state = JOBS[jid]
    state["status"] = "running"
    save_state(jid)

    try:
        src = fitz.open(d/"input.pdf")
        total = len(src)
        state["total"] = total

        out = fitz.open()
        # If resuming from a checkpoint, start over from checkpoint only if available.
        # For small tests this keeps the implementation simple and robust.
        ai_requested = set(state.get("ai_requested", []))
        dpi = int(os.getenv("RENDER_DPI", "160"))

        for pno in range(total):
            if state.get("pause_requested"):
                state["status"] = "paused"
                save_state(jid)
                src.close()
                out.close()
                return

            img = render_page(src, pno, dpi=dpi)
            restored = restore_page(img)
            if pno + 1 in ai_requested:
                restored = apply_selected_ai(restored)

            text = ocr_to_pdf_page(out, restored)
            score, label = score_page(restored, text)
            state["pages"][str(pno+1)] = {
                "score": score,
                "label": label,
                "ocr_chars": len(text),
            }
            state["current"] = pno + 1

            # Checkpoint every N pages.
            every = int(os.getenv("CHECKPOINT_EVERY", "10"))
            if (pno + 1) % every == 0 or pno + 1 == total:
                out.save(d/"checkpoint.pdf", garbage=4, deflate=True)
                b2_put(d/"checkpoint.pdf", f"{jid}/checkpoint.pdf")
                save_state(jid)

        out.save(d/"final.pdf", garbage=4, deflate=True)
        out.close()
        src.close()
        state["status"] = "done"
        state["progress"] = 100
        save_state(jid)
        b2_put(d/"final.pdf", f"{jid}/final.pdf")
    except Exception as e:
        state["status"] = "error"
        state["error"] = repr(e)
        save_state(jid)


@app.get("/")
def home():
    return FileResponse("app/static/index.html")


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Carica un PDF.")
    jid = uuid.uuid4().hex[:12]
    d = BASE / jid
    d.mkdir(parents=True, exist_ok=True)
    with open(d/"input.pdf", "wb") as f:
        while True:
            chunk = await file.read(1024*1024)
            if not chunk:
                break
            f.write(chunk)

    src = fitz.open(d/"input.pdf")
    total = len(src)
    src.close()
    state = {
        "id": jid, "filename": file.filename, "total": total, "current": 0,
        "progress": 0, "status": "created", "pause_requested": False,
        "pages": {}, "ai_requested": []
    }
    JOBS[jid] = state
    save_state(jid)
    b2_put(d/"input.pdf", f"{jid}/input.pdf")
    return state


@app.post("/api/jobs/{jid}/start")
def start(jid: str):
    if jid not in JOBS:
        st = load_state(jid)
        if not st:
            raise HTTPException(404, "Progetto non trovato.")
        JOBS[jid] = st
    state = JOBS[jid]
    if state["status"] == "running":
        return state
    state["pause_requested"] = False
    state["status"] = "queued"
    save_state(jid)
    threading.Thread(target=worker, args=(jid,), daemon=True).start()
    return state


@app.post("/api/jobs/{jid}/pause")
def pause(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "Progetto non trovato.")
    JOBS[jid]["pause_requested"] = True
    save_state(jid)
    return JOBS[jid]


@app.post("/api/jobs/{jid}/ai")
def ai(jid: str, payload: dict):
    if jid not in JOBS:
        raise HTTPException(404, "Progetto non trovato.")
    pages = payload.get("pages", [])
    clean = []
    for p in pages:
        try:
            n = int(p)
            if 1 <= n <= JOBS[jid]["total"]:
                clean.append(n)
        except Exception:
            pass
    JOBS[jid]["ai_requested"] = sorted(set(JOBS[jid].get("ai_requested", []) + clean))
    save_state(jid)
    return {"ai_requested": JOBS[jid]["ai_requested"]}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    if jid not in JOBS:
        st = load_state(jid)
        if not st:
            raise HTTPException(404, "Progetto non trovato.")
        JOBS[jid] = st
    return JOBS[jid]


@app.get("/api/jobs/{jid}/download")
def download(jid: str):
    d = BASE / jid
    path = d/"final.pdf"
    if not path.exists():
        if not b2_get(f"{jid}/final.pdf", path):
            raise HTTPException(404, "PDF finale non disponibile.")
    return FileResponse(path, media_type="application/pdf",
                        filename="restaurato.pdf")
