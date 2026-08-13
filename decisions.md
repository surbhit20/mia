# Decisions Log

Every non-obvious decision made while designing or implementing this project
— what was decided, why, and what else was considered. Not for routine or
self-evident code changes; for anything where a different call could
reasonably have been made (architecture, library/tool choice, scope cuts,
reversals of earlier decisions).

Newest entries at the top. When implementation surfaces a decision not
already covered by the design spec (`docs/superpowers/specs/`), log it here
at the time it's made, not retroactively.

---

## 2026-08-13 — Live test result: Attendee's anonymous Google Meet join does NOT work (contradicts docs)

**Why this matters:** The previous entry's pivot decision rested on Attendee's
documented claim that signed-in bot login is optional for Google Meet, with
anonymous guest join as the default. Self-hosted Attendee was deployed
locally (Docker Compose, see below for the deployment issues hit along the
way) and tested against two real, live Google Meet calls:
1. An instant meeting started ad-hoc from a personal account.
2. A calendar-scheduled meeting (with a Google Meet link generated via
   Calendar → Add Google Meet video conferencing), joined as a human host
   *before* dispatching the bot, specifically to test whether host presence
   changed the outcome.
Both attempts failed identically: `state: "fatal_error"`, event
`could_not_join_meeting`, `sub_type: "login_required"`. Worker logs showed
the bot's browser navigated to `accounts.google.com` — the same sign-in wall
fought manually all afternoon with our own Playwright approach. Ruling out
both "instant vs. scheduled meeting" and "host absent vs. present" as the
variable means this looks like a general, unconditional requirement: Google
now appears to require sign-in for any Meet join attempt from an automated
browser, contradicting Attendee's own documentation (which may be stale, or
may describe behavior that applied before Google tightened this).
**Status:** open — next step is deciding whether to pursue signed-in bots
(requires a paid Google Workspace account on a custom domain, per the
earlier entry) or reconsider the approach again. Not yet decided.

## 2026-08-13 — Attendee local deployment: two Docker issues fixed along the way

1. **OOM during parallel build** (exit 137) on an 8GB-RAM Apple Silicon Mac:
   `docker compose build` builds all three services (app/worker/scheduler)
   in parallel by default; since they share one Dockerfile, building only
   `attendee-app-local` first (then letting the other two hit cache) avoided
   the memory spike instead of raising Docker's memory allocation (risky
   with only 8GB total system RAM).
2. **`OSError: [Errno 35] Resource deadlock avoided`** on Python imports
   from the bind-mounted source directory (`volumes: - .:/attendee` in
   `dev.docker-compose.yaml`), even with VirtioFS already enabled — likely
   concurrent/forked-process file reads racing under `amd64` emulation on
   Apple Silicon. Fixed by removing the bind mount entirely for
   `attendee-app-local`/`-worker-local`/`-scheduler-local` (we're running
   Attendee as infrastructure, not developing its codebase, so we don't
   need live code reload — the image's baked-in `COPY` from build time is
   sufficient). This had a side effect: removing the bind mount also
   removed the container's only way to see `.env` (Django loads it via
   `load_dotenv()` + `os.getenv()` from the working directory), which
   surfaced as `CREDENTIALS_ENCRYPTION_KEY` migration failing with `None`.
   Fixed by adding `env_file: .env` to each of the three services instead —
   confirmed safe since Django reads via `os.getenv()`, which `env_file`
   populates directly without needing a physical `.env` file in the
   container.

## 2026-08-13 — Pivot: replace Playwright JoinWorker with self-hosted Attendee.dev; no bot-account login needed for joining

