import random
import math

class WordSearch:
    DIRECTIONS = {
        'H_FWD': (0, 1),
        'H_REV': (0, -1),
        'V_FWD': (1, 0),
        'V_REV': (-1, 0),
        'D_FWD_R': (1, 1),
        'D_FWD_L': (1, -1),
        'D_REV_R': (-1, 1),
        'D_REV_L': (-1, -1),
    }

    def __init__(self, words, options=None):
        self.words = [word.upper() for word in words]
        self.options = options or {}
        self.grid_size = self._calculate_grid_size()
        self.grid = [[' ' for _ in range(self.grid_size)] for _ in range(self.grid_size)]
        self.placed_words = [] # Stores (word, row, col, dir_name, color)
        self.enabled_directions = self._get_enabled_directions()
        
        # 33 distinct highlight colors for answer key (supports up to 33 words)
        self.palette = [
            (255, 170, 170), # Red
            (170, 240, 170), # Green
            (170, 170, 255), # Blue
            (255, 210, 140), # Orange
            (210, 170, 255), # Purple
            (140, 230, 230), # Teal
            (255, 170, 210), # Pink
            (230, 230, 140), # Olive
            (255, 255, 150), # Yellow
            (170, 210, 255), # Sky Blue
            (210, 255, 170), # Lime
            (255, 190, 190), # Salmon
            (190, 255, 220), # Mint
            (220, 190, 255), # Lavender
            (255, 220, 190), # Peach
            (190, 220, 190), # Sage
            (255, 180, 255), # Magenta
            (180, 255, 200), # Seafoam
            (220, 200, 170), # Tan
            (200, 220, 255), # Cornflower
            (255, 200, 170), # Apricot
            (170, 200, 200), # Steel
            (240, 200, 220), # Blush
            (200, 240, 170), # Chartreuse
            (170, 240, 255), # Aqua
            (240, 170, 200), # Raspberry
            (210, 210, 180), # Khaki
            (180, 210, 240), # Periwinkle
            (240, 240, 190), # Cream
            (210, 180, 210), # Mauve
            (180, 240, 190), # Spring
            (240, 190, 240), # Orchid
            (190, 210, 210), # Slate
        ]

    def _calculate_grid_size(self):
        total_letters = sum(len(word) for word in self.words)
        max_word_len = max(len(word) for word in self.words) if self.words else 0
        
        # Use provided multiplier or default to 2.5
        multiplier = self.options.get('multiplier', 2.5)
        
        # Heuristic: grid area >= multiplier * total letters
        n_heuristic = math.ceil(math.sqrt(multiplier * total_letters))
        
        # Grid must be at least as large as the longest word
        n = max(max_word_len + 2, n_heuristic)
        return n

    def _get_enabled_directions(self):
        enabled = []
        if self.options.get('h_fwd', True): # Default to horizontal forward
            enabled.append('H_FWD')
        if self.options.get('h_rev'):
            enabled.append('H_REV')
        if self.options.get('v_fwd'):
            enabled.append('V_FWD')
        if self.options.get('v_rev'):
            enabled.append('V_REV')
        if self.options.get('d_fwd_r'):
            enabled.append('D_FWD_R')
        if self.options.get('d_fwd_l'):
            enabled.append('D_FWD_L')
        if self.options.get('d_rev_r'):
            enabled.append('D_REV_R')
        if self.options.get('d_rev_l'):
            enabled.append('D_REV_L')
        
        # Fallback if nothing is enabled
        if not enabled:
            enabled = ['H_FWD']
        return enabled

    def generate(self):
        # Sort words by length descending to place harder words first
        sorted_words = sorted(self.words, key=len, reverse=True)
        if self._backtrack(sorted_words, 0):
            self._fill_randoms()
            return True
        return False

    def _backtrack(self, word_list, index):
        if index == len(word_list):
            return True

        word = word_list[index]
        color = self.palette[index % len(self.palette)]
        possibilities = self._get_all_possibilities(word)
        random.shuffle(possibilities)

        for row, col, dir_name in possibilities:
            dr, dc = self.DIRECTIONS[dir_name]
            if self._can_place(word, row, col, dr, dc):
                placed_chars = self._place(word, row, col, dr, dc)
                self.placed_words.append((word, row, col, dir_name, color))
                
                if self._backtrack(word_list, index + 1):
                    return True
                
                # Undo
                self._remove(word, row, col, dr, dc, placed_chars)
                self.placed_words.pop()

        return False

    def _get_all_possibilities(self, word):
        possibilities = []
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                for dir_name in self.enabled_directions:
                    possibilities.append((r, c, dir_name))
        return possibilities

    def _can_place(self, word, row, col, dr, dc):
        word_len = len(word)
        end_r = row + dr * (word_len - 1)
        end_c = col + dc * (word_len - 1)

        # Out of bounds check
        if not (0 <= end_r < self.grid_size and 0 <= end_c < self.grid_size):
            return False

        for i in range(word_len):
            r, c = row + i * dr, col + i * dc
            grid_char = self.grid[r][c]
            
            if grid_char != ' ':
                if not self.options.get('overlap'):
                    return False
                if grid_char != word[i]:
                    return False
        
        return True

    def _place(self, word, row, col, dr, dc):
        placed_chars = [] # Track which cells were actually changed (for undo)
        for i in range(len(word)):
            r, c = row + i * dr, col + i * dc
            if self.grid[r][c] == ' ':
                self.grid[r][c] = word[i]
                placed_chars.append((r, c))
        return placed_chars

    def _remove(self, word, row, col, dr, dc, placed_chars):
        for r, c in placed_chars:
            self.grid[r][c] = ' '

    def _fill_randoms(self):
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                if self.grid[r][c] == ' ':
                    self.grid[r][c] = random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')

    def get_answer_data(self):
        """
        Returns a mapping of (r, c) -> {'char': letter, 'colors': [list_of_rgb_tuples]}
        for the color-coded answer key.
        """
        data = {}
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                data[(r, c)] = {'char': self.grid[r][c], 'colors': []}
                
        for word, row, col, dir_name, color in self.placed_words:
            dr, dc = self.DIRECTIONS[dir_name]
            for i in range(len(word)):
                r, c = row + i * dr, col + i * dc
                data[(r, c)]['char'] = word[i]
                data[(r, c)]['colors'].append(color)
        return data

    def get_answer_grid(self):
        """Return a 2D grid with only the answer letters filled in, blanks elsewhere."""
        grid = [[' ' for _ in range(self.grid_size)] for _ in range(self.grid_size)]
        for word, row, col, dir_name, color in self.placed_words:
            dr, dc = self.DIRECTIONS[dir_name]
            for i in range(len(word)):
                r, c = row + i * dr, col + i * dc
                grid[r][c] = word[i]
        return grid

