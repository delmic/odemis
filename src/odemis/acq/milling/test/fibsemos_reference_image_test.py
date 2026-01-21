import unittest

import numpy

from odemis import model
from odemis.acq.feature import CryoFeature
from odemis.acq.milling.fibsemos import _get_reference_image


class TestResolveFeatureReferenceImage(unittest.TestCase):
    def test_returns_in_memory_reference_image(self):
        feature = CryoFeature(name="f1", stage_position={}, fm_focus_position={})
        da = model.DataArray(numpy.zeros((10, 12), dtype=numpy.uint16), metadata={model.MD_DIMS: "YX"})
        feature.reference_image = da

        out = _get_reference_image(feature)
        self.assertIs(out, da)

    def test_raises_if_missing_in_memory(self):
        feature = CryoFeature(name="f1", stage_position={}, fm_focus_position={})
        setattr(feature, "reference_image", None)

        with self.assertRaises(ValueError):
            _get_reference_image(feature)


if __name__ == "__main__":
    unittest.main()
