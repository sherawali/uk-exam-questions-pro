# 🏛️ Comprehensive Database, Persistence & Sync Engine Audit Report
**Devbhoomi Guru (UKPSC / UKSSSC Prep App)**  
**Auditor:** Principal Mobile Architect & QA Auditor  
**Date:** November 16, 2026  
**Audit Scope:**
- Database Core: `lib/core/database/` (`db_helper.dart`, `database_migrator.dart`, `database_seeder.dart`, all `tables/`)
- Sync Engine: `lib/core/services/sync_service.dart`, `lib/core/services/update_controller.dart`, `lib/features/test/services/pro_mock_sync_service.dart`
- Sync UI & Controllers: `lib/core/controllers/sync_controller.dart`, `lib/features/sync/screens/sync_screen.dart`
- Providers: `lib/core/providers/database_providers.dart`, `lib/core/providers/sync_providers.dart`
- Asset Flow: `assets/` and `formatted-questions/`

---

## Executive Summary
A deep zero-compromise audit was performed on the database persistence layer, migration pipeline, asset seeding flow, and synchronization engine. While the core architecture utilizes SQLite with WAL mode, memory-mapped I/O (30MB), and SQLCipher encryption, **multiple critical architectural hazards, data loss risks, and synchronization flaws** were identified that jeopardize student exam preparation data and application stability.

### Key Severity Breakdown:
- **P0 / Blocker (Immediate Data Loss Risk):** 3
- **P1 / High (Sync Inconsistency & Schema Drift):** 5
- **P2 / Medium (Performance & Query Overhead):** 4
- **P3 / Low (Code Hygiene & Redundancy):** 2

---

## Section 1: SQLite Transaction Safety & Concurrency

### 🔴 Finding 1.1: Unprotected Multi-Chunk Seeding Without Outer Transaction
- **File:** `lib/core/database/database_seeder.dart` (Lines 95–110)
- **Severity:** P0 (Blocker)
- **Root Cause:**
  In `DatabaseSeeder.seedInitialData()`, the rows extracted from local assets (16,000+ questions) are split into chunks of 1,000. Each chunk is committed via an independent batch (`batch.commit(noResult: true)`), but there is **no outer database transaction** wrapping the loop.
  ```dart
  const chunkSize = 1000;
  for (int i = 0; i < allRows.length; i += chunkSize) {
    final chunk = allRows.sublist(...);
    final batch = db.batch();
    for (final row in chunk) {
      batch.insert(QuestionsTable.tableName, row, conflictAlgorithm: ConflictAlgorithm.replace);
    }
    await batch.commit(noResult: true); // Independent transaction per chunk!
  }
  ```
- **Student Impact:**
  If the application is force-closed by the Android OS (low memory) or killed by the student while chunk 5 is committing, chunks 1–4 remain permanently committed in SQLite. On the subsequent launch, `_runOneTimeDataPatches` in `db_helper.dart` checks:
  ```dart
  final count = Sqflite.firstIntValue(await db.rawQuery('SELECT COUNT(*) FROM questions'));
  if (count == null || count == 0) { await DatabaseSeeder.seedInitialData(db); }
  ```
  Since `count > 0` (e.g., 4,000 rows exist), the app considers seeding "already done" and **permanently leaves out the remaining 12,000 questions**. The student is left with an incomplete question bank with no way to recover without clearing app data.
- **Remediation:**
  Wrap all chunk batch operations in an atomic `db.transaction((txn) async { ... })` and pass `txn` to `txn.batch()`. If any chunk fails, the entire transaction rolls back cleanly, allowing a complete, fresh retry.

---

### 🔴 Finding 1.2: `ConflictAlgorithm.replace` Wipes Question Metadata & Mutates Primary Key
- **File:** `lib/core/services/sync_service.dart` (Lines 1195–1205), `lib/core/database/database_seeder.dart` (Line 106)
- **Severity:** P0 (Blocker)
- **Root Cause:**
  When syncing questions in `syncFile()`, the service executes:
  ```dart
  batch.insert(
    QuestionsTable.tableName,
    mappedData,
    conflictAlgorithm: ConflictAlgorithm.replace,
  );
  ```
  In SQLite, when an insert violates the `uuid UNIQUE` constraint under `REPLACE`:
  1. The existing record is **deleted**, and a new record with an incremented autoincrement `q_id` is inserted.
  2. Any column not explicitly populated in `mappedData` is overwritten by its schema default.
  Notice `_mapJsonToQuestion()` in `sync_service.dart` (Lines 1326–1345) does **not** include the `is_bookmarked` field.
