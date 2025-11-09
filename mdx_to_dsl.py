#!/usr/bin/env python3
import os
import sys
import re
from pathlib import Path
from readmdict import MDX, MDD

def sanitize_html_to_dsl(text: str) -> str:
    """
    Converts HTML/text from MDX into structured DSL markup.
    Handles simple <b>, <i>, numbered meanings, and examples.
    """
    text = text.replace("\r", "").strip()

    # Common inline HTML -> DSL
    replacements = {
        "<br>": "\n", "<br/>": "\n", "<BR>": "\n", "<BR/>": "\n",
        "&nbsp;": " ",
        "<b>": "[b]", "</b>": "[/b]",
        "<strong>": "[b]", "</strong>": "[/b]",
        "<i>": "[i]", "</i>": "[/i]",
        "<em>": "[i]", "</em>": "[/i]",
        "<u>": "[u]", "</u>": "[/u]",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)

    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Normalize whitespace
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r" {2,}", " ", text)

    # Add [m1], [m2] before numbered meanings like "1.", "2."
    text = re.sub(r"\n\s*(\d+)\.\s*", lambda m: f"\n[m{m.group(1)}] ", text)

    # Optional: highlight examples ("Example:", quotes, etc.)
    text = re.sub(r"([A-Z][^.!?]+[.!?])", r"[ex]\1[/ex]", text)

    # Decode HTML entities
    html_entities = {"&lt;": "<", "&gt;": ">", "&amp;": "&"}
    for k, v in html_entities.items():
        text = text.replace(k, v)

    # Add [p] for part of speech markers like “noun”, “adj.”, “verb”
    text = re.sub(r"\b(noun|adj\.?|verb|adv\.?|prep\.?|conj\.?)\b",
                  r"[p]\1[/p]", text, flags=re.IGNORECASE)

    return text.strip()



def convert_mdx_to_dsl(mdx_path: str, output_path: str):
    """
    Convert MDX dictionary to DSL text format.
    """
    mdx_path = Path(mdx_path)
    if not mdx_path.exists():
        sys.exit(f"❌ File not found: {mdx_path}")

    mdx = MDX(str(mdx_path))
    entries = list(mdx.items())

    print(f"📖 Loaded {len(entries):,} entries from {mdx_path.name}")

    # Write DSL
    with open(output_path, "w", encoding="utf-16") as f:
        f.write(f'#NAME "{mdx_path.stem} (Converted)"\n')
        f.write('#INDEX_LANGUAGE "English"\n')
        f.write('#CONTENTS_LANGUAGE "English"\n\n')

        for i, (key, value) in enumerate(entries, 1):
            try:
                headword = key.decode("utf-8", errors="ignore").strip()
                content = value.decode("utf-8", errors="ignore")
                content = sanitize_html_to_dsl(content)
                f.write(f"{headword}\n\t{content}\n\n")
                if i % 1000 == 0:
                    print(f"  → {i:,} entries converted...")
            except Exception as e:
                print(f"⚠️  Error parsing entry {i}: {e}")

    print(f"✅ Conversion complete → {output_path}")


def extract_mdd(mdd_path: str, output_dir: str):
    """
    Extract all media files from .mdd into a target directory.
    """
    mdd_path = Path(mdd_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    mdd = MDD(str(mdd_path))
    entries = list(mdd.items())
    print(f"🎧 Extracting {len(entries):,} media files from {mdd_path.name}")

    for key, value in entries:
        try:
            filename = key.decode("utf-8", errors="ignore").lstrip("\\/")
            file_path = output_dir / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "wb") as f:
                f.write(value)
        except Exception as e:
            print(f"⚠️  Error extracting {key}: {e}")

    print(f"📦 Media extracted → {output_dir}")


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  mdx_to_dsl.py <input.mdx> <output.dsl>")
        print("  (optional) mdx_to_dsl.py <input.mdd> <output_dir>")
        sys.exit(1)

    src = sys.argv[1]
    dst = sys.argv[2]

    if src.lower().endswith(".mdx"):
        convert_mdx_to_dsl(src, dst)
    elif src.lower().endswith(".mdd"):
        extract_mdd(src, dst)
    else:
        sys.exit("❌ Unsupported input file type (must be .mdx or .mdd)")


if __name__ == "__main__":
    main()
