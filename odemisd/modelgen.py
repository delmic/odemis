# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
# various functions to instantiate a model from a yaml description

import itertools
import model
import yaml

class OdemisSyntaxError(Exception):
    pass

class OdemisSemanticError(Exception):
    pass

def get_instantiation_model(inst_file):
    """
    Converts the instantiation model odm.yaml file into a python representation
    inst_file (file): opened file that contains the yaml
    Note that in practice is does almost no checks but try to decode the yaml file.
    So there is only syntax check, but tries to makes errors clear to understand
    and fix.
    returns (dict str -> dict):  python representation of a yaml file
    
    Raises:
        OdemisSyntaxError in case there is a syntax error
    """
    try:
        # yaml.load() is dangerous as it can create any python object
        data = yaml.safe_load(inst_file)
    except yaml.YAMLError, exc:
        print "Syntax error in microscope instantiation file:", exc
        if hasattr(exc, 'problem_mark'):
            mark = exc.problem_mark
            # display the line
            inst_file.seek(0)
            print list(itertools.islice(inst_file, mark.line, mark.line + 1))[0],
            # display the column
            print " " * mark.column + "^"
        raise OdemisSyntaxError("Syntax error in microscope instantiation file.")
    return data
    
def instantiate_model(inst_model, dry_run=False):
    """
    Generates the real microscope model from the microscope instantiation model
    inst_model (dict str -> dict): python representation of the yaml instantiation file
    dry_run (bool): if True, it will check the semantic and try to instantiate the 
      model without actually any driver contacting the hardware.
    returns 2-tuple (set (HwComponents), Microscope): the set of all the 
      HwComponents in the model, and specifically the Microscope component
      
    Raises:
        OdemisSemanticError in case an error in the model is detected. Note that
        (obviously) not every 
        Exception (dependent on the driver): in case initialisation of a driver fails
    """
    comps = set()
    mic = None
    
    # mark the children by adding a "parent" attribute
    for name, attr in inst_model.items():
        print name
        if "children" in attr:
            for childname in attr["children"]:
                # detect direct loop
                if childname == name:
                    raise OdemisSyntaxError("Semantic error in "
                            "microscope instantiation file: component %s "
                            "has itself as children." % name)
                # detect child with multiple parents
                if "parent" in inst_model[childname]:
                    raise OdemisSyntaxError("Semantic error in "
                            "microscope instantiation file: component %s "
                            "is child of both %s and %s." 
                            % (childname, name, inst_model[childname]["parent"]))
                inst_model[childname]["parent"] = name
    
    # for each component which is not child
        # load class (with special case for two types of components?)
        # instance class with args
        # add it to the list of comps
        # if it has children, add the children to the list 
    for name, attr in inst_model.items():
        print name
        if "parent" in attr: # children are created by their parents
            continue
        comp = instantiate_comp(name, attr)
        
    # look for the microscope component (check there is only one)
        
    # Connect all the sub-components of microscope: detectors, emmiters, actuators 
    
    # if the set comps - (microscope + detect + emmiters + actuators) contains components
    # without children => warn
    
    # for each component, set the affect
    
    # for each component set the properties
    
    return comps, mic