"""Generate Tauri and Web PWA icon inputs from the application's canonical logo."""

from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[1]

# Locate icon image for Tauri & PWA (icon.png > logo.png > LOGO.png)
icon_path = root / "frontend" / "public" / "icon.png"
logo_path = root / "frontend" / "public" / "logo.png"
logo_upper_path = root / "frontend" / "public" / "LOGO.png"

if icon_path.exists():
    source_path = icon_path
elif logo_path.exists():
    source_path = logo_path
elif logo_upper_path.exists():
    source_path = logo_upper_path
else:
    source_path = icon_path

source_img = Image.open(source_path).convert("RGBA")

def make_square_icon(img: Image.Image) -> Image.Image:
    w, h = img.size
    if w == h:
        return img
    max_dim = max(w, h)
    canvas = Image.new("RGBA", (max_dim, max_dim), (0, 0, 0, 0))
    offset = ((max_dim - w) // 2, (max_dim - h) // 2)
    canvas.paste(img, offset, img)
    return canvas

source = make_square_icon(source_img)

# Corner pixel color for maskable icon padding
corner_color = source.getpixel((0, 0))

# -------------------------------------------------------------
# 1. Tauri Icons (src-tauri/icons)
# -------------------------------------------------------------
tauri_target = root / "src-tauri" / "icons"
tauri_target.mkdir(parents=True, exist_ok=True)

for size, name in ((32, "32x32.png"), (128, "128x128.png"), (256, "128x128@2x.png")):
    source.resize((size, size), Image.Resampling.LANCZOS).save(tauri_target / name)

source.save(
    tauri_target / "icon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)

# -------------------------------------------------------------
# 2. Web & PWA Icons (frontend/public and frontend/public/icons)
# -------------------------------------------------------------
web_target = root / "frontend" / "public" / "icons"
web_target.mkdir(parents=True, exist_ok=True)

# Main favicon.ico for browser tab
source.save(
    root / "frontend" / "public" / "favicon.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48)],
)

# Standard PNG favicons & app icons
png_icons = {
    "favicon-16.png": 16,
    "favicon-32.png": 32,
    "favicon-48.png": 48,
    "apple-touch-icon-120.png": 120,
    "apple-touch-icon-152.png": 152,
    "apple-touch-icon-167.png": 167,
    "apple-touch-icon-180.png": 180,
    "mstile-150.png": 150,
    "icon-192.png": 192,
    "icon-512.png": 512,
}

for name, size in png_icons.items():
    source.resize((size, size), Image.Resampling.LANCZOS).save(web_target / name)

# Maskable PWA icons (with safe zone padding: logo scaled to ~80% and centered)
for size, name in ((192, "maskable-192.png"), (512, "maskable-512.png")):
    canvas = Image.new("RGBA", (size, size), corner_color)
    inner_size = int(size * 0.8)
    scaled_logo = source.resize((inner_size, inner_size), Image.Resampling.LANCZOS)
    offset = (size - inner_size) // 2
    canvas.paste(scaled_logo, (offset, offset), scaled_logo)
    canvas.save(web_target / name)

print("Successfully generated all Tauri and Web/PWA icons from canonical logo!")
