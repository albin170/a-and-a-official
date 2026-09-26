"""Supabase client singleton.

Needs env vars:
  SUPABASE_URL         e.g. https://qxoqntaelyqteqlqslyq.supabase.co
  SUPABASE_SERVICE_KEY the service_role / secret key (server-side only, never commit it)
"""
import os
from supabase import create_client

_client = None


def sb():
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            raise RuntimeError("Missing SUPABASE_URL / SUPABASE_SERVICE_KEY env vars.")
        _client = create_client(url, key)
    return _client
