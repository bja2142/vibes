import multiprocessing
import threading
import time
import os
import glob
from web import models
from web.database import SessionLocal
import core

STORAGE_DIR = "storage"
MAX_EXECUTION_TIME = 300 # 5 minutes

def cleanup_puzzle_artifacts(hash_id):
    files = glob.glob(os.path.join(STORAGE_DIR, f"{hash_id}*"))
    for f in files:
        try:
            os.remove(f)
        except Exception:
            pass

def delete_puzzle_record(puzzle_id, db):
    puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
    if puzzle:
        db.delete(puzzle)
        db.commit()

def generation_worker(puzzle_id, num_words, direction_overrides, api_key, multiplier=2.5, custom_words=None):
    db = SessionLocal()
    try:
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if not puzzle: return
        hash_id = puzzle.hash_id

        def update_status(msg, words=None):
            puzzle.status_message = msg
            if words: puzzle.words = ",".join(words)
            db.commit()

        if custom_words:
            words = [w.strip() for w in custom_words.split(',') if w.strip()]
            puzzle.words = ",".join(words)
            db.commit()
        else:
            words = core.generate_words(puzzle.theme, puzzle.age_range, num_words, api_key, status_callback=update_status)
            
        if puzzle.review_required:
            puzzle.status = "awaiting_review"
            puzzle.status_message = "STEP_REVIEW: Awaiting user word review."
            db.commit()
            return

        output_base = os.path.join(STORAGE_DIR, f"{hash_id}")
        core.run_word_search_logic(words, puzzle.theme, output_base, direction_overrides, status_callback=update_status, multiplier=multiplier)
        
        if puzzle.has_styled:
            puzzle.styling_attempts += 1
            success = core.apply_styling(puzzle.theme, puzzle.age_range, puzzle.style, puzzle.color_mode, puzzle.ink_saver, output_base, api_key, status_callback=update_status, model_name=puzzle.model_name)
            if success:
                puzzle.status = "completed"
            else:
                if puzzle.styling_attempts >= 2:
                    cleanup_puzzle_artifacts(hash_id)
                    db.delete(puzzle)
                else:
                    puzzle.status = "failed_styling"
                    puzzle.status_message = "STEP_STYLE_FAILED: Styling failed. Click to retry."
        else:
            puzzle.suggested_prompt = core.build_styling_prompt(puzzle.theme, puzzle.age_range, puzzle.style, puzzle.color_mode, puzzle.ink_saver)
            puzzle.status = "completed"

        db.commit()
    except core.ExplicitContentError as e:
        print(f"Worker rejected explicit content: {e}", flush=True)
        db.rollback()
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if puzzle:
            puzzle.status = "rejected"
            puzzle.error_message = str(e)
            puzzle.status_message = "STEP_REJECTED: Content rejected."
            db.commit()
    except Exception as e:
        print(f"Worker generation failed: {e}", flush=True)
        db.rollback()
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if puzzle:
            cleanup_puzzle_artifacts(puzzle.hash_id)
            db.delete(puzzle)
            db.commit()
    finally: db.close()

def resume_generation_worker(puzzle_id, reviewed_words, direction_overrides, api_key, multiplier=2.5):
    db = SessionLocal()
    try:
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if not puzzle: return
        hash_id = puzzle.hash_id

        def update_status(msg, words=None):
            puzzle.status_message = msg
            db.commit()

        puzzle.words = reviewed_words
        puzzle.status = "pending"
        db.commit()

        words_list = [w.strip() for w in reviewed_words.split(',') if w.strip()]
        output_base = os.path.join(STORAGE_DIR, f"{hash_id}")
        
        core.run_word_search_logic(words_list, puzzle.theme, output_base, direction_overrides, status_callback=update_status, multiplier=multiplier)
        
        if puzzle.has_styled:
            puzzle.styling_attempts += 1
            success = core.apply_styling(puzzle.theme, puzzle.age_range, puzzle.style, puzzle.color_mode, puzzle.ink_saver, output_base, api_key, status_callback=update_status, model_name=puzzle.model_name)
            if success: puzzle.status = "completed"
            else:
                if puzzle.styling_attempts >= 2:
                    cleanup_puzzle_artifacts(hash_id)
                    db.delete(puzzle)
                else:
                    puzzle.status = "failed_styling"
                    puzzle.status_message = "STEP_STYLE_FAILED: Styling failed."
        else:
            puzzle.suggested_prompt = core.build_styling_prompt(puzzle.theme, puzzle.age_range, puzzle.style, puzzle.color_mode, puzzle.ink_saver)
            puzzle.status = "completed"

        db.commit()
    except Exception as e:
        print(f"Resume failed: {e}", flush=True)
        db.rollback()
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if puzzle:
            cleanup_puzzle_artifacts(puzzle.hash_id)
            db.delete(puzzle)
            db.commit()
    finally: db.close()

