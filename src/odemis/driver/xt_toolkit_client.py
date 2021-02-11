# -*- coding: utf-8 -*-
"""
Created on 11 Feb 2021

@author: Kornee Kleijwegt

Copyright © 2021 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your
option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see
http://www.gnu.org/licenses/.
"""
import logging
import math

from odemis import model
from odemis.driver import xt_client


class SEM(xt_client.SEM):
    """
    Driver which extends the xt_client.SEM (containing the XT software) class with the addition of the XTtoolkit
    functionality. The XT software is used by TFS to control their microscopes.
    XTtoolkit provides extra functionality for the FAST-EM project which xtlib does not provide, it is a development
    library by TFS. To use this driver the XT adapter developed by Delmic should be running on the TFS PC. In the user
    configuration file `delmic-xt-config.ini` on the Microscope PC, xt_type must be set to "xttoolkit".
    Communication to the Microscope server is done via Pyro5.
    """

    def __init__(self, name, role, children, address, daemon=None, **kwargs):
        """
        Parameters
        ----------
        address: str
            server address and port of the Microscope server, e.g. "PYRO:Microscope@localhost:4242"
        timeout: float
            Time in seconds the client should wait for a response from the server.
        """
        super(SEM, self).__init__(name, role, children, address, daemon=daemon, **kwargs)

        # Overwrite the scanner child created in the parten __init__
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("SEM was not given a 'scanner' child")
        self.children.value.remove(self._scanner)  # Remove single beam Scanner
        self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)  # Add multibeam Scanner
        self.children.value.add(self._scanner)

