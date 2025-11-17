import logging
import math
import os
import threading
import time
from pathlib import Path
from concurrent import futures
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, CancelledError
from typing import List, Optional, Union

from odemis import model
from odemis.acq.milling.patterns import (
    MicroexpansionPatternParameters,
    MillingPatternParameters,
    RectanglePatternParameters,
    TrenchPatternParameters,
)
from odemis.acq.milling.tasks import (
    MillingSettings,
    MillingTaskSettings,
)
from odemis.acq.feature import CryoFeature, REFERENCE_IMAGE_FILENAME
from odemis.util import executeAsyncTask

# Check if fibsemOS is available
try:
    from fibsem.microscopes.odemis_microscope import (
        OdemisThermoMicroscope,
        OdemisTescanMicroscope,
        from_odemis_image
    )
    from fibsem.milling import (
        FibsemMillingStage,
        MillingAlignment,
        estimate_total_milling_time,
        mill_stages,
    )
    from fibsem.milling.patterning.patterns2 import (
        BasePattern,
        MicroExpansionPattern,
        RectanglePattern,
        TrenchPattern,
    )
    from fibsem.structures import (
        FibsemMillingSettings,
        Point,
        FibsemImage,
        FibsemImageMetadata,
        FibsemRectangle,
        BeamType,
        ImageSettings,
        MicroscopeState,
    )
    from fibsem.utils import load_microscope_configuration
    FIBSEMOS_INSTALLED = True
except ImportError as e:
    logging.warning(f"fibsemOS is not installed or not available: {e}")
    FIBSEMOS_INSTALLED = False

_persistent_millmng: Optional["FibsemOSMillingTaskManager"] = None


def _get_reference_image(feature: CryoFeature) -> model.DataArray:
    """Get the in-memory reference image for a feature or raise."""

    if feature.reference_image is None:
        logging.error(
            "Missing reference image for feature '%s' (path=%s). "
            "This feature was likely loaded from disk without hydrating reference_image.",
            feature.name.value,
            getattr(feature, "path", None),
        )
        raise ValueError("Missing feature.reference_image.")
    return feature.reference_image


def _crop_to_reduced_area(ref_img: 'FibsemImage', rect: 'FibsemRectangle') -> 'FibsemImage':
    """Crop a fibsemOS image to the provided reduced-area rectangle.

    :param ref_img: The image to crop.
    :param rect: Rectangle with fractional coordinates (left, top, width, height).
    :return: The same image instance with cropped data.
    """

    h, w = ref_img.data.shape[-2], ref_img.data.shape[-1]

    # fractional to pixel indices
    x0 = int(rect.left * w)
    y0 = int(rect.top * h)
    x1 = int((rect.left + rect.width) * w)
    y1 = int((rect.top + rect.height) * h)

    # clamp to valid range just in case of rounding
    x0 = max(0, min(w, x0))
    x1 = max(0, min(w, x1))
    y0 = max(0, min(h, y0))
    y1 = max(0, min(h, y1))

    # crop along the last two axes, DataArray slicing behaves like numpy
    ref_img.data = ref_img.data[..., y0:y1, x0:x1]
    return ref_img


def create_fibsemos_tfs_microscope() -> 'OdemisThermoMicroscope':
    """Create and return a fibsemOS Thermo microscope instance."""

    # TODO: extract the rest of the required metadata

    # stage metadata
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    pre_tilt = stage_md[model.MD_CALIB].get(model.MD_SAMPLE_PRE_TILT, math.radians(35))
    rotation_reference = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]

    # loads the default config
    config = load_microscope_configuration()
    config.system.stage.shuttle_pre_tilt = math.degrees(pre_tilt)
    # Used by fibsemOS for moving the stage flat to the electron beam
    config.system.stage.rotation_reference = math.degrees(rotation_reference)
    # Used by fibsemOS for moving the stage flat to the ion beam
    config.system.stage.rotation_180 = math.degrees(rotation_reference + math.pi)
    microscope = OdemisThermoMicroscope(config.system)

    return microscope

