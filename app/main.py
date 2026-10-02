import json, time, uuid, threading
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import fitz, cv2, numpy as np, pytesseract

BASE=Path(__file__).resolve().parent.parent
DATA=BASE/"data"; DATA.mkdir(exist_ok=True)
JOBS={}; LOCK=threading.Lock()
app=FastAPI(title="PDF AI Restorer")
app.mount("/static",StaticFiles(directory=str(BASE/"app"/"static")),name="static")

def save(j):
    (DATA/j["id"]).mkdir(exist_ok=True)
    (DATA/j["id"]/"state.json").write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding="utf-8")

def load():
    for d in DATA.iterdir():
        p=d/"state.json"
        if p.exists():
            try: JOBS[d.name]=json.loads(p.read_text(encoding="utf-8"))
            except: pass

def enhance(img):
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    gray=cv2.fastNlMeansDenoising(gray,None,8,7,21)
    gray=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8)).apply(gray)
    blur=cv2.GaussianBlur(gray,(0,0),1.0)
    gray=cv2.addWeighted(gray,1.18,blur,-0.18,0)
    return cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)

def score(img):
    g=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    sharp=cv2.Laplacian(g,cv2.CV_64F).var()
    contrast=float(g.std())
    s=round(max(0,min(100,35+min(sharp/20,35)+min(contrast/4,30))))
    return s,("good" if s>=72 else "review" if s>=52 else "ai_candidate")

def render(page):
    pix=page.get_pixmap(matrix=fitz.Matrix(1.7,1.7),alpha=False)
    a=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n)
    return cv2.cvtColor(a,cv2.COLOR_RGB2BGR)

def ocr(img):
    try:
        d=pytesseract.image_to_data(cv2.cvtColor(img,cv2.COLOR_BGR2RGB),
                                    lang="ita+eng",output_type=pytesseract.Output.DICT)
        out=[]
        for i,t in enumerate(d["text"]):
            t=t.strip()
            if not t: continue
            try: c=float(d["conf"][i])
            except: c=-1
            if c<20: continue
            out.append((t,int(d["left"][i]),int(d["top"][i]),int(d["width"][i]),int(d["height"][i])))
        return out
    except Exception:
        return []

def process(j):
    try:
        src=DATA/j["id"]/"original.pdf"; out=DATA/j["id"]/"restored.pdf"
        doc=fitz.open(src); result=fitz.open(); j["status"]="processing"; save(j)
        for n,page in enumerate(doc,1):
            while j.get("paused"): time.sleep(1)
            j["current"]=n; save(j)
            img=render(page); restored=enhance(img); sc,label=score(restored)
            words=ocr(restored)
            ok,enc=cv2.imencode(".jpg",restored,[int(cv2.IMWRITE_JPEG_QUALITY),94])
            if not ok: raise RuntimeError("Errore immagine.")
            p=result.new_page(width=page.rect.width,height=page.rect.height)
            p.insert_image(page.rect,stream=enc.tobytes())
            sx,sy=page.rect.width/restored.shape[1],page.rect.height/restored.shape[0]
            for t,x,y,w,h in words:
                r=fitz.Rect(x*sx,y*sy,(x+w)*sx,(y+h)*sy)
                p.insert_textbox(r,t,fontsize=max(3,r.height*.8),fontname="helv",render_mode=3)
            j.setdefault("pages",{})[str(n)]={"score":sc,"label":label,"ai_used":False}
            save(j)
        result.save(out,garbage=4,deflate=True); doc.close(); result.close()
        j["status"]="done"; save(j)
    except Exception as e:
        j["status"]="error"; j["error"]=str(e); save(j)

@app.get("/",response_class=HTMLResponse)
def home(): return (BASE/"app/static/index.html").read_text(encoding="utf-8")

@app.post("/api/jobs")
async def create(file:UploadFile=File(...)):
    if not file.filename.lower().endswith(".pdf"): raise HTTPException(400,"Carica un PDF.")
    jid=uuid.uuid4().hex[:12]; folder=DATA/jid; folder.mkdir()
    src=folder/"original.pdf"
    with src.open("wb") as f:
        while chunk:=await file.read(1024*1024): f.write(chunk)
    try: total=len(fitz.open(src))
    except: raise HTTPException(400,"PDF non leggibile.")
    j={"id":jid,"filename":file.filename,"total":total,"current":0,"status":"ready","paused":False,"pages":{}}
    JOBS[jid]=j; save(j); return j

@app.post("/api/jobs/{jid}/start")
def start(jid:str,bg:BackgroundTasks):
    if jid not in JOBS: raise HTTPException(404)
    JOBS[jid]["paused"]=False; bg.add_task(process,JOBS[jid]); return JOBS[jid]

@app.post("/api/jobs/{jid}/pause")
def pause(jid:str):
    if jid not in JOBS: raise HTTPException(404)
    JOBS[jid]["paused"]=True; save(JOBS[jid]); return JOBS[jid]

@app.get("/api/jobs/{jid}")
def status(jid:str):
    if jid not in JOBS: raise HTTPException(404)
    return JOBS[jid]

@app.get("/api/jobs/{jid}/download")
def download(jid:str):
    p=DATA/jid/"restored.pdf"
    if not p.exists(): raise HTTPException(409,"PDF non pronto.")
    return FileResponse(p,media_type="application/pdf",filename="PDF_restaurato.pdf")

@app.post("/api/jobs/{jid}/ai")
def ai(jid:str,payload:dict):
    if jid not in JOBS: raise HTTPException(404)
    pages=payload.get("pages",[])
    if not pages: raise HTTPException(400,"Nessuna pagina selezionata.")
    j=JOBS[jid]
    for n in pages:
        if str(n) in j.get("pages",{}): j["pages"][str(n)]["ai_requested"]=True
    j["ai_queue"]=pages; save(j)
    return {"ok":True,"message":"Pagine aggiunte alla coda AI. Il collegamento al modello AI è predisposto ma va configurato sul server.", "pages":pages}

load()
