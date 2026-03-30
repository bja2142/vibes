import json
import os
import sys
import time
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import style
from word_search import WordSearch
from generator import WordSearchGenerator

try:
    import pytesseract
except ImportError:
    pytesseract = None

AGE_LEVELS = {
    "5-6": {
        "min_len": 3, "max_len": 5, "num_words": 6,
        "flags": ["h_fwd"]
    },
    "6-8": {
        "min_len": 4, "max_len": 6, "num_words": 9,
        "flags": ["h_fwd", "v_fwd"]
    },
    "8-10": {
        "min_len": 5, "max_len": 8, "num_words": 12,
        "flags": ["h_fwd", "v_fwd", "d_fwd_r", "d_fwd_l"]
    },
    "11-12": {
        "min_len": 6, "max_len": 10, "num_words": 15,
        "flags": ["h_fwd", "h_rev", "v_fwd", "v_rev", "d_fwd_r", "d_fwd_l"]
    },
    "12+": {
        "min_len": 7, "max_len": 12, "num_words": 18,
        "flags": ["h_fwd", "h_rev", "v_fwd", "v_rev", "d_fwd_r", "d_fwd_l", "d_rev_r", "d_rev_l", "overlap"]
    }
}

ART_STYLES = {
    "cartoon": "Vibrant, thick lines, playful characters, high energy animation style.",
    "watercolor": "Soft edges, artistic paint textures, gentle feel, pastel colors, bleeding ink effects.",
    "sketch": "Hand-drawn pencil and pen look, high-contrast, clean detailed lines, cross-hatching.",
    "flat": "Modern vector-style, solid vibrant colors, clean geometric shapes, no gradients.",
    "isometric": "3D-perspective, organized technical layout, playful but structured, miniature world feel.",
    "cyberpunk": "Neon lights, futuristic city elements, high contrast darks and vibrant glows, synthwave aesthetic.",
    "origami": "Intricate paper-fold textures, sharp geometric creases, minimalist crafting look, paper material feel.",
    "steampunk": "Brass gears, copper pipes, Victorian industrial aesthetic, sepia tones, steam and clockwork elements.",
    "pixel-art": "Retro 8-bit video game aesthetic, blocky characters, limited color palette, nostalgic vibe.",
    "oil-painting": "Thick impasto brushstrokes, rich textures, classic fine art feel, dramatic lighting.",
    "crayon": "Childlike wax scribbles, rough texture, vibrant primary colors, playful school-day feel.",
    "blueprint": "Technical blue background, white thin architectural lines, drafting paper texture, engineering style.",
    "stained-glass": "Vibrant translucent colors, thick black lead outlines, mosaic patterns, light shining through glass.",
    "pop-art": "Ben-Day dots, comic book style, bold saturated colors, high-impact Warhol-inspired aesthetic.",
    "chalkboard": "Hand-drawn white chalk on a dusty black chalkboard background, classroom feel."
}

class ExplicitContentError(Exception):
    """Raised when the theme is flagged as explicit/inappropriate."""
    pass


