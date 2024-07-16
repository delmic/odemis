"""
1.Check milling with one preset above the specified lamella
2.Repeat the process to do milling below the lamella

Assumptions:
- lamella is at centre of the FOV

"""
import logging
import math
import os
import threading
import time
from concurrent import futures
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from typing import List

import numpy
import yaml

from odemis import model
from odemis.acq import move
from odemis.acq.acqmng import acquire
from odemis.acq.drift import AnchoredEstimator
from odemis.acq.feature import CryoFeature
from odemis.acq.move import MILLING, MicroscopePostureManager
from odemis.acq.milling.tasks import MillingTaskSettings, MillingTask
from odemis.acq.orsay_milling import mill_rectangle
from odemis.acq.stitching._tiledacq import MOVE_SPEED_DEFAULT
from odemis.acq.stream import UNDEFINED_ROI
from odemis.dataio import find_fittest_converter
from odemis.util import executeAsyncTask, dataio

ANCHOR_MAX_PIXELS = 512 ** 2  # max number of pixels for the anchor region


class MillingSettings(object):
    """
    Model class for milling settings

    """

    def __init__(self, name, current, horizontal_fov, roi,
                 pixel_size, duration, beam_angle=None,
                 dc_roi=UNDEFINED_ROI, dc_period=None, dc_dwell_time=None, dc_current=None):
        """
       Settings class for milling a rectangle.

       :param name: (str) name for the given milling setting
       :param current: (float) Probe current, as available in the presets, based on the preset naming. The ion beam current in A
       :param horizontal_fov:(float) width of the FoV when milling (to be set on the ion-beam settings), keep it fixed below 45um
       :param roi: (float, float, float, float) Region of interest, relative to the FoV (0→ 1)
       :param pixel_size:  float, float) Distance between the points, in m (Note: the orsay API expects a
        “probesize + overlap”, but we simplify it by just a pixelSize X/Y).
       :param beam_angle: (float) the angle between the stage and the beam column (for now this will be fixed, to ~10°), in rad
       :param duration: (float) total time to mill the region
       :param dc_roi: (float, float, float, float) Anchor region for the drift correction relative to the FoV (0→ 1).
        Use UNDEFINED_ROI if drift correction shouldn’t be applied
       :param dc_period: (float) time of milling before running the drift correction in s
       :param dc_dwell_time: (float) dwell time to be used during anchor region acquisition
       :param dc_current: (float) drift correction current
       :return:
       """
        self.horizontalFoV = model.FloatContinuous(horizontal_fov, unit="m", range=(5e-06, 45e-06))
        self.current = model.FloatContinuous(current, unit="A", range=(0.5e-12, 3000e-12))
        self.name = model.StringVA(name)
        self.roi = model.TupleContinuous(tuple(roi),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, float))
        self.pixelSize = model.TupleContinuous(tuple(pixel_size), unit="m",
                                               range=((0, 0), (1e-3, 1e-3)),  # max 1 mm: arbitrary gigantic value
                                               cls=(int, float))
        # angles ot of this range would be dangerous for the hardware and probably not useful for the user
        if beam_angle is None:
            stage = model.getComponent(role="stage")
            stage_md = stage.getMetadata()
            try:
                self._ion_beam_angle = stage_md[model.MD_ION_BEAM_TO_SAMPLE_ANGLE]
            except KeyError:
                raise ValueError("Ion beam angle not defined in stage metadata")
        else:
            self.beamAngle = model.FloatContinuous(beam_angle, unit="rad", range=(math.radians(6), math.radians(10)))
        self.duration = model.FloatContinuous(duration, unit="s", range=(0.1, 100e3))
        # drift correction settings
        if dc_roi == UNDEFINED_ROI:
            if dc_period is None:
                dc_period = duration
            if dc_dwell_time is None:
                dc_dwell_time = 1e-09
            if dc_current is None:
                dc_current = 1e-12
        else:
            if dc_period is None:
                raise ValueError("dc_period has to be provided.")
            if dc_dwell_time is None:
                raise ValueError("dc_dwell_time has to be provided.")
            if dc_current is None:
                raise ValueError("dc_current has to be provided.")
        self.dcRoi = model.TupleContinuous(tuple(dc_roi),
                                           range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                           cls=(int, float))
        self.dcPeriod = model.FloatContinuous(dc_period, unit="s", range=(0.1, 100e3))
        self.dcDwellTime = model.FloatContinuous(dc_dwell_time, unit="s", range=(1e-09, 1e-03))
        self.dcCurrent = model.FloatContinuous(dc_current, unit="A", range=(0.5e-12, 3000e-12))