def styling_retry_worker(puzzle_id, api_key):
    db = SessionLocal()
    try:
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if not puzzle: return
        hash_id = puzzle.hash_id
        def update_status(msg, words=None):
            puzzle.status_message = msg
            db.commit()
        output_base = os.path.join(STORAGE_DIR, f"{hash_id}")
        puzzle.status = "pending"
        db.commit()
        success = core.apply_styling(puzzle.theme, puzzle.age_range, puzzle.style, puzzle.color_mode, puzzle.ink_saver, output_base, api_key, status_callback=update_status, model_name=puzzle.model_name)
        puzzle.styling_attempts += 1
        if success: puzzle.status = "completed"
        else:
            if puzzle.styling_attempts >= 2:
                cleanup_puzzle_artifacts(hash_id)
                db.delete(puzzle)
            else:
                puzzle.status = "failed_styling"
                puzzle.status_message = "STEP_STYLE_FAILED: Styling failed again."
        db.commit()
    except Exception as e:
        print(f"Worker styling retry failed: {e}", flush=True)
        db.rollback()
        puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
        if puzzle:
            cleanup_puzzle_artifacts(puzzle.hash_id)
            db.delete(puzzle)
            db.commit()
    finally: db.close()

class TaskManager:
    def __init__(self):
        self.active_tasks = {}
        self._lock = threading.Lock()

    def start_generation(self, puzzle_id, hash_id, num_words, direction_overrides, api_key, multiplier=2.5, custom_words=None):
        p = multiprocessing.Process(target=generation_worker, args=(puzzle_id, num_words, direction_overrides, api_key, multiplier, custom_words))
        with self._lock:
            self.active_tasks[hash_id] = {"process": p, "start_time": time.time(), "puzzle_id": puzzle_id}
            p.start()

    def start_resume(self, puzzle_id, hash_id, reviewed_words, direction_overrides, api_key, multiplier=2.5):
        p = multiprocessing.Process(target=resume_generation_worker, args=(puzzle_id, reviewed_words, direction_overrides, api_key, multiplier))
        with self._lock:
            self.active_tasks[hash_id] = {"process": p, "start_time": time.time(), "puzzle_id": puzzle_id}
            p.start()

    def start_styling_retry(self, puzzle_id, hash_id, api_key):
        p = multiprocessing.Process(target=styling_retry_worker, args=(puzzle_id, api_key))
        with self._lock:
            self.active_tasks[hash_id] = {"process": p, "start_time": time.time(), "puzzle_id": puzzle_id}
            p.start()

    def check_timeouts(self):
        current_time = time.time()
        tasks_to_kill = []
        tasks_to_remove = []
        processes_to_join = []
        with self._lock:
            for hash_id, info in self.active_tasks.items():
                process = info["process"]
                if not process.is_alive():
                    tasks_to_remove.append(hash_id)
                    processes_to_join.append(process)
                elif (current_time - info["start_time"]) > MAX_EXECUTION_TIME:
                    tasks_to_kill.append((hash_id, info["puzzle_id"], process))
        # Join completed processes to avoid zombies
        for process in processes_to_join:
            process.join(timeout=5)
        for hash_id, puzzle_id, process in tasks_to_kill:
            process.terminate()
            process.join(timeout=5)
            if process.is_alive(): process.kill()
            cleanup_puzzle_artifacts(hash_id)
            db = SessionLocal()
            try:
                puzzle = db.query(models.Puzzle).filter(models.Puzzle.id == puzzle_id).first()
                if puzzle:
                    db.delete(puzzle)
                    db.commit()
            finally: db.close()
            tasks_to_remove.append(hash_id)
        if tasks_to_remove:
            with self._lock:
                for hash_id in tasks_to_remove:
                    if hash_id in self.active_tasks: del self.active_tasks[hash_id]

task_manager = TaskManager()