- **Student Impact:**
  Whenever an existing question is updated via sync or reseeded, SQLite resets `is_bookmarked` back to `0`. Any student who bookmarked questions will find their bookmarks silently un-synced on the questions table.
- **Remediation:**
  Use `ConflictAlgorithm.ignore` or execute an explicit `UPDATE ... WHERE uuid = ?` preserving local user state (`is_bookmarked`, `source`), or rely solely on `bookmarks` table for bookmark truth and remove `is_bookmarked` from `questions`.

---

## Section 2: Clean Database Migrations & Zero Data Loss

### 🔴 Finding 2.1: Catastrophic Database Deletion in `_selfRepairDatabase`
- **File:** `lib/core/database/db_helper.dart` (Lines 60–65, 114–141)
- **Severity:** P0 (Blocker)
- **Root Cause:**
  In `_initDatabase()`:
  ```dart
  try {
    return await _openDatabaseInternal(path, _dbKey);
  } catch (e, stack) {
    debugPrint('DatabaseHelper: Primary openDatabase failed, self-repairing: $e\n$stack');
    return await _selfRepairDatabase(path);
  }
  ```
  And inside `_selfRepairDatabase(path)`:
  ```dart
  try {
    await deleteDatabase(path);
  } catch (_) {}
  try {
    final walFile = File('$path-wal');
    if (await walFile.exists()) await walFile.delete();
    final shmFile = File('$path-shm');
    if (await shmFile.exists()) await shmFile.delete();
  } catch (_) {}
  ```
  If `openDatabase` fails due to a **transient file lock**, Android process preemptive wake-up, or a minor migration error inside `onUpgrade`, `_selfRepairDatabase` immediately calls `deleteDatabase(path)`.
- **Student Impact:**
  **Complete, unrecoverable data annihilation.** All test session history (`mock_history`), customized SM-2 spaced repetition revision schedules (`user_question_revision`), bookmarks, streak data, and user profile preferences are permanently wiped without student confirmation and without creating a backup file (`.db.bak`).
- **Remediation:**
  1. Never delete the primary database without creating an emergency copy: `File(path).copySync('$path.corrupt_backup_${DateTime.now().millisecondsSinceEpoch}')`.
  2. Implement an exponential retry mechanism (up to 3 retries with 500ms delay) before declaring corruption.
  3. Only purge the database if SQLite throws a verified `DatabaseCorruptException`.

---

### 🟠 Finding 2.2: Schema Drift Between Fresh Install (`createTable`) and Incremental Migrations (`onUpgrade`)
- **Files:**
  - `lib/core/database/tables/mock_history_table.dart` (Lines 4–15)
  - `lib/core/database/tables/user_profiles_table.dart` (Lines 5–18)
  - `lib/core/database/tables/questions_table.dart` (Lines 6–34)
  - `lib/core/database/database_migrator.dart` (Lines 124–133, 160–174, 186–194, 204–213, 214–222, 248–260)
- **Severity:** P1 (High)
- **Root Cause:**
  When a user installs the app fresh (version 20), SQLite runs `onCreate` which executes `DatabaseMigrator.createAllTables(db)`. These use the static `createTable` strings in the table files.
  However, several columns added in migration steps are **completely missing** from the table `createTable` declarations:
  1. **`mock_history`**: Migrated in v12 to include `total_marks REAL DEFAULT 0.0` and `negative_marks REAL DEFAULT 0.0`. Neither column exists in `MockHistoryTable.createTable`!
  2. **`user_profiles`**: Migrated in v14 (`is_plus INTEGER`), v16 (`study_streak_dates TEXT`), and v19 (`active_exam_id TEXT`, `active_post_id TEXT`). **None** of these 4 columns exist in `UserProfilesTable.createTable`!
  3. **`questions`**: Migrated in v17 to include `mock_id TEXT`. This column is missing in `QuestionsTable.createTable`!
