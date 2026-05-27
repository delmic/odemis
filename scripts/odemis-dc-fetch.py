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
