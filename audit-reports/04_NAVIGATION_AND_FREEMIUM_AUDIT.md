# 🧭 DEEP AUDIT REPORT: NAVIGATION, DEEP LINKS, FREEMIUM ACCESS, BILLING & SECURITY
**Devbhoomi Guru (UKPSC / UKSSSC Prep Platform)**  
**Version Audited:** `1.6.0+40` (App displayed: `1.5.9`)  
**Auditor:** Principal Mobile Architect & QA Security Auditor  
**Date:** November 2026  

---

## 🌟 EXECUTIVE SUMMARY

A rigorous, zero-compromise architectural and security audit was conducted covering Navigation stacks, Deep Links, Notification Routing, Freemium 3-Tier Gating, In-App Purchases (Google Play Billing), AdService fallbacks, and Repository Security.

| Audit Domain | Status | Severity | Primary Finding |
|---|---|---|---|
| **1. Deep Links & Notification Routing** | 🔴 Critical Flaw | High | No native intent filters in AndroidManifest; notifications have `onTap: null` (dead ends); no global `navigatorKey`. |
| **2. Freemium 3-Tier Gating** | 🟠 Vulnerabilities Found | High | Rewarded video ad bypass exploit in `ca_month_detail_screen.dart`; dead `_unlockedSets` memory state in `topic_selection_screen.dart`. |
| **3. Entitlement & Billing Resilience** | 🔴 Severe Bug | Critical | `TypeError` in `EntitlementStore` fallback (`prefs.getInt` vs `prefs.setString`) revokes paid VIP status on Keystore fail. Missing Lifetime plan in Paywall UI. |
| **4. AdService Fallbacks & Offline Mode** | 🟡 Acceptable with UX Lag | Medium | Graceful fail-open properly implemented across mock & set screens, but unbuffered 8s timeout freezes screen on flaky 2G networks. |
| **5. Navigation Stack & Pop Safety** | 🟢 Resilient & Secure | Low | CBT exam session lock, back confirmation modals, and `popUntil(r.isFirst)` on result screens are solidly engineered. |
| **6. Repository & Credential Security** | 🚨 Critical Security Hole | Severe | GitHub PAT token with write access (`ghp_...`) is embedded directly in client app code across 2 services. |

---

## 1. 🔗 DEEP LINKS & NOTIFICATION ROUTING AUDIT

### 1.1 Complete Absence of Native Intent Filters
- **File:** `android/app/src/main/AndroidManifest.xml` (Lines 48–65, 99–113)
- **Root Cause:**
  `MainActivity` only declares `android.intent.action.MAIN` and `android.intent.category.LAUNCHER`. There is **no `<intent-filter>`** defining custom schemes (`devbhoomiguru://`) or App Links / HTTP schemes (`https://devbhoomiguru.com/*`).
- **Student Impact:**
  Any external link shared via WhatsApp, Telegram, YouTube descriptions, or SMS cannot launch the app. Android treats the URL as an unhandled web link or fails to route it.
- **Recommendation:**
  Add deep-link intent filter in `AndroidManifest.xml`:
  ```xml
  <intent-filter android:autoVerify="true">
      <action android:name="android.intent.action.VIEW" />
      <category android:name="android.intent.category.DEFAULT" />
      <category android:name="android.intent.category.BROWSABLE" />
      <data android:scheme="devbhoomiguru" />
  </intent-filter>
  ```

---

### 1.2 Dead-End Notification Responses (Null `onTap`)
- **Files:**
  - `lib/core/services/notification_service.dart` (Lines 25, 33–40)
  - `lib/main.dart` (Lines 153–155)
  - `lib/features/splash/screens/splash_screen.dart` (Lines 67–75)
- **Root Cause:**
  `NotificationService` declares `Future<void> Function(String? payload)? onTap;`. However, in `main.dart` and `splash_screen.dart`, it is initialized via `await NotificationService().init();` without passing any `onTap` handler!
  Furthermore, `MyApp` in `lib/main.dart` does not declare a `navigatorKey: ...`.
- **Student Impact:**
  When a student receives a notification (Ranbheri Subah Mission, Shaam Streak Guard, Content Update, or Git Remote Broadcast) and taps on it:
  1. `onDidReceiveNotificationResponse` finds `onTap == null`.
  2. The notification merely brings the app to foreground on Home Tab 0.
  3. The deep route or mission target in the payload is completely discarded.
- **Recommendation:**
  1. Provide a top-level `GlobalKey<NavigatorState> appNavigatorKey = GlobalKey<NavigatorState>();` in `MaterialApp`.
  2. In `_initNonBlockingBackgroundServices()` and `splash_screen.dart`, register `NotificationService().init(onTap: (payload) => ...)` and route payloads through `AppDeepLinkRouter`.

