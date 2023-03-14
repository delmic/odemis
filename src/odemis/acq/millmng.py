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

import numpy
import yaml

from odemis import model
from odemis.acq import move
from odemis.acq.acqmng import acquire
from odemis.acq.drift import AnchoredEstimator
from odemis.acq.feature import CryoFeature
from odemis.acq.move import MILLING
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
                 pixel_size, beam_angle, duration,
                 dc_roi, dc_period, dc_dwell_time, dc_current):
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
        self.roi = model.TupleContinuous(roi,
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, float))
        self.pixelSize = model.VigilantAttribute(pixel_size, unit="m")
        # angles ot of this range would be dangerous for the hardware and probably not useful for the user
        self.beamAngle = model.FloatContinuous(beam_angle, unit="rad", range=(math.radians(6), math.radians(10)))
        self.duration = model.FloatVA(duration, unit="s")
        # drift correction settings
        self.dcRoi = model.TupleContinuous(dc_roi,
                                           range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                           cls=(int, float))
        self.dcPeriod = model.FloatContinuous(dc_period, unit="s", range=(1, 180))
        self.dcDwellTime = model.FloatContinuous(dc_dwell_time, unit="s", range=(1e-09, 1e-03))
        self.dcCurrent = model.FloatContinuous(dc_current, unit="A", range=(0.5e-12, 3000e-12))


def load_config(yaml_filename):
    """
    Load user input from yaml settings file.
    :param yaml_filename: (str) Filename path of user configuration file.
    :return: (dict) Dictionary containing user input settings.
    """
    try:
        with open(yaml_filename, "r") as f:
            settings_dict = yaml.safe_load(f)

    except yaml.YAMLError as exc:
        logging.error("Syntax error in milling settings yaml file: %s", exc)

    return settings_dict


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
        stage_time = 0

        for setting_nb, setting in enumerate(self._settings):
            time_estimate += setting.duration.value
            if setting.dcRoi.value != UNDEFINED_ROI:
                self._iterations.append(int(numpy.ceil(setting.duration.value / setting.dcPeriod.value)))
                self._pass_duration.append(setting.duration.value / self._iterations[setting_nb])

                drift_acq_time += self.estimate_drift_time(setting)
            else:
                self._iterations.append(1)
                self._pass_duration.append(setting.duration.value)

        prev_stage_pos_ref = None  # previous stage position used for reference in estimating time

        for site in self.sites:
            stage_time += self.estimate_stage_movement_time(site, prev_stage_pos_ref)
            prev_stage_pos_ref = site.pos.value

        self._milling_time_estimate = time_estimate + drift_acq_time  # time_estimate per feature
        self._remaining_stage_time = stage_time  # total stage movement time

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

        self._remaining_stage_time -= self.estimate_stage_movement_time(self.sites[sites_done])
        total_duration = milling_time + self._remaining_stage_time

        return total_duration

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

    def _mill_all_settings(self, site: CryoFeature):
        """
        Iterates over all the milling settings for one site
        :param site: The site to mill.
        :raises MoveError: if the stage failed to move to the given site.
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
                    logging.debug(f"Milling finished for feature: {site.name.value}")
                except Exception as exp:
                    logging.debug(
                        f"Milling setting: {milling_settings.name.value} for feature: {site.name.value} failed: {exp}")
                    raise

            else:
                for itr in range(self._iterations[setting_nb]):
                    with self._future._task_lock:
                        if self._future._task_state == CANCELLED:
                            raise CancelledError()

                        self._scanner.probeCurrent.value = milling_settings.current.value
                        self._future.running_subf = mill_rectangle(milling_settings.roi.value, self._scanner,
                                                                   iteration=self._iterations[setting_nb],
                                                                   duration=self._pass_duration[setting_nb],
                                                                   probe_size=probe_size, overlap=overlap)
                    try:
                        self._future.running_subf.result()
                        logging.debug(f"Milling finished for feature: {site.name.value}")
                    except Exception as exp:
                        logging.debug(
                            f"Milling setting: {milling_settings.name.value} for feature: {site.name.value} at iteration: {itr} failed: {exp}")
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
                    self._scanner.shift.value = (drift[0] + previous_shift[0],
                                                 drift[1] + previous_shift[1])  # shift in m - absolute position
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
                logging.warning(
                    f"Acquisition for feature {site.name.value} partially failed: {exp}")
        except Exception as exp:
            logging.debug(f"Acquisition for feature {site.name.value} failed: {exp}")

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

            for site_idx, site in enumerate(self.sites):
                # Update progress on the milling sites left
                start_time = time.time()
                remaining_t = self.estimate_milling_time(site_idx, actual_time_per_site)
                self._future.set_end_time(time.time() + remaining_t)

                with self._future._task_lock:
                    if self._future._task_state == CANCELLED:
                        raise CancelledError()
                    logging.debug(f"Retracting the objective to set the imaging in FIB mode")
                    self._future.running_subf = move.cryoSwitchSamplePosition(MILLING)

                self._future.running_subf.result()

                # For one given site, move the stage at the site and
                # do milling with all milling settings for the given site
                # If timeout exception is raised during stage movement, continue milling the next site
                try:
                    self._mill_all_settings(site)
                except MoveError:
                    logging.debug("Failed to move the stage for feature %s, skipping to next feature ", site.name.value)
                    continue

                # Update the status of milling for the given site
                site.status = self._feature_status
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
