# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
# various functions to instantiate a model from a yaml description
# There is a standard JSON parser, but JSON doesn't allow comments (and wants
# {} around the whole file and "" around each string). In addition, the standard
# parser doesn't report where the error is situated in the file.

from __future__ import division
import collections
import itertools
import logging
from odemis import model
from odemis.util import mock
import re
import yaml


class ParseError(Exception):
    pass


class SemanticError(Exception):
    pass


# the classes that we provide in addition to the device drivers
# Nice name => actual class name
internal_classes = {"Microscope": "odemis.model.Microscope",
                    # The following is deprecated, as it can now be directly used
                    # as "actuator.MultiplexActuator"
                    "CombinedActuator": "odemis.driver.actuator.MultiplexActuator",
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
            raise ParseError("Syntax error in microscope file: "
                             "class name '%s' is malformed." % name)

        names = name.rsplit(".", 1)
        if len(names) < 2:
            raise ParseError("Syntax error in microscope file: "
                "class name '%s' is not in the form 'module.method'." % name)
        module_name = "odemis.driver." + names[0] # always look in drivers directory
        class_name = names[1]

    try:
        mod = __import__(module_name, fromlist=[class_name])
    except ImportError:
        raise SemanticError("Error in microscope file: "
            "no module '%s' exists (class '%s')." % (module_name, class_name))
#        return None # DEBUG

    try:
        the_class = getattr(mod, class_name)
    except AttributeError:
        raise SemanticError("Error in microscope file: "
                "module '%s' has no class '%s'." % (module_name, class_name))

    return the_class


# TODO: one big flaw of this model is that there is confusion between two types
# of "children":
#  * A component which is needed by another one (a functional dependency/requirement)
#    => could be in a 'requires' attribute
#  * A component which is created by another one (provided/creation)
#    => could be in a 'provides' attribute

class Instantiator(object):
    """
    manages the instantiation of a whole model
    """
    def __init__(self, inst_file, container=None, create_sub_containers=False,
                 dry_run=False):
        """
        inst_file (file): opened file that contains the yaml
        container (Container): container in which to instantiate the components
        create_sub_containers (bool): whether the leave components (components which
           have no children created separately) are running in isolated containers
        dry_run (bool): if True, it will check the semantic and try to instantiate the
          model without actually any driver contacting the hardware.
        """
        self.ast = self._parse_instantiation_model(inst_file) # AST of the model to instantiate
        self.root_container = container # the container for non-leaf components

        self.microscope = None # the root of the model (Microscope component)
        self._microscope_name = None  # the name of the microscope
        self._microscope_ast = None # the definition of the Microscope
        self.components = set() # all the components created
        self.sub_containers = {}  # container's name -> container: all the sub-containers created for the components
        self._comp_container = {}  # comp name -> container: the container that runs the given component
        self.create_sub_containers = create_sub_containers # flag for creating sub-containers
        self.dry_run = dry_run # flag for instantiating mock version of the components

        self._preparate_microscope()

        # update/fill up the model with implicit information
        self._fill_creator()

        # Sanity checks
        self._check_lone_component()
        self._check_affects()

        # TODO: if the microscope has a known role, check it has the minimum
        # required sub-components (with the right roles) and otherwise raise
        # SemanticError

        # TODO: check here that each class is loadable.

        # TODO: check there is no cyclic dependencies on the parents/children

    def _preparate_microscope(self):
        """
        Find the microscope definition and do some updates on the definition if
        needed. In particular, Microscope used to be special with 3 types of
        child. In case the definition has not been updated, we do it here.
        """
        # look for the microscope def
        microscopes = [(n, a) for n, a in self.ast.items() if a.get("class") == "Microscope"]
        if len(microscopes) == 1:
            cname, microscope = microscopes[0]
        elif len(microscopes) > 1:
            raise SemanticError("Error in microscope file: "
                                "there are several Microscopes (%s)." %
                                ", ".join(n for n, a in microscopes))
        else:
            raise SemanticError("Error in microscope file: "
                                "no Microscope component found.")

        self._microscope_name = cname
        self._microscope_ast = microscope

        if "children" not in microscope:
            microscope["children"] = {}
        elif not isinstance(microscope["children"], collections.Mapping):
            # upgrade from list -> dict
            logging.debug("Upgrading the microscope children list to a dict")
            d = dict(("c%d" % i, c) for i, c in enumerate(microscope["children"]))
            microscope["children"] = d

        for a in ("actuators", "detectors", "emitters"):
            if a in microscope:
                logging.info("Microscope component contains field '%s', which is "
                             "deprecated and can be merged in field 'children'.",
                             a)
                for i, name in enumerate(microscope[a]):
                    role = "%s%d" % (a[:-1], i)
                    microscope["children"][role] = name
                del microscope[a]

    def _fill_creator(self):
        """
        Add the "parents" field (= reverse of children) and update the creator
         field for the components that don't have it explicitly set.
        """
        # update the children by adding a "parents" attribute
        for name, comp in self.ast.items():
            children_names = comp.get("children", {}) # dict internal name -> name
            for child_name in children_names.values():
                # detect direct loop
                if child_name == name:
                    raise SemanticError("Error in microscope file: "
                                        "component %s is child of itself." % name)

                if "parents" not in self.ast[child_name].keys():
                    self.ast[child_name]["parents"] = []
                self.ast[child_name]["parents"].append(name)

        # For each component which is created by delegation (= no class):
        # * if no creator specified, use its parent (and error if multiple parents)
        # * if creator specified, check it's one of the parents
        for name, comp in self.ast.items():
            if "class" in comp:
                continue
            parents = comp["parents"]
            if "creator" in comp:
                creator_name = comp["creator"]
                if creator_name not in parents:
                    raise SemanticError("Error in microscope file: component %s "
                            "is creator of component %s but doesn't have it as a child."
                            % (creator_name, name))
            else:
                # If one parent is Microscope, it's dropped for the "creator"
                # guess because Microscope is known to create no component.
                if len(parents) > 1:
                    parents = [p for p in parents if self.ast[p].get("class") != "Microscope"]

                if len(parents) == 0:
                    raise SemanticError("Error in microscope file: component %s "
                            "has no class specified and is not created by any "
                            "component." % name)
                elif len(parents) > 1:
                    raise SemanticError("Error in microscope file: component %s "
                            "has to be created by one of its parents %s, but no "
                            "creator is designated." % (name, tuple(parents)))
                else:
                    comp["creator"] = parents[0]
                    logging.debug("Identified %s as creator of %s",
                                  parents[0], name)

    def _check_cyclic(self):

        # TODO
        # in theory, it's done, but if there is a dependency loop some components
        # will never be instantiable
        instantiated = set(c.name for c in self._instantiator.components)
        left = set(self._instantiator.ast.keys()) - instantiated
        if left:
            raise SemanticError("Some components could not be instantiated due "
                                "to cyclic dependency: %s" %
                                (", ".join(left)))

    def _check_lone_component(self):
        """
        Check that every component is instantiate for eventually being a part
        of the microscope.
        Every component should be either:
         * A child of the microscope
         * Creator of a child of the microscope
        """
        # All the components used for the microscope
        comps_used = {self._microscope_name}
        comps_used |= self.get_required_components(self._microscope_name)
        for cname in comps_used.copy():
            # If a used component creates non-used components (as side effect),
            # these created components are not required by the microscope, but used
            attrs = self.ast[cname]
            while "creator" in attrs:
                comps_used |= self.get_delegated_children(cname)
                cname = attrs["creator"]  # look at the creator too
                attrs = self.ast[cname]
            comps_used |= self.get_delegated_children(cname)

        for cname, attrs in self.ast.items():
            if cname not in comps_used:
                role = attrs.get("role")
                if role is not None:
                    # Note: some old microscope files had 'none' instead of 'null'
                    # which was turning into the string 'none' instead of None.
                    # TODO: don't warn if the role == 'none' or 'None'?
                    logging.warning("Component '%s' is not directly required by the microscope but has non-null role '%s'",
                                    cname, role)

                creations = self.get_delegated_children(cname)
                if not creations & comps_used:
                    if len(creations) > 1:
                        logging.info("Component '%s' would create non-used components %s", cname, creations)
                    raise SemanticError("Component '%s' is defined but not required by the microscope" % (cname,))

    def _check_affects(self):
        """
        Check that the affects of the components are correct.
        In particular, it checks that all affects points to existing components
        """
        for cname, attrs in self.ast.items():
            affects = attrs.get("affects", [])
            for affcname in affects:
                if affcname not in self.ast:
                    # TODO: Convert to a SemanticError (in 2017 or later), once
                    # all the currently broken microscope files are fixed
                    logging.warning("Component '%s' affects non-existing component '%s'.", cname, affcname)
                    # raise SemanticError("Component '%s' affects non-existing component '%s'." % (cname, affcname))

    def _parse_instantiation_model(self, inst_file):
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
        except yaml.YAMLError as exc:
            logging.error("Syntax error in microscope file: %s", exc)
            if hasattr(exc, 'problem_mark'):
                mark = exc.problem_mark
                # display the line
                inst_file.seek(0)
                line = list(itertools.islice(inst_file, mark.line, mark.line + 1))[0]
                logging.error("%s", line.rstrip("\n"))
                # display the column
                logging.error(" " * mark.column + "^")
            raise ParseError("Syntax error in microscope file.")

        logging.debug("YAML file read like this: " + str(data))
        # TODO detect duplicate keys (e.g., two components with the same name)
        # Currently Pyyaml fail to detect that error: http://pyyaml.org/ticket/128 (contains patch)

        return data

    def _make_args(self, name):
        """
        Create init arguments for a component instance and its children
        name (str): name that will be given to the component instance
        returns (dict (str -> value)): init argument for the component instance
        """
        attr = self.ast[name]
        # it's not an error if there is not init attribute, just not specific arguments
        init = attr.get("init", {}).copy()

        # it's an error to specify "name" and "role" in the init
        if "name" in init:
            raise SemanticError("Error in microscope file: "
                "component '%s' should not have a 'name' entry in the init." % name)
        init["name"] = name
        if "role" in init:
            raise SemanticError("Error in microscope file: "
                "component '%s' should not have a 'role' entry in the init." % name)
        if "role" not in attr:
            raise SemanticError("Error in microscope file: "
                                "component '%s' has no role specified." % name)
        init["role"] = attr["role"]

        class_name = attr.get("class", None)
        if self.dry_run and not class_name == "Microscope":
            # mock class needs some hints to create the fake VAs
            init["_vas"] = attr.get("properties", {}).keys()

        # microscope take a special "model" argument which is AST itself
        if class_name == "Microscope":
            init["model"] = self.ast

        # create recursively the children
        if "children" in init:
            raise SemanticError("Error in microscope file: "
                "component '%s' should not have a 'children' entry in the init." % name)
        if "children" in attr and not class_name == "Microscope":
            init["children"] = {}
            children_names = attr["children"]
            for internal_role, child_name in children_names.items():
                child_attr = self.ast[child_name]
                # Two types of children creation:
                if "creator" in child_attr and child_attr["creator"] == name:
                    # the child creation is delegated... to this component
                    # => it'll be created by the component, so
                    # we just pass the init arguments
                    init["children"][internal_role] = self._make_args(child_name)
                else:
                    # the child has a class or is created by another component
                    # => we explicitly reuse it
                    init["children"][internal_role] = self._get_component_by_name(child_name)

        # take care of power supplier argument
        if "power_supplier" in init:
            raise SemanticError("Error in microscope file: "
                "component '%s' should not have a 'power_supplier' entry in the init." % name)
        if "power_supplier" in attr:
            psu_name = attr["power_supplier"]
            init["power_supplier"] = self._get_component_by_name(psu_name)

        return init

    def is_leaf(self, name):
        """
        says whether a component is a leaf or not. A "leaf" is a component which
          has no children separately instantiated (ie, only delegated children).
        name (str): name of the component instance
        """
        attr = self.ast[name]

        children_names = attr.get("children", {}).values()
        for child_name in children_names:
            child_attr = self.ast[child_name]
            if "class" in child_attr:
                # the child has a class => it will be instantiated separately
                return False

        return True

    def _get_container(self, name):
        """
        Find the best container to instantiate a component
        name (str): name of the component to instantiate
        return (None or container): None means a new container must be created
        """
        # If it's a leaf, use its own container
        if self.create_sub_containers and self.is_leaf(name):
            return None

        attr = self.ast[name]
        if attr.get("class") == "Microscope":
            # The Microscope (root) is special
            return self.root_container

        # If it's not a leaf, it's probably a wrapper (eg, MultiplexActuator),
        # which is simple Python code and so doesn't need to run in a
        # separate container. If clearly it wraps just one other component,
        # use the same container, otherwise, use the root container

        # Get the instantiated children (ie, dependencies)
        children_names = attr.get("children", {})
        children_cont = set()
        for child_name in children_names.values():
            if "class" in self.ast[child_name]:
                try:
                    cont = self._comp_container[child_name]
                except KeyError:
                    logging.warning("Component %s was not created yet, but %s depends on it", child_name, name)
                    continue
                children_cont.add(cont)

        if len(children_cont) == 1:
            return children_cont.pop()

        # Multiple dependencies -> just use the root container then
        return self.root_container

    def _instantiate_comp(self, name):
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
        args = self._make_args(name)

        logging.debug("Going to instantiate %s (%s) with args %s",
                      name, class_name, args)

        # if the component is connected to a PowerSupplier we first need to turn
        # it on before we instantiate it
        if "power_supplier" in args:
            f = args["power_supplier"].supply({name: True})
            f.result()

        if self.dry_run and not class_name == "Microscope":
            # mock class for everything but Microscope (because it is safe)
            args["_realcls"] = class_comp
            class_comp = mock.MockComponent

        try:
            cont = self._get_container(name)
            if cont is None:
                # new container has the same name as the component
                cont, comp = model.createInNewContainer(name, class_comp, args)
                self.sub_containers[name] = cont
            else:
                logging.debug("Creating %s in container %s", name, cont)
                comp = cont.instantiate(class_comp, args)
            self._comp_container[name] = cont
        except Exception:
            logging.error("Error while instantiating component %s.", name)
            raise

        self.components.add(comp)
        # Add all the children to our list of components. Useful only if child
        # created by delegation, but can't hurt to add them all.
        self.components |= comp.children.value

        return comp

    def _get_component_by_name(self, name):
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

    def get_required_components(self, name):
        """
        Return all the components required (but not created) by the component for
          instantiation
        name (str): name of the component
        return (set of str): the name of the components that will be required when
          instantiating the given component, (not including the component itself)
        """
        ret = set()

        attrs = self.ast[name]
        children = attrs.get("children", {}).values()
        try:
            children.append(attrs["power_supplier"])
        except KeyError:
            pass  # no power supplier

        for n in children:
            cattrs = self.ast[n]
            if cattrs.get("creator") != name and n not in ret:
                ret.add(n)
                ret |= self.get_required_components(n)

        return ret

    def get_delegated_children(self, name):
        """
        Return all the components created by delegation when creating the given
         component (including the given component)
        name (str): name of the component
        return (set of str): the name of the components that will be created when
          instantiating the given component, including the component itself
        """
        ret = {name}
        for n, attrs in self.ast.items():
            if attrs.get("creator") == name:
                # Note: by passing ret, and checking it's not already added,
                # we could handle cyclic creation... but it's better to fail here
                # than trying to instantiate such beast.
                ret |= self.get_delegated_children(n)

        return ret

    @classmethod
    def get_children(cls, root):
        """
        Return the set of components which are referenced from the given component
         (via children)
        root (HwComponent): the component to start from
        returns (set of HwComponents): all the children, including the component
          itself.
        """
        ret = {root}
        for child in root.children.value:
            ret |= cls.get_children(child)

        return ret

    def instantiate_microscope(self):
        """
        Generates the just the microscope component

        Raises:
            SemanticError: an error in the model is detected. Note that
            (obviously) not every error can be detected.
            LookupError
            ParseError
            Exception (dependent on the driver): in case initialisation of a driver fails
        """
        self.microscope = self._instantiate_comp(self._microscope_name)
        return self.microscope

    def _update_properties(self, name):
        """
        Set the VA values as defined in the "properties" section of the component

        name (str): name of the component for which to set the VAs
        """
        attrs = self.ast[name]
        if "properties" in attrs:
            comp = self._get_component_by_name(name)
            for prop_name, value in attrs["properties"].items():
                try:
                    va = getattr(comp, prop_name)
                except AttributeError:
                    raise SemanticError("Error in microscope file: "
                            "Component '%s' has no property '%s'." % (name, prop_name))
                if not isinstance(va, model.VigilantAttributeBase):
                    raise SemanticError("Error in microscope file: "
                            "Component '%s' has no property (VA) '%s'." % (name, prop_name))
                try:
                    va.value = value
                except Exception as exp:
                    raise ValueError("Error in microscope file: "
                                     "%s.%s = '%s' failed due to '%s'" %
                                     (name, prop_name, value, exp))

    def _update_metadata(self, name):
        """
        Update the metadata as defined in the "metadata" section of the component

        name (str): name of the component for which to set the metadata
        """
        attrs = self.ast[name]
        if "metadata" in attrs:
            comp = self._get_component_by_name(name)
            compmd = {}
            for md_name, value in attrs["metadata"].items():
                # To indicate the metadata name:
                # UPPER_CASE_NAME -> use model.MD_UPPER_CASE_NAME
                # We could also accept the actual MD string, but it'd get more
                # complicated and not really help anyone.
                try:
                    fullname = "MD_" + md_name
                    md = getattr(model, fullname)
                except AttributeError:
                    raise SemanticError("Error in microscope file: "
                        "Component '%s' has unknown metadata '%s'." % (name, md_name))

                compmd[md] = value

            comp.updateMetadata(compmd)  # Ought to work

    def _update_affects(self, name):
        """
        Update .affects of the given component, and of all the components which
         affects the given component

        name (str): the (new) component
        """
        comp = self._get_component_by_name(name)
        attrs = self.ast[name]
        # Just set all the components (by name), even if they are not yet active
        comp.affects.value = attrs.get("affects", [])
        logging.debug("Updating affect %s -> %s", name,
                      ", ".join(comp.affects.value))

    def instantiate_component(self, name):
        """
        Generate the component (and its children, if they are created by delegation)
        All the children that are created by separate instantiation must already
        have been created.
        It will take care of updating the .children VA of the microscope if
         needed.

        return (Component): the new component created. Note that more component
         might have been created (by delegation). You can find them by looking
         at the .children VA of the new component.
        Raises:
            LookupError: if the component doesn't exists in the AST or
                  if the children should have been created and are not.
            ValueError: if the component has already been instantiated
            KeyError: if component should be created by delegation
        """
        for c in self.components:
            if c.name == name:
                raise ValueError("Trying to instantiate again component %s" % name)

        comp = self._instantiate_comp(name)

        # Add to the microscope all the new components that should be child
        mchildren = self._microscope_ast["children"].values()
        # we only care about children created by delegation, but all is fine
        newcmps = self.get_children(comp) # that includes comp itself
        for c in newcmps:
            self._update_properties(c.name)
            self._update_metadata(c.name)
            self._update_affects(c.name)
        newchildren = set(c for c in newcmps if c.name in mchildren)
        self.microscope.children.value = self.microscope.children.value | newchildren

        return comp

    def get_instantiables(self, instantiated=None):
        """
        Find the components that are currently not yet instantiated, but
        could be directly (ie, all their children or power_supplier created explicitly are
        already instantiated)
        instantiated (None or set of str): the names of the components already
          instantiated. If it's None, it will use the list of all the components
          ever instantiated.
        return (set of str): names of all the components that are instantiable
        """
        comps = set()
        if instantiated is None:
            instantiated = set(c.name for c in self.components)
        for n, attrs in self.ast.items():
            if n in instantiated: # should not be already instantiated
                continue
            if "class" not in attrs: # created by delegation
                continue
            if "power_supplier" in attrs:
                psuname = attrs["power_supplier"]
                logging.debug("%s has a power_supplier %s", n, psuname)
                if psuname not in instantiated:
                    logging.debug("Component %s is not instantiable yet", n)
                    continue
            for cname in attrs.get("children", {}).values():
                childat = self.ast[cname]
                # the child must be either instantiated or instantiated via delegation
                if cname not in instantiated and not childat.get("creator") == n:
                    logging.debug("Component %s is not instantiable yet", n)
                    break
            else:
                comps.add(n)

        return comps

