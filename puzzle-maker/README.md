# Word Search AI Pipeline (Gemini Edition)

A high-performance tool to generate themed, age-appropriate word search puzzles. Uses **Google Gemini 2.5 Flash Lite** for fast word generation and **Gemini 3.1 Flash Image** (Banana Pro 2) for professional-grade styling.

## Key Features
- **Web Dashboard:** Modern, sleek interface for generating and managing puzzles.
- **Gemini-Native:** Fully integrated with the latest `google-genai` SDK for both text and images.
- **AI Prompt Refinement:** Every styling request is automatically enhanced by Gemini Lite to add artistic detail, lighting, and texture cues.
- **15 Diverse Art Styles:** Choose from a wide range of styles including `Cartoon`, `Watercolor`, `Cyberpunk`, `Steampunk`, `Origami`, `Pixel-Art`, and more.
- **Robust Word Generation:** Automatically filters out invalid words (spaces/hyphens) and "tops up" the list via AI to ensure high-quality grids.
- **Granular Progress Tracking:** Real-time checklist and count-up timer in the UI to monitor word list building, grid creation, and styling.
- **Interactive Visualization:** Automatic popup of the full preview upon completion, featuring a fullscreen zoom-in overlay.
- **Styling Retry Logic:** Intelligent handling of Gemini timeouts with a one-click "Retry Styling" option that preserves your generated words and grid.
- **Color-Coded Solutions:** The answer key features unique word colors and **RGB blending** for overlapping letters.
- **Persistence:** Database and images are stored in a single directory for easy backup and persistence between Docker runs.

## Age-Based Difficulty Mapping

| Age Range | Grade Level | Directions Enabled |
| :--- | :--- | :--- |
| **5-6** | Kindergarten | Horizontal Forward ONLY |
| **6-8** | Grade 1-2 | Horizontal & Vertical Forward |
| **8-10** | Grade 3-4 | H/V/Diagonal Forward |
| **11-12** | Grade 5-6 | H/V/D Forward & Reverse + Overlap |
| **12+** | Middle/High | All 8 Directions + Overlap |

## Quick Start (Docker)

1.  **Prepare your environment:**
    ```bash
    mkdir -p storage data
    touch .env # Add GEMINI_API_KEY=your_key_here
    ```

2.  **Launch the Web Interface (Recommended):**
    This command mounts the `storage` directory for images and `data` for the database to persist between restarts.
    ```bash
    docker run --rm -p 8000:8000 \
      -v "$(pwd)/storage:/app/storage" \
      -v "$(pwd)/data:/app/data" \
      -v "$(pwd)/.env:/app/.env" \
      word-search-generator web
    ```
    Visit `http://localhost:8000` to start generating!

3.  **CLI Usage (Orchestrate):**
    Generates words, creates the grid, and **automatically restyles** it.
    ```bash
    docker run --rm \
      -v "$(pwd)/storage:/app/storage" \
      -v "$(pwd)/data:/app/data" \
      -v "$(pwd)/.env:/app/.env" \
      word-search-generator orchestrate \
      --theme "Mermaids and Corgis" \
      --age "8-10" \
      --style "watercolor" \
      --apply-style \
      --output "/app/storage/corgis.png"
    ```

## CLI Arguments (`orchestrate`)

| Argument | Description |
| :--- | :--- |
| `--theme` | The topic of the word search (e.g., "Deep Sea", "Space"). |
| `--age` | Target age level (5-6, 6-8, 8-10, 11-12, 12+). |
| `--style` | Illustration style (e.g., `cartoon`, `watercolor`, `cyberpunk`, `pixel-art`, etc.). |
| `--apply-style` | Automatically triggers the Banana Pro 2 styling step. |
| `--color-mode` | Color mode for the final illustration (`color` or `bw`). |
| `--ink-saver` | Focuses on line art and high contrast to save printer ink. |
| `--num-words` | (Optional) Manual override for word count (6-33, in multiples of 3). |
| `--output` | Path to save the primary image (Answer key is saved alongside). |

## Styling Cost Estimates

The dominant cost per puzzle is the image generation call. Text-only calls (word generation, prompt refinement via `gemini-2.5-flash-lite`) are negligible.

### Gemini 3.1 Flash Image Preview (default — `gemini-3.1-flash-image-preview`)

Image output priced at **$60 / 1M tokens**.

| Age | Words | Output Size | Tokens | Cost |
| :--- | :--- | :--- | :--- | :--- |
| 5-6 | 6 | ~1K | 1,120 | $0.067 |
| 6-8 | 9 | ~1K | 1,120 | $0.067 |
| 8-10 | 12 | ~2K | 1,680 | $0.101 |
| 11-12 | 15 | ~2K | 1,680 | $0.101 |
| 12+ | 18 | ~2K | 1,680 | $0.101 |
| Max | 33 | ~4K | 2,520 | $0.151 |

### Gemini 3 Pro Image Preview (`gemini-3-pro-image-preview`)

Image output priced at **$120 / 1M tokens**.

| Age | Words | Output Size | Tokens | Cost |
| :--- | :--- | :--- | :--- | :--- |
| 5-6 | 6 | ~1K | 1,120 | $0.134 |
| 6-8 | 9 | ~1K | 1,120 | $0.134 |
| 8-10 | 12 | ~2K | 1,120 | $0.134 |
| 11-12 | 15 | ~2K | 1,120 | $0.134 |
| 12+ | 18 | ~2K | 1,120 | $0.134 |
| Max | 33 | ~4K | 2,000 | $0.240 |

> Output size tiers are approximate, based on the generated grid dimensions for each age level with default multiplier (2.5). Actual token counts may vary. See [Google's image generation pricing](https://ai.google.dev/gemini-api/docs/pricing) for current rates.

## Technical Breakdown
For a detailed overview of all engine capabilities, including grid logic and RGB blending, see [docs/FEATURES.md](docs/FEATURES.md).

## Development
To rebuild the image after making local changes:
```bash
docker build -t word-search-generator .
```
