# One-time setup (macOS)

Run `./setup_audio.sh` first, then complete these manual steps — none of
this can be scripted.

## 0. Install `mia`

```sh
pip install -e ".[dev]"     # runtime + test dependencies
playwright install chromium # the browser JoinWorker drives
cp .env.example .env        # then fill in your keys (see step 5)
```

`mia` runs as a long-lived foreground process:

```sh
python -m mia.main
```

## 1. Audio MIDI Setup routing

1. Open **Audio MIDI Setup** (Spotlight search).
2. Click **+** (bottom left) → **Create Multi-Output Device**.
3. Check both your normal speakers and **BlackHole 2ch**.
4. This routes call audio to both your ears and to BlackHole (which `mia`
   captures from).

**Warning:** do not set this Multi-Output Device as your Mac's system-wide
default output — every system sound (Slack pings, email notifications)
would leak into the call. Only select it as the output *inside Meet's own
in-call settings* (next step). If your macOS version supports per-app audio
output routing (Sonoma+), prefer that instead; otherwise mute other
notification sounds while `mia` is running.

## 2. Bot account login and device selection (one time, in Chromium)

1. Run `python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch_persistent_context('~/.mia/chrome-profile', headless=False, channel='chrome'); input('press enter when done'); b.close()"`.
   (`channel='chrome'` uses your real, locally-installed Google Chrome
   instead of Playwright's bundled Chromium — Google's sign-in flow
   detects and blocks the bundled one outright. Requires Google Chrome to
   already be installed.)
2. Log into the bot's dedicated Google account.
3. Join any Meet call, open in-call device settings, and select
   **BlackHole 2ch** as both the microphone and speaker.
4. Press enter in the terminal to close — this profile is reused on every
   future run, so this is a one-time step.

## 3. Automation permission

The first time `mia` runs `tab_detector.py`, macOS will prompt to allow
Terminal (or whichever app runs `mia`) to control Google Chrome via
Automation. Click **OK**. If missed, grant it manually under
**System Settings → Privacy & Security → Automation**.

## 4. Google Calendar OAuth

Set `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env` (from a Google
Cloud project with the Calendar API enabled), then start `mia`
(`python -m mia.main`) once: it opens the OAuth consent flow in a browser
and stores the resulting refresh token at `~/.mia/token.json`.

## 5. Environment variables

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
