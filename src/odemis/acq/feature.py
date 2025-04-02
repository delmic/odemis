import glob
import itertools
import json
import logging
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
from odemis.acq.align.autofocus import AutoFocus, estimateAutoFocusTime
from odemis.acq.move import FM_IMAGING, POSITION_NAMES, MicroscopePostureManager
from odemis.acq.stitching._tiledacq import SAFE_REL_RANGE_DEFAULT
from odemis.acq.stream import Stream, StaticFluoStream
from odemis.dataio import find_fittest_converter
from odemis.util import dataio, executeAsyncTask
from odemis.util.comp import generate_zlevels
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

FIDUCIAL, POI, SURFACE_FIDUCIAL, PROJECTED_FIDUCIAL, PROJECTED_POI = (
    "Fiducial",
    "PointOfInterest",
    "SurfaceFiducial",
    "ProjectedPoints",
    "ProjectedPOI",
)


class FIBFMCorrelationData:
    """
    Class consisting of parameters related to the multipoint correlation between FIB and FM. The multipoint correlation
    uses external three-dimensional correlation toolbox (3DCT) to perform correlation. This class is used to store the input
    and output parameters of the correlation. The input parameters are the fiducials, points of interest in the FM
    image and the position of top surface of the lamella. Fiducials are points which are distinctly visible in both views
    whereas point of interest is visible more in FM than FIB. The multipoint correlation helps in finding the POI in FIB
    by using the FM-FIB fiducial pairs and point of interest in FM. The output parameters are the correlation results,
    the projected fiducials and points of interest in the FIB image. The projected fiducials are the reprojection of FIB
    fiducaials according to the correlation results. Minimum 4 FM and FIB fiducial pairs with one point of interest
    in FM is required to execute correlation. This class is used to store the correlation data for a feature
    at a defined status.
    """

    def __init__(self):
        # Input parameters of multipoint correlation. The input is provided by the user in the GUI
        # The fm_pois are the points of interest in the FM image for which the poi in FIB is calculated using
        # correlation. The fm_fiducials and fib fiducials are pairs of distinct fiducials in FM and FIB images
        # respectively which are used to calculate the transformation matrix between the two images.
        self.fm_pois: List[Target] = []
        self.fm_fiducials: List[Target] = []
        self.fib_fiducials: List[Target] = []
        # The fib surface fiducial is the target information provided by the user indicating the top surface of lamella
        # in FIB image. This is used to correct the projected fiducials/poi in the FIB image based on the thickness of
        # the lamella.
        self.fib_surface_fiducial : Target = None
        self.fib_stream = None #:StaticSEMStream = None or StaticFIBStream = None
        self.fm_streams: List[StaticFluoStream] = []
        # key to find the stream used from all the acquired streams
        # key is a tuple of (stream shape, MD_POS)
        self.fib_stream_key = None
        self.fm_stream_key = None

        # Output parameters of multipoint correlation. The output is calculated from run_correlation function
        self.correlation_result = {}
        self.fib_projected_pois: List[Target] = []
        self.fib_projected_fiducials: List[Target] = []

    def clear(self):
        """Clear output parameters when any input parameters are changed"""
        self.correlation_result = {}
        self.fib_projected_pois = []
        self.fib_projected_fiducials = []

    def to_dict(self) -> Dict[str, List[Dict[str, List]]]:
        """
        Convert the FIBFMCorrelationData to a dictionary.
        :return: The dictionary representation of the FIBFMCorrelationData.
        """
        data = {
            "fm_fiducials": [fid.to_dict() for fid in self.fm_fiducials],
            "fm_pois": [poi.to_dict() for poi in self.fm_pois],
            "fib_fiducials": [fid.to_dict() for fid in self.fib_fiducials],
            "fib_projected_pois": [poi.to_dict() for poi in self.fib_projected_pois],
            "fib_projected_fiducials": [fid.to_dict() for fid in self.fib_projected_fiducials],
            "fib_surface_fiducial": self.fib_surface_fiducial.to_dict() if self.fib_surface_fiducial else None,
            "correlation_result": self.correlation_result,
            "fm_stream_key": self.fm_stream_key,
            "fib_stream_key": self.fib_stream_key,
        }
        return data

    @staticmethod
    def from_dict(data: Dict[str, List[Dict[str, List]]]) -> "FIBFMCorrelationData":
        """
        Convert the dictionary to FIBFMCorrelationData.
        :param data: The dictionary representation of the FIBFMCorrelationData.
        :return: The FIBFMCorrelationData object.
        """
        correlation_data = FIBFMCorrelationData()
        correlation_data.fm_fiducials = [Target.from_dict(fid) for fid in data["fm_fiducials"]]
        correlation_data.fm_pois = [Target.from_dict(poi) for poi in data["fm_pois"]]
        correlation_data.fib_fiducials = [Target.from_dict(fid) for fid in data["fib_fiducials"]]
        correlation_data.fib_projected_pois = [Target.from_dict(poi) for poi in data["fib_projected_pois"]]
        correlation_data.fib_projected_fiducials = [Target.from_dict(fid) for fid in data["fib_projected_fiducials"]]
        correlation_data.fib_surface_fiducial = Target.from_dict(data["fib_surface_fiducial"]) if data["fib_surface_fiducial"] else None
        correlation_data.correlation_result = data["correlation_result"]
        correlation_data.fm_stream_key = data["fm_stream_key"]
        correlation_data.fib_stream_key = data["fib_stream_key"]
        return correlation_data