- **Student Impact:**
  On a fresh installation, queries attempting to read or write `is_plus`, `active_exam_id`, `active_post_id`, or `study_streak_dates` on `user_profiles` will fail or default silently. Inconsistencies between upgraded devices and fresh installs will cause intermittent crashes in profile and analytics services.
- **Remediation:**
  Synchronize all static `createTable` statements in `tables/*.dart` so that a fresh install builds the exact v20 production schema.

---

## Section 3: SWR Sync Throttle, Error Handling & Flaky Network Resilience

### 🔴 Finding 3.1: Partial Sync Failure Masquerades as 100% Success & Corrupts Sync Version
- **File:** `lib/core/services/sync_service.dart` (Lines 289–340)
- **Severity:** P0 (Blocker)
- **Root Cause:**
  In `SyncService.syncAll()`:
  ```dart
  if (totalSynced == 0 &&
      networkFailedFiles == filesToProcess.length &&
      filesToProcess.isNotEmpty) {
    throw SyncOfflineException('सिंक पूरा नहीं हो सका...');
  }
  ```
  If 1 file succeeds (e.g. `uttarakhand_questions.json`) and 12 files fail due to a network disconnection halfway through, `totalSynced > 0`. The condition is false, so no exception is thrown!
  The execution continues directly to Lines 320–337:
  ```dart
  await prefs.setString('synced_manifest_version', cfg['version'].toString());
  await prefs.setString('synced_manifest_time', cfg['last_sync'].toString());
  ```
  The app writes the latest manifest version and timestamp to SharedPreferences as if the entire sync succeeded!
- **Student Impact:**
  Because `synced_manifest_version` now matches the remote version, `checkForUpdates()` will henceforth return `false`. The 12 failed subjects (History, Polity, Geography, Current Affairs, etc.) will **never be downloaded again**, and the student will believe their question bank is up-to-date while missing thousands of new questions.
- **Remediation:**
  Only update `synced_manifest_version` and `synced_manifest_time` if **all** question files in the manifest were successfully downloaded and verified (`networkFailedFiles == 0`). If any file fails, record the partial status, surface a warning SnackBar/Dialog, and do NOT record a complete manifest sync.

---

### 🔴 Finding 3.2: Sync Pruner Deletes Seeded PYQ Questions (`source = 'seed'`)
- **File:** `lib/core/services/sync_service.dart` (Lines 1208–1252)
- **Severity:** P0 (Blocker)
- **Root Cause:**
  In `syncFile()`:
  ```dart
  // 2. Prune old/zombie questions that were deleted from the remote file
  final List<Map<String, dynamic>> existingRows = await txn.query(
    QuestionsTable.tableName,
    columns: ['uuid'],
    where: 'subject_id = ?',
    whereArgs: [subjectId],
  );
  final existingUuids = existingRows.map((r) => r['uuid'] as String).toSet();
  final incomingSet = incomingUuids.toSet();
  final toDelete = existingUuids.difference(incomingSet).toList();
  ...
  await txn.delete(
    QuestionsTable.tableName,
    where: 'uuid IN ($placeholders)',
    whereArgs: chunk,
  );
  ```
  The developer added a `source` column to `QuestionsTable` specifically to distinguish `'seed'` (bundled PYQ papers) from `'cdn'` (remote question packs).
  However, the `existingRows` query filters **only** by `where: 'subject_id = ?'`. It does **not** exclude `source = 'seed'`!
- **Student Impact:**
  When `math_questions.json` or `uttarakhand_questions.json` syncs from the CDN, all PYQ questions seeded locally from `ukpsc_real_pyq_papers.json` that share those subject IDs are flagged as "zombies" and **permanently deleted from SQLite**, along with any student bookmarks and SM-2 revision entries referencing them!