def create_fibsemos_tescan_microscope(config_path: Path = None) -> 'OdemisTescanMicroscope':
    """Create a fibsemOS Tescan microscope instance with the current microscope configuration."""

    # TODO: Extract the rest of the required metadata

    # stage metadata
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    pre_tilt = stage_md[model.MD_CALIB].get(model.MD_SAMPLE_PRE_TILT, math.radians(35))
    rotation_reference = stage_md[model.MD_FAV_SEM_POS_ACTIVE]["rz"]

    # loads the default config
    config = load_microscope_configuration(config_path)
    config.system.stage.shuttle_pre_tilt = math.degrees(pre_tilt)
    # Used by fibsemOS for moving the stage flat to the electron beam
    config.system.stage.rotation_reference = math.degrees(rotation_reference)
    # Used by fibsemOS for moving the stage flat to the ion beam
    config.system.stage.rotation_180 = math.degrees(rotation_reference + math.pi)

    # Get the Tescan SEM component to extract host and port info
    fibsem = model.getComponent(role="fibsem")
    ip_address: str = fibsem.host
    port: int = fibsem.port
    # Pass the IP address to the fibsemOS config as well
    config.system.info.ip_address = ip_address
    microscope = OdemisTescanMicroscope(config.system)

    microscope.connect_to_microscope(ip_address, port)

    return microscope

def create_fibsemos_microscope(config_path: Path = None) -> Union['OdemisThermoMicroscope | OdemisTescanMicroscope']:
    """Create a fibsemOS microscope instance with the current microscope configuration."""
    stage_bare = model.getComponent(role="stage-bare")
    stage_md = stage_bare.getMetadata()
    md_calib = stage_md.get(model.MD_CALIB, {})
    stage_version = md_calib.get("version", None)

    if stage_version == "tfs_3":
        return create_fibsemos_tfs_microscope()
    elif stage_version == "tescan_1":
        return create_fibsemos_tescan_microscope(config_path)
    else:
        raise ValueError(f"Stage version {stage_version} is not supported")


def convert_pattern_to_fibsemos(p: MillingPatternParameters) -> 'BasePattern':
    """Convert from an Odemis pattern to a fibsemOS pattern"""
    if isinstance(p, RectanglePatternParameters):
        return _convert_rectangle_pattern(p)

    elif isinstance(p, TrenchPatternParameters):
        return _convert_trench_pattern(p)

    elif isinstance(p, MicroexpansionPatternParameters):
        return _convert_microexpansion_pattern(p)
    else:
        raise NotImplementedError(f"Conversion not implemented for pattern type: {type(p)}")

