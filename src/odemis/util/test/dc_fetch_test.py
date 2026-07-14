#!/usr/bin/env python3
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
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from odemis.util import dc_fetch


class DCFetchTest(unittest.TestCase):
    """Unit tests for S3 retrieval helpers."""

    def test_parse_since_utc_date(self) -> None:
        """Date input should parse as UTC midnight."""
        parsed = dc_fetch.parse_since_utc("2026-03-22")
        self.assertEqual(parsed, datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc))

    def test_parse_key_timestamp(self) -> None:
        """Timestamp should be parsed from key basename."""
        parsed = dc_fetch.parse_key_timestamp_utc("host/z_stack_acquired-20260322T104530-a1b2c3d4.zip")
        self.assertEqual(parsed, datetime(2026, 3, 22, 10, 45, 30, tzinfo=timezone.utc))

    def test_should_download_key_filters(self) -> None:
        """Event and since filters should both be enforced."""
        key = "host/z_stack_acquired-20260322T104530-a1b2c3d4.zip"
        since_before = datetime(2026, 3, 22, 10, 0, 0, tzinfo=timezone.utc)
        since_after = datetime(2026, 3, 22, 11, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(dc_fetch.should_download_key(key, "z_stack_acquired", since_before))
        self.assertFalse(dc_fetch.should_download_key(key, "other_event", since_before))
        self.assertFalse(dc_fetch.should_download_key(key, "z_stack_acquired", since_after))

    def test_iter_s3_objects_paginates(self) -> None:
        """S3 iterator should follow continuation tokens."""
        client = Mock()
        client.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "host/a.zip"}],
                "IsTruncated": True,
                "NextContinuationToken": "token-1",
            },
            {
                "Contents": [{"Key": "host/b.zip"}],
                "IsTruncated": False,
            },
        ]
        keys = [item["Key"] for item in dc_fetch.iter_s3_objects(client, "bucket", "host/")]
        self.assertEqual(keys, ["host/a.zip", "host/b.zip"])
        self.assertEqual(client.list_objects_v2.call_count, 2)

    def test_parse_host_filters_comma_list(self) -> None:
        """Host parser should accept comma-separated values and normalize them."""
        hosts = dc_fetch.parse_host_filters("meteor-5099, atlas-001 ,/secom-22/")
        self.assertEqual(hosts, ["meteor-5099", "atlas-001", "secom-22"])

    def test_fetch_samples_downloads_matching_keys(self) -> None:
        """Fetch flow should download matching keys and report counters."""
        with tempfile.TemporaryDirectory(prefix="dc_fetch_") as tmp_dir:
            output_dir = Path(tmp_dir)
            client = Mock()
            client.list_objects_v2.return_value = {
                "Contents": [
                    {"Key": "host/evt-20260322T100000-aaaa1111.zip"},
                    {"Key": "host/other-20260322T100000-bbbb2222.zip"},
                ],
                "IsTruncated": False,
            }

            def _download_file(_bucket: str, _key: str, filename: str) -> None:
                Path(filename).write_bytes(b"zip")

            client.download_file.side_effect = _download_file

            with patch("odemis.util.dc_fetch.build_s3_client_from_config", return_value=(client, "bucket")):
                result = dc_fetch.fetch_samples(
                    event_filter="evt",
                    since_utc=datetime(2026, 3, 22, 9, 0, 0, tzinfo=timezone.utc),
                    output_dir=output_dir,
                )

            self.assertEqual(result["listed"], 2)
            self.assertEqual(result["matched"], 1)
            self.assertEqual(result["downloaded"], 1)
            self.assertEqual(result["failed"], 0)
            # Key "host/evt-20260322T100000-aaaa1111.zip" is flattened to
            # "host_evt-20260322T100000-aaaa1111.zip" to avoid host collisions.
            self.assertTrue((output_dir / "host_evt-20260322T100000-aaaa1111.zip").exists())

    def test_fetch_samples_applies_host_filter_prefix(self) -> None:
        """Host filter should become the S3 list prefix."""
        with tempfile.TemporaryDirectory(prefix="dc_fetch_") as tmp_dir:
            output_dir = Path(tmp_dir)
            client = Mock()
            client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

            with patch("odemis.util.dc_fetch.build_s3_client_from_config", return_value=(client, "bucket")):
                dc_fetch.fetch_samples(
                    event_filter=None,
                    since_utc=None,
                    output_dir=output_dir,
                    host_filter="meteor-5099",
                )

            call_kwargs = client.list_objects_v2.call_args.kwargs
            self.assertEqual(call_kwargs["Bucket"], "bucket")
            self.assertEqual(call_kwargs["Prefix"], "meteor-5099/")

    def test_fetch_samples_applies_multiple_host_prefixes(self) -> None:
        """Comma-separated hosts should trigger one listing call per host prefix."""
        with tempfile.TemporaryDirectory(prefix="dc_fetch_") as tmp_dir:
            output_dir = Path(tmp_dir)
            client = Mock()
            client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

            with patch("odemis.util.dc_fetch.build_s3_client_from_config", return_value=(client, "bucket")):
                dc_fetch.fetch_samples(
                    event_filter=None,
                    since_utc=None,
                    output_dir=output_dir,
                    host_filter="meteor-5099,atlas-001",
                )

            self.assertEqual(client.list_objects_v2.call_count, 2)
            first_prefix = client.list_objects_v2.call_args_list[0].kwargs["Prefix"]
            second_prefix = client.list_objects_v2.call_args_list[1].kwargs["Prefix"]
            self.assertEqual(first_prefix, "meteor-5099/")
            self.assertEqual(second_prefix, "atlas-001/")

    def test_fetch_samples_passes_bucket_endpoint_region_overrides(self) -> None:
        """Overrides should be forwarded to the S3 client builder."""
        with tempfile.TemporaryDirectory(prefix="dc_fetch_") as tmp_dir:
            output_dir = Path(tmp_dir)
            client = Mock()
            client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

            with patch("odemis.util.dc_fetch.build_s3_client_from_config", return_value=(client, "bucket")) as builder:
                dc_fetch.fetch_samples(
                    event_filter=None,
                    since_utc=None,
                    output_dir=output_dir,
                    host_filter=None,
                    bucket_override="other-bucket",
                    endpoint_override="https://s3.eu-west-1.amazonaws.com",
                    region_override="eu-west-1",
                )

            kwargs = builder.call_args.kwargs
            self.assertEqual(kwargs["bucket_override"], "other-bucket")
            self.assertEqual(kwargs["endpoint_override"], "https://s3.eu-west-1.amazonaws.com")
            self.assertEqual(kwargs["region_override"], "eu-west-1")

    def test_build_s3_client_creates_template_config_when_missing(self) -> None:
        """First run should create dc_fetch.ini template and prompt user to fill credentials."""
        with tempfile.TemporaryDirectory(prefix="dc_fetch_cfg_") as tmp_dir:
            config_path = Path(tmp_dir) / "dc_fetch.ini"
            with self.assertRaises(RuntimeError) as ctx:
                dc_fetch.build_s3_client_from_config(config_path=config_path)

            self.assertTrue(config_path.exists())
            self.assertIn(str(config_path), str(ctx.exception))
            content = config_path.read_text(encoding="utf-8")
            self.assertIn("[s3]", content)
            self.assertIn("access_key =", content)
            self.assertIn("secret_key =", content)

    def test_build_s3_client_uses_dc_fetch_ini_values(self) -> None:
        """Client builder should read credentials and defaults from dc_fetch.ini."""
        with tempfile.TemporaryDirectory(prefix="dc_fetch_cfg_") as tmp_dir:
            config_path = Path(tmp_dir) / "dc_fetch.ini"
            config_path.write_text(
                "[s3]\n"
                "access_key = test-access\n"
                "secret_key = test-secret\n"
                "bucket = test-bucket\n"
                "endpoint_url =\n"
                "region = eu-west-1\n",
                encoding="utf-8",
            )

            with patch("boto3.client") as mock_boto3_client:
                mock_boto3_client.return_value = Mock()
                _client, bucket = dc_fetch.build_s3_client_from_config(config_path=config_path)

            self.assertEqual(bucket, "test-bucket")
            call_kwargs = mock_boto3_client.call_args.kwargs
            self.assertEqual(call_kwargs.get("aws_access_key_id"), "test-access")
            self.assertEqual(call_kwargs.get("aws_secret_access_key"), "test-secret")
            self.assertEqual(call_kwargs.get("region_name"), "eu-west-1")
            self.assertIsNone(call_kwargs.get("endpoint_url"))


if __name__ == "__main__":
    unittest.main()
