# HRMS — Fixes & Enhancements (implementation notes)

This document covers the five requested changes, how to enable them, and the
exact places where **you must supply credentials** (SMTP, social API tokens,
optional TURN server).

## ⚙️ One-time setup (required)

```bash
cd hrms_django
python manage.py migrate          # creates login_otps, password_resets, live_sessions
# (or apply hrms_system_schema.sql — it now contains the same 3 new tables)
python manage.py runserver 0.0.0.0:8000
```

The React app is served by Django from `../dist`. No rebuild is needed — the
prebuilt bundle in `dist/assets/` was patched directly.

---

## 1) Login with OTP verification ✅

- **Flow:** email + password → `POST /api/auth/login` (verifies against
  `app_users`, emails a 6-digit code) → the login screen shows a **code entry
  step** → `POST /api/auth/verify-otp` → dashboard access.
- OTP codes are salted + SHA-256 hashed in `login_otps`, expire in 10 min, allow
  5 attempts, and a single active code per user. "Resend code" is available.
- **⚠️ You must configure SMTP before first login**, or codes can't be sent and
  no one can log in. Easiest: set the global fallback in `hrms_django/.env`:
  ```
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=you@yourdomain.com
  SMTP_PASSWORD=<app password>
  SMTP_FROM_EMAIL=you@yourdomain.com
  ```
  (Gmail needs an **App Password**.)

  Alternatively you can use Resend instead of SMTP by setting:
  ```bash
  RESEND_API_KEY=<your resend api key>
  RESEND_FROM_EMAIL=you@yourdomain.com
  RESEND_FROM_NAME="HRMS"
  ```
  Resend is preferred if `RESEND_API_KEY` is configured; SMTP is only used as a
  fallback when Resend is not available.

  Per-user SMTP saved in **Settings → Email Configuration** overrides the global
  fallback when present.
  user set + confirm a new password, then `POST /api/auth/reset-password`
  **updates `app_users.password` in the database** for all future logins.
- The old client-only (EmailJS + localStorage) flow was replaced by these
  server endpoints, so the link arrives by real email and the DB is the source
  of truth.

## 3) Email Configuration → auto-post jobs to linked accounts ✅

- When a job is created (`POST /api/jobs`), it is auto-posted to the creator's
  linked social accounts via **real platform APIs** (LinkedIn UGC Posts + X/Twitter v2).
  Results are returned on the response as `socialResults`.
- **⚠️ Requires OAuth access tokens.** Store them in **Settings → Email
  Configuration**, which saves to `user_email_config.social`. Use this shape:
  ```json
  {
    "linkedin": { "url": "...", "accessToken": "<w_member_social token>", "authorUrn": "urn:li:person:XXXX" },
    "twitter":  { "url": "...", "accessToken": "<tweet.write user token>" }
  }
  ```
  A platform with only a profile URL (no token) is **skipped** with a note, so
  job creation never fails. Posting code: `hrms_django/api/social_poster.py`.
- To pass the creator, include `userEmail` in the job POST body (optional; if
  omitted the server uses any configured social account).

## 4) F2F Interview — recruiter LIVE access (WebRTC) ✅

- **Full live video**, peer-to-peer. When a candidate starts their AI interview,
  the browser publishes its camera/mic via `RTCPeerConnection`
  (`window.HRMSLive.publish`, in `dist/assets/hrms-live.js`).
- Recruiters (signed in) see a floating **🔴 Live Interviews** button with a live
  count. Clicking it lists active sessions (`GET /api/live`) and **Join** opens
  the live feed.
- SDP/ICE are exchanged by polling the backend (`/api/live/*`); the media flows
  directly browser-to-browser, so **no media server is needed**.
- **Networking note:** STUN servers are preconfigured. If candidate and recruiter
  are on restrictive networks (symmetric NAT), add a **TURN** server to make P2P
  reliable — edit the `ICE` array near the top of `dist/assets/hrms-live.js`:
  ```js
  { urls: "turn:YOUR_TURN_HOST:3478", username: "...", credential: "..." }
  ```

## 5) F2F Interview — follow-up "Send" fix ✅

- **Resilient dual-path send.** "Send" / "Send All Pending" first calls the
  server mailer (`POST /api/interviews/send-followup`, SMTP — same config as #1,
  and it persists `email_sent`+outcome to the DB). If that fails for *any* reason
  (server not restarted/route missing, no SMTP configured, network), it
  **automatically falls back to the EmailJS client sender** (the working
  hard-coded credentials already in the app), so the message still goes out.
  Only if **both** paths fail does it show the precise error in a toast.
- **Send now guides you:** clicking Send before choosing an outcome shows
  "Pick an outcome — Selected, Waitlisted or Rejected — before sending."
  (Previously the button was silently disabled, which read as "not working".)

### ⚠️ If Send still seems broken — check these (in order)
1. **Hard-refresh the browser** (Ctrl/Cmd+Shift+R). The JS bundle was renamed to
   `index-DCh6bk0Rv2.js` specifically to bust the year-long immutable cache;
   a stale cached `index-DCh6bk0R.js` would run the *old* code without the fix.
2. **Restart Django** so `POST /api/interviews/send-followup` exists, and run
   `python manage.py migrate`.
3. **Configure email** — SMTP in `.env`/Settings (#1). Without it the server path
   returns a clear "No SMTP configuration found…" message; the EmailJS fallback
   then tries to deliver (works if that EmailJS account is within quota).
4. The toast now shows the **exact** failure reason; the browser console also
   logs `[EmailJS]` results and the Network tab shows the endpoint response.

---

## Files changed / added

**Backend (`hrms_django/api/`)**
- `mailer.py` *(new)* — SMTP sending from per-user / env config + branded HTML.
- `auth_views.py` *(new)* — OTP login + forgot/reset endpoints.
- `social_poster.py` *(new)* — LinkedIn + X/Twitter posting.
- `live_views.py` *(new)* — WebRTC signaling endpoints.
- `models.py` — `LoginOtp`, `PasswordReset`, `LiveSession`.
- `migrations/0004_auth_and_live.py` *(new)*.
- `views.py` — job auto-post hook + `interview_send_followup`.
- `urls.py` — new `/api/auth/*`, `/api/live/*`, `/api/interviews/send-followup`.

**Frontend (`dist/`)**
- `assets/hrms-live.js` *(new)* — WebRTC publisher + recruiter monitor + button.
- `index.html` — loads `hrms-live.js`.
- `assets/index-DCh6bk0R.js` — patched: OTP login step, backend reset page,
  follow-up routed to server, candidate publish/stop hooks. A pre-edit backup is
  at `assets/index-DCh6bk0R.js.preauth.bak`.

**Verification:** `cd hrms_django && python smoke.py` runs all new endpoints
end-to-end on SQLite (SMTP mocked) — currently **all pass**.
