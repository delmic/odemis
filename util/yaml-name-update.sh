#!/bin/sh
# Update the components name of one microscope file to the newer names

cp "$1" "$1"~

# The (:|,|]|}) are there to catch (just) the non-quoted version
sed -i -r -e 's/"Spectra"/"Light Engine"/g
              s/Spectra(:|,|]|})/"Light Engine"\1/g
              s/"Nikon Lens"/"Optical Objective"/
              s/"Nikon Super Duper"/"Optical Objective"/
              s/"MultiBand Fixed Filter"/"Optical Emission Filter"/g
              s/"MultiBand Filter"/"Optical Emission Filter"/g
              s/"FW102C"/"Optical Emission Filter"/g
              s/(^|\s)FW102C(:|,|]|})/\1"Optical Emission Filter"\2/g
              s/"EBeam ExtXY"/"SEM E-beam"/g
              s/"SEM ExtXY"/"SEM Scan Interface"/g
              s/"Zyla"/"Camera"/g
              s/Zyla(:|,|]|})/"Camera"\1/g
              s/"Clara"/"Camera"/g
              s/Clara(:|,|]|})/"Camera"\1/g
              s/"SED ExtXY"/"SEM Detector"/g
              s/"Sample stage"/"Sample Stage"/g
              s/"OLStage"/"Sample Stage"/g
              s/OLStage(:|,|]|})/"Sample Stage"\1/g
              s/"Optical stage"/"Objective Stage"/g
              s/"SEM-Optical Alignment"/"Objective Stage"/g
              s/"Optical focus"/"Optical Focus"/g
              s/"OpticalZ actuator"/"Optical Focus"/g
              s/"PIGCS"/"Stage Actuators"/g
              s/(^|\s)PIGCS(:|,|]|})/\1"Stage Actuators"\2/g
              s/"TMCM"/"Sample Holder Actuators"/g
              s/(^|\s)TMCM(:|,|]|})/\1"Sample Holder Actuators"\2/g
              s/"MirrorMover"/"Mirror Actuators"/g
              s/MirrorMover(:|,|]|})/"Mirror Actuators"\1/g
              s/"AndorSpec"/"Spectrometer"/g
              s/(^|\s)AndorSpec(:|,|]|})/\1"Spectrometer"\2/g
              s/"iDus"/"Spectral Camera"/g
              s/iDus(:|,|]|})/"Spectral Camera"\1/g
              s/"SR303i"/"Spectrograph"/g
              s/SR303i(:|,|]|})/"Spectrograph"\1/g
              s/"MFFSelector"/"Fiber Flipper"/g
              s/MFFSelector(:|,|]|})/"Fiber Flipper"\1/g
              s/"MFFLens"/"Focus Lens Flipper"/g
              s/MFFLens(:|,|]|})/"Focus Lens Flipper"\1/g
              s/"AR Lens"/"Focus Lens"/g
              s/"ARCam"/"Angular Camera"/g
              s/ARCam(:|,|]|})/"Angular Camera"\1/g
              ' "$1"

# To remove the possible double " introduced (eg: ""Spectra"")
sed -i -e 's/""/"/g' "$1"

# Show the changes
diff -u "$1"~ "$1"

          

