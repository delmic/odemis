#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch DataCollector ZIP samples from S3.

Examples:
  ./scripts/odemis-dc-fetch.py
  ./scripts/odemis-dc-fetch.py --output ./downloads
  ./scripts/odemis-dc-fetch.py --event z_stack_acquired
  ./scripts/odemis-dc-fetch.py --since 2026-03-01
  ./scripts/odemis-dc-fetch.py --host meteor-5099
  ./scripts/odemis-dc-fetch.py --host meteor-5099,atlas-001,secom-22
  ./scripts/odemis-dc-fetch.py --bucket delmic-odemis-collect-test --region eu-west-1
  ./scripts/odemis-dc-fetch.py --since 2026-03-01T12:30:00 --event z_stack_acquired --output ./dc_samples
"""

import logging
import sys

from odemis.util.dc_fetch import main


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    rc = main(sys.argv[1:])
    logging.shutdown()
    sys.exit(rc)
