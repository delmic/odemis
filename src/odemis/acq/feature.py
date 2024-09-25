import glob
import json
import logging
import math
import os
import threading
import time
from concurrent import futures
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from typing import Dict, List, Tuple

from odemis import model
from odemis.acq.acqmng import (
    SettingsObserver,
    acquire,
    acquireZStack,
    estimateTime,
    estimateZStackAcquisitionTime,
)
from odemis.acq.move import FM_IMAGING, POSITION_NAMES, MicroscopePostureManager
from odemis.acq.stream import Stream
from odemis.dataio import find_fittest_converter
from odemis.util import dataio, executeAsyncTask
from odemis.util.dataio import data_to_static_streams, open_acquisition, splitext
from odemis.util.driver import estimate_stage_movement_time
from odemis.util.filename import create_filename

# The current state of the feature
FEATURE_ACTIVE, FEATURE_ROUGH_MILLED, FEATURE_POLISHED, FEATURE_DEACTIVE = (
    "Active",
    "Rough Milled",
    "Polished",
    "Discarded",
)


class CryoFeature(object):
    """
    Model class for a cryo interesting feature
    """

    def __init__(self, name, x, y, z, streams=None):
        """
        :param name: (string) the feature name
        :param x: (float) the X axis of the feature position
        :param y: (float) the Y axis of the feature position
        :param z: (float) the Z axis of the feature position
        :param streams: (List of StaticStream) list of acquired streams on this feature
        """
        self.name = model.StringVA(name)
        # The 3D position of an interesting point in the site (Typically, the milling should happen around that
        # volume, never touching it.)
        self.pos = model.TupleContinuous((x, y, z), range=((-1, -1, -1), (1, 1, 1)), cls=(int, float), unit="m")

        self.status = model.StringVA(FEATURE_ACTIVE)
        # TODO: Handle acquired files
        self.streams = streams if streams is not None else model.ListVA()


