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

## 2026-08-18 — Resolved: Recall does not echo the bot's own audio back to us

**Why it was open:** the transcript path shipped with no self-echo filter,
and the spec recorded it as unverified: if Recall's mixed stream carried mia's
own spoken confirmations, they would land in the transcript as a participant
and be summarized as meeting discussion.

**Resolved by observation, not argument.** Across three live meetings, none of
mia's spoken phrases ("On it", her confirmations, her fallback replies) appear
anywhere in the generated summaries, and no transcript line ever attributed
her own words to a speaker. Recall excludes the bot's output from the stream
it sends back.

**A related intuition that does NOT explain it:** using headphones. mia's
audio is injected by Recall's bot directly into the meeting and never plays
through the user's speakers, so it cannot loop through the user's microphone
whatever they are wearing. Acoustic echo and stream echo are different
mechanisms, and only the second was ever in question here.

**Kept anyway:** `is_self_echo` still guards the STT path. It costs nothing on
the Recall path and remains load-bearing for `demo_standalone.py`, where
BlackHole routes injected audio back into capture by design.

## 2026-08-18 — Action Items may be ticked only from executed tool calls, never inferred

**Why:** The summary doc's checklist marks items mia completed. The obvious
implementation — let the model read the transcript and decide what got done
— produces confident false claims about the user's real calendar. So
completion is sourced from `ToolCallResult`s mia actually executed, passed to
the summarizer as ground truth, and the prompt forbids inferring completion
from the transcript. The same list also deduplicates: a commitment both
discussed and executed appears once, ticked, not twice.
**What this cost to get right:** three separate review rounds each found a
different way for a non-completion to reach the "ground truth" list.
`tool_name is None` (mia spoke but ran no tool — including clarifying
questions). Then a handler that *raised* still returned a `tool_name`, so a
booking that failed on a Google error would have been ticked "done by Mia".
Then read-only lookups (`find_calendar_events`, `find_gmail_messages`), which
complete nothing. `ToolCallResult` now carries `succeeded` and `mutated`, and
`Tool` carries `mutates`; all three must hold for an item to tick.
**Alternatives considered:** string-matching the "Sorry, that didn't work"
sentinel to detect failure — rejected as fragile. An allowlist of mutating
tool names inside `summary.py` — rejected because it silently drifts whenever
a tool is added.

## 2026-08-18 — Reversal: mia now produces a post-meeting summary doc

**Why:** Directly reverses "2026-08-12 — Pivot: live voice agent, not a
transcript/summary pipeline", and relaxes the "no transcript ever persisted"
principle. The reversal is bounded: the transcript streams over the websocket
bridge already in place, accumulates in memory, and is discarded once the
summary is written. What persists is the summary, in a Google Doc, never the
record of who said what. Requested after the live-voice path was working, on
the grounds that a meeting with no artifact wastes what mia already heard.
**Alternatives considered:** Recall's post-meeting async transcription with
"perfect diarization" — more accurate speaker labels, but it leaves a
recording and transcript at rest on Recall's servers and needs polling.
Local markdown file instead of a Doc — simplest and needs no new OAuth scope,
but not shareable. Emailing the summary — rejected; it would escalate the
Gmail scope from read-only to send-as-the-user.
**Notable sub-decisions:** speaker labels fall back to a sequential
`Speaker N` numbered by first appearance, never the raw Recall participant id
(an opaque integer that renders as "Speaker 847293"), and never one shared
"Unknown speaker" bucket — a single shared label reads to the summarizing
model as one person saying everything. Calendar attendees are passed as
context only and never mapped onto a participant id: nothing links them, and
a confident misattribution is worse than an anonymous speaker.

## 2026-08-17 — Reversal: replaced self-hosted Attendee.dev with Recall.ai

**Why:** Reverses both "2026-08-12 — Build the Meet-join mechanism in-house
(Playwright)" and the 2026-08-13 pivot to self-hosted Attendee, and abandons
the founding constraint that meeting audio never leaves local control.
Anonymous-guest joins through Attendee worked, then degraded over a single
day of testing as Google's anti-abuse detection began auto-denying join
requests — an inherent property of anonymous meeting-bot joins, not a defect
in our code or Attendee's. Reliability was chosen over the privacy stance
deliberately and explicitly.
**Alternatives considered:** finishing the signed-in-bot/SAML path, which
stays fully local — rejected after the Workspace trial, an auto-suspended bot
account, and tunnel churn had already consumed days. Continuing with
anonymous joins — rejected as unfixable from our side.
**Consequences:** meeting audio now flows through Recall's cloud and is
billed per minute. Recall's bot dials out to a websocket we host, so the
bridge needs a real public `wss://` URL — a paid ngrok reserved domain, since
the free `.ngrok-free.dev` tier is blocked on this network. Barge-in is lost:
Recall has no interrupt/stop-audio API, so once a response is POSTed it plays
to completion.

## 2026-08-17 — Audio frames must be paced by real time, not by a network buffer

