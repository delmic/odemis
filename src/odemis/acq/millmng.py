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
from datetime import datetime
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError

import numpy
import yaml
from numpy import long

from odemis.acq import stream

from odemis import model
from odemis.acq.acqmng import acquire
from odemis.acq.drift import AnchoredEstimator
from odemis.acq.feature import FEATURE_ACTIVE, FEATURE_DEACTIVE, CryoFeature, FEATURE_ROUGH_MILLED
from odemis.acq.orsay_milling import mill_rectangle
from odemis.acq.stitching._tiledacq import MOVE_SPEED_DEFAULT
from odemis.acq.stream import UNDEFINED_ROI
from odemis.util import executeAsyncTask, dataio

# Rough milling (RM)
config_filename_rect1 = '/src/odemis/acq/milling_760pA.yaml'


# def main():
#     millings = load_config(config_filename_rect1)
#
#     positions = [{'x': 0, 'y': 0, 'z': 0}, {'x': 0, 'y': 0, 'z': 0}]
#
#     sites = []
#     for i in range(0, len(positions)):
#         feature = CryoFeature('object_' + str(i), positions[i]['x'], positions[i]['y'], positions[i]['z'])
#         sites.append(feature)
#
#     feature_post_status = FEATURE_ROUGH_MILLED
#
#     ebeam = model.getComponent(role="e-beam")
#     sed = model.getComponent(role="se-detector")
#     stage = model.getComponent(role="stage")
#     at the end FM is typical instead of SEM
#     stem = stream.SEMStream("Secondary electrons", sed, sed.data, ebeam)
#     acq_streams = [stem]
#
#     f = mill_features(millings, sites, feature_post_status, acq_streams, ebeam, sed, stage)
#     # gui - add_done_Callback()
#     f.result()


class MillingSettings(object):
    """
    Model class for milling settings

    """

    def __init__(self, name, current, horizontal_fov, roi,
                 pixel_size, beam_angle, duration,
                 dc_roi, dc_period, dc_dwell_time, dc_current):
        """
       Settings class for milling a rectangle.
       
       :param name: (str) name for the given milling setting.
       :param current: allow currents from the predefined
       :param horizontal_fov:  do not ask user, keep it fixed below 45um
       :param roi: 
       :param pixel_size:  do not ask user, it needs to set by us
       :param beam_angle: 
       :param duration: 
       :param dc_roi: 
       :param dc_period: 
       :param dc_dwell_time:  do not ask user, depends on dc_current
       :param dc_current:  do not ask user, keep it same as milling current
       :return: 
       """
        current_choices = {20e-12, 100e-12, 350e-12, 760e-12}
        dwell_time_choices = {1e-6, 10e-6} # for drift correction
        self.name = model.StringVA(name)
        self.current = model.FloatEnumerated(current, unit="A",
                                             choices=current_choices)
        self.horizontalFoV = model.FloatContinuous(horizontal_fov, unit="m", range=(5e-06, 45e-06))
        self.roi = model.TupleContinuous(roi,
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float))
        self.pixelSize = model.VigilantAttribute(pixel_size, unit="m")
        self.beamAngle = model.FloatContinuous(beam_angle, unit="rad", range=(math.radians(6), math.radians(10)))  # in degrees
        self.duration = model.FloatVA(duration, unit="s")
        # drift correction settings
        self.dcRoi = model.TupleContinuous(dc_roi,
                                           range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                           cls=(int, long, float))
        self.dcPeriod = model.FloatContinuous(dc_period, unit="s", range=(1, 180))
        self.dcDwellTime = model.FloatEnumerated(dc_dwell_time, unit="s", choices=dwell_time_choices)
        self.dcCurrent = model.FloatEnumerated(dc_current, unit="A",
                                               choices=current_choices)


# def load_config(yaml_filename):
#     """Load user input from yaml settings file.
#     Parameters
#     ----------
#     yaml_filename : str
#         Filename path of user configuration file.
#     Returns
#     -------
#     dict
#         Dictionary containing user input settings.
#     """
#     with open(yaml_filename, "r") as f:
#         settings_dict = yaml.safe_load(f)
#
#     # settings = Settings(**settings_dict)  # convert to python dataclass
#     return settings_dict


def mill_features(millings, sites, feature_post_status, acq_streams, ebeam, sed, stage, aligner):
    # Create a progressive future with running sub future
    future = model.ProgressiveFuture()
    future.running_subf = model.InstantaneousFuture()
    future._task_lock = threading.Lock()
    # create acquisition task
    milling_task = MillingRectangleTask(future, millings, sites, feature_post_status, acq_streams, ebeam, sed, stage,
                                        aligner)
    # add the ability of cancelling the future during execution
    future.task_canceller = milling_task.cancel

    # set the progress of the future
    total_duration = milling_task.estimate_milling_time()
    future.set_end_time(time.time() + total_duration)

    # assign the acquisition task to the future
    executeAsyncTask(future, milling_task.run)

    return future


