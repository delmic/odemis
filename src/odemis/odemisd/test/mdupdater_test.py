"""
Created on 22 Feb 2022

@author: Éric Piel

Copyright © 2022 Éric Piel, Delmic

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

import unittest
import unittest.mock

import numpy

from odemis import model
from odemis.driver import simsem, static
from odemis.model import Microscope
from odemis.odemisd.mdupdater import MetadataUpdater
from odemis.util import mock


class MDUpdaterTest(unittest.TestCase):

    def test_lens_observer(self):
        # Create microscope (.Alive)
        mic = Microscope("Fake mic", "secom")

        # Create a CCD (the role matters)
        img = model.DataArray(numpy.empty((512, 768), dtype=numpy.uint16))
        ccd = mock.FakeCCD(img)  # role is always "ccd"

        # Create Lens affects CCD (the role matters)
        lens = static.OpticalLens("Lens", "lens", mag=0.51, pole_pos=(458, 519), focus_dist=0.5e-3,
                                  mirror_pos_top=[600.5, 0.2],  # Try with list, as when initialized from YAML it'll be a list
                                  mirror_pos_bottom=(-200, 0.3))
        lens.affects.value = [ccd.name]

        # Mock model.getComponent()
        comps = [ccd, lens]
        def fake_get_component(name):
            for c in comps:
                if c.name == name:
                    return c
            raise LookupError(f"no component {name}")

        with unittest.mock.patch.object(model, "getComponent", fake_get_component):

            # Create a MetadataUpdater
            mdup = MetadataUpdater("MDUpdater", mic)
            mic.alive.value = {ccd, lens}

            # Now, the lens should be observed: the VAs values should be copied on the CCD metadata
            md_ccd = ccd.getMetadata()
            self.assertEqual(md_ccd[model.MD_AR_POLE], lens.polePosition.value)
            self.assertEqual(md_ccd[model.MD_AR_MIRROR_TOP], lens.mirrorPositionTop.value)
            self.assertEqual(md_ccd[model.MD_AR_MIRROR_BOTTOM], lens.mirrorPositionBottom.value)

            # change polePosition => check that the CCD metadata is updated
            pol_pos = (321, 123)
            lens.polePosition.value = pol_pos
            md_ccd = ccd.getMetadata()
            self.assertEqual(md_ccd[model.MD_AR_POLE], pol_pos)

            # change binning => check it's updated
            ccd.binning.value = (2, 4)
            exp_pol_pos = pol_pos[0] / 2, pol_pos[1] / 4
            md_ccd = ccd.getMetadata()
            self.assertEqual(md_ccd[model.MD_AR_POLE], exp_pol_pos)

            # For mirror positions, only the binning in Y is used
            exp_mir_top = tuple(v / 4 for v in lens.mirrorPositionTop.value)
            self.assertEqual(md_ccd[model.MD_AR_MIRROR_TOP], exp_mir_top)
            exp_mir_bot = tuple(v / 4 for v in lens.mirrorPositionBottom.value)
            self.assertEqual(md_ccd[model.MD_AR_MIRROR_BOTTOM], exp_mir_bot)

            mdup.terminate()


class EbeamMDUpdaterTest(unittest.TestCase):
    """
    Tests for e-beam metadata propagation via MetadataUpdater, using mock components.
    """

    @classmethod
    def setUpClass(cls):
        # Create a minimal sparc2 microscope
        cls.mic = Microscope("Fake SPARC2", "sparc2")

        # Create a SimSEM with an e-beam scanner and an se-detector child
        cls.sem = simsem.SimSEM(
            "SEM",
            "sem",
            children={
                "scanner": {"name": "e-beam scanner", "role": "e-beam"},
                "detector0": {"name": "se-detector", "role": "se-detector"},
            }
        )
        cls.ebeam = next(c for c in cls.sem.children.value if c.role == "e-beam")

        # Create a fake CCD that will be affected by the e-beam
        img = model.DataArray(numpy.empty((512, 768), dtype=numpy.uint16))
        cls.ccd = mock.FakeCCD(img)

        cls.ebeam.affects.value = [cls.ccd.name, "se-detector"]

        # Mock model.getComponent() so MetadataUpdater can resolve component names
        all_comps = list(cls.sem.children.value) + [cls.sem, cls.ccd]

        def fake_get_component(name):
            for c in all_comps:
                if c.name == name:
                    return c
            raise LookupError(f"no component {name}")

        cls._patch_get_component = unittest.mock.patch.object(model, "getComponent", fake_get_component)
        cls._patch_get_component.start()

        cls.mdup = MetadataUpdater("MDUpdater", cls.mic)
        cls.mic.alive.value = set(cls.sem.children.value) | {cls.ccd}

    @classmethod
    def tearDownClass(cls):
        cls.mdup.terminate()
        cls.sem.terminate()
        cls._patch_get_component.stop()

    def test_ebeam_beam_params_on_detector(self):
        """
        Verify that when probeCurrent or accelVoltage change on the e-beam,
        MD_BEAM_CURRENT and MD_BEAM_VOLTAGE are updated on the affected detectors.
        """
        # Both VAs must be present on the simulated e-beam
        self.assertTrue(model.hasVA(self.ebeam, "probeCurrent"),
                        "e-beam has no probeCurrent VA")
        self.assertTrue(model.hasVA(self.ebeam, "accelVoltage"),
                        "e-beam has no accelVoltage VA")

        # After setup, the metadata should already be populated (init=True on subscribe)
        md = self.ccd.getMetadata()
        self.assertIn(model.MD_BEAM_CURRENT, md,
                      "MD_BEAM_CURRENT not set on CCD after startup")
        self.assertIn(model.MD_BEAM_VOLTAGE, md,
                      "MD_BEAM_VOLTAGE not set on CCD after startup")
        self.assertAlmostEqual(md[model.MD_BEAM_CURRENT], self.ebeam.probeCurrent.value)
        self.assertAlmostEqual(md[model.MD_BEAM_VOLTAGE], self.ebeam.accelVoltage.value)

        # Change probeCurrent => MD_BEAM_CURRENT must be updated
        new_current = self.ebeam.probeCurrent.choices - {self.ebeam.probeCurrent.value}
        new_current = next(iter(new_current))  # pick any value different from current
        self.ebeam.probeCurrent.value = new_current
        md = self.ccd.getMetadata()
        self.assertAlmostEqual(md[model.MD_BEAM_CURRENT], new_current)

        # Change accelVoltage => MD_BEAM_VOLTAGE must be updated
        orig_voltage = self.ebeam.accelVoltage.value
        voltage_range = self.ebeam.accelVoltage.range
        new_voltage = voltage_range[1] if orig_voltage == voltage_range[0] else voltage_range[0]
        self.ebeam.accelVoltage.value = new_voltage
        md = self.ccd.getMetadata()
        self.assertAlmostEqual(md[model.MD_BEAM_VOLTAGE], new_voltage)


if __name__ == "__main__":
    unittest.main()
