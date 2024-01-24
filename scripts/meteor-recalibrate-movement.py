#!/usr/bin/env python3
'''
@author: Patrick Cleeve

This script is intended only for METEOR on TFS FIBSEMs. 
This script is to update the calibration metadata for the stage-bare component while odemis is running.

The intended use for the script is to easily update the calibrate values for stage movements, when the meteor is
moving with no rotation, i.e. moving between the FIB <-> METEOR positions, rather than between SEM <-> METEOR positions.
 
The script will ask the user for the distance between the FIB and METEOR positions in the x and y directions. It will then convert these values
to the calibration metadata format and update the stage-bare component with the new values.

run as:
python3 ./scripts/meteor-recalibrate-movement.py 

and follow the instructions in the terminal.

'''

# TODO: update the yaml file with these values too
# TODO: create a plugin for this

import math
from odemis import model
from odemis.acq.move import isNearPosition, ATOL_ROTATION_TRANSFORM
from odemis.util.driver import isInRange
from Pyro4.errors import CommunicationError

def main():

    print(f"METEOR Stage Movement Calibration Updater")
    print(f"-" * 20)
    
    # get stage-bare component
    try:
        stage = model.getComponent(role="stage-bare")
    except CommunicationError as e:
        print("ERROR: Could not connect to odemis. Is odemis running?")
        return

    print(f"Current stage-bare component: {stage}")    
    print(f"-" * 20)
    
    # check which movement mode the calibration data is intended for, if not FIB <-> METEOR, warn user.
    stage_md = stage.getMetadata()
    fm_pos_active = stage_md[model.MD_FAV_FM_POS_ACTIVE]
    sem_pos_active = stage_md[model.MD_FAV_SEM_POS_ACTIVE]

    # check if configured for  FIB <-> METEOR movement
    # SEM <-> METEOR movement has rz axis movement, FIB <-> METEOR movement does not
    has_rz = not isNearPosition(sem_pos_active, fm_pos_active, {"rz"}, atol_rotation=ATOL_ROTATION_TRANSFORM)

    if has_rz:
        sem_rz = f"{math.degrees(sem_pos_active['rz']):02f}"
        flm_rz = f"{math.degrees(fm_pos_active['rz']):02f}"
        err_msg = f"""ERROR: 
        The current calibration data is for SEM <-> METEOR movement (movement with rotation).
        The current rotation positions are: FIBSEM: {sem_rz} deg, METEOR: {flm_rz} deg
        This script is intended for FIB <-> METEOR movement (movement with no rotation). 
        If you wish to continue, please update the calibration data in the yaml file to FIB <-> METEOR movement. 
        You will need to restart odemis for the changes to take effect.
        """
        print(err_msg)
        return

    print(f"Valid calibration data found for FIB <-> METEOR movement. Continuing...")
    print('-' * 20)

    print(f"Calibration values are the distance between the FIB and METEOR positions in the x and y directions.")
    print(f"Current calibration values: {stage_md[model.MD_POS_COR]}")

    # get imaging ranges
    sem_active_range = stage_md[model.MD_SEM_IMAGING_RANGE]
    flm_active_range = stage_md[model.MD_FM_IMAGING_RANGE]
    working_range = {"x": [0, 0], "y": [0, 0]}

    for axis in ["x", "y"]: 
        margin = (sem_active_range[axis][1] - sem_active_range[axis][0]) * 0.05 # 5% of SEM imaging range
        x0 = flm_active_range[axis][0] - sem_active_range[axis][0] - margin     # minimum move sem -> flm
        x1 = flm_active_range[axis][1] - sem_active_range[axis][0] + margin     # maximum move sem -> flm
        working_range[axis] = [x0, x1]
 
    print(f"""Calibration data should be within the ranges: 
        x range: {working_range['x']}
        y range: {working_range['y']} 
        """)

    # get x, y values from user
    x = input(f"Enter new x calibration value (m): ")
    y = input(f"Enter new y calibration value (m): ")
    print("-" * 20)

    # check they are floats
    try:
        x = float(x) 
        y = float(y)
    except  ValueError:
        print("Invalid input. Please enter a float. Calibration data has not been updated.")
        return
    
    # validate range
    x_stage_range = stage.axes["x"].range
    y_stage_range = stage.axes["y"].range
    # check within stage axes range
    if not isInRange({"x": x}, {'x': x_stage_range}, {'x'}):
        print(f"Invalid input. x value {x} is outside of the range of the stage {x_stage_range}. Calibration data has not been updated.")
        return
    
    if not isInRange({"y": y}, {'y': y_stage_range}, {'y'}):
        print(f"Invalid input. y value {y} is outside of the range of the stage {y_stage_range}. Calibration data has not been updated.")
        return

    # check if within imaging range
    if not isInRange({"x": x, "y": y}, working_range, {'x', 'y'}):
        print(f"""Invalid input. Requested calibration would put stage outside of fm imaging range.
              The calibration should be within these ranges:  
            x: {x} (range: {working_range['x']}.
            y: {y} (range: {working_range['y']}. 
            Calibration data has not been updated.""")
        return

    # get user confirmation for values
    print(f"Change to new calibration values: {[x, y]} m")

    ret = input(f"Confirm? (y/N): ")

    if ret.lower() == "y": 

        md_pos_cor = {model.MD_POS_COR: [x, y]}
        stage.updateMetadata(md_pos_cor)

        print(f"Calibration values updated to: {stage.getMetadata()[model.MD_POS_COR]}")
    else:
        print("Calibration aborted. The calibration data was not updated.")


if __name__ == "__main__":
    main()