class Scanner(xt_client.Scanner):
    """
    This class extends  behaviour of the xt_client.Scanner class with XTtoolkit functionality.
    xt_client.Scanner contains Vigilant Attributes for magnification, accel voltage, blanking, spotsize, beam shift,
    rotation and dwell time. This class adds XTtoolkit functionality via the Vigilant Attributes for the pitch,
    beam stigmator, pattern stigmator, the beam shift transformation matrix (read-only),
    multiprobe rotation (read-only), aperture index, beamlet index, beam mode (multi/single beam)
    Whenever one of these attributes is changed, its setter also updates another value if needed.
    """

    def __init__(self, name, role, parent, hfw_nomag, **kwargs):
        super(Scanner, self).__init__(name, role, parent, hfw_nomag, **kwargs)

        # Add XTtoolkit specific VA's
        pitch_info = self.parent.pitch_info()
        # TODO change VA and method names with pitch to delta pitch
        self.pitch = model.FloatContinuous(
            self.parent.get_pitch() * 1e-6,
            unit=pitch_info["unit"],
            range=tuple(i * 1e-6 for i in pitch_info["range"]),
            setter=self._setPitch,
        )

        beam_stigmator_info = self.parent.primary_stigmator_info()
        beam_stigmator_range_x = beam_stigmator_info["range"]["x"]
        beam_stigmator_range_y = beam_stigmator_info["range"]["y"]
        beam_stigmator_range = tuple((i, j) for i, j in zip(beam_stigmator_range_x, beam_stigmator_range_y))
        self.beamStigmator = model.TupleContinuous(
            tuple(self.parent.get_primary_stigmator()),
            unit=beam_stigmator_info["unit"],
            range=beam_stigmator_range,
            setter=self._setBeamStigmator)

        pattern_stigmator_info = self.parent.secondary_stigmator_info()
        pattern_stigmator_range_x = pattern_stigmator_info["range"]["x"]
        pattern_stigmator_range_y = pattern_stigmator_info["range"]["y"]
        pattern_stigmator_range = tuple((i, j) for i, j in zip(pattern_stigmator_range_x,
                                                                   pattern_stigmator_range_y))
        self.patternStigmator = model.TupleContinuous(
            tuple(self.parent.get_secondary_stigmator()),
            unit=pattern_stigmator_info["unit"],
            range=pattern_stigmator_range,
            setter=self._setPatternStigmator)

        self.beamShiftTransformationMatrix = model.ListVA(
            self.parent.get_dc_coils(),
            unit=None,
            readonly=True)

        self.multiprobeRotation = model.FloatVA(
            math.radians(self.parent.get_mpp_orientation()),
            unit="rad",
            readonly=True)

        aperture_index_info = self.parent.aperture_index_info()
        self.apertureIndex = model.IntContinuous(
            int(self.parent.get_aperture_index()),
            unit=aperture_index_info["unit"],
            range=tuple(int(i) for i in aperture_index_info["range"]),
            setter=self._setApertureIndex)

        beamlet_index_info = self.parent.beamlet_index_info()
        beamlet_index_range_x = beamlet_index_info["range"]["x"]
        beamlet_index_range_y = beamlet_index_info["range"]["y"]
        beamlet_index_range = tuple((int(i), int(j)) for i, j in zip(beamlet_index_range_x, beamlet_index_range_y))
        beamlet_index = self.parent.get_beamlet_index()
        self.beamletIndex = model.TupleContinuous(
            tuple(int(i) for i in beamlet_index),  # convert tuple values to integers.,
            unit=beamlet_index_info["unit"],
            range=beamlet_index_range,
            setter=self._setBeamletIndex
        )

        multibeam_mode = (self.parent.get_use_case() == 'MultiBeamTile')
        self.multiBeamMode = model.BooleanVA(
                multibeam_mode,
                setter=self._setMultiBeamMode
        )

    def _updateSettings(self):
        """
        Read all the current settings from the SEM and reflects them on the VAs
        """
        # TODO When the new approach of adding update function to a list is implemented in xt_client.py instead of
        #  overwriting the method updateSettings the method _updateMBSettings can be added to the list of
        #  functions to be updated.

        # Polling XT client settings
        super(Scanner, self)._updateSettings()
        # Polling XTtoolkit settings
        # TODO K.K. how to remove waiting but not have error because of inheritance.
        import time
        time.sleep(1)
        self._updateMBSettings()

    def _updateMBSettings(self):
        """
        Read all the current settings for the multi beam SEM via XTtoolkit and reflects them on the VAs
        """
        try:
            pitch = self.parent.get_pitch()
            if pitch != self.pitch.value:
                self.pitch._value = pitch
                self.pitch.notify(pitch)
            beam_stigmator = self.parent.get_primary_stigmator()
            if beam_stigmator != self.beamStigmator.value:
                self.beamStigmator._value = beam_stigmator
                self.beamStigmator.notify(beam_stigmator)
            pattern_stigmator = self.parent.get_secondary_stigmator()
            if pattern_stigmator != self.patternStigmator.value:
                self.patternStigmator._value = pattern_stigmator
                self.patternStigmator.notify(pattern_stigmator)
            beam_shift_transformation_matrix = self.parent.get_dc_coils()
            if beam_shift_transformation_matrix != self.beamShiftTransformationMatrix.value:
                self.beamShiftTransformationMatrix._value = beam_shift_transformation_matrix
                self.beamShiftTransformationMatrix.notify(beam_shift_transformation_matrix)
            mpp_rotation = math.radians(self.parent.get_mpp_orientation())
            if mpp_rotation != self.multiprobeRotation.value:
                self.multiprobeRotation._value = mpp_rotation
                self.multiprobeRotation.notify(mpp_rotation)
            aperture_index = self.parent.get_aperture_index()
            if aperture_index != self.apertureIndex.value:
                self.apertureIndex._value = aperture_index
                self.apertureIndex.notify(aperture_index)
            beamlet_index = tuple(int(i) for i in self.parent.get_beamlet_index())
            if beamlet_index != self.beamletIndex.value:
                self.beamletIndex._value = beamlet_index
                self.beamletIndex.notify(beamlet_index)
            multibeam_mode = (self.parent.get_use_case() == 'MultiBeamTile')
            if multibeam_mode != self.multiBeamMode.value:
                self.multiBeamMode._value = multibeam_mode
                self.multiBeamMode.notify(multibeam_mode)

        except Exception:
            logging.exception("Unexpected failure when polling XTtoolkit settings")

    def _setPitch(self, pitch):
        self.parent.set_pitch(pitch * 1e6)  # Convert from micrometer to meters.
        return self.parent.get_pitch() * 1e-6

    def _setBeamStigmator(self, beam_stigmator_value):
        self.parent.set_primary_stigmator(*beam_stigmator_value)
        return self.parent.get_primary_stigmator()

    def _setPatternStigmator(self, pattern_stigmator_value):
        self.parent.set_secondary_stigmator(*pattern_stigmator_value)
        return self.parent.get_secondary_stigmator()

    def _setApertureIndex(self, aperture_index):
        self.parent.set_aperture_index(aperture_index)
        return int(self.parent.get_aperture_index())

    def _setBeamletIndex(self, beamlet_index):
        self.parent.set_beamlet_index(beamlet_index)
        new_beamlet_index = self.parent.get_beamlet_index()
        return tuple(int(i) for i in new_beamlet_index)  # convert tuple values to integers.

    def _setMultiBeamMode(self, multi_beam_mode):
        # TODO: When changing the beam mode of the microscope we don't want to also change the aperture and beamlet
        #  index. However, it this is the current implementation direction of TFS. Therefore in the future code like
        #  the uncommented parts below need to be implemented. Note this code still needs to be tested and is
        #  therefore now just an example of an implementation.
        # current_aperture = self.apertureIndex.value
        # current_beamlet = self.beamletIndex.value
        if multi_beam_mode:
            self.parent.set_use_case('MultiBeamTile')
        else:
            self.parent.set_use_case('SingleBeamlet')

        # TODO: Example of an implementation to make sure aperture is unchanged when switching modes, see TODO above.
        # if self.parent.get_aperture_index() != current_aperture or self.parent.get_beamlet_index() != current_beamlet:
        #     time.sleep(3)
        #     self.apertureIndex.value = current_aperture
        #     self.beamletIndex.value = current_beamlet
        return (self.parent.get_use_case() == 'MultiBeamTile')