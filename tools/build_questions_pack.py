#!/usr/bin/env python3
"""
Build / verify `questions_pack.sqlite.gz` from the subject JSON banks.

Why this exists
---------------
The app consumes `questions_pack.sqlite.gz` (declared in `content_manifest.json`).
The subject `*_questions.json` files are the source of truth. Reports kept coming
in for questions that had already been "fixed" in the JSON, because the
published SQLite pack still carried the old answer/explanation.

This script is the single place that turns JSON -> pack, and it also audits the
result so a stale pack can never be published again.

Usage
-----
    python3 tools/build_questions_pack.py --check    # audit only, exit 1 on drift
    python3 tools/build_questions_pack.py --build    # rewrite pack + manifest
"""

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "questions_pack.sqlite.gz")
MANIFEST = os.path.join(ROOT, "content_manifest.json")
PACK_INNER_NAME = "questions_pack.sqlite"

# (file, subject_id, exam_id) — order matters: it defines q_id ordering.
SOURCES = [
    ("agriculture_questions.json", "agriculture", "generic"),
    ("computer_questions.json", "computer", "generic"),
    ("current_affairs_questions.json", "current_affairs", "generic"),
    ("economics_questions.json", "economics", "generic"),
    ("environment_questions.json", "environment", "generic"),
    ("geography_questions.json", "geography", "generic"),
    ("high_court_mock_01.json", "high_court_clerk", "uk_high_court_clerk"),
    ("hindi_questions.json", "general_hindi", "generic"),
    ("history_questions.json", "history", "generic"),
    ("math_questions.json", "math", "generic"),
    ("polity_questions.json", "polity", "generic"),
    ("reasoning_questions.json", "reasoning", "generic"),
    ("science_questions.json", "science", "generic"),
    ("uttarakhand_questions.json", "uttarakhand_gk", "generic"),
]

TABLE_SQL = """CREATE TABLE questions (
        q_id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT UNIQUE NOT NULL,
        exam_id TEXT,
        post_id TEXT,
        subject_id TEXT,
        question_text TEXT NOT NULL,
        question_text_hindi TEXT,
        question_type TEXT,
        options_json TEXT,
        correct_answer TEXT NOT NULL,
        explanation TEXT NOT NULL,
        difficulty TEXT,
        topic TEXT,
        marks REAL DEFAULT 1.0,
        negative_marks REAL DEFAULT 0.25,
        time_weight REAL DEFAULT 1.0,
        extra_data TEXT,
        is_premium INTEGER DEFAULT 0,
        is_bookmarked INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        version TEXT,
        last_sync TEXT,
        source TEXT DEFAULT 'cdn'
    )"""

INDEX_SQL = [
    "CREATE INDEX idx_q_uuid ON questions(uuid)",
    "CREATE INDEX idx_q_subject ON questions(subject_id)",
    "CREATE INDEX idx_q_difficulty ON questions(difficulty)",
    "CREATE INDEX idx_q_topic ON questions(topic)",
]

