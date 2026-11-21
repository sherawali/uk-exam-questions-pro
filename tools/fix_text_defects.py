#!/usr/bin/env python3
"""
Repair mechanical text defects that came from PDF/copy-paste extraction:

  * statement lists glued to the lead-in / to each other
        "... विचार करें :1. उत्तर प्रदेश2. मध्य प्रदेश3. राजस्थानचम्बल नदी उपरोक्त ..."
  * duplicated words
        "कंप्यूटर में में Encryption का क्या अर्थ है?"
  * garbled single-syllable fragments
        "प्रदान करने वा ले"  ->  "प्रदान करने वाले"
        "इनमे से से कौन"     ->  "इनमें से कौन"

Idempotent, whitespace-only changes otherwise. Run with --apply.
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = sorted(f for f in os.listdir(ROOT) if f.endswith("_questions.json"))
FILES += ["high_court_mock_01.json"]
DEV = "\u0900-\u097F"
WORDB = re.compile(r"[^\s\u0964\u0965]+")
ART = re.compile(r"[\u0080-\u00BF\u00C0-\u024F³ĥ]")

GARBLE = [
    ("इनमे से से", "इनमें से"), ("इनमे से", "इनमें से"), ("इनमे किस", "इनमें किस"),
    ("करने वा ले", "करने वाले"), ("होने वा ले", "होने वाले"), ("रहने वा ले", "रहने वाले"),
    ("जीतने वा ले", "जीतने वाले"), ("कहे जा ने वा ले", "कहे जाने वाले"),
    ("राष्ट्रमंडल खेल खेल", "राष्ट्रमंडल खेल"),
    ("देशों देशों", "देशों"),
    ("के द्रीय", "केंद्रीय"), ("कें द्रीय", "केंद्रीय"),
    ("एक का एक", "एक"),
]


def _in_statement_list(t, pos):
    """True when pos sits inside a numbered statement list (1. 2. 3. ...)."""
    head = t[:pos]
    return bool(re.search(r"(?:^|\n)\s*[1-9][.)]\s", head))


def fix(t):
    """Only touch the specific paste artefacts; never numbers/ratios/exponents."""
    if not isinstance(t, str):
        return t, 0
    orig = t
    for a, b in GARBLE:
        t = t.replace(a, b)

    # 1) statement list glued to lead-in punctuation: "करें :1. उत्तर प्रदेश"
    #    (requires Devanagari right after, so "HCl (3:1)" style ratios stay intact)
    t = re.sub(r"([?।:])([ \t]*)([1-9])[.)][ \t]*(?=[\u0900-\u097F])",
               lambda m: m.group(1) + ("\n\n" if "\n" not in m.group(2) else m.group(2))
               + m.group(3) + ". ", t)

    # 2) statement glued to previous statement: "... प्रदेश2. मध्य प्रदेश"
    t = re.sub(r"([\u0900-\u097F])([2-9])[.)][ \t]*(?=[\u0900-\u097F])",
               lambda m: m.group(1) + "\n" + m.group(2) + ". ", t)

    # 3) last statement glued to the question stem. Split right before the
    #    interrogative/lead-in word, keeping the statement text intact:
    #    "3. राजस्थानचम्बल नदी ..."   -> "3. राजस्थान\n\nचम्बल नदी ..."
    #    "4. नागा पहाड़ियांइनमें ..."  -> "4. नागा पहाड़ियां\n\nइनमें ..."
    t = re.sub(r"([\u0900-\u097F])(?=(?:इनमें|इनमे|उपरोक्त|नीचे|निम्नलिखित|"
               r"चम्बल|चंबल|कौन|उपर्युक्त)\b|(?:इनमें|इनमे)\s)",
               lambda m: m.group(1) + "\n\n"
               if m.start() > 0 and _in_statement_list(t, m.start()) else m.group(1), t)

    # 4) collapse the runs of blank lines this can produce, keep at most one gap
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t, int(t != orig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    total = 0
    for name in FILES:
        path = os.path.join(ROOT, name)
        raw = open(path, encoding="utf-8", newline="").read()
        data = json.loads(raw)
        changed = []
        for q in data["questions"]:
            for key in ("question", "explanation"):
                nt, n = fix(q.get(key))
                if n:
                    changed.append((q["uuid"], key, q.get(key), nt))
                    q[key] = nt
            ed = q.get("extra_data") or {}
            for key in ("question_en", "explanation_en"):
                if isinstance(ed.get(key), str):
                    nt, n = fix(ed[key])
                    if n:
                        changed.append((q["uuid"], "en:" + key, ed[key], nt))
                        ed[key] = nt
        total += len(changed)
        if changed:
            print(f"{name}: {len(changed)} strings")
            for u, k, a, b in changed[:6]:
                print(f"   {u[:18]} {k}")
                print(f"      - {a[:110]}")
                print(f"      + {b[:110].replace(chr(10), chr(92) + 'n')}")
            if args.apply:
                nl = "\r\n" if "\r\n" in raw else "\n"
                body = json.dumps(data, ensure_ascii=False, indent=2).replace("\n", nl)
                if raw.endswith(nl):
                    body += nl
                open(path, "w", encoding="utf-8", newline="").write(body)
    print(f"\ntotal strings needing repair: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
