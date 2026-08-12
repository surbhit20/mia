# Meet Live Voice Agent — Design

Date: 2026-08-12
Status: Approved, not yet implemented

Supersedes: `2026-08-12-meet-notetaker-bot-design.md` (transcript/summary
approach, abandoned before implementation — see "Why not a transcript
pipeline" below).

## Context

This is sub-project 1 of a larger idea: a Google-native personal assistant
that joins meetings, reads mail, and manages calendar/docs on the user's
behalf. The full idea spans several independent subsystems (Meet bot, Gmail
automation, Calendar booking, Drive/Docs automation). This spec covers only
the Meet-joining/live-voice piece — the hardest and most technically
uncertain part — so it's being built and validated first. Gmail triage and
broader calendar-booking automation beyond the live "block this slot"
command are deferred to their own future specs.

## Goal

A bot that joins your Google Meet calls as a live participant, listens
continuously, and — when addressed with a wake word — acts immediately on
spoken commands (e.g. "Hey Bot, block this time slot") by calling real
Google APIs (Calendar, and other tools later), then confirms out loud in
the meeting. This is a live agent, not a notetaker: it has no transcript or
summary as a deliverable, and nothing meeting-related is written to disk
after the fact.

## Why not a transcript pipeline

An earlier draft of this spec scraped Google's live captions and ran a
post-call LLM summarization pass over the transcript. That's explicitly not
what's wanted: the transcript should never be the primary source of
information or a deliverable. Speech is only ever used transiently, to
detect a wake word and capture the command that follows — nothing is
persisted to disk, and there is no summarization step.

## Feasibility findings (carried over, still apply)

- Google has no general-purpose API for a program to join a Meet call live.
  The Meet Media API exists but is in developer preview (as of April 2026)
  and requires every participant to be enrolled in the preview — not usable
  for joining arbitrary meetings.
- The Google Meet REST API can fetch recordings/transcripts *after* a
  meeting, but only on Google Workspace (Business Standard+/Enterprise) with
  recording/transcription explicitly enabled. The target account here is
  personal Gmail, so this path isn't available regardless.
- The standard approach for joining live (used by commercial meeting-bot
  vendors and open-source projects alike) is browser automation: a real
  Chromium instance joins the call as a participant. This spec builds that
  join mechanism in-house rather than using a third-party API or
  self-hosted project, to keep meeting audio off third-party infrastructure
  and learn the mechanics directly.

## Architecture

```
Mic activity detected (CoreAudio: mic in use, system-wide, any app)
  AND an active Meet call tab is open (AppleScript/System Events scan of
      Chrome tabs for meet.google.com/xxx-xxxx-xxx, not the homepage)
  -> optional Calendar API lookup (event within +/-10 min of now with a
     Meet link) to enrich the prompt with a real title
  -> macOS desktop notification: "Join '<title or generic>'?" [Join / Skip]
  -> on Join: Join Worker launches, joins that specific Meet URL

Meet call audio (captured via BlackHole virtual audio device)
  -> local VAD (per ~20-30ms frame) — detects speech start/end
  -> streaming STT (Deepgram) — continuous, transcribes to text
  -> wake-word matcher — fuzzy match (Levenshtein) against live STT text
  -> on trigger: buffer the utterance, VAD silence marks end-of-command
  -> command text -> Claude (tool-calling) picks a tool + args
  -> tool executes (MVP: Calendar API — block/create event)
  -> confirmation text generated
  -> TTS (ElevenLabs) synthesizes it to audio
  -> injected into a virtual microphone device -> heard by everyone in the call

Meet tab closes, or mic-in-use-by-Chrome signal drops
  -> leave signal -> bot leaves the call
```

### Components

1. **Mic Activity Monitor** — polls macOS CoreAudio's
   `kAudioDevicePropertyDeviceIsRunningSomewhere` on the default input
   device. This is a status flag, not audio capture, so it needs no
   microphone permission of its own. Fires on *any* app using the mic
   (dictation, Voice Memos, a phone call, a Meet call) — on its own it's
   only a candidate signal, never enough by itself to prompt.
2. **Meet Tab Detector** — queries Chrome via AppleScript/System Events for
   a tab whose URL matches an active Meet call
   (`meet.google.com/xxx-xxxx-xxx`, excluding the homepage/pre-join lobby
   pages). Combined with mic activity, this is what actually fires the join
   prompt — mic activity alone is not enough to distinguish a Meet call
   from unrelated mic use.
3. **Calendar Enricher** — on trigger, looks up the Calendar API for an
   event within +/-10 minutes of now with a Meet link. Purely optional
   enrichment: if found, its title is used in the notification; if not
   found (ad-hoc, calendar-less meeting), the notification falls back to a
   generic prompt using the URL scraped directly from the tab. Calendar
   access is never required for the trigger to fire.
4. **Join Prompt** — a macOS desktop notification with Join/Skip actions.
   Nothing joins automatically; the bot only joins when this notification is
   explicitly accepted. A notification left unactioned for 2 minutes is
   treated as Skip.
5. **Join Worker (Playwright)** — launched when the Join Prompt is
   accepted; launches a persistent, pre-authenticated Chromium profile as a
   dedicated bot Google account (not the user's own account) and joins the
   specific Meet URL from the trigger. Launched with
   `--use-fake-ui-for-media-stream` so the mic/camera permission prompt is
   auto-accepted — there's no human present to click "Allow," and without
   this flag the bot joins deaf/mute or stalls indefinitely.
6. **Leave Detector** — symmetric to the join trigger: when the Meet tab
   closes, or the mic-in-use-by-Chrome signal drops, that's treated as the
   call having ended. If the bot is in the call, it leaves. This also
   removes the need for a scheduled end time on ad-hoc meetings that have
   no calendar entry.
7. **Audio Capture** — reads meeting audio from the BlackHole virtual
   device.
8. **Local VAD** — Silero VAD or WebRTC VAD, run directly on raw ~20-30ms
   audio frames in parallel with STT. Used purely for turn-taking timing
   (detecting when someone has stopped speaking), not for transcription
   content — this keeps end-of-command detection sub-second instead of
   waiting on cloud STT endpointing/punctuation signals.
9. **Streaming STT** — Deepgram, continuous, transcribes captured audio to
   text in real time. Output is transient — used only by the wake-word
   matcher and command buffer, never persisted.
10. **Wake-word matcher** — fuzzy-matches (Levenshtein distance, threshold
    tuned during live testing) the live STT text against the wake phrase, to
    tolerate STT mishearing accents/noise ("hay bot," "a bot," etc.) that an
    exact string match would miss.
11. **Command buffer** — once the wake word triggers, buffers the following
    utterance's STT text as the command; VAD silence detection (not STT
    endpointing) marks the end of the command.
12. **Tool dispatcher** — a registry of `{name, schema, handler}` tools;
    Claude is given the registry and selects a tool via tool-calling. MVP
    registers exactly one tool (`block_calendar_slot` → Calendar API), but
    the dispatcher is generic so new tools (email, docs, etc.) register the
    same way later without changing the core loop.
13. **Voice-turn state manager** — gates the pipeline through
    `IDLE -> LISTENING -> COMMAND_CAPTURED -> SPEAKING -> (cooldown) -> LISTENING`.
    While in `SPEAKING` and for a short cooldown after, incoming STT text is
    ignored by the wake-word matcher and command buffer. This prevents the
    bot from hearing its own synthesized voice (played into the call via the
    virtual mic, captured back via BlackHole) and mistaking it for a new
    wake word or command. The STT connection itself stays open throughout
    (avoids reconnect latency) — only downstream processing is gated.
14. **TTS + audio injection** — ElevenLabs synthesizes the confirmation
    text; the audio is played into the virtual microphone device feeding
    the bot's Chromium profile, so it's heard by all meeting participants.

### Auth

- The bot's dedicated Google account is logged into the persistent Chromium
  profile once, manually, ahead of time. Playwright reuses that profile's
  session/cookies on every run — no scripted login flow, which avoids
  triggering Google's automated-login detection.
- In that same one-time manual setup session, the bot's audio devices
  (BlackHole/aggregate device) are explicitly selected as microphone and
  speaker inside Meet's own in-call device settings. Meet remembers this
  selection per Chromium profile going forward, so it doesn't need to be
  scripted on every run.
- Calendar access for the bot account uses OAuth user credentials (not a
  service account), with the refresh token stored locally. This is used only
  for the Calendar Enricher's optional title lookup at detection time — it
  is never required for the join trigger to work.

## macOS setup & permissions

Two one-time, unscriptable setup requirements:

**Audio routing** — requires BlackHole (free virtual audio driver) plus a
Multi-Output/Aggregate Device configured in Audio MIDI Setup. Audio MIDI
Setup device routing isn't exposed via a public CLI, so the deliverable
includes:

- `setup_audio.sh` — installs BlackHole via Homebrew, verifies it's present.
- `SETUP.md` — step-by-step manual instructions for the one-time Audio MIDI
  Setup routing, and for the one-time Meet in-call device selection
  described under Auth above. Explicitly warns: if the Mac's *system-wide*
  default output is set to the Multi-Output Device, every system sound
  (Slack pings, email notifications, etc.) leaks into the meeting.
  Recommendation: use per-app output routing if the macOS version supports
  it (Sonoma+), otherwise mute other notification sounds while the bot is
  running.
- A startup check in the bot that verifies the expected virtual devices
  exist on the system and fails fast with a message pointing to `SETUP.md`
  if not — rather than silently joining with no working audio.

**Automation permission** — the Meet Tab Detector needs macOS's Automation
permission (System Events controlling Chrome) to read tab URLs via
AppleScript. Granted via a one-time system prompt on first run (or
pre-granted through System Settings -> Privacy & Security -> Automation).
`SETUP.md` documents this alongside the audio routing steps, and the startup
check verifies it (a probe AppleScript call) rather than letting detection
silently never fire.

## Error handling

- **Mic+tab trigger fires but no calendar match** — not a failure; the
  notification is shown with a generic title, using the URL scraped
  directly from the tab (handles ad-hoc, calendar-less meetings).
- **Automation permission not granted** (can't query Chrome tabs) — fail
  fast at startup with a message pointing to `SETUP.md`, rather than
  silently never detecting anything.
- **Notification unactioned for 2 minutes** — treated as Skip; no stale
  prompt lingers.
- **Can't join** (not admitted, bad/expired link, meeting cancelled) — log
  and skip; detection keeps running for future triggers.
- **Wake word false-triggers** on normal conversation, and no valid tool
  match follows within the command window — bot stays silent, no action
  taken, no false confirmation spoken.
- **STT mishears / command doesn't map to a valid tool** — bot speaks a
  short "didn't catch that," rather than guessing or silently failing.
- **Tool execution fails** (e.g. Calendar API error) — bot speaks a short
  failure notice rather than staying silent.
- **Audio device missing/misconfigured at startup** — fail fast with a
  message pointing to `SETUP.md`; don't join the meeting deaf/mute.
- **Repeated trigger for the same ongoing call** — mic/tab signals are
  polled continuously, so the same active Meet tab could re-trigger
  detection logic repeatedly. A local state file tracks the currently
  open/prompted/joined meeting (by tab URL) so the same call doesn't
  produce duplicate notifications or duplicate joins, and survives a
  process restart mid-call.

## Logging

Logfire (Pydantic's OpenTelemetry-based observability platform), chosen
over plain file logging for the per-meeting, multi-turn nature of this
pipeline.

- `logfire.configure()` at process startup.
- One span per meeting (detect -> prompt -> join -> leave), with nested
  **voice-turn spans**: wake-word detected -> command captured -> tool
  executed -> response spoken. This gives a per-turn timeline in the
  dashboard (detection-to-prompt latency, wake-to-command latency, tool
  execution time, response latency) rather than flat log lines.
- Trigger events that *don't* result in a meeting (mic activity with no
  Meet tab, or a Skipped notification) are still logged at INFO, outside
  any meeting span, so false-positive/skip rates are visible over time.
- Logfire's HTTP auto-instrumentation captures outbound calls to Deepgram,
  Claude, ElevenLabs, and the Calendar API as nested spans automatically.
- Console output kept alongside Logfire's cloud backend, for live tailing
  without opening the dashboard.
- **Logfire must never be a hard dependency for correctness.** If it's
  unreachable, logging calls are fire-and-forget and must not block or fail
  the live voice loop.
- Free Logfire Personal tier (10M spans/logs per month, no card required)
  is expected to comfortably cover solo use.
- Setup: create a free Logfire account, generate a write token, store it
  alongside the bot's other local credentials.

## Testing

- The trigger-combination logic (mic activity + tab detection + optional
  calendar match -> prompt decision) is unit-testable by mocking all three
  signal sources independently — covers the no-calendar-match fallback and
  the "mic active but no Meet tab" no-op case without needing a live call.
- Wake-word fuzzy-matching and tool-dispatch logic are unit-testable
  against recorded/synthetic STT transcripts (no live dependency).
- The Calendar tool handler is unit-testable directly against the Calendar
  API (create/verify/delete a test event).
- VAD turn-taking logic is unit-testable against recorded audio fixtures
  with known speech/silence boundaries.
- Live-only validation (CoreAudio mic signal, AppleScript tab detection,
  notification delivery, join, audio routing, and the full voice loop
  including self-echo gating) can only be validated against the real OS and
  a real call:
  1. Manual solo test call first — verify detection fires correctly (and
     doesn't fire on non-Meet mic use), then that join, wake word, command
     execution, and spoken confirmation work end to end, and that the bot
     doesn't trigger on its own voice.
  2. Dogfood on a real, low-stakes meeting before relying on it.

## Scope

**In:** local mic-activity + Meet-tab detection gated by an explicit
Join/Skip notification (no blind auto-join, works with or without a
calendar entry), symmetric leave detection, live audio capture, local VAD,
streaming STT (transient use only), fuzzy wake-word activation, self-echo
gating via voice-turn state, extensible tool-calling dispatch,
calendar-blocking as the first tool, spoken confirmation in-meeting.

**Out for now:** any tool besides calendar blocking (dispatcher supports
adding them, but none else implemented), any transcript/notes/summary
output, always-listening mode without a wake word, multi-user support,
running on anything other than the user's local machine, detecting meetings
on platforms other than Google Meet (Zoom/Teams/FaceTime tab detection
would follow the same pattern but isn't built).

## Open questions for future sub-projects

- How the bot gets admitted into calls once it joins — the dedicated bot
  account will still hit a waiting room unless the user manually admits it
  or the meeting's "quick access" settings allow it in. Worth confirming
  during live testing rather than assuming.
- Whether/how this integrates with the Gmail-triage and broader
  calendar-booking sub-projects once they're designed.
- Whether additional tools (beyond calendar blocking) get designed as
  their own specs or folded into this one as the dispatcher grows.
