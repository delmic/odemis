Odemis Data Collection Framework — Setup Guide
===============================================

**Audience:** Software and support engineers setting up data collection on an
Odemis installation or on a developer workstation.

.. note::
   All commands are Bash. Enter them line by line unless stated otherwise.

This guide covers:

- Prerequisites
- Installing the boto3 package
- Creating and installing the credentials key file
- Setting up the local queue directory (``dc_queue``)
- Verifying the configuration file
- Test-bucket vs production-bucket mode
- Running the unit tests
- Quick smoke-test with ``odemis-dc-fetch``
- Troubleshooting


Prerequisites
-------------

- Ubuntu 22.04 LTS or later
- Odemis installed from the Debian package or a source checkout
- AWS S3 credentials (``access_key`` + ``secret_key``) for the target bucket.
  Obtain these from a Delmic software engineer; they are **not** stored in this
  repository.
- Write access to ``/usr/share/odemis/`` (for the key file)


Installing the boto3 Package
-----------------------------

``boto3`` is the AWS SDK for Python used to upload data to S3.

When installed via the Odemis Debian package it is listed as a dependency and
will be pulled in automatically:

.. code-block:: bash

   sudo apt update
   sudo apt install python3-boto3

To verify the installation:

.. code-block:: bash

   python3 -c "import boto3; print(boto3.__version__)"

If the command prints a version string (e.g. ``1.28.0``) the package is ready.
If it raises ``ImportError``, re-run the ``apt install`` command above.


Installing the Credentials Key File
-------------------------------------

The framework reads S3 credentials from a single JSON file at: /usr/share/odemis/datacollector.key

The file must contain exactly the following two keys:

.. code-block:: json

   { "access_key": "<AWS_ACCESS_KEY_ID>", "secret_key": "<AWS_SECRET_ACCESS_KEY>" }

Obtain the actual key values from a Delmic software engineer. Do **not** commit
them to any repository or share them over unencrypted channels.


Production Setup
~~~~~~~~~~~~~~~~

Contact a software engineer who has access to the AWS IAM console to get the
key pair, then run the following commands line by line:

.. code-block:: bash

   # Create the file — replace placeholder values with the real keys
   sudo tee /usr/share/odemis/datacollector.key > /dev/null << 'EOF'
   { "access_key": "<PRODUCTION_ACCESS_KEY_ID>", "secret_key": "<PRODUCTION_SECRET_ACCESS_KEY>" }
   EOF

   # Restrict access: readable only by root and the odemis process user
   sudo chmod 600 /usr/share/odemis/datacollector.key
   sudo chown root:root /usr/share/odemis/datacollector.key


Test / Developer Setup
~~~~~~~~~~~~~~~~~~~~~~

Use the test IAM credentials scoped to ``delmic-odemis-collect-test``. Ask a
software engineer for the test key pair. The setup steps are identical:

.. code-block:: bash

   # Create the file — replace placeholder values with the real keys
   sudo tee /usr/share/odemis/datacollector.key > /dev/null << 'EOF'
   { "access_key": "<TEST_ACCESS_KEY_ID>", "secret_key": "<TEST_SECRET_ACCESS_KEY>" }
   EOF

   # Restrict access: readable only by root and the odemis process user
   sudo chmod 600 /usr/share/odemis/datacollector.key
   sudo chown root:root /usr/share/odemis/datacollector.key

.. important::
   Even with test credentials installed, the framework still targets the
   **production** bucket by default. You must also set the environment variable
   described in :ref:`test-bucket-mode` to redirect uploads to the test bucket.


Setting up the Local Queue Directory (dc_queue)
-------------------------------------------------

.. note::
   This section applies to the **test / developer setup** only. Skip it for a
   production installation.

The framework stages serialised ZIP archives in a local directory before
uploading them. The default path is:

.. code-block:: text

   ~/.local/share/odemis/dc_queue

This directory is created automatically by the framework at runtime if it does
not exist, as long as the parent ``~/.local/share/odemis/`` is writable by the
process.

For a standard Odemis installation ``~/.local/share/odemis/`` is already
created by the package post-install script. If that is not the case, create it
manually:

