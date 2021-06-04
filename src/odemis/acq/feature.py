from numpy import long

from odemis import model

# The current state of the feature
FEATURE_ACTIVE, FEATURE_MILLED, FEATURE_DEACTIVE = "Active", "Milled", "Discarded"


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
        :param milling_angle: (float)  angle used for milling (angle between the sample and the ion-beam, similar to the one in the chamber tab, not the actual Rx)
        :param streams: (List of StaticStream) list of acquired streams on this feature
        """
        self.name = model.StringVA(name)
        # The 3D position of an interesting point in the site (Typically, the milling should happen around that
        # volume, never touching it.)
        self.pos = model.TupleContinuous((x, y, z), range=((-1, -1, -1), (1, 1, 1)), cls=(long, float), )
        if milling_angle <= 0:
            raise ValueError(f"Milling should be > 0, but got {milling_angle}")
        self.milling_angle = model.FloatVA(milling_angle)
        # TODO: Get the existing feature status
        self.status = model.StringVA(FEATURE_ACTIVE, )
        self.streams = streams if streams is not None else model.ListVA()


def new_feature_name(length):
    """
    Create new feature name based on the feature list length
    :param length: current len() of feature list
    :return: (string) feature new name
    """
    return f"Feature {length + 1}"
