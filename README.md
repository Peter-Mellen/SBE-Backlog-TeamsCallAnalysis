# SBE-Backlog-TeamsCallAnalysis

Backend-only proof of concept for pulling Microsoft Teams meeting transcripts into a local store with Microsoft Graph, scoped to one configured user.

This PoC does three things:

- runs an app-only Graph client using your Entra app registration
- syncs transcript history for one organizer with Graph delta
- exposes webhook endpoints so new transcript events can trigger a sync

It intentionally stops at ingestion. There is no database, UI, or scoring engine yet.

## What Lands Where

- raw Graph notifications: `data/notifications/`
- raw transcript files: `data/transcripts/<hash>/content.vtt`
- transcript metadata: `data/transcripts/<hash>/metadata.json`
- parsed utterances: `data/transcripts/<hash>/utterances.json`
- latest delta token: `data/state/delta_link.txt`

## Repo Layout

- `src/sbe_teams_call_analysis/config.py`: env loading and runtime settings
- `src/sbe_teams_call_analysis/graph.py`: token acquisition and Graph requests
- `src/sbe_teams_call_analysis/sync.py`: delta sync and transcript download flow
- `src/sbe_teams_call_analysis/server.py`: webhook server for validation and notifications
- `src/sbe_teams_call_analysis/vtt.py`: simple WebVTT parser

## Prerequisites

- Python 3.11+
- an Entra app registration with client credentials
- admin consent for transcript-related Graph app permissions
- a Teams application access policy that allows the app to act on your user
- a public HTTPS URL for the webhook if you want automatic future capture

## Graph Permissions

Start with:

- `OnlineMeetingTranscript.Read.All`

If `getAllTranscripts` rejects the initial sync with insufficient privileges, add:

- `OnlineMeetings.Read.All`

That second permission is included here because Microsoft documentation for `getAllTranscripts` and transcript endpoints is still inconsistent across pages.

## Teams Application Access Policy

After granting admin consent in Entra, apply a Teams application access policy to your own user. Use your app's client ID and your own Entra object ID.

```powershell
Connect-MicrosoftTeams

New-CsApplicationAccessPolicy `
  -Identity "SBETranscriptPoc" `
  -AppIds "<your-client-id>" `
  -Description "Allow Teams transcript PoC to act on one user"

Grant-CsApplicationAccessPolicy `
  -PolicyName "SBETranscriptPoc" `
  -Identity "<your-user-object-id>"
```

Propagation can take a little while after the grant.

## Local Setup

1. Create a virtual environment if you want one.
2. Install the package in editable mode.
3. Copy `.env.example` to `.env`.
4. Fill in the settings for your tenant, app, and target user.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

Recommended `.env` values:

- `TARGET_USER_ID`: your Entra object ID
- `NOTIFICATION_URL`: `https://<public-host>/webhook`
- `LIFECYCLE_NOTIFICATION_URL`: optional for this PoC because the default subscription lifetime is 55 minutes
- `CLIENT_STATE`: any random shared secret string
- `INITIAL_SYNC_START_DATE`: use an ISO timestamp like `2026-04-01T00:00:00Z` to keep the first backfill narrow

## Running The PoC

### 1. Initial Manual Sync

This pulls transcript history for the configured user and stores everything under `data/`.

```powershell
sbe-teams-poc sync
```

### 2. Run The Webhook Server

```powershell
sbe-teams-poc serve --sync-on-start
```

Local endpoints:

- `http://127.0.0.1:8080/healthz`
- `http://127.0.0.1:8080/webhook`
- `http://127.0.0.1:8080/lifecycle`

### 3. Expose The Webhook Publicly

Use any HTTPS tunnel you already trust, for example Cloudflare Tunnel or ngrok, and point it at local port `8080`.

Your public URLs should map like this:

- `https://<public-host>/webhook`
- `https://<public-host>/lifecycle`

### 4. Create The Subscription

Once the public webhook is live and `.env` contains the correct `NOTIFICATION_URL`, create the Graph subscription:

```powershell
sbe-teams-poc create-subscription
```

The created subscription is stored in `data/subscriptions/` and the latest one is mirrored to `data/state/last_subscription.json`.

### 5. Renew Or Inspect Subscriptions

```powershell
sbe-teams-poc list-subscriptions
sbe-teams-poc renew-subscription
```

If you omit the ID on `renew-subscription`, the tool uses the last saved subscription ID.

## Notes And Constraints

- This is app-only and single-user by design.
- The webhook path schedules a delta sync instead of trying to fully process the notification payload inline.
- The default subscription lifetime is `55` minutes so you can get started without a lifecycle URL.
- The PoC assumes scheduled online meetings with transcript generation enabled.
- Checklist scoring is not included yet. The next step after proving ingestion is to layer a deterministic checklist evaluator on top of `utterances.json`.
