#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import inspect
from functools import partial
import os
import numpy.random
import odemis
from odemis import model, util
import logging
import math
from odemis.model import CancellableFuture
from odemis.util import limit_invocation, TimeoutError, executeAsyncTask, \
    perpendicular_distance, to_str_escape, testing, driver, timeout
import time
import unittest
import weakref

logging.getLogger().setLevel(logging.DEBUG)


class TestLimitInvocation(unittest.TestCase):
    def test_not_too_often(self):
        self.count = 0
        now = time.time()
        end = now + 1.1 # a bit more than 1 s
        while time.time() < end:
            self.count_max_1s()
            time.sleep(0.01)

        self.assertLessEqual(self.count, 2, "method was called more than twice in 1 second: %d" % self.count)

        time.sleep(2) # wait for the last potential calls to happen
        self.assertLessEqual(self.count, 3, "method was called more than three times in 2 seconds: %d" % self.count)

    @limit_invocation(1)
    def count_max_1s(self):
        # never called more than once per second
        self.count += 1
        time.sleep(0.2)

    def test_gc(self):
        u = Useless()
        wku = weakref.ref(u)
        now = time.time()
        end = now + 1.1  # a bit more than 1 s
        while time.time() < end:
            u.doit(time.time(), b=3)
            time.sleep(0.01)

        # Check the object u has nothing preventing it from being dereferenced
        del u
        time.sleep(1) # wait for the last potential calls to happen
        self.assertIsNone(wku())


class Useless(object):
    """
    Independent class for testing limit_invocation decorator
    """
    def __del__(self):
        print("Useless %r is gone" % self)

    @limit_invocation(0.1)
    def doit(self, a, b=None):
        print("doing it %s, %s" % (a, b))


class TestTimeout(unittest.TestCase):
    @timeout(1.2)
    def test_notimeout(self):
        time.sleep(1)

    def test_timeout(self):
        self.assertRaises(TimeoutError, self.toolong)

    @timeout(0.5)
    def toolong(self):
        # will always timeout
        time.sleep(1)


class TestExectuteTask(unittest.TestCase):

    def test_execute(self):
        f = CancellableFuture()
        t = executeAsyncTask(f, self.long_task, args=(42,), kwargs={"d": 3})
        self.assertFalse(f.done())
        time.sleep(0.1)
        self.assertTrue(f.running())
        r = f.result()
        self.assertEqual(r, -1)
        self.assertEqual(self._v, 42)
        self.assertEqual(self._d, 3)

    def long_task(self, v, d=1):
        self._v = v
        self._d = d
        time.sleep(1)
        return -1


class SortedAccordingTestCase(unittest.TestCase):

    def test_simple(self):
        in_exp = ((([1, 2, 3], [3, 2, 1]), [3, 2, 1]),
                  (([1, 2, 3], [4, 2]), [2, 1, 3]),
                  (([], [4, 2]), []),
                  ((["b", "a"], []), ["b", "a"]),
                  )
        for i, eo in in_exp:
            o = util.sorted_according_to(*i)
            self.assertEqual(o, eo, "Failed to get correct output for %s" % (i,))