def generate_words(theme, age, num_words, api_key, status_callback=None, max_retries=3):
    if not genai:
        raise ImportError("google-genai library not installed. Please rebuild the Docker image.")

    age_cfg = AGE_LEVELS.get(age, AGE_LEVELS["8-10"])
    target_num = num_words or age_cfg["num_words"]
    min_l, max_l = age_cfg["min_len"], age_cfg["max_len"]

    client = genai.Client(api_key=api_key)
    collected_words = []
    seen_lower = set()

    attempts = 0
    while len(collected_words) < target_num and attempts < max_retries:
        remaining = target_num - len(collected_words)
        if status_callback:
            status_callback(f"STEP_WORDS: Generating words (Attempt {attempts+1}, collecting {remaining} more)...")

        prompt = (f"Generate a list of {remaining} words about '{theme}' for a {age}-year-old. "
                  f"Words must be between {min_l} and {max_l} letters long. "
                  "CRITICAL: None of the words may contain spaces or hyphens. Use single words only. "
                  "Only refuse if the topic is clearly sexual, glorifies violence/drugs, or contains hate speech. "
                  "Fictional characters, video games, fantasy creatures, and pop culture are all acceptable. "
                  "If you must refuse, respond with ONLY the word REFUSED and nothing else. "
                  "Otherwise, provide ONLY the words separated by commas, no other text.")

        try:
            print(f"Generating {remaining} words using Gemini 2.5 Flash Lite (Batch {attempts+1})...", flush=True)
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            if not response.text:
                raise ExplicitContentError(f"The theme '{theme}' was flagged as inappropriate content.")
            response_text = response.text.strip()
            if response_text.upper() == "REFUSED":
                raise ExplicitContentError(f"The theme '{theme}' was flagged as inappropriate content.")
            batch = [w.strip() for w in response_text.split(',') if w.strip()]
            valid_batch = [w for w in batch if ' ' not in w and '-' not in w and min_l <= len(w) <= max_l]
            for w in valid_batch:
                key = w.lower()
                if key not in seen_lower:
                    seen_lower.add(key)
                    collected_words.append(w)
        except ExplicitContentError:
            raise
        except Exception as e:
            print(f"Word generation attempt {attempts+1} failed: {e}", flush=True)
            time.sleep(2 ** attempts)
        attempts += 1

    if not collected_words:
        raise Exception("Failed to generate any valid words after multiple attempts.")

    # Remove words that are substrings of other words (e.g. "CAT" inside "SCATTER")
    # These would be trivially found inside the longer word on the grid
    def remove_substrings(words):
        upper = [w.upper() for w in words]
        keep = []
        removed = []
        for i, w in enumerate(upper):
            is_sub = any(w in other and w != other for j, other in enumerate(upper) if i != j)
            if is_sub:
                removed.append(words[i])
            else:
                keep.append(words[i])
        return keep, removed

    collected_words, removed = remove_substrings(collected_words)
    if removed:
        print(f"Removed substring words: {', '.join(removed)}", flush=True)
        # Update seen set
        for w in removed:
            seen_lower.discard(w.lower())

    # If we lost words due to substring removal, try to collect replacements
    sub_attempts = 0
    while len(collected_words) < target_num and sub_attempts < max_retries:
        remaining = target_num - len(collected_words)
        if status_callback:
            status_callback(f"STEP_WORDS: Replacing {remaining} substring words...")
        prompt = (f"Generate a list of {remaining} words about '{theme}' for a {age}-year-old. "
                  f"Words must be between {min_l} and {max_l} letters long. "
                  "CRITICAL: None of the words may contain spaces or hyphens. Use single words only. "
                  f"Do NOT use any of these words: {', '.join(w.upper() for w in collected_words)}. "
                  "Provide ONLY the words separated by commas, no other text.")
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt
            )
            if response.text:
                batch = [w.strip() for w in response.text.strip().split(',') if w.strip()]
                valid_batch = [w for w in batch if ' ' not in w and '-' not in w and min_l <= len(w) <= max_l]
                for w in valid_batch:
                    key = w.lower()
                    if key not in seen_lower:
                        seen_lower.add(key)
                        collected_words.append(w)
                # Re-check for new substrings
                collected_words, newly_removed = remove_substrings(collected_words)
                if newly_removed:
                    print(f"Removed new substring words: {', '.join(newly_removed)}", flush=True)
                    for w in newly_removed:
                        seen_lower.discard(w.lower())
        except Exception as e:
            print(f"Substring replacement attempt failed: {e}", flush=True)
        sub_attempts += 1

    final_list = collected_words[:target_num]
    if status_callback:
        status_callback(f"STEP_GRID: Words generated: {', '.join(final_list)}", words=final_list)
    return final_list

