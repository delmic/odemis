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

Odemis Annotated Data Collection Framework.

Provides a thread-safe, non-blocking DataCollector.record() call that any
Odemis module can invoke to capture a labelled data sample. Serialisation
happens asynchronously in a background daemon thread; the caller returns
immediately.
"""

import configparser
import json
import logging
import os
import queue
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import boto3
except ImportError:
    logging.error("boto3 is required for S3 upload functionality; install with 'sudo apt install python3-boto3'")
    raise
import numpy

import odemis
from odemis import model
from odemis.dataio import hdf5, tiff


# S3 bucket name — shared production bucket, created once by the dev team.
S3_BUCKET = "delmic-odemis-collect"

# S3 bucket used for automated tests (not the production bucket).
S3_TEST_BUCKET = "delmic-odemis-collect-test"

# S3 endpoint URL — None means let boto3 resolve the regional endpoint automatically.
# Set explicitly only for custom S3-compatible storage.
S3_ENDPOINT_URL = None
S3_REGION = "eu-west-1"

# Path to the S3 credentials key file (JSON with access_key / secret_key).
_CREDENTIALS_PATH = "/usr/share/odemis/datacollector.key"

# Default paths
_CONF_DIR = os.path.join(os.path.expanduser("~"), ".config", "odemis")
_DEFAULT_QUEUE_DIR = Path("~/.local/share/odemis/dc_queue")

_VALID_IMAGE_FORMATS = ("TIFF", "HDF5")
_INITIAL_RETRY_DELAY_SECONDS = 30.0
_MAX_RETRY_DELAY_SECONDS = 3600.0
CONSENT_DATE_KEY = "consent_date"

# Collection probability: fraction of record() calls that are actually uploaded.
# 100% when consent expires within 1 day (1-day trial), 10% otherwise.
_DEFAULT_COLLECTION_PROBABILITY = 0.10
_FULL_COLLECTION_PROBABILITY = 1.0


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe version of name.

    Strips path components (prevents traversal) and replaces any character
    that is not alphanumeric, -, _ or . with an underscore.

    :param name: Raw name to sanitize.
    :returns: Safe filename string; never empty (falls back to "_").
    """
    # Strip path components to prevent directory traversal.
    name = os.path.basename(name.replace("\\", "/"))
    # Replace characters unsafe in filenames.
    name = re.sub(r"[^\w\-.]", "_", name)
    return name or "_"

def _search_credentials() -> dict:
    """
    Load S3 credentials from the standard key-file location.
    The key file is a JSON file containing access_key and secret_key.
    :returns: Dict with access_key and secret_key.
    :raises LookupError: If the key file is not found at the expected location.
    """
    if not os.path.isfile(_CREDENTIALS_PATH):
        raise LookupError(
            f"S3 credentials key file not found at {_CREDENTIALS_PATH}"
        )
    with open(_CREDENTIALS_PATH, "r") as fh:
        data = json.load(fh)
    return {
        "access_key": data["access_key"],
        "secret_key": data["secret_key"],
    }


