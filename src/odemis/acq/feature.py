import copy
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

import yaml

from odemis import model
from odemis.acq.acqmng import (
    SettingsObserver,
    acquire,
    acquireZStack,
    estimateTime,
    estimateZStackAcquisitionTime,
)
from odemis.acq.align.autofocus import AutoFocus, estimateAutoFocusTime
from odemis.acq.milling.tasks import MillingTaskSettings, load_milling_tasks
from odemis.acq.milling.tasks import __file__ as milling_tasks_file
from odemis.acq.move import (
    FM_IMAGING,
    MILLING,
    POSITION_NAMES,
    SEM_IMAGING,
    MicroscopePostureManager,
)
from odemis.acq.stitching._tiledacq import SAFE_REL_RANGE_DEFAULT
from odemis.acq.stream import Stream
from odemis.dataio import find_fittest_converter
from odemis.util import dataio, executeAsyncTask
from odemis.util.comp import generate_zlevels
from odemis.util.dataio import data_to_static_streams, open_acquisition, splitext
from odemis.util.driver import estimate_stage_movement_time
from odemis.util.filename import create_filename

# The current state of the feature
FEATURE_ACTIVE, FEATURE_READY_TO_MILL, FEATURE_ROUGH_MILLED, FEATURE_POLISHED, FEATURE_DEACTIVE = (
    "Active",
    "Ready to Mill",
    "Rough Milled",
    "Polished",
    "Discarded",
)

# TODO: drift correction area for each feature?
MILLING_TASKS_PATH = os.path.join(os.path.dirname(milling_tasks_file), "milling_tasks.yaml")

class CryoFeature(object):
    """
    Model class for a cryo interesting feature
    """

    def __init__(self, name, stage_position: dict, fm_focus_position: dict, streams=None, milling_tasks=None):
        """
        :param name: (string) the feature name
        :param stage_position: (dict) the stage position of the feature (stage-bare)
        :param fm_focus_position: (dict) the focus position of the feature
        :param streams: (List of StaticStream) list of acquired streams on this feature
        """
        self.name = model.StringVA(name)
        self.stage_position = model.VigilantAttribute(stage_position, unit="m") # stage-bare
        self.fm_focus_position = model.VigilantAttribute(fm_focus_position, unit="m")
        self.posture_positions: Dict[str, Dict[str, float]] = {} # positions for each posture

        if milling_tasks is None:
            milling_tasks = load_milling_tasks(MILLING_TASKS_PATH)
        self.milling_tasks: Dict[str, MillingTaskSettings] = milling_tasks

        self.status = model.StringVA(FEATURE_ACTIVE)
        # TODO: Handle acquired files
        self.streams = streams if streams is not None else model.ListVA()

        # attributes for automated milling
        self.path: str = None
        self.reference_image: model.DataArray = None

    def set_posture_position(self, posture: str, position: Dict[str, float]) -> None:
        """
        Set the stage position for the given posture.
        :param posture: the posture to set the position for
        :param position: the position to set
        """
        self.posture_positions[posture] = position

    def get_posture_position(self, posture: str) -> Dict[str, float]:
        """
        Get the stage position for the given posture.
        :param posture: the posture to get the position for
        :return: the position for the given posture
        """
        return self.posture_positions.get(posture, None)

    def save_milling_task_data(self,
                               stage_position: Dict[str, float],
                               path: str,
                               reference_image: model.DataArray,
                               milling_tasks: Dict[str, MillingTaskSettings] = None
                               ) -> None:
        """Assign the milling task data to the feature and save the reference image.
        This information is required for the automated milling process.
        :param stage_position: the stage position of the feature for milling
        :param path: the path to save the feature data
        :param reference_image: the reference image of the feature (FIB)
        :param milling_tasks: the milling tasks for the feature (optional)
        """

        logging.info(f"Saving milling data for feature: {self.name.value}")

        # assign the milling tasks
        if milling_tasks is not None:
            self.milling_tasks = copy.deepcopy(milling_tasks)

        # create a directory for the feature data
        self.path = path
        os.makedirs(self.path, exist_ok=True)

        # assign the reference image
        self.reference_image = reference_image

        # save the reference image to disk
        filename = os.path.join(self.path, f"{self.name.value}-Reference-Alignment-FIB.ome.tiff") # TODO: check this for uniqueness? no, we want to overwrite
        exporter = find_fittest_converter(filename)
        exporter.export(filename, reference_image)

        # save the milling position (it can be updated by the user)
        self.set_posture_position(posture=MILLING, position=stage_position)

        # set the feature status to ready to mill
        self.status.value = FEATURE_READY_TO_MILL

        logging.info(f"Milling tasks: {self.milling_tasks}, path: {self.path}, Reference image: {filename}")
        logging.info(f"Stage position for milling: {self.get_posture_position(MILLING)}")
        logging.info(f"Feature {self.name.value} is ready to mill.")

