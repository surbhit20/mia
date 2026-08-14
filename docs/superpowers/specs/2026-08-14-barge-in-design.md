# Barge-In — Design

Date: 2026-08-14
Status: Approved, not yet implemented

## Context

Today, while mia is speaking (from the moment a command is captured through
the end of TTS playback), STT is fully gated off (`should_process_stt()` is
`LISTENING`-only) — she cannot hear anything, so there is no way to
interrupt or redirect her mid-response short of waiting for her to finish.
This adds the ability to interrupt her by saying the wake word again while
she's talking.

## Goal

"Hey Mia, block 30 minutes" → *"Blocked 30 minutes starting—"* → "**Hey
Mia**, make it an hour instead" → she stops mid-sentence, captures the new
command, and acts on it, exactly as if it were any other wake-word-triggered
command.

## Why wake-word-gated, not any-speech

Considered and rejected: letting any detected speech interrupt playback,
the way a private one-on-one voice assistant (e.g. an ElevenLabs
Conversational AI agent) can. That model works because the whole audio
stream in a 1:1 voice chat is understood to be directed at the agent. mia's
situation is the opposite: she is present in a real meeting where most of
what she hears is other people talking to each other, not to her. Any-speech
interruption would mean mia stops talking every time someone in the meeting
says anything while she happens to be mid-response — indistinguishable, from
her code's point of view, from someone actually trying to redirect her.
Wake-word-gating avoids this entirely and keeps one consistent rule across
the whole system: mia only ever reacts to speech that starts with her name,
whether that's a fresh command or an interruption of one already in
progress. This was confirmed explicitly rather than assumed, including
after directly considering the any-speech alternative and choosing the
consistent wake-word rule instead, even at some cost to conversational
fluidity.

## Design

**The core fix:** `start_speaking()` currently fires *before* the Claude
call and TTS synthesis even begin, so today's `SPEAKING` state actually
covers "processing" as well as "audio is playing." Barge-in only makes
sense once mia is audibly talking — you interrupt someone mid-sentence, not
while they're silently thinking. So `start_speaking()` moves to fire right
before playback actually begins (after Claude + TTS synthesis complete).
`COMMAND_CAPTURED` then precisely means "processing" and `SPEAKING`
precisely means "audio is playing" — a correction to what the states
represent, not just a change needed for this feature.

**State machine (`turn_state.py`):**
- `wake_word_detected()` becomes a valid transition from `SPEAKING` as well
  as `IDLE`/`LISTENING`, transitioning `SPEAKING` → `LISTENING`. That
  transition *is* the barge-in.
- `should_process_stt()` returns `True` for `LISTENING` **or** `SPEAKING`
  (today: `LISTENING`-only) — STT keeps flowing to Deepgram while mia
  talks, so the wake-word matcher can hear an interruption at all.
- `COMMAND_CAPTURED` (Claude dispatch + TTS synthesis) stays
  non-interruptible. Canceling an in-flight Claude API call is a
  materially larger feature on its own, and isn't needed for the actual
  goal here (interrupting mid-sentence, not mid-thought).

**Playback (`audio/injection.py`):** the single blocking
`inject_into_virtual_mic` is replaced with three small pieces:
- `start_playback(pcm_audio, device_name, sample_rate) -> None` —
  non-blocking (`sd.play(..., blocking=False)`; sounddevice is already
  asynchronous under the hood, so no new thread is needed).
- `is_playback_active() -> bool` — wraps `sd.get_stream()`.
- `stop_playback() -> None` — wraps `sd.stop()`.

**Turn-handling loop (`main.py`'s `_run_call_loop`, and
`demo_standalone.py`'s equivalent — both copies need the same change):**
- On command capture: dispatch to Claude + synthesize as today, still
  blocking, now happening in `COMMAND_CAPTURED`. Then `start_speaking()` +
  `start_playback()` — control returns to the main loop immediately
  instead of blocking until audio finishes.
- Every loop iteration: if `SPEAKING` and playback has finished naturally
  (`not is_playback_active()`), call `finish_speaking()`. This replaces
  today's `finally: finish_speaking()`, which ran immediately after the
  old blocking play call.
- Inside `on_transcript`'s existing wake-word branch: call
  `stop_playback()` unconditionally (a harmless no-op if nothing is
  currently playing), alongside the existing `wake_word_detected()` +
  `command_buffer.start()` calls. This is what actually cuts mia off the
  instant a barge-in wake word is heard — no other new logic needed here,
  since the existing wake-word-handling code path is reused as-is once the
  state-machine transition above makes it reachable from `SPEAKING`.

## What happens to the interrupted response

Nothing needs undoing. By the time playback starts, `dispatch_command` has
already fully run and already recorded the exchange in `ConversationHistory`
(the earlier conversational-memory feature). Barge-in only cuts off how
long mia keeps talking about it — not the underlying action (a calendar
event already booked, an email search already run) and not the memory of
it having happened.

## Error handling

No new failure modes beyond what already exists: `stop_playback()` /
`is_playback_active()` are thin wrappers over `sounddevice` globals with no
new exception surface. If `start_playback()` itself fails (e.g. the output
device disappears), that already propagates through the existing
try/except around the turn-handling block, same as today's blocking
`inject_into_virtual_mic` failures do.

## Testing

- `turn_state.py`'s changes are pure state-transition logic, fully unit
  testable: `wake_word_detected()` from `SPEAKING` moves to `LISTENING`;
  `should_process_stt()` returns `True` in both `LISTENING` and `SPEAKING`,
  `False` elsewhere; `COMMAND_CAPTURED` remains unreachable from a wake
  word.
- `audio/injection.py`'s new functions are live-hardware-only, consistent
  with the rest of this file (no automated test, verified live).
- The loop restructuring in `main.py`/`demo_standalone.py` has no
  dedicated automated test, consistent with the existing pattern for this
  integration code — verified live: interrupting mid-response and
  confirming the new command is captured and acted on, and confirming
  normal (non-interrupted) responses still transition to `COOLDOWN`
  correctly.

## Explicit scope

**In:** wake-word-gated barge-in during `SPEAKING` (audio playback) only,
the `turn_state`/`injection`/loop changes described above.

**Out:** any-speech interruption (rejected above); interrupting during
`COMMAND_CAPTURED` (Claude/TTS processing); canceling an in-flight Claude
API call; anything related to the not-yet-built Attendee/live-Meet
integration (this design is scoped to the currently-implemented
`sounddevice`-based playback path used by both `main.py`'s BlackHole
output and `demo_standalone.py`'s default-device output — if the Attendee
integration is built later, its realtime-audio output would need an
equivalent stop/interrupt call, but that's future work).
