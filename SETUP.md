# One-time setup (macOS)

Run `./setup_audio.sh` first, then complete these manual steps — none of
this can be scripted.

## 0. Install `mia`

```sh
pip install -e ".[dev]"     # runtime + test dependencies
cp .env.example .env        # then fill in your keys (see step 4)
```

`mia` runs as a long-lived foreground process:

```sh
python -m mia.main
```

## 1. Audio MIDI Setup routing and Chromium bot account login (obsolete)

**No longer needed.** These steps applied only to the old Playwright/
BlackHole-driven Meet-join path (a `JoinWorker` browser instance, routed
through a BlackHole 2ch multi-output device, using a dedicated signed-in
Chrome profile for device selection). That path has been replaced by the
Attendee integration: Attendee's bot joins the call and handles its own
audio routing, so there is no BlackHole multi-output device to configure
and no Chrome bot-account profile to sign in. See the `ATTENDEE_*` entries in
step 4 below for the environment variables that configure it instead.

(`demo_standalone.py`, the separate local-mic/speaker demo, still imports
`mia.audio.capture`/`mia.audio.injection` but runs against your system's
default input/output devices — it does not require BlackHole either.)

## 2. Automation permission

The first time `mia` runs `tab_detector.py`, macOS will prompt to allow
Terminal (or whichever app runs `mia`) to control Google Chrome via
Automation. Click **OK**. If missed, grant it manually under
**System Settings → Privacy & Security → Automation**.

## 3. Google Calendar + Gmail OAuth

Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env` (from a Google
Cloud project with both the **Calendar API** and the **Gmail API**
enabled — `gmail.readonly` is a restricted scope, so it must also be
listed under the OAuth consent screen's scopes). Then start `mia`
(`python -m mia.main`) once: it opens the OAuth consent flow in a browser
(now asking for both Calendar and Gmail read access) and stores the
resulting refresh token at `~/.mia/token.json`.

If you're upgrading from a version of `mia` that only requested Calendar
access, delete the cached token once (`rm -f ~/.mia/token.json`) so the
next run re-consents with the full scope list — `mia` also detects this
automatically and re-prompts (see `_authorize_google` in `main.py`), but
deleting it manually avoids relying on that check.

## 4. Environment variables

`python -m mia.main` loads `.env` from the working directory at startup
(real exported environment variables take precedence).

Required — the process refuses to start without them:

- `DEEPGRAM_API_KEY`
- `ANTHROPIC_API_KEY`
- `ELEVENLABS_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Optional:

- `LOGFIRE_TOKEN` — enables Logfire reporting. Leave it unset and `mia`
  runs normally with reporting disabled; Logfire is never required for
  correctness.
- `WAKE_WORD` — defaults to `hey mia`.
- `FUZZY_THRESHOLD` — wake-word match threshold, defaults to `0.75`.
- `ATTENDEE_API_KEY` — the process starts fine without it, but it's
  required to actually create Attendee bots (i.e. to join a call).
- `ATTENDEE_BASE_URL` — defaults to `http://localhost:8000`.
- `ATTENDEE_WEBSOCKET_PORT` — defaults to `8765`.
- `ATTENDEE_BOT_NAME` — defaults to `Mia`.
