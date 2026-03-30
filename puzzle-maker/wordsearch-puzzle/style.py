import argparse
import os
import sys
import io
from PIL import Image
from dotenv import load_dotenv

# Load local .env if it exists
load_dotenv()

# Use the newer google-genai library for Banana Pro 2 (Gemini 3.1 Flash Image)
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None

def style_image(image_path, prompt, output_path, api_key, model_name=None):
    """
    Apply styling to an image using Banana Pro 2 (Gemini 3.1 Flash Image).
    This model is multimodal and uses the generate_content method.
    """
    if not genai:
        print("Error: google-genai library not installed. Styling skipped.", flush=True)
        return False

    if not api_key:
        print("Error: API_KEY not provided for styling. Styling skipped.", flush=True)
        return False

    print(f"Applying Banana Pro 2 styling to: {image_path}", flush=True)
    print(f"Styling Prompt: {prompt}", flush=True)

    try:
        # Initialize client without global http_options
        client = genai.Client(api_key=api_key)

        with Image.open(image_path) as source_img:
            # Call gemini-3.1-flash-image-preview (Banana Pro 2) using generate_content
            # as it is a native multimodal model.
            print("Sending request to Gemini (this may take 20-40 seconds)...", flush=True)
            use_model = model_name or 'gemini-3.1-flash-image-preview'
            print(f"Using model: {use_model}", flush=True)
            response = client.models.generate_content(
                model=use_model,
                contents=[source_img, prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"]
                )
            )

        # Inspect response for safety blocks or errors
        if not response.candidates:
            print("Error: No candidates returned from API. The request might have been blocked.", flush=True)
            return False

        candidate = response.candidates[0]
        if candidate.finish_reason and candidate.finish_reason != 'STOP':
            print(f"Warning: Image generation finished with reason: {candidate.finish_reason}", flush=True)
            if candidate.safety_ratings:
                print(f"Safety Ratings: {candidate.safety_ratings}", flush=True)

        # Extract image from response parts
        if candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                if part.inline_data:
                    image_bytes = part.inline_data.data
                    with Image.open(io.BytesIO(image_bytes)) as styled_image:
                        styled_image.save(output_path)
                    print(f"Successfully saved styled image to: {output_path}", flush=True)
                    return True

        print("Error: No image data found in the response parts. Check safety filters.", flush=True)
        return False

    except Exception as e:
        print(f"Styling failed with an exception: {e}", flush=True)
        return False

def main():
    parser = argparse.ArgumentParser(description="Apply Banana Pro 2 AI Styling to an Image")
    parser.add_argument("--image", required=True, help="Path to the base word search image.")
    parser.add_argument("--prompt", required=True, help="Styling prompt.")
    parser.add_argument("--output", required=True, help="Path to save the styled image.")
    
    args = parser.parse_args()
    
    # Flexible API Key Loading
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")

    if not api_key:
        print("Error: GEMINI_API_KEY or API_KEY environment variable not set.", flush=True)
        sys.exit(1)

    style_image(args.image, args.prompt, args.output, api_key)

if __name__ == "__main__":
    main()