def load_config(yaml_filename):
    """
    Load user input from yaml settings file.
    :param yaml_filename: (str) Filename path of user configuration file.
    :return: (list) List containing MillingSettings.
    """
    try:
        with open(yaml_filename, "r") as f:
            settings = yaml.safe_load(f)

    except yaml.YAMLError as exc:
        logging.error("Syntax error in milling settings yaml file: %s", exc)

    millings = []
    for ms in settings:
        milling_setting = MillingSettings(**ms)
        millings.append(milling_setting)

    return millings


# To handle the timeout error when the stage is not able to move to the desired position
# It logs the message and raises the MoveError exception
class MoveError(Exception):
    pass


class MillingRectangleTask(object):
    """
    This class represents a milling Task for milling rectangular regions on the sample.
    """

    def __init__(self, future: futures.Future, millings: list, sites: list, feature_post_status: str, acq_streams,
                 ebeam, sed, stage, aligner, log_path=None):
        """
        Constructor
        :param future: (ProgressiveFuture) the future that will be executing the task
        :param millings: (list of MillingSettings) Settings corresponding to each milling, to be milled in order
        :param sites: (list of Features) Each Feature to be milled
        :param feature_post_status: (str) value to set on the Feature at the end of a complete milling series
        :param acq_streams: type of acquisition streams to be used for milling
        :param ebeam: model component for the scanner
        :param sed: model component for the detector
        :param stage: model component for the stage
        :param aligner: model component for the aligner
        :param log_path: (str) path to the log anchor region acquisition used in drift correction
        """
        self._stage = stage
        self._scanner = ebeam
        self._detector = sed
        self._aligner = aligner

        self._future = future
        if future is not None:
            self._future.running_subf = model.InstantaneousFuture()
            self._future._task_lock = threading.Lock()

        self._settings = millings
        self._feature_status = feature_post_status  # site and feature means the same
        self.sites = sites
        self.streams = acq_streams

        self._log_path = log_path
        if log_path:
            filename = os.path.basename(self._log_path)
            if not filename:
                raise ValueError("Filename is not found on log path.")
            self._exporter = find_fittest_converter(filename)
            self._fn_bs, self._fn_ext = dataio.splitext(filename)
            self._log_dir = os.path.dirname(self._log_path)

        # internal class requirements between functions
        self._pass_duration = []  # one value per milling setting, duration(s) after which drift acquisition starts
        self._iterations = []  # one value per milling setting, numbers of scans of .roi
        stage_md = stage.getMetadata()
        try:
            self._ion_beam_angle = stage_md[model.MD_ION_BEAM_TO_SAMPLE_ANGLE]
        except KeyError:
            raise ValueError("Ion beam angle not defined in stage metadata")

        # Rough estimate of the stage movement speed, for estimating the extra
        # duration due to movements (copied from _tiledacq.py)
        self._move_speed = MOVE_SPEED_DEFAULT
        if model.hasVA(stage, "speed"):
            try:
                self._move_speed = (stage.speed.value["x"] + stage.speed.value["y"]) / 2
            except Exception as ex:
                logging.warning("Failed to read the stage speed: %s", ex)

        # estimate milling time from the list of milling settings-> millings
        time_estimate = 0
        drift_acq_time = 0

        for setting_nb, setting in enumerate(self._settings):
            time_estimate += setting.duration.value
            if setting.dcRoi.value != UNDEFINED_ROI:
                self._iterations.append(int(numpy.ceil(setting.duration.value / setting.dcPeriod.value)))
                self._pass_duration.append(setting.duration.value / self._iterations[setting_nb])

                drift_acq_time += self.estimate_drift_time(setting)
            else:
                self._iterations.append(1)
                self._pass_duration.append(setting.duration.value)

        self._milling_time_estimate = time_estimate + drift_acq_time  # time_estimate per feature

    def cancel(self, future: "Future") -> bool:
        """
        Canceler of acquisition task.
        :param future: the future that will be executing the task
        :return: True if it successfully cancelled (stopped) the future
        """
        logging.debug("Canceling milling procedure...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Milling procedure cancelled.")
        return True

    def estimate_milling_time(self, sites_done: int = 0, actual_time_per_site: float = None) -> float:
        """
        Estimates the milling time for the given feature.
        :param sites_done: number of sites already milled
        :param actual_time_per_site: actual milling time measured for a single site
        :return: (float > 0): the estimated time is in seconds
        """
        remaining_sites = (len(self.sites) - sites_done)
        if actual_time_per_site:
            milling_time = actual_time_per_site * remaining_sites
        else:
            milling_time = self._milling_time_estimate * remaining_sites

        # Time it takes to go from one site to the other
        prev_stage_pos_ref = None  # previous stage position used for reference in estimating time
        stage_time = 0
        for site in self.sites:
            stage_time += self.estimate_stage_movement_time(site, prev_stage_pos_ref)
            prev_stage_pos_ref = site.pos.value

        return milling_time + stage_time

    def estimate_stage_movement_time(self, site: CryoFeature, stage_pos_ref: tuple = None) -> float:
        """
        Estimation the time taken by the stage to move from current position to the location of given site
        :param site: (CryoFeature) consists X,Y,Z coordinates for stage location in m.
        :param stage_pos_ref: (tuple) reference position of the stage in m from where the stage moves.
        :return: (float) time to move the stage between two points in s.
        """
        # current position from x,y,z of stage position and eliminating rx,ry,rz
        if stage_pos_ref is None:
            stage_pos = self._stage.position.value
            current_pos = [stage_pos[an] for an in ("x", "y", "z")]
        else:
            current_pos = stage_pos_ref
        target_pos = site.pos.value  # list
        diff = [abs(target - current) for target, current in zip(target_pos, current_pos)]
        stage_time = math.sqrt(sum(d ** 2 for d in diff)) / self._move_speed

        return stage_time

    def estimate_drift_time(self, setting: MillingSettings) -> float:
        """
        Estimate the time taken to acquire drift correction images
        :param setting: (MillingSettings) milling settings
        return (float): estimated time to acquire 1 anchor area
        """
        if setting.dcRoi.value == UNDEFINED_ROI:
            return 0

        nb_anchor_scanning = math.ceil(setting.duration.value / setting.dcPeriod.value)
        drift_estimation = AnchoredEstimator(self._scanner, self._detector,
                                             setting.dcRoi.value, setting.dcDwellTime.value, ANCHOR_MAX_PIXELS,
                                             follow_drift=False)
        return drift_estimation.estimateAcquisitionTime() * nb_anchor_scanning

    def _move_to_site(self, site: CryoFeature):
        """
        Move the stage to the given site.
        :param site: (CryoFeature) The site to move to.
        :raises MoveError: if the stage failed to move to the given site.
        """
        target_pos = {"x": site.pos.value[0],
                      "y": site.pos.value[1],
                      "z": site.pos.value[2]}
        logging.debug("For feature %s moving the stage to %s m", site.name.value, target_pos)
        self._future.running_subf = self._stage.moveAbs(target_pos)
        stage_time = self.estimate_stage_movement_time(site)
        t = stage_time * 10 + 5  # adding extra margin
        try:
            self._future.running_subf.result(t)
        except TimeoutError:
            self._future.running_subf.cancel()
            raise MoveError(f"Failed to move the stage for feature {site.name.value} within {t} s")

        # The stage never *exactly* reaches the target position. => Store how
        # far it was away, according to the stage encoders. We could try to
        # compensate using beam shift. However, on the MIMAS stage, there is
        # currently too much imprecision that the encoders do not detect, so we
        # just log the information.
        actual_pos = self._stage.position.value
        diff_pos = [target_pos[a] - actual_pos[a] for a in ("x", "y", "z")]
        logging.debug("Stage reached %s, away from target by %s m", actual_pos, diff_pos)

        # Reset the beam shift to 0,0 in order to start from scratch with drift
        # compensation, to reduce the chances of reaching the limits of the shift.
        self._scanner.shift.value = 0, 0

    def _mill_all_settings(self, site: CryoFeature):
        """
        Iterates over all the milling settings for one site
        :param site: The site to mill.
        :raises MoveError: if the stage failed to move to the given site, or the beam shift failed to compensate drift.
        """
        self._move_to_site(site)

        for setting_nb, milling_settings in enumerate(self._settings):
            # Tilt stage angle
            stage_rx = self._ion_beam_angle - milling_settings.beamAngle.value
            logging.debug("Tilting stage to %f °", math.degrees(stage_rx))
            self._future.running_subf = self._stage.moveAbs({"rx": stage_rx})

            try:
                t = 15  # s
                self._future.running_subf.result(timeout=t)
            except TimeoutError:
                logging.debug("Failed to tilt the stage for feature %s within %s s", site.name.value, t)
                raise

            self._scanner.horizontalFoV.value = milling_settings.horizontalFoV.value

            # Make the blanker automatic (ie, disabled when acquiring)
            self._scanner.blanker.value = None

            # Initialize the drift corrector only if it is requested by the user
            if milling_settings.dcRoi.value != UNDEFINED_ROI:
                # Change the current to the drift correction current
                self._scanner.probeCurrent.value = milling_settings.dcCurrent.value
                drift_est = AnchoredEstimator(self._scanner, self._detector,
                                              milling_settings.dcRoi.value,
                                              milling_settings.dcDwellTime.value,
                                              max_pixels=ANCHOR_MAX_PIXELS, follow_drift=False)
                # acquire an image at the given location (RoI)
                drift_est.acquire()
                da = drift_est.raw[-1]
                pxs_anchor = da.metadata[model.MD_PIXEL_SIZE]
                if self._log_path:
                    fn = f"{self._fn_bs}-dc-{site.name.value}-{milling_settings.name.value}-0{self._fn_ext}"
                    self._exporter.export(os.path.join(self._log_dir, fn), [da])

            # Compute the probe size and overlap, based on the requested pixel size
            # For the probe size, use the pixel size in the largest dimension (typically X)
            # To adjust the pixel size in Y, compute the overlap so that probe_size * (1-overlap)) == pixelSize.
            # X is horizontal axis, Y is vertical axis
            pxs = milling_settings.pixelSize.value
            probe_size = max(pxs)
            overlap = [1 - (pxs[0] / probe_size), 1 - (pxs[1] / probe_size)]  # Always >= 0

            logging.debug(f"Milling setting: {milling_settings.name.value} for feature: {site.name.value}")

            self._scanner.probeCurrent.value = milling_settings.current.value

            if milling_settings.dcRoi.value == UNDEFINED_ROI:

                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                    self._future.running_subf = mill_rectangle(milling_settings.roi.value, self._scanner,
                                                               iteration=self._iterations[setting_nb],
                                                               duration=self._pass_duration[setting_nb],
                                                               probe_size=probe_size, overlap=overlap)
                try:
                    self._future.running_subf.result()
                    logging.debug(f"Milling {milling_settings.name.value} for feature {site.name.value} finished")
                except Exception as exp:
                    logging.error(
                        f"Milling setting {milling_settings.name.value} for feature {site.name.value} failed: {exp}")
                    raise

            else:
                for itr in range(self._iterations[setting_nb]):
                    with self._future._task_lock:
                        if self._future._task_state == CANCELLED:
                            raise CancelledError()

                        self._scanner.probeCurrent.value = milling_settings.current.value
                        self._future.running_subf = mill_rectangle(milling_settings.roi.value, self._scanner,
                                                                   iteration=1,
                                                                   duration=self._pass_duration[setting_nb],
                                                                   probe_size=probe_size, overlap=overlap)
                    try:
                        self._future.running_subf.result()
                        logging.debug(f"Milling {milling_settings.name.value} for feature {site.name.value} iteration {itr} finished")
                    except Exception as exp:
                        logging.error(
                            f"Milling {milling_settings.name.value} for feature {site.name.value} at iteration {itr} failed: {exp}")
                        raise

                    # Change the current to the drift correction current
                    self._scanner.probeCurrent.value = milling_settings.dcCurrent.value
                    # Acquire an image at the given location (RoI)
                    drift_est.acquire()

                    # Estimate the drift since the last correction
                    drift_est.estimate()
                    drift = (pxs_anchor[0] * drift_est.drift[0],
                             -(pxs_anchor[1] * drift_est.drift[1]))

                    # Move FIB to compensate drift
                    previous_shift = self._scanner.shift.value
                    shift = (drift[0] + previous_shift[0], drift[1] + previous_shift[1])  # m
                    self._scanner.shift.value = self._scanner.shift.clip(shift)
                    if self._scanner.shift.value != shift:  # check if it has been clipped
                        raise MoveError(f"Failed to set the beam shift to {shift} m, limited to {self._scanner.shift.value}")
                    logging.debug("Ion-beam shift in m: %s changed to: %s", previous_shift, self._scanner.shift.value)
                    if self._log_path:
                        fn = f"{self._fn_bs}-dc-{site.name.value}-{milling_settings.name.value}-{itr + 1}{self._fn_ext}"
                        self._exporter.export(os.path.join(self._log_dir, fn), drift_est.raw[-1])

            self._scanner.blanker.value = True
            with self._future._task_lock:
                if self._future._task_state == CANCELLED:
                    raise CancelledError()

    def _acquire_feature(self, site: CryoFeature):
        """
        Acquire the data for the given feature and add to the given site.
        :param site: (CryoFeature) The feature to acquire.
        """
        data = []
        try:
            # check if cancellation happened while the acquiring future is working
            with self._future._task_lock:
                if self._future._task_state == CANCELLED:
                    raise CancelledError()
                self._future.running_subf = acquire(self.streams)
            data, exp = self._future.running_subf.result()
            if exp:
                logging.error(
                    f"Acquisition for feature {site.name.value} partially failed: {exp}")
        except Exception as exp:
            logging.error(f"Acquisition for feature {site.name.value} failed: {exp}")

        # Check on the acquired data
        if not data:
            logging.warning("The acquired data array in stream %s for feature %s is empty", self.streams,
                            site.name)
        else:
            # Convert the data to StaticStreams, and add them to the feature
            new_streams = dataio.data_to_static_streams(data)
            site.streams.value.extend(new_streams)
            logging.info("The acquisition for stream %s for feature %s is done", self.streams, site.name)

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously
        """
        self._future._task_state = RUNNING

        try:
            actual_time_per_site = None
            self._scanner.shift.value = (0, 0)  # reset drift correction to have more margin

            microscope = model.getMicroscope()
            posture_manager = MicroscopePostureManager(microscope)
            for site_idx, site in enumerate(self.sites):
                # Update progress on the milling sites left
                start_time = time.time()
                remaining_t = self.estimate_milling_time(site_idx, actual_time_per_site)
                self._future.set_end_time(time.time() + remaining_t)

                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()
                    logging.debug(f"Retracting the objective to set the imaging in FIB mode")
                    self._future.running_subf = posture_manager.cryoSwitchSamplePosition(MILLING)

                self._future.running_subf.result()

                # For one given site, move the stage at the site and
                # do milling with all milling settings for the given site
                # If timeout exception is raised during stage movement, continue milling the next site
                try:
                    self._mill_all_settings(site)
                except MoveError as ex:
                    logging.warning("Feature %s: %s. Skipping to next feature.", site.name.value, ex)
                    continue

                # Update the status of milling for the given site
                site.status.value = self._feature_status
                logging.debug(f"The milling of feature {site.name.value} completed")

                # Acquire the streams of the milled site
                self._acquire_feature(site)

                # Store the actual time during milling one site computed after moving the stage
                actual_time_per_site = time.time() - start_time

        except CancelledError:
            logging.debug("Stopping because milling was cancelled")
            raise
        except Exception:
            logging.exception("The milling failed")
            raise
        finally:
            # activate the blanker
            self._scanner.blanker.value = True
            self._future._task_state = FINISHED


def mill_features(millings: list, sites: list, feature_post_status, acq_streams, ebeam, sed, stage,
                  aligner, log_path=None) -> futures.Future:
    """
    Mill features on the sample.
    :param millings: (list of MillingSettings) Settings corresponding to each milling, to be milled in order
    :param sites: (list of Features) Each Feature to be milled
    :param feature_post_status: (str) value to set on the Feature at the end of a complete milling series
    :param acq_streams: type of acquisition streams to be used for milling
    :param ebeam: model component for the scanner
    :param sed: model component for the detector
    :param stage: model component for the stage
    :param aligner: model component for the objective
    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create acquisition task
    milling_task = MillingRectangleTask(future, millings, sites, feature_post_status, acq_streams, ebeam, sed, stage,
                                        aligner, log_path)
    # add the ability of cancelling the future during execution
    future.task_canceller = milling_task.cancel

    # set the progress of the future
    total_duration = milling_task.estimate_milling_time()
    future.set_end_time(time.time() + total_duration)

    # assign the acquisition task to the future
    executeAsyncTask(future, milling_task.run)

    return future