---

### 1.3 `AppDeepLinkRouter` Routing Gaps & Substring Traps
- **File:** `lib/core/utils/app_deep_link_router.dart` (Lines 65–148)
- **Root Cause:**
  1. `AppDeepLinkRouter` is only called from `flash_news_banner.dart` and `flash_news_ticker.dart`; it is disconnected from notifications, system intents, and app cold-start.
  2. If a clean route starts with `subject:` or `practice:`, but the subject is unknown (e.g. `subject:chemistry`), it falls through to Step 5 (Lines 126–147) and forces open `SpecialMockPapersScreen(initialPostId: 'chemistry')`, creating a broken screen with empty mock cards.
  3. It lacks query parameter support (e.g., `mockNumber=3`, `setNumber=6`, `mode=booklet`).
- **Recommendation:**
  Validate `matchedSubjectId` against known catalogs; if invalid, show an informative SnackBar or fallback to `MainShell` Tab 1 (PracticeTab) instead of forcing `SpecialMockPapersScreen`.

---

## 2. 🛡️ FREEMIUM 3-TIER GATING AUDIT (FREE 1-5, AD 6-10, VIP 11+)

### 2.1 Critical Bypass: Rewarded Video Ad Early Close Exploit
- **File:** `lib/features/test/screens/ca_month_detail_screen.dart` (Lines 83–106)
- **Root Cause:**
  In `_onPartTap`, `AdService().showRewardedAd` is called **without supplying `onUserCancelled`**:
  ```dart
  await AdService().showRewardedAd(
    onAdDismissed: () async { ... await _launchPartTest(part, isEnglish); },
    onAdFailed: () async { ... await _launchPartTest(part, isEnglish); },
    // onUserCancelled is omitted!
  );
  ```
  In `lib/core/services/ad_service.dart` (Lines 364–368):
  ```dart
  if (!_rewardGranted) {
    if (onUserCancelled != null) {
      onUserCancelled();
    } else {
      onAdFailed(); // <--- TRIGGERS FAIL-OPEN!
    }
  }
  ```
- **Student Impact / Security Hole:**
  Any student can tap an ad-locked Current Affairs test, immediately click the 'X' (close) button within 1 second without watching the video, and the app triggers `onAdFailed`, unlocking the test immediately.
- **Recommendation:**
  Always provide `onUserCancelled` in `ca_month_detail_screen.dart`:
  ```dart
  onUserCancelled: () {
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(isEnglish ? 'Please watch the complete ad to unlock.' : 'कृपया टेस्ट अनलॉक करने हेतु पूरा वीडियो देखें।')),
      );
    }
  },
  ```

---

### 2.2 False Lock / Lost State: Zombie `_unlockedSets` in `TopicSelectionScreen`
- **File:** `lib/features/test/screens/topic_selection_screen.dart` (Lines 214, 2321, 2523, 2715)
- **Root Cause:**
  `final Set<int> _unlockedSets = {};` is declared and checked in 5 places (`isFree = isPlus || policy.isSetFree(...) || _unlockedSets.contains(setNum)`).
  However, `_unlockedSets.add(...)` is **never called anywhere in the file**!
  When the video finishes, `_unlockAndStartSet` only launches the test with `isSessionPass: true`.
- **Student Impact:**
  If a student watches a video ad to unlock Set 6, finishes the 25 questions, and returns to `TopicSelectionScreen`, Set 6 still displays the Blue `🎬 वीडियो देखें (अनलॉक)` badge. If they tap it to review or retry, they are forced to watch another ad or locked out.
- **Recommendation:**
  In `_unlockAndStartSet`:
  ```dart
  setState(() {
    _unlockedSets.add(setNum);
  });
  ```

---

### 2.3 Weakness Drill Ad Bypass in `ResultScreen`
- **File:** `lib/features/test/screens/result_screen.dart` (Lines 391–401)
- **Root Cause:**
  ```dart
  await AdService().showRewardedAd(
    onAdDismissed: () async { await _startWeaknessDrill(wrongQuestions); },
    onAdFailed: () async { await _startWeaknessDrill(wrongQuestions); },
    onUserCancelled: () async { await _startWeaknessDrill(wrongQuestions); }, // <--- BYPASS
  );
  ```
  Canceling the ad still launches the Weakness Drill. While student-friendly, this makes the ad gate entirely cosmetic.

---

## 3. 💳 IN-APP PURCHASES & GURU PLUS PAYWALL AUDIT

