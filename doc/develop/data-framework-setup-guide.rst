Odemis Data Collection Framework — Setup Guide
==============================================

| Audience : Software / support engineers setting up data collection on
  an
| Odemis installation or on a developer workstation.

Note - all commands are in bash, all the lines need to be entered line
by line unless mentioned otherwise

| This guide covers:
| Prerequisites
| Installing the boto3 package
| Creating and installing the credentials key file
| Setting up the local queue directory (dc_queue)
| Verifying the configuration file
| Test-bucket vs production-bucket mode
| Running the unit tests
| Quick smoke-test with odemis-dc-fetch
| Troubleshooting

Prerequisites
----------------

- Ubuntu 22.04 LTS (or later)
- Odemis installed from the debian package (or source checkout)
- AWS S3 credentials (access_key + secret_key) for the target bucket. Obtain
  these from a Delmic software engineer; they are NOT stored in this repository.
- Write access to /usr/share/odemis/ (for the key file)

Installing the boto3 package
-------------------------------

boto3 is the AWS SDK for Python used to upload data to S3.

| When installed via the Odemis debian package it is listed as a
  dependency and
| will be pulled in automatically:

::

   sudo apt update  
   sudo apt install python3-boto3

To verify the installation:

::

   python3 \-c "import boto3; print(boto3.\_\_version\_\_)"

| If the command prints a version string (e.g. “1.28.0”) the package is
  ready.
| If it raises ImportError, re-run the apt install command above.

Installing the credentials key file
--------------------------------------

The framework reads S3 credentials from a single JSON file at:

::

   /usr/share/odemis/datacollector.key

The file must contain exactly the following two keys:

::

   { "access\_key": "\<AWS\_ACCESS\_KEY\_ID\>","secret\_key":"\<AWS\_SECRET\_ACCESS\_KEY\>" }

| Obtain the actual key values from a Delmic software engineer. Do NOT
  commit
| them to any repository or share them over unencrypted channels.

-——— PRODUCTION SETUP -———

| Contact a software engineer who has access to the AWS IAM console
| to get key pair.

Steps:

::

   \# Create the file (replace the placeholder values with the real keys) line by line

1. | sudo tee /usr/share/odemis/datacollector.key > /dev/null << ‘EOF’

2. { “access_key”: “<PRODUCTION_ACCESS_KEY_ID>”,“secret_key”:
   “<PRODUCTION_SECRET_ACCESS_KEY>”}

   EOF

   | # Restrict access: readable only by root and the odemis process
     user in bash (Can be copied together)
   | sudo chmod 600 /usr/share/odemis/datacollector.key
   | sudo chown root:root /usr/share/odemis/datacollector.key

-——— TEST / DEVELOPER SETUP -———

| Use the test IAM credentials (scoped to “delmic-odemis-collect-test”).
  Ask a
| software engineer for the test key pair. The setup steps are
  identical:

::

   \# Create the file (replace the placeholder values with the real keys) line by line

3. | sudo tee /usr/share/odemis/datacollector.key > /dev/null << ‘EOF’

4. { “access_key”: “<TEST_ACCESS_KEY_ID>”,“secret_key”:
   “<TEST_SECRET_ACCESS_KEY>”}

   EOF

   | # Restrict access: readable only by root and the odemis process
     user in bash (Can be copied together)
   | sudo chmod 600 /usr/share/odemis/datacollector.key
   | sudo chown root:root /usr/share/odemis/datacollector.key

| **IMPORTANT**: Even with test credentials installed, the framework
  still targets
| the PRODUCTION bucket by default. You must also set the environment
  variable
| described in Section 6 to redirect uploads to the test bucket.

.. _section-1:

Setting up the local queue directory (dc_queue)
--------------------------------------------------

| Skip for Production setup
| -——— TEST / DEVELOPER SETUP -———
| The framework stages serialised ZIP archives in a local directory
  before
| uploading them. The default path is:

::

   /.local/share/odemis/dc\_queue

| This directory is created automatically by the framework at runtime if
  it does
| not exist, as long as the parent /.local/share/odemis/ is writable by the
  process.

