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
