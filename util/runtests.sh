#!/bin/bash

# Run all the tests that can be found:
# Every file which is in the pattern /test/*_test.py

TESTLOG=./test.log
ODEMISPATH="$(readlink -m "./src/odemis")"

# This environment variable makes the GUI test cases automatically close the test frames
export NOMANUAL=1

# This environment variable (should) make the driver test not try to use real hardware (only simulator)
export TEST_NOHW=1

# Not related to tests, but to QA in general: Expections usually take only 1 argument
# So a comma is probably a sign of syntax error and should be replace by a %
echo "These files might have syntax error when raising an exception:"
grep -IrE --colour 'raise.*",' --include=*.py src/odemis/
echo "---"

echo "These files are not using division from the future:"
grep -IrL "from __future__ import.*division" --include=*.py src/
echo "---"

echo "These files do not have the license header:"
grep -LIr "GNU General Public License" --include=*.py src/
echo "---"

PYTHONPATH=./src/:../Pyro4/src/
if [ -f /etc/odemis.conf ]; then
    # use the odemis config if it's available
    . /etc/odemis.conf
fi
export PYTHONPATH

if [ ! -d /var/run/odemisd ] ; then
    echo  "Need /var/run/odemisd"
    sudo mkdir -m 777 /var/run/odemisd
fi

# stop the backend
odemis-stop

# make sure it is full path
TESTLOG="$(readlink -m "$TESTLOG")"

# find the test scripts (should not contain spaces)
testfiles="$(find "$ODEMISPATH" -wholename "*/test/*test.py")"

#Warn if some files are misnamed
skippedfiles="$(find "$ODEMISPATH" -wholename "*/test/*.py" -and -not -wholename "*/test/*test.py")"
if [ "$skippedfiles" != "" ]; then
    echo "Warning, these scripts are not named *_test.py and will be skipped:"
    echo "$skippedfiles"
fi

echo "Running tests on $(date)" > "$TESTLOG"
# run each test script and save the output
failures=0
for f in $testfiles; do
    echo "Running $f..."
    if ! grep -q "__main__" $f; then
        echo "WARNING: test $f seems to not be runnable"
    fi
    echo "Running $f:" >> "$TESTLOG"
    # run it in its own directory (sometimes they need specific files from there)
    pushd "$(dirname $f)" > /dev/null
        python $f --verbose >> "$TESTLOG" 2>&1
        #echo coucou >> "$TESTLOG" 2>&1
        status=$?
        echo $f returned $status >> "$TESTLOG" 2>&1
    popd > /dev/null
    grep -E "(OK|FAILED)" "$TESTLOG" | tail -1
    if [ "$status" -gt 0 ]; then
        failures=$(( $failures + 1 ))
    fi
done

if [ $failures -gt 0 ]; then
    echo "$failures test failed. See $TESTLOG for error messages."
    exit 1
else
    echo "All tests passed"
fi

# try to clean up a bit
pkill -f odemis.odemisd.main