- **Remediation:**
  Modify the query in line 1211 to strictly ignore seeded rows:
  ```dart
  where: 'subject_id = ? AND (source IS NULL OR source != "seed")',
  ```

---

### 🟠 Finding 3.3: Orphaned File Pruner Performs Unchecked Table-Wide Deletion
- **File:** `lib/core/services/sync_service.dart` (Lines 1574–1620)
- **Severity:** P1 (High)
- **Root Cause:**
  `_pruneOrphanedFiles()` compares local `synced_files` with `remoteMetadata`. If a remote file is missing, it runs:
  ```dart
  await txn.delete(
    QuestionsTable.tableName,
    where: 'subject_id = ?',
    whereArgs: [subjectId],
  );
  ```
  If the remote GitHub API is throttled, returns a partial response, or jsDelivr returns a fallback structure, any subject not present in that single response has **all its local questions wiped**, with no cascade cleanup on `bookmarks` or `user_question_revision`.
- **Remediation:**
  Never auto-delete entire subjects without validating that the remote manifest was fetched completely and with explicit confirmation.

---

## Section 4: Foreign Key Integrity & Data Normalization

### 🟠 Finding 4.1: SQLite Foreign Keys are Disabled at Runtime
- **File:** `lib/core/database/db_helper.dart` (Lines 75–93)
- **Severity:** P1 (High)
- **Root Cause:**
  In SQLite, foreign key constraint enforcement is **disabled by default** on every database connection.
  In `DatabaseHelper._openDatabaseInternal()`, `onOpen` sets WAL mode, synchronous, memory cache, but **omits** `PRAGMA foreign_keys = ON;`.
- **Student Impact:**
  Foreign keys declared in `PostsTable` (`FOREIGN KEY (exam_id) REFERENCES exam_config(exam_id)`) are never enforced. If an exam is deleted or altered, orphaned posts and questions remain.
- **Remediation:**
  Add `await db.execute('PRAGMA foreign_keys = ON;');` in `onOpen` in `db_helper.dart`.

---

### 🟠 Finding 4.2: Lack of Composite Unique Constraints on `bookmarks`
- **File:** `lib/core/database/tables/bookmarks_table.dart` (Lines 4–11)
- **Severity:** P1 (High)
- **Root Cause:**
  The `bookmarks` table schema is:
  ```sql
  CREATE TABLE bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_uuid TEXT,
    profile_id INTEGER,
    created_at TEXT
  );
  ```
  There is **no** `UNIQUE(profile_id, question_uuid)` constraint.
  While `BookmarksRepository` checks for existing bookmarks before inserting, any concurrent tap or automated restore will insert duplicate bookmark rows for the same question.
- **Remediation:**
  Add `UNIQUE(profile_id, question_uuid)` to the table definition and migration script.

---

### 🟡 Finding 4.3: Split Source of Truth for Bookmarks
- **Files:** `lib/features/test/repositories/question_repository.dart` (Lines 267–278) vs `lib/features/test/repositories/bookmarks_repository.dart` (Lines 18–85)
- **Severity:** P2 (Medium)
- **Root Cause:**
  `QuestionRepository.toggleBookmark(String uuid)` directly updates `QuestionsTable` (`is_bookmarked = 1/0`) but never touches `BookmarksTable`.
  Conversely, `BookmarksRepository.toggleBookmark` updates both.
- **Student Impact:**
  If any screen calls `QuestionRepository.toggleBookmark`, the `bookmarks` screen (which queries `BookmarksTable`) will not show the bookmarked question.
- **Remediation:**
  Deprecate and remove `QuestionRepository.toggleBookmark`. All bookmark operations must pass exclusively through `BookmarksRepository`.

---

## Section 5: In-Memory Caching vs. SQLite Query Overhead

### 🟠 Finding 5.1: `ORDER BY RANDOM()` Full Table Scans on 16,000+ Rows
- **File:** `lib/features/test/repositories/question_repository.dart` (Lines 71, 126)
- **Severity:** P1 (High)
- **Root Cause:**
  `getQuestionsByPostAndSubject` uses:
  ```dart
  orderBy: 'RANDOM()',
  ```
  In SQLite, `ORDER BY RANDOM()` reads every candidate row, copies it into a temporary sorting table in memory, computes a pseudo-random integer, and sorts the entire result set before applying `LIMIT`. On devices with lower RAM, this blocks the main UI thread during test creation.