class CryoFeature(object):
    """
    Model class for a cryo interesting feature
    """

    def __init__(self, name, x, y, z, streams=None, correlation_data=None):
        """
        :param name: (string) the feature name
        :param x: (float) the X axis of the feature position
        :param y: (float) the Y axis of the feature position
        :param z: (float) the Z axis of the feature position
        :param streams: (List of StaticStream) list of acquired streams on this feature
        :param correlation_data: (Dict[str,FIBFMCorrelationData]) Dictionary mapping the feature status to
        FIBFMCorrelationData, where feature status like Active, Rough Milled or polished is the key.
        """
        self.name = model.StringVA(name)
        # The 3D position of an interesting point in the site (Typically, the milling should happen around that
        # volume, never touching it.)
        self.pos = model.TupleContinuous((x, y, z), range=((-1, -1, -1), (1, 1, 1)), cls=(int, float), unit="m")

        self.status = model.StringVA(FEATURE_ACTIVE)
        # TODO: Handle acquired files
        self.streams = streams if streams is not None else model.ListVA()
        self.correlation_data = correlation_data


def get_features_dict(features: List[CryoFeature]) -> Dict[str, str]:
    """
    Convert list of features to JSON serializable list of dict
    :param features: list of CryoFeature
    :return: list of JSON serializable features
    """
    flist = []
    for feature in features:
        feature_item = {'name': feature.name.value,
                        'pos': feature.pos.value,
                        'status': feature.status.value,
                        'correlation_data': {k: v.to_dict() for k, v in feature.correlation_data.items()}} if feature.correlation_data  else None
        flist.append(feature_item)
    return {'feature_list': flist}


class FeaturesDecoder(json.JSONDecoder):
    """
    Json decoder for the CryoFeature class and its attributes
    """

    def __init__(self, *args, **kwargs):
        json.JSONDecoder.__init__(self, object_hook=self.object_hook, *args, **kwargs)

    def object_hook(self, obj):
        if 'name' in obj and 'pos' in obj:
            correlation_data = obj["correlation_data"]
            pos = obj['pos']
            feature = CryoFeature(obj['name'], pos[0], pos[1], pos[2])
            feature.status.value = obj['status']
            feature.correlation_data = {k: FIBFMCorrelationData.from_dict(v) for k, v in correlation_data.items()}
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
                pos = site.pos.value
                site.pos.value = (pos[0], pos[1], foc_pos)  # NOTE: tuples cant do assignment so we need to replace the whole tuple
                logging.debug(f"auto focus succeeded at {site.name.value} with conf:{conf}. new focus position: {foc_pos}")
            else:
                # if the confidence is low, restore the previous focus position
                self._move_focus(site, {"z": site.pos.value[2]})
                logging.debug(f"auto focus failed due at {site.name.value} with conf:{conf}. restoring focus position {site.pos.value[2]}")

        except TimeoutError as e:
            logging.debug(f"Timed out during autofocus at {site.name.value}. {e}")
            self._future._running_subf.cancel()

            # restore the previous focus position
            self._move_focus(site, {"z": site.pos.value[2]})
            logging.warning(f"auto focus timed out at {site.name.value}. restoring focus position {site.pos.value[2]}")

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
        self._move_focus(site, focus_pos)

    def _move_focus(self, site: CryoFeature, focus_pos: Dict[str, float]) -> None:
        """Move the focus to the given position."""
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
            rel_rng = SAFE_REL_RANGE_DEFAULT
            focus_rng = (self.focus.position.value["z"] + rel_rng[0], self.focus.position.value["z"] + rel_rng[1])
            autofocus_time = estimateAutoFocusTime(detector=self.streams[0].detector,
                                                        emt=None,
                                                        focus=self.focus,
                                                        rng_focus=focus_rng)

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


class Target:
    def __init__(self, x:float, y:float, z:float, name:str, type:str, index: int,  fm_focus_position: float, size: float = None ):
        """
        Target class to store the target information for multipoint correlation. The target can be a fiducial, point of
        interest, surface fiducial, projected fiducial or projected point of interest. The target information is used to
        calculate the transformation matrix between the FIB and FM images. The target information is provided by the user
        in the GUI. The target information is saved in the feature data structure.
        :param x: physical position in x in meters
        :param y: physical position in y in meters
        :param z: physical position in z in meters
        :param name: name of the target saved as <type>-<index>. For example, "FM-1", "POI-2", "FIB-3", "PP", "PPOI"
        :param type: type of the given target like Fiducial, PointOfInterest, ProjectedPoints, ProjectedPOI or SurfaceFiducial
        :param index: index of the target in the given type. For example, "FM-1", "FM-2", "FM-3"...
        :param fm_focus_position: position of the focus (objective in cased of Meteor) in meters
        """
        self.coordinates = model.ListVA((x, y, z), unit="m")
        self.type = model.StringEnumerated(type, choices={FIDUCIAL, POI, SURFACE_FIDUCIAL, PROJECTED_FIDUCIAL,
                                                          PROJECTED_POI})
        self.name = model.StringVA(name)
        # Warning: The index and target name are in sync. To increase the index limit more than 9, please first change the logic of finding indices from the
        # target name in add_new_target() in tab_gui_data.py and
        # _on_current_coordinates_changes(), _on_cell_changing in  multi_point_correlation.py
        self.index = model.IntContinuous(index, range=(1, 9))
        self.fm_focus_position = model.FloatVA(fm_focus_position, unit="m")

    def to_dict(self) -> dict:
        return {
            "coordinates": self.coordinates.value,
            "type": self.type.value,
            "name": self.name.value,
            "index": self.index.value,
            "fm_focus_position": self.fm_focus_position.value,
        }

    @staticmethod
    def from_dict(d: dict) -> "Target":
        x, y, z = d["coordinates"]
        return Target(
            x=x,
            y=y,
            z=z,
            name=d["name"],
            type=d["type"],
            index=d["index"],
            fm_focus_position=d["fm_focus_position"],
        )
