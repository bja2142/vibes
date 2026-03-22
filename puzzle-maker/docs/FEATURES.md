# Word Search AI Pipeline - Feature Documentation

This document provides a detailed overview of all the capabilities and technical features of the Word Search AI Pipeline.

## 1. Core Generation Engine
The engine responsible for creating the word search grid.
- **Backtracking Algorithm:** Guarantees that all words are placed on the grid if a solution exists.
- **Dynamic Grid Sizing:** Automatically calculates the optimal grid size. It ensures the grid is large enough for the longest word and that the total area is at least 2.5 times the total number of letters in the input words.
- **Full Directional Support:** Supports all 8 possible word orientations:
    - Horizontal (Forward and Reverse)
    - Vertical (Forward and Reverse)
    - Diagonal (Forward-Right, Forward-Left, Reverse-Right, Reverse-Left)
- **Overlap Support:** Can be toggled to allow words to share common letters at intersections.
- **Random Padding:** Automatically fills all empty grid spaces with random uppercase letters.

## 2. Graphic Design & Rendering
Generates high-quality, printable graphics.
- **Grid-less Design:** Clean black text on a white background with no visible grid lines for a professional look.
- **1-Inch Padding:** Maintains a strict 1-inch border around the entire word search.
- **Bold Typography:** Uses massive, bold monospace fonts (DejaVu Sans Mono) for maximum readability.
- **Dynamic Sizing:** Automatically adjusts image and PDF page height based on the length of the word list.
- **Multi-Format Support:**
    - **PNG:** High-resolution images for digital sharing or quick printing.
    - **PDF:** Professional multi-page documents (Page 1: Puzzle, Page 2: Answer Key).

## 3. Advanced Answer Key Features
Innovative tools to make grading easy for parents and teachers.
- **Color-Coding:** Each word in the solution is assigned a unique, high-contrast color.
- **RGB Blending (Union):** If two words overlap in the same cell, the cell background color is the mathematical union (average RGB) of the words' colors.
- **Color-Coded Checklist:** The word list at the bottom of the answer key is also color-coded, with matching colored boxes next to each word.
- **Clean Reference:** The answer key is always kept clean and unstylized, even if the puzzle has been processed by AI styling.

## 4. AI Orchestration (Gemini)
Automates the creation of themed content.
- **Age-Appropriate Difficulty:** Automatically maps user-provided age ranges to specific grid settings:
    - **Age 5-6:** Horizontal Forward only.
    - **Age 6-8:** Horizontal & Vertical Forward.
    - **Age 8-10:** Adds Forward Diagonals.
    - **Age 11-12:** Adds Reverse and Overlaps.
    - **Age 12+:** All 8 directions enabled.
- **Robust Word Generation:**
    - Uses `gemini-2.5-flash-lite` for cost-effective, high-speed word list creation.
    - **Validation Loop:** Automatically detects and filters out words with spaces or hyphens.
    - **Auto-Top-up:** If the AI generates invalid words, the system automatically requests a second batch to ensure the desired word count is reached.
- **Artistic Styling (Banana Pro 2):**
    - Uses `gemini-3.1-flash-image-preview` to transform the grid into a themed illustration.
    - **Artistic Title Rewriting:** Instructs the AI to rewrite the puzzle title in a font that matches the selected art style.
    - **Style Support:** Choose from `cartoon`, `watercolor`, `sketch`, `flat`, `isometric`, or provide your own custom style string.

## 5. Web Dashboard
A user-friendly interface for the entire pipeline.
- **FastAPI Powered:** High-performance backend with asynchronous task processing.
- **Searchable Gallery:** Instantly search through past puzzles by theme, words within the puzzle, or the art style used.
- **Detailed View:** A modal interface to compare the Styled, Original, and Answer versions of every puzzle.
- **Manual Overrides:** Fine-tune word counts (6-33 in multiples of 3) and specific grid directions directly from the UI.
- **ZIP Downloads:** Download all versions of a puzzle at once in a single ZIP file.
- **Data Persistence:** All puzzle metadata and generated files are stored in a persistent SQLite database and storage directory.

## 6. Deployment & CLI
- **Dockerized:** Fully portable environment with all libraries and system fonts pre-installed.
- **Dispatcher Entrypoint:** A single Docker image that can run as a `web` server, an AI `orchestrate` pipeline, or a manual `generator`.
- **Environment Support:** Seamlessly reads API keys from Docker flags or local `.env` files.
