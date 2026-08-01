# Server/VM Deployment — Discussion Notes

Working notes from planning discussion on moving this tool from a local
desktop `.exe` to a multi-user VM-hosted service. Not an implementation plan
yet — captures decisions made and questions still open, for when we're ready
to actually build the server version.

## Why the current desktop architecture doesn't carry over

The app today (`gvc_web.py` + `gvcAutomation.py` + `win_hide.py`) is built
as a single-user local desktop tool, not a server:

- **CAPTCHA solving requires a real interactive desktop.** A human watches
  a real Chrome GUI window and solves the CAPTCHA/logs in by hand.
- **`win_hide.py`'s window hide/show trick depends on that same
  interactive desktop.** `EnumWindows`/`ShowWindow` only work against the
  window station of the calling session — there's nothing to find/hide on
  a headless VM or a non-interactive service session.
- **Single global `Runner`.** One scan at a time, one user, in-process
  state — no session isolation.
- **Packaged as a PyInstaller windowed `.exe`**, bound to `127.0.0.1`,
  meant to be double-clicked on one person's machine, not run as a
  supervised background service.
- These also caused the original bug report that started this discussion:
  closing the console doesn't reliably stop the server, and Chrome can be
  orphaned (no window-station tricks needed to explain that once we're
  headless/server-side — the fix there is proper process supervision, not
  Win32 window games).

## Decisions made so far

1. **Go headless.** Chrome runs with no visible window at all
   (`--headless=new` or similar). This removes the dependency on an
   interactive desktop entirely and lets the app run as a normal
   background service (systemd unit / Docker container / Windows
   Service) with real start-stop-restart semantics.
   - Tradeoff accepted: headless Chrome is more fingerprintable than
     headful, so the portal's bot detection may trigger more CAPTCHAs or
     behave differently than it does today. Not solved yet — see CAPTCHA
     section below.

2. **Web frontend + VM backend, client-server.** This is basically what
   `gvc_web.py` already does architecturally (FastAPI backend + browser
   frontend polling `/api/status` for logs) — the change is turning it
   from "localhost-only desktop exe" into a real network-facing service:
   bind behind HTTPS with proper auth, host on a VM, keep (or upgrade to
   WebSocket/SSE) the log-streaming approach.

3. **Multi-user, concurrent.** Confirmed with the client: multiple users
   need to run scans at the same time from the same deployment. The
   current single global `Runner` singleton has to become a real
   per-session/per-user model (isolated browser session, credentials,
   proxy assignment, and log stream per user).

4. **Pakistan-IP requirement.** The portal is believed to only allow
   Pakistani IPs. Cloud VM hosting (AWS/Azure/DigitalOcean/etc.) is
   effectively never PK-based, so the VM's own IP won't work — traffic
   needs to be routed through a Pakistani proxy.
   - **Still need to confirm** the portal actually geofences by IP (vs.
     some other bot-detection signal) before committing to this.

5. **Proxy provider: Webshare rotating proxies.** Each user's connection
   on the VM gets its own dedicated Pakistani proxy IP — not one shared
   IP for every user. This avoids the failure mode where every user's
   automated session looks like one IP hammering the portal in parallel.
   - **Config requirement:** each user's proxy must be **sticky for the
     duration of that user's session** (same IP from login through the
     whole scan run), not rotated per-request — a mid-session IP change
     reads as suspicious to anti-bot systems the same way a shared IP
     does.
   - **Still need to confirm** Webshare's pool actually has adequate
     Pakistani IP coverage (PK is less commonly stocked than US/UK/EU in
     most providers' pools).

6. **Mobile access — resolved, no native app needed.** Because all the
   heavy lifting (headless Chrome, Selenium, proxy routing) runs
   server-side on the VM and the frontend is just a web page
   polling/streaming status over HTTP, the client can access it from a
   phone by opening the site URL in a mobile browser — no App
   Store/native app development required.
   - **Remaining work:** the current `web/index.html` is a fixed
     two-column desktop layout (`grid-template-columns: 400px 1fr`) and
     needs a responsive/mobile layout pass (stacked columns, touch-sized
     controls). This is a CSS/UI task, not a platform decision.

## Open questions — not yet decided

1. **CAPTCHA, for real.** Set aside earlier to unblock the
   headless/multi-user discussion, but headless means there's no window
   left for a human to look at, so this has to be solved before the
   server version is usable end-to-end. Two candidate approaches:
   - Relay the CAPTCHA (screenshot/iframe) to *that specific user's own
     browser* in the web frontend, let them solve it there, feed the
     answer back into their automation session.
   - A paid third-party CAPTCHA-solving service (2Captcha, CapSolver,
     etc.).
2. **Per-user isolation design** — how sessions, browser profiles,
   credentials, and proxy assignment are actually kept separate per user
   in code (replacing the single global `Runner`).
3. **Resource sizing / concurrency caps.** Each concurrent user is a full
   headless Chrome instance (CPU/RAM-heavy). How many concurrent users
   are expected, and does the VM need a queue/cap once it's full?
4. **Access control.** Does the frontend need real user
   accounts/login for customers, or is it internal-only?
5. **Storage of user PII/credentials.** Multi-tenant means storing other
   people's portal logins and passport details somewhere — needs a real
   answer (encryption at rest, per-user isolation), not the current
   "password is never written to disk" local-only approach.
6. **VM provider/OS choice.** Headless Chrome + Docker is most natural on
   Linux; the current codebase (`win_hide.py`, Job Object ideas, etc.) is
   Windows-only ctypes code that becomes irrelevant if we go Linux —
   worth deciding early since it affects how much of the existing code
   ports forward.
7. **Process supervision for N concurrent headless Chrome sessions** —
   proper cleanup/restart policy per session (containerized per-session
   is one option) so a crashed session can't leak a Chrome process the
   way the desktop version could.
8. **Anti-bot/rate-limiting strategy across many parallel sessions** —
   even with per-user PK proxies, many concurrent automated sessions
   hitting the same portal could get caught by pattern/timing-based
   detection, not just IP-based detection.

## Status

This is a future-deployment plan, not in progress. Near-term work
continues on the local `.exe` track separately — see next steps in
conversation.