class AlmostEqualTestCase(unittest.TestCase):

    def test_simple(self):
        in_exp = {(0., 0): True,
                  (-5, -5.): True,
                  (1., 1. - 1e-9): True,
                  (1., 1. - 1e-3): False,
                  (1., 1. + 1e-3): False,
                  (-5e-8, -5e-8 + 1e-19): True,
                  (5e18, 5e18 + 1): True,
                  }
        for i, eo in in_exp.items():
            o = util.almost_equal(*i)
            self.assertEqual(o, eo, "Failed to get correct output for %s" % (i,))

    def test_rot_simple(self):
        in_exp = {(0., 0): True,
                  (-5, -5.): True,
                  (1., 1. - 1e-9): True,
                  (1., 1. - 1e-3): False,
                  (1., 1. + 1e-3): False,
                  (2 * math.pi, 6.28): False,
                  (2 * math.pi, 4 * math.pi): True,
                  (3 * math.pi, 5 * math.pi): True,
                  (-2 * math.pi, 4 * math.pi): True,
                  }
        for i, eo in in_exp.items():
            o = util.rot_almost_equal(*i)
            self.assertEqual(o, eo, "Failed to get correct output for %s" % (i,))

    def test_rot_atol(self):
        in_exp = {(0.1, 0, 0.2): True,
                  (2 * math.pi, 6.28, 0.01): True,
                  (2 * math.pi + 1e-6, 4 * math.pi, 10e-6): True,
                  (3 * math.pi + 1e-6, 5 * math.pi, 10e-6): True,
                  (-2 * math.pi - 1e-6, 4 * math.pi, 10e-6): True,
                  (-2 * math.pi - 20e-6, 4 * math.pi, 10e-6): False,
                  }
        for i, eo in in_exp.items():
            o = util.rot_almost_equal(*i)
            self.assertEqual(o, eo, "Failed to get correct output for %s" % (i,))

    def test_rot_rtol(self):
        in_exp = {(0.1, 0.11, 0.2): True,
                  (0.1, 0.11, 0.01): False,
                  (-0.1, -0.11, 0.01): False,
                  (2 * math.pi, 6.28, 0.1): False,  # values very close from 0
                  (3 * math.pi + 1e-6, 5 * math.pi, 0.01): True,  # values very far from 0
                  (2001 * math.pi + 1e-6, 5 * math.pi, 0.01): True,
                  (2000 * math.pi + 1e-6, 5 * math.pi, 0.01): False,
                  }
        for i, eo in in_exp.items():
            o = util.rot_almost_equal(i[0], i[1], rtol=i[2])
            self.assertEqual(o, eo, "Failed to get correct output for %s" % (i,))


class StrEscapeTestCase(unittest.TestCase):
# TODO:unit tests with line = b"\00a\xffb\nc\x82d'e"
# line = b"\x00a\xffb\nc\xc2\x82d'e\xe6\x88\x91\xea\x8d\x88"
# ul = u"\x00aÿb\nc\x82d'e我\ua348"
# ul = line.decode("latin1")
    BYTES = b"\x00a\xffb\nc\xc2\x82d'e\xe6\x88\x91\xea\x8d\x88"
    BYTES_ESC = r"\x00a\xffb\nc\xc2\x82d'e\xe6\x88\x91\xea\x8d\x88"
    UNICODE = u"\x00aÿb\nc\x82d'e我\ua348"
    UNICODE_ESC = r"\x00a\xffb\nc\x82d'e\u6211\ua348"

    def test_fixed(self):
        s_esc = to_str_escape(self.BYTES)
        self.assertEqual(s_esc, self.BYTES_ESC)

        s_esc = to_str_escape(self.UNICODE)
        self.assertEqual(s_esc, self.UNICODE_ESC)

    def test_short(self):
        s_esc = to_str_escape(b"")
        self.assertEqual(s_esc, "")

        s_esc = to_str_escape(u"")
        self.assertEqual(s_esc, "")

    def test_long(self):
        s_esc = to_str_escape(self.BYTES * 1000)
        self.assertEqual(s_esc, self.BYTES_ESC * 1000)

        s_esc = to_str_escape(self.UNICODE * 1000)
        self.assertEqual(s_esc, self.UNICODE_ESC * 1000)


class PerpendicularDistanceTestCase(unittest.TestCase):

    def test_simple(self):
        """ Test distance using easy geometry """
        a = (0, 0)
        b = (1, 0)
        # Move the point along another line segment at 1 unit away
        for e in ((0, 1), (0.5, 1), (1, 1), (0.5, -1)):
            dist = perpendicular_distance(a, b, e)
            self.assertAlmostEqual(dist, 1)

        # Move the point along the original segment itself => dist == 0
        for e in ((0, 0), (0.5, 0), (1, 0)):
            dist = perpendicular_distance(a, b, e)
            self.assertAlmostEqual(dist, 0)

    def test_null_line(self):
        """ Test distance when the line is just a single point"""
        a = (1, 0)
        b = (1, 0)
        e = (0, 0)
        # Follow a segment at 1 unit away
        dist = perpendicular_distance(a, b, e)
        self.assertAlmostEqual(dist, 1)


# Bounding box clipping test data generation
def tp(trans, ps):
    """ Translate points ps using trans """
    r = []
    i = 0
    for p in ps:
        r.append(p + trans[i])
        i = (i + 1) % len(trans)
    return tuple(r)


