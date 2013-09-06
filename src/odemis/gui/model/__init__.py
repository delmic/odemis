# -*- coding: utf-8 -*-
"""
Created on 22 Feb 2013

@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This package contains contains all classes that describe various objects that
can be controlled through the GUI.

"""

import stream

# TODO: move to stream

# All the stream types related to optical
OPTICAL_STREAMS = (stream.FluoStream,
                   stream.BrightfieldStream,
                   stream.StaticStream)

# All the stream types related to electron microscope
EM_STREAMS = (stream.SEMStream,
              stream.StaticSEMStream)

SPECTRUM_STREAMS = (stream.SpectrumStream,
                    stream.StaticSpectrumStream)

AR_STREAMS = (stream.ARStream,
              stream.StaticARStream) # TODO: StaticARStream