def estimate_milling_time(*args, **kwargs) -> float:
    """
    Estimate the duration of milling.
    :params: arguments are the same as mill_features() arguments.
    :return: (float > 0): estimated milling time in seconds
    """
    milling_task = MillingRectangleTask(None, *args, **kwargs)
    return milling_task.estimate_milling_time()



def mill_patterns(settings: MillingTaskSettings) -> futures.Future:
    """
    Run Mill patterns.
    :param settings: (MillingTaskSettings) Settings for the milling task
    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create acquisition task
    milling_task = MillingTask(future, settings)
    # add the ability of cancelling the future during execution
    future.task_canceller = milling_task.cancel

    # set the progress of the future (TODO: fix dummy time estimate)
    future.set_end_time(time.time() + 10 * len(settings.patterns))

    # assign the acquisition task to the future
    executeAsyncTask(future, milling_task.run)

    return future

from odemis.acq.align.shift import MeasureShift
from pprint import pprint
import matplotlib.pyplot as plt
from concurrent import futures

from odemis.acq.milling.feature import CryoLamellaFeature
from odemis.dataio import find_fittest_converter

class AutomatedMillingManager(object):


    def __init__(self, future, project, stage, sem_stream, fib_stream, fm_stream, task_list):
        
        self.stage = stage
        self.sem_stream = sem_stream
        self.fib_stream = fib_stream
        self.fm_stream = fm_stream
        self.ion_beam = fib_stream.emitter
        self.focus = fm_stream.focuser
        self.project = project
        self.task_list = task_list
        self._exporter = find_fittest_converter("filename.ome.tiff")

        self._future = future
        if future is not None:
            self._future.running_subf = model.InstantaneousFuture()
            self._future._task_lock = threading.Lock()



    def cancel(self, future: 'Future') -> bool:
        """
        Canceler of milling task.
        :param future: the future that will be executing the task
        :return: True if it successfully cancelled (stopped) the future
        """
        logging.debug("Canceling milling procedure...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Milling procedure cancelled.")
        return True

    def run(self):

        self.posture_manager = MicroscopePostureManager(model.getMicroscope())
        workflows = ["MILLING", "IMAGING"] # TODO: implement imaging workflow separately (fm acquisition)

        FLM_ACQUISITION = False

        for task_num, task_name in enumerate(self.task_list, 1):
            print(f"Starting {task_name} for {len(self.project.features)} features...")
            task_num = f"{task_num:02d}"
            feature: CryoLamellaFeature
            for name, feature in self.project.features.items():
                
                feature_name = feature.name.value
                print(f"Feature: {feature.name.value}")
                print(f"Position: {feature.position.value}")
                print(f"Previous Status: {feature.status.value}")
                print("--" * 10)
                print(f"Starting {task_name} for {feature.name.value}")
                
                # move to position
                self._future.running_subf = self.stage.moveAbs(feature.position.value) # TODO: make this move safer using posture manager
                self._future.running_subf.result()

                self._future.msg = f"Moved to {feature.name.value}"
                self._future.set_progress()

                # reset beam shift
                self.ion_beam.shift.value = (0, 0)

                # match image settings for alignment
                ref_image = feature.reference_image # load from directory?
                pixel_size = ref_image.metadata[model.MD_PIXEL_SIZE]
                fov = pixel_size[0] * ref_image.shape[1]
                self.ion_beam.horizontalFoV.value = fov

                # beam shift alignment
                self._future.running_subf = acquire([self.fib_stream])
                data, _ = self._future.running_subf.result()
                new_image = data[0]

                # roll data by a random amount
                import random
                # x, y = random.randint(0, 100), random.randint(0, 100)
                # new_image = numpy.roll(new_image, [x, y], axis=[0, 1])
                # print(f"Shifted image by {x}, {y} pixels")

                align_filename = os.path.join(feature.path, f"{feature_name}-{task_num}-{task_name}-Alignment-FIB.ome.tiff").replace(" ", "-") # TODO: make unique
                self._exporter.export(align_filename, new_image)       

                def align_reference_image(ref_image, new_image, scanner):
                    shift_px = MeasureShift(ref_image, new_image, 10)
                    # shift_px = (1, 1)
                    pixelsize = ref_image.metadata[model.MD_PIXEL_SIZE]
                    shift_m = (shift_px[0] * pixelsize[0], shift_px[1] * pixelsize[1])

                    previous_shift = scanner.shift.value
                    print(f"Previous: {previous_shift}, Shift: {shift_m}")
                    shift = (shift_m[0] + previous_shift[0], shift_m[1] + previous_shift[1])  # m
                    scanner.shift.value = shift
                    print(f"Shift: {scanner.shift.value}")

                align_reference_image(ref_image, new_image, scanner=self.ion_beam)

                # mill patterns
                task = feature.milling_tasks[task_name]
                self._future.msg = f"Start Milling Task: {task_name} ({len(task.patterns)} Patterns) for {feature.name.value}"
                self._future.set_progress()

                from odemis.acq.milling.tasks import draw_milling_tasks
                fig = draw_milling_tasks(new_image, {task_name: task})
                plt.show()

                print(f"Starting Milling Task: {task}")
                self._future.running_subf = mill_patterns(task)
                self._future.running_subf.result()

                # acquire images
                self._future.running_subf = acquire([self.sem_stream, self.fib_stream])
                data, ex = self._future.running_subf.result()
                sem_image, fib_image = data

                # save images
                sem_filename = os.path.join(feature.path, f"{feature_name}-{task_num}-{task_name}-Finished-SEM.ome.tiff").replace(" ", "-") # TODO: make unique 
                fib_filename = os.path.join(feature.path, f"{feature_name}-{task_num}-{task_name}-Finished-FIB.ome.tiff").replace(" ", "-") # TODO: make unique
                self._exporter.export(sem_filename, sem_image)
                self._exporter.export(fib_filename, fib_image)       

                if FLM_ACQUISITION:
                    # move to flm position
                    # TODO: use pm to move to flm position
                    # TODO: move the fm acquisitions to the end of each task.
                    print(f"Moving to FLM position for {feature.name.value}")
                    # set objective position
                    self._future.running_subf = self.focus.moveAbs({"z": feature.focus_position.value})
                    self._future.running_subf.result()

                    # TODO: get fm acquisition settings from where?

                    # acquire fm z-stack
                    self._future.running_subf = acquire([self.fm_stream])
                    data, ex = self._future.running_subf.result()
                    fm_image = data
                    # save flm image
                    fm_filename = os.path.join(feature.path, f"{feature_name}-{task_num}-{task_name}-Finished-FLM.ome.tiff").replace(" ", "-")
                    self._exporter.export(fm_filename, fm_image)

                    # plot images
                    fig, ax = plt.subplots(1, 3, figsize=(10, 5))
                    ax[0].imshow(sem_image, cmap="gray")
                    ax[1].imshow(fib_image, cmap="gray")
                    ax[2].imshow(fm_image[0], cmap="gray")
                    plt.show()
                
                else:
                    # plot images
                    fig, ax = plt.subplots(1, 2, figsize=(10, 5))
                    ax[0].imshow(sem_image, cmap="gray")
                    ax[1].imshow(fib_image, cmap="gray")
                    plt.show()

                # update status
                feature.status.value = task_name

                print(f"Finished {task_name} for {feature.name.value}")

                # save project
                self.project.save()


def run_automated_milling(stage, sem_stream, fib_stream, fm_stream, task_list, project) -> futures.Future:
    """
    Automatically mill and image a list of features.

    :return: ProgressiveFuture
    """
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    # create automated milling task
    amm = AutomatedMillingManager(
        future=future,
        stage=stage,
        sem_stream=sem_stream,
        fib_stream=fib_stream,
        fm_stream=fm_stream,
        task_list=task_list,
        project=project,
    )
    # add the ability of cancelling the future during execution
    future.task_canceller = amm.cancel

    # set the progress of the future
    total_duration = 100000 # TODO: estimated duration
    import time
    future.set_end_time(time.time() + total_duration)

    # assign the acquisition task to the future
    executeAsyncTask(future, amm.run)

    return future