class MillingRectangleTask(object):
    """
    Copied from class ZStackAcquisitionTask(object):
    """

    def __init__(self, future, millings, sites, feature_post_status, acq_streams, ebeam, sed, stage, aligner):
        """

        :param future:
        :param millings:
        :param sites:
        :param feature_post_status:
        :param acq_streams:
        :param ebeam:
        :param sed:
        :param stage:
        """
        # site and feature means the same
        self._stage = stage
        self._scanner = ebeam
        self._detector = sed
        self._aligner = aligner

        self._future = future
        self._settings = millings
        self._feature_status = feature_post_status
        self.sites = sites
        self.streams = acq_streams

        # internal class requirements between functions
        self._pass_duration = []  # one value per milling setting, duration(s) after which drift acquisition starts
        self._iterations = []  # one value per milling setting, numbers of scans of .roi
        stage_md = stage.getMetadata()
        #todo ion beam angle not defined

        # self._ion_beam_angle = stage_md[model.MD_ION_BEAM_TO_SAMPLE_ANGLE]
        self._ion_beam_angle = math.radians(6)
        self._move_speed = MOVE_SPEED_DEFAULT  # stage speed used from -tiledacq.py

        # estimate milling time from the list of milling settings-> millings
        time_estimate = 0
        drift_acq_time = 0
        stage_time = 0
        for setting_nb, setting in enumerate(self._settings):
        # for setting_nb in range(len(self._settings)): # enumerate does not take a single-element list
        #     setting = self._settings[setting_nb]
            time_estimate += setting.duration.value
            # if self._settings[i]["dcROI"] != UNDEFINED_ROI:
            if setting.dcRoi.value != UNDEFINED_ROI:
                self._iterations.append(int(numpy.ceil(setting.duration.value / setting.dcPeriod.value)))
                self._pass_duration.append(setting.duration.value / self._iterations[setting_nb])

                drift_acq_time += self.estimate_drift_time(setting)
            else:
                self._iterations.append(int(1))
                self._pass_duration.append(setting.duration.value) 

        for site in self.sites:
            stage_time += self.estimate_stage_movement_time(site)

        self._milling_time_estimate = time_estimate + drift_acq_time  # time_estimate per feature
        self._remaining_stage_time = stage_time  # total stage movement time

        # set the FIB mode with the retracted state of the objective
        # get the metadata to retract the objective
        align_md = self._aligner.getMetadata()
        deactive_pos = align_md[model.MD_FAV_POS_DEACTIVE]

        # create a future and update the appropriate controls after it is called
        logging.debug(f"Retracting the objective to set the imaging in FIB mode")
        self._future.running_subf = self._aligner.moveAbs(deactive_pos)

        try:
            self._future.running_subf.result()
        except Exception as exp:
            logging.exception(
                f"Unable to retract the objective due to exception: {exp}")
            self._future.running_subf.cancel()
            logging.debug("Cancelling the milling")
            self._future._task_state = CANCELLED
            raise CancelledError()

    def cancel(self, future):
        """
        Canceler of acquisition task.
        """
        logging.debug("Canceling milling procedure...")

        with future._task_lock:
            if future._task_state == FINISHED:
                return False
            future._task_state = CANCELLED
            future.running_subf.cancel()
            logging.debug("Milling procedure cancelled.") #add line number
        return True

    def estimate_milling_time(self, site_idx=0, actual_time_per_site=None):
        """
        :param site_idx:
        :param actual_time_per_site:
        :return:
        """
        """
        Estimates the milling time for the given feature.
        :return (Float > 0): the estimated time
        """
        if len(self.sites) == 0:
            logging.debug("Site location is not provided by the user, cancelling the milling")
            self._future._task_state = CANCELLED
            raise CancelledError()

        remaining_sites = (len(self.sites) - site_idx)

        if actual_time_per_site:
            milling_time = actual_time_per_site * remaining_sites
        else:
            milling_time = self._milling_time_estimate * remaining_sites

        self._remaining_stage_time -= self.estimate_stage_movement_time(self.sites[site_idx])
        total_duration = milling_time + self._remaining_stage_time

        return total_duration

    def estimate_stage_movement_time(self, site):
        """
        Estimation the time taken by the stage to move from current position to the location of given site
        :param site: (CryoFeature) consists X,Y,Z coordinates for stage location in m.
        :return: (float) time to move the stage between two points in s.
        """
        # current position from x,y,z of stage position and eliminating rx,ry,rz
        current_pos = [p[1] for p in self._stage.position.value.items() if len(p[0]) == 1]
        target_pos = site.pos.value  # list
        diff = [abs(target - current) for target, current in zip(target_pos, current_pos)]
        stage_time = math.hypot(diff[0], diff[1], diff[2]) / self._move_speed

        return stage_time

    def estimate_drift_time(self, setting):
        """

        return (float): estimated time to acquire 1 anchor area
        """
        # nb_anchor_scanning = numpy.ceil(
        #     self._settings[setting_nb]["duration"] / self._settings[setting_nb]["dcPeriod"])  # class , va's
        drift_acq_time = 0
        if setting.dcRoi.value != UNDEFINED_ROI:
            nb_anchor_scanning = numpy.ceil(setting.duration.value / setting.dcPeriod.value)  # repeat?
            drift_estimation = AnchoredEstimator(self._scanner, self._detector,
                                                 setting.dcRoi.value, setting.dcDwellTime.value, max_pixels=512 ** 2, follow_drift=False)
            drift_acq_time = drift_estimation.estimateAcquisitionTime() * nb_anchor_scanning

        return drift_acq_time

    def run(self):
        """
        The main function of the task class, which will be called by the future asynchronously

        """
        if not self._future:
            return
        self._future._task_state = RUNNING

        try:
            actual_time_per_site = None
            # if no site is provided
            if len(self.sites) == 0:
                logging.debug("Site location is not provided by the user, cancelling the milling")
                self._future._task_state = CANCELLED
                raise CancelledError()
            for site_idx, site in enumerate(self.sites):
            # for/ site_idx in range(len(self.sites)):
            #     site = self.sites[site_idx]
                # Update progress on the milling sites left
                start_time = time.time()
                remaining_t = self.estimate_milling_time(site_idx, actual_time_per_site)
                self._future.set_end_time(time.time() + remaining_t)

                # .sites[idx].status already instantiated as FEATURE_ACTIVE
                # Move the stage
                target_pos = {"x": site.pos.value[0],
                              "y": site.pos.value[1],
                              "z": site.pos.value[2]}
                # Todo check if it is wise to move in Z direction without affecting the OPTICAL-FIB alignment
                logging.debug("For feature %s moving the stage to %s m", site.name.value, target_pos)
                self._future.running_subf = self._stage.moveAbs(target_pos)
                stage_time = self.estimate_stage_movement_time(site)
                t = stage_time + 5  # adding extra margin
                try:
                    self._future.running_subf.result(t)  # or 2 min should be fast enough to move anywhere
                except TimeoutError:
                    logging.warning("Failed to move the stage for feature %s within %s s", site.name.value, t)
                    self._future.running_subf.cancel()
                    continue  # Try the next feature

                # For each milling setting mill a rectangle for the given site
                for setting_nb, milling_settings in enumerate(self._settings):
                    # Tilt stage angle
                    stage_rx = self._ion_beam_angle - milling_settings.beamAngle.value
                    logging.debug("Tilting stage to %f °", math.degrees(stage_rx))
                    self._future.running_subf = self._stage.moveAbs({"rx": stage_rx})

                    try:
                        t = 15  # s
                        self._future.running_subf.result(timeout=t)
                    except TimeoutError:
                        logging.warning("Failed to tilt the stage for feature %s within %s s", site.name.value, t)
                        self._future.running_subf.cancel()
                        logging.debug("Cancelling the milling")
                        self._future._task_state = CANCELLED
                        raise CancelledError()

                    # Configure current
                    self._scanner.current.value = milling_settings.current.value

                    # Configure HoV
                    self._scanner.horizontalFoV.value = milling_settings.horizontalFoV.value

                    # Initialize the drift corrector
                    # max_pixels can change according to drift correction approach
                    drift_est = AnchoredEstimator(self._scanner, self._detector,
                                                  milling_settings.dcRoi.value,
                                                  milling_settings.dcDwellTime.value,
                                                  max_pixels=512 ** 2, follow_drift=False)
                    # acquire an image at the given location (RoI)
                    drift_est.acquire()
                    da = drift_est.raw[-1]
                    pixel_size = da.metadata[model.MD_PIXEL_SIZE]

                    # Compute the probe size and overlap, based on the requested pixel size
                    # For the probe size, use the pixel size in the largest dimension (typically X)
                    # To adjust the pixel size in Y, compute the overlap so that probe_size * (1-overlap)) == pixelSize.
                    # X is horizontal axis, Y is vertical axis
                    # TODO: does it work if pxs[0] < pxs[1] ?
                    pxs = milling_settings.pixelSize.value
                    probe_size = max(pxs)
                    overlap = [1 - (pxs[0] / probe_size), 1 - (pxs[1] / probe_size)]  # Always >= 0

                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                    hori_dim = (milling_settings.roi.value[2] - milling_settings.roi.value[0]) * \
                               milling_settings.pixelSize.value[0]
                    vert_dim = (milling_settings.roi.value[3] - milling_settings.roi.value[1]) * \
                               milling_settings.pixelSize.value[1]
                    dims = [hori_dim, vert_dim]

                    hori_t = ((milling_settings.roi.value[2] - milling_settings.roi.value[0]) / 2 - 0.5) * \
                             milling_settings.pixelSize.value[0]
                    vert_t = ((milling_settings.roi.value[3] - milling_settings.roi.value[1]) / 2 - 0.5) * \
                             milling_settings.pixelSize.value[1]
                    trans = [hori_t, vert_t]

                    waiting_log_time = milling_settings.duration.value + self.estimate_drift_time(milling_settings)

                    # Make the blanker automatic (ie, disabled when acquiring)
                    self._scanner.blanker.value = None

                    logging.debug(f"Milling setting: {milling_settings.name.value} for feature: {site.name.value}")
                    logging.info(
                        f"Milling a rectangle of dimensions: {dims} m which is {trans} m away from the center will "
                        f"take approximately {waiting_log_time} s")

                    if milling_settings.dcRoi.value != UNDEFINED_ROI:

                        for itr in range(self._iterations[setting_nb]):
                            if self._future._task_state == CANCELLED:
                                raise CancelledError()

                            self._future.running_subf = mill_rectangle(milling_settings.roi.value, self._scanner,
                                                                       iteration=1,
                                                                       duration=self._pass_duration[setting_nb],
                                                                       probe_size=probe_size, overlap=overlap)
                            try:
                                self._future.running_subf.result()
                                logging.debug(f"Milling finished for feature: {site.name.value}")
                            except Exception as exp:
                                logging.error(
                                    f"Milling setting: {milling_settings.name.value} for feature: {site.name.value} at iteration: {itr} failed: {exp}")
                                raise

                            # Acquire an image at the given location (RoI)
                            drift_est.acquire()

                            # Estimate the drift
                            drift_est.estimate()

                            # Move FIB to compensate drift
                            previous_shift = self._scanner.shift.value
                            logging.debug("Ion-beam shift in m : %s", previous_shift)
                            self._scanner.shift.value = (pixel_size[0] * drift_est.drift[0] + previous_shift[0],
                                                         -(pixel_size[1] * drift_est.drift[1]) + previous_shift[
                                                             1])  # shift in m - absolute position
                            logging.debug("New Ion-beam shift in m : %s", self._scanner.shift.value)
                            logging.debug("pixel size in m : %s", pixel_size)

                    else:
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
                            logging.error(
                                f"Milling setting: {milling_settings.name.value} for feature: {site.name.value} failed: {exp}")
                            raise

                    self._scanner.blanker.value = True

                    if self._future._task_state == CANCELLED:
                        raise CancelledError()

                site.status = self._feature_status
                logging.debug(f"The milling of feature {site.name.value} completed")

                # Acquire the stream of milled site
                data = []
                try:
                    self._future.running_subf = acquire(self.streams)
                    data, exp = self._future.running_subf.result()
                    if exp:
                        logging.warning(
                            f"Acquisition for feature {site.name.value} partially failed: {exp}")
                except Exception as exp:
                    logging.exception(f"Acquisition for feature {site.name.value} failed: {exp}")

                # check if cancellation happened while the acquiring future is working
                if self._future._task_state == CANCELLED:
                    raise CancelledError()

                # Check on the acquired data
                if not data:
                    logging.warning("The acquired data array in stream %s for feature %s is empty", self.streams,
                                    site.name)
                else:
                    # Convert the data to StaticStreams, and add them to the feature
                    new_streams = dataio.data_to_static_streams(data)
                    site.streams.value.extend(new_streams)
                    logging.info("The acquisition for stream %s for feature %s is done", self.streams, site.name)

                # Store the actual time during milling one site computed after moving the stage
                actual_time_per_site = time.time() - start_time

        except CancelledError:
            logging.debug("Stopping because milling was cancelled")
            raise
        except Exception as exp:
            logging.exception(f"The milling failed due to {exp}")
            raise
        finally:
            # activate the blanker
            self._scanner.blanker.value = True
            # state that the future has finished
            with self._future._task_lock:
                self._future._task_state = FINISHED
