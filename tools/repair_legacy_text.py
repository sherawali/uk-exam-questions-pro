#!/usr/bin/env python3
"""
Repair legacy-font (Krutidev/Chanakya) transliteration artefacts in the banks.

Some explanations/questions were pasted from a legacy non-Unicode Hindi font and
came through as Latin-1 bytes glued into Devanagari words, e.g.

    सैÆय -> सैन्य      तÃय -> तथ्य      नेतृÂव -> नेतृत्व
    Èलासी -> प्लासी    शताÊदी -> शताब्दी   तÂकालीन -> तत्कालीन

A blind mapping is unsafe (the same byte means different things in different
words: ब्रĺ is ब्रह्म, but ° is usually the anusvara), so every replacement is
only applied when the result already exists **verbatim / as a stem** in the
corpus' own clean vocabulary. Anything unverifiable is reported, never touched.

    python3 tools/repair_legacy_text.py --report     # show what it would do
    python3 tools/repair_legacy_text.py --apply      # rewrite the JSON banks
"""

import argparse
import collections
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV = "\u0900-\u097F"
ART = re.compile(r"[\u0080-\u00BF\u00C0-\u024F³ĥ]")
WORDB = re.compile(r"[^\s\u0964\u0965]+")

CANDS = {
    "Æ": ["न", "न्", "ण"], "Â": ["त्व", "त्य", "त्", "त्थ", "त्म", "त्र", "त"],
    "Ã": ["थ्", "थ", "ध", "द्"], "É": ["फ्", "क्", "फ", "ग्"],
    "´": ["ख्या", "ख्य", "ंख", "ख", "क्ष", "क"], "º": ["ज्", "ज", "य", "द्", "व्"],
    "È": ["प्", "स्ट", "क्", "च्", "ग्"], "Ê": ["ब्", "द्", "व्", "म्"],
    "³": ["क्", "क", "ग", "न", "ट", "स्", "ड"],
    # '°' is almost always the anusvara; keeping only 'ं' avoids the
    # वि° -> विम class of false repairs.
    "°": ["ं"],
    "¸": ["र्", "च्", "छ्"], "ĥ": ["न", "न्", "ह", "म", "ब"],
    "Á": ["ण्ड", "ंड", "ड", "र", "न"], "¥": ["द", "र्", "व"],
    "Å": ["य्", "य"], "Ô": ["य", "ज", "द"], "±": ["ं", "त"],
    "²": ["२"], "£": [""], "é": [""], "ê": ["ि", "लि"],
    "ĺ": ["ह्म", "ह्"], "«": ["र्", "र्थं", "र्थ"],
}

# Curated fixes where the byte alone is ambiguous; verified by reading context.
OVERRIDES = {
    "स°ा": "सत्ता", "स°ाएँ": "सत्ताएँ", "वि°": "वित्त", "वि°ीय": "वित्तीय",
    "द°": "दत्त", "कलक°ा": "कलकत्ता", "प°ों": "पत्तों", "संपि°": "संपत्ति",
    "चि°ू": "चित्तू", "आपि°": "आपत्ति", "उÂपि°": "उत्पत्ति", "उÂपि°": "उत्पत्ति",
    "तê": "र्ती", "गĥी": "गद्दी", "अÔमक": "अश्मक", "राजु³क": "राजुक",
    "ईस्थवी": "ईस्वी", "Êयाज": "ब्याज", "Êयौरा": "ब्यौरा",
    "पाÁड्य": "पांड्य", "Áय": "ण्य", "वेदारÁयम": "वेदारण्यम",
    "कÔमीर": "कश्मीर", "दुÔमन": "दुश्मन", "अस्थपृÔय": "अस्पृश्य",
    "अस्थपृÔयता": "अस्पृश्यता", "माल¥": "मालद", "माल¥-मिंटो": "मालद-मिंटो",
    "आयुव¥द": "आयुर्वेद", "यजुव¥द": "यजुर्वेद", "तीथ«कर": "तीर्थंकर",
    "सवो¸च": "सर्वोच्च", "सं´या": "संख्या", "अल्पसं´यक": "अल्पसंख्यक",
    "अल्पसं´यकों": "अल्पसंख्यकों", "आÂमीय": "आत्मीय", "पुरातािÂवक": "पुरातात्विक",
    "Âवररित": "त्वरित", "साहििÂयक": "साहित्यक", "स्थथापÂय": "स्थापत्य",
    "पाटê": "पाटन", "नेतृÂवकता्र": "नेतृत्वकर्ता", "ररिपिÊलकन": "रिपब्लिकन",
    "उपाÅयाय": "उपाध्याय", "उपाÅयक्ष": "उपाध्यक्ष", "सीताÅयक्ष": "सीताध्यक्ष",
    "आÅयािÂमक": "आध्यात्मिक", "संÅया": "संध्या", "उ°म": "उत्तम", "गुणव°ा": "गुणवत्ता",
    "ब्रĥ": "ब्रह्म", "ब्रĺबांधव": "ब्रह्मबांधव", "मुĥे": "मुहें",
    "तुबुĥीन": "तुबुहीन", "बदरुĥीन": "बदरुहीन",
}

FILES = sorted(glob.glob(os.path.join(ROOT, "*_questions.json")))
FILES += [os.path.join(ROOT, "high_court_mock_01.json")]


def core(w):
    return w.strip(".,;:()[]\"'“”‘’!?।|-–—/*")


def texts_of(q):
    ed = q.get("extra_data") or {}
    out = [q.get("question"), q.get("explanation")]
    out += list(q.get("options") or [])
    out += [ed.get("question_en"), ed.get("explanation_en")]
    out += list(ed.get("options_en") or [])
    return [t for t in out if isinstance(t, str)]


