#!/usr/bin/env python3
"""Build deterministic desktop icon assets from the approved B300 logo."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


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
    emblem = make_white_transparent(
        logo.crop(crop_box).resize((512, 512), Image.Resampling.LANCZOS)
    )

    # Retain the two round SWD contact points but omit the cable continuation;
    # otherwise it exits the square crop and looks accidentally clipped.
    emblem_pixels = emblem.load()
    for y in range(340, emblem.height):
        for x in range(425, emblem.width):
            red, green, blue, _alpha = emblem_pixels[x, y]
            emblem_pixels[x, y] = (red, green, blue, 0)

    png_output.parent.mkdir(parents=True, exist_ok=True)
    emblem.save(png_output, format="PNG", optimize=True)
    emblem.save(ico_output, format="ICO", sizes=[(size, size) for size in ICON_SIZES])

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