# First we define a bounding boxes, at different locations
bounding_boxes = [(-2, -2, 0, 0),
                  (-1, -1, 1, 1),
                  (0, 0, 2, 2),
                  (2, 2, 4, 4)]

# From this, we generate boxes that are situated all around these
# bounding boxes, but that do not touch or overlap them.


def relative_boxes(bb):

    t_left = [(-3, i) for i in range(-3, 4)]
    to_the_left = [tp(t, bb) for t in t_left]

    t_top = [(i, -3) for i in range(-3, 4)]
    to_the_top = [tp(t, bb) for t in t_top]

    t_right = [(3, i) for i in range(-3, 4)]
    to_the_right = [tp(t, bb) for t in t_right]

    t_bottom = [(i, 3) for i in range(-3, 4)]
    to_the_bottom = [tp(t, bb) for t in t_bottom]

    outside_boxes = to_the_left + to_the_top + to_the_right + to_the_bottom

    # Selection boxes that touch the outside of the bounding box
    touch_left = [tp((1, 0), b) for b in to_the_left[1:-1]]
    touch_top = [tp((0, 1), b) for b in to_the_top[1:-1]]
    touch_right = [tp((-1, 0), b) for b in to_the_right[1:-1]]
    touch_bottom = [tp((0, -1), b) for b in to_the_bottom[1:-1]]

    touching_boxes = touch_left + touch_top + touch_right + touch_bottom

    # Partial overlapping boxes
    overlap_left = [tp((1, 0), b) for b in touch_left[1:-1]]
    overlap_top = [tp((0, 1), b) for b in touch_top[1:-1]]
    overlap_right = [tp((-1, 0), b) for b in touch_right[1:-1]]
    overlap_bottom = [tp((0, -1), b) for b in touch_bottom[1:-1]]

    overlap_boxes = overlap_left + overlap_top + overlap_right + overlap_bottom

    return outside_boxes, touching_boxes, overlap_boxes


class CanvasTestCase(unittest.TestCase):

    def test_clipping(self):

        tmp = "{}: {} - {} -> {}"

        for bb in bounding_boxes:
            outside, touching, overlap = relative_boxes(bb)

            for b in outside:
                r = util.rect_intersect(b, bb)
                msg = tmp.format("outside", b, bb, r)
                self.assertIsNone(r, msg)

            for b in touching:
                r = util.rect_intersect(b, bb)
                msg = tmp.format("touching", b, bb, r)
                self.assertIsNone(r, msg)

            for b in overlap:
                r = util.rect_intersect(b, bb)
                msg = tmp.format("overlap", b, bb, r)
                self.assertIsNotNone(r, msg)

                # 'Manual' checks
                if bb == (-1, -1, 1, 1):
                    if b[:2] == (-2, -2):
                        self.assertEqual(r, (-1, -1, 0, 0), msg)
                    elif b[:2] == (0, -1):
                        self.assertEqual(r, (0, -1, 1, 1), msg)
                    elif b[:2] == (0, 0):
                        self.assertEqual(r, (0, 0, 1, 1), msg)

            # full and exact overlap
            b = bb
            r = util.rect_intersect(b, bb)
            self.assertEqual(r, bb)

            # inner overlap
            b = (bb[0] + 1, bb[1] + 1, bb[2], bb[3])
            r = util.rect_intersect(b, bb)
            self.assertEqual(r, b)

            # overflowing overlap
            b = (bb[0] - 1, bb[1] - 1, bb[2] + 1, bb[2] + 1)
            r = util.rect_intersect(b, bb)
            self.assertEqual(r, bb)

    def test_line_clipping(self):
        bounding_box = (0, 4, 4, 0)
        clip = partial(util.clip_line, *bounding_box)

        # Test lines within bounding box, i.e. no clipping should occur
        internal = [
            (0, 0, 0, 0),
            (2, 2, 2, 2),
            (0, 0, 4, 4),
            (4, 4, 0, 0),
            (0, 2, 2, 0),
            (2, 0, 0, 2),
        ]

        for line in internal:
            self.assertEqual(line, clip(*line))

        # Test clipping for lines originating in the center of the bounding box and ending outside
        # of it.
        inner_to_outer = [
            ((2, 2, 2, 6), (2, 2, 2, 4)),
            ((2, 2, 6, 2), (2, 2, 4, 2)),
            ((2, 2, 2, -2), (2, 2, 2, 0)),
            ((2, 2, -2, 2), (2, 2, 0, 2)),
            ((2, 2, 6, -2), (2, 2, 4, 0)),
            ((2, 2, -2, -2), (2, 2, 0, 0)),
            ((2, 2, -2, -2), (2, 2, 0, 0)),
        ]

        for orig, clipped in inner_to_outer:
            self.assertEqual(clipped, clip(*orig))

        outer_to_inner = [
            ((2, 6, 2, 2), (2, 4, 2, 2)),
            ((6, 2, 2, 2), (4, 2, 2, 2)),
            ((2, -2, 2, 2), (2, 0, 2, 2)),
            ((-2, 2, 2, 2), (0, 2, 2, 2)),
            ((6, -2, 2, 2), (4, 0, 2, 2)),
            ((-2, -2, 2, 2), (0, 0, 2, 2)),
            ((-2, -2, 2, 2), (0, 0, 2, 2)),
        ]

        for orig, clipped in outer_to_inner:
            self.assertEqual(clipped, clip(*orig))


CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-sim.odm.yaml"


class TestBackendStarter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # make sure initially no backend is running.
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            testing.stop_backend()

    @classmethod
    def tearDownClass(cls):
        # turn off everything when the testing finished.
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            testing.stop_backend()

    def test_no_running_backend(self):
        # check if there is no running backend
        backend_status = driver.get_backend_status()
        self.assertIn(backend_status, [driver.BACKEND_STOPPED, driver.BACKEND_DEAD])
        # run enzel
        testing.start_backend(ENZEL_CONFIG)
        # now check if the role is enzel
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")

    def test_running_backend_same_as_requested(self):
        # run enzel backend
        testing.start_backend(ENZEL_CONFIG)
        # check if the role is enzel
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")
        # run enzel backend again
        testing.start_backend(ENZEL_CONFIG)
        # it should still be enzel.
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")

    def test_running_backend_different_from_requested(self):
        # run sparc backend
        testing.start_backend(SPARC_CONFIG)
        # check if the role is sparc
        role = model.getMicroscope().role
        self.assertEqual(role, "sparc")
        # now run another backend (enzel)
        testing.start_backend(ENZEL_CONFIG)
        # check if the role now is enzel instead of sparc
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")


class FindClosestTestCase(unittest.TestCase):

    def test_simple(self):
        """Finding the closest point in a single list"""
        closest = util.find_closest(15, [1, 2, 3])
        self.assertEqual(closest, 3, "Failed to get correct output.")

    def test_multiple(self):
        """Test finding the closest point in multiple lists"""
        in_exp = {
            tuple([-1, -5, -6]): -1,
            tuple([1, 2, 3]): 3,
            tuple([0, 5, 10]): 5,
            tuple([-15, 30, -60]): -15
        }

        for testlist, hardcoded_closest in in_exp.items():
            closest = util.find_closest(5, testlist)
            self.assertEqual(hardcoded_closest, closest, "Failed to get correct output for "
                                                         "the closest value to 5 in %s" % in_exp)


class IndexClosestTestCase(unittest.TestCase):

    def test_simple(self):
        """ Test finding the index of the closest point in a list """
        simple_list = [1, 5, 6]
        closest_index = util.index_closest(1, simple_list)
        self.assertEqual(closest_index, 0, "Failed to get correct output for "
                                           "the closest index to 1 in %s" % simple_list)

    def test_multiple(self):
        """ Test finding the index of the closest point in multiple lists """
        in_exp = {
            tuple([-1, -5, -6]): 0,
            tuple([1, 2, 3]): 2,
            tuple([0, 5, 10]): 1,
            tuple([-15, 30, -60]): 0
        }

        for testlist, hardcoded_closest_index in in_exp.items():
            closest = util.index_closest(5, testlist)
            self.assertEqual(hardcoded_closest_index, closest, "Failed to get correct output for "
                                                               "the closest index to 5 in %s" % in_exp)