class DataCollectorConfig:
    """Persistent configuration for the data-collection framework.

    Backed by a configparser INI file at
    ~/.config/odemis/datacollector.config.

    Sections
    --------
    [general]
        consent       — true / false / none (not yet decided).
        consent_date  — Date (ISO UTC) until which consent is active.
                        Consent auto-expires to False after this date.
                        Commented out when not applicable.

    The file is written in a human-readable format with inline comments so it
    can be inspected and manually edited by a support engineer.  Example::

        [general]
        # Data sharing consent (true / false).
        # consent = true
        #
        # Date until which consent is active (ISO). Auto-expires to false.
        # consent_date =
    """
    file_name: str = "datacollector.config"

    def __init__(self) -> None:
        self.file_path = Path(_CONF_DIR) / self.file_name
        self._cp = configparser.ConfigParser(interpolation=None)
        self._lock = threading.Lock()
        self._read()

    def _read(self) -> None:
        """Read the config file if it exists; otherwise leave defaults."""
        if self.file_path.exists():
            self._cp.read(self.file_path)
        else:
            logging.info("No datacollector config found; using defaults.")

    def _write(self) -> None:
        """Write the current config to file, creating parent directories if needed."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_section("general")

        # consent
        if self.consent is None:
            try:
                self._cp.remove_option("general", "consent")
            except configparser.NoOptionError:
                pass
        else:
            self._cp.set("general", "consent", "true" if self.consent else "false")

        # consent_date (stored as local date: YYYY-MM-DD)
        consent_day = self.consent_date
        if consent_day is None:
            try:
                self._cp.remove_option("general", CONSENT_DATE_KEY)
            except configparser.NoOptionError:
                pass
        else:
            self._cp.set("general", CONSENT_DATE_KEY, consent_day.isoformat())

        with self.file_path.open("w", encoding="utf-8") as fh:
            self._cp.write(fh)

        os.chmod(str(self.file_path), 0o600)

    def _ensure_section(self, section: str) -> None:
        """
        Ensure that section exists in the config, creating it if necessary.
        :param section: Section name to ensure.
        """
        if not self._cp.has_section(section):
            self._cp.add_section(section)

    @property
    def consent(self) -> Optional[bool]:
        """
        Get the consent state, or None if not yet set.
        :return: True if consented, False if declined, None if undecided.
        """
        try:
            return self._cp.getboolean("general", "consent")
        except (configparser.NoSectionError, configparser.NoOptionError):
            return None
        except ValueError:
            return None

    @consent.setter
    def consent(self, value: bool) -> None:
        """
        Set consent to true or false, and clear consent expiry. If none, the consent is cleared
         and becomes undecided.
        :param value: True to opt in, False to opt out.
        """
        with self._lock:
            self._ensure_section("general")
            if value is None:
                self.clear_consent()
            else:
                self._cp.set("general", "consent", "true" if value else "false")
                self._cp.remove_option("general", CONSENT_DATE_KEY)
                self._write()

    def clear_consent(self) -> None:
        """
        Unset consent so it becomes undecided again.
        :return: None
        """
        with self._lock:
            self._ensure_section("general")
            self._cp.remove_option("general", "consent")
            self._cp.remove_option("general", CONSENT_DATE_KEY)
            self._write()

    @property
    def consent_date(self) -> Optional[date]:
        """
        Return the consent expiry date as a local-date value, or None when unset.
        When this date is reached, consent automatically expires to False.
        :return: Local date of consent expiry, or None if not set.
        """
        try:
            value = self._cp.get("general", CONSENT_DATE_KEY)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return None
        value = value.strip()
        if not value:
            return None
        # Preferred format: YYYY-MM-DD (local date only).
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass

        return None

    def set_consent_with_expiry(self, consent_date: date) -> None:
        """
        Enable consent until consent_date (inclusive), then auto-expire to False.

        When consent_date is today or tomorrow, record() uses 100% collection
        probability; otherwise the default 10% applies.

        :param consent_date: Local date after which consent is revoked automatically.
        :return: None
        """
        with self._lock:
            self._ensure_section("general")
            self._cp.set("general", "consent", "true")
            self._cp.set("general", CONSENT_DATE_KEY, consent_date.isoformat())
            self._write()

    def get_upload_backend(self) -> "S3UploadBackend":
        """
        Return the configured upload backend instance.

        When the environment variable TEST_DATACOLLECTION is set to "1",
        uploads are redirected to the test bucket (S3_TEST_BUCKET) so that
        developer and CI runs do not pollute the production dataset.

        :return: Configured S3UploadBackend instance.
        """
        credentials = _search_credentials()
        if os.environ.get("TEST_DATACOLLECTION") == "1":
            bucket = S3_TEST_BUCKET
            logging.info(
                "DataCollector: TEST_DATACOLLECTION=1 — using test bucket '%s'", bucket
            )
        else:
            bucket = S3_BUCKET
        return S3UploadBackend(
            access_key=credentials["access_key"],
            secret_key=credentials["secret_key"],
            endpoint_url=S3_ENDPOINT_URL,
            region=S3_REGION,
            bucket=bucket,
        )


@dataclass
class _WorkItem:
    """A single data-collection event to be serialised and uploaded."""
    event_name: str
    schema_version: str
    payload: dict
    image_format: str = "TIFF"
    submitted_at: float = field(default_factory=time.monotonic)


def _serialize(item: _WorkItem, queue_dir: Path) -> Path:
    """
    Serialise item into a ZIP archive and place it in queue_dir.
    :param item: The work item to serialise.
    :param queue_dir: Directory where the finished ZIP is placed.
    :returns: Path to the created ZIP file inside queue_dir.
    :raises OSError: On disk errors (caller must handle).
    """
    queue_dir.mkdir(parents=True, exist_ok=True)

    sample_uuid = str(uuid.uuid4())
    uuid8 = sample_uuid.split("-")[0]
    timestamp_utc = datetime.now(timezone.utc)
    timestamp_str = timestamp_utc.strftime("%Y%m%dT%H%M%S")
    # Sanitize and truncate event_name so the filename is always filesystem-safe.
    safe_event = _sanitize_filename(item.event_name)[:64] if item.event_name else "event"
    zip_name = f"{safe_event}-{timestamp_str}-{uuid8}.zip"

    tmp_dir = Path(tempfile.mkdtemp(prefix="dc_"))
    try:
        payload_meta: dict = {}
        extra_files: list = []  # list of (arcname, abs_path)

        for key, value in item.payload.items():
            abs_path = None
            if value is None or isinstance(value, (str, int, float, bool)):
                payload_meta[key] = value

            elif isinstance(value, numpy.ndarray):
                if item.image_format.upper() == "HDF5":
                    exporter = hdf5
                elif item.image_format.upper() == "TIFF":
                    exporter = tiff
                else:
                    logging.warning("DataArray not in valid format", exc_info=True)
                    exporter = None

                if exporter is not None:
                    ext = exporter.EXTENSIONS[0]
                    arc_name = f"{_sanitize_filename(key)}.{ext}"
                    abs_path = tmp_dir / arc_name
                    try:
                        da = value if isinstance(value, model.DataArray) else model.DataArray(value)
                        tiff.export(str(abs_path), da)
                    except Exception:
                        logging.warning("Failed to export DataArray to %s at %s",  ext, abs_path, exc_info=True)
                        abs_path = None

                if abs_path is not None and abs_path.exists():
                    extra_files.append((arc_name, abs_path))
                    payload_meta[key] = arc_name
                else:
                    payload_meta[key] = None
                    payload_meta["export_error"] = True

            elif isinstance(value, (dict, list)):
                arc_name = f"extra_{_sanitize_filename(key)}.json"
                abs_path = tmp_dir / arc_name
                abs_path.write_text(json.dumps(value, default=str), encoding="utf-8")
                extra_files.append((arc_name, abs_path))
                payload_meta[key] = arc_name

            else:
                # Fallback: store string representation, guarding against
                # __repr__/__str__ implementations that raise.
                try:
                    payload_meta[key] = str(value)
                except Exception:
                    logging.warning("Failed to convert payload key '%s' to string", key, exc_info=True)
                    payload_meta[key] = "<unserializable>"

        metadata = {
            "sample_uuid": sample_uuid,
            "timestamp_utc": timestamp_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "system_id": socket.gethostname(),
            "odemis_version": odemis.__version__,
            "event_name": item.event_name,
            "schema_version": item.schema_version,
            "payload": payload_meta,
        }

        meta_path = tmp_dir / "metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        # Build ZIP in temp dir, then rename atomically into queue_dir.
        tmp_zip = queue_dir / f"{uuid8}.tmp"
        final_zip = queue_dir / zip_name
        with zipfile.ZipFile(str(tmp_zip), "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(meta_path), "metadata.json")
            for arc_name, abs_path in extra_files:
                zf.write(str(abs_path), arc_name)

        os.replace(str(tmp_zip), str(final_zip))
        return final_zip

    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def _enforce_queue_limit(queue_dir: Path) -> None:
    """
    Delete the oldest ZIP files if the queue exceeds 10% of partition space.
    :param queue_dir: The staging directory to inspect.
    """
    if not queue_dir.exists():
        return

    zips = sorted(queue_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        return

    try:
        usage = shutil.disk_usage(str(queue_dir))
    except OSError:
        logging.warning("Cannot read disk usage for %s", queue_dir)
        return

    limit = usage.total * 0.10  # 10 % of partition
    total_size = sum(p.stat().st_size for p in zips)

    while total_size > limit and zips:
        oldest = zips.pop(0)
        try:
            size = oldest.stat().st_size
            oldest.unlink()
            total_size -= size
            logging.info("Queue limit exceeded: removed oldest sample %s", oldest.name)
        except OSError:
            logging.warning("Could not remove queue file %s", oldest)


class S3UploadBackend:
    """S3 upload backend implemented with boto3."""

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        endpoint_url: Optional[str] = S3_ENDPOINT_URL,
        region: str = S3_REGION,
        bucket: str = S3_BUCKET,
    ) -> None:
        """
        Initialize the S3 upload backend with the given credentials and configuration.
        :param access_key: AWS access key ID.
        :param secret_key: AWS secret access key.
        :param endpoint_url: Optional S3 endpoint URL (for custom S3-compatible storage).
        :param region: AWS region name (default "eu-west-1").
        :param bucket: S3 bucket name to upload to (default "delmic-odemis-collect").
        """
        self._access_key = access_key
        self._secret_key = secret_key
        self._endpoint_url = endpoint_url
        self._region = region
        self._bucket = bucket
        self._client = None

    def _get_client(self) -> "boto3.client":
        """
        Get a cached boto3 S3 client.
         :return: boto3 S3 client instance.
        """
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self._endpoint_url,
                region_name = self._region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
            )
        return self._client

    def upload(self, local_path: Path, remote_key: str) -> None:
        """Upload local_path to remote_key in the configured bucket."""
        client = self._get_client()
        client.upload_file(str(local_path), self._bucket, remote_key)


def _upload(zip_path: Path, backend: S3UploadBackend) -> None:
    """Upload zip_path with backend using the standard remote key."""
    remote_key = f"{socket.gethostname()}/{zip_path.name}"
    backend.upload(zip_path, remote_key)


class _BackgroundWorker:
    """
    Daemon thread that consumes WorkItem objects from a queue.
    For each item it calls _enforce_queue_limit, _serialize,
    and _upload in sequence.  After a successful upload the local ZIP
    is deleted.  Exceptions are caught and logged, but never directly shown to the user.
    The thread is started lazily and restarted automatically if it dies.
    """

    def __init__(self, config: DataCollectorConfig, queue_dir: Path = _DEFAULT_QUEUE_DIR) -> None:
        self._config = config
        self._queue_dir = queue_dir
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._upload_backend: Optional[S3UploadBackend] = None
        self._next_retry_at: float = 0.0
        self._retry_delay: float = _INITIAL_RETRY_DELAY_SECONDS
        """
        Initialize the background worker with the given configuration and queue directory.
        :param config: DataCollectorConfig instance for accessing configuration and upload backend.
        :param queue_dir: Directory where ZIP files are staged for upload (default /var/log
         /odemis/dc_queue).
        """

    def enqueue(self, item: _WorkItem) -> None:
        """Add item to the processing queue and ensure the thread is alive.
        :param item: The work item to enqueue.
        """
        self._ensure_thread()
        self._queue.put_nowait(item)

    def _ensure_thread(self) -> None:
        """Start the background thread if it's not already running."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="DataCollectorWorker",
                    daemon=True,
                )
                self._thread.start()
                logging.debug("DataCollector background thread started.")

    def _get_upload_backend(self) -> S3UploadBackend:
        """Return a cached upload backend."""
        if self._upload_backend is None:
            self._upload_backend = self._config.get_upload_backend()
        return self._upload_backend

    def _schedule_retry(self) -> None:
        """Schedule the next retry using exponential backoff."""
        delay = self._retry_delay
        self._next_retry_at = time.monotonic() + delay
        self._retry_delay = min(self._retry_delay * 2.0, _MAX_RETRY_DELAY_SECONDS)
        logging.warning("DataCollector upload failed; retrying in %.0f s", delay)

    def _reset_retry(self) -> None:
        """Reset retry state after a successful upload."""
        self._next_retry_at = 0.0
        self._retry_delay = _INITIAL_RETRY_DELAY_SECONDS

    def _pending_zip_paths(self, queue_dir: Path) -> list[Path]:
        """Return pending ZIP files ordered oldest-first."""
        if not queue_dir.exists():
            return []
        return sorted(queue_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)

    def _process_pending_zips(self, queue_dir: Path) -> bool:
        """
        Upload pending ZIP files from queue_dir.
        :param queue_dir: Directory to scan for pending ZIP files.
        :return: True when pending work existed (including backoff wait),
         otherwise False.
        """
        pending = self._pending_zip_paths(queue_dir)
        if not pending:
            return False

        now = time.monotonic()
        if now < self._next_retry_at:
            time.sleep(min(1.0, self._next_retry_at - now))
            return True

        try:
            backend = self._get_upload_backend()
        except Exception:
            logging.warning("DataCollector failed to initialize upload backend", exc_info=True)
            self._schedule_retry()
            return True
        for zip_path in pending:
            try:
                _upload(zip_path, backend)
                zip_path.unlink(missing_ok=True)
                self._reset_retry()
            except Exception:
                logging.warning(
                    "DataCollector upload failed for %s", zip_path.name, exc_info=True
                )
                self._schedule_retry()
                return True
        return True

    def _process_work_item(self, item: _WorkItem) -> None:
        """Serialize one work item and trigger upload processing."""
        _enforce_queue_limit(self._queue_dir)
        _serialize(item, self._queue_dir)
        self._process_pending_zips(self._queue_dir)

    def _run(self) -> None:
        """Main loop: process items until the thread is stopped."""
        while True:
            # Always drain the in-memory queue first with a non-blocking get so
            # new record() calls are serialised to disk even when upload backoff
            # is in progress.  Without this, the queue grows unbounded and
            # queue.get() is never reached while pending ZIPs exist.
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                item = None

            if item is not None:
                try:
                    self._process_work_item(item)
                except Exception:
                    logging.warning("DataCollector error processing event '%s'",
                                    item.event_name, exc_info=True)
                continue  # immediately check for more queued items

            # No in-memory items; try to upload pending ZIPs from disk.
            try:
                had_pending = self._process_pending_zips(self._queue_dir)
            except Exception:
                logging.warning("DataCollector error while processing pending uploads", exc_info=True)
                self._schedule_retry()
                had_pending = True

            if had_pending:
                continue

            # Nothing pending; block briefly for new items to arrive.
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process_work_item(item)
            except Exception:
                logging.warning(
                    "DataCollector error processing event '%s'", item.event_name, exc_info=True
                )


