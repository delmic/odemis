# -*- coding: utf-8 -*-
"""
Created on 11 March 2026

Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import configparser
import json
import logging
import os
import shutil
import socket
import stat
import tempfile
import time
import unittest
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy

from odemis.util.datacollector import (
    DataCollector,
    DataCollectorConfig,
    S3UploadBackend,
    S3_BUCKET,
    S3_REGION,
    S3_TEST_BUCKET,
    _CREDENTIALS_PATH,
    _TEST_DATACOLLECTION_ENV,
    _BackgroundWorker,
    _WorkItem,
    _enforce_queue_limit,
    _serialize,
)

logging.basicConfig(level=logging.DEBUG)


class TestDataCollectorConfig(unittest.TestCase):
    """Tests for DataCollectorConfig read/write behaviour."""

    def setUp(self) -> None:
        self._tmp_conf_dir = tempfile.mkdtemp(prefix="dc_conf_")

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp_conf_dir, ignore_errors=True)

    def _make_config(self) -> DataCollectorConfig:
        """Return a DataCollectorConfig pointed at the temp directory."""
        cfg = DataCollectorConfig.__new__(DataCollectorConfig)
        cfg.file_path = Path(self._tmp_conf_dir) / "datacollector.config"
        import threading
        cfg._cp = configparser.ConfigParser(interpolation=None)
        cfg._lock = threading.Lock()
        cfg._read()
        return cfg

    def test_consent_initially_none(self) -> None:
        """Consent should be None when no config file exists."""
        cfg = self._make_config()
        self.assertIsNone(cfg.consent)

    def test_consent_round_trip(self) -> None:
        """Setting consent to True/False persists to disk and re-reads correctly."""
        cfg = self._make_config()
        cfg.consent = True
        cfg2 = self._make_config()
        self.assertTrue(cfg2.consent)
        cfg.consent = False
        cfg3 = self._make_config()
        self.assertFalse(cfg3.consent)

    def test_config_file_permissions(self) -> None:
        """Config file should be written with mode 0o600 (security requirement)."""
        cfg = self._make_config()
        cfg.consent = True
        mode = stat.S_IMODE(os.stat(str(cfg.file_path)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_postpone_consent_sets_due_and_clears_consent(self) -> None:
        """Postponing should clear consent and schedule next reminder."""
        cfg = self._make_config()
        cfg.consent = True
        cfg.postpone_consent()
        self.assertIsNone(cfg.consent)
        remind_after = cfg.remind_date
        self.assertIsNotNone(remind_after)
        self.assertGreater(remind_after, datetime.now(timezone.utc))

    def test_postpone_does_not_override_explicit_opt_out(self) -> None:
        """Postpone should not schedule reminders when consent is explicitly False."""
        cfg = self._make_config()
        cfg.consent = False
        cfg.postpone_consent()
        self.assertFalse(cfg.consent)
        self.assertIsNone(cfg.remind_date)
        self.assertFalse(cfg.should_prompt_for_consent())

    def test_should_prompt_for_consent_logic(self) -> None:
        """Prompt logic follows consent and remind-after semantics."""
        cfg = self._make_config()
        self.assertTrue(cfg.should_prompt_for_consent())

        cfg.consent = True
        self.assertFalse(cfg.should_prompt_for_consent())

        cfg.clear_consent()
        cfg.remind_date = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertFalse(cfg.should_prompt_for_consent())

        cfg.remind_date = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertTrue(cfg.should_prompt_for_consent())

    def test_get_upload_backend_uses_production_bucket_by_default(self) -> None:
        """get_upload_backend uses the production bucket when TEST_DATACOLLECTION is unset."""
        cfg = self._make_config()
        fake_creds = {"access_key": "AKID", "secret_key": "SECRET"}
        with patch("odemis.util.datacollector._search_credentials", return_value=fake_creds), \
                patch.dict(os.environ, {}, clear=False) as env:
            env.pop(_TEST_DATACOLLECTION_ENV, None)
            backend = cfg.get_upload_backend()
        self.assertIsInstance(backend, S3UploadBackend)
        self.assertEqual(backend._bucket, S3_BUCKET)

    def test_get_upload_backend_uses_test_bucket_when_env_set(self) -> None:
        """get_upload_backend selects S3_TEST_BUCKET when TEST_DATACOLLECTION=1."""
        cfg = self._make_config()
        fake_creds = {"access_key": "AKID", "secret_key": "SECRET"}
        with patch("odemis.util.datacollector._search_credentials", return_value=fake_creds), \
                patch.dict(os.environ, {_TEST_DATACOLLECTION_ENV: "1"}):
            backend = cfg.get_upload_backend()
        self.assertIsInstance(backend, S3UploadBackend)
        self.assertEqual(backend._bucket, S3_TEST_BUCKET)

    def test_get_upload_backend_ignores_non_one_env_value(self) -> None:
        """get_upload_backend falls back to production when TEST_DATACOLLECTION != '1'."""
        cfg = self._make_config()
        fake_creds = {"access_key": "AKID", "secret_key": "SECRET"}
        for value in ("0", "true", "yes", ""):
            with self.subTest(value=value), \
                    patch("odemis.util.datacollector._search_credentials", return_value=fake_creds), \
                    patch.dict(os.environ, {_TEST_DATACOLLECTION_ENV: value}):
                backend = cfg.get_upload_backend()
            self.assertEqual(backend._bucket, S3_BUCKET, f"Expected production bucket for env={value!r}")


class TestSerialize(unittest.TestCase):
    """Tests for _serialize() — ZIP structure and metadata correctness."""

    def setUp(self) -> None:
        self._tmp_queue = Path(tempfile.mkdtemp(prefix="dc_queue_"))

    def tearDown(self) -> None:
        shutil.rmtree(str(self._tmp_queue), ignore_errors=True)

    def _make_item(self, payload: dict, image_format: str = "TIFF") -> _WorkItem:
        return _WorkItem(
            event_name="test_event",
            schema_version="1.0",
            payload=payload,
            image_format=image_format,
        )

    def test_zip_created(self) -> None:
        """A ZIP file is created in queue_dir after serialisation."""
        item = self._make_item({"score": 0.9})
        zip_path = _serialize(item, self._tmp_queue)
        self.assertTrue(zip_path.exists(), "ZIP file not created")
        self.assertTrue(zip_path.suffix == ".zip")

    def test_zip_filename_format(self) -> None:
        """ZIP filename follows <event>-<timestamp>-<uuid8>.zip convention."""
        item = self._make_item({"x": 1})
        zip_path = _serialize(item, self._tmp_queue)
        name = zip_path.name
        parts = name[:-4].split("-")  # strip .zip
        self.assertEqual(parts[0], "test_event")
        self.assertEqual(len(parts[2]), 8, "UUID8 part should be 8 hex characters")

    def test_metadata_json_envelope_fields(self) -> None:
        """metadata.json must contain all standard envelope fields."""
        item = self._make_item({"score": 0.5})
        zip_path = _serialize(item, self._tmp_queue)
        with zipfile.ZipFile(str(zip_path)) as zf:
            meta = json.loads(zf.read("metadata.json"))
        required = {"sample_uuid", "timestamp_utc", "system_id", "odemis_version",
                    "event_name", "schema_version", "payload"}
        self.assertEqual(required, required & meta.keys())
        self.assertEqual(meta["event_name"], "test_event")
        self.assertEqual(meta["schema_version"], "1.0")

    def test_primitive_payload_inlined(self) -> None:
        """Primitive payload values are inlined in metadata.json."""
        item = self._make_item({"score": 0.87, "n": 12, "name": "foo", "flag": True})
        zip_path = _serialize(item, self._tmp_queue)
        with zipfile.ZipFile(str(zip_path)) as zf:
            meta = json.loads(zf.read("metadata.json"))
        self.assertAlmostEqual(meta["payload"]["score"], 0.87)
        self.assertEqual(meta["payload"]["n"], 12)
        self.assertEqual(meta["payload"]["name"], "foo")
        self.assertTrue(meta["payload"]["flag"])

    def test_numpy_array_exported_as_tiff(self) -> None:
        """numpy.ndarray values are exported as .ome.tiff formats."""
        arr = numpy.zeros((64, 64), dtype=numpy.uint16)
        item = self._make_item({"image": arr})
        zip_path = _serialize(item, self._tmp_queue)
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            meta = json.loads(zf.read("metadata.json"))
        output_formats = meta["payload"]["image"]
        self.assertIn(output_formats, names, "TIFF sidecar not in ZIP")
        self.assertTrue(output_formats.endswith(".ome.tiff"))

    def test_dict_payload_written_as_extra_json(self) -> None:
        """dict payload values are written as extra_*.json."""
        item = self._make_item({"params": {"a": 1, "b": 2}})
        zip_path = _serialize(item, self._tmp_queue)
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            meta = json.loads(zf.read("metadata.json"))
        self.assertIn("extra_params.json", names)
        self.assertEqual(meta["payload"]["params"], "extra_params.json")

    def test_list_payload_written_as_extra_json(self) -> None:
        """list payload values are written as extra_*.json sidecars."""
        item = self._make_item({"items": [1, 2, 3]})
        zip_path = _serialize(item, self._tmp_queue)
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
        self.assertIn("extra_items.json", names)

    def test_atomic_write(self) -> None:
        """No .tmp files remain after successful serialisation."""
        item = self._make_item({"x": 1})
        _serialize(item, self._tmp_queue)
        tmps = list(self._tmp_queue.glob("*.tmp"))
        self.assertEqual(tmps, [], "Leftover .tmp files found")

    def test_hdf5_image_format(self) -> None:
        """When image_format=HDF5, DataArray is exported as an .h5."""
        arr = numpy.zeros((32, 32), dtype=numpy.float32)
        item = self._make_item({"data": arr}, image_format="HDF5")
        zip_path = _serialize(item, self._tmp_queue)
        with zipfile.ZipFile(str(zip_path)) as zf:
            names = zf.namelist()
            meta = json.loads(zf.read("metadata.json"))
        output_formats = meta["payload"]["data"]
        self.assertIn(output_formats, names)
        self.assertTrue(output_formats.endswith(".h5"))


class TestEnforceQueueLimit(unittest.TestCase):
    """Tests for _enforce_queue_limit()."""

    def setUp(self) -> None:
        self._tmp_queue = Path(tempfile.mkdtemp(prefix="dc_qlimit_"))

    def tearDown(self) -> None:
        shutil.rmtree(str(self._tmp_queue), ignore_errors=True)

    def _write_zip(self, name: str, size_bytes: int, mtime: float) -> Path:
        """Create a dummy ZIP file of the given size and modification time."""
        p = self._tmp_queue / name
        p.write_bytes(b"\x00" * size_bytes)
        os.utime(str(p), (mtime, mtime))
        return p

    def test_no_deletion_when_under_limit(self) -> None:
        """Files are NOT deleted when total queue size is within the 10% limit."""
        self._write_zip("small.zip", 1024, time.time())
        _enforce_queue_limit(self._tmp_queue)
        self.assertTrue((self._tmp_queue / "small.zip").exists())

    def test_oldest_deleted_when_over_limit(self) -> None:
        """Oldest ZIPs are deleted when the queue exceeds 10% of partition space."""
        import collections
        FakeDiskUsage = collections.namedtuple("usage", ["total", "used", "free"])
        fake_usage = FakeDiskUsage(total=300, used=0, free=300)

        now = time.time()
        # 3 files × 20 bytes = 60 bytes > 10% of 300 (= 30 bytes).
        old = self._write_zip("old.zip", 20, now - 100)
        self._write_zip("newer.zip", 20, now - 50)
        self._write_zip("newest.zip", 20, now)

        with patch("odemis.util.datacollector.shutil.disk_usage", return_value=fake_usage):
            _enforce_queue_limit(self._tmp_queue)

        self.assertFalse(old.exists(), "Oldest ZIP should have been deleted")

    def test_empty_dir_no_error(self) -> None:
        """_enforce_queue_limit on an empty directory must not raise."""
        try:
            _enforce_queue_limit(self._tmp_queue)
        except Exception as exc:
            self.fail(f"_enforce_queue_limit raised unexpectedly: {exc}")

    def test_nonexistent_dir_no_error(self) -> None:
        """_enforce_queue_limit on a non-existent directory must not raise."""
        try:
            _enforce_queue_limit(Path("/nonexistent/path/dc_queue"))
        except Exception as exc:
            self.fail(f"_enforce_queue_limit raised unexpectedly: {exc}")


class TestUploadAndRetry(unittest.TestCase):
    """Tests upload backend and retry behavior."""

    def setUp(self) -> None:
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="dc_upload_"))
        self._queue_dir = self._tmp_dir / "queue"
        self._queue_dir.mkdir(parents=True, exist_ok=True)

        cfg = DataCollectorConfig.__new__(DataCollectorConfig)
        cfg.file_path = self._tmp_dir / "datacollector.config"
        import threading
        cfg._cp = configparser.ConfigParser(interpolation=None)
        cfg._lock = threading.Lock()
        cfg._read()
        cfg.consent = True
        self._cfg = cfg
        self._worker = _BackgroundWorker(cfg, queue_dir=self._queue_dir)

    def tearDown(self) -> None:
        shutil.rmtree(str(self._tmp_dir), ignore_errors=True)

    def _write_zip(self, name: str, mtime: float) -> Path:
        """Create a pending ZIP in queue_dir with a stable mtime."""
        p = self._queue_dir / name
        p.write_bytes(b"zip")
        os.utime(str(p), (mtime, mtime))
        return p

    def test_upload_called_after_serialization(self) -> None:
        """Worker should upload a serialized ZIP right after it is created."""
        order = []
        item = _WorkItem(event_name="upload_call_test", schema_version="1.0", payload={"x": 1})
        target_zip = self._queue_dir / "serialized.zip"

        class _Backend:
            def upload(self, local_path: Path, remote_key: str) -> None:
                order.append(("upload", local_path.name, remote_key))

        def _fake_serialize(work_item: _WorkItem, queue_dir: Path) -> Path:
            del work_item, queue_dir
            target_zip.write_bytes(b"zip")
            order.append(("serialize", target_zip.name))
            return target_zip

        with patch("odemis.util.datacollector._serialize", side_effect=_fake_serialize), \
                patch.object(self._worker, "_get_upload_backend", return_value=_Backend()):
            self._worker._process_work_item(item)

        self.assertEqual(order[0], ("serialize", "serialized.zip"))
        self.assertEqual(order[1][0], "upload")
        self.assertEqual(order[1][1], "serialized.zip")
        self.assertFalse(target_zip.exists(), "ZIP should be deleted after successful upload")

    def test_retry_on_failure_then_success(self) -> None:
        """Failed upload is retried and eventually clears pending ZIPs."""
        now = time.time()
        older = self._write_zip("older.zip", now - 60)
        newer = self._write_zip("newer.zip", now - 30)
        uploaded_names = []
        failures = {"count": 0}

        class _Backend:
            def upload(self, local_path: Path, remote_key: str) -> None:
                uploaded_names.append(local_path.name)
                if failures["count"] == 0:
                    failures["count"] += 1
                    raise ConnectionError("temporary network issue")

        with patch.object(self._worker, "_get_upload_backend", return_value=_Backend()):
            had_pending = self._worker._process_pending_zips(self._queue_dir)
            self.assertTrue(had_pending)
            self.assertTrue(older.exists(), "Failed ZIP should remain for retry")
            self.assertGreater(self._worker._next_retry_at, 0.0)

            with patch("odemis.util.datacollector.time.monotonic", return_value=self._worker._next_retry_at + 1.0):
                had_pending = self._worker._process_pending_zips(self._queue_dir)
            self.assertTrue(had_pending)

        self.assertFalse(older.exists())
        self.assertFalse(newer.exists())
        self.assertEqual(uploaded_names[:3], ["older.zip", "older.zip", "newer.zip"])

    def test_pending_flush_oldest_first(self) -> None:
        """Recovery flush should process queued ZIP files oldest-first."""
        now = time.time()
        self._write_zip("oldest.zip", now - 120)
        self._write_zip("middle.zip", now - 60)
        self._write_zip("newest.zip", now - 10)
        uploaded = []

        class _Backend:
            def upload(self, local_path: Path, remote_key: str) -> None:
                uploaded.append(local_path.name)

        with patch.object(self._worker, "_get_upload_backend", return_value=_Backend()):
            had_pending = self._worker._process_pending_zips(self._queue_dir)
        self.assertTrue(had_pending)
        self.assertEqual(uploaded, ["oldest.zip", "middle.zip", "newest.zip"])
        self.assertEqual(list(self._queue_dir.glob("*.zip")), [])


class TestRealS3Integration(unittest.TestCase):
    """Real S3 integration tests for Phase 2 upload workflow.

    Tests use the credentials from ``_CREDENTIALS_PATH`` and upload to
    ``S3_TEST_BUCKET`` (not the production bucket).  The test class is
    skipped automatically when the key file is absent.
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise unittest.SkipTest(f"boto3 is required for real S3 integration tests: {exc}")

        import json as _json
        if not os.path.isfile(_CREDENTIALS_PATH):
            raise unittest.SkipTest(
                f"S3 key file not found at {_CREDENTIALS_PATH}; skipping real S3 integration tests."
            )
        with open(_CREDENTIALS_PATH, "r") as fh:
            creds = _json.load(fh)

        cls._access_key = creds["access_key"]
        cls._secret_key = creds["secret_key"]
        cls._bucket = S3_TEST_BUCKET
        cls._region = S3_REGION

        cls._s3_client = boto3.client(
            "s3",
            aws_access_key_id=cls._access_key,
            aws_secret_access_key=cls._secret_key,
            region_name=cls._region,
        )

    def setUp(self) -> None:
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="dc_reals3_"))
        self._queue_dir = self._tmp_dir / "queue"
        self._queue_dir.mkdir(parents=True, exist_ok=True)
        self._created_keys: list[str] = []

    def tearDown(self) -> None:
        for key in self._created_keys:
            try:
                self._s3_client.delete_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                logging.warning("Could not delete test object %s: %s", key, exc)
        shutil.rmtree(str(self._tmp_dir), ignore_errors=True)

    def _new_remote_key(self, suffix: str = ".zip") -> str:
        """Create a unique remote key for test uploads."""
        return f"odemis-integration-tests/{socket.gethostname()}/{uuid.uuid4().hex}{suffix}"

    def test_s3_upload_backend_uploads_file(self) -> None:
        """S3UploadBackend.upload should place an object in the configured bucket."""
        local_path = self._tmp_dir / "sample.zip"
        local_path.write_bytes(b"integration-test-payload")
        remote_key = self._new_remote_key()

        backend = S3UploadBackend(
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            bucket=self._bucket,
        )
        backend.upload(local_path, remote_key)
        self._created_keys.append(remote_key)

        response = self._s3_client.head_object(Bucket=self._bucket, Key=remote_key)
        self.assertGreater(response["ContentLength"], 0)

    def test_worker_uploads_pending_and_deletes_local(self) -> None:
        """Background worker should upload pending ZIPs and delete them locally."""
        old_zip = self._queue_dir / "old.zip"
        new_zip = self._queue_dir / "new.zip"
        old_zip.write_bytes(b"old")
        new_zip.write_bytes(b"new")
        now = time.time()
        os.utime(str(old_zip), (now - 60, now - 60))
        os.utime(str(new_zip), (now - 30, now - 30))

        cfg = DataCollectorConfig.__new__(DataCollectorConfig)
        cfg.file_path = self._tmp_dir / "datacollector.config"
        import threading
        cfg._cp = configparser.ConfigParser(interpolation=None)
        cfg._lock = threading.Lock()
        cfg._read()
        cfg.consent = True
        worker = _BackgroundWorker(cfg, queue_dir=self._queue_dir)

        uploaded_keys: list[str] = []
        backend = S3UploadBackend(
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            bucket=self._bucket,
        )

        def _capture_upload(local_path: Path, backend_obj: S3UploadBackend) -> None:
            remote_key = f"{socket.gethostname()}/{local_path.name}"
            backend_obj.upload(local_path, remote_key)
            uploaded_keys.append(remote_key)

        with patch.object(worker, "_get_upload_backend", return_value=backend), \
                patch("odemis.util.datacollector._upload", side_effect=_capture_upload):
            had_pending = worker._process_pending_zips(self._queue_dir)

        self.assertTrue(had_pending)
        self.assertFalse(old_zip.exists(), "Old pending ZIP should be removed locally")
        self.assertFalse(new_zip.exists(), "New pending ZIP should be removed locally")
        self.assertEqual(len(uploaded_keys), 2)

        self._created_keys.extend(uploaded_keys)
        for key in uploaded_keys:
            self.assertIsNotNone(self._s3_client.head_object(Bucket=self._bucket, Key=key))



