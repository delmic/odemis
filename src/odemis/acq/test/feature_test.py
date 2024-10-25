#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import unittest

from odemis.acq.feature import (
    CryoFeature,
    FeaturesDecoder,
    get_features_dict,
    read_features,
    save_features,
)

logging.getLogger().setLevel(logging.DEBUG)


class TestFeatureEncoderDecoder(unittest.TestCase):
    """
    Test the json encoder and decoder of the CryoFeature class
    """
    test_str = '{"feature_list": [{"name": "Feature-1", "status": "Active", "stage_position": {"x": 0, "y": 0, "z": 0}, "fm_focus_position": {"z": 0}, "posture": "FM", "posture_positions": {}}, '+ \
               '{"name": "Feature-2", "status": "Active", "stage_position": {"x": 0.001, "y": 0.001, "z": 0.001}, "fm_focus_position": {"z": 0.002}, "posture": "FM", "posture_positions": {}}]}'

    def test_feature_encoder(self):
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3})
        features = [feature1, feature2]
        json_str = json.dumps(get_features_dict(features))
        self.assertEqual(json_str, self.test_str)

    def test_feature_decoder(self):
        features = json.loads(self.test_str, cls=FeaturesDecoder)
        self.assertEqual(len(features), 2)
        self.assertEqual(features[0].name.value, "Feature-1")
        self.assertEqual(features[0].status.value, "Active")
        self.assertEqual(features[1].stage_position.value, {"x": 1e-3, "y": 1e-3, "z": 1e-3})
        self.assertEqual(features[1].fm_focus_position.value, {"z": 2e-3})

    def test_save_read_features(self):
        feature1 = CryoFeature("Feature-1", stage_position={"x": 0, "y": 0, "z": 0}, fm_focus_position={"z": 0})
        feature2 = CryoFeature("Feature-2", stage_position={"x": 1e-3, "y": 1e-3, "z": 1e-3}, fm_focus_position={"z": 2e-3})

        features = [feature1, feature2]
        save_features("", features)
        r_features = read_features("")
        self.assertEqual(len(features), len(r_features))
        self.assertEqual(features[0].name.value, r_features[0].name.value)


if __name__ == "__main__":
    unittest.main()
