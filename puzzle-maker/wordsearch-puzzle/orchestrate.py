import argparse
import os
import sys
from dotenv import load_dotenv
import core

# Load local .env if it exists
load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Orchestrate Gemini-Themed Word Search")
    parser.add_argument("--theme", required=True)
    parser.add_argument("--age", choices=core.AGE_LEVELS.keys(), default="8-10")
    parser.add_argument("--output", default="output/final_search.png")
    parser.add_argument("--num-words", type=int)
    parser.add_argument("--style", default="cartoon", help="Illustration style.")
    parser.add_argument("--color-mode", choices=["color", "bw"], default="color", help="Color mode.")
    parser.add_argument("--ink-saver", action="store_true")
    parser.add_argument("--apply-style", action="store_true", help="Automatically restyle the grid using Banana Pro 2.")
    
    args = parser.parse_args()
    
    # Flexible API Key Loading
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY or API_KEY environment variable not set.", flush=True)
        sys.exit(1)

    try:
        words, has_styled = core.run_pipeline(
            theme=args.theme,
            age=args.age,
            num_words=args.num_words,
            style_name=args.style,
            color_mode=args.color_mode,
            ink_saver=args.ink_saver,
            apply_style=args.apply_style,
            output_base=os.path.splitext(args.output)[0],
            api_key=api_key
        )
        print(f"Words generated: {', '.join(words)}", flush=True)
        print(f"Base graphic saved to: {args.output}", flush=True)
        if has_styled:
            styled_path = f"{os.path.splitext(args.output)[0]}_styled{os.path.splitext(args.output)[1]}"
            print(f"Styled graphic saved to: {styled_path}", flush=True)
        
        ans_path = f"{os.path.splitext(args.output)[0]}_answer{os.path.splitext(args.output)[1]}"
        print(f"Answer key saved to: {ans_path}", flush=True)

        pdf_path = f"{os.path.splitext(args.output)[0]}_combined.pdf"
        print(f"Combined PDF saved to: {pdf_path}", flush=True)
        
    except Exception as e:
        print(f"Pipeline failed: {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