**Why:** Non-obvious consequence of the Recall migration, and the root cause
of mia responding to roughly one wake word in seven. `BlackHoleCapture` read a
hardware input stream, which is paced by the sound card and physically cannot
return a frame before that frame's audio has elapsed. `RecallAudioBridge`
reads a network buffer, which has no such pacing. With the timeout derived
from the frame size (32ms frame → 64ms) and Recall delivering ~200ms batches,
the buffer ran dry between every batch and `pull()` substituted silence —
measured at 44% of all frames during continuous speech, injected *into the
middle* of live sentences on their way to Deepgram.
**Second-order effect:** command capture ended on a count of consecutive
non-speech *frames*, which only equals a duration if frames arrive at real
time. Buffered audio draining at memory speed fired it early; a starved
stream fired it late. Now measured in wall-clock seconds.
**Alternatives considered:** raising the frame-derived multiplier — rejected;
it hides the coupling rather than removing it. The starvation timeout is now
an explicit constant tied to Recall's delivery cadence, and padding is what it
should be: an emergency fallback for a dead stream, not routine behavior.

## 2026-08-14 — Investigated: barge-in "not working" in the demo was acoustics, not code

**Why this matters:** After the Deepgram keepalive fix, barge-in still
appeared completely non-functional live-testing `demo_standalone.py`.
Root-caused via the same process (added temporary `print()` diagnostics
showing every `SPEAKING`-time transcript and its `is_self_echo` verdict),
rather than assuming the self-echo filter (previous entry below) was too
aggressive. The evidence: every transcript captured during a response was a
near-verbatim echo of mia's *own* words, correctly flagged
`self_echo=True` -- and the user's actual barge-in attempt never appeared
in the transcript stream at all. Conclusion: on a laptop with built-in
mic + built-in speakers and no physical separation, mia's own voice
through the speakers is loud/close enough to the mic to drown out a human
trying to talk over her -- the audio never reaches Deepgram clearly enough
to transcribe, so there's nothing for the self-echo filter or the wake-word
matcher to even evaluate. Confirmed by retrying with headphones on for
output: barge-in worked correctly on the first attempt.
**Not a code bug** -- the state machine, self-echo filter, and barge-in
transitions were all already correct (per the earlier task reviews). This
is a `demo_standalone.py`-specific testing-setup limitation, documented in
its module docstring. The real Meet path (via BlackHole, once built) won't
have this problem: audio arrives as a clean digital mix through Meet's own
pipeline, not a physical mic/speaker competition, so no headphones-style
workaround should be needed there.
**Cleanup:** removed the temporary debug `print()` statements added for
this investigation (both this one and the earlier `stt.py` CLOSE/ERROR
event prints) once root cause was confirmed, per the debugging process --
the `repr(event)` improvement in `safe_log`'s structured fields was kept.

## 2026-08-14 — Fix: Deepgram's own idle-timeout was structurally guaranteed on slow turns

