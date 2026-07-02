"""R2 lake access — the backend's only door to the data lake.

Mirrors the collector's helpers (collector/collect.py) so both sides read
the same layout. All chain-data access will move behind MarketView(as_of)
at M2; this module stays the low-level object-store client.
"""

from __future__ import annotations

import io
import json
import os
import re
from typing import Any

import boto3
import pandas as pd


class R2NotConfigured(RuntimeError):
    """R2 credentials are missing — data routes must report this honestly."""


_REQUIRED_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


def r2_configured() -> bool:
    return all(os.environ.get(k) for k in _REQUIRED_ENV)


def r2_client() -> Any:
    if not r2_configured():
        missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
        raise R2NotConfigured(f"missing env: {', '.join(missing)}")
    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def bucket() -> str:
    return os.environ["R2_BUCKET"]


def get_json(s3: Any, key: str, default: Any) -> Any:
    try:
        obj = s3.get_object(Bucket=bucket(), Key=key)
        return json.loads(obj["Body"].read())
    except Exception:
        return default


def get_parquet(s3: Any, key: str) -> pd.DataFrame | None:
    try:
        obj = s3.get_object(Bucket=bucket(), Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))
    except Exception:
        return None


def list_keys(s3: Any, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return keys


def list_date_prefixes(s3: Any, prefix: str) -> list[str]:
    """Sorted ISO dates found as date=YYYY-MM-DD/ sub-prefixes (cheap listing)."""
    dates: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket(), Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            m = re.search(r"date=(\d{4}-\d{2}-\d{2})", cp["Prefix"])
            if m:
                dates.append(m.group(1))
    return sorted(dates)


def list_chain_dates(s3: Any, source: str, ticker: str) -> list[str]:
    return list_date_prefixes(s3, f"options/source={source}/ticker={ticker}/")
