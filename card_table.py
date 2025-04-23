from PIL import Image
import os

class CardTable:
    def __init__(self, card_path_dir, background_path, card_size=(100, 150), spacing=20, margin=20):
        self.card_path_dir = card_path_dir
        self.background_path = background_path
        self.card_width, self.card_height = card_size
        self.spacing = spacing
        self.margin = margin

    def _load_card_image(self, name):
        filename = f"{name}.png" if name.lower() != "back" else "back.png"
        path = os.path.join(self.card_path_dir, filename)
        card_img = Image.open(path).convert("RGBA")
        return card_img.resize((self.card_width, self.card_height))

    def render_table(self, cards):
        card_images = [self._load_card_image(card) for card in cards]

        content_width = len(card_images) * self.card_width + (len(card_images) - 1) * self.spacing
        total_width = content_width + self.margin * 2
        total_height = self.card_height + self.margin * 2

        background = Image.open(self.background_path).convert("RGBA")
        background = background.resize((total_width, total_height))

        x = self.margin
        y = self.margin
        for card_img in card_images:
            background.paste(card_img, (x, y), card_img)
            x += self.card_width + self.spacing

        return background