**Why:** Live-debugged (systematic-debugging process, not guessed): after
barge-in shipped, a long Gmail-search turn reliably disconnected Deepgram
mid-turn. Added direct `print()`/`repr()` logging of the raw CLOSE/ERROR
event (Logfire's console handler only prints the log message, not
structured fields — the dashboard has them, but that's a slower loop than
seeing it in the terminal). The real event confirmed the root cause exactly:
`ConnectionClosedError(code=1011, reason='Deepgram did not receive audio
data or a text message within the timeout window')`, firing ~5.4s after
"command dispatched" — i.e. mid-`synthesize()`, before playback even
started. `send_keepalive_if_idle()` was only ever called from the *caller's
own loop*, which cannot iterate again until the fully-synchronous
Claude-dispatch + TTS-synthesis sequence (`COMMAND_CAPTURED`) finishes. So
this wasn't a probabilistic risk — any turn whose Claude+TTS work took
longer than Deepgram's real idle timeout (observed ~5s, not the ~10s
originally assumed) was **guaranteed** to disconnect. The Gmail tool's extra
internal Claude call plus longer spoken summaries made it easy to hit
reliably, which is what surfaced it.
**Fix:** `StreamingSTT` now runs its own background keepalive thread
(started in `start()`, stopped in `stop()`) calling
`send_keepalive_if_idle()` on a fixed wall-clock interval
(`_KEEPALIVE_IDLE_SECONDS`), independent of whatever the caller's thread is
doing. This supersedes the entry below (2026-08-14, "mia went permanently
deaf when Deepgram's idle socket died mid-turn") — that fix made a *dead*
connection recoverable; this fix stops the most common cause of death from
happening at all during a slow turn. The `_reconnect_if_needed()` recovery
path stays as a second line of defense for disconnects from other causes
(network blips, etc.).
**Alternatives considered:** making `dispatch_command`/`synthesize` run
non-blocking (e.g. on a background thread) so the main loop itself could
keep iterating during a turn — a materially bigger architectural change
(introduces concurrency into the Claude/TTS pipeline) for no benefit beyond
what a dedicated keepalive thread already solves directly.

## 2026-08-14 — Fix: mia went permanently deaf when Deepgram's idle socket died mid-turn

**Why:** Confirmed by live testing (the Gmail search feature made this easy
to hit): a voice turn involving two Claude calls (tool selection plus the
Gmail tool's own internal summarization call) and a longer spoken response
runs long enough for Deepgram's ~10s idle-connection timeout to fire while
the main loop is blocked mid-turn — the loop only sends keepalives once per
iteration, and it isn't iterating during a blocking turn. This was already
a known, documented gap (see the accepted-limitation entry below), but
"accepted" turned out to mean "the bot goes fully and permanently deaf until
the process is restarted" once actually observed live — not survivable for
a real running assistant. `StreamingSTT.send_frame()` and
`.send_keepalive_if_idle()` now call a new `_reconnect_if_needed()` first:
if the socket is dead, attempt `stop()` + `start()` again, backed off to at
most once every 2 seconds so a genuinely unreachable Deepgram doesn't get
hammered every ~32ms from the audio loop. A failed reconnect attempt is
caught and logged, not raised, so it degrades back to "still deaf, will
retry" rather than crashing the call loop.
**Does not fix:** the root cause (keepalives can't fire during a blocking
turn) — that's still true, and a long enough turn will still disconnect the
socket. What changes is the failure mode: a brief gap in transcription
(bounded by the reconnect handshake time) instead of permanent deafness.
**Alternatives considered:** sending keepalives from a background timer
thread during the blocking turn, so the disconnect never happens in the
first place — more invasive (a second thread touching the same connection
object `send_frame`/`send_keepalive_if_idle` already touch, needing its own
locking), and the reconnect approach fixes the actually-observed failure
mode (permanent deafness) with a much smaller, more contained change.
Worth revisiting if disconnects turn out to happen often enough in practice
that even a bounded reconnect gap is disruptive.

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

## 2026-08-17 — AttendeeClient integration: live trial findings (wss:// required, container OOM)

**Finding 1 — Attendee's API hard-rejects `ws://`, requires `wss://`.**
Confirmed live: `create_bot` with `websocket_settings.audio.url` starting
with `ws://` returns a 400 (`bots/serializers.py`'s request validation
unconditionally requires `wss://`, no dev-mode bypass, no exception for
`localhost`/`host.docker.internal`). This meant the bot-join path as
originally shipped in the AttendeeClient PR would have failed on every
real invocation. Fixed: `AttendeeAudioBridge` now optionally terminates
TLS with a self-signed cert (`src/mia/audio/tls_cert.py`, generated once
via `openssl` and reused, stored at `~/.mia/attendee-bridge-tls/`), and
`main.py` builds a `wss://` URL and passes the cert/key.

**Finding 2 — Attendee's own websocket client uses default (strict) TLS
verification, so the Docker container running it must be told to trust
our self-signed cert.** Read `bots/bot_controller/bot_websocket_client.py`
directly: it calls `websockets.sync.client.connect(url)` with no SSL
override. A self-signed cert is rejected by Python's default CA trust
store unless added to it. This is a **local-environment setup step, not
something mia's own code can do from inside its own process** — it lives
outside this repo, in whichever machine runs the self-hosted Attendee
Docker Compose stack:

```bash
docker cp ~/.mia/attendee-bridge-tls/cert.pem attendee-attendee-worker-local-1:/usr/local/share/ca-certificates/mia-bridge.crt
docker exec --user root attendee-attendee-worker-local-1 update-ca-certificates
```

This is exec'd into the running container, so it does **not** survive a
container recreate (`docker compose up --force-recreate` or a fresh
`docker compose up` after removing the container) — it needs to be
re-run after that. Verified live, twice: once via a direct Python
`websockets.sync.client.connect()` call from inside the worker container
(mirroring Attendee's exact connection code) against a throwaway test
server, and once end-to-end with a real bot joining a real Meet call —
the worker log showed `BotWebsocketClient websocket connected` and live
`SilenceStatus` audio-volume messages flowing through mia's bridge.

**Finding 3 — the self-hosted Attendee Docker stack is genuinely resource-
constrained on this machine and can silently die mid-session.** During
the live trial, the bot's actual browser session (headless Chrome + a
screen-recording ffmpeg process) stopped producing any log output ~30s
after successfully joining, with the participant disappearing from the
real Meet call's UI -- but Attendee's own `Bot.state` stayed stuck at
`joined_recording` and neither raised an error nor updated. `docker
inspect`'s `.State.OOMKilled` was `true` for the worker container, and
`docker stats` showed it sitting at ~75% of its memory limit even at
rest. Root cause: this Mac is Apple Silicon, and Attendee's Docker image
is `linux/amd64` -- so the whole stack (headless Chrome, video capture,
audio pipeline) runs under x86 emulation, which is memory- and CPU-heavy
enough to hit the container's memory ceiling under real load. **This is
an environment/infrastructure limitation of self-hosting Attendee on this
specific machine, not a defect in the AttendeeClient integration code** --
the actual scope of the trial (bot join, TLS-secured realtime-audio
websocket connection, live audio streaming through the bridge) was fully
verified working before the OOM kill. Not yet resolved; a real fix would
mean raising the container's memory limit (if resources allow) or moving
self-hosted Attendee to a native-architecture host.

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