def load():
    data = {}
    for f in FILES:
        data[f] = json.load(open(f, encoding="utf-8"))
    return data


def clean_vocab(data):
    """Frequency of every token in the corpus, used to verify repairs."""
    v = collections.Counter()
    for d in data.values():
        for q in d["questions"]:
            for t in texts_of(q):
                for w in WORDB.findall(t):
                    c = core(w)
                    if c and not ART.search(c):
                        v[c] += 1
                    elif c:
                        v[c] += 1
    return v


IMATRA = "\u093f"

# Real words the shift rule would wrongly rewrite
# (संधि = treaty/sandhi, बलि = offering, सखि = female friend ...).
MATRA_BLOCK = {
    "संधि", "सन्धि", "बलि", "सखि", "मलि", "चरति", "वशि", "ऋषि", "कवि",
    "मुनि", "गति", "रति", "मति", "प्रति", "अति", "यति", "नति", "स्थिति",
    "कवयित्री", "कवयित्रियों", "नेत्रि", "मित्रि", "अग्नि", "वनस्पति",
}

# Words where the legacy paste shifted the i-matra one cluster to the right:
#   वकिल्प (va-ka-I-la) should be विकल्प (va-I-ka-la).
# Only applied when exactly ONE shift produces a word that already occurs in the
# corpus (>=2 times), so it cannot invent new words.
def repair_matra(tok, freq):
    if IMATRA not in tok or len(tok) < 3:
        return None
    if any(ch.isdigit() for ch in tok):
        return None
    if tok in MATRA_BLOCK:
        return None
    hits = set()
    for i, ch in enumerate(tok):
        if ch != IMATRA:
            continue
        for k in (1, 2, 3):
            if i - k < 0:
                continue
            cand = tok[: i - k] + IMATRA + tok[i - k: i] + tok[i + 1:]
            if cand != tok and freq.get(cand, 0) >= 2:
                hits.add(cand)
    if len(hits) == 1:
        cand = hits.pop()
        return (cand, "matra", IMATRA, freq.get(cand, 0))
    return None


def repair_token(tok, vocab):
    if tok in OVERRIDES:
        return (OVERRIDES[tok], "override", OVERRIDES[tok], 10**6)
    if not ART.search(tok):
        return None
    arts = {c for c in tok if ART.match(c)}
    if any(a not in CANDS for a in arts):
        return None
    best = None
    for a in arts:
        for r in CANDS[a]:
            cand = tok.replace(a, r)
            if ART.search(cand):
                continue
            stem = re.sub(r"(ता|त्व|ों|ें|ी|ीय|क|ों)$", "", cand)
            if cand in vocab or stem in vocab:
                score = vocab.get(cand, 0) + vocab.get(stem, 0)
                if best is None or score > best[3]:
                    best = (cand, a, r, score)
    return best


def walk_strings(data, vocab, apply):
    """Yield (file, record_count) and optionally mutate strings in place."""
    changed = collections.Counter()
    verified = collections.Counter()
    unverified = collections.Counter()
    for f, d in data.items():
        for q in d["questions"]:
            def fix(t):
                if not isinstance(t, str) or not (ART.search(t) or IMATRA in t):
                    return t
                def sub(m):
                    w = m.group(0)
                    c = core(w)
                    if not c or not re.search("[" + DEV + "]", c):
                        return w
                    if any(ch.isdigit() for ch in c):
                        return w
                    if ART.search(c):
                        if not re.search("[" + DEV + "]", c):
                            return w
                        r = repair_token(c, vocab)
                    else:
                        r = repair_matra(c, vocab)
                    if not r:
                        unverified[c] += 1
                        return w
                    verified[c] += 1
                    pre = w[: w.index(c)]
                    post = w[w.index(c) + len(c):]
                    changed[f] += 1
                    return pre + r[0] + post
                return WORDB.sub(sub, t)

            for key in ("question", "explanation"):
                nt = fix(q.get(key))
                if apply:
                    q[key] = nt
            if isinstance(q.get("options"), list):
                nq = [fix(o) for o in q["options"]]
                if apply:
                    q["options"] = nq
            ed = q.get("extra_data")
            if isinstance(ed, dict):
                for k in ("question_en", "explanation_en"):
                    if k in ed:
                        nt = fix(ed[k])
                        if apply:
                            ed[k] = nt
                if isinstance(ed.get("options_en"), list):
                    ne = [fix(o) for o in ed["options_en"]]
                    if apply:
                        ed["options_en"] = ne
    return changed, verified, unverified


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="rewrite the JSON files")
    ap.add_argument("--report", action="store_true", help="report only (default)")
    ap.add_argument("--show-unverified", type=int, default=25)
    args = ap.parse_args()

    data = load()
    vocab = clean_vocab(data)
    changed, verified, unverified = walk_strings(data, vocab, args.apply)

    print(f"repairable occurrences : {sum(verified.values())} "
          f"({len(verified)} distinct tokens)")
    print(f"unverifiable occurrences: {sum(unverified.values())} "
          f"({len(unverified)} distinct tokens) - left untouched")
    for f, n in changed.most_common():
        print(f"  {os.path.basename(f):32s} {n:4d} replacements")

    if args.show_unverified:
        print("\ntop unverifiable tokens (need a human/domain decision):")
        for w, c in unverified.most_common(args.show_unverified):
            print(f"  {c:4d}  {w}")

    if args.apply:
        for f, d in data.items():
            raw = open(f, encoding="utf-8", newline="").read()
            nl = "\r\n" if "\r\n" in raw else "\n"
            body = json.dumps(d, ensure_ascii=False, indent=2).replace("\n", nl)
            if raw.endswith(nl) and not raw.rstrip().endswith("}"):
                body += nl
            elif raw.endswith(nl):
                body += nl
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(body)
        print("\nwritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
