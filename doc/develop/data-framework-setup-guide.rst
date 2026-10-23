Odemis Data Collection Framework
===============================================

**Audience:** Software and support engineers setting up data collection on an
Odemis installation or on a developer workstation.

This guide covers:

- Prerequisites
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
- AWS S3 credentials (``access_key`` + ``secret_key``) for the target bucket
  (typically upload-only IAM keys).
  Obtain these from a Delmic software engineer; they are **not** stored in this
  repository.


Installing the Credentials Key File
-------------------------------------

The framework reads S3 credentials from a single JSON file at: /usr/share/odemis/datacollector.key

The file must contain exactly the following two keys:

.. code-block:: json

   { "access_key": "<AWS_ACCESS_KEY_ID>", "secret_key": "<AWS_SECRET_ACCESS_KEY>" }

Obtain the actual key values from a Delmic software engineer. These keys have
upload-only access (no read/retrieve), so internal sharing
over standard channels is lower risk than full-access AWS credentials.

Although these keys can be seen as public, do **not** commit these keys to git repositories
(especially public remotes).
Public repository scrapers continuously harvest AWS key patterns, and committing
keys can also trigger GitHub secret-scanning/security warnings.

Note that the same key file is used for both production and test-buckets, for data collection.
For fetching the data back for analysis, a different key file is needed. These keys are per user,
and should be treated as private.


Production Setup
~~~~~~~~~~~~~~~~

Contact a software engineer who has access to the AWS IAM console to get the
key pair, then run the following commands line by line:

.. code-block:: bash

   # Create the file — replace placeholder values with the real keys
   sudo tee /usr/share/odemis/datacollector.key > /dev/null << 'EOF'
   { "access_key": "<PRODUCTION_ACCESS_KEY_ID>", "secret_key": "<PRODUCTION_SECRET_ACCESS_KEY>" }
   EOF


Local Queue Directory (dc_queue)
-------------------------------------------------

The framework stages serialised ZIP archives in a local directory before
uploading them. The path is ``~/.local/share/odemis/dc_queue``.


The framework automatically enforces a soft limit of 10 % of the partition's
total disk space on ``~/.local/share/odemis/dc_queue``. When the limit is
exceeded, the oldest ZIP files are deleted with a ``WARNING`` log entry.

Inspecting queued files:

.. code-block:: bash

   ls -lh ~/.local/share/odemis/dc_queue/

Files follow the naming convention::

   <event_name>-<YYYYMMDDTHHmmss>-<uuid8>.zip

Any file ending in ``.tmp`` is an incomplete write left over from a crash; the
framework removes such files on startup.


Setting the configuration file
----------------------------------

The per-organisation data sharing acceptance is stored in:

.. code-block:: text

   ~/.config/odemis/data_sharing.config

This file is created automatically, with ``acquisition_analytics = false``, if
it doesn't exist when Odemis starts. Afterwards, Odemis never modifies it. It
is read only once, at start-up, so it must be edited before Odemis is
started.

A typical file looks like:

.. code-block:: ini

   [general]
   acquisition_analytics = true


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

.. important::
   The test bucket credentials (``access_key`` / ``secret_key`` in the key
   file) must be scoped to the **test** bucket by the Delmic AWS admin. If you
   install production credentials and set ``TEST_DATACOLLECTION=1``, uploads
   will fail with an ``AccessDenied`` error because the IAM policy only permits
   writes to the production prefix.


Managing Consent
----------------

The default consent is defined by ``acquisition_analytics`` in the
configuration file (see above), and is reported by ``get_consent_default()``.
``get_consent()`` reports the consent currently in use.

In the Odemis GUI (METEOR and MIMAS), the menu *Help > Data sharing* shows the
current status (Enabled, Disabled, Enabled for the session, or Disabled for the
session). The entry *Change...* opens a window allowing to either keep the
default or to override it for the session. The override is only kept in memory
(``set_temporary_consent()``): it ends when Odemis is closed, and the
configuration file is never changed.

The default sampling rate is 10%. When the consent is enabled for the session
(whereas the default is disabled), the sampling rate increases to 100%.

When consent is active, collected data is staged in the queue directory and
uploaded to S3 in the background. When consent is disabled, no data is collected
or uploaded.


Running the Unit Tests
-----------------------

Unit tests run without any hardware and without a real S3 connection. All
upload calls are mocked.

.. code-block:: bash

   # From the repository root
   env TEST_NOHW=1 python3 src/odemis/util/test/datacollector_test.py


Real S3 Integration Tests
~~~~~~~~~~~~~~~~~~~~~~~~~~

The class ``TestRealS3Integration`` uploads to the test bucket and cleans up
after itself. It requires:

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
profile that has data-analyst read access; the upload key in
``/usr/share/odemis/datacollector.key`` is typically write-only and usually
does not grant retrieval rights.

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
Investigate why uploads are not succeeding (credentials, network).

----

**Problem:** ``TEST_DATACOLLECTION=1`` is set but uploads still go to
production.

**Solution:** The environment variable is read at the
moment ``get_upload_backend()`` is called, which is lazy (first upload attempt).
Verify the variable is exported in the same shell/environment that runs the
Odemis process.