**Why:** After an entire afternoon fighting Google's automation-detection
block on sign-in (see the two entries below), decided to test whether a
self-hosted, purpose-built meeting-bot project had already solved this
before continuing to patch our own Playwright approach. Verified two things
directly from Attendee's source and docs before committing to the switch:
(1) Attendee genuinely supports two-way realtime audio (base64 PCM in/out
over websocket, `/output_audio` and `/speech` REST alternatives), so it can
replace the whole join+capture+inject layer, not just the join; (2)
critically, **signed-in bot login is optional for Google Meet — with none
configured, the bot joins as an anonymous guest by default.** Signed-in mode
exists (`bots/templates/projects/partials/google_meet_bot_logins.html`) but
requires a *paid Google Workspace account on a custom domain* with a
private-key/certificate auth flow — not password login, and not something
our free consumer `surbhit.bot@gmail.com` account could use anyway. This
means the entire Chrome-profile-copying/real-Chrome/command-line-flag saga
from today is now moot for the join step: Attendee's own bot infrastructure
handles anonymous join, sidestepping Google's consumer-account sign-in
detection entirely (which is precisely the problem class Attendee exists to
solve).
**What changes:** `join_worker.py` (Playwright), `audio/capture.py`
(BlackHole), `audio/injection.py` (sounddevice) are retired. New
`AttendeeClient` module talks to a locally self-hosted Attendee instance
(Docker Compose: Django + Postgres + Redis, cloned to `~/Desktop/attendee`,
sibling to this project). VAD, wake-word matching, command buffer, our own
Deepgram STT wrapper, Claude tool-calling, and ElevenLabs TTS all stay
unchanged — they just get their audio from/to Attendee's websocket instead
of BlackHole. The bot Google account is still used for Calendar API access
(unrelated, already working via OAuth) but is no longer needed for the Meet
*join* mechanism.
**Requires:** real AWS credentials + an S3 bucket (Attendee's self-hosting
guide states the app doesn't work without S3 configured; no documented
MinIO/local-S3 fallback).
**Not yet validated:** whether Attendee's anonymous-guest join actually
works end-to-end against a real Meet call in practice (still needs a live
test) — the docs/source confirm the *design*, not yet an observed result.
**Alternatives considered:** continuing to patch our own Playwright
approach (stealth patches, etc.) — rejected as an unbounded arms race
against Google's detection, after already burning significant time on
narrower fixes (`channel="chrome"`, `$HOME` vs `~`, native-profile login)
that each solved one symptom and hit another.

## 2026-08-13 — `SETUP.md`'s login command must use `$HOME`, not `~`, and must not sign in from Playwright at all

**Why:** Two real bugs found during live manual setup, on top of the
`channel="chrome"` fix above:
1. `~/.mia/chrome-profile` passed as a literal string to
   `launch_persistent_context` is never expanded (Playwright doesn't do
   `~`-expansion, and it's inside quotes so the shell doesn't either) — it
   silently resolves relative to the current working directory instead,
   creating a bogus nested profile (observed: a literal folder named `~`
   inside the project directory) rather than erroring. `SETUP.md`'s command
   now uses `$HOME` inside the double-quoted `-c` string, which the shell
   does expand.
2. `channel="chrome"` alone doesn't avoid Google's sign-in block: Google
   also flags *any* Chrome instance launched with extra command-line flags
   (a bare `--user-data-dir` included), independent of whether real CDP
   automation is involved. A completely unautomated `open -a "Google
   Chrome" --args --user-data-dir=...` hit the identical "Couldn't sign you
   in ... browser or app may not be secure" error. `SETUP.md` now instructs
   doing the login through Chrome's native profile switcher (Add Chrome
   Profile), with zero command-line flags, then copying that profile's data
   into `~/.mia/chrome-profile` afterward — Playwright only ever opens an
   *already-authenticated* profile, never triggers the sign-in flow itself.
**How to apply:** if a future setup step needs a fresh authenticated Google
session, assume any non-default browser launch flag risks this same block;
prefer creating/signing in via the browser's own UI and handing automation
an already-authenticated profile afterward.

## 2026-08-13 — JoinWorker uses real Chrome (`channel="chrome"`), not bundled Chromium