def refine_styling_prompt(original_prompt, api_key):
    """
    Use the lite model to sanity-check and enhance the styling prompt.
    """
    try:
        client = genai.Client(api_key=api_key)
        system_prompt = (
            "You are an expert AI prompt engineer. Review and enhance the following image restyling prompt "
            "for clarity, artistic detail, and consistency. The prompt will be sent alongside an existing word search "
            "image — the model must restyle it, not generate a new image. "
            "CRITICAL CONSTRAINT: Every letter in the word search grid must be preserved EXACTLY as-is. "
            "The model must NOT change, substitute, rearrange, or re-render any letter in the grid. "
            "All letters must remain 100% visible and legible. "
            "Instruct the model to decorate the background and borders ONLY. "
            "Absolutely no artistic elements, textures, or overlays may conceal, blur, or overwrite the letters. "
            "Return only the enhanced version of the prompt, no other text."
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=f"{system_prompt}\n\nPROMPT TO ENHANCE: {original_prompt}"
        )
        if not response.text:
            print("Prompt refinement returned empty. Using original.")
            return original_prompt
        return response.text.strip()
    except Exception as e:
        print(f"Prompt refinement failed: {e}. Using original.")
        return original_prompt

def run_word_search_logic(words_list, theme, output_base, options, status_callback=None, multiplier=2.5):
    """
    Directly calls the WordSearch and WordSearchGenerator classes.
    """
    if status_callback:
        status_callback("STEP_GRID: Creating word search grid...")
    
    # Copy options to avoid mutating caller's dict, and add multiplier
    opts = dict(options)
    opts['multiplier'] = multiplier

    ws = WordSearch(words_list, opts)
    if not ws.generate():
        raise Exception("Could not place all words in the grid. Try fewer words.")
    # Save grids for later OCR verification
    grid_path = f"{output_base}_grid.json"
    with open(grid_path, 'w') as f:
        json.dump(ws.grid, f)
    answer_grid_path = f"{output_base}_answer_grid.json"
    with open(answer_grid_path, 'w') as f:
        json.dump(ws.get_answer_grid(), f)
    gen = WordSearchGenerator(ws)
    original_path = f"{output_base}_original.png"
    answer_path = f"{output_base}_answer.png"
    if status_callback:
        status_callback("STEP_TEMPLATE: Creating plain template...")
    gen.render_image(original_path, title=f"{theme} Word Search")
    if status_callback:
        status_callback("STEP_ANSWER: Creating answer sheet...")
    gen.render_image(answer_path, title=f"{theme} Word Search", answer_key=True)
    render_combined_pdf(output_base, has_styled=False)
    return True

def build_styling_prompt(theme, age, style_name, color_mode, ink_saver):
    """Build the base styling prompt from puzzle parameters."""
    color_desc = "Full color" if color_mode == "color" else "Black and white"
    ink_saver_desc = "Use an ink-saving style with high contrast and minimal dense dark areas." if ink_saver else ""
    style_desc = ART_STYLES.get(style_name.lower(), f"Artistic {style_name} style.")

    return (
        f"Restyle this existing word search image in a {style_name} style themed around '{theme}'. "
        f"{style_desc} {color_desc}. {ink_saver_desc} "
        f"Add a vibrant background featuring {theme} elements around and behind the existing grid. "
        f"CRITICAL RULES FOR THE GRID LETTERS: "
        "1. Every single letter in the grid must remain EXACTLY as it appears — "
        "do NOT change, rearrange, substitute, or re-render any letter. "
        "2. The letters must remain perfectly legible — no artistic elements, textures, "
        "characters, or overlays may obscure them. "
        "3. Only decorate the background, borders, and margins around the grid. "
        f"Restyle the title '{theme} Word Search' in a beautiful, artistic font that matches the {style_name} style. "
        f"Use a friendly, engaging artistic style suitable for a {age}-year-old."
    )


