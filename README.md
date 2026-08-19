# mia

A voice agent that joins your Google Meet calls as a live participant,
listens for a wake word, and acts immediately on spoken commands by calling
real Google APIs. When the meeting ends she writes a summary to a Google
Doc and emails it to you, with the things she did during the call already
ticked off.

Say "Hey Mia" and ask her to block time, look up your schedule, cancel or
move a meeting, or search your inbox — she confirms out loud once it's
done.

The meeting transcript is held in memory for the length of the call and
discarded once the summary is written. What persists is the summary, never
the record of who said what.

## Status

The real Meet-join path runs through [Recall.ai](https://www.recall.ai)'s
cloud meeting-bot API: Recall's bot joins the call and streams audio to a
local websocket bridge that `mia` exposes publicly via an ngrok reserved
domain. It needs a Recall API key and that reserved domain, but no Google
Workspace account. There's also a standalone demo (`demo_standalone.py`),
which runs the exact same pipeline against your machine's own microphone
and speakers instead of joining a call.

## What she can do

- **`block_calendar_slot`** — create a calendar event ("block 30 minutes
  for focus time")
- **`find_calendar_events`** — look up what's on your calendar, or check
  if you're free at a given time
- **`cancel_calendar_event`** — cancel an event by the time it's at
  ("cancel my 4pm")
- **`update_calendar_event`** — move, resize, rename, or re-describe an
  existing event ("move my 4pm to 3", "make my 4pm an hour")
- **`find_gmail_messages`** — search your inbox and get a spoken summary

After the call she also produces a **meeting summary**: a Google Doc with
a written summary and a single Action Items checklist, emailed to you with
the Doc attached as a PDF. Items she carried out herself are already
ticked; everything else is left open. Only tools that actually ran and
succeeded can be ticked — completion is never inferred from what was said.

Every command is gated on the wake word, and a short rolling conversational
memory lets follow-up questions and disambiguation ("which one did you
mean?") resolve naturally. Saying the wake word while she is speaking does
start a new command, but it cannot cut her off mid-sentence: Recall has no
interrupt API, so a response plays to completion once sent. Her spoken
confirmations are short by design, which is what makes that acceptable.

## How it works

Local voice activity detection (silero-vad) feeds Deepgram's streaming
speech-to-text, whose transcripts are fuzzy-matched against the wake word.
Once triggered, the following speech is buffered as a command and sent to
Claude with tool definitions for everything above; Claude picks a tool,
mia executes it against the real Google API, and the result is spoken back
via ElevenLabs TTS. An explicit turn-state machine
(idle → listening → command-captured → speaking → cooldown) keeps this
predictable and prevents mia from reacting to her own voice.

Separately, Recall streams a diarized transcript over the same websocket.
It accumulates in memory with per-speaker attribution and, once the bot has
left the call, is summarized by Claude into the Doc. Leaving happens first
on purpose — Recall bills for time in the call, and summarizing takes
several seconds.

## Setup

See [SETUP.md](SETUP.md) for the full one-time setup (audio routing,
Chrome profile, OAuth, environment variables).

Quick start once set up:

```sh
pip install -e ".[dev]"
cp .env.example .env   # fill in your API keys
python demo_standalone.py
```

## Development

```sh
pip install -e ".[dev]"
pytest
```

Every non-obvious implementation decision is logged in
[decisions.md](decisions.md) as it's made. Design specs and implementation
plans for each feature live under `docs/superpowers/`.
