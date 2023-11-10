import json
import os

from odemis import model

# The current state of the feature
FEATURE_ACTIVE, FEATURE_ROUGH_MILLED, FEATURE_POLISHED, FEATURE_DEACTIVE = "Active", "Rough Milled", "Polished", "Discarded"


class CryoFeature(object):
    """
    Model class for a cryo interesting feature
    """

    def __init__(self, name, x, y, z, d=None, streams=None):
        """
        :param name: (string) the feature name
        :param x: (float) the X axis of the feature position
        :param y: (float) the Y axis of the feature position
        :param z: (float) the Z axis of the feature position
        :param d: (float) distance of feature position from focus
        :param streams: (List of StaticStream) list of acquired streams on this feature
        """
        self.name = model.StringVA(name)
        # The 3D position of an interesting point in the site (Typically, the milling should happen around that
        # volume, never touching it.)
        if d:
            self.pos = model.TupleContinuous((x, y, z, d), range=((-1, -1, -1, -1), (1, 1, 1, 1)), cls=(int, float),
                                             unit="m")
        else:
            self.pos = model.TupleContinuous((x, y, z), range=((-1, -1, -1), (1, 1, 1)), cls=(int, float), unit="m")
        self.status = model.StringVA(FEATURE_ACTIVE)
        # TODO: Handle acquired files
        self.streams = streams if streams is not None else model.ListVA()


def get_features_dict(features):
    """
    Convert list of features to JSON serializable list of dict
    :param features: (list) list of CryoFeature
    :return: (dict) list of JSON serializable features
    """
    flist = []
    for feature in features:
        feature_item = {'name': feature.name.value, 'pos': feature.pos.value,
                        'status': feature.status.value}
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
            feature = CryoFeature(obj['name'], pos[0], pos[1], pos[2])
            feature.status.value = obj['status']
            return feature
        if 'feature_list' in obj:
            return obj['feature_list']


def save_features(project_dir, features):
    """
    Save the whole features list directly to the file
    :param project_dir: (string) directory to save the file to (typically project directory)
    :param features: (list of Features) all the features to serialize
    """
    filename = os.path.join(project_dir, "features.json")
    with open(filename, 'w') as jsonfile:
        json.dump(get_features_dict(features), jsonfile)


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