INSERT_SQL = (
    "INSERT INTO questions(uuid,exam_id,post_id,subject_id,question_text,"
    "question_text_hindi,question_type,options_json,correct_answer,explanation,"
    "difficulty,topic,marks,negative_marks,time_weight,extra_data,is_premium,"
    "is_bookmarked,is_active,version,last_sync,source)"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def clean_question(q, subject_id):
    """Return the canonical pack row for one JSON question object."""
    options = q["options"]
    idx = q.get("correctIndex")
    if not isinstance(idx, int) or not (0 <= idx < len(options)):
        raise ValueError(f"{q.get('uuid')}: correctIndex {idx!r} out of range")
    extra = q.get("extra_data")
    return {
        "uuid": q["uuid"],
        "exam_id": None,
        "post_id": None,
        "subject_id": subject_id,
        "question_text": q["question"],
        "question_text_hindi": None,
        "question_type": q.get("questionType", "mcq"),
        "options_json": json.dumps(options, ensure_ascii=False),
        "correct_answer": options[idx],
        "explanation": q["explanation"],
        "difficulty": q.get("difficulty"),
        "topic": q.get("subCategory"),
        "marks": 1.0,
        "negative_marks": 0.25,
        "time_weight": 1.0,
        "extra_data": json.dumps(extra, ensure_ascii=False) if extra else None,
        "is_premium": 0,
        "is_bookmarked": 0,
        "is_active": 1,
        "version": None,
        "last_sync": None,
        "source": "cdn",
    }


def read_sources():
    rows = []
    for filename, subject_id, exam_id in SOURCES:
        path = os.path.join(ROOT, filename)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for q in data["questions"]:
            row = clean_question(q, subject_id)
            row["exam_id"] = exam_id
            row["post_id"] = exam_id
            rows.append(row)
    return rows


def build(db_path):
    rows = read_sources()
    if os.path.exists(db_path):
        os.remove(db_path)
    db = sqlite3.connect(db_path)
    db.execute(TABLE_SQL)
    for row in rows:
        db.execute(INSERT_SQL, tuple(row.values()))
    for sql in INDEX_SQL:
        db.execute(sql)
    db.commit()
    db.close()
    return rows


def read_pack_rows():
    raw = gzip.decompress(open(PACK, "rb").read())
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        db = sqlite3.connect(tmp_path)
        db.row_factory = sqlite3.Row
        rows = {r["uuid"]: dict(r) for r in db.execute("select * from questions")}
        db.close()
    finally:
        os.unlink(tmp_path)
    return raw, rows


def check():
    """Compare the published pack against the JSON banks."""
    raw, pack = read_pack_rows()
    expected = {r["uuid"]: r for r in read_sources()}

    problems = []
    for uuid, want in expected.items():
        got = pack.get(uuid)
        if got is None:
            problems.append(f"{uuid}: missing from pack")
            continue
        for field in ("subject_id", "question_text", "correct_answer",
                      "explanation", "options_json", "extra_data",
                      "difficulty", "topic"):
            if got[field] != want[field]:
                problems.append(
                    f"{uuid} ({want['subject_id']}): {field} differs\n"
                    f"    JSON : {want[field]!r}\n"
                    f"    pack : {got[field]!r}"
                )
    for uuid in set(pack) - set(expected):
        problems.append(f"{uuid}: present in pack but not in any JSON bank")

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    if manifest["sha256"] != hashlib.sha256(raw).hexdigest():
        problems.append("content_manifest.json: sha256 does not match the pack")
    if manifest["compressed_size_bytes"] != os.path.getsize(PACK):
        problems.append("content_manifest.json: compressed_size_bytes is stale")
    if manifest["raw_size_bytes"] != len(raw):
        problems.append("content_manifest.json: raw_size_bytes is stale")
    if manifest["total_questions"] != len(pack):
        problems.append("content_manifest.json: total_questions is stale")

    if problems:
        print(f"FAIL: {len(problems)} drift(s) between JSON banks and published pack\n")
        for p in problems[:50]:
            print(" -", p)
        if len(problems) > 50:
            print(f" ... and {len(problems) - 50} more")
        return 1

    print(f"OK: pack matches JSON banks ({len(pack)} questions), manifest consistent")
    return 0


def do_build():
    fd, db_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        rows = build(db_path)
        raw = open(db_path, "rb").read()
    finally:
        os.unlink(db_path)

    sha = hashlib.sha256(raw).hexdigest()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with open(PACK, "wb") as fh:
        gz = gzip.GzipFile(filename=PACK_INNER_NAME, mode="wb", fileobj=fh,
                           compresslevel=9, mtime=int(datetime.now(timezone.utc).timestamp()))
        gz.write(raw)
        gz.close()

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    manifest["content_version"] = int(manifest.get("content_version", 0)) + 1
    manifest["total_questions"] = len(rows)
    manifest["raw_size_bytes"] = len(raw)
    manifest["compressed_size_bytes"] = os.path.getsize(PACK)
    manifest["sha256"] = sha
    manifest["generated_at"] = now
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"Rebuilt {PACK_INNER_NAME}: {len(rows)} questions, sha256={sha}")
    print(f"content_manifest.json -> content_version {manifest['content_version']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--build", action="store_true", help="rebuild pack + manifest")
    group.add_argument("--check", action="store_true", help="audit only (default)")
    args = ap.parse_args()
    return do_build() if args.build else check()


if __name__ == "__main__":
    sys.exit(main())
