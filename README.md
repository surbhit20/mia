# mia

A voice agent that joins your Google Meet calls as a live participant,
listens for a wake word, and acts immediately on spoken commands by calling
real Google APIs. It's not a notetaker — there's no transcript or summary
deliverable, and no meeting audio or transcript content is ever persisted
to disk. Speech is only ever used transiently to detect the wake word and
capture the command that follows.

Say "Hey Mia" and ask her to block time, look up your schedule, cancel or
move a meeting, or search your inbox — she confirms out loud once it's
done.

## Status

The real Meet-join path (a signed-in bot via [Attendee.dev](https://github.com/attendee-labs/attendee))
is currently blocked on Google Workspace account access. The actively
tested and working path today is the standalone demo
(`demo_standalone.py`), which runs the exact same pipeline against your
machine's own microphone and speakers instead of joining a call.

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

Every command is gated on the wake word — including mid-response, so you
can interrupt her while she's still talking (barge-in) — and a short
rolling conversational memory lets follow-up questions and disambiguation
("which one did you mean?") resolve naturally.

## How it works

Local voice activity detection (silero-vad) feeds Deepgram's streaming
speech-to-text, whose transcripts are fuzzy-matched against the wake word.
Once triggered, the following speech is buffered as a command and sent to
Claude with tool definitions for everything above; Claude picks a tool,
mia executes it against the real Google API, and the result is spoken back
via ElevenLabs TTS. An explicit turn-state machine
(idle → listening → command-captured → speaking → cooldown) keeps this
predictable and prevents mia from reacting to her own voice.

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