def _convert_rectangle_pattern(p: RectanglePatternParameters) -> 'RectanglePattern':
    """Convert an Odemis rectangle pattern to a fibsemOS RectanglePattern."""
    return RectanglePattern(
        width=p.width.value,
        height=p.height.value,
        depth=p.depth.value,
        rotation=p.rotation.value,
        scan_direction=p.scan_direction.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_trench_pattern(p: TrenchPatternParameters) -> 'TrenchPattern':
    """Convert an Odemis trench pattern to a fibsemOS TrenchPattern."""
    return TrenchPattern(
        width=p.width.value,
        upper_trench_height=p.height.value,
        lower_trench_height=p.height.value,
        spacing=p.spacing.value,
        depth=p.depth.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _convert_microexpansion_pattern(p: MicroexpansionPatternParameters) -> 'MicroExpansionPattern':
    """Convert an Odemis microexpansion pattern to a fibsemOS MicroExpansionPattern."""
    return MicroExpansionPattern(
        width=p.width.value,
        height=p.height.value,
        depth=p.depth.value,
        distance=p.spacing.value,
        point=Point(x=p.center.value[0], y=p.center.value[1])
    )

def _format_preset(voltage: float, current: float) -> str:
    """
    Format voltage (V) and current (A) into a string like:
    '30 keV; 150 pA', scaling units automatically.
    """

    # Voltage: always shown in keV
    voltage_keV = voltage / 1000
    voltage_str = f"{voltage_keV:g} keV"

    # Current: choose pA, nA, or uA
    abs_I = abs(current)

    if abs_I < 1e-9:
        # pA
        current_val = current * 1e12
        unit = "pA"
    elif abs_I < 1e-6:
        # nA
        current_val = current * 1e9
        unit = "nA"
    else:
        # uA
        current_val = current * 1e6
        unit = "uA"

    current_str = f"{current_val:g} {unit}"

    return f"{voltage_str}; {current_str}"


def convert_milling_settings(s: MillingSettings) -> 'FibsemMillingSettings':
    """Convert Odemis milling settings to fibsemOS milling settings."""
    return FibsemMillingSettings(
        milling_current=s.current.value,
        milling_voltage=s.voltage.value,
        patterning_mode=s.mode.value,
        hfw=s.field_of_view.value,
        rate=s.rate.value,                # m^3/A/s
        dwell_time=s.dwell_time.value,    # s
        preset=_format_preset(s.voltage.value, s.current.value)
    )

# task converter
def convert_task_to_milling_stage(task: MillingTaskSettings) -> 'FibsemMillingStage':
    """Convert a single Odemis milling task to a fibsemOS milling stage."""
    s = convert_milling_settings(task.milling)
    p = convert_pattern_to_fibsemos(task.patterns[0])
    a = MillingAlignment(enabled=task.milling.align.value)

    milling_stage = FibsemMillingStage(
        name=task.name,
        milling=s,
        pattern=p,
        alignment=a,
    )
    return milling_stage

def convert_milling_tasks_to_milling_stages(milling_tasks: List[MillingTaskSettings]) -> List['FibsemMillingStage']:
    """Convert a list of Odemis milling tasks to fibsemOS milling stages."""
    milling_stages = []

    for task in milling_tasks:
        milling_stage = convert_task_to_milling_stage(task)
        milling_stages.append(milling_stage)

    return milling_stages

class FibsemOSMillingTaskManager:
    """Manage running milling tasks via fibsemOS using a persistent microscope connection."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize the manager and establish the fibsemOS microscope connection."""
        # create microscope connection
        self.microscope = create_fibsemos_microscope(config_path)
        self._lock = threading.Lock()
        self._active = False
        self._cancel_requested = False

        # per-run state (set in async_run)
        self.milling_stages: List["FibsemMillingStage"] = []
        self._future: Optional[futures.Future] = None

    def cancel(self, future: futures.Future) -> bool:
        """Request cancellation of the current milling run."""
        logging.debug("Canceling milling procedure...")
        with self._lock:
            if not self._active:
                return False
            if self._cancel_requested:
                return True
            self._cancel_requested = True
        # Do not hold the lock during potentially blocking calls
        subf = getattr(future, "running_subf", None)
        if subf is not None:
            subf.cancel()
        try:
            self.microscope.stop_milling()
        finally:
            logging.debug("Milling procedure cancelled.")
        return True

    def estimate_milling_time(self) -> float:
        """Estimate the total milling time for the currently configured stages (seconds)."""
        return estimate_total_milling_time(self.milling_stages)

    def _run(self):
        """Internal worker that performs the milling stages sequentially."""
        future = self._future
        if future is None:
            # Should never happen if async_run configured correctly, but don't use assert.
            with self._lock:
                self._active = False
            raise RuntimeError("Internal error: milling run started without an associated future.")

        try:
            for stage in self.milling_stages:
                with self._lock:
                    if self._cancel_requested:
                        raise CancelledError()

                logging.info(f"Running milling stage: {stage.name}")
                ref_img = from_odemis_image(_get_reference_image(self.feature))
                ref_img.metadata.image_settings.path = self.path
                ref_img.metadata.image_settings.reduced_area = stage.alignment.rect

                ref_img = _crop_to_reduced_area(ref_img, stage.alignment.rect)

                mill_stages(self.microscope, [stage], ref_img)

        finally:
            with self._lock:
                self._active = False
                self._cancel_requested = False

    def async_run(self,
                  *,
                  future: futures.Future,
                  tasks: List[MillingTaskSettings],
                  feature: CryoFeature,
                  path: Optional[str] = None) -> futures.Future:
        """Prepare and start a milling run asynchronously (one run at a time)."""
        if path is None:
            path = os.getcwd()

        milling_stages = convert_milling_tasks_to_milling_stages(tasks)
        end_time = time.time() + estimate_total_milling_time(milling_stages) + 30

        with self._lock:
            if self._active:
                raise RuntimeError("A fibsemOS milling session is already running.")
            self._active = True
            self._cancel_requested = False
            self.microscope._last_imaging_settings.path = path
            self.milling_stages = milling_stages
            self.path = path
            self.feature = feature
            self._future = future
            self._future.running_subf = model.InstantaneousFuture()
            self._future.task_canceller = self.cancel
            # +30 s as estimate time only includes milling time, not current switching time, etc
            self._future.set_end_time(end_time)

            try:
                executeAsyncTask(self._future, self._run)
            except Exception:
                self._active = False
                raise
        return self._future


def run_milling_tasks_fibsemos(tasks: List[MillingTaskSettings], feature: CryoFeature, path: Optional[str] = None, config_path: Optional[str] = None) -> futures.Future:
    """Run the given milling tasks asynchronously using a persistent fibsemOS manager."""
    global _persistent_millmng

    if _persistent_millmng is None:
        _persistent_millmng = FibsemOSMillingTaskManager(config_path)

    future = model.ProgressiveFuture()
    return _persistent_millmng.async_run(future=future, tasks=tasks, feature=feature, path=path)