class NormalizeRectTestCase(unittest.TestCase):

    def test_already_normalized(self):
        """Test normalizing a rectangle with already normalized values"""
        rect = (1, 2, 3, 4)
        normalized_rect = util.normalize_rect(rect)

        self.assertEqual(normalized_rect, rect, "Failed to normalize the rectangle")

    def test_non_normalized(self):
        """Test normalizing a rectangle with not yet normalized values"""
        rect = (10, 7, 5, 3)
        normalized_rect = util.normalize_rect(rect)
        expected_normalized_rect = (5, 3, 10, 7)

        self.assertEqual(normalized_rect, expected_normalized_rect, "Failed to normalize the rectangle")


class IsPointInRectTestCase(unittest.TestCase):

    def test_points_in_rect(self):
        """Test whether the given coordinates are in the rectangle"""
        rect = (5, 3, 10, 7)
        point = (8.0, 4.0)
        in_rect = util.is_point_in_rect(point, rect)
        self.assertTrue(in_rect, "Failed to get correct output for the point in rectangle %s with "
                                 "point %s" % (rect, point))

    def test_points_outside_rect(self):
        """Test whether the given coordinates are not in the rectangle"""
        rect = (5, 3, 10, 7)
        point = (22.0, 44.0)
        in_rect = util.is_point_in_rect(point, rect)
        self.assertFalse(in_rect, "Failed to get correct output for the point in rectangle %s with "
                                  "point %s" % (rect, point))


class ExpandRectTestCase(unittest.TestCase):

    def test_expand_rect(self):
        """Test expanding a rectangle"""
        rect = (5, 3, 10, 7)
        expanded_rect = util.expand_rect(util.normalize_rect(rect), 5)
        expected_rect = (0, -2, 15, 12)
        self.assertEqual(expanded_rect, expected_rect, "Failed to get correct output for rectangle %s with "
                                                       "expanded rectangle %s" % (expanded_rect, expected_rect))


class GetPolygonBBoxTestCase(unittest.TestCase):

    def test_poly_bbox_positive(self):
        """Test getting the minimum and maximum values from a list of tuples with positive values"""
        values = [(2, 3), (1, 1), (2, 5), (5, 8), (0, 5), (10, 3), (0, 1)]
        min_0, min_1, max_0, max_1 = 0, 1, 10, 8
        rmin_0, rmin_1, rmax_0, rmax_1 = util.get_polygon_bbox(values)

        self.assertEqual(min_0, rmin_0)
        self.assertEqual(max_0, rmax_0)
        self.assertEqual(min_1, rmin_1)
        self.assertEqual(max_1, rmax_1)

    def test_poly_bbox_negative(self):
        """Test getting the minimum and maximum values from a list of tuples with negative values"""
        values = [(-1, -3), (-1, -1), (-2, -5), (-5, -8), (0, -5), (-10, -3), (0, -1)]
        min_0, min_1, max_0, max_1 = -10, -8, 0, -1
        rmin_0, rmin_1, rmax_0, rmax_1 = util.get_polygon_bbox(values)

        self.assertEqual(min_0, rmin_0)
        self.assertEqual(max_0, rmax_0)
        self.assertEqual(min_1, rmin_1)
        self.assertEqual(max_1, rmax_1)

    def test_poly_bbox_mixed(self):
        """Test getting the minimum and maximum values from a list of tuples with positive and negative values"""
        values = [(1, -3), (1, 1), (-2, 5), (5, 8), (0, 5), (-10, 3), (0, -1)]
        min_0, min_1, max_0, max_1 = -10, -3, 5, 8
        rmin_0, rmin_1, rmax_0, rmax_1 = util.get_polygon_bbox(values)

        self.assertEqual(min_0, rmin_0)
        self.assertEqual(max_0, rmax_0)
        self.assertEqual(min_1, rmin_1)
        self.assertEqual(max_1, rmax_1)

    def test_incorrect_length(self):
        """Test that the function raises an error when only one coordinate is given"""
        with self.assertRaises(ValueError):
            util.get_polygon_bbox([(0, 0)])

    def test_incorrect_shape(self):
        """Test that the function raises an error when coordinates of different shapes are given"""
        with self.assertRaises(ValueError):
            util.get_polygon_bbox([(0, 0), (0, 1, 2)])