def get_features_dict(features: List[CryoFeature]) -> Dict[str, str]:
    """
    Convert list of features to JSON serializable list of dict
    :param features: list of CryoFeature
    :return: list of JSON serializable features
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


def save_features(project_dir: str, features: List[CryoFeature]) -> None:
    """
    Save the whole features list directly to the file
    :param project_dir: directory to save the file to (typically project directory)
    :param features: all the features to serialize
    """
    filename = os.path.join(project_dir, "features.json")
    with open(filename, "w") as jsonfile:
        json.dump(get_features_dict(features), jsonfile)


def read_features(project_dir: str) -> List[CryoFeature]:
    """
    Deserialize and return the features list from the json file
    :param project_dir: directory to read the file from (typically project directory)
    :return: list of deserialized featuers
    """
    filename = os.path.join(project_dir, "features.json")
    if not os.path.exists(filename):
        raise ValueError(f"Features file doesn't exists in this location. {filename}")
    with open(filename, "r") as jsonfile:
        return json.load(jsonfile, cls=FeaturesDecoder)


def load_project_data(path: str) -> dict:
    """load meteor project data from a directory:
    :param path: path to the project directory
    :return: dictionary containing the loaded data (features and overviews)
    """

    # load overview images
    overview_filenames = glob.glob(os.path.join(path, "*overview*.ome.tiff"))
    overview_data = []
    for fname in overview_filenames:
        # note: we only load the overview data, as the conversion to streams
        # is done in the localisation_tab.add_overview_data which also
        # handles assigning the streams throughout the gui
        overview_data.extend(open_acquisition(fname))

    features = []
    try:
        # read features
        features = read_features(path)
    except ValueError:
        logging.warning("No features.json file found in the project directory.")

    # load feature streams
    for f in features:
        # search dir for images matching f.name.value
        stream_filenames = glob.glob(os.path.join(path, f"*{f.name.value}*.ome.tiff"))
        for fname in stream_filenames:
            f.streams.value.extend(data_to_static_streams(open_acquisition(fname)))

    return {"overviews": overview_data, "features": features}


def add_feature_info_to_filename(feature: CryoFeature, filename: str) -> str:
    """
    Add details of the given feature and the counter at the end of the given filename.
    :param feature: the feature to add to the filename
    :param filename: filename given by user
    """
    path_base, ext = splitext(filename)
    feature_name = feature.name.value
    feature_status = feature.status.value

    path, basename = os.path.split(path_base)
    ptn = f"{basename}-{feature_name}-{feature_status}-{{cnt}}"

    return create_filename(path, ptn, ext, count="001")


# To handle the timeout error when the stage is not able to move to the desired position
# It logs the message and raises the MoveError exception
class MoveError(Exception):
    pass


# Time to wait for the stage to settle after moving
STAGE_WAIT_TIME = 3 # seconds
OBJECTIVE_WAIT_TIME = 2 # seconds


class CryoFeatureAcquisitionTask(object):
    """This class represents the task of acquiring data for a list of features."""

    def __init__(
        self,
        future: futures.Future,
        features: List[CryoFeature],
        stage: model.Actuator,
        focus: model.Actuator,
        streams: List[Stream],
        filename: str,
        zlevels: Dict[Stream, float] = {},
        settings_obs: SettingsObserver = None,
    ):
        self.features = features
        self.stage = stage
        self.focus = focus
        self.streams = streams
        self.zlevels = zlevels
        self.filename = filename
        self._settings_obs = settings_obs

        # Find the fittest converter for the given filename
        self.exporter = find_fittest_converter(filename)

        # Get the microscope and the posture manager
        microscope = model.getMicroscope()
        self.pm = MicroscopePostureManager(microscope)

        self._future = future
        if future is not None:
            self._future.running_subf = model.InstantaneousFuture()
            self._future._task_lock = threading.Lock()

    def cancel(self, future: futures.Future) -> bool:
        """
        Canceler of acquisition task.
        :param future: the future that will be executing the task
        :return: True if it successfully cancelled (stopped) the future
        """
        logging.debug("Canceling acquisition procedure...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Acquisition procedure cancelled.")
        return True

    def _acquire_feature(self, site: CryoFeature) -> None:
        """
        Acquire the data for the given feature and add to the given site.
        :param site: The feature to acquire.
        """
        data = []
        try:
            # check if cancellation happened while the acquiring future is working
            with self._future._task_lock:
                if self._future._task_state == CANCELLED:
                    raise CancelledError()
                if self.zlevels:
                    self._future.running_subf = acquireZStack(
                        self.streams, self.zlevels, self._settings_obs
                    )
                else:  # no zstack
                    self._future.running_subf = acquire(
                        self.streams, self._settings_obs
                    )
            data, exp = self._future.running_subf.result()
            if exp:
                logging.error(
                    f"Acquisition for feature {site.name.value} partially failed: {exp}"
                )
        except Exception as exp:
            logging.error(f"Acquisition for feature {site.name.value} failed: {exp}")

        # Check on the acquired data
        if not data:
            logging.warning(
                "The acquired data array in stream %s for feature %s is empty",
                self.streams,
                site.name,
            )
            return

        # export the data
        self._export_data(site, data)

        # Convert the data to StaticStreams, and add them to the feature
        new_streams = dataio.data_to_static_streams(data)
        site.streams.value.extend(new_streams)
        logging.info(f"The acquisition of {self.streams} for {site.name.value} is done")

    def _move_to_site(self, site: CryoFeature):
        """
        Move the stage to the given site and move the objective lens to position.
        :param site: The site to move to.
        :raises MoveError: if the stage failed to move to the given site.
        """
        # NOTE: site.pos is a tuple of (stage_x, stage_y, objective_z) coordinates
        stage_pos = {
            "x": site.pos.value[0],
            "y": site.pos.value[1],
        }
        focus_pos = {"z": site.pos.value[2]}
        logging.debug(f"For feature {site.name.value} moving the stage to {stage_pos} m")
        self._future.running_subf = self.stage.moveAbs(stage_pos)

        # estimate the time to move the stage
        t = estimate_stage_movement_time(
            stage=self.stage,
            start_pos=self.stage.position.value,
            end_pos=stage_pos,
            axes=["x", "y"],
            independent_axes=True,
        )
        t = t * 5 + 3 # adding extra margin
        try:
            self._future.running_subf.result(t)
        except TimeoutError:
            self._future.running_subf.cancel()
            raise MoveError(
                f"Failed to move the stage for feature {site.name.value} within {t} s"
            )

        logging.debug(
            "For feature %s moving the objective to %s m", site.name.value, focus_pos
        )
        self._future.running_subf = self.focus.moveAbs(focus_pos)

        # objective move shouldn't take longer than 2 seconds
        t = OBJECTIVE_WAIT_TIME * 2 # adding extra margin
        try:
            self._future.running_subf.result(t)
        except TimeoutError:
            self._future.running_subf.cancel()
            raise MoveError(
                f"Failed to move the objective for feature {site.name.value} within {t} s"
            )

    def estimate_acquisition_time(self) -> float:
        """Estimate the acquisition time for the acquisition task."""
        if self.zlevels:
            acq_time = estimateZStackAcquisitionTime(self.streams, self.zlevels)
        else:  # no zstack
            acq_time = estimateTime(self.streams)
        logging.debug(f"Estimated total acquisition time {acq_time}s")
        return acq_time

    def estimate_movement_time(self) -> float:
        """Estimate the movement time for the acquisition task."""

        # calculate the time to move between each position
        positions = []
        for f in self.features:
            if f.status.value == FEATURE_DEACTIVE:
                continue
            positions.append(f.pos.value)

        stage_movement_time = 0
        for start, end in zip(positions[0:-1], positions[1:]):
            stage_movement_time += estimate_stage_movement_time(
                                    stage=self.stage,
                                    start_pos={"x": start[0], "y": start[1]},
                                    end_pos={"x": end[0], "y": end[1]},
                                    axes=["x", "y"], independent_axes=True)

        # add the time to wait for the stage to settle
        expected_stage_time = stage_movement_time + STAGE_WAIT_TIME * len(positions)
        logging.debug(f"Estimated total stage movement time {stage_movement_time}s")

        # estimate the time to move the objective
        expected_objective_time = 1 * len(positions) # 1 second for objective movement (estimated)
        logging.debug(f"Estimated total objective movement time {expected_objective_time}s")

        # total movement time
        expected_movement_time = expected_stage_time + expected_objective_time
        logging.debug(f"Estimated total movement time {expected_movement_time}s")
        return expected_movement_time

    def estimate_total_time(self) -> float:
        """Estimate the total time for the acquisition task."""
        n_active_features = sum(f.status.value != FEATURE_DEACTIVE for f in self.features)
        expected_movement_time = self.estimate_movement_time()
        expected_acq_time = self.estimate_acquisition_time()
        total_time = expected_acq_time * n_active_features + expected_movement_time
        logging.debug(f"Estimated total time {total_time}s for {n_active_features} features")
        return total_time

    def run(self) -> Tuple[List[model.DataArray], Exception]:
        """Run the acquisition task."""
        exp = None
        self._future._task_state = RUNNING
        self._future.set_progress(end=time.time() + self.estimate_total_time() + 2)
        try:
            with self._future._task_lock:
                if self._future._task_state == CANCELLED:
                    raise CancelledError()

                # move to FM IMAGING posture
                current_posture = self.pm.getCurrentPostureLabel()
                logging.debug(f"Current Posture: {POSITION_NAMES[current_posture]}")

                if current_posture != FM_IMAGING:
                    raise ValueError(f"Currently only supported when at {POSITION_NAMES[FM_IMAGING]} posture")
                    # TODO: enable this when the cryo switch is fixed
                    logging.debug(
                        f"Moving to {POSITION_NAMES[FM_IMAGING]} from {POSITION_NAMES[current_posture]}"
                    )
                    self._future.running_subf = self.pm.cryoSwitchSamplePosition(FM_IMAGING)
                    self._future.running_subf.result()

            for feature in self.features:
                logging.debug(f"starting acquisition task for {feature.name.value}")

                if feature.status.value == FEATURE_DEACTIVE:
                    logging.info(f"Skipping feature {feature.name.value} because it is deactivated")
                    continue

                # move to feature position
                self._move_to_site(feature)

                # acquire data
                self._acquire_feature(feature)

        except CancelledError:
            logging.debug("Stopping because acquisition was cancelled")
            raise
        except Exception as exp:
            logging.exception(f"The acquisition failed: {exp}")
            raise
        finally:
            self._future._task_state = FINISHED

        return [], exp

    def _export_data(self, feature: CryoFeature, data: List[model.DataArray]) -> None:
        """
        Called to export the acquired data.
        data: the returned data/images from the future
        """

        filename = add_feature_info_to_filename(feature, self.filename)

        self.exporter.export(filename, data)
        logging.info("Acquisition saved as file '%s'.", filename)


def acquire_at_features(
    features: List[CryoFeature],
    stage: model.Actuator,
    focus: model.Actuator,
    streams: List[Stream],
    filename: str,
    zlevels: Dict[Stream, float] = {},
    settings_obs: SettingsObserver = None,
) -> futures.Future:
    """
    Acquire data at the given features.
    :param features: The features to acquire data at.
    :param stage: The stage to move to the features.
    :param focus: The focuser to move to the features.
    :param streams: The streams to acquire data from.
    :param filename: The filename to save the acquired data to.
    :param zlevels: The z-levels to acquire data at for each stream.
    :param settings_obs: The settings observer to use for the acquisition.
    :return: The future of the acquisition task.
    """
    # Create a future for the acquisition task
    future = model.ProgressiveFuture()
    task = CryoFeatureAcquisitionTask(
        future=future,
        features=features,
        stage=stage,
        focus=focus,
        streams=streams,
        filename=filename,
        zlevels=zlevels,
        settings_obs=settings_obs,
    )

    # Assign the cancellation function to the future
    future.task_canceller = task.cancel

    # set progress of the future
    future.set_end_time(time.time() + task.estimate_total_time())

    # assign the acquisition task to the future
    executeAsyncTask(future, task.run)

    return future