def get_feature_position_at_posture(pm: MicroscopePostureManager, feature: CryoFeature, posture: int, recalculate: bool = False) -> Dict[str, float]:
    """Get the feature position at the given posture, if it doesn't exist, create it."""
    position = feature.get_posture_position(posture)

    # if the position doesn't exist at that posture, create it
    if position is None or recalculate:
        try:
            logging.info(f"Feature position for {feature.name.value} at {posture} posture doesn't exist. Creating it.")
            position = pm.to_posture(feature.stage_position.value, posture)
            feature.set_posture_position(posture=posture, position=position)
        except Exception as e:
            logging.error(f"Error while converting feature position to {posture} posture: {e}")
            return None
    return position

def get_features_dict(features: List[CryoFeature]) -> Dict[str, str]:
    """
    Convert list of features to JSON serializable list of dict
    :param features: list of CryoFeature
    :return: list of JSON serializable features
    """
    flist = []
    for feature in features:
        feature_item = {'name': feature.name.value,
                        'status': feature.status.value,
                        'stage_position': feature.stage_position.value,
                        'fm_focus_position': feature.fm_focus_position.value,
                        'posture_positions': feature.posture_positions,
                        "milling_tasks": {k: v.to_json() for k, v in feature.milling_tasks.items()},
                        }
        if feature.path:
            feature_item['path'] = feature.path
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
        if 'name' in obj and 'status' in obj:
            stage_position = obj['stage_position']
            fm_focus_position = obj['fm_focus_position']
            posture_positions = obj.get('posture_positions', {})
            milling_task_json = obj.get('milling_tasks', {})
            feature = CryoFeature(name=obj['name'],
                                  stage_position=stage_position,
                                  fm_focus_position=fm_focus_position
                                  )
            feature.status.value = obj['status']
            feature.posture_positions = {int(k): v for k, v in posture_positions.items()} # convert keys to int
            feature.milling_tasks = {k: MillingTaskSettings.from_json(v) for k, v in milling_task_json.items()}
            feature.path = obj.get('path', None)

            # load the reference image
            if feature.path:
                filename = os.path.join(feature.path, f"{feature.name.value}-Reference-Alignment-FIB.ome.tiff")
                if os.path.exists(filename):
                    feature.reference_image = open_acquisition(filename)[0]
                else:
                    logging.warning(f"Reference image for feature {feature.name.value} not found in {filename}")
            return feature
        if 'feature_list' in obj:
            return obj['feature_list']
        return obj

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
        stream_filenames = []
        glob_path = os.path.join(path, f"*-{glob.escape(f.name.value)}-{{ext}}")
        for ext in ["*.tif", "*.tiff", "*.h5"]:
            stream_filenames.extend(glob.glob(glob_path.format(ext=ext)))

        for fname in stream_filenames:
            f.streams.value.extend(data_to_static_streams(open_acquisition(fname)))

    return {"overviews": overview_data, "features": features}

