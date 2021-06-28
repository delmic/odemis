import json
import logging
import os

from numpy import long

from odemis import model
# The current state of the feature
from odemis.gui.model import DEFAULT_MILLING_ANGLE

FEATURE_ACTIVE, FEATURE_ROUGH_MILLED, FEATURE_POLISHED, FEATURE_DEACTIVE = "Active", "Rough Milled", "Polished", "Discarded"


class CryoFeature(object):
    """
    Model class for a cryo interesting feature
    """

    def __init__(self, name, x, y, z, milling_angle, streams=None):
        """
        :param name: (string) the feature name
        :param x: (float) the X axis of the feature position
        :param y: (float) the Y axis of the feature position
        :param z: (float) the Z axis of the feature position
        :param milling_angle: (float)  angle used for milling (angle between the sample and the ion-beam, similar to the
        one in the chamber tab, not the actual Rx)
        :param streams: (List of StaticStream) list of acquired streams on this feature
        """
        self.name = model.StringVA(name)
        # The 3D position of an interesting point in the site (Typically, the milling should happen around that
        # volume, never touching it.)
        self.pos = model.TupleContinuous((x, y, z), range=((-1, -1, -1), (1, 1, 1)), cls=(long, float), )
        # TODO: Check if negative milling angle is allowed
        if milling_angle <= 0:
            milling_angle = DEFAULT_MILLING_ANGLE
            logging.warning(f"Given milling angle {milling_angle} is negative, setting it to default {DEFAULT_MILLING_ANGLE}")
        self.milling_angle = model.FloatVA(milling_angle)
        self.status = model.StringVA(FEATURE_ACTIVE, )
        # TODO: Handle acquired files
        self.streams = streams if streams is not None else model.ListVA()


class FeaturesEncoder(json.JSONEncoder):
    """
    Json encoder for the CryoFeature class and its attributes
    """

    def default(self, features):
        flist = []
        for feature in features.value:
            feature_item = {'name': feature.name.value, 'pos': feature.pos.value,
                            'milling_angle': feature.milling_angle.value, 'status': feature.status.value}
            flist.append(feature_item)
        return {'feature_list': flist}


class FeaturesDecoder(json.JSONDecoder):
    """
    Json decoder for the CryoFeature class and its attributes
    """

    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        # Either the object is the feature list or the feature objects inside it
        if 'name' in obj:
            pos = obj['pos']
            feature = CryoFeature(obj['name'], pos[0], pos[1], pos[2], obj['milling_angle'])
            feature.status.value = obj['status']
            return feature
        if 'feature_list' in obj:
            return obj['feature_list']


def save_features(project_dir, features):
    """
    Save the whole features list directly to the file
    :param project_dir: (string) directory to save the file to (typically project directory)
    :param features: (list of CryoFeature) list of features to serialize
    """
    filename = os.path.join(project_dir, "features.json")
    with open(filename, 'w') as jsonfile:
        json.dump(features, jsonfile, cls=FeaturesEncoder)


def read_features(project_dir):
    """
    Deserialize and return the features list from the json file
    :param project_dir: (string) directory to read the file from (typically project directory)
    :return: (list of CryoFeature) list of deserialized featuers
    """
    filename = os.path.join(project_dir, "features.json")
    if not os.path.exists(filename):
        raise ValueError(f"Features file doesn't exists in this location. {filename}")
    with open(filename, 'r') as jsonfile:
        return json.load(jsonfile, cls=FeaturesDecoder)