def _ocr_grid_from_variant(grid_img, grid_size, margin, s_cell, thresh, contrast=1.0):
    """
    Run image_to_boxes on one preprocessing variant and return a grid of detected letters.
    Returns ocr_grid[r][c] = detected letter or None.
    """
    grid_2x = grid_img.resize((grid_img.width * 2, grid_img.height * 2), Image.LANCZOS)
    s_margin = margin * 2
    s_cell_2x = s_cell * 2

    gray = ImageOps.grayscale(grid_2x)
    if contrast != 1.0:
        gray = ImageEnhance.Contrast(gray).enhance(contrast)
    bw = gray.point(lambda x: 0 if x < thresh else 255, '1')
    img_h = bw.height

    boxes = pytesseract.image_to_boxes(bw, config='--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    ocr_grid = [[None for _ in range(grid_size)] for _ in range(grid_size)]

    for line in boxes.strip().split('\n'):
        parts = line.split()
        if len(parts) < 5 or not parts[0].isalpha():
            continue
        ch = parts[0].upper()
        x1, y1_inv, x2, y2_inv = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
        y1 = img_h - y2_inv
        y2 = img_h - y1_inv
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        col = int((cx - s_margin) / s_cell_2x)
        row = int((cy - s_margin) / s_cell_2x)
        if 0 <= row < grid_size and 0 <= col < grid_size:
            ocr_grid[row][col] = ch

    return ocr_grid


def verify_styled_image(styled_path, expected_grid):
    """
    Use Tesseract OCR to verify that the styled image still contains the correct grid letters.
    Uses per-cell ensemble voting: runs multiple preprocessing variants and for each answer
    cell, checks if ANY variant read the correct letter. A cell only fails if NO variant
    can find the expected letter. This eliminates tolerance for OCR noise — zero tolerance,
    zero false passes.
    Returns (passed, accuracy, details) tuple.
    """
    if not pytesseract:
        print("pytesseract not available, skipping OCR verification.", flush=True)
        return True, 1.0, "OCR not available"

    try:
        grid_size = len(expected_grid)
        if grid_size == 0:
            return True, 1.0, "Empty grid"

        # Count total answer cells
        total_answer = sum(1 for row in expected_grid for ch in row if ch.strip())

        img = Image.open(styled_path)

        # Crop to just the grid area to avoid false matches from title/word list
        from generator import WordSearchGenerator as Gen
        padding = Gen.PADDING
        cell_size = Gen.CELL_SIZE
        canonical_w = grid_size * cell_size + 2 * padding
        scale = img.width / canonical_w
        margin = cell_size * 0.5 * scale
        crop_left = max(0, int(padding * scale - margin))
        crop_top = max(0, int(padding * scale - margin))
        crop_right = min(img.width, int((padding + grid_size * cell_size) * scale + margin))
        crop_bottom = min(img.height, int((padding + grid_size * cell_size) * scale + margin))
        grid_img = img.crop((crop_left, crop_top, crop_right, crop_bottom))

        s_cell = cell_size * scale

        # Per-cell ensemble: collect all OCR results per cell across all variants
        # cell_readings[r][c] = set of letters detected across all variants
        cell_readings = [[set() for _ in range(grid_size)] for _ in range(grid_size)]

        variant_count = 0
        for contrast in [1.0, 2.0, 3.0, 5.0]:
            for thresh in [80, 128, 180, 200]:
                ocr_grid = _ocr_grid_from_variant(
                    grid_img, grid_size, margin, s_cell, thresh, contrast
                )
                variant_count += 1
                for r in range(grid_size):
                    for c in range(grid_size):
                        if ocr_grid[r][c] is not None:
                            cell_readings[r][c].add(ocr_grid[r][c])

        img.close()
        print(f"OCR: ran {variant_count} preprocessing variants", flush=True)

        # Check each answer cell: pass if expected letter appears in ANY variant
        mismatches = []
        for r in range(grid_size):
            for c in range(grid_size):
                exp = expected_grid[r][c].strip().upper()
                if not exp:
                    continue
                readings = cell_readings[r][c]
                if exp in readings:
                    continue  # At least one variant got the right letter
                # Failed — report what was found instead
                if not readings:
                    found = '?'
                else:
                    found = ','.join(sorted(readings))
                mismatches.append({
                    'row': r + 1, 'col': c + 1,
                    'expected': exp, 'found': found
                })

        matched = total_answer - len(mismatches)
        accuracy = matched / total_answer if total_answer > 0 else 1.0

        if mismatches:
            mismatch_lines = [
                f"({m['row']},{m['col']}): {m['expected']}→{m['found']}"
                for m in mismatches
            ]
            print(f"OCR mismatches: {'; '.join(mismatch_lines)}", flush=True)

        if len(mismatches) == 0:
            details = f"OCR passed: {matched}/{total_answer} answer letters verified across {variant_count} variants"
            print(f"OCR verification PASSED: {details}", flush=True)
            return True, 1.0, details, []

        details = f"OCR flagged {len(mismatches)} letter(s) across {total_answer} for review ({accuracy:.0%} matched)"
        print(f"OCR verification FAILED: {details}", flush=True)
        return False, accuracy, details, mismatches

    except Exception as e:
        print(f"OCR verification error: {e}", flush=True)
        return True, 1.0, f"OCR error (skipped): {e}", []


def render_style_check(styled_path, mismatches, grid_size, output_path):
    """
    Render a copy of the styled image with red boxes around cells that failed OCR.
    Shows the expected letter above the box and what OCR detected below it.
    Mismatches use 1-indexed row/col.
    """
    from generator import WordSearchGenerator as Gen
    padding = Gen.PADDING
    cell_size = Gen.CELL_SIZE

    img = Image.open(styled_path).copy()
    canonical_w = grid_size * cell_size + 2 * padding
    scale = img.width / canonical_w
    draw = ImageDraw.Draw(img)

    # Load a font for the annotations
    label_size = max(12, int(cell_size * scale * 0.3))
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
        if os.path.exists(font_path):
            label_font = ImageFont.truetype(font_path, label_size)
        else:
            label_font = ImageFont.load_default()
    except Exception:
        label_font = ImageFont.load_default()

    for m in mismatches:
        r = m['row'] - 1  # convert to 0-indexed
        c = m['col'] - 1
        x1 = int((padding + c * cell_size) * scale)
        y1 = int((padding + r * cell_size) * scale)
        x2 = int((padding + (c + 1) * cell_size) * scale)
        y2 = int((padding + (r + 1) * cell_size) * scale)
        cx = (x1 + x2) // 2

        # Draw thick red rectangle
        for offset in range(3):
            draw.rectangle(
                [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                outline=(255, 0, 0)
            )

        # Draw expected letter above the box with a red background pill
        expected = m['expected']
        found = m['found']
        # Label above: expected letter
        label_y = y1 - label_size - 4
        bbox = draw.textbbox((cx, label_y), expected, font=label_font, anchor="mt")
        pad = 3
        draw.rectangle(
            [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
            fill=(255, 0, 0)
        )
        draw.text((cx, label_y), expected, fill=(255, 255, 255), font=label_font, anchor="mt")

        # Label below: what OCR found
        found_label = f"→{found}"
        label_y_bot = y2 + 2
        bbox_bot = draw.textbbox((cx, label_y_bot), found_label, font=label_font, anchor="mt")
        draw.rectangle(
            [bbox_bot[0] - pad, bbox_bot[1] - 1, bbox_bot[2] + pad, bbox_bot[3] + 1],
            fill=(80, 0, 0)
        )
        draw.text((cx, label_y_bot), found_label, fill=(255, 200, 200), font=label_font, anchor="mt")

    img.save(output_path)
    img.close()
    print(f"Style check image saved: {output_path} ({len(mismatches)} cells highlighted)", flush=True)


def apply_styling(theme, age, style_name, color_mode, ink_saver, output_base, api_key, status_callback=None, model_name=None):
    if status_callback:
        status_callback("STEP_STYLE: Refining prompt with AI...")
    base_prompt = build_styling_prompt(theme, age, style_name, color_mode, ink_saver)
    refined_prompt = refine_styling_prompt(base_prompt, api_key)
    if status_callback:
        status_callback("STEP_STYLE: Stylizing with Gemini...")
    original_path = f"{output_base}_original.png"
    styled_path = f"{output_base}_styled.png"
    success = style.style_image(original_path, refined_prompt, styled_path, api_key, model_name=model_name)
    print(f"Styling result: {'success' if success else 'failed'}", flush=True)

    if success:
        # Verify styled image letters match the original grid
        ocr_warning = None
        answer_grid_path = f"{output_base}_answer_grid.json"
        if os.path.exists(answer_grid_path):
            with open(answer_grid_path, 'r') as f:
                expected_grid = json.load(f)
            print("Starting OCR letter verification...", flush=True)
            if status_callback:
                status_callback("STEP_STYLE: Verifying letter integrity...")
            passed, accuracy, details, mismatches = verify_styled_image(styled_path, expected_grid)
            if not passed and mismatches:
                ocr_warning = details
                grid_size = len(expected_grid)
                style_check_path = f"{output_base}_style_check.png"
                render_style_check(styled_path, mismatches, grid_size, style_check_path)
                print(f"OCR verification WARNING: {details}", flush=True)
        render_combined_pdf(output_base, has_styled=True)
        if status_callback:
            if ocr_warning:
                status_callback(f"STEP_COMPLETE_WITH_WARNING: {ocr_warning}")
            else:
                status_callback("STEP_COMPLETE: Styling complete!")
        return True
    else:
        if status_callback:
            status_callback("STEP_STYLE_FAILED: Styling failed or timed out.")
        return False

def render_combined_pdf(output_base, has_styled=False):
    """Render all generated images into a single multi-page PDF."""
    pdf_path = f"{output_base}_combined.pdf"
    page_w, page_h = letter

    # Collect pages in order: styled (if exists), original, answer
    pages = []
    if has_styled:
        styled_path = f"{output_base}_styled.png"
        if os.path.exists(styled_path):
            pages.append(styled_path)
    original_path = f"{output_base}_original.png"
    if os.path.exists(original_path):
        pages.append(original_path)
    answer_path = f"{output_base}_answer.png"
    if os.path.exists(answer_path):
        pages.append(answer_path)

    if not pages:
        return None

    c = canvas.Canvas(pdf_path, pagesize=letter)
    margin = 36  # 0.5 inch margins
    usable_w = page_w - 2 * margin
    usable_h = page_h - 2 * margin

    for img_path in pages:
        with Image.open(img_path) as img:
            img_w, img_h = img.size

        # Scale to fit page while preserving aspect ratio
        scale = min(usable_w / img_w, usable_h / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        x = margin + (usable_w - draw_w) / 2
        y = margin + (usable_h - draw_h) / 2

        c.drawImage(ImageReader(img_path), x, y, width=draw_w, height=draw_h)
        c.showPage()

    c.save()
    return pdf_path


def run_pipeline(theme, age, num_words, style_name, color_mode, ink_saver, apply_style, output_base, api_key, direction_overrides=None, status_callback=None, multiplier=2.5, model_name=None):
    words = generate_words(theme, age, num_words, api_key, status_callback=status_callback)
    if direction_overrides:
        options = direction_overrides
    else:
        age_cfg = AGE_LEVELS.get(age, AGE_LEVELS["8-10"])
        flags = age_cfg["flags"]
        options = {
            'h_fwd': 'h_fwd' in flags, 'h_rev': 'h_rev' in flags,
            'v_fwd': 'v_fwd' in flags, 'v_rev': 'v_rev' in flags,
            'd_fwd_r': 'd_fwd_r' in flags, 'd_fwd_l': 'd_fwd_l' in flags,
            'd_rev_r': 'd_rev_r' in flags, 'd_rev_l': 'd_rev_l' in flags,
            'overlap': 'overlap' in flags
        }
    run_word_search_logic(words, theme, output_base, options, status_callback=status_callback, multiplier=multiplier)
    if apply_style:
        styled_success = apply_styling(theme, age, style_name, color_mode, ink_saver, output_base, api_key, status_callback=status_callback, model_name=model_name)
        return words, styled_success
    if status_callback:
        status_callback("STEP_COMPLETE: Generation complete!")
    return words, False
