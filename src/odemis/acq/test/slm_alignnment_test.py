# -*- coding: utf-8 -*-
"""Tests for SLM alignment-stage axis coupling and mappings."""

import logging
import math
import os
import time
import unittest

import odemis
from odemis import model
from odemis.acq.move import MicroscopePostureManager, Posture
from odemis.util import testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_FIBSEM_SLM_CONFIG = CONFIG_PATH + "sim/meteor-fibsem-slm-sim.odm.yaml"


class TestMeteorFibsemSlmAlignmentStage(unittest.TestCase):
    """Validate SLM alignment-stage mappings onto lens-arm and focus dependencies."""

    MIC_CONFIG = METEOR_FIBSEM_SLM_CONFIG

    @classmethod
    def setUpClass(cls) -> None:
        """Start backend and acquire required components for alignment-stage tests."""
        testing.start_backend(cls.MIC_CONFIG)

        cls.microscope = model.getMicroscope()
        cls.pm = MicroscopePostureManager(microscope=cls.microscope)
        cls.align_stage = model.getComponent(role="align-coincident")
        cls.lens_arm = model.getComponent(role="lens-arm-coincident")
        cls.focus = model.getComponent(role="focus-coincident")

    def setUp(self) -> None:
        """Move to SLM posture when supported to keep axis behavior deterministic."""
        if Posture.SLM_IMAGING in self.pm.postures:
            self.pm.switch_posture(Posture.LOADING).result()
            self.pm.switch_posture(Posture.SLM_IMAGING).result()
            time.sleep(0.1)

    def test_alignment_x_moves_lens_arm_s(self) -> None:
        """
        Moving alignment X should map to lens-arm S axis with negated scale.

        The forward transform is:
            delta_s = -delta_align_x (negated mapping via ConvertStage)
        """
        delta_x = 50e-6  # m
        align_start = self.align_stage.position.value.copy()
        lens_start = self.lens_arm.position.value.copy()

        self.align_stage.moveRel({"x": delta_x}).result()
        time.sleep(0.1)

        align_after = self.align_stage.position.value
        lens_after = self.lens_arm.position.value
        moved_align_x = align_after["x"] - align_start["x"]
        moved_lens_s = lens_after["s"] - lens_start["s"]
        logging.debug("SLM Alignment X move: delta_align_x=%s delta_lens_s=%s (negated)", moved_align_x, moved_lens_s)

        self.assertAlmostEqual(moved_align_x, delta_x, delta=1e-7)
        self.assertAlmostEqual(moved_lens_s, -delta_x, delta=1e-7)  # Negated mapping

    def test_alignment_y_couples_lens_l_and_focus_z(self) -> None:
        """
        Moving alignment Y should change both lens-arm L and focus Z with expected projected relation.

        Alignment Y maps to Linked LZ.x (the ConvertStage input).
        The transformation matrix from alignment to hardware is:
            delta_l = -1/sin(45°) * delta_align_y
            delta_z = 1/tan(45°) * delta_align_y
        """
        delta_y = 50e-6  # m
        align_start = self.align_stage.position.value.copy()
        lens_start = self.lens_arm.position.value.copy()
        focus_start = self.focus.position.value.copy()

        self.align_stage.moveRel({"y": delta_y}).result()
        time.sleep(0.1)

        align_after = self.align_stage.position.value
        lens_after = self.lens_arm.position.value
        focus_after = self.focus.position.value

        moved_align_y = align_after["y"] - align_start["y"]
        delta_l = lens_after["l"] - lens_start["l"]
        delta_z = focus_after["z"] - focus_start["z"]
        # Reconstruct align_y from hardware changes using inverse transform
        # Method 1: reconstructed_y = -sin(45°) * delta_l
        # Method 2 (simpler): reconstructed_y = delta_z
        reconstructed_y1 = - math.sin(math.pi/4) * delta_l
        reconstructed_y2 = delta_z
        logging.debug(
            "SLM Alignment Y move: delta_align_y=%s delta_l=%s delta_z=%s reconstructed_y=%s",
            moved_align_y,
            delta_l,
            delta_z,
            reconstructed_y1,
        )

        self.assertAlmostEqual(moved_align_y, delta_y, delta=1e-7)
        self.assertAlmostEqual(reconstructed_y1, reconstructed_y2, delta=1e-7)
        self.assertAlmostEqual(moved_align_y, reconstructed_y1, delta=1e-7)

    def test_alignment_z_moves_focus_z_opposite(self) -> None:
        """
        Moving alignment Z should move focus Z in opposite direction with lens-arm L unchanged.

        The forward transform (sample/alignment to hardware) is:
            delta_l = -1/sin(45°) * delta_align_y + 0 * delta_align_z
            delta_z = 1/tan(45°) * delta_align_y - delta_align_z

        When align.y doesn't move (delta_align_y = 0)
        Therefore: delta_z = -delta_align_z (opposite direction)
        """
        delta_z_align = 50e-6  # m
        align_start = self.align_stage.position.value.copy()
        lens_start = self.lens_arm.position.value.copy()
        focus_start = self.focus.position.value.copy()

        self.align_stage.moveRel({"z": delta_z_align}).result()
        time.sleep(0.1)

        align_after = self.align_stage.position.value
        lens_after = self.lens_arm.position.value
        focus_after = self.focus.position.value

        moved_align_z = align_after["z"] - align_start["z"]
        delta_l = lens_after["l"] - lens_start["l"]
        delta_s = lens_after["s"] - lens_start["s"]
        delta_focus_z = focus_after["z"] - focus_start["z"]

        logging.debug(
            "SLM Alignment Z move: delta_align_z=%s delta_l=%s delta_s=%s delta_focus_z=%s (should be opposite)",
            moved_align_z,
            delta_l,
            delta_s,
            delta_focus_z,
        )

        self.assertAlmostEqual(moved_align_z, delta_z_align, delta=1e-7)
        self.assertAlmostEqual(delta_s, 0.0, delta=1e-9)  # S axis not affected
        self.assertAlmostEqual(delta_l, 0.0, delta=1e-9)  # L axis not affected (align.y didn't move)
        self.assertAlmostEqual(delta_focus_z, -moved_align_z, delta=1e-7)  # Z moves OPPOSITE to align.z


if __name__ == "__main__":
    unittest.main()
