#!/usr/bin/env python
"""Disable 2FA when the authenticator is lost.

Run inside the container: docker exec <container> python /app/scripts/reset-2fa.py
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.core.database import AsyncSessionLocal
from app.services.setup_state import set_totp_secret, set_totp_enabled


async def main():
    async with AsyncSessionLocal() as s:
        await set_totp_secret(s, None)
        await set_totp_enabled(s, False)
        await s.commit()
    print("2FA disabled. Log in with your password.")


asyncio.run(main())
