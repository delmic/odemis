#!/bin/bash
# Move the turret to the dedicated exchange position and back again
# arg1: "<kymera serial number>" (eg KY-0523) to select the kymera if not running yet
# arg2: "<odemis role>" (eg spectrograph-dedicated) to select the kymera if backend running
# Example:
# kymera-exchange-turret.sh KY-0523 spectrograph-dedicated

WINDOW_TITLE="Turret exchange"
ICON_PATH="$HOME/.local/share/icons/hicolor/128x128/apps/ky328-turret.png"

# Check there is 2 arguments
if [ "$#" -ne 2 ]; then
    echo "Expected 2 arguments: serial number and odemis role"
    exit 1
fi

serial_num=$1
role=$2

# Confirm the user really wants to change the turret
zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --question --text "Do you want to exchange the turret of the Kymera 328i?" --ok-label "Yes" --cancel-label "No"
if [ $? -ne 0 ]; then
    exit 0
fi

# Check if odemis is running
odemis --check
status=$?

if [ $status = 0 ] || [ $status = 3 ] ; then  # Running or starting
    select_args="--role $role"
    # For safety, close the shutter of the streak cam (in case it's not already closed)
    odemis --set-attr streak-unit shutter True
else
    select_args="--serial $serial_num"
fi

# Move the turret to the exchange position
shrkconfig --exchange-turret $select_args | zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --progress --pulsate --no-cancel --text "Moving Kymera 328i turret..." --auto-close
shkconfig_status=${PIPESTATUS[0]}
if [ $shkconfig_status -ne 0 ]; then
    zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --error --text "Error moving Kymera 328i turret"
    exit 1
fi

# Wait until the user has exchanged the turret
zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --info --text "You can now exchange the turret. Put back the cap, and then press OK when done."

# Move the turret back to the original position
shrkconfig --detect-turret $select_args | zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --progress --pulsate --no-cancel --text "Moving Kymera 328i turret back..." --auto-close
shkconfig_status=${PIPESTATUS[0]}
if [ $shkconfig_status -ne 0 ]; then
    zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --error --text "Error moving back Kymera 328i turret. Trying turn off/on the spectrograph."
    exit 1
fi

# Show a message to the user that's done
if [ $status = 0 ] || [ $status = 3 ] ; then  # Running or starting
    zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --info --text "Spectrograph ready, you can now do full stop of Odemis, and restart it."
else
    zenity --title "$WINDOW_TITLE" --window-icon "$ICON_PATH" --info --text "Spectrograph ready, you can start Odemis"
fi