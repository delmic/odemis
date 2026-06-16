#!/usr/bin/env python3
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

Fetch DataCollector ZIP samples from S3.

Examples:
  odemis-dc-fetch
  odemis-dc-fetch --output ./downloads
  odemis-dc-fetch --event z_stack_acquired
  odemis-dc-fetch --since 2026-03-01
  odemis-dc-fetch --host meteor-5099
  odemis-dc-fetch --host meteor-5099,atlas-001,secom-22
  odemis-dc-fetch --bucket delmic-odemis-collect-test --region eu-west-1
  odemis-dc-fetch --since 2026-03-01T12:30:00 --event z_stack_acquired --output ./dc_samples
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from odemis.util.dc_fetch import fetch_samples, parse_since_utc


def build_argument_parser() -> argparse.ArgumentParser:
    """
    Build CLI argument parser for odemis-dc-fetch.
    :return: Configured ArgumentParser instance.
    """
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


def main(argv: Optional[List[str]] = None) -> int:
    """
    Run CLI retrieval flow.
    :param argv: Optional command line arguments.
    :return: Process return code.
    """
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rc = main(sys.argv[1:])
    logging.shutdown()
    sys.exit(rc)