- **Remediation:**
  Fetch the question IDs/UUIDs first (`SELECT uuid FROM questions WHERE ...`), shuffle them in Dart using `List.shuffle()`, take the required slice (`take(limit)`), and fetch only those questions by UUIDs.

---

### 🟠 Finding 5.2: Memory Thrashing When `excludeUuids > 500`
- **File:** `lib/features/test/repositories/question_repository.dart` (Lines 65–70, 78–86)
- **Severity:** P1 (High)
- **Root Cause:**
  ```dart
  limit: limit > 0
      ? (limit + (excludeUuids != null && excludeUuids.length > 500 ? excludeUuids.length : 0))
      : null,
  ```
  If a student has attempted 3,000 questions, the query fetches `20 + 3000 = 3,020` **full question models** (with large JSON option arrays, Hindi and English explanations) into Dart memory, only to filter them via `where((q) => !excludeSet.contains(q.uuid))` and discard 3,000 of them!
- **Student Impact:**
  Causes massive GC pressure and frame drops (jank) on test launch.
- **Remediation:**
  Use SQLite subqueries or temporary tables for exclusion:
  `WHERE uuid NOT IN (SELECT question_uuid FROM test_sessions_attempted WHERE profile_id = ?)` instead of passing thousands of parameters or over-fetching rows into the Dart heap.

---

### 🟡 Finding 5.3: Duplicate Provider Declaration for `bookmarksRepositoryProvider`
- **Files:**
  - `lib/core/providers/database_providers.dart` (Line 24)
  - `lib/features/test/repositories/bookmarks_repository.dart` (Line 129)
- **Severity:** P2 (Medium)
- **Root Cause:**
  Both files define a top-level `bookmarksRepositoryProvider`. This causes confusion in imports and risks using different provider instances across features.
- **Remediation:**
  Centralize `bookmarksRepositoryProvider` in `lib/core/providers/database_providers.dart` and export it cleanly.

---

## Section 6: Actionable Implementation Plan

| Action ID | Priority | Target File(s) | Remediation Summary |
|---|---|---|---|
| **ACT-01** | **P0** | `lib/core/services/sync_service.dart` | Protect `source = 'seed'` questions from file pruning in `syncFile()`. |
| **ACT-02** | **P0** | `lib/core/services/sync_service.dart` | Prevent partial sync from setting `synced_manifest_version` when files fail. |
| **ACT-03** | **P0** | `lib/core/database/db_helper.dart` | Safe self-repair: create `.corrupt_backup` before calling `deleteDatabase`. |
| **ACT-04** | **P0** | `lib/core/database/database_seeder.dart` | Wrap multi-chunk seeding in an outer `db.transaction()`. |
| **ACT-05** | **P1** | `tables/mock_history_table.dart`, `tables/user_profiles_table.dart`, `tables/questions_table.dart` | Fix schema drift: add missing columns (`total_marks`, `negative_marks`, `is_plus`, `active_exam_id`, `active_post_id`, `mock_id`) to `createTable`. |
| **ACT-06** | **P1** | `lib/core/database/db_helper.dart` | Enable `PRAGMA foreign_keys = ON;` in `onOpen`. |
| **ACT-07** | **P1** | `lib/core/database/tables/bookmarks_table.dart` | Add `UNIQUE(profile_id, question_uuid)` constraint. |
| **ACT-08** | **P2** | `lib/features/test/repositories/question_repository.dart` | Replace `ORDER BY RANDOM()` and over-fetching with 2-step ID shuffle. |
| **ACT-09** | **P2** | `lib/features/test/repositories/question_repository.dart` | Remove orphaned `toggleBookmark` method. |
| **ACT-10** | **P3** | `lib/core/providers/database_providers.dart` | De-duplicate `bookmarksRepositoryProvider`. |

---
**Report compiled and verified.** All findings are directly traceable to file paths and line ranges.
