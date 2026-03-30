import os
import re
import uuid
import zipfile
import glob
from io import BytesIO
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from web import models, database
from web.tasks import task_manager
import core
from .database import engine, SessionLocal, get_db
from dotenv import load_dotenv
import hashlib
import asyncio
from pydantic import BaseModel


# Load local .env if it exists
load_dotenv()

# Create tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI()

class ReviewRequest(BaseModel):
    words: str
@app.on_event("startup")
async def startup_event():
    # Cleanup stale tasks from previous runs
    db = SessionLocal()
    try:
        stale_puzzles = db.query(models.Puzzle).filter(models.Puzzle.status == "pending").all()
        for puzzle in stale_puzzles:
            from .tasks import cleanup_puzzle_artifacts
            cleanup_puzzle_artifacts(puzzle.hash_id)
            db.delete(puzzle)
        db.commit()
    except Exception as e:
        print(f"Failed to cleanup stale tasks: {e}", flush=True)
    finally:
        db.close()


    async def periodic_timeout_check():
        while True:
            task_manager.check_timeouts()
            await asyncio.sleep(10)
    
    asyncio.create_task(periodic_timeout_check())

app.mount("/static", StaticFiles(directory="web/static"), name="static")
app.mount("/storage", StaticFiles(directory="storage"), name="storage")
templates = Jinja2Templates(directory="web/templates")

STORAGE_DIR = "storage"
HASH_ID_PATTERN = re.compile(r'^[0-9a-f]{32}$')

def validate_hash_id(hash_id: str):
    if not HASH_ID_PATTERN.match(hash_id):
        raise HTTPException(status_code=400, detail="Invalid puzzle ID")

def get_puzzle_files(hash_id):
    return {
        "original": os.path.join(STORAGE_DIR, f"{hash_id}_original.png"),
        "answer": os.path.join(STORAGE_DIR, f"{hash_id}_answer.png"),
        "styled": os.path.join(STORAGE_DIR, f"{hash_id}_styled.png"),
        "style_check": os.path.join(STORAGE_DIR, f"{hash_id}_style_check.png"),
        "pdf": os.path.join(STORAGE_DIR, f"{hash_id}_combined.pdf"),
    }

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db), q: str = None):
    query = db.query(models.Puzzle)
    if q:
        query = query.filter(
            (models.Puzzle.theme.ilike(f"%{q}%")) |
            (models.Puzzle.style.ilike(f"%{q}%")) |
            (models.Puzzle.words.ilike(f"%{q}%"))
        )
    puzzles = query.order_by(models.Puzzle.created_at.desc()).all()
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "puzzles": puzzles, 
        "age_levels": core.AGE_LEVELS.keys(),
        "art_styles": core.ART_STYLES.keys(),
        "search_query": q or ""
    })

@app.post("/generate")
async def generate(
    theme: str = Form(...),
    age: str = Form(...),
    style: str = Form(...),
    color_mode: str = Form(...),
    num_words: int = Form(None),
    multiplier: float = Form(2.5),
    use_custom_words: bool = Form(False),
    custom_words: str = Form(None),
    review_words: bool = Form(False),
    h_fwd: bool = Form(False),
    h_rev: bool = Form(False),
    v_fwd: bool = Form(False),
    v_rev: bool = Form(False),
    d_fwd_r: bool = Form(False),
    d_fwd_l: bool = Form(False),
    d_rev_r: bool = Form(False),
    d_rev_l: bool = Form(False),
    overlap: bool = Form(False),
    ink_saver: bool = Form(False),
    apply_style: bool = Form(False),
    use_advanced_model: bool = Form(False),
    db: Session = Depends(get_db)
):
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="API Key not configured")

    if num_words is not None and (num_words < 6 or num_words > 33 or num_words % 3 != 0):
        raise HTTPException(status_code=400, detail="Word count must be a multiple of 3 between 6 and 33")

    if multiplier < 1.5 or multiplier > 5.0:
        raise HTTPException(status_code=400, detail="Multiplier must be between 1.5 and 5.0")

    if use_custom_words and custom_words:
        # Basic ASCII validation for CSV
        try:
            custom_words.encode('ascii')
        except UnicodeEncodeError:
            raise HTTPException(status_code=400, detail="Custom words must be ASCII only")

    safe_theme = "".join([c for c in theme if c.isalnum() or c in (' ', '-', '_')]).strip()
    hash_id = hashlib.md5(f"{theme}{uuid.uuid4()}".encode()).hexdigest()

    new_puzzle = models.Puzzle(
        hash_id=hash_id,
        theme=safe_theme,
        age_range=age,
        style=style,
        color_mode=color_mode,
        ink_saver=ink_saver,
        has_styled=apply_style,
        review_required=review_words,
        model_name='gemini-3-pro-image-preview' if (apply_style and use_advanced_model) else None,
        status="pending",
        status_message="Queueing request..."
    )
    db.add(new_puzzle)
    db.commit()
    db.refresh(new_puzzle)

    direction_overrides = {
        'h_fwd': h_fwd, 'h_rev': h_rev,
        'v_fwd': v_fwd, 'v_rev': v_rev,
        'd_fwd_r': d_fwd_r, 'd_fwd_l': d_fwd_l,
        'd_rev_r': d_rev_r, 'd_rev_l': d_rev_l,
        'overlap': overlap
    }

    # Pass custom_words if enabled
    worker_custom_words = custom_words if use_custom_words else None

    task_manager.start_generation(new_puzzle.id, hash_id, num_words, direction_overrides, api_key, multiplier=multiplier, custom_words=worker_custom_words)
    return {"status": "success", "hash_id": hash_id}

