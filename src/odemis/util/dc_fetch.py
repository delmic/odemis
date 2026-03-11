# -*- coding: utf-8 -*-
"""
Created on 11 March 2026

@author: Karishma Kumar

Copyright © 2026 Karishma Kumar, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Retrieval helpers for downloading DataCollector ZIP samples from S3.
"""


import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from odemis.util.datacollector import DataCollectorConfig, S3UploadBackend


def parse_since_utc(value: str) -> datetime:
    """Parse a date/datetime string to UTC-aware datetime.

    Accepts ISO-8601 date (`YYYY-MM-DD`) and datetime (`YYYY-MM-DDTHH:MM:SS` with optional timezone).
    """
    text = value.strip()
    if len(text) == 10:
        parsed = datetime.strptime(text, "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_key_timestamp_utc(key: str) -> Optional[datetime]:
    """Parse `<event>-<YYYYMMDDTHHmmss>-<uuid>.zip` timestamp from key basename."""
    name = Path(key).name
    if not name.endswith(".zip"):
        return None
    stem = name[:-4]
    parts = stem.rsplit("-", 2)
    if len(parts) != 3:
        return None
    ts = parts[1]
    try:
        parsed = datetime.strptime(ts, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def parse_key_event_name(key: str) -> Optional[str]:
    """Parse event name from `<event>-<YYYYMMDDTHHmmss>-<uuid>.zip` key basename."""
    name = Path(key).name
    if not name.endswith(".zip"):
        return None
    stem = name[:-4]
    parts = stem.rsplit("-", 2)
    if len(parts) != 3:
        return None
    return parts[0] or None


def build_argument_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for `odemis-dc-fetch`."""
    examples = (
        "Examples:\n"
        "  odemis-dc-fetch\n"
        "  odemis-dc-fetch --output ./downloads\n"
        "  odemis-dc-fetch --event z_stack_acquired\n"
        "  odemis-dc-fetch --since 2026-03-01\n"
        "  odemis-dc-fetch --host meteor-5099\n"
        "  odemis-dc-fetch --host meteor-5099,atlas-001,secom-22\n"
        "  odemis-dc-fetch --bucket delmic-odemis-collect-test --region eu-west-1\n"
        "  odemis-dc-fetch --since 2026-03-01T12:30:00 --event z_stack_acquired --output ./dc_samples"
    )
    parser = argparse.ArgumentParser(
        description="Fetch data-collection ZIP samples from S3.",
        epilog=examples,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--event",
        dest="event",
        help="Only fetch samples matching event name.",
    )
    parser.add_argument(
        "--since",
        dest="since",
        help="Only fetch samples since UTC date/datetime (e.g. 2026-03-01 or 2026-03-01T12:30:00).",
    )
    parser.add_argument(
        "--output",
        dest="output",
        default="./dc_samples",
        help="Output directory for downloaded ZIPs (default: ./dc_samples).",
    )
    parser.add_argument(
        "--host",
        dest="host",
        help="Optional host/system-id filter; use comma-separated IDs for multiple hosts. "
             "By default, fetch across all hosts.",
    )
    parser.add_argument(
        "--bucket",
        dest="bucket",
        help="Optional S3 bucket override (default from datacollector backend config).",
    )
    parser.add_argument(
        "--endpoint-url",
        dest="endpoint_url",
        help="Optional S3 endpoint URL override (default from datacollector backend config).",
    )
    parser.add_argument(
        "--region",
        dest="region",
        help="Optional AWS region name for S3 client creation.",
    )
    return parser


def iter_s3_objects(s3_client: Any, bucket: str, prefix: str) -> Iterator[Dict[str, Any]]:
    """Iterate S3 objects under `prefix` using `list_objects_v2` pagination."""
    token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        response = s3_client.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            yield item
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")


def should_download_key(key: str, event_filter: Optional[str], since_utc: Optional[datetime]) -> bool:
    """Return whether an S3 key should be downloaded by filters."""
    if not key.endswith(".zip"):
        return False
    if event_filter:
        event_name = parse_key_event_name(key)
        if event_name != event_filter:
            return False
    if since_utc:
        key_ts = parse_key_timestamp_utc(key)
        if key_ts is None:
            return False
        if key_ts < since_utc:
            return False
    return True


def create_s3_client_from_config(config: DataCollectorConfig) -> tuple[Any, str]:
    """Create S3 client and return `(client, bucket)`."""
    backend = config.get_upload_backend()
    if not isinstance(backend, S3UploadBackend):
        raise RuntimeError("Only S3 backend is supported for retrieval.")
    # Accessing protected members intentionally to reuse existing backend setup.
    client = backend._get_client()  # pylint: disable=protected-access
    bucket = backend._bucket  # pylint: disable=protected-access
    return client, bucket


def parse_host_filters(value: Optional[str]) -> List[str]:
    """Parse comma-separated host filters into normalized host IDs."""
    if not value:
        return []
    hosts = [part.strip().strip("/") for part in value.split(",")]
    return [host for host in hosts if host]


def build_s3_client_from_config(
    config: DataCollectorConfig,
    bucket_override: Optional[str] = None,
    endpoint_override: Optional[str] = None,
    region_override: Optional[str] = None,
) -> tuple[Any, str]:
    """Build an S3 client using datacollector credentials with optional endpoint/bucket overrides."""
    backend = config.get_upload_backend()
    if not isinstance(backend, S3UploadBackend):
        raise RuntimeError("Only S3 backend is supported for retrieval.")
    endpoint_url = endpoint_override or backend._endpoint_url  # pylint: disable=protected-access
    bucket = bucket_override or backend._bucket  # pylint: disable=protected-access
    import boto3
    client_kwargs: Dict[str, Any] = {
        "endpoint_url": endpoint_url,
        "aws_access_key_id": backend._access_key,  # pylint: disable=protected-access
        "aws_secret_access_key": backend._secret_key,  # pylint: disable=protected-access
    }
    if region_override:
        client_kwargs["region_name"] = region_override
    else:
        client_kwargs["region_name"] = backend._region  # pylint: disable=protected-access
    client = boto3.client("s3", **client_kwargs)
    return client, bucket


def fetch_samples(
    event_filter: Optional[str],
    since_utc: Optional[datetime],
    output_dir: Path,
    host_filter: Optional[str] = None,
    bucket_override: Optional[str] = None,
    endpoint_override: Optional[str] = None,
    region_override: Optional[str] = None,
) -> Dict[str, int]:
    """Fetch matching samples from S3 into output directory."""
    cfg = DataCollectorConfig()
    s3_client, bucket = build_s3_client_from_config(
        cfg,
        bucket_override=bucket_override,
        endpoint_override=endpoint_override,
        region_override=region_override,
    )
    host_filters = parse_host_filters(host_filter)
    prefixes = [f"{host}/" for host in host_filters] if host_filters else [""]

    output_dir.mkdir(parents=True, exist_ok=True)

    listed = 0
    matched = 0
    downloaded = 0
    skipped_existing = 0
    failed = 0

    for prefix in prefixes:
        for item in iter_s3_objects(s3_client, bucket=bucket, prefix=prefix):
            listed += 1
            key = item.get("Key")
            if not key or not should_download_key(key, event_filter, since_utc):
                continue
            matched += 1
            destination = output_dir / Path(key).name
            if destination.exists():
                skipped_existing += 1
                continue
            try:
                s3_client.download_file(bucket, key, str(destination))
                downloaded += 1
            except Exception:
                failed += 1
                logging.exception("Failed to download key %s", key)

    return {
        "listed": listed,
        "matched": matched,
        "downloaded": downloaded,
        "skipped_existing": skipped_existing,
        "failed": failed,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for retrieval flow."""
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    try:
        since_utc = parse_since_utc(args.since) if args.since else None
    except ValueError:
        logging.error("Invalid --since value: %s", args.since)
        return 2

    output_dir = Path(args.output)
    try:
        result = fetch_samples(
            event_filter=args.event,
            since_utc=since_utc,
            output_dir=output_dir,
            host_filter=args.host,
            bucket_override=args.bucket,
            endpoint_override=args.endpoint_url,
            region_override=args.region,
        )
    except Exception:
        logging.exception("Failed to fetch samples from S3")
        return 1

    print(
        "listed={listed} matched={matched} downloaded={downloaded} "
        "skipped_existing={skipped_existing} failed={failed}".format(**result)
    )
    return 0 if result["failed"] == 0 else 1
