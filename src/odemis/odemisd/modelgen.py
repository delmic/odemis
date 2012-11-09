# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# various functions to instantiate a model from a yaml description

from odemis import model
import itertools
import logging
import re
import yaml
#TODO use JSON instead of YAML (pro: json parser is part of python, and it should
# be possible to detect duplicate names with object_pairs_hook, 
# con: every string must be quoted)
# see: json.JSONDecoder(object_pairs_hook=(lambda x: print(x))).decode('{"bar":1, "bar": 2}')

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
internal_classes = {"Microscope": "odemis.model.Microscope", 
                    "CombinedActuator": "odemis.model.CombinedActuator",
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
        module_name = "odemis.driver." + names[0] # always look in drivers directory
        class_name = names[1]
        
    try:
        mod = __import__(module_name, fromlist=[class_name]) 
    except ImportError:
        raise SemanticError("Error in microscope instantiation file: "
            "no module '%s' exists (class '%s')." % (module_name, class_name))
#        return None # DEBUG
    
    try:
        the_class = getattr(mod, class_name)
    except AttributeError:
        raise SemanticError("Error in microscope instantiation "
            "file: module '%s' has no class '%s'." % (module_name, class_name))
    
    return the_class 

class Instantiator(object):
    """
    manages the instantiation of a whole model
    """
    def __init__(self, model_ast, container=None, create_sub_containers=False,
                      dry_run=False):
        """
        model_ast (dict str -> dict): python representation of the yaml instantiation file
        container (Container): container in which to instantiate the components
        create_sub_containers (bool): whether the leave components (components which
           have no children created separately) are running in isolated containers
        dry_run (bool): if True, it will check the semantic and try to instantiate the 
          model without actually any driver contacting the hardware.
        """
        self.ast = model_ast # AST of the model to instantiate
        self.root_container = container # the container for non-leaf components
        
        self.microscope = None # the root of the model (Microscope)
        self.components = set() # all the components created
        self.sub_containers = set() # all the sub-containers created for the components
        self.create_sub_containers = create_sub_containers # flag for creating sub-containers
        self.dry_run = dry_run # flag for instantiating mock version of the components
        

    def make_args(self, name):
        """
        Create init arguments for a component instance and its children
        name (str): name that will be given to the component instance
        returns (dict (str -> value)): init argument for the component instance
        """
        attr = self.ast[name]
        # it's not an error if there is not init attribute, just not specific arguments
        init = dict(attr.get("init", {})) # copy
        
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
                child_attr = self.ast[child_name]
                # Two types of children creation:
                if "class" in child_attr:
                    # the child has a class => we explicitly create/reuse it
                    init["children"][internal_name] = self.get_or_instantiate_comp(child_name)
                else:
                    # the child has no class => it'll be created by the component,
                    # we just pass the init arguments
                    init["children"][internal_name] = self.make_args(child_name)
        
        return init
    
    def is_leaf(self, name):
        """
        says whether a component is a leaf or not. A "leaf" is a component which
        has no children separately instantiated.
        name (str): name of the component instance
        """
        attr = self.ast[name]
        if attr.get("class", "") == "Microscope":
            return False
        
        children_names = attr.get("children", {})
        for child_name in children_names.values():
            child_attr = self.ast[child_name]
            if "class" in child_attr:
                # the child has a class => it will be instantiated separately
                return False
        
        return True
        
    def instantiate_comp(self, name):
        """
        Instantiate a component
        name (str): name that will be given to the component instance
        returns (HwComponent): an instance of the component
        Raises:
            SemanticError in case an error in the model is detected. 
        """
        attr = self.ast[name]
        class_name = attr["class"]
        class_comp = get_class(class_name)
        
        # create the arguments:
        # name (str)
        # role (str)
        # children:
        #  * explicit creation: (dict str -> HwComponent) internal name -> comp
        #  * delegation: (dict str -> dict) internal name -> init arguments
        # anything else is passed as is
        args = self.make_args(name)
    
        if self.dry_run and not class_name == "Microscope":
            # mock class for everything but Microscope (because it is safe)
            args["_realcls"] = class_comp
            class_comp = model.MockComponent
            args["_vas"] = attr.get("properties", {}).keys()
            
        try:
            if self.create_sub_containers and self.is_leaf(name):
                # new container has the same name as the component
                comp = model.createInNewContainer(name, class_comp, args)
                self.sub_containers.add(model.getContainer(name))
            elif self.root_container:
                comp = self.root_container.instantiate(class_comp, args)
            else:
                comp = class_comp(**args)
        except Exception:
            logging.error("Error while instantiating component %s.", name)
            raise
        
        # Add all the children to our list of components. Useful only if child 
        # created by delegation, but can't hurt to add them all.
        self.components |= getattr(comp, "children", set())
        
        return comp
        
    def get_component_by_name(self, name):
        """
        Find a component by its name in the set of instantiated components
        name (str): name of the component
        return HwComponent
        Raises:
             LookupError: if no component is found
        """
        for comp in self.components:
            if comp.name == name:
                return comp
        raise LookupError("No component named '%s' found" % name)
    
    def get_or_instantiate_comp(self, name):
        """
        returns a component for the given name, either from the components already
          instantiated, or a new instantiated one if it does not exist. final_comps 
          is also updated. 
        """
        try:
            return self.get_component_by_name(name)
        except LookupError:
            # we need to instantiate it
            attr = self.ast[name]
            if "class" in attr:
                comp = self.instantiate_comp(name)
                self.components.add(comp)
                return comp
            else:
                # created by delegation => we instantiate the parent 
                try:
                    parent = attr["parent"]
                except KeyError:
                    raise SemanticError("Error in microscope instantiation file: "
                                        "component %s has no class specified and "
                                        "is not a child." % name)
                parent_comp = self.instantiate_comp(parent)
                self.components.add(parent_comp)
                # now the child ought to be created
                return self.get_component_by_name(name)
    
    @staticmethod
    def get_children(root):
        """
        Return the set of components which are referenced from the given component 
         (children, emitters, detectors, actuators...)
        root (HwComponent): the component to start from
        returns (set of HwComponents)
        """
        ret = set([root])
        for child in getattr(root, "children", set()):
            ret |= Instantiator.get_children(child)
        if isinstance(root, model.Microscope):
            for child in (root.detectors | root.emitters | root.actuators):
                ret |= Instantiator.get_children(child)
        
        return ret
    
    def update_microscope(self):
        assert(not isinstance(self.microscope, model.ComponentProxy))
        # Connect all the sub-components of microscope: detectors, emitters, actuators
        detector_names = self.ast[self.microscope.name].get("detectors", []) # none is weird but ok
        detectors = [self.get_component_by_name(name) for name in detector_names]
        self.microscope._detectors = set(detectors)
        if not detectors:
            logging.warning("Microscope contains no detectors.")
        
        emitter_names = self.ast[self.microscope.name].get("emitters", []) # none is weird but ok
        emitters = [self.get_component_by_name(name) for name in emitter_names]
        self.microscope._emitters = set(emitters)
        if not emitters:
            logging.warning("Microscope contains no emitters.")
        
        actuator_names = self.ast[self.microscope.name].get("actuators", [])
        actuators = [self.get_component_by_name(name) for name in actuator_names]
        self.microscope._actuators = set(actuators)
        
    def instantiate_model(self):
        """
        Generates the real microscope model from the microscope instantiation model

        Raises:
            SemanticError: an error in the model is detected. Note that
            (obviously) not every error can be detected.
            LookupError 
            ParseError
            Exception (dependent on the driver): in case initialisation of a driver fails
        """
        # mark the children by adding a "parent" attribute
        for name, attr in self.ast.items():
            children_names = attr.get("children", {}) # dict internal name -> name
            for child_name in children_names.values():
                # detect direct loop
                if child_name == name:
                    raise SemanticError("Error in "
                            "microscope instantiation file: component %s "
                            "is child of itself." % name)
                # detect child with multiple parents
                if ("parent" in self.ast[child_name] and
                    self.ast[child_name]["parent"] != name):
                    logging.warning("component %s is child of both %s and %s.", 
                            child_name, name, self.ast[child_name]["parent"])
#                    raise SemanticError("Error in "
#                            "microscope instantiation file: component %s "
#                            "is child of both %s and %s." 
#                            % (child_name, name, self.ast[child_name]["parent"]))
                self.ast[child_name]["parent"] = name
        
        # try to get every component, at the end, we have all of them 
        for name in self.ast:
            self.get_or_instantiate_comp(name)
        
        # look for the microscope component (check there is only one)
        microscopes = [m for m in self.components if isinstance(m, model.Microscope)]
        if len(microscopes) == 1:
            self.microscope = microscopes[0]
        elif len(microscopes) > 1:
            raise SemanticError("Error in microscope instantiation file: "
                    "there are several Microscopes (%s)." % 
                    ", ".join([m.name for m in microscopes]))
        else:
            raise SemanticError("Error in microscope instantiation "
                    "file: no Microscope component found.")
        
        self.update_microscope()
        
        # Some validation:
        # The only components which are not either Microscope or referenced by it 
        # should be parents
        left_over = self.components - self.get_children(self.microscope)
        for c in left_over:
            if not hasattr(c, "children"):
                logging.warning("Component '%s' is never used.", c.name)
        
        # for each component, set the affect
        for name, attr in self.ast.items():
            affected_names = attr.get("affects", [])
            comp = self.get_component_by_name(name)
            affected = [self.get_component_by_name(n) for n in affected_names]
            try:
                comp._set_affects(affected)
            except AttributeError:
                raise SemanticError("Error in microscope instantiation "
                        "file: Component '%s' does not support 'affects'." % name)

        # for each component set the properties
        for name, attr in self.ast.items():
            if "properties" in attr:
                comp = self.get_component_by_name(name)
                for prop_name, value in attr["properties"].items():
                    try:
                        getattr(comp, prop_name).value = value
                    except AttributeError:
                        raise SemanticError("Error in microscope instantiation "
                                "file: Component '%s' has no property '%s'." % (name, prop_name))
    
def instantiate_model(inst_model, container=None, create_sub_containers=False,
                      dry_run=False):
    """
    Generates the real microscope model from the microscope instantiation model
    inst_model (dict str -> dict): python representation of the yaml instantiation file
    container (Container): container in which to instantiate the components
    create_sub_containers (bool): whether the leave components (components which
       have no children created separately) are running in isolated containers
    dry_run (bool): if True, it will check the semantic and try to instantiate the 
      model without actually any driver contacting the hardware.
    returns 3-tuple (Microscope, set (HwComponents), set(Containers)): 
        * the Microscope component
        * the set of all the HwComponents in the model (or proxy to them)
        * the sub_containers created (if create_sub_containers is True)
      
    Raises:
        SemanticError: an error in the model is detected. Note that
        (obviously) not every error can be detected.
        LookupError 
        ParseError
        Exception (dependent on the driver): in case initialisation of a driver fails
    """
    instantiator = Instantiator(inst_model, container, create_sub_containers, dry_run)
    try:
        instantiator.instantiate_model()
    except:
        logging.exception("Failed to instantiate the model")
        # clean up by stopping everything which we had started
        for comp in instantiator.components:
            try:
                comp.terminate()
            except:
                pass
        for container in instantiator.sub_containers:
            try:
                container.terminate()
            except:
                pass
        raise
    
    return instantiator.microscope, instantiator.components, instantiator.sub_containers 

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