.. code-block:: bash

   sudo mkdir -p ~/.local/share/odemis/dc_queue
   sudo chown $USER:$USER ~/.local/share/odemis/dc_queue
   sudo chmod 750 ~/.local/share/odemis/dc_queue


Queue Disk Limit
~~~~~~~~~~~~~~~~

The framework automatically enforces a soft limit of 10 % of the partition's
total disk space on ``~/.local/share/odemis/dc_queue``. When the limit is
exceeded, the oldest ZIP files are deleted with a ``WARNING`` log entry.

- **Production:** ``~/.local/share/odemis/`` is normally on the main OS
  partition. No special configuration is required.
- **Test / developer workstation:** the queue directory may be redirected to a
  temporary location by instantiating ``_BackgroundWorker`` with a custom
  ``queue_dir`` argument (used in unit tests). For manual testing with the real
  Odemis GUI, the default path is always used.


Inspecting Queued Files
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   ls -lh ~/.local/share/odemis/dc_queue/

Files follow the naming convention::

   <event_name>-<YYYYMMDDTHHmmss>-<uuid8>.zip

Any file ending in ``.tmp`` is an incomplete write left over from a crash; the
framework removes such files on startup. You can delete them manually if Odemis
is not running:

.. code-block:: bash

   rm -f ~/.local/share/odemis/dc_queue/*.tmp


Verifying the Configuration File
----------------------------------

The per-user consent state is stored in:

.. code-block:: text

   ~/.config/odemis/datacollector.config

This file is created automatically the first time the user interacts with the
consent dialog (opt in, opt out, or remind later). Its permissions are always
set to ``0600`` (owner read/write only) by the framework.

A typical file after opt-in looks like:

.. code-block:: ini

   [general]
   # Data sharing consent (none / true / false).
   consent = true

To check the current consent state without starting Odemis:

.. code-block:: bash

   python3 -c "
   from odemis.util.datacollector import DataCollectorConfig
   cfg = DataCollectorConfig()
   print('consent:', cfg.consent)
   print('should_prompt:', cfg.should_prompt_for_consent())
   "

To manually reset consent (forces the dialog to appear on the next launch):

.. code-block:: bash

   python3 -c "
   from odemis.util.datacollector import DataCollectorConfig
   cfg = DataCollectorConfig()
   cfg.clear_consent()
   print('Consent cleared.')
   "


.. _test-bucket-mode:

Test-Bucket vs Production-Bucket Mode
---------------------------------------

Production (Default)
~~~~~~~~~~~~~~~~~~~~~

- **Bucket:** ``delmic-odemis-collect``
- **Region:** ``eu-west-1``

This is the default when the framework starts normally. No environment variable
needs to be set. Use the production IAM credentials in the key file
(see `Installing the Credentials Key File`_).


Test / Developer Mode
~~~~~~~~~~~~~~~~~~~~~~

- **Bucket:** ``delmic-odemis-collect-test``
- **Region:** ``eu-west-1``

Set the following environment variable before starting Odemis (or any script
that calls ``record()``):

.. code-block:: bash

   export TEST_DATACOLLECTION=1

The framework logs the following ``INFO`` message when test mode is active::

   DataCollector: TEST_DATACOLLECTION=1 — using test bucket 'delmic-odemis-collect-test'

To run data collection within a single shell session in test mode:

.. code-block:: bash

   TEST_DATACOLLECTION=1 python3 <your_script_or_odemis_launcher>

.. important::
   The test bucket credentials (``access_key`` / ``secret_key`` in the key
   file) must be scoped to the **test** bucket by the Delmic AWS admin. If you
   install production credentials and set ``TEST_DATACOLLECTION=1``, uploads
   will fail with an ``AccessDenied`` error because the IAM policy only permits
   writes to the production prefix.


Running the Unit Tests
-----------------------

Unit tests run without any hardware and without a real S3 connection. All
upload calls are mocked.

.. code-block:: bash

   # From the repository root
   env TEST_NOHW=1 python3 src/odemis/util/test/datacollector_test.py

To run a specific test class or method:

.. code-block:: bash

   env TEST_NOHW=1 python3 src/odemis/util/test/datacollector_test.py \
       DataCollectorTest.test_record_returns_fast

   env TEST_NOHW=1 python3 src/odemis/util/test/datacollector_test.py \
       TestSerialize.test_metadata_json_envelope_fields


Real S3 Integration Tests
~~~~~~~~~~~~~~~~~~~~~~~~~~

The class ``TestRealS3Integration`` uploads to the test bucket and cleans up
after itself. It requires:

- ``boto3`` installed
- ``/usr/share/odemis/datacollector.key`` present with test bucket credentials
- ``TEST_DATACOLLECTION=1`` is **not** needed here — the test class hard-codes
  the test bucket directly

.. code-block:: bash

   env TEST_NOHW=1 python3 src/odemis/util/test/datacollector_test.py \
       TestRealS3Integration

If the key file is absent, this class is skipped automatically.


Quick Smoke-Test with odemis-dc-fetch
---------------------------------------

After a successful upload (production or test), use the retrieval script to
confirm objects landed in S3. The retrieval script can only be used with an AWS
profile that has data-analyst read access; the ``access_key`` / ``secret_key``
in the key file determine whether fetching is permitted.

.. code-block:: bash

   odemis-dc-fetch \
       --bucket delmic-odemis-collect \
       --region eu-west-1 \
       --output ./dc_samples_test

Filter by event name and date:

.. code-block:: bash

   odemis-dc-fetch \
       --bucket delmic-odemis-collect-test \
       --region eu-west-1 \
       --event feature_collected \
       --since 2026-04-01 \
       --output ./dc_samples_test

The script prints a one-line summary::

   listed=N matched=N downloaded=N skipped_existing=N failed=0

If ``failed`` is non-zero, check the log output for ``AccessDenied`` or network
errors and verify the key file credentials.


Troubleshooting
----------------

**Problem:** ``boto3 ImportError`` when starting Odemis.

**Solution:** ``sudo apt install python3-boto3``

----

**Problem:** ``LookupError: S3 credentials key file not found at /usr/share/odemis/datacollector.key``

**Solution:** Create the key file as described in
`Installing the Credentials Key File`_.

----

**Problem:** ``botocore.exceptions.ClientError: AccessDenied``

**Cause:** The IAM key in the key file does not have permission to write to the
bucket being targeted.

**Solution:** Ensure the key file contains credentials matching the target
bucket (production key → production bucket, test key → test bucket). Check
whether ``TEST_DATACOLLECTION=1`` is set unexpectedly.

----

**Problem:** Uploads never happen; queue fills up.

**Cause:** Network is unavailable or credentials are wrong.

**Solution:** Check the Odemis log for ``DataCollector upload failed`` entries.
The framework retries with exponential back-off (30 s → 60 s → ... up to 1 h).
Pending ZIPs remain in ``~/.local/share/odemis/dc_queue/`` and are flushed
oldest-first once connectivity is restored.

----

**Problem:** ``Queue limit exceeded: removed oldest sample`` appears in the log.

**Cause:** The queue directory has grown beyond 10 % of the partition.

**Solution:** Check disk space with ``df -h ~/.local/share/odemis/dc_queue``.
Consider moving ``~/.local/share/odemis/`` to a larger partition, or
investigate why uploads are not succeeding (credentials, network).

----

**Problem:** Consent dialog does not appear on first launch.

**Cause:** ``~/.config/odemis/datacollector.config`` already contains a consent
value (e.g. from a previous installation).

**Solution:** Reset consent manually (see `Verifying the Configuration File`_)
or delete the config file:

.. code-block:: bash

   rm ~/.config/odemis/datacollector.config

----

**Problem:** ``TEST_DATACOLLECTION=1`` is set but uploads still go to
production.

**Solution:** This cannot happen — the environment variable is read at the
moment ``get_upload_backend()`` is called, which is lazy (first upload attempt).
Verify the variable is exported in the same shell/environment that runs the
Odemis process.

----

**Problem:** ``*.tmp`` files accumulate in ``dc_queue``.

**Cause:** Odemis crashed mid-write.

**Solution:** Stop Odemis, then:

.. code-block:: bash

   rm -f ~/.local/share/odemis/dc_queue/*.tmp

The framework also cleans these up automatically on the next startup.
