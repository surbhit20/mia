# One-time setup (macOS)

Run `./setup_audio.sh` first, then complete these manual steps — none of
this can be scripted.

## 0. Install `mia`

```sh
pip install -e ".[dev]"     # runtime + test dependencies
cp .env.example .env        # then fill in your keys (see step 5)
```

`mia` runs as a long-lived foreground process:

```sh
python -m mia.main
```

## 1. Audio MIDI Setup routing (standalone demo only)

These steps route audio for `demo_standalone.py`, the local BlackHole-based
demo that runs mia's pipeline against your own microphone and speakers.
They are not needed for the real Meet-join path, which joins calls through
Recall.ai's cloud meeting bot instead — see step 2.

1. Open **Audio MIDI Setup** (Spotlight search).
2. Click **+** (bottom left) → **Create Multi-Output Device**.
3. Check both your normal speakers and **BlackHole 2ch**.
4. This routes call audio to both your ears and to BlackHole (which
   `demo_standalone.py` captures from).

**Warning:** do not set this Multi-Output Device as your Mac's system-wide
default output — every system sound (Slack pings, email notifications)
would leak into the call. Only select it as the output *inside Meet's own
in-call settings*. If your macOS version supports per-app audio output
routing (Sonoma+), prefer that instead; otherwise mute other notification
sounds while running the demo.

## 2. Recall.ai Meet-bot path

The real Meet-join path runs through [Recall.ai](https://www.recall.ai)'s
cloud meeting-bot API instead of a locally-driven browser: Recall's bot
joins the Google Meet call and connects out to a local websocket server
(`RecallAudioBridge`) that `mia` runs, exposed publicly through an ngrok
reserved domain. No Google Workspace bot account or browser profile is
needed.

1. Get a Recall.ai API key and set `RECALL_API_KEY` in `.env`.
2. Install [ngrok](https://ngrok.com) and set up a **reserved domain**
   (paid tier — the free tier's interstitial page blocks Recall's bot from
   connecting automatically), pointed at `http://localhost:8765` (or
   whatever `RECALL_WEBSOCKET_PORT` is set to):
   ```sh
   ngrok http --domain=your-reserved-domain.ngrok.app 8765
   ```
3. Set `RECALL_WEBSOCKET_HOSTNAME` in `.env` to that reserved domain --
   hostname only, no `https://` prefix (e.g.
   `RECALL_WEBSOCKET_HOSTNAME=your-reserved-domain.ngrok.app`).

## 3. Automation permission

The first time `mia` runs `tab_detector.py`, macOS will prompt to allow
Terminal (or whichever app runs `mia`) to control Google Chrome via
Automation. Click **OK**. If missed, grant it manually under
**System Settings → Privacy & Security → Automation**.

## 4. Google Calendar + Gmail OAuth

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