### 3.1 Catastrophic Type Crash: Revoking VIP Entitlement on Keystore Glitch
- **File:** `lib/features/billing/services/entitlement_store.dart` (Lines 91 vs 167)
- **Root Cause:**
  In `writePlus()` (Line 167):
  ```dart
  await prefs.setString(
    BillingIds.expiryStorageKey,
    expiryDate.millisecondsSinceEpoch.toString(), // Wrote String
  );
  ```
  In the `readEntitlement()` fallback `catch` block (Line 91):
  ```dart
  final expiryMs = prefs.getInt(BillingIds.expiryStorageKey); // Reading as int!
  ```
  Dart throws `TypeError: type 'String' is not a subtype of type 'int' in type cast`.
  This unhandled exception is caught by the inner `catch (_) { return const EntitlementRecord(isPlus: false); }`!
- **Student Impact:**
  If `FlutterSecureStorage` has a temporary Keystore timeout or initialization glitch (common on low-end Xiaomi/Realme phones during cold boots), the fallback logic crashes with a type exception and **instantly revokes the paying student's VIP pass**, showing them as a Free user!
- **Recommendation:**
  In `entitlement_store.dart` Line 91:
  ```dart
  final expiryRaw = prefs.getString(BillingIds.expiryStorageKey);
  final expiryMs = expiryRaw != null ? int.tryParse(expiryRaw) : prefs.getInt(BillingIds.expiryStorageKey);
  ```

---

### 3.2 Missing Lifetime Plan Card on Paywall UI
- **Files:**
  - `lib/features/billing/constants/billing_ids.dart` (Line 11: `guruPlusLifetime`)
  - `lib/features/billing/widgets/vip_pricing_cards.dart` (Lines 51–92)
- **Root Cause:**
  `BillingIds.guruPlusLifetime` (₹199 Lifetime) is defined, priced, and handled in `EntitlementController` and `guru_plus_screen.dart` (Lines 64–75).
  However, `VipPricingCards` **only renders two plans**:
  - Plan 1: 1 Year Full Pass (`BillingIds.guruPlus1Year`, ₹149)
  - Plan 2: 1 Month Sprint Pass (`BillingIds.guruPlus1Month`, ₹79)
  The Lifetime plan card is **completely absent from the UI**!
- **Student Impact:**
  Students cannot select or purchase the Lifetime VIP plan from the UI, even though it is marketed as the primary tier across other screens.
- **Recommendation:**
  Add the Lifetime VIP plan card to `VipPricingCards.build()`.

---

### 3.3 Google Play Billing Policy Disclosures Missing
- **Files:**
  - `lib/features/billing/screens/guru_plus_screen.dart`
  - `lib/features/billing/widgets/vip_trust_strip.dart`
  - `lib/core/constants/app_strings.dart`
- **Root Cause:**
  1. **No Refund Policy Statement:** The paywall screen lacks any refund disclosure (e.g. Google Play 48-hour standard refund policy or contact details).
  2. **No Direct Support Email:** `guru_plus_screen.dart` has no support contact or email. If payment fails or money is deducted without entitlement, there is no email provided on the screen.
  3. **Version Desync:** `AppStrings.appVersion` is hardcoded to `'1.5.9'` while `pubspec.yaml` specifies `1.6.0+40`.

---

## 4. 📺 ADSERVICE RESILIENCE & OFFLINE BEHAVIOR

### 4.1 Graceful Fail-Open Evaluation
- **Analysis:**
  - When `AdService().showRewardedAd` encounters an AdMob no-fill, offline mode, or timeout:
    - It triggers `onAdFailed()`.
    - `mock_list_screen.dart`, `special_mock_papers_screen.dart`, `topic_selection_screen.dart`, and `pre_test_screen.dart` all display a student-friendly SnackBar:
      `"विज्ञापन नेटवर्क व्यस्त है, आपके अध्ययन के लिए टेस्ट शुरू किया जा रहा है।"`
    - They grant an immediate session pass and launch the test.
  - **Verdict:** Highly ethical, student-first freemium design. The app never blocks an offline student from studying.

### 4.2 Latency Friction: 8-Second Freeze on Mountain 2G/3G
- **File:** `lib/core/services/ad_service.dart` (Lines 276–281)
- **Issue:**
  The `fallbackTimer` duration is set to **8 seconds**. In Uttarakhand hill districts with flaky mobile data, if a student taps "Watch Ad", the screen waits up to 8 seconds without a progress spinner before fail-open kicks in.
- **Recommendation:**
  Reduce fallback timeout to 4–5 seconds and display a non-blocking circular loading HUD (`"विज्ञापन लोड हो रहा है..."`) so the UI does not feel frozen.

---

## 5. 🔄 NAVIGATION STACKS, POP BEHAVIOR & LOOP SAFETY

### 5.1 Active CBT Session Discipline
- **Files:**
  - `lib/features/test/screens/test_screen.dart` (Lines 119–125, 340–405)
  - `lib/features/test/screens/real_exam_booklet_screen.dart` (Lines 638–644, 540–590)