| For a standard Odemis installation /.local/share/odemis/ is already created
  by the
| package post-install script. If that is not the case, create it
  manually:

::

   sudo mkdir \-p /.local/share/odemis/dc\_queue  
   sudo chown $USER:$USER /.local/share/odemis/dc\_queue  
   sudo chmod 750 /.local/share/odemis/dc\_queue

-——— Queue disk limit -———

| The framework automatically enforces a soft limit of 10 % of the
  partition’s
| total disk space on /.local/share/odemis/dc_queue. When the limit is
  exceeded, the
| oldest ZIP files are deleted with a WARNING log entry.

- Production: /.local/share/odemis/ is normally on the main OS partition.
  No special configuration is required.

- Test / developer workstation: the queue directory may be redirected to a
  temporary location by instantiating `_BackgroundWorker` with a custom
  `queue_dir` argument (used in unit tests). For manual testing with the
  real Odemis GUI, the default path is always used.

-——— Inspecting queued files -———

::

   ls \-lh /.local/share/odemis/dc\_queue/

| Files follow the naming convention:
| <event_name>-<YYYYMMDDTHHmmss>-<uuid8>.zip

| Any file ending in “.tmp” is an incomplete write left over from a
  crash; the
| framework removes such files on startup. You can delete them manually
  if
| Odemis is not running:

::

   rm \-f /.local/share/odemis/dc\_queue/\*.tmp

Verifying the configuration file
-----------------------------------

The per-user consent state is stored in:

::

   \~/.config/odemis/datacollector.config

| This file is created automatically the first time the user interacts
  with the
| consent dialog (opt in, opt out, or remind later). Its permissions are
  always
| set to 0600 (owner read/write only) by the framework.

A typical file after opt-in looks like:

::

   \[general\]  
   \# Data sharing consent (none / true / false).  
   consent \= true  
   \#  
   \# Date after which the consent dialog will be shown again (YYYY-MM-DD).  
   \# reminder\_date \=

To check the current consent state without starting Odemis (copied
together in bash):

::

   python3 \-c "  
   from odemis.util.datacollector import DataCollectorConfig  
   cfg \= DataCollectorConfig()  
   print('consent:', cfg.consent)  
   print('should\_prompt:', cfg.should\_prompt\_for\_consent())  
   "

To manually reset consent (forces the dialog to appear on the next
launch, copied together in bash):

::

   python3 \-c "  
   from odemis.util.datacollector import DataCollectorConfig  
   cfg \= DataCollectorConfig()  
   cfg.clear\_consent()  
   print('Consent cleared.')  
   "

Test-bucket vs production-bucket mode
----------------------------------------

-——— Production (default) -———

| Bucket : delmic-odemis-collect
| Region : eu-west-1

| This is the default when the framework starts normally. No environment
| variable needs to be set. Use the production IAM credentials in the
  key file
| (Section 3).

-——— Test / developer mode -———

| Bucket : delmic-odemis-collect-test
| Region : eu-west-1

| Set the following environment variable before starting Odemis (or any
  script
| that calls record()):

::

   export TEST\_DATACOLLECTION=1

The framework logs the following INFO message when test mode is active:

::

   DataCollector: TEST\_DATACOLLECTION=1 — using test bucket 'delmic-odemis-collect-test'

To run the data collection within a single shell session in test mode:

::

   TEST\_DATACOLLECTION=1 python3 \<your\_script\_or\_odemis\_launcher\>

| IMPORTANT: The test bucket credentials (access_key / secret_key in the
  key
| file) must be scoped to the TEST bucket by the Delmic AWS admin. If
  you
| install production credentials and set TEST_DATACOLLECTION=1, uploads
  will
| fail with an AccessDenied error because the IAM policy only permits
  writes to
| the production prefix.

Running the unit tests
-------------------------

| Unit tests run without any hardware and without a real S3 connection.
| All upload calls are mocked.

::

   \# From the repository root  
   env TEST\_NOHW=1 python3 src/odemis/util/test/datacollector\_test.py

To run a specific test class or method:

