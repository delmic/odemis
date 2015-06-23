#!/usr/bin/env python
# -*- coding: utf-8 -*-


# FIXME: Muy importante!!!
# This script doesn't work, the cause is probably that many GUI test
# cases make use of a test frame, that gets closed after the test case has
# run bby a call to wx.CallAfter(cls.app.Exit)
# The CallAfter is needed to make sure the frame doesn't close too fast,
# resulting in tests failing
# Howerever, the CallAfter messes things up when a lot of GUI tests are run
# in quick succession using this script.

def run_test():
    import sys

    sys.path.extend(["/home/rinze/dev/lib/Pyro4/src/",
                     "/home/rinze/dev/odemis/src",
                     "/home/rinze/dev/odemis/src/gui",
                     "/home/rinze/dev/misc/"])

    import unittest
    import os
    import logging

    import odemis.gui.test as test

    print "\n** Gathering all Odemis GUI TestCases...\n"

    alltests = unittest.TestSuite()

    path = os.path.dirname(os.path.realpath(__file__))

    for _, _, files in os.walk(path):
        modules_to_test = [x[:-3] for x in files if x.endswith('test.py')]

        for file_name in sorted(modules_to_test):
            print " * Adding module %s" % file_name
            module = __import__(file_name)
            logging.getLogger().setLevel(logging.ERROR)

            module.INSPECT = False
            module.MANUAL = False
            module.SLEEP_TIME = 10

            alltests.addTest(unittest.findTestCases(module))

    result = unittest.TestResult()

    test.INSPECT = False
    test.MANUAL = False
    test.SLEEP_TIME = 10

    print "\n** Running..."
    alltests.run(result)

    num_errors = len(result.errors)
    print "\n** %d Errors occured" % num_errors

    for error in result.errors:
        print "  * %s" % error[0]
        for line in error[1].splitlines():
            print "  %s" % line

    num_failures = len(result.failures)
    print "\n** %d Failures occured\n" % num_failures

    for failure in result.failures:
        print " * %s" % failure[0]
        for line in failure[1].splitlines():
            print "  %s" % line

    print "** Done."

if __name__ == '__main__':
    run_test()
