#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 10 Jun 2026

@author: Éric Piel

This is a script to acquire SEM and CCD images on a SPARC every 15 minutes and
track the drift of the SEM image over time. The shift between each SEM image and
the first one is computed and stored in a CSV file.

Run as:
./scripts/sparc_mirror_time_track.py --sem-fov 100e-6 --sem-focus 3.5e-3 \\
    --spot-focus 4.0e-3 --output ~/Pictures/mirror_track

--sem-fov:    horizontal field-of-view for the SEM image, in meters (e.g. 100e-6)
--sem-focus:  ebeam-focus Z position used when acquiring the SEM image, in meters
--spot-focus: ebeam-focus Z position used when the e-beam is in spot mode for CCD
--output:     base path/prefix for all output files.  The script creates:
                <prefix>.csv          — timestamps and X/Y drift in meters
                <prefix>_sem_NNN.ome.tiff — SEM image at each step
                <prefix>_ccd_NNN.ome.tiff — CCD image at each step
--period:     time between acquisitions in seconds (default: 900, i.e. 15 min)

Stop the script at any time with Ctrl+C.

You first need to run the odemis backend with a SPARC config, e.g.:
odemis-start install/linux/usr/share/odemis/sim/sparc2-mirror-alignment-sim.odm.yaml
"""

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

from odemis import dataio, model
from odemis.acq.align.shift import MeasureShift
from odemis.dataio import tiff

logging.getLogger().setLevel(logging.INFO)

# Dwell time used when the e-beam is in spot mode (CCD acquisition).
SPOT_DWELL_TIME = 1  # s
# FoV set on the e-beam when acquiring with the CCD in spot mode.
SPOT_FOV = 1e-6  # m  (1 µm)
# Scale factor for SEM acquisition.
SEM_SCALE = (2, 2)
SEM_DWELL_TIME = 1e-6  # s (1 µs)

AR_MD = {model.MD_AR_POLE, model.MD_AR_FOCUS_DISTANCE, model.MD_AR_PARABOLA_F,
         model.MD_AR_XMAX, model.MD_AR_HOLE_DIAMETER, model.MD_ROTATION,
         model.MD_ROTATION_COR, model.MD_SHEAR, model.MD_SHEAR_COR}


def _discard_data(df: model.DataFlow, data: model.DataArray) -> None:
    """Dummy subscriber used to keep the e-beam active in spot mode."""
    pass


def acquire_sem_image(
    escan: model.HwComponent,
    sed: model.HwComponent,
    efocus: model.HwComponent,
    sem_fov: float,
    sem_focus_z: float,
    sem_dwell_time: float,
) -> model.DataArray:
    """Acquire a full-frame SEM image at the specified FoV and focus Z.

    :param escan: e-beam scanner component.
    :param sed: secondary-electron detector component.
    :param efocus: ebeam-focus actuator component.
    :param sem_fov: horizontal field-of-view for the SEM image, in meters.
    :param sem_focus_z: Z position of the ebeam-focus actuator, in meters.
    :param sem_dwell_time: dwell time to use for the SEM scan, in seconds.
      Explicitly set on every call to undo any changes made by prior acquisitions.
    :returns: acquired SEM image as a DataArray.
    """
    logging.info("Moving ebeam-focus to SEM z=%.4g m", sem_focus_z)
    efocus.moveAbs({"z": sem_focus_z}).result()

    logging.info("Setting SEM FoV to %.4g m", sem_fov)
    escan.horizontalFoV.value = sem_fov

    logging.info("Setting SEM scale to %s", SEM_SCALE)
    escan.scale.value = SEM_SCALE
    # After changing the scale the resolution range updates; use the maximum (it's automatically cliped)
    escan.resolution.value = escan.resolution.range[1]
    logging.info("SEM resolution: %s", escan.resolution.value)

    # Restore the dwell time explicitly — a previous spot-mode acquisition might
    # have left a much longer dwell time on the hardware.
    escan.dwellTime.value = sem_dwell_time
    logging.info("SEM dwell time: %.4g s", escan.dwellTime.value)

    logging.info("Acquiring SEM image")
    return sed.data.get()


def acquire_ccd_image(
    escan: model.HwComponent,
    sed: model.HwComponent,
    ccd: model.HwComponent,
    efocus: model.HwComponent,
    spot_focus_z: float,
) -> model.DataArray:
    """Acquire a CCD image with the e-beam in spot mode.

    The CCD exposure settings are left unchanged (whatever the hardware is currently
    using).  Only the e-beam configuration is overridden.

    :param escan: e-beam scanner component.
    :param sed: secondary-electron detector component (used to keep the beam active).
    :param ccd: CCD camera component.
    :param efocus: ebeam-focus actuator component.
    :param spot_focus_z: Z position of the ebeam-focus actuator for spot mode, in meters.
    :returns: acquired CCD image as a DataArray.
    """
    logging.info("Moving ebeam-focus to spot z=%.4g m", spot_focus_z)
    efocus.moveAbs({"z": spot_focus_z}).result()

    logging.info("Setting e-beam to spot mode (FoV=%.4g m)", SPOT_FOV)
    # Save the original dwell time so it can be restored after spot-mode acquisition.
    original_dwell_time = escan.dwellTime.value
    escan.horizontalFoV.value = SPOT_FOV
    escan.scale.value = (1, 1)
    escan.resolution.value = (1, 1)
    escan.dwellTime.value = SPOT_DWELL_TIME

    # Subscribe to sed to keep the beam active while the CCD acquires.
    sed.data.subscribe(_discard_data)
    try:
        logging.info("Acquiring CCD image (current CCD settings)")
        return ccd.data.get()
    finally:
        sed.data.unsubscribe(_discard_data)
        escan.dwellTime.value = original_dwell_time


def compute_shift_m(
    first_img: model.DataArray,
    current_img: model.DataArray,
) -> tuple[float, float]:
    """Compute the X/Y shift of current_img relative to first_img, in meters.

    :param first_img: reference (first) SEM image.
    :param current_img: current SEM image.
    :returns: tuple (shift_x_m, shift_y_m) – shift in meters, positive values
      indicate that the current image has moved towards increasing X/Y.
    """
    shift_px = MeasureShift(first_img, current_img, precision=10)
    pxs = current_img.metadata[model.MD_PIXEL_SIZE]
    # MeasureShift returns (x, y) in pixels.
    shift_x_m = shift_px[0] * pxs[0]
    shift_y_m = shift_px[1] * pxs[1]
    logging.info(
        "Drift: x=%.3g m (%.2f px)  y=%.3g m (%.2f px)",
        shift_x_m, shift_px[0],
        shift_y_m, shift_px[1],
    )
    return shift_x_m, shift_y_m


def run_timelapse(
    sem_fov: float,
    sem_focus_z: float,
    spot_focus_z: float,
    output_prefix: Path,
    period: float,
) -> None:
    """Main acquisition loop – runs until Ctrl+C is pressed.

    :param sem_fov: horizontal FoV for SEM images, in meters.
    :param sem_focus_z: ebeam-focus Z for SEM acquisition, in meters.
    :param spot_focus_z: ebeam-focus Z for CCD spot acquisition, in meters.
    :param output_prefix: base Path used to derive all output filenames.
    :param period: time between acquisition cycles, in seconds.
    """
    # Find hardware components.
    escan = model.getComponent(role="e-beam")
    sed = model.getComponent(role="se-detector")
    ccd = model.getComponent(role="ccd")
    efocus = model.getComponent(role="ebeam-focus")

    # Prepare output paths.
    csv_path = output_prefix.with_suffix(".csv")
    sem_pattern = str(output_prefix) + "_sem_%03d.ome.tiff"
    ccd_pattern = str(output_prefix) + "_ccd_%03d.ome.tiff"

    first_sem_img = None
    i = 0
    csv_file = csv_path.open("w", newline="")
    try:
        writer = csv.writer(csv_file)
        writer.writerow(["timestamp", "shift_x_m", "shift_y_m"])

        while True:
            i += 1
            logging.info("--- Acquisition %d ---", i)
            cycle_start = time.time()

            # TODO: allow to select the dwell time via a command-line argument?
            # SEM image
            sem_img = acquire_sem_image(escan, sed, efocus, sem_fov, sem_focus_z, SEM_DWELL_TIME)
            sem_fn = sem_pattern % i
            tiff.export(sem_fn, sem_img)
            logging.info("Saved SEM image: %s", sem_fn)

            # CCD image.
            ccd_img = acquire_ccd_image(escan, sed, ccd, efocus, spot_focus_z)
            ccd_fn = ccd_pattern % i
            # Drop AR metadata, so that when opening the image, it's not shown as AR
            for k in AR_MD:
                ccd_img.metadata.pop(k, None)

            tiff.export(ccd_fn, ccd_img)
            logging.info("Saved CCD image: %s", ccd_fn)

            # Compute drift relative to first image.
            if first_sem_img is None:
                first_sem_img = sem_img
                shift_x_m, shift_y_m = 0.0, 0.0
            else:
                shift_x_m, shift_y_m = compute_shift_m(first_sem_img, sem_img)

            # Write CSV row.
            writer.writerow([time.time(), shift_x_m, shift_y_m])
            csv_file.flush()

            # Sleep until the next cycle.
            elapsed = time.time() - cycle_start
            remaining = period - elapsed
            if remaining < 0:
                logging.warning(
                    "Acquisition took %.1f s, which is %.1f s longer than the period",
                    elapsed, -remaining,
                )
            else:
                logging.info("Sleeping for %.1f s until next acquisition", remaining)
                time.sleep(remaining)

    except KeyboardInterrupt:
        logging.info("Interrupted by user after %d acquisition(s)", i)
    finally:
        csv_file.close()
        logging.info("CSV saved to: %s", csv_path)


def main(args: list[str]) -> int:
    """Handle command-line arguments and start the acquisition loop.

    :param args: command-line arguments list (including the script name at index 0).
    :returns: exit code (0 on success, non-zero on error).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Periodically acquire SEM and CCD images on a SPARC and track "
            "the SEM image drift over time."
        )
    )
    parser.add_argument(
        "--sem-fov", dest="sem_fov", type=float, required=True,
        help="Horizontal field-of-view for SEM images, in meters (e.g. 100e-6)",
    )
    parser.add_argument(
        "--sem-focus", dest="sem_focus_z", type=float, required=True,
        help="ebeam-focus Z position used for SEM acquisition, in meters",
    )
    parser.add_argument(
        "--spot-focus", dest="spot_focus_z", type=float, required=True,
        help="ebeam-focus Z position used for CCD spot-mode acquisition, in meters",
    )
    parser.add_argument(
        "--output", "-o", dest="output", required=True,
        help=(
            "Base path/prefix for output files. "
            "Creates <prefix>.csv, <prefix>_sem_NNN.ome.tiff, <prefix>_ccd_NNN.ome.tiff"
        ),
    )
    parser.add_argument(
        "--period", dest="period", type=float, default=900.0,
        help="Time between acquisitions in seconds (default: 900 = 15 min)",
    )
    parser.add_argument(
        "--log-level", dest="loglev", metavar="<level>", type=int, default=1,
        help="Verbosity level: 0=WARNING, 1=INFO (default), 2=DEBUG",
    )

    options = parser.parse_args(args[1:])

    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    logging.getLogger().setLevel(loglev_names[min(len(loglev_names) - 1, options.loglev)])

    output_prefix = Path(options.output)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    try:
        run_timelapse(
            sem_fov=options.sem_fov,
            sem_focus_z=options.sem_focus_z,
            spot_focus_z=options.spot_focus_z,
            output_prefix=output_prefix,
            period=options.period,
        )
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("Unexpected error while running acquisition.")
        return 127

    return 0


if __name__ == "__main__":
    ret = main(sys.argv)
    logging.shutdown()
    sys.exit(ret)
