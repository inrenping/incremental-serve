"""
Script to generate a long-lived JWT token for MCP plugin authentication.

Usage:
    cd /var/www/incremental-serve
    source venv/bin/activate
    python -m scripts.generate_token 1

Where "1" is your user_id.
"""

import sys
from datetime import timedelta

from app.core.security import create_access_token


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.generate_token <user_id>")
        sys.exit(1)

    user_id = sys.argv[1]
    token = create_access_token(
        data={"sub": str(user_id)},
        expires_delta=timedelta(days=365),
    )
    print(f"\nYour 365-day JWT Token:\n")
    print(token)
    print(f"\nPaste this into ChatGPT Plugin → Authentication → Bearer Token")


if __name__ == "__main__":
    main()
