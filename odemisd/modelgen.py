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
import logging
import model
import re
import yaml

class ParseError(Exception):
    pass

class SemanticError(Exception):
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
        ParseError in case there is a syntax error
    """
    try:
        # yaml.load() is dangerous as it can create any python object
        data = yaml.safe_load(inst_file)
    except yaml.YAMLError, exc:
        logging.error("Syntax error in microscope instantiation file: %s", exc)
        if hasattr(exc, 'problem_mark'):
            mark = exc.problem_mark
            # display the line
            inst_file.seek(0)
            line = list(itertools.islice(inst_file, mark.line, mark.line + 1))[0]
            logging.error("%s", line)
            # display the column
            logging.error(" " * mark.column + "^")
        raise ParseError("Syntax error in microscope instantiation file.")
    
    logging.debug("YAML file read like this: " + str(data))
    # TODO detect duplicate keys (e.g., two components with the same name)
    # Currently Pyyaml fail to detect that error: http://pyyaml.org/ticket/128 (contains patch) 
    
    return data

# the classes that we provide in addition to the device drivers 
# Nice name => actual class name
internal_classes = {"Microscope": "model.Microscope", 
                    "CombinedActuator": "model.CombinedActuator",
                    }
def get_class(name):
    """
    name (str): class name given as "package.module.class" or "module.class" or
      one of the internal classes name 
    returns the class object
    Raises:
         SemanticError in case an error is detected.
         ParseError in case names are not possible module names
    """
    if name in internal_classes:
        module_name, class_name = internal_classes[name].rsplit(".", 1)
    else:
        # It comes from the user, so check carefully there is no strange characters
        if not re.match("(\w|\.)+\.(\w)+\Z", name):
            raise ParseError("Syntax error in microscope instantiation "
                "file: class name '%s' is malformed." % name)
    
        names = name.rsplit(".", 1)
        if len(names) < 2:
            raise ParseError("Syntax error in microscope instantiation file: "
                "class name '%s' is not in the form 'module.method'." % name)
        module_name = "driver." + names[0] # always look in drivers directory
        class_name = names[1]
        
    try:
        mod = __import__(module_name, fromlist=[class_name]) 
    except ImportError, exc:
        # FIXME: once we have all the device drivers written, we can uncomment this
        raise SemanticError("Error in microscope instantiation file: "
            "no module '%s' exists (class '%s')." % (module_name, class_name))
#        return None # DEBUG
    
    try:
        the_class = getattr(mod, class_name)
    except AttributeError, exc:
        raise SemanticError("Error in microscope instantiation "
            "file: module '%s' has no class '%s'." % (module_name, class_name))
    
    return the_class 

def make_args(name, attr, inst_comps):
    """
    Create init arguments for a component instance and its children
    name (str): name that will be given to the component instance
    attr (dict (str -> value)): attributes of the component
    inst_comps (dict (str -> dict)): all the components of the instantiation model
    returns (dict (str -> value)): init argument for the component instance
    """
    # it's not an error if there is not init attribute, just not specific arguments
    if "init" in attr:
        init = dict(attr["init"]) # copy
    else:
        init = {}
    
    # it's an error to specify "name" and "role" in the init
    if "name" in init:
        raise SemanticError("Error in microscope instantiation "
            "file: component '%s' should not have a 'name' entry in the init." % name)
    init["name"] = name
    if "role" in init:
        raise SemanticError("Error in microscope instantiation "
            "file: component '%s' should not have a 'role' entry in the init." % name)
    if not "role" in attr:
        raise SemanticError("Error in microscope instantiation "
            "file: component '%s' has no role specified." % name)
    init["role"] = attr["role"]
    
    # create recursively the children
    if "children" in init:
        raise SemanticError("Error in microscope instantiation "
            "file: component '%s' should not have a 'children' entry in the init." % name)
    if "children" in attr:
        init["children"] = {}
        children_names = attr["children"]
        for internal_name, child_name in children_names.items():
            child_attr = inst_comps[child_name]
            init["children"][internal_name] = make_args(child_name, child_attr, inst_comps)
    
    return init
        
    
def instantiate_comp(name, attr, inst_comps, dry_run=False):
    """
    Instantiate a component
    name (str): name that will be given to the component instance
    attr (dict (str -> value)): attributes of the component
    inst_comps (dict (str -> dict)): all the components in the instantiation model
    returns (HwComponent): an instance of the component
    Raises:
        SemanticError in case an error in the model is detected. 
    """
    if not "class" in attr:
        raise SemanticError("Error in microscope instantiation "
            "file: component %s has no class specified." % name)
    class_name = attr["class"]
    class_comp = get_class(class_name)
    
    # create the arguments:
    # name (str)
    # role (str)
    # children (dict str -> dict): same thing for each child as a dict of internal name -> init arguments
    # anything else is passed as is
    args = make_args(name, attr, inst_comps)
   
    if dry_run:
        # mock classes for everything... but internal classes (because they are safe)
        if class_name in internal_classes:
            comp = class_comp(**args)
        else:
            comp = model.MockComponent(**args)
    else:
        comp = class_comp(**args)
    return comp
    
def get_component_by_name(comps, name):
    """
    Find a component by its name
    comps (set of HwComponent): all the components
    name (str): name of the component
    return HwComponent
    Raises:
         LookupError: if no component is found
    """
    for comp in comps:
        if comp.name == name:
            return comp
    raise LookupError("No component named '%s' found" % name)
    
def instantiate_model(inst_model, dry_run=False):
    """
    Generates the real microscope model from the microscope instantiation model
    inst_model (dict str -> dict): python representation of the yaml instantiation file
    dry_run (bool): if True, it will check the semantic and try to instantiate the 
      model without actually any driver contacting the hardware.
    returns 2-tuple (set (HwComponents), Microscope): the set of all the 
      HwComponents in the model, and specifically the Microscope component
      
    Raises:
        SemanticError in case an error in the model is detected. Note that
        (obviously) not every error can be detected.
        LookupError 
        ParseError
        Exception (dependent on the driver): in case initialisation of a driver fails
    """
    comps = set()
    
    # mark the children by adding a "parent" attribute
    for name, attr in inst_model.items():
        if "children" in attr:
            for child_name in attr["children"].values():
                # detect direct loop
                if child_name == name:
                    raise SemanticError("Error in "
                            "microscope instantiation file: component %s "
                            "has itself as children." % name)
                # detect child with multiple parents
                if "parent" in inst_model[child_name]:
                    raise SemanticError("Error in "
                            "microscope instantiation file: component %s "
                            "is child of both %s and %s." 
                            % (child_name, name, inst_model[child_name]["parent"]))
                inst_model[child_name]["parent"] = name
    
    # for each component which is not child
    # add it to the list of comps
    # if it has children, add the children to the list 
    for name, attr in inst_model.items():
        if "parent" in attr: # children are created by their parents
            continue
        comp = instantiate_comp(name, attr, inst_model, dry_run)
        comps.add(comp)
        comps |= getattr(comp, "children", set([]))
        
    # look for the microscope component (check there is only one)
    microscopes = [m for m in comps if isinstance(m, model.Microscope)]
    if len(microscopes) == 1:
        mic = microscopes[0]
    elif len(microscopes) > 1:
        raise SemanticError("Error in microscope instantiation file: "
                "there are several Microscopes (%s)." % 
                ", ".join([m.name for m in microscopes]))
    else:
        raise SemanticError("Error in microscope instantiation "
                "file: no Microscope component found.")
    
    # Connect all the sub-components of microscope: detectors, emitters, actuators
    detector_names = inst_model[mic.name].get("detectors", []) # none is weird but ok
    detectors = [get_component_by_name(comps, name) for name in detector_names]
    mic.detectors = set(detectors)
    if not detectors:
        logging.warning("Microscope contains no detectors.")
    
    emitter_names = inst_model[mic.name].get("emitters", []) # none is weird but ok
    emitters = [get_component_by_name(comps, name) for name in emitter_names]
    mic.emitters = set(emitters)
    if not emitters:
        logging.warning("Microscope contains no emitters.")
    
    actuator_names = inst_model[mic.name].get("actuators", [])
    actuators = [get_component_by_name(comps, name) for name in actuator_names]
    mic.actuators = set(actuators)
    
    # the only components which are not either Microscope or referenced by it 
    # should be parents
    left_over = comps - (set([mic]) | mic.detectors | mic.emitters | mic.actuators)
    for c in left_over:
        # TODO CombinedActuator as well are ok?
        if not getattr(c, "children", set()):
            logging.warning("Component '%s' is never used.", c.name)
    
    # for each component, set the affect
    for name, attr in inst_model.items():
        if "affects" in attr:
            comp = get_component_by_name(comps, name)
            for affected_name in attr["affects"]:
                affected = get_component_by_name(comps, affected_name)
                try:
                    comp.affects.add(affected)
                except AttributeError:
                    raise SemanticError("Error in microscope instantiation "
                            "file: Component '%s' does not support 'affects'." % name)

    # for each component set the properties
    for name, attr in inst_model.items():
        if "properties" in attr:
            comp = get_component_by_name(comps, name)
            for prop_name, value in attr["properties"].items():
                try:
                    getattr(comp, prop_name).value = value
                except AttributeError:
                    raise SemanticError("Error in microscope instantiation "
                            "file: Component '%s' has no property '%s'." % (name, prop_name))
               
    return comps, mic