**Why:** Discovered during live manual setup — Google's sign-in flow actively
detects Playwright's bundled Chromium (via `navigator.webdriver` and other
automation fingerprints) and refuses login outright ("Couldn't sign you in
... This browser or app may not be secure"), even for a real human typing a
real password. Real, locally-installed Google Chrome doesn't carry the same
default automation signature. Since the one-time manual login (`SETUP.md`
step 2) and the bot's later automated joins (`JoinWorker.join`) both need to
work against the same persistent profile, both now use `channel="chrome"`
for consistency — logging in with one browser binary and joining with
another would risk profile/session incompatibilities beyond just the
detection risk.
**Requires:** real Google Chrome installed on the machine running `mia`
(separate from the Playwright-managed Chromium installed via
`playwright install chromium`, which is no longer what `JoinWorker` uses).
**Alternatives considered:** stealth/anti-detection patches for bundled
Chromium — more fragile, actively fought by Google, not worth the
maintenance burden for a one-line fix.

## 2026-08-12 — Accepted: Deepgram keepalive can't fire during a blocking voice turn

**Why:** The final whole-branch review's fix for "no keepalive, dead socket
ejects the bot" (`stt.py`'s `send_keepalive_if_idle()`) is driven from the
same loop thread that blocks synchronously through `dispatch_command`
(Claude), `synthesize` (ElevenLabs), and `inject_into_virtual_mic` (blocking
playback) — so for a voice turn lasting the full 7-12s the original finding
described, the keepalive is structurally unable to fire until *after* that
window, not during it. The re-review caught this; it's a narrower, already-
acknowledged residual (no reconnect logic was ever in scope), not a
regression of the part that matters most: an exception from a dead socket no
longer crashes the loop or ejects the bot — verified by execution — it now
degrades to "deaf until re-triggered" instead.
**How to apply:** Accepted as-is rather than triggering a second fix wave,
since (a) the dangerous failure mode is closed and verified, and (b) fully
validating any further fix needs a real Deepgram session anyway, consistent
with this project's live-tuning-required constants elsewhere. First thing to
try if live testing shows the bot going deaf mid-turn: send a keepalive
immediately before entering the blocking Claude/TTS/injection section in
`main.py`'s `_run_call_loop`, in addition to the existing idle-based one.

## 2026-08-12 — Final-review fix pass: wake word, dedup TTL, frame size

**Why:** Whole-branch review surfaced choices the per-task reviews couldn't
see. Three of the fixes had real alternatives:
- **Wake word `hey mia`, not `hey bot`.** "hey bot" fuzzy-matched ordinary
  meeting speech ("the bottom line", "they both agreed") 4/20 times at the
  shipped threshold; "hey mia" scored 0/20 with every true positive still
  matching. Raising the threshold instead was rejected — "they both" scores
  100.0, so no threshold separates it.
- **State entries expire after 4 hours** instead of never. Meet URLs are
  stable across a recurring event, so a permanent "skipped" blacklisted
  tomorrow's standup. Four hours outlives any single meeting (no call can
  re-prompt itself mid-session) and clears well before the next day.
  Alternative considered: expiring only "skipped"/"prompted" — rejected, a
  stale "joined" from a crashed run is the same trap.
- **32ms audio frames, not the spec's 30ms.** silero-vad's window is exactly
  512 samples at 16kHz; 30ms is 480 and was zero-padded on every inference.
  `FrameVAD` now rejects a mismatched `frame_ms` rather than silently
  padding, and the end-of-command silence run was retuned (24 frames) to
  keep the same ~768ms window.

**Also:** `LOGFIRE_TOKEN` became optional (the spec requires Logfire never
be a hard dependency); with no token, `logfire.configure(send_to_logfire=False)`
is used so log calls stay silent no-ops rather than warning on every line.

## 2026-08-12 — Split `main` (README-only) from `mia` (active development)

**Why:** Keep the repo's default branch minimal/presentable; all spec docs
and implementation work happen on `mia` instead.
**Alternatives considered:** Single branch with everything — simpler, but
mixes a landing-page README with in-progress design churn.

## 2026-08-12 — Join-trigger: local mic + Meet-tab detection, not calendar

