# Access Control & Code Protection — Discussion Notes

Working notes from the planning discussion on gating the prototype behind a
Firebase login bound to a specific machine, and on stopping a recipient of the
shared folder from extracting the automation. **Not an implementation plan** —
the requirements interview was cut short. Captures the requirement, two findings
that change the design, and the questions still open, for when we pick this up.

Companion to `server-deployment-plan.md` (VM/multi-user track). See the
cross-reference at the end — the mobile APK goal changes one of its conclusions.

---

## 1. The requirement, as stated

The client wants to share this prototype with named individuals, one at a time,
without those people being able to pass it on:

1. The recipient sends the client their email address.
2. The client adds that email to Firebase and **sets the password themselves**
   (the user does not self-register or choose their own password).
3. The client tells the recipient: "use your email, and this password."
4. On the recipient's **first login**, the app reads the machine's **MAC
   address** and stores it in Firebase against that account.
5. On **every later login**, Firebase checks email + password + MAC. All three
   must match or the login is refused.
6. Separately: if the recipient shares the whole setup folder onward, nobody
   should be able to open it, pull out the automation, and run it themselves.

**Scope: prototype only.** The end goal is a **mobile app installed from an
APK** — the reason being that a phone app can read the incoming **OTP SMS
automatically**, which is the one step of the booking flow that still needs a
human today.

---

## 2. Findings that change the design

Both were established during the discussion, not assumed.

### 2.1 MAC address alone is a weak binding

- **Spoofable in about two minutes**, no tooling required: Device Manager →
  network adapter → Advanced → Network Address → type a new value. Also settable
  from PowerShell or the registry.
- **It legitimately changes.** A machine has a different MAC per adapter, so
  Wi-Fi and Ethernet do not match. Docking stations, USB-C hubs, VPN clients and
  virtual adapters (VirtualBox, Hyper-V, WSL) all add more. Windows also ships
  **MAC randomisation** for Wi-Fi, on by default on many builds.
- Net effect, if bound to MAC alone: a determined sharer gets through, while a
  paying user who undocks their laptop gets locked out. **Both failure modes at
  once, in the wrong direction.**

Recommendation to consider: hash a **composite** of Windows `MachineGuid` +
motherboard/disk serial + MAC into one device ID. Still MAC-derived as asked,
but survives an adapter change and is materially harder to forge. Store only the
hash server-side, never the raw identifiers.

### 2.2 PyInstaller gives no source protection — demonstrated, not theorised

During this session's build verification, `gvcAutomation`'s compiled bytecode was
extracted straight out of the shipped `GVC_App/GVC_Scanner.exe` using
PyInstaller's **own** reader classes:

```python
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
car = CArchiveReader(r"GVC_App\GVC_Scanner.exe")
open("pyz.tmp", "wb").write(car.extract(next(n for n in car.toc if n.endswith(".pyz"))))
code = ZlibArchiveReader("pyz.tmp").extract("gvcAutomation")   # module code object
```

That is roughly six lines, using a library the recipient already has if they have
Python. The resulting code objects decompile back to readable source with
standard tools. The `.exe` is a container, not a lock.

**Consequences that follow:**

- **A local licence gate can be patched out.** If the bundle is extractable, the
  "is this user allowed?" check can be found and neutered. The login gate and the
  code protection are **one problem, not two** — a gate is only as strong as the
  packaging around it.
- **Firebase client API keys are public by design.** They will ship inside the
  app and can be read. This is expected and documented by Google — but it means
  **Firestore security rules have to carry the entire weight**. Nothing may
  depend on the key staying secret.
- Realistically: client-side protection raises the *cost* of theft. It cannot
  make theft impossible while the automation executes on someone else's machine.
  The only complete answer is moving the valuable logic server-side — which is
  what `server-deployment-plan.md` is about, and is not available for this
  prototype.

---

## 3. Open questions

These are the four the interview was about to put to the client, with the
options and a recommendation each. **None is decided.**

### Q1 — How is the device identified?

