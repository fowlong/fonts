#!/usr/bin/env python3
"""Generate a CSV index with useful matching metadata for every font file in the repo."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable

from fontTools.ttLib import TTCollection, TTFont, TTLibError

FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}
DEFAULT_BASE_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"

NAME_IDS = {
    1: "family_name",
    2: "subfamily_name",
    3: "unique_subfamily_id",
    4: "full_name",
    5: "version",
    6: "postscript_name",
    16: "typographic_family_name",
    17: "typographic_subfamily_name",
    21: "wws_family_name",
    22: "wws_subfamily_name",
}

FS_SELECTION_FLAGS = {
    0: "italic",
    5: "bold",
    6: "regular",
    7: "use_typo_metrics",
    8: "wws",
    9: "oblique",
}

MAC_STYLE_FLAGS = {
    0: "bold",
    1: "italic",
}

# Diagnostic characters for shape fingerprinting.
# These characters best discriminate between font families because their
# proportional widths vary significantly across serif, sans-serif, mono, etc.
# Each column stores advance_width / units_per_em for that character.
SHAPE_CHARS = ["e", "o", "i", "l", "m", "w", "n", "a", "r", "s"]

CSV_COLUMNS = [
    "repo_path",
    "download_url",
    "license_scope",
    "family_directory",
    "file_name",
    "font_format",
    "collection_index",
    "family_name",
    "subfamily_name",
    "full_name",
    "postscript_name",
    "typographic_family_name",
    "typographic_subfamily_name",
    "wws_family_name",
    "wws_subfamily_name",
    "unique_subfamily_id",
    "version",
    "units_per_em",
    "glyph_count",
    "cmap_codepoint_count",
    "is_variable",
    "variation_axes",
    "weight_class",
    "width_class",
    "italic_angle",
    "is_italic",
    "is_oblique",
    "is_bold",
    "is_regular",
    "is_monospace",
    "fs_selection_flags",
    "mac_style_flags",
    "panose",
    "x_height",
    "cap_height",
    "average_char_width",
    "hhea_ascender",
    "hhea_descender",
    "hhea_line_gap",
    "typo_ascender",
    "typo_descender",
    "typo_line_gap",
    "win_ascent",
    "win_descent",
    "underline_position",
    "underline_thickness",
] + [f"cw_{ch}" for ch in SHAPE_CHARS]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Repository root to scan.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "font_index.csv",
        help="CSV file to write.",
    )
    parser.add_argument("--repo-owner", default="google", help="GitHub owner/org for raw download URLs.")
    parser.add_argument("--repo-name", default="fonts", help="GitHub repo name for raw download URLs.")
    parser.add_argument("--ref", default="main", help="Git ref/branch/tag for raw download URLs.")
    return parser.parse_args()


def iter_font_paths(repo_root: Path) -> Iterable[Path]:
    for path in sorted(repo_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in FONT_EXTENSIONS:
            yield path


def get_best_name(font: TTFont, name_id: int) -> str:
    name_table = font["name"]
    candidates = name_table.getName(name_id, 3, 1, 0x409), name_table.getName(name_id, 1, 0, 0)
    for candidate in candidates:
        if candidate:
            return str(candidate)
    for record in name_table.names:
        if record.nameID == name_id:
            try:
                return record.toUnicode()
            except Exception:
                return str(record)
    return ""


def get_flag_names(value: int, mapping: dict[int, str]) -> list[str]:
    return [label for bit, label in mapping.items() if value & (1 << bit)]


def get_variation_axes(font: TTFont) -> str:
    if "fvar" not in font:
        return ""
    axes = []
    for axis in font["fvar"].axes:
        axes.append(
            {
                "tag": axis.axisTag,
                "min": axis.minValue,
                "default": axis.defaultValue,
                "max": axis.maxValue,
            }
        )
    return json.dumps(axes, sort_keys=True, separators=(",", ":"))


def build_download_url(base_url: str, owner: str, repo: str, ref: str, relative_path: str) -> str:
    return base_url.format(owner=owner, repo=repo, ref=ref, path=relative_path)


PANOSE_ATTRS = (
    "familyType",
    "serifStyle",
    "weight",
    "proportion",
    "contrast",
    "strokeVariation",
    "armStyle",
    "letterForm",
    "midline",
    "xHeight",
)


def panose_to_string(os2) -> str:
    if not os2 or not hasattr(os2, "panose"):
        return ""
    return "-".join(str(getattr(os2.panose, attr, "")) for attr in PANOSE_ATTRS)


def extract_char_widths(font: TTFont, units_per_em: int) -> dict[str, str]:
    """Extract advance widths for diagnostic characters, normalised to unitsPerEm."""
    result: dict[str, str] = {}
    if units_per_em <= 0:
        return {f"cw_{ch}": "" for ch in SHAPE_CHARS}

    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"] if "hmtx" in font else None
    if hmtx is None:
        return {f"cw_{ch}": "" for ch in SHAPE_CHARS}

    for ch in SHAPE_CHARS:
        glyph_name = cmap.get(ord(ch))
        if glyph_name and glyph_name in hmtx.metrics:
            advance_width = hmtx.metrics[glyph_name][0]
            result[f"cw_{ch}"] = round(advance_width / units_per_em, 5)
        else:
            result[f"cw_{ch}"] = ""

    return result


def extract_rows(font_path: Path, repo_root: Path, base_url: str, owner: str, repo: str, ref: str) -> list[dict[str, object]]:
    relative_path = font_path.relative_to(repo_root).as_posix()
    download_url = build_download_url(base_url, owner, repo, ref, relative_path)
    license_scope = relative_path.split("/", 1)[0] if "/" in relative_path else ""
    family_directory = str(font_path.relative_to(repo_root).parent).replace("\\", "/")

    empty_cw = {f"cw_{ch}": "" for ch in SHAPE_CHARS}

    try:
        if font_path.suffix.lower() == ".ttc":
            collection = TTCollection(font_path, lazy=True)
            fonts_to_read = list(collection.fonts)
            collection_length = len(fonts_to_read)
        else:
            collection = None
            fonts_to_read = [TTFont(font_path, lazy=True)]
            collection_length = 1
    except TTLibError as exc:
        return [{
            "repo_path": relative_path,
            "download_url": download_url,
            "license_scope": license_scope,
            "family_directory": family_directory,
            "file_name": font_path.name,
            "font_format": font_path.suffix.lower().lstrip("."),
            "collection_index": "",
            "family_name": "",
            "subfamily_name": "",
            "full_name": "",
            "postscript_name": "",
            "typographic_family_name": "",
            "typographic_subfamily_name": "",
            "wws_family_name": "",
            "wws_subfamily_name": "",
            "unique_subfamily_id": "",
            "version": f"UNREADABLE: {exc}",
            "units_per_em": "",
            "glyph_count": "",
            "cmap_codepoint_count": "",
            "is_variable": "",
            "variation_axes": "",
            "weight_class": "",
            "width_class": "",
            "italic_angle": "",
            "is_italic": "",
            "is_oblique": "",
            "is_bold": "",
            "is_regular": "",
            "is_monospace": "",
            "fs_selection_flags": "",
            "mac_style_flags": "",
            "panose": "",
            "x_height": "",
            "cap_height": "",
            "average_char_width": "",
            "hhea_ascender": "",
            "hhea_descender": "",
            "hhea_line_gap": "",
            "typo_ascender": "",
            "typo_descender": "",
            "typo_line_gap": "",
            "win_ascent": "",
            "win_descent": "",
            "underline_position": "",
            "underline_thickness": "",
            **empty_cw,
        }]

    fonts = []
    for collection_index, current_font in enumerate(fonts_to_read):
        name_values = {column: get_best_name(current_font, name_id) for name_id, column in NAME_IDS.items()}
        os2 = current_font["OS/2"] if "OS/2" in current_font else None
        head = current_font["head"] if "head" in current_font else None
        hhea = current_font["hhea"] if "hhea" in current_font else None
        post = current_font["post"] if "post" in current_font else None
        cmap = current_font.getBestCmap() or {}
        fs_selection = getattr(os2, "fsSelection", 0) if os2 else 0
        mac_style = getattr(head, "macStyle", 0) if head else 0
        panose = panose_to_string(os2)
        upm = getattr(head, "unitsPerEm", 0) if head else 0

        char_widths = extract_char_widths(current_font, upm)

        fonts.append(
            {
                "repo_path": relative_path,
                "download_url": download_url,
                "license_scope": license_scope,
                "family_directory": family_directory,
                "file_name": font_path.name,
                "font_format": font_path.suffix.lower().lstrip("."),
                "collection_index": collection_index if collection_length > 1 else "",
                **name_values,
                "units_per_em": upm or "",
                "glyph_count": len(current_font.getGlyphOrder()),
                "cmap_codepoint_count": len(cmap),
                "is_variable": "fvar" in current_font,
                "variation_axes": get_variation_axes(current_font),
                "weight_class": getattr(os2, "usWeightClass", "") if os2 else "",
                "width_class": getattr(os2, "usWidthClass", "") if os2 else "",
                "italic_angle": getattr(post, "italicAngle", "") if post else "",
                "is_italic": "italic" in get_flag_names(fs_selection, FS_SELECTION_FLAGS) or "italic" in get_flag_names(mac_style, MAC_STYLE_FLAGS),
                "is_oblique": "oblique" in get_flag_names(fs_selection, FS_SELECTION_FLAGS),
                "is_bold": "bold" in get_flag_names(fs_selection, FS_SELECTION_FLAGS) or "bold" in get_flag_names(mac_style, MAC_STYLE_FLAGS),
                "is_regular": "regular" in get_flag_names(fs_selection, FS_SELECTION_FLAGS),
                "is_monospace": getattr(post, "isFixedPitch", 0) == 1 if post else "",
                "fs_selection_flags": "|".join(get_flag_names(fs_selection, FS_SELECTION_FLAGS)),
                "mac_style_flags": "|".join(get_flag_names(mac_style, MAC_STYLE_FLAGS)),
                "panose": panose,
                "x_height": getattr(os2, "sxHeight", "") if os2 else "",
                "cap_height": getattr(os2, "sCapHeight", "") if os2 else "",
                "average_char_width": getattr(os2, "xAvgCharWidth", "") if os2 else "",
                "hhea_ascender": getattr(hhea, "ascent", "") if hhea else "",
                "hhea_descender": getattr(hhea, "descent", "") if hhea else "",
                "hhea_line_gap": getattr(hhea, "lineGap", "") if hhea else "",
                "typo_ascender": getattr(os2, "sTypoAscender", "") if os2 else "",
                "typo_descender": getattr(os2, "sTypoDescender", "") if os2 else "",
                "typo_line_gap": getattr(os2, "sTypoLineGap", "") if os2 else "",
                "win_ascent": getattr(os2, "usWinAscent", "") if os2 else "",
                "win_descent": getattr(os2, "usWinDescent", "") if os2 else "",
                "underline_position": getattr(post, "underlinePosition", "") if post else "",
                "underline_thickness": getattr(post, "underlineThickness", "") if post else "",
                **char_widths,
            }
        )

        current_font.close()

    if collection is not None:
        collection.close()
    return fonts


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    base_url = DEFAULT_BASE_URL
    for font_path in iter_font_paths(repo_root):
        rows.extend(extract_rows(font_path, repo_root, base_url, args.repo_owner, args.repo_name, args.ref))

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