class FindPlotContentTestCase(unittest.TestCase):

    def test_find_plot_content(self):
        """Test finding left and right most non-zero values"""
        xd = (10, 15, 20, 25, 30, 35, 40)
        yd = (0, 1, 0, 0, 2, -3, 0)
        plot_content = util.find_plot_content(xd, yd)
        self.assertEqual(plot_content, (15, 35), "Failed to get correct output while finding plot content "
                                                 "for %s with values  %s %s" % (plot_content, xd, yd))


class WrapToMpiPpiTestCase(unittest.TestCase):

    def test_positive_pi(self):
        """Test value is smaller than +pi value"""
        converted_angle = util.wrap_to_mpi_ppi(4.65)
        self.assertLessEqual(converted_angle, math.pi, "Failed to convert the angle to a value between -pi and +pi "
                                                       "with the converted value: %s" % converted_angle)

    def test_negative_pi(self):
        """Test value is bigger than -pi value"""
        converted_angle = util.wrap_to_mpi_ppi(-5.1)
        self.assertGreaterEqual(converted_angle, -math.pi, "Failed to convert the angle to a value between -pi and +pi "
                                                           "with the converted value: %s" % converted_angle)


class RecursiveDictUpdateTestCase(unittest.TestCase):

    def test_dict_update(self):
        """Test recursively updating first dict with the second dict"""
        dict1 = {
          "firstname": "John",
          "lastname": "Doe",
          "birthdate": 1964
        }

        dict2 = {
            "suffix": "van",
            "birthdate": 1994
        }

        expected_dict = {
            "firstname": "John",
            "suffix": "van",
            "lastname": "Doe",
            "birthdate": 1994
        }

        new_dict = util.recursive_dict_update(dict1, dict2)
        self.assertEqual(new_dict, expected_dict, "Failed to get correct output while updating the dictionary "
                                                  " %s" % new_dict)


class PairwiseTestCase(unittest.TestCase):

    def test_pairwise(self):
        """Test pairing two items in a tuple"""
        test = util.pairwise(("item 1", "item 2"))
        test_list = list(test)[0]
        self.assertEqual(test_list, ("item 1", "item 2"), "Failed to pair.")


class InspectGetMembersTestCase(unittest.TestCase):

    def test_inspect_get_members(self):
        """ Test the builtin inspect.getmembers raises a TypeError for the dummy class, and that
        util.inspect_getmembers has the correct output. """
        with self.assertRaises(TypeError):
            inspect.getmembers(InspectGetMembersDummy())

        res = util.inspect_getmembers(InspectGetMembersDummy())
        self.assertEqual(len(res), 27)


class InspectGetMembersDummy:
    """
    https://stackoverflow.com/questions/54478679/workaround-for-getattr-special-method-breaking-inspect-getmembers-in-pytho
    """
    def __getattr__(self, name):
        def wrapper():
            print("For testing purposes, this does nothing useful.")

        return wrapper


class GetBestDtypeForAccTestCase(unittest.TestCase):

    def test_get_best_dtype_for_acc(self):
        """Test setting the fitting dtype"""
        # test that for a non int the same dtype is returned
        img = numpy.random.random((10, 9)).astype(numpy.float64)
        idtype = util.get_best_dtype_for_acc(img.dtype, 5)
        self.assertEqual(idtype, img.dtype)

        img = numpy.random.random((10, 9)).astype(numpy.uint8)
        idtype = util.get_best_dtype_for_acc(img.dtype, 3)
        self.assertEqual(idtype, numpy.uint16)

        img = numpy.random.random((10, 9)).astype(numpy.uint8)
        # for uint8 images the max value is 255, and for uint16 it is 65535. If the number of uint8 images to be
        # accumulated times 255 exceeds 65535 the best dtype becomes uint32.
        idtype = util.get_best_dtype_for_acc(img.dtype, int(65535/255) + 1)
        self.assertEqual(idtype, numpy.uint32)

        # for multiple uint64 images the expected dtype is float64
        img = numpy.random.random((10, 9)).astype(numpy.uint64)
        idtype = util.get_best_dtype_for_acc(img.dtype, 3)
        self.assertEqual(idtype, numpy.float64)


if __name__ == "__main__":
    unittest.main()
