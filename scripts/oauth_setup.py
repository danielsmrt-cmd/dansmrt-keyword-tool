"""
oauth_setup.py — ONE-TIME script. Run this on your own laptop, not in GitHub
Actions. It opens a browser for you to consent, then prints a refresh token
you paste into GitHub secrets once. After that, GitHub Actions refreshes its
own access tokens on every run — nothing further to do in a browser again
(unless you ever revoke access in your Google account).

Scope requested: yt-analytics.readonly (view-only access to YouTube
Analytics — watch time, retention, subscriber deltas). This does NOT grant
upload, edit, or delete permissions on your channel.

Prerequisites (see README.md "OAuth setup" section for the click-by-click):
  1. In Google Cloud Console (SECONDARY account, same project as your
     YT_API_KEY), create an OAuth 2.0 Client ID of type "Desktop app".
  2. Have the Client ID and Client Secret from that screen ready.

Usage:
    pip install google-auth-oauthlib --break-system-packages
    python scripts/oauth_setup.py
"""

import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Missing dependency. Run: pip install google-auth-oauthlib")
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]


def main():
    print("=== DanSmrt Keyword Radar — one-time YouTube Analytics authorization ===\n")
    client_id = input("Paste your OAuth Client ID: ").strip()
    client_secret = input("Paste your OAuth Client Secret: ").strip()
    if not client_id or not client_secret:
        print("Both values are required. Aborting.")
        sys.exit(1)

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    print("\nA browser window will open. Sign in with your SECONDARY Google "
          "account (the one your channel/API key uses) and approve access.\n")
    creds = flow.run_local_server(port=0)

    print("\n--- SUCCESS ---")
    print("Add these three as GitHub repo secrets (Settings → Secrets and "
          "variables → Actions → New repository secret):\n")
    print(f"YT_OAUTH_CLIENT_ID     = {client_id}")
    print(f"YT_OAUTH_CLIENT_SECRET = {client_secret}")
    print(f"YT_REFRESH_TOKEN       = {creds.refresh_token}")
    print("\nKeep this output private — do not commit it or paste it anywhere "
          "public. This script does not save it to disk.")


if __name__ == "__main__":
    main()