::

   env TEST\_NOHW=1 python3 src/odemis/util/test/datacollector\_test.py \\  
       DataCollectorTest.test\_record\_returns\_fast

   env TEST\_NOHW=1 python3 src/odemis/util/test/datacollector\_test.py \\  
       TestSerialize.test\_metadata\_json\_envelope\_fields

-——— Real S3 integration tests -———

| The class TestRealS3Integration uploads to the TEST bucket and cleans
  up after
| itself. It requires:
| • boto3 installed
| • /usr/share/odemis/datacollector.key present with TEST bucket
  credentials
| • TEST_DATACOLLECTION=1 is NOT needed here — the test class hard-codes
  the
| test bucket directly

::

   env TEST\_NOHW=1 python3 src/odemis/util/test/datacollector\_test.py \\  
       TestRealS3Integration

If the key file is absent, this class is skipped automatically.

Quick smoke-test with odemis-dc-fetch
----------------------------------------

| After a successful upload (production or test), use the retrieval
  script to
| confirm objects landed in S3. The retrieval script can only be used by
  aws profile of data analyst. In other words, the access and secret key
  will decide if it is possible to fetch the data.

::

   python3 scripts/odemis-dc-fetch.py \\  
       \--bucket delmic-odemis-collect \\  
       \--region eu-west-1 \\  
       \--output ./dc\_samples\_test

Filter by event name and date:

::

   python3 scripts/odemis-dc-fetch.py \\  
       \--bucket delmic-odemis-collect-test \\  
       \--region eu-west-1 \\  
       \--event feature\_collected \\  
       \--since 2026-04-01 \\  
       \--output ./dc\_samples\_test

| The script prints a one-line summary:
| listed=N matched=N downloaded=N skipped_existing=N failed=0

| If “failed” is non-zero, check the log output for AccessDenied or
  network
| errors and verify the key file credentials.

Troubleshooting
------------------

| Problem : boto3 ImportError when starting Odemis
| Solution: sudo apt install python3-boto3

-———

| Problem : LookupError: S3 credentials key file not found at
| /usr/share/odemis/datacollector.key
| Solution: Create the key file as described in Section 3.

-———

| Problem : botocore.exceptions.ClientError: AccessDenied
| Cause : The IAM key in the key file does not have permission to write
  to
| the bucket being targeted.
| Solution: Ensure the key file contains credentials matching the target
  bucket
| (production key → production bucket, test key → test bucket).
| Check whether TEST_DATACOLLECTION=1 is set unexpectedly.

-———

| Problem : Uploads never happen; queue fills up
| Cause : Network is unavailable or credentials are wrong.
| Solution: Check the Odemis log for “DataCollector upload failed”
  entries.
| The framework retries with exponential back-off (30 s → 60 s → …
| up to 1 h). Pending ZIPs remain in /.local/share/odemis/dc_queue/ and
| are flushed oldest-first once connectivity is restored.

-———

| Problem : “Queue limit exceeded: removed oldest sample” appears in the
  log
| Cause : The queue directory has grown beyond 10 % of the partition.
| Solution: Check disk space (df -h /.local/share/odemis/dc_queue). Consider
| moving /.local/share/odemis/ to a larger partition, or investigate why
| uploads are not succeeding (credentials, network).

-———

| Problem : Consent dialog does not appear on first launch
| Cause : ~/.config/odemis/datacollector.config already contains a
  consent
| value (e.g. from a previous installation).
| Solution: Reset consent manually (see Section 5) or delete the config
  file:
| rm ~/.config/odemis/datacollector.config

-———

| Problem : TEST_DATACOLLECTION=1 is set but uploads still go to
  production
| Solution: This cannot happen — the environment variable is read at the
  moment
| get_upload_backend() is called, which is lazy (first upload attempt).
| Verify the variable is exported in the same shell/environment that
| runs the Odemis process.

-———

| Problem : \*.tmp files accumulate in dc_queue
| Cause : Odemis crashed mid-write.
| Solution: Stop Odemis, then: rm -f /.local/share/odemis/dc_queue/\*.tmp
| The framework also cleans these up automatically on the next startup.