class DataCollectorTest(unittest.TestCase):
    """Integration-level tests for DataCollector.record()."""

    def setUp(self) -> None:
        self._tmp_dir = Path(tempfile.mkdtemp(prefix="dc_test_"))
        self._queue_dir = self._tmp_dir / "queue"

        cfg = DataCollectorConfig.__new__(DataCollectorConfig)
        cfg.file_path = self._tmp_dir / "datacollector.config"
        import threading
        cfg._cp = configparser.ConfigParser(interpolation=None)
        cfg._lock = threading.Lock()
        cfg._read()
        cfg.consent = True
        self._cfg = cfg

        worker = _BackgroundWorker(cfg, queue_dir=self._queue_dir)

        self._collector = DataCollector()
        self._collector._config = cfg
        self._collector._worker = worker
        self._collector._init_ok = True

    def tearDown(self) -> None:
        shutil.rmtree(str(self._tmp_dir), ignore_errors=True)

    def test_record_returns_fast(self) -> None:
        """record() must return to the caller in under 10 ms."""
        arr = numpy.zeros((256, 256), dtype=numpy.uint16)
        t0 = time.monotonic()
        self._collector.record("perf_test", "1.0", {"image": arr})
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.assertLess(elapsed_ms, 10.0, f"record() took {elapsed_ms:.1f} ms (limit: 10 ms)")

    def test_serialize_creates_zip_with_metadata(self) -> None:
        """_serialize() produces a ZIP with valid metadata.json."""
        item = _WorkItem(event_name="zip_test", schema_version="1.0", payload={"score": 0.5})
        self._queue_dir.mkdir(parents=True, exist_ok=True)
        zip_path = _serialize(item, self._queue_dir)
        self.assertTrue(zip_path.exists())
        with zipfile.ZipFile(str(zip_path)) as zf:
            meta = json.loads(zf.read("metadata.json"))
        self.assertEqual(meta["event_name"], "zip_test")
        self.assertIn("sample_uuid", meta)
        self.assertIn("timestamp_utc", meta)
        self.assertIn("odemis_version", meta)

    def test_noop_when_consent_false(self) -> None:
        """record() does not enqueue work when consent is False."""
        self._cfg.consent = False
        q = self._collector._worker._queue
        size_before = q.qsize()
        self._collector.record("no_consent_test", "1.0", {"x": 1})
        self.assertEqual(q.qsize(), size_before, "No item should be enqueued when consent is False")

    def test_noop_when_consent_none(self) -> None:
        """record() does not enqueue work when consent has not been set."""
        self._cfg._cp.remove_option("general", "consent")
        q = self._collector._worker._queue
        size_before = q.qsize()
        self._collector.record("no_consent_none_test", "1.0", {"x": 1})
        self.assertEqual(q.qsize(), size_before, "No item should be enqueued when consent is None")

    def test_no_exception_on_bad_payload_value(self) -> None:
        """record() must not raise for unserializable payload values."""
        class _Unserializable:
            def __repr__(self):
                raise RuntimeError("boom")

        try:
            self._collector.record("bad_payload", "1.0", {"bad": _Unserializable()})
        except Exception as exc:
            self.fail(f"record() raised unexpectedly: {exc}")

    def test_raises_for_empty_event_name(self) -> None:
        """record() raises ValueError for an empty event_name."""
        with self.assertRaises(ValueError):
            self._collector.record("", "1.0", {})

    def test_raises_for_non_string_event_name(self) -> None:
        """record() raises ValueError when event_name is not a string."""
        with self.assertRaises(ValueError):
            self._collector.record(123, "1.0", {})  # type: ignore[arg-type]

    def test_raises_for_empty_schema_version(self) -> None:
        """record() raises ValueError for an empty schema_version."""
        with self.assertRaises(ValueError):
            self._collector.record("event", "", {})

    def test_raises_for_non_dict_payload(self) -> None:
        """record() raises ValueError when payload is not a dict."""
        with self.assertRaises(ValueError):
            self._collector.record("event", "1.0", [1, 2, 3])  # type: ignore[arg-type]

    def test_raises_for_invalid_image_format(self) -> None:
        """record() raises ValueError for an unknown image_format."""
        with self.assertRaises(ValueError):
            self._collector.record("event", "1.0", {}, image_format="PNG")

    def test_validation_raises_even_when_consent_false(self) -> None:
        """Input validation fires before the consent gate."""
        self._cfg.consent = False
        with self.assertRaises(ValueError):
            self._collector.record("", "1.0", {})

    def test_valid_hdf5_format_accepted(self) -> None:
        """record() accepts 'HDF5' as image_format without raising."""
        try:
            self._collector.record("event", "1.0", {}, image_format="HDF5")
        except ValueError as exc:
            self.fail(f"record() raised ValueError for valid HDF5 format: {exc}")

if __name__ == "__main__":
    unittest.main()
