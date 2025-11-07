#!/usr/bin/env python3
import os
import re

WN_DIR = "dict"  # directory containing WordNet 3.1 data files
OUTFILE = "wordnet31_rich.dsl"

pos_files = {
    "n": "data.noun",
    "v": "data.verb",
    "a": "data.adj",
    "s": "data.adj",   # satellite adjectives share data.adj
    "r": "data.adv"
}

def escape_dsl(text):
    if not text:
        return ""
    return text.replace("[", r"\[").replace("]", r"\]")

# Parse data.* files into synset structures -----------------------------------

synsets = {}  # {offset: {keys...}}

for pos, fname in pos_files.items():
    path = os.path.join(WN_DIR, fname)
    if not os.path.exists(path):
        continue

    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("  ") or line.startswith("\t") or "  " not in line:
                continue
            if line.startswith(" "):
                continue
            parts = line.strip().split(" | ")
            syn_line, gloss = parts[0], (parts[1] if len(parts) > 1 else "")
            tokens = syn_line.split()
            offset = int(tokens[0])
            ss_type = tokens[2]  # n v a s r
            w_cnt = int(tokens[3], 16)
            lemmas = []
            idx = 4
            for i in range(w_cnt):
                lemmas.append(tokens[idx].replace("_", " "))
                idx += 2  # skip lex_id
            p_cnt = int(tokens[idx])
            idx += 1
            pointers = []
            for i in range(p_cnt):
                pointer_symbol = tokens[idx]
                idx += 1
                target_offset = int(tokens[idx])
                idx += 1
                target_pos = tokens[idx]
                idx += 2  # skip source/target lexical ids
                pointers.append((pointer_symbol, target_offset, target_pos))

            synsets[offset] = {
                "pos": ss_type,
                "lemmas": lemmas,
                "gloss": gloss,
                "ptrs": pointers,
            }

# Build lemma → synset-offsets index -------------------------------------------

lemma_index = {}

for offset, syn in synsets.items():
    for lemma in syn["lemmas"]:
        lemma_index.setdefault(lemma, []).append(offset)

# Helper to fetch related targets ----------------------------------------------

def find_related(offset, symbol):
    rel = []
    entry = synsets.get(offset)
    if not entry:
        return rel
    for sym, toff, tpos in entry["ptrs"]:
        if sym == symbol:
            target = synsets.get(toff)
            if target:
                rel.extend(target["lemmas"])
    return sorted(set(rel))

# Generate DSL -----------------------------------------------------------------

with open(OUTFILE, "w", encoding="utf-8") as out:
    out.write('#NAME "WordNet 3.1 English-English (Rich)"\n')
    out.write('#INDEX_LANGUAGE "English"\n')
    out.write('#CONTENTS_LANGUAGE "English"\n\n')

    for lemma in sorted(lemma_index.keys()):
        out.write(f"{lemma}\n")

        for offset in lemma_index[lemma]:
            syn = synsets[offset]

            pos = syn["pos"]
            gloss = escape_dsl(syn["gloss"])
            synonyms = sorted(set(syn["lemmas"]))

            # POS marker
            out.write(f"\t[p]{pos}[/p]\n")

            # Definition
            if gloss:
                out.write(f"\t[i]{gloss}[/i]\n")

            # Synonyms
            if len(synonyms) > 1:
                syn_str = ", ".join(escape_dsl(x) for x in synonyms)
                out.write(f"\t[b]Synonyms:[/b] {syn_str}\n")

            # Hypernyms (~)
            hypers = find_related(offset, "@")
            if hypers:
                out.write(f"\t[b]Hypernyms:[/b] {', '.join(escape_dsl(x) for x in hypers)}\n")

            # Hyponyms (~i)
            hypos = find_related(offset, "~")
            if hypos:
                out.write(f"\t[b]Hyponyms:[/b] {', '.join(escape_dsl(x) for x in hypos)}\n")

            # Antonyms (!)
            ants = find_related(offset, "!")
            if ants:
                out.write(f"\t[b]Antonyms:[/b] {', '.join(escape_dsl(x) for x in ants)}\n")

            # Similar to (&)
            similar = find_related(offset, "&")
            if similar:
                out.write(f"\t[b]Similar:[/b] {', '.join(escape_dsl(x) for x in similar)}\n")

            # Derivationally related (+)
            deriv = find_related(offset, "+")
            if deriv:
                out.write(f"\t[b]Derived forms:[/b] {', '.join(escape_dsl(x) for x in deriv)}\n")

        out.write("\n")

print(f"Generated: {OUTFILE}")
