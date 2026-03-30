from PIL import Image, ImageDraw, ImageFont
import os

class WordSearchGenerator:
    DPI = 96
    PADDING = 192 # 2 inches — extra whitespace gives Gemini room for artistic elements
    CELL_SIZE = 70 # Even larger cell
    FONT_SIZE = 60 # Filling 85% of cell
    WORD_LIST_FONT_SIZE = 35 # Huge words

    def __init__(self, ws):
        self.ws = ws

    def _get_font(self, size, bold=False):
        # Explicit paths inside the Docker container
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
        
        try:
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, size)
            
            # Fallback for local dev
            local_fonts = [
                "Courier-Bold" if bold else "Courier",
                r"C:\Windows\Fonts\courbd.ttf" if bold else r"C:\Windows\Fonts\cour.ttf",
            ]
            for f in local_fonts:
                try:
                    return ImageFont.truetype(f, size)
                except:
                    continue
            
            print(f"Warning: Could not load font {font_path}. Falling back to default.")
            return ImageFont.load_default()
        except Exception as e:
            print(f"Font loading error: {e}")
            return ImageFont.load_default()

    def _blend_colors(self, colors):
        if not colors:
            return (255, 255, 255) # White
        if len(colors) == 1:
            return colors[0]
        
        # Simple RGB averaging for union
        r = sum(c[0] for c in colors) // len(colors)
        g = sum(c[1] for c in colors) // len(colors)
        b = sum(c[2] for c in colors) // len(colors)
        return (r, g, b)

    def render_image(self, output_path, title=None, answer_key=False):
        size = self.ws.grid_size
        answer_data = self.ws.get_answer_data()
        
        # Word List Layout (Sorted alphabetically for easy reference)
        # Using ws.placed_words to get colors for each word
        sorted_placed_words = sorted(self.ws.placed_words, key=lambda x: x[0])
        words_per_row = 3
        word_rows = (len(sorted_placed_words) + words_per_row - 1) // words_per_row
        word_list_height = word_rows * 70 + 100

        # Calculate image dimensions
        img_w = size * self.CELL_SIZE + 2 * self.PADDING
        img_h = self.PADDING + size * self.CELL_SIZE + self.PADDING + word_list_height + 96  # 1 inch bottom margin
        
        image = Image.new('RGB', (img_w, img_h), color='white')
        draw = ImageDraw.Draw(image)
        font = self._get_font(self.FONT_SIZE, bold=True)
        title_font = self._get_font(30, bold=True) # Reduced to half (was 60)
        
        # Draw Title
        if title:
            draw.text((img_w/2, self.PADDING/2), title, fill='black', font=title_font, anchor="mm")

        # Draw Grid Border
        grid_top = self.PADDING - 5
        grid_left = self.PADDING - 5
        grid_bottom = self.PADDING + size * self.CELL_SIZE + 5
        grid_right = self.PADDING + size * self.CELL_SIZE + 5
        draw.rectangle([grid_left, grid_top, grid_right, grid_bottom], outline='black', width=4)

        # Draw Grid
        for r in range(size):
            for c in range(size):
                if answer_key:
                    cell_data = answer_data.get((r, c))
                    char = cell_data['char']
                    cell_colors = cell_data['colors']
                    
                    if cell_colors:
                        bg_color = self._blend_colors(cell_colors)
                        # Draw cell background
                        box_l = self.PADDING + c * self.CELL_SIZE
                        box_t = self.PADDING + r * self.CELL_SIZE
                        box_r = box_l + self.CELL_SIZE
                        box_b = box_t + self.CELL_SIZE
                        draw.rectangle([box_l, box_t, box_r, box_b], fill=bg_color)
                else:
                    char = self.ws.grid[r][c]

                x = self.PADDING + c * self.CELL_SIZE + self.CELL_SIZE/2
                y = self.PADDING + r * self.CELL_SIZE + self.CELL_SIZE/2
                draw.text((x, y), char, fill='black', font=font, anchor="mm")

        # Draw Word List Checklist
        word_list_y_start = grid_bottom + 60
        for i, (word, _, _, _, color) in enumerate(sorted_placed_words):
            row = i // words_per_row
            col = i % words_per_row
            
            box_size = 35
            x_pos = self.PADDING + col * (img_w - 2 * self.PADDING) / words_per_row
            y_pos = word_list_y_start + row * 70
            
            # Draw Checkbox
            if answer_key:
                # Color code the word in the answer key word list
                draw.rectangle([x_pos, y_pos, x_pos + box_size, y_pos + box_size], fill=color, outline='black', width=3)
            else:
                draw.rectangle([x_pos, y_pos, x_pos + box_size, y_pos + box_size], outline='black', width=3)
                
            # Draw Word (Bold)
            draw.text((x_pos + box_size + 20, y_pos - 5), word, fill='black', font=self._get_font(self.WORD_LIST_FONT_SIZE, bold=True))

        image.save(output_path)