| Option | Trade-off |
|---|---|
| **Composite fingerprint incl. MAC** *(recommended)* | MachineGuid + board/disk serial + MAC, hashed. Survives adapter changes; much harder to fake. |
| MAC only | Matches the brief literally. Accepts spoofing and false lockouts (§2.1). |
| MachineGuid only | Stable across adapters, no false lockouts — but resets on Windows reinstall, and is not a MAC. |

### Q2 — What happens on a new machine?

| Option | Trade-off |
|---|---|
| **Block, admin clears the binding on request** *(recommended)* | Tightest control. Show the device ID in the error so the user can send it. Puts the client in the loop for every hardware change. |
| Allow N device slots per account | Self-service, no support load. But N slots can become N people. |
| Block with a self-service cooldown | Lowest support load, weakest control — a sharer just waits out the cooldown. |

### Q3 — How are users administered?

| Option | Trade-off |
|---|---|
| **Firebase Console by hand** *(recommended for prototype)* | Add the user in Auth, create one Firestore doc. Nothing extra to build or secure. Fine for a handful of users. |
| A small admin CLI we build | `add-user`, `list`, `reset-device`, `disable`. Faster at scale, but needs a **service-account key with full admin rights that must never ship to clients**. |
| Console now, CLI later | Document the manual steps so the CLI is easy to add if user count grows. |

### Q4 — How much code-protection effort?

| Option | Trade-off |
|---|---|
| **Free hardening + server-side gate** *(recommended)* | Strip source/docs from the bundle, obfuscate names, make the check depend on data only the server can supply so a no-op'd gate breaks the app. Real friction, zero cost. |
| That plus PyArmor (paid) | Commercial bytecode encryption; strongest realistic option for desktop Python. Licence fee, plus a build step that can fight PyInstaller. |
| Minimal — login gate only | Accept that a technical recipient bypasses it. Defensible if prototype users are non-technical. |

### Q5 — Offline behaviour *(was going to be decided, not asked)*

Proposed: **require network at launch.** The automation cannot reach the visa
portal without internet anyway, so an offline grace period buys nothing and only
opens a bypass (pull the network cable, skip the check). Worth confirming rather
than assuming.

---

## 4. Design sketch — undecided, for reference only

- **Firebase Auth**, email/password provider. Admin-created accounts only;
  self-registration disabled.
- **Firestore**, one document per user keyed by `uid`:
  `{ email, deviceHash, boundAt, disabled, note }`.
- **Security rules** doing the real work:
  - a user may read only their own document;
  - a user may write `deviceHash` **only when it is currently null** — the
    first-login claim — and may never overwrite or clear it;
  - `disabled` is not client-writable at all;
  - only the service account (admin) can clear `deviceHash` to re-bind.
- **Gate placement** in the app: before the main window is usable, and re-checked
  before a scan starts — so a stale window cannot keep running after an account
  is disabled.
- **Failure UX:** on mismatch, show the computed device ID so the user can send
  it to the client for a reset. On network failure, say so plainly rather than
  failing closed with a misleading "access denied".

Files this would touch: `gvc_tkinter.py` (gate before `build_ui` / inside
`start_scan`), a new auth module, and `GVC_Scanner.spec` (bundle hardening).

---

## 5. Cross-reference: this changes a conclusion in `server-deployment-plan.md`

That document's **item 6** concluded *"Mobile access — resolved, no native app
needed"*, on the grounds that a phone browser can reach the web frontend.

**That no longer holds.** The client's stated end goal is an installed **APK**,
and the reason is specific: **automatic OTP SMS capture**. A mobile web page
cannot read incoming SMS — that needs native permissions. So the APK is not a
nicer wrapper around the web UI, it exists for a capability the web UI cannot
have. Item 6 should be revisited when the mobile track starts.

Related existing code: `gvcAutomation.select_slot_and_request_otp()` and
`verify_otp_requested()` request the OTP and confirm the portal accepted it; the
user currently types the received code into Chrome by hand. That manual step is
what the APK is meant to remove.

Standing constraint to carry forward: `#otpuser` on the portal holds a serialised
User object including the username and password hash — treat as secret, never log
it.

---

## Status

Not started. Blocked on Q1–Q5 above. Near-term work is on other features.
