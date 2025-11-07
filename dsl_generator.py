#!/usr/bin/env python3
import nltk
from nltk.corpus import wordnet as wn

# Make sure WordNet 3.1 data is available
nltk.download("wordnet", quiet=True)

def escape_dsl(text):
    if not text:
        return ""
    return text.replace("[", r"\[").replace("]", r"\]")

outfile = "wordnet31.dsl"

with open(outfile, "w", encoding="utf-8") as f:
    f.write('#NAME "WordNet 3.1 English-English"\n')
    f.write('#INDEX_LANGUAGE "English"\n')
    f.write('#CONTENTS_LANGUAGE "English"\n\n')

    for lemma in sorted(set(wn.words())):
        f.write(f"{lemma}\n")

        synsets = wn.synsets(lemma)
        for syn in synsets:
            pos = syn.pos()
            gloss = escape_dsl(syn.definition())
            examples = syn.examples()

            synonyms = sorted(set(l.name().replace("_", " ")
                                  for l in syn.lemmas()))

            # part-of-speech marker
            f.write(f"\t[p]{pos}[/p]\n")

            # definition line
            f.write(f"\t[i]{gloss}[/i]\n")

            # synonyms
            if len(synonyms) > 1:
                syn_str = ", ".join(escape_dsl(s) for s in synonyms)
                f.write(f"\t[b]Synonyms:[/b] {syn_str}\n")

            # examples
            for ex in examples:
                ex = escape_dsl(ex)
                f.write(f"\t[b]Example:[/b] {ex}\n")

        f.write("\n")

print(f"Generated: {outfile}")