@app.post("/review/{hash_id}")
async def review_words(hash_id: str, request: ReviewRequest, db: Session = Depends(get_db)):
    validate_hash_id(hash_id)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
    if not puzzle: raise HTTPException(status_code=404, detail="Not found")
    if puzzle.status != "awaiting_review": raise HTTPException(status_code=400, detail="Not awaiting review")

    # Hardcoded defaults for directions if no override stored (we can improve this by storing overrides in DB)
    age_cfg = core.AGE_LEVELS.get(puzzle.age_range, core.AGE_LEVELS["8-10"])
    flags = age_cfg["flags"]
    direction_overrides = {
        'h_fwd': 'h_fwd' in flags, 'h_rev': 'h_rev' in flags,
        'v_fwd': 'v_fwd' in flags, 'v_rev': 'v_rev' in flags,
        'd_fwd_r': 'd_fwd_r' in flags, 'd_fwd_l': 'd_fwd_l' in flags,
        'd_rev_r': 'd_rev_r' in flags, 'd_rev_l': 'd_rev_l' in flags,
        'overlap': 'overlap' in flags
    }

    # Update status immediately so the long-poll doesn't see stale 'awaiting_review'
    puzzle.status = "pending"
    puzzle.status_message = "STEP_GRID: Building puzzle with reviewed words..."
    puzzle.words = request.words
    db.commit()

    task_manager.start_resume(puzzle.id, hash_id, request.words, direction_overrides, api_key)
    return {"status": "success"}

@app.post("/retry/{hash_id}")
async def retry_styling(hash_id: str, db: Session = Depends(get_db)):
    validate_hash_id(hash_id)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
    if not puzzle: raise HTTPException(status_code=404, detail="Not found")
    if puzzle.status != "failed_styling": raise HTTPException(status_code=400, detail="Not in a retryable state")
    puzzle.status = "pending"
    puzzle.status_message = "STEP_STYLE: Retrying styling..."
    db.commit()
    task_manager.start_styling_retry(puzzle.id, hash_id, api_key)
    return {"status": "success"}

@app.get("/status/{hash_id}")
async def get_status(hash_id: str, last_message: str = None, db: Session = Depends(get_db)):
    import asyncio
    validate_hash_id(hash_id)

    # Long-poll: if client sends last_message, wait up to 10s for a change
    if last_message is not None:
        for _ in range(20):  # 20 x 0.5s = 10s max
            puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
            if not puzzle:
                return {"status": "deleted", "message": "Deleted after multiple failures."}
            if puzzle.status_message != last_message or puzzle.status in ('completed', 'failed', 'failed_styling', 'rejected', 'awaiting_review'):
                break
            db.expire_all()  # Force fresh read on next query
            await asyncio.sleep(0.5)

    puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
    if not puzzle: return {"status": "deleted", "message": "Deleted after multiple failures."}
    return {
        "status": puzzle.status,
        "message": puzzle.status_message,
        "words": puzzle.words,
        "error": puzzle.error_message,
        "suggested_prompt": puzzle.suggested_prompt
    }

@app.get("/detail/{hash_id}")
async def get_detail(hash_id: str, db: Session = Depends(get_db)):
    validate_hash_id(hash_id)
    puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
    if not puzzle: raise HTTPException(status_code=404, detail="Puzzle not found")
    ocr_warning = None
    if puzzle.status_message and "STEP_COMPLETE_WITH_WARNING" in puzzle.status_message:
        # Extract the warning details after the prefix
        ocr_warning = puzzle.status_message.split("STEP_COMPLETE_WITH_WARNING: ", 1)[-1]
    return {
        "hash_id": puzzle.hash_id,
        "theme": puzzle.theme,
        "has_styled": puzzle.has_styled,
        "suggested_prompt": puzzle.suggested_prompt,
        "ocr_warning": ocr_warning,
    }

@app.get("/download/{hash_id}/{file_type}")
async def download_file(hash_id: str, file_type: str, db: Session = Depends(get_db)):
    validate_hash_id(hash_id)
    puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
    if not puzzle: raise HTTPException(status_code=404, detail="Puzzle not found")
    files = get_puzzle_files(hash_id)
    if file_type not in files or not os.path.exists(files[file_type]): raise HTTPException(status_code=404, detail="File not found")
    safe_filename = puzzle.theme.replace('"', '_')
    if file_type == "pdf":
        return FileResponse(files[file_type], filename=f"{safe_filename}_Complete.pdf", media_type="application/pdf")
    return FileResponse(files[file_type], filename=f"{safe_filename}_{file_type.capitalize()}.png")

@app.get("/download-all/{hash_id}")
async def download_zip(hash_id: str, db: Session = Depends(get_db)):
    validate_hash_id(hash_id)
    puzzle = db.query(models.Puzzle).filter(models.Puzzle.hash_id == hash_id).first()
    if not puzzle: raise HTTPException(status_code=404, detail="Puzzle not found")
    files = get_puzzle_files(hash_id)
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for f_type, f_path in files.items():
            if os.path.exists(f_path):
                ext = os.path.splitext(f_path)[1]
                zip_file.write(f_path, f"{puzzle.theme}_{f_type.capitalize()}{ext}")
    zip_buffer.seek(0)
    safe_filename = puzzle.theme.replace('"', '_')
    return StreamingResponse(iter([zip_buffer.getvalue()]), media_type="application/x-zip-compressed", headers={"Content-Disposition": f'attachment; filename="{safe_filename}_Full_Set.zip"'})
