# One-time setup (macOS)

Run `./setup_audio.sh` first, then complete these manual steps — none of
this can be scripted.

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

1. Run `python -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch_persistent_context('~/.mia/chrome-profile', headless=False); input('press enter when done'); b.close()"`.
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
Cloud project with the Calendar API enabled), then run `mia`'s OAuth flow
once (wired up in Task 19) to store a refresh token locally.