**Why:** Calendar-based auto-join misses ad-hoc/calendar-less meetings and
never asks for confirmation before joining. Researched how Fireflies, Otter,
Fathom, Granola, and Wispr Flow's Notetaker detect meetings — the pattern
that handles calendar-less meetings is device-level mic activity combined
with detecting an active call, with calendar used only as optional
enrichment, never as the trigger.
**Alternatives considered:** Calendar-only polling with auto-join (original
design, replaced); mic activity alone (rejected — too many false positives
from dictation, Voice Memos, phone calls with no Meet tab open).

## 2026-08-12 — Five hardening fixes to the live-voice design

**Why:** Concrete, verified technical gaps in the original live-voice
architecture:
- Launch Chromium with `--use-fake-ui-for-media-stream` — no human is
  present to click the mic/camera permission prompt.
- Explicit voice-turn state manager to gate STT processing while TTS is
  playing — otherwise the bot can hear and react to its own synthesized
  voice looping back through the virtual mic.
- Fuzzy (Levenshtein) wake-word matching instead of exact string match —
  STT mishears accents/noise ("hay bot," "a bot").
- Audio device selection (BlackHole) done once manually inside Meet's own
  UI, persisted per Chromium profile — not scripted per run.
- Local VAD on raw audio frames for turn-taking, instead of waiting on
  STT's own endpointing/punctuation signal — meaningfully lower latency.

## 2026-08-12 — Pivot: live voice agent, not a transcript/summary pipeline

**Why:** Explicit user requirement — the transcript must never be the
primary source of information or a deliverable. Speech should only be used
transiently to detect a wake word and capture a command, with real Google
API calls (e.g. blocking calendar time) executed live, in the meeting, with
a spoken confirmation. Nothing meeting-related is written to disk
afterward.
**Alternatives considered:** The original design (scrape live captions,
summarize post-call via LLM, write to a Google Doc) — abandoned before
implementation once this requirement was stated.

## 2026-08-12 — Logging: Logfire instead of plain rotating file logs

**Why:** The pipeline is multi-stage and per-meeting; Logfire's
OpenTelemetry-based spans give a per-meeting/per-turn timeline (join
latency, wake-to-command latency, tool execution time) in a dashboard,
instead of grepping flat log lines. Free tier (10M spans/month) comfortably
covers solo use. Must never be a hard dependency — logging calls are
fire-and-forget so an unreachable Logfire backend can't block the live
voice loop.
**Alternatives considered:** Local rotating file handler (`logging` module)
— simpler, no external account, but no structured per-meeting tracing.

## 2026-08-12 — Runtime: local machine only

**Why:** Simplest to start; the bot only needs to run while meetings are
happening, not 24/7.
**Alternatives considered:** Always-on cloud VM — more reliable (works even
if the laptop is off), but adds hosting/ops cost and complexity not
justified yet.

## 2026-08-12 — Language: Python

**Why:** User preference; Playwright's Python bindings are fully supported,
and it keeps the LLM/summarization side (now voice-agent side) in the same
language.
**Alternatives considered:** TypeScript/Node.js — more meeting-bot reference
code exists in Node (Recall.ai, Attendee.dev), but not chosen.

## 2026-08-12 — Dedicated bot Google account, not the user's own account

**Why:** Makes it visually clear to other participants that a bot is
present; avoids session/login conflicts with the user's own normal Meet
usage.
**Alternatives considered:** Logging in as the user — simpler (no invite
step), but less transparent to other participants and can't coexist with
the user's own browser session.

## 2026-08-12 — Build the Meet-join mechanism in-house (Playwright)

**Why:** Deliberate choice to learn the mechanics directly and keep meeting
audio off third-party infrastructure, even though it means owning the
maintenance burden of Google changing the Meet UI or tightening bot
detection.
**Alternatives considered:** Commercial Meeting Bot API (Recall.ai,
MeetGeek) — least maintenance, but meeting audio flows through a third
party and costs per-minute. Self-hosted open source (Attendee.dev, Vexa) —
no third-party data flow and less maintenance than building from scratch,
but less learning value and less control.
