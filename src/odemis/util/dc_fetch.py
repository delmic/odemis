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

import logging
import configparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import boto3

from odemis.util.datacollector import S3_BUCKET, S3_ENDPOINT_URL, S3_REGION


_DC_FETCH_CONFIG_PATH = Path.home() / ".config" / "odemis" / "dc_fetch.ini"


def parse_since_utc(value: str) -> datetime:
    """
    Parse a date/datetime string to UTC-aware datetime.
    :param value: ISO-8601 date (YYYY-MM-DD) or datetime
                  (YYYY-MM-DDTHH:MM:SS with optional timezone offset or Z suffix).
    :return: UTC-aware datetime.
    """
    text = value.strip()
    if len(text) == 10:
        parsed = datetime.strptime(text, "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    # datetime.fromisoformat() does not accept the 'Z' suffix in Python < 3.11.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def parse_key_timestamp_utc(key: str) -> Optional[datetime]:
    """
    Parse <event>-<YYYYMMDDTHHmmss>-<uuid>.zip timestamp from key basename.

    The S3 object key is the full path-like key in the bucket, for example:
    ``meteor-5099/z_stack_acquired-20260322T104530-a1b2c3d4.zip``.
    In this format, ``meteor-5099/`` is the host prefix and the basename is
    ``z_stack_acquired-20260322T104530-a1b2c3d4.zip``.

    :param key: S3 object key.
    :return: Parsed UTC datetime, or None if parsing failed.
    """
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
    """
    Parse event name from <event>-<YYYYMMDDTHHmmss>-<uuid>.zip key basename.

    Example key:
    ``meteor-5099/z_stack_acquired-20260322T104530-a1b2c3d4.zip``

    Parsed event name from the basename:
    ``z_stack_acquired``

    :param key: S3 object key.
    :return: Event name, or None if parsing failed.
    """
    name = Path(key).name
    if not name.endswith(".zip"):
        return None
    stem = name[:-4]
    parts = stem.rsplit("-", 2)
    if len(parts) != 3:
        return None
    return parts[0] or None

def iter_s3_objects(s3_client: Any, bucket: str, prefix: str) -> Iterator[Dict[str, Any]]:
    """
    Iterate S3 objects under prefix using list_objects_v2 pagination.
    :param s3_client: Boto3 S3 client instance.
    :param bucket: S3 bucket name.
    :param prefix: S3 prefix to filter objects.
    :return: Iterator of S3 object dictionaries.
    """
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
        if not token:
            logging.warning("S3 list_objects_v2 returned IsTruncated=True but no NextContinuationToken; stopping pagination.")
            break

def should_download_key(key: str, event_filter: Optional[str], since_utc: Optional[datetime]) -> bool:
    """
    Return whether an S3 key should be downloaded by filters.
    :param key: S3 object key (for example: ``meteor-5099/z_stack_acquired-20260322T104530-a1b2c3d4.zip``).
    :param event_filter: Optional event name filter.
    :param since_utc: Optional UTC datetime filter.
    :return: True if the key should be downloaded, False otherwise.
    """
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

def parse_host_filters(value: Optional[str]) -> List[str]:
    """
    Parse comma-separated host filters into normalized host IDs.
    :param value: Comma-separated host filter string.
    :return: List of normalized host IDs.
    """
    if not value:
        return []
    hosts = [part.strip().strip("/") for part in value.split(",")]
    return [host for host in hosts if host]


def _write_dc_fetch_config(
    config: configparser.ConfigParser,
    config_path: Path,
) -> None:
    """
    Write dc_fetch INI config to disk, creating parent directories if needed.

    :param config: Parsed configuration object.
    :param config_path: Destination INI file path.
    :return: None.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as fp:
        config.write(fp)


def _load_or_init_dc_fetch_config(config_path: Path) -> configparser.ConfigParser:
    """
    Load the data retrieval keys from the config path. If the file does not
    exist, the template for the file will be created.

    :param config_path: path from where the data retrieval keys exists
    :return: Parsed configuration object.
    :raises RuntimeError: If the config was just created or credentials are missing.
    """
    resolved_path = config_path or _DC_FETCH_CONFIG_PATH
    cp = configparser.ConfigParser(interpolation=None)
    default_endpoint = "" if S3_ENDPOINT_URL is None else str(S3_ENDPOINT_URL)
    defaults = {
        "access_key": "",
        "secret_key": "",
        "bucket": S3_BUCKET,
        "endpoint_url": default_endpoint,
        "region": S3_REGION,
    }

    if resolved_path.exists():
        cp.read(str(resolved_path), encoding="utf-8")
    else:
        cp["s3"] = defaults
        _write_dc_fetch_config(cp, resolved_path)
        raise RuntimeError(
            "dc_fetch config created at {}. Please edit [s3] access_key and "
            "secret_key, then run the command again.".format(resolved_path)
        )

    if not cp.has_section("s3"):
        cp["s3"] = defaults
        _write_dc_fetch_config(cp, resolved_path)
        raise RuntimeError(
            "Missing [s3] section in {}. A template was written. Please set "
            "[s3] access_key and secret_key.".format(resolved_path)
        )

    access_key = cp.get("s3", "access_key", fallback="").strip()
    secret_key = cp.get("s3", "secret_key", fallback="").strip()
    if not access_key or not secret_key:
        raise RuntimeError(
            "Missing S3 credentials in {}. Please set [s3] access_key and "
            "secret_key.".format(resolved_path)
        )

    return cp

def build_s3_client_from_config(
    config_path: Path,
    bucket_override: Optional[str] = None,
    endpoint_override: Optional[str] = None,
    region_override: Optional[str] = None,
) -> Tuple[Any, str]:
    """
    Build an S3 client from dc_fetch.ini with optional endpoint/bucket overrides.
    :param config_path: path to retrieve keys for data retrieval.
    :param bucket_override: Optional S3 bucket name override.
    :param endpoint_override: Optional S3 endpoint URL override.
    :param region_override: Optional AWS region name override.
    :return: Tuple of (Boto3 S3 client, bucket name).
    """
    cp = _load_or_init_dc_fetch_config(config_path=config_path)
    default_endpoint = "" if S3_ENDPOINT_URL is None else str(S3_ENDPOINT_URL)

    endpoint_url_text = endpoint_override
    if endpoint_url_text is None:
        endpoint_url_text = cp.get("s3", "endpoint_url", fallback=default_endpoint).strip()
    endpoint_url = endpoint_url_text or None

    bucket = bucket_override
    if bucket is None:
        bucket = cp.get("s3", "bucket", fallback=S3_BUCKET).strip() or S3_BUCKET

    region_name = region_override
    if region_name is None:
        region_name = cp.get("s3", "region", fallback=S3_REGION).strip() or S3_REGION

    client_kwargs: Dict[str, Any] = {
        "endpoint_url": endpoint_url,
        "aws_access_key_id": cp.get("s3", "access_key").strip(),
        "aws_secret_access_key": cp.get("s3", "secret_key").strip(),
        "region_name": region_name,
    }
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
    """
    Fetch matching samples from S3 into output directory.
    :param event_filter: Optional event name filter.
    :param since_utc: Optional UTC datetime filter.
    :param output_dir: Directory to save downloaded samples.
    :param host_filter: Optional comma-separated host filter string.
    :param bucket_override: Optional S3 bucket name override.
    :param endpoint_override: Optional S3 endpoint URL override.
    :param region_override: Optional AWS region name override.
    :return: Dictionary with counts of listed, matched, downloaded, skipped, and failed samples.
    """
    s3_client, bucket = build_s3_client_from_config(
        config_path=_DC_FETCH_CONFIG_PATH,
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
            raw_key = item.get("Key")
            key = raw_key if isinstance(raw_key, str) else None
            if not key or not should_download_key(key, event_filter, since_utc):
                continue
            matched += 1
            # Flatten the S3 key (e.g. "host/file.zip" → "host_file.zip") so
            # files from different hosts never collide in the output directory.
            flat_name = key.replace("/", "_")
            destination = output_dir / flat_name
            if destination.exists():
                skipped_existing += 1
                continue
            # Write to a .part file first so a failed download never leaves a
            # truncated ZIP that would be mistaken for a complete file on retry.
            tmp_dest = Path(str(destination.with_suffix(".part")))
            try:
                s3_client.download_file(bucket, key, str(tmp_dest))
                tmp_dest.rename(destination)
                downloaded += 1
            except Exception:
                tmp_dest.unlink(missing_ok=True)
                failed += 1
                logging.exception("Failed to download key %s", key)

    return {
        "listed": listed,
        "matched": matched,
        "downloaded": downloaded,
        "skipped_existing": skipped_existing,
        "failed": failed,
    }
