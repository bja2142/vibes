import argparse
import sys
import os
from word_search import WordSearch
from generator import WordSearchGenerator

def main():
    parser = argparse.ArgumentParser(description="Generate a word search grid.")
    parser.add_argument("--words", required=True, help="Comma-separated list of words or path to a file.")
    parser.add_argument("--title", default="Word Search", help="Title for the word search.")
    parser.add_argument("--output", default="word_search.pdf", help="Output file path (PNG or PDF).")
    parser.add_argument("--h-fwd", action="store_true", default=True, help="Allow horizontal forward.")
    parser.add_argument("--h-rev", action="store_true", help="Allow horizontal reverse.")
    parser.add_argument("--v-fwd", action="store_true", help="Allow vertical forward.")
    parser.add_argument("--v-rev", action="store_true", help="Allow vertical reverse.")
    parser.add_argument("--d-fwd-r", action="store_true", help="Allow diagonal forward right.")
    parser.add_argument("--d-fwd-l", action="store_true", help="Allow diagonal forward left.")
    parser.add_argument("--d-rev-r", action="store_true", help="Allow diagonal reverse right.")
    parser.add_argument("--d-rev-l", action="store_true", help="Allow diagonal reverse left.")
    parser.add_argument("--overlap", action="store_true", help="Allow words to overlap.")
    
    args = parser.parse_args()

    # Load words
    if os.path.isfile(args.words):
        with open(args.words, 'r') as f:
            words = [line.strip() for line in f if line.strip()]
    else:
        words = [w.strip() for w in args.words.split(',') if w.strip()]

    # Filter invalid words (spaces, hyphens)
    words = [w for w in words if ' ' not in w and '-' not in w]

    if not words:
        print("Error: No valid words provided.")
        sys.exit(1)

    options = {
        'h_fwd': args.h_fwd,
        'h_rev': args.h_rev,
        'v_fwd': args.v_fwd,
        'v_rev': args.v_rev,
        'd_fwd_r': args.d_fwd_r,
        'd_fwd_l': args.d_fwd_l,
        'd_rev_r': args.d_rev_r,
        'd_rev_l': args.d_rev_l,
        'overlap': args.overlap
    }

    print(f"Generating word search with {len(words)} words...", flush=True)
    ws = WordSearch(words, options)
    
    if not ws.generate():
        print("Error: Could not place all words in the grid. Try fewer words or a smaller list.", flush=True)
        sys.exit(1)

    gen = WordSearchGenerator(ws)

    ext = os.path.splitext(args.output)[1].lower()
    print(f"Saving Image to {args.output}...", flush=True)
    gen.render_image(args.output, title=args.title)
    ans_output = f"{os.path.splitext(args.output)[0]}_answer{ext}"
    print(f"Saving Answer Key to {ans_output}...", flush=True)
    gen.render_image(ans_output, title=args.title, answer_key=True)

    print("Done!", flush=True)

if __name__ == "__main__":
    main()
