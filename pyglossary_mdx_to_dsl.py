#!/usr/bin/env python3
import sys
from pathlib import Path
from pyglossary.glossary_v2 import Glossary

# --- Handle plugin loader differences between versions ---
try:
    from pyglossary import plugin_loader as _plugin_api
except ImportError:
    from pyglossary import plugin_handler as _plugin_api


def load_plugin(name: str):
    """
    Loads a PyGlossary plugin by name, handling version differences.
    """
    if hasattr(_plugin_api, "loadPlugin"):
        _plugin_api.loadPlugin(name)
    elif hasattr(_plugin_api, "load_plugin"):
        _plugin_api.load_plugin(name)
    elif hasattr(_plugin_api, "load"):
        _plugin_api.load(name)
    else:
        print(f"⚠️  Warning: could not explicitly load plugin '{name}', assuming auto-detect.")


def convert_mdx_to_dsl(input_mdx: str, output_dsl: str):
    """
    Convert MDX (and optional MDD) to DSL using PyGlossary.
    """
    in_path = Path(input_mdx)
    if not in_path.exists():
        sys.exit(f"❌ File not found: {in_path}")

    # Load required plugins
    load_plugin("Mdict")
    load_plugin("DSL")

    glossary = Glossary()

    print(f"📖 Loading {in_path.name} ...")
    glossary.read(str(in_path), format="Mdict")

    # Check for MDD (media) file
    mdd_path = in_path.with_suffix(".mdd")
    if mdd_path.exists():
        print(f"🎧 Found {mdd_path.name}")
        load_plugin("MDD")
        glossary.read(str(mdd_path), format="MDD")

    print("⚙️  Converting to DSL format...")
    glossary.write(
        str(output_dsl),
        format="DSL",
        encoding="utf-16",
        writeResources=True,
    )

    print(f"✅ Conversion complete → {output_dsl}")


def main():
    if len(sys.argv) < 3:
        print("Usage: pyglossary_mdx_to_dsl.py <input.mdx> <output.dsl>")
        sys.exit(1)

    input_mdx = sys.argv[1]
    output_dsl = sys.argv[2]
    convert_mdx_to_dsl(input_mdx, output_dsl)


if __name__ == "__main__":
    main()