def import_features_from_autolamella(path: str) -> List[CryoFeature]:
    """Import feature positions from an autolamella experiment and convert them to odemis features.
    :param path: path to the autolamella experiment directory
    :return: list of CryoFeature
    """

    with open(os.path.join(path, "experiment.yaml"), "r") as f:
        exp = yaml.load(f, Loader=yaml.FullLoader)

    # get the relevant components
    pm = MicroscopePostureManager(model.getMicroscope())

    cryo_features = []
    for lamella in exp["positions"]:  # get the position
        pos = lamella["state"]["microscope_state"]["stage_position"]
        name = pos["name"]

        # remap r->rz, t->rx
        pos["rx"] = pos.pop("t")
        pos["rz"] = pos.pop("r")

        # apply raw coordinate system offset (x, y only) # TODO: can't be used like this with sample-stage, need to fix
        # if hasattr(pm.stage, "_raw_offset"):
        # pos["x"] += pm.stage._raw_offset["x"]
        # pos["y"] += pm.stage._raw_offset["y"]

        posture = pm.getCurrentPostureLabel(pos=pos)
        logging.info(
            f"Feature: {name}, pos: {pos}, Posture: {POSITION_NAMES[posture]}"
        )  # stage-bare

        # NOTE: for now, we should check this is in SEM Imaging and skip if not for safety
        if posture != SEM_IMAGING:
            logging.warning(
                f"Cryo feature {name} is not in SEM Imaging posture, skipping."
            )
            continue

        # create feature
        # TODO: we need to handle this better, the focus may be anywhere? maybe use the active position?
        focus_pos = model.getComponent(role="focus").getMetadata()[model.MD_FAV_POS_ACTIVE]
        cryo_feat = CryoFeature(
            name=name,
            stage_position=pos,
            fm_focus_position=focus_pos,
        )

        cryo_feat.set_posture_position(SEM_IMAGING, pos)
        cryo_feat.set_posture_position(FM_IMAGING, pm.to_posture(pos, FM_IMAGING))

        cryo_features.append(cryo_feat)

    return cryo_features

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

def _create_fibsem_filename(filename: str, acq_type: str) -> str:
    """
    Create a filename for FIBSEM images.
    :param filename: filename given by user
    :param acq_type: the type of acquisition (FIB or SEM)
    :return: the created filename
    """
    path_base, ext = splitext(filename)
    path, basename = os.path.split(path_base)
    ptn = f"{basename}-{acq_type}-{{cnt}}"

    return create_filename(path, ptn, ext, count="001")

# To handle the timeout error when the stage is not able to move to the desired position
# It logs the message and raises the MoveError exception
class MoveError(Exception):
    pass