- **Analysis:**
  Both active exam environments use `PopScope(canPop: _canPop, onPopInvokedWithResult: ...)` to intercept Android predictive back and iOS edge swipes.
  They present a Devanagari confirmation modal (`AppModalSheet.showConfirmation`). Unanswered sessions are safely discarded; in-progress sessions are flushed to SQLite before popping.
- **Verdict:** Compliant with `flutter-adaptive-navigation` Rule 3.

---

### 5.2 Result Screen Back Stack Unwinding
- **Files:**
  - `lib/features/test/screens/result_screen.dart` (Lines 64–70)
  - `lib/features/test/screens/booklet_result_screen.dart` (Lines 243–249)
- **Analysis:**
  Both screens implement:
  ```dart
  onPopInvokedWithResult: (didPop, _) {
    if (!didPop) {
      Navigator.popUntil(context, (r) => r.isFirst);
    }
  }
  ```
  This eliminates back-button loops into completed exams and returns the student cleanly to `MainShell`.

---

### 5.3 Tab Switching & Double-Press App Exit
- **File:** `lib/features/home/screens/main_shell.dart` (Lines 106–129)
- **Analysis:**
  - If current tab is not Home (Tab 0), back press switches to Tab 0.
  - If current tab is Home, requires double-press within 2 seconds with SnackBar alert.
  - Drawer item "Settings & Profile" correctly pops the drawer and sets `selectedTabProvider.notifier.state = 3`.

---

## 6. 🚨 CRITICAL SECURITY VULNERABILITY: EXPOSED GITHUB PAT

### 6.1 Master PAT Hardcoded in Client Services
- **Files:**
  - `lib/core/services/student_feedback_service.dart` (Lines 26–29)
  - `lib/core/services/question_report_service.dart` (Lines 32–35)
- **Vulnerability Details:**
  The app splits a GitHub Personal Access Token into 3 static string segments:
  ```dart
  static const String _t1 = 'ghp_REDACTED';
  static const String _t2 = 'REDACTED';
  static const String _t3 = 'REDACTED';
  static String get _token => '$_t1$_t2$_t3';
  ```
  This token had **full write access** to the `sherawali/uk-exam-questions-pro` repository (which hosts `master_config.json`, question banks, and app sync manifests).
- **Attack Vector:**
  1. Any basic APK extraction (`strings classes.dex` or Dart snapshot inspector) reveals the concatenated token in seconds.
  2. Any proxy (mitmproxy, Charles, HTTP Toolkit) capturing the feedback submission logs the full header: `Authorization: Bearer ghp_REDACTED`.
  3. An attacker could delete questions, tamper with exam keys, or push malicious content to thousands of students.
- **Immediate Fix Required:**
  1. **Revoke the exposed PAT immediately in GitHub Settings.**
  2. Replace direct client-side GitHub Issue API calls with a secured Cloudflare Worker or Supabase edge function that uses a server-side secret with write-only issue creation permissions.

---

## 📋 CONSOLIDATED ACTION PLAN

| Priority | Action Item | Target Files |
|---|---|---|
| 🚨 **P0 - Critical** | Revoke exposed GitHub PAT and route feedback via proxy | `student_feedback_service.dart`, `question_report_service.dart` |
| 🔴 **P1 - High** | Fix `EntitlementStore` fallback type crash (`prefs.getString` vs `prefs.getInt`) | `lib/features/billing/services/entitlement_store.dart` |
| 🔴 **P1 - High** | Add `onUserCancelled` to `ca_month_detail_screen.dart` to close ad bypass | `lib/features/test/screens/ca_month_detail_screen.dart` |
| 🟠 **P2 - Medium** | Hook up `NotificationService.onTap` with global `navigatorKey` and `AppDeepLinkRouter` | `lib/main.dart`, `lib/core/services/notification_service.dart` |
| 🟠 **P2 - Medium** | Populate `_unlockedSets.add(setNum)` on rewarded ad unlock | `lib/features/test/screens/topic_selection_screen.dart` |
| 🟡 **P3 - Medium** | Add missing Lifetime VIP Card to `VipPricingCards` UI | `lib/features/billing/widgets/vip_pricing_cards.dart` |
| 🟡 **P3 - Low** | Add refund disclosure & support email to `guru_plus_screen.dart` | `lib/features/billing/screens/guru_plus_screen.dart` |
| 🟢 **P4 - Low** | Sync `AppStrings.appVersion` (`1.5.9` ➔ `1.6.0+40`) | `lib/core/constants/app_strings.dart` |
| 🟢 **P4 - Low** | Add custom scheme intent-filter in `AndroidManifest.xml` | `android/app/src/main/AndroidManifest.xml` |
