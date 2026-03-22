# Word Search AI Pipeline - Expected Behavior & Specification

This document serves as the exhaustive specification for the Word Search AI Pipeline application, consolidating all features, architectural decisions, and user experience requirements discussed.

## 1. System Overview
The Word Search AI Pipeline is a full-stack application designed to generate professional-quality, themed word search puzzles for children. It integrates advanced AI for content generation and artistic styling, providing both a command-line interface and a modern web dashboard.

## 2. Core Generation Engine
The backend engine (Python) is responsible for the mathematical placement of words and graphic rendering.

### 2.1 Placement Algorithm
- **Backtracking Logic:** Uses a recursive backtracking algorithm to ensure 100% placement of the word list. If a word cannot fit, the engine backtracks to reposition previous words.
- **Dynamic Grid Sizing:**
    - The grid is always square ($N \times N$).
    - $N$ is calculated as $\max(W_{max} + 2, \lceil \sqrt{multiplier \times L} \rceil)$, where $W_{max}$ is the longest word and $L$ is the total letter count.
    - **Default Multiplier:** 2.5 (controllable via UI/CLI).
- **Directional Support:** Supports all 8 orientations: Horizontal (Fwd/Rev), Vertical (Fwd/Rev), and Diagonal (4 directions).
- **Overlap Logic:** Supports "True Union" overlapping, where words can share common letters.

### 2.2 Content Generation (Gemini AI)
- **Model:** Uses `gemini-2.5-flash-lite` for cost-effective, high-speed text generation.
- **Robustness:** 
    - Words must never contain spaces or hyphens.
    - The system performs a validation loop: any invalid words are discarded, and the AI is prompted for a "top-up" batch until the target count is met.
- **Age Mapping:**
    - **5-6 (K):** 3-5 letters, 6 words, Horizontal Forward only.
    - **6-8 (G1-2):** 4-6 letters, 9 words, H/V Forward.
    - **8-10 (G3-4):** 5-8 letters, 12 words, H/V/D Forward.
    - **11-12 (G5-6):** 6-10 letters, 15 words, H/V/D Forward/Reverse + Overlap.
    - **12+:** 7+ letters, 18+ words, All directions + Overlap.

## 3. Graphic Design & Outputs
The tool produces three distinct high-resolution PNG images (and optional multi-page PDF).

### 3.1 Visual Standards
- **Typography:** Uses massive, bold **DejaVu Sans Mono** fonts.
- **Grid Layout:** "Grid-less" design (no visible lines). Black text on white background for the base versions.
- **Padding:** Strict 1-inch padding between the grid border and the image edge.
- **Word Checklist:** Located at the bottom, organized in **3 even columns**. Each word has a checkbox.
- **Title:** Centered at the top. In the base version, the font size is reduced (30pt) to prevent overflow for long titles.

### 3.2 The Answer Key
- **Color-Coding:** Every word is assigned a unique, distinct soft color.
- **RGB Blending:** If two words overlap, the cell background is filled with the mathematical average RGB value of both colors.
- **Checklist:** The checkboxes at the bottom are filled with the word's assigned color for easy reference.

### 3.3 Artistic Styling (Banana Pro 2)
- **Model:** Uses `gemini-3.1-flash-image-preview`.
- **Workflow:** The "Plain" version is sent to the AI with a prompt refined by the Lite model.
- **Artistic Title:** The AI is instructed to rewrite the title in a beautiful font matching the art style.
- **Legibility:** The AI is strictly forbidden from placing textures or objects over the grid letters.

## 4. Web Dashboard (UX/UI)
A modern, responsive dashboard built with FastAPI, Tailwind CSS, and HTMX.

### 4.1 Generation Workflow
- **Form:** All parameters (Theme, Age, Style, Multiplier, Word Count, Direction Overrides) are customizable.
- **Review Mode:** Option to pause after word generation to allow user editing before building the grid.
- **Loading State:** 
    - Form is disabled on submit.
    - A **Progress Modal** appears with a **Count-up Timer**.
    - A **Checklist** updates in real-time: `Building word list` -> `Creating template` -> `Creating answer sheet` -> `Stylizing`.
- **Completion:** The modal closes and the **Detail View** opens automatically.

### 4.2 Gallery & Detail View
- **Search:** Instant filtering by theme, style, or words contained in the puzzle.
- **Gallery Cards:** Clickable cards showing the styled thumbnail.
- **Detail Modal:** Displays the Styled Masterpiece, the Plain Grid, and the Answer Key side-by-side.
- **Zoom:** Clicking any image in the modal opens a fullscreen **Zoom Overlay**.
- **Downloads:** Buttons for individual files or a consolidated ZIP.

## 5. Technical Architecture

### 5.1 Concurrency & Multiprocessing
- **Non-blocking:** Heavy tasks run in `multiprocessing.Process` so the web server remains responsive.
- **Race Condition Prevention:** Registry access is protected by `threading.Lock`.
- **Database:** SQLite with `timeout=15` to handle concurrent writes from child processes.
- **Watchdog:** A startup handler cleans up "stale" pending tasks. A background monitor kills tasks exceeding 300s.

### 5.2 Persistence & Storage
- **Directory:** All data lives in `/app/storage`.
- **Database:** `word_search.db` is stored inside the storage folder for volume persistence.
- **Filenames:** Files are stored as `{md5_hash}_[type].png` but delivered to the user with theme-based names (e.g., `Egypt_Styled.png`).

### 5.3 Error Handling
- **Styling Retries:** If Gemini fails/times out, the user can click a "Retry Styling" button.
- **Cleanup:** If a puzzle fails styling twice, all associated files and the DB record are automatically deleted.

## 6. Deployment
- **Docker:** Single image with a dispatcher entrypoint (`web`, `orchestrate`, `generator`, `style`).
- **Secrets:** API keys loaded from `GEMINI_API_KEY` or `API_KEY` (env or `.env` file).