# Time to wait for the stage to settle after moving
STAGE_WAIT_TIME = 1.5   # seconds
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
        zparams: Dict[str, float] = {},
        settings_obs: SettingsObserver = None,
        use_autofocus: bool = True,
        autofocus_conf_level: float = 0.8,
    ):
        self.features = features
        self.stage = stage
        self.focus = focus
        self.streams = streams
        self.zparams = zparams
        self.filename = filename
        self._settings_obs = settings_obs

        # autofocus settings
        self.use_autofocus = use_autofocus
        self.autofocus_conf_level = autofocus_conf_level

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
            # check if cancellation happened while the acquiring future is working (before autofocus)
            with self._future._task_lock:
                if self._future._task_state == CANCELLED:
                    raise CancelledError()

                if self.use_autofocus:
                    self._run_autofocus(site)

            # check if cancellation happened while the acquiring future is working (before acquisition)
            with self._future._task_lock:
                if self._future._task_state == CANCELLED:
                    raise CancelledError()

                if self.zparams:
                    # recalculate the zlevels based on the zparms
                    # we need the zmin, zmax, zstep, and the new focus position
                    zmin, zmax, zstep = self.zparams["zmin"], self.zparams["zmax"], self.zparams["zstep"]
                    levels = generate_zlevels(focuser=self.focus, zrange=(zmin, zmax), zstep=zstep)

                    # Only use zstack for the optical streams (not SEM), as that's the ones
                    # the user is interested in on the METEOR/ENZEL.
                    zlevels = {s: levels for s in self.streams}
                    logging.warning(f"The zlevels for feature {site.name.value} are {zlevels}")

                    self._future.running_subf = acquireZStack(
                        self.streams, zlevels, self._settings_obs
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

    def _run_autofocus(self, site: CryoFeature) -> None:
        """Run the autofocus for the given feature."""

        # TODO: allow the user to select the autofocus stream, rather than just using the first one

        try:
            # run the autofocus at the current position
            logging.debug(f"running autofocus at {site.name.value} at focus: {self.focus.position.value}, req conf: {self.autofocus_conf_level}")
            rel_rng = SAFE_REL_RANGE_DEFAULT
            focus_rng = (self.focus.position.value["z"] + rel_rng[0], self.focus.position.value["z"] + rel_rng[1])
            self._future._running_subf = AutoFocus(detector=self.streams[0].detector,
                                                        emt=None,
                                                        focus=self.focus,
                                                        rng_focus=focus_rng)

            # note: the auto focus moves the objective to the best position
            foc_pos, foc_lev, conf = self._future._running_subf.result(timeout=900)
            if conf >= self.autofocus_conf_level:

                # update the feature focus position
                site.fm_focus_position.value = {"z": foc_pos}  # NOTE: tuples cant do assignment so we need to replace the whole tuple
                logging.debug(f"auto focus succeeded at {site.name.value} with conf:{conf}. new focus position: {foc_pos}")
            else:
                # if the confidence is low, restore the previous focus position
                self._move_focus(site, site.fm_focus_position.value)
                logging.debug(f"auto focus failed due at {site.name.value} with conf:{conf}. restoring focus position {site.fm_focus_position.value}")

        except TimeoutError as e:
            logging.debug(f"Timed out during autofocus at {site.name.value}. {e}")
            self._future._running_subf.cancel()

            # restore the previous focus position
            self._move_focus(site, site.fm_focus_position.value)
            logging.warning(f"auto focus timed out at {site.name.value}. restoring focus position {site.fm_focus_position.value}")

    def _move_to_site(self, site: CryoFeature):
        """
        Move the stage to the given site and move the objective lens to position.
        :param site: The site to move to.
        :raises MoveError: if the stage failed to move to the given site.
        """
        stage_position = get_feature_position_at_posture(pm=self.pm, feature=site, posture=FM_IMAGING)      # stage-bare
        fm_focus_position = site.fm_focus_position.value
        logging.debug(f"For feature {site.name.value} moving the stage to {stage_position}")
        self._future.running_subf = self.stage.moveAbs(stage_position)

        # estimate the time to move the stage
        t = estimate_stage_movement_time(
            stage=self.stage,
            start_pos=self.stage.position.value,
            end_pos=stage_position,
            axes=["x", "y", "z"],
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
            "For feature %s moving the objective to %s m", site.name.value, fm_focus_position
        )
        self._move_focus(site, fm_focus_position)

    def _move_focus(self, site: CryoFeature, fm_focus_position: Dict[str, float]) -> None:
        """Move the focus to the given position."""
        self._future.running_subf = self.focus.moveAbs(fm_focus_position)

        # objective move shouldn't take longer than 2 seconds
        t = OBJECTIVE_WAIT_TIME * 2 # adding extra margin
        try:
            self._future.running_subf.result(t)
        except TimeoutError:
            self._future.running_subf.cancel()
            raise MoveError(
                f"Failed to move the objective for feature {site.name.value} within {t} s"
            )

    def _generate_zlevels(self, zmin: float, zmax: float, zstep: float) -> Dict[Stream, List[float]]:
        """Generate the zlevels for the zstack acquisition in the required format."""
        # calculate the zlevels for the zstack acquisition
        levels = generate_zlevels(self.focus, (zmin, zmax), zstep)
        zlevels = {s: levels for s in self.streams}
        logging.debug(f"Generated zlevels for the zstack acquisition: {zlevels}")
        return zlevels

    def estimate_acquisition_time(self) -> float:
        """Estimate the acquisition time for the acquisition task, including autofocus."""

        autofocus_time = 0
        if self.use_autofocus:
            autofocus_time = estimateAutoFocusTime(self.streams[0].detector, None, steps=20)

        if self.zparams:
            zlevels = self._generate_zlevels(zmin=self.zparams["zmin"],
                                             zmax=self.zparams["zmax"],
                                             zstep=self.zparams["zstep"])
            acq_time = estimateZStackAcquisitionTime(self.streams, zlevels)
        else:  # no zstack
            acq_time = estimateTime(self.streams)
        logging.debug(f"Estimated total acquisition time {acq_time}s, autofocus time {autofocus_time}s")
        return acq_time + autofocus_time

    def estimate_movement_time(self) -> float:
        """Estimate the movement time for the acquisition task."""

        # calculate the time to move between each position
        positions = []
        for f in self.features:
            if f.status.value == FEATURE_DEACTIVE:
                continue
            positions.append(get_feature_position_at_posture(pm=self.pm,
                                                             feature=f,
                                                             posture=FM_IMAGING))

        stage_movement_time = 0
        for start, end in zip(positions[0:-1], positions[1:]):
            stage_movement_time += estimate_stage_movement_time(
                                    stage=self.stage,
                                    start_pos=start, end_pos=end,
                                    axes=["x", "y", "z"], independent_axes=True)

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
        self._future.set_progress(end=time.time() + self.estimate_total_time() + 2) # +2 for pessimistic margin
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

        # add feature name to the data description
        for d in data:
            name = feature.name.value
            status = feature.status.value
            d.metadata[model.MD_DESCRIPTION] = f"{name}-{status}-{d.metadata[model.MD_DESCRIPTION]}"

        self.exporter.export(filename, data)
        logging.info("Acquisition saved as file '%s'.", filename)


def acquire_at_features(
    features: List[CryoFeature],
    stage: model.Actuator,
    focus: model.Actuator,
    streams: List[Stream],
    filename: str,
    zparams: Dict[str, float] = {},
    settings_obs: SettingsObserver = None,
    use_autofocus: bool = False,
) -> futures.Future:
    """
    Acquire data at the given features.
    :param features: The features to acquire data at.
    :param stage: The stage to move to the features.
    :param focus: The focuser to move to the features.
    :param streams: The streams to acquire data from.
    :param filename: The filename to save the acquired data to.
    :param zparams: The z-levels parameters to acquire data at for each stream.
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
        zparams=zparams,
        settings_obs=settings_obs,
        use_autofocus=use_autofocus,
    )

    # Assign the cancellation function to the future
    future.task_canceller = task.cancel

    # set progress of the future
    future.set_end_time(time.time() + task.estimate_total_time())

    # assign the acquisition task to the future
    executeAsyncTask(future, task.run)

    return future

def calculate_stage_tilt_from_milling_angle(milling_angle: float, pre_tilt: float, column_tilt: int = math.radians(52)) -> float:
    """Calculate the stage tilt from the milling angle and the pre-tilt.
    :param milling_angle: the milling angle in radians
    :param pre_tilt: the pre-tilt in radians
    :param column_tilt: the column tilt in radians (default TFS = 52deg)
    :return: the stage tilt in radians
    """
    # Equation:
    # MillingAngle = 90 - ColumnTilt + StageTilt - PreTilt
    # StageTilt = MillingAngle + PreTilt + ColumnTilt - 90

    # calculate the stage tilt from the milling angle and the pre-tilt
    stage_tilt = milling_angle + pre_tilt + column_tilt - math.radians(90)
    return stage_tilt