class DataCollector:
    """Thread-safe recorder for annotated data samples."""

    def __init__(self) -> None:
        self._config: Optional[DataCollectorConfig] = None
        self._worker: Optional[_BackgroundWorker] = None
        self._init_ok: bool = False
        self._init_lock = threading.Lock()
        # Collect percentage of the acquired data based on the selected probability
        # in order to keep the growth of the collected data in control.
        self.probability = _DEFAULT_COLLECTION_PROBABILITY

    def _lazy_init(self) -> None:
        """Initialise configuration and worker on first use."""
        if self._init_ok:
            return
        with self._init_lock:
            if self._init_ok:
                return
            try:
                self._config = DataCollectorConfig()
                self._worker = _BackgroundWorker(self._config)
                self._init_ok = True
                logging.debug("DataCollector initialised.")
            except Exception:
                logging.warning(
                    "DataCollector failed to initialise; all record() calls will be no-ops."
                )

    def get_consent(self) -> Optional[bool]:
        """Return current consent state and auto-expire temporary consent when needed."""
        self._lazy_init()
        if not self._init_ok:
            return None
        consent = self._config.consent
        if consent is not True:
            return consent

        consent_day = self._config.consent_date
        if consent_day is None:
            return True

        today_local = datetime.now().astimezone().date()
        if today_local > consent_day:
            self._config.consent = False
            logging.info("DataCollector: temporary consent expired; consent set to False.")
            return False
        return True

    def set_consent(self, value: bool) -> None:
        """
        Persist explicit user consent choice.
        :param value: Boolean indicating user's consent choice.
        """
        if not isinstance(value, bool):
            raise ValueError("value must be a bool")
        self._lazy_init()
        if not self._init_ok:
            return
        self._config.consent = value

    def set_temporary_consent(self, days: int = 1) -> None:
        """
        Enable consent for the specified number of days, after which it
        auto-expires to False.

        When days == 1, the remaining time at the moment record() is called
        will be ≤ 1 day, so 100% collection probability applies throughout.
        When days > 1, the default 10% probability applies until the final day.

        :param days: Number of days the temporary consent is active.
        """
        self._lazy_init()
        if not self._init_ok:
            return
        consent_day = datetime.now().astimezone().date() + timedelta(days=days)
        self._config.set_consent_with_expiry(consent_date=consent_day)

    def record(
        self,
        event_name: str,
        schema_version: str,
        payload: dict,
        image_format: str = "TIFF",
    ) -> None:
        """
        Capture an annotated data sample at a software event.
        Returns immediately (non-blocking). Serialisation and upload happen
        asynchronously in a background thread.  If consent has not been
        granted, this is a no-op.  This function never raises (beyond the
        input validation below); all errors are logged and suppressed.

        Collection probability is applied before enqueuing: 100% when
        temporary consent (1-day) is active, 10% otherwise.

        :param event_name: Human-readable event identifier, e.g.
         "z_stack_acquired".  Must be a non-empty string.
        :param schema_version: Payload schema version string, e.g. "1.0".
         Must be a non-empty string.
        :param payload: Dict of arbitrary values.  Must be a dict.  Supported
         value types:
         - Python primitives (str, int, float, bool, None) — inlined in
           metadata.json
         - :class:odemis.model.DataArray / :class:numpy.ndarray —
            exported as TIFF or HDF5 files
            - dict / list — written as extra_<key>.json
        :param image_format: Format for DataArray export.  "TIFF"
         (default) or "HDF5".
        :raises ValueError: If any input parameter is invalid.
        """
        if not isinstance(event_name, str) or not event_name:
            raise ValueError("event_name must be a non-empty string")
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError("schema_version must be a non-empty string")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        if not isinstance(image_format, str) or image_format.upper() not in _VALID_IMAGE_FORMATS:
            raise ValueError(
                f"image_format must be one of {_VALID_IMAGE_FORMATS}, got {image_format!r}"
            )

        try:
            self._lazy_init()
            if not self._init_ok:
                return

            consent = self.get_consent()
            if not consent:
                logging.debug(
                    "DataCollector: consent=%s, skipping event '%s'.", consent, event_name
                )
                return

            # Apply collection probability: 100% if consent expires within 1 day,
            # 10% otherwise (no consent_date = permanent opt-in).
            consent_day = self._config.consent_date
            if consent_day is not None:
                days_left = (consent_day - datetime.now().astimezone().date()).days
            else:
                days_left = None

            if days_left is not None and days_left <= 1:
                self.probability = _FULL_COLLECTION_PROBABILITY
            else:
                self.probability = _DEFAULT_COLLECTION_PROBABILITY

            item = _WorkItem(
                event_name=event_name,
                schema_version=schema_version,
                payload=payload,
                image_format=image_format,
            )
            self._worker.enqueue(item)
        except Exception:
            logging.warning(
                "Unexpected error in DataCollector.record(); event '%s' dropped.", event_name, exc_info=True
            )
