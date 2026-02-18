"""
icons/make_icon.py
Generates the DGX Desktop Remote app icon (PNG + ICO) using Pillow.
Run once: python3 icons/make_icon.py
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os

OUT = Path(__file__).parent

def make_icon(size=256):
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # --- rounded rectangle background (purple) ---
    margin = int(size * 0.08)
    r      = int(size * 0.18)
    x0, y0 = margin, margin
    x1, y1 = size - margin, size - int(size * 0.18)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill="#6C63FF")

    # --- three "screen lines" ---
    lx0 = int(size * 0.22)
    lx1 = int(size * 0.78)
    for frac in [0.38, 0.52, 0.66]:
        ly = int(size * frac)
        draw.rounded_rectangle([lx0, ly - 3, lx1, ly + 3], radius=3, fill="#c0c0d8")

    # --- green "online" dot (bottom-right) ---
    dot_r  = int(size * 0.14)
    dot_cx = int(size * 0.78)
    dot_cy = int(size * 0.78)
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill="#22D47E",
    )

    return img


if __name__ == "__main__":
    img256 = make_icon(256)
    png_path = OUT / "app.png"
    img256.save(png_path)
    print(f"Saved {png_path}")

    # Also save ICO (multi-size for Windows compatibility)
    ico_path = OUT / "app.ico"
    imgs = [make_icon(s) for s in (16, 32, 48, 64, 128, 256)]
    imgs[0].save(ico_path, format="ICO", sizes=[(s, s) for s in (16, 32, 48, 64, 128, 256)], append_images=imgs[1:])
    print(f"Saved {ico_path}")

    # Copy png into ~/.local/share/icons for desktop integration
    icon_dir = Path.home() / ".local" / "share" / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy(png_path, icon_dir / "dgx-desktop-remote.png")
    print(f"Installed icon → {icon_dir / 'dgx-desktop-remote.png'}")
