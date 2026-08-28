#!/usr/bin/env python3
"""Build deterministic desktop icon assets from the approved B300 logo."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "branding" / "logo.png"
DEFAULT_PNG = ROOT / "branding" / "b300-stlink-icon.png"
DEFAULT_ICO = ROOT / "branding" / "b300-stlink-icon.ico"
DEFAULT_WORDMARK = ROOT / "branding" / "b300-stlink-wordmark.png"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def make_white_transparent(image: Image.Image) -> Image.Image:
    transparent = image.convert("RGBA")
    pixels = []
    for red, green, blue, _alpha in transparent.getdata():
        distance_from_white = 255 - min(red, green, blue)
        alpha = max(0, min(255, round((distance_from_white - 4) * 255 / 24)))
        pixels.append((red, green, blue, alpha))
    transparent.putdata(pixels)
    return transparent


def build_icon(source: Path, png_output: Path, ico_output: Path,
               wordmark_output: Path) -> None:
    logo = Image.open(source).convert("RGBA")
    width, height = logo.size

    # The approved source is square. This normalized crop contains the complete
    # chip/shield/SWD emblem while excluding the wordmark that is unreadable at
    # taskbar sizes.
    crop_box = (
        round(width * 0.016),
        round(height * 0.271),
        round(width * 0.415),
        round(height * 0.670),
    )

    size = 512
    margin = 20
    radius = 104

    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Ambient drop shadow for elevation
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (margin + 4, margin + 12, size - margin - 4, size - margin + 12),
        radius=radius,
        fill=(0, 0, 0, 80),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    icon.alpha_composite(shadow)

    card_rect = (margin, margin, size - margin, size - margin)

    # Render emblem on solid white surface (keeps chip body, pins, and interior solid bright white)
    emblem_raw = logo.crop(crop_box)
    emblem_pixels = emblem_raw.load()
    # Clean the bottom-right cable continuation
    for y in range(round(emblem_raw.height * 0.68), emblem_raw.height):
        for x in range(round(emblem_raw.width * 0.85), emblem_raw.width):
            emblem_pixels[x, y] = (255, 255, 255, 255)

    emblem_size = size - margin * 2 - 24
    emblem_resized = emblem_raw.resize((emblem_size, emblem_size), Image.Resampling.LANCZOS)

    # Pure solid white squircle card surface
    card = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    pos_x = (size - emblem_resized.width) // 2
    pos_y = (size - emblem_resized.height) // 2
    card.paste(emblem_resized, (pos_x, pos_y))

    # Slate border on the card
    border_draw = ImageDraw.Draw(card)
    border_draw.rounded_rectangle(
        card_rect,
        radius=radius,
        outline=(203, 213, 225, 255),
        width=4,
    )

    # Mask to keep only the rounded card area
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(card_rect, radius=radius, fill=255)

    icon.paste(card, (0, 0), mask)

    png_output.parent.mkdir(parents=True, exist_ok=True)
    icon.save(png_output, format="PNG", optimize=True)
    icon.save(ico_output, format="ICO", sizes=[(s, s) for s in ICON_SIZES])

    wordmark_box = (
        round(width * 0.040),
        round(height * 0.290),
        round(width * 0.980),
        round(height * 0.710),
    )
    wordmark = make_white_transparent(logo.crop(wordmark_box))
    wordmark.thumbnail((1100, 420), Image.Resampling.LANCZOS)
    wordmark_output.parent.mkdir(parents=True, exist_ok=True)
    wordmark.save(wordmark_output, format="PNG", optimize=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--png-output", type=Path, default=DEFAULT_PNG)
    parser.add_argument("--ico-output", type=Path, default=DEFAULT_ICO)
    parser.add_argument("--wordmark-output", type=Path, default=DEFAULT_WORDMARK)
    args = parser.parse_args(argv)
    build_icon(args.source, args.png_output, args.ico_output, args.wordmark_output)
    print("Created: %s" % args.png_output)
    print("Created: %s" % args.ico_output)
    print("Created: %s" % args.wordmark_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
