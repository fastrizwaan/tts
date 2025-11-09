#!/usr/bin/env python3
from readmdict import MDX
import sys, os
from pathlib import Path

def extract_mdx(input_path: str, output_dir: str):
    mdx_path = Path(input_path)
    if not mdx_path.exists():
        sys.exit(f"❌ File not found: {mdx_path}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"📖 Reading {mdx_path.name} ...")
    mdx = MDX(str(mdx_path))
    entries = list(mdx.items())
    print(f"Found {len(entries):,} entries")

    # Write all entries to a single text file
    out_txt = output_dir / (mdx_path.stem + "_entries.txt")
    with open(out_txt, "w", encoding="utf-8") as f:
        for key, value in entries:
            headword = key.decode("utf-8", errors="ignore").strip()
            definition = value.decode("utf-8", errors="ignore").strip()
            f.write(f"{headword}\n{definition}\n\n")

    print(f"✅ Extracted entries to {out_txt}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: extract_mdx_content.py <input.mdx> <output_dir>")
        sys.exit(1)
    extract_mdx(sys.argv[1], sys.argv[2])
