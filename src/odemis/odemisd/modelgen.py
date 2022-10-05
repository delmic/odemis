# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012-2016 Éric Piel, Delmic

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

from collections.abc import Mapping
import itertools
import logging
import os
import re
import yaml

from odemis import model
from odemis.util import mock


# Detect duplicate keys on mappings (e.g., two components with the same name)
# Currently Pyyaml fail to detect that error: http://pyyaml.org/ticket/128 (contains patch)
# We extend the class, to make it behave as we need.
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


class SafeLoader(yaml.SafeLoader):
    def __init__(self, stream):
        self._root = os.path.dirname(stream.name)  # Directory containing the YAML file
        super(SafeLoader, self).__init__(stream)

    def construct_mapping(self, node, deep=False):
        # From BaseConstructor
        if not isinstance(node, MappingNode):
            raise ConstructorError(None, None,
                    "expected a mapping node, but found %s" % node.id,
                    node.start_mark)
        # From SafeContrusctor
        self.flatten_mapping(node)
        # From BaseConstructor
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise ConstructorError("while constructing a mapping", node.start_mark,
                        "found unacceptable key (%s)" % exc, key_node.start_mark)
            value = self.construct_object(value_node, deep=deep)
            if key in mapping:
                # TODO: Raise a ConstructorError (as defined in YAML), once all
                # offending files are fixed. Or do a deep merge instead?
                logging.warning("Mapping already has key (%s) defined, will override it. "
                                "Semantic will soon change, do not rely on this behaviour. "
                                "Only define the key once.",
                                key)
#                 raise ConstructorError("while constructing a mapping", node.start_mark,
#                                        "key (%s) already defined" % (key,),
#                                        key_node.start_mark)
            mapping[key] = value
        return mapping

    def include(self, node):
        """
        Method for including values defined in a yaml file into another yaml file. This can be used to add values to
        a specified key in a dict or outside a dict (e.g. to define the content of a component definition).

        :param node (yaml.nodes.ScalarNode): Contains both the key which indicated to including another file, and the
        path to the file.
        :return: Data which from external file which needs to be included.
        """
        try:
            filename = os.path.join(self._root, self.construct_scalar(node))  # For supporting relative paths
            with open(filename, 'r') as f:
                logging.info("Loading file '%s' via the !include key", filename)
                return yaml.load(f, SafeLoader)

        except FileNotFoundError as error:
            # Informing the user in case the user forgot a comma and maybe received an unclear error as result
            if any(c in filename for c in ("!", "\n", "\\", "/", " ", ":", "<")):
                logging.error("File not found, probably because an invalid character is used or a comma is forgotten "
                              "when using include in a dict.")
            raise FileNotFoundError(error)

    def construct_yaml_map(self, node):
        """
        Method overwriting the original "construct_yaml_map" which adds the functionality to extend/update a dict in a
        yaml file with an external yaml file via the use of the key "!extend"
        For double defined keys the values contained in the last "!extend" key will be used.
        :param node (Mapping node):
        """
        data = {}
        yield data

        # Check all nodes for the "!extend" key
        for idx, (key_node, value_node) in enumerate(node.value):
            if key_node.tag == "!extend":
                filename = os.path.join(self._root, key_node.value)  # For supporting relative paths
                logging.info("Loading file '%s' via the !extend key", filename)
                try:
                    with open(filename, 'r') as f:
                        try:
                            node_new = SafeLoader(f).get_single_node()  # Get the data from the external file.
                        except yaml.parser.ParserError as error:
                            raise ParseError("Parsing of file '%s' using the '!include' key failed with the error:\n%s"
                                             % (key_node.value, error))
                except FileNotFoundError as error:
                    if any(c in filename for c in ("!", "\n", "\\", "/", " ", ":", "<")):
                        logging.error("File not found, probably because an invalid character is used"
                                      "or a comma is forgotten when using the '!include' key in a dict.")

                    raise FileNotFoundError(error)

                del node.value[idx]  # Delete the old ScalarNode which defined the reference to the external file.
                for scal_node in node_new.value:  # Add the respective ScalarNodes to the main MappingNode.
                    node.value.append(scal_node)  # Order of the output dict is in arbitrary order.

        try:
            value = self.construct_mapping(node)
        except yaml.parser.ParserError as error:
            try:  # A try/catch part because accessing the node structure during an parsing error might fail.
                for (key_node, value_node) in node.value:
                    if key_node.tag == "!include" or value_node.tag == "!include":
                        filename = value_node.value if value_node.tag == "!include" else key_node.value
                        error = ParseError("Parsing of file '%s' using the '!include' key failed with the error:\n%s"
                                           % (filename, error))
                        break
                    if key_node.tag == "!extend" or value_node.tag == "!extend":
                        filename = value_node.value if value_node.tag == "!extend" else key_node.value
                        error = ParseError("Parsing of file '%s' using the '!extend' key failed with the error:\n%s"
                                           % (filename, error))
                        break
            except:
                pass
            logging.error("Parsing of the input file failed with the following traceback: \n%s", error)
            raise error

        data.update(value)

# Add yaml merger features to the SafeLoader class to combine multiple yaml files into one
# Add the key '!include' by adding a constructor which allows to include external yaml files
SafeLoader.add_constructor('!include', SafeLoader.include)
# Overwrite construct_yaml_map with custom mapper which adds support for the "!extend" key to extend a dictionary
SafeLoader.yaml_constructors['tag:yaml.org,2002:map'] = SafeLoader.construct_yaml_map

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
    except ImportError as ex:
        raise SemanticError("Error in microscope file: "
            "fail loading module '%s' (class '%s'): %s." % (module_name, class_name, ex))
#        return None # DEBUG

    try:
        the_class = getattr(mod, class_name)
    except AttributeError:
        raise SemanticError("Error in microscope file: "
                "module '%s' has no class '%s'." % (module_name, class_name))

    return the_class

# A component can reference other components as 'children' or as 'dependencies'.
#  * A dependency is a component which is needed by another one (a functional dependency/requirement)
#  * A child is a component which is created by another one (provided/creation)
# It used to be the case that 'children' and 'dependencies' were provided in the same attribute
# called 'children'. Therefore we need to provide compatibility with both ways of defining references.
# The microscope class is special. It only has a .children attribute and no .dependencies
# attribute because we want to be able to list all the components.


class Instantiator(object):
    """
    manages the instantiation of a whole model
    """

    def __init__(self, inst_file, settings_file=None, container=None, create_sub_containers=False,
                 dry_run=False):
        """
        inst_file (file): opened file that contains the YAML
        settings_file (file or None): opened settings file in YAML format.
          If None, persistent settings will be not be stored.
        container (Container): container in which to instantiate the components
        create_sub_containers (bool): whether the leave components (components which
           have no children created separately) are running in isolated containers
        dry_run (bool): if True, it will check the semantic and try to instantiate the
          model without actually any driver contacting the hardware. It will also
          be stricter, and some issues which are normally just warnings will be
          considered errors.
        """
        self.ast = self._parse_instantiation_model(inst_file)  # AST of the model to instantiate
        self._can_persist = settings_file is not None
        self._persistent_props, self._persistent_mds = self._parse_settings(settings_file)
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
        self._check_lone_component(strict=dry_run)
        self._check_affects(strict=dry_run)
        self._check_duplicate_roles()

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
        elif not isinstance(microscope["children"], Mapping):
            # upgrade from list -> dict
            logging.debug("Upgrading the microscope children list to a dict")
            d = {"c%d" % i: c for i, c in enumerate(microscope["children"])}
            microscope["children"] = d

        for a in ("actuators", "detectors", "emitters"):
            if a in microscope:
                logging.warning("Microscope component contains field '%s', which is "
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
            references = list(comp.get("children", {}).values()) + list(comp.get("dependencies", {}).values())
            for ref in references:
                # detect direct loop
                if ref == name:
                    raise SemanticError("Error in microscope file: "
                                        "component %s is child/dependency of itself." % ref)
                if ref not in self.ast:
                    raise SemanticError("Error in microscope file: "
                                        "component %s references unknown child/dependency %s." %
                                        (name, ref))

                if "parents" not in self.ast[ref].keys():
                    self.ast[ref]["parents"] = []
                self.ast[ref]["parents"].append(name)

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
                parents = [p for p in parents if self.ast[p].get("class") != "Microscope"]

                if len(parents) == 0:
                    raise SemanticError("Error in microscope file: component %s "
                            "has no class specified and is not created by any "
                            "component." % name)
                else:
                    creator = None
                    for p in parents:
                        if name in self.ast[p].get("children", {}).values() and creator:
                            raise SemanticError("Error in microscope file: component %s "
                                    "has to be created by one of its parents %s, but no "
                                    "creator is designated." % (name, tuple(parents)))
                        elif name in self.ast[p].get("children", {}).values() and not creator:
                            creator = p
                    if creator:
                        comp["creator"] = creator
                        logging.debug("Identified %s as creator of %s",
                                      creator, name)

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

    def _check_lone_component(self, strict=False):
        """
        Check that every component is instantiated for eventually being a part
        of the microscope.
        Every component should be either:
         * A child of the microscope
         * Creator of a child of the microscope
        strict (bool): if strict, will raise an error, instead of just printing
          warnings
        """
        # All the components used for the microscope
        comps_used = {self._microscope_name}
        comps_used |= self.get_required_components(self._microscope_name)
        for cname in comps_used.copy():
            # If a used component creates non-used components (as side effect),
            # these created components are not required by the microscope, but used
            attrs = self.ast[cname]
            while "creator" in attrs:
                comps_used |= self.get_children_names(cname)
                cname = attrs["creator"]  # look at the creator too
                attrs = self.ast[cname]
            comps_used |= self.get_children_names(cname)

        for cname, attrs in self.ast.items():
            if cname not in comps_used:
                role = attrs.get("role")
                if role is not None:
                    # Note: some old microscope files had 'none' instead of 'null'
                    # which was turning into the string 'none' instead of None.
                    # TODO: don't warn if the role == 'none' or 'None'?
                    if strict:
                        raise SemanticError("Component '%s' has role %s but it is not marked as child of the microscope" % (cname, role))
                    else:
                        logging.warning("Component '%s' has role %s but it is not used by the microscope",
                                        cname, role)

                creations = self.get_children_names(cname)
                if not creations & comps_used:
                    if len(creations) > 1:
                        logging.info("Component '%s' will create non-used components %s", cname, creations)
                    if strict:
                        raise SemanticError("Component '%s' is defined but not required by the microscope" % (cname,))
                    else:
                        logging.warning("Component '%s' is defined but not required by the microscope", cname)

    def _check_affects(self, strict=False):
        """
        Check that the affects of the components are correct.
        In particular, it checks that all affects points to existing components
        strict (bool): if strict, will raise an error, instead of just printing
          warnings
        """
        for cname, attrs in self.ast.items():
            affects = attrs.get("affects", [])
            for affcname in affects:
                if affcname not in self.ast:
                    if strict:
                        raise SemanticError("Component '%s' affects non-existing component '%s'." % (cname, affcname))
                    else:
                        logging.warning("Component '%s' affects non-existing component '%s'.", cname, affcname)

    def _check_duplicate_roles(self):
        """
        Check that any component with a role is unique
        In particular, it checks that all affects points to existing components
        """
        roles = {}  # Dict role -> comp name
        for cname, attrs in self.ast.items():
            role = attrs.get("role")
            if role is not None:
                if role == "None" or role == "none":
                    logging.warning("Component '%s' has role '%s', which mostly likely should be set to 'null'",
                                    cname, role)
                    continue
                if role in roles:
                    ocname = roles[role]
                    raise SemanticError("Components '%s' and '%s' both have role '%s', but roles should be unique." %
                                        (ocname, cname, role))
                else:
                    roles[role] = cname

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
            # The standard PyYAML loader is dangerous as it can create any python object
            data = yaml.load(inst_file, SafeLoader)
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
            init["_vas"] = list(attr.get("properties", {}).keys())

        # microscope take a special "model" argument which is AST itself
        if class_name == "Microscope":
            init["model"] = self.ast

        # create recursively the children
        if "children" in init:
            raise SemanticError("Error in microscope file: "
                "component '%s' should not have a 'children' entry in the init." % name)
        if "dependencies" in init:
            raise SemanticError("Error in microscope file: "
                "component '%s' should not have a 'dependencies' entry in the init." % name)
        if "children" in attr and not class_name == "Microscope":
            init["children"] = {}
            init["dependencies"] = {}
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
                    init["dependencies"][internal_role] = self._get_component_by_name(child_name)
        if "dependencies" in attr and class_name == "Microscope":
            raise SemanticError("Error in microscope file: "
                                "microscope class '%s' should not have 'dependencies' argument." % name)
        if "dependencies" in attr:
            if init.get("dependencies"):
                # In case some children are dependencies (ie, old-style), but there
                # are also explicit "dependencies" used, warn, as it should be
                # completely converted.
                logging.warning("Mix legacy dependent-component (%s) + dependencies", init["dependencies"].keys())
            else:
                init["dependencies"] = {}
            dep_names = attr["dependencies"]
            for internal_role, dep_name in dep_names.items():
                init["dependencies"][internal_role] = self._get_component_by_name(dep_name)

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
          has no dependencies.
        name (str): name of the component instance
        """
        attr = self.ast[name]

        if attr.get("dependencies", {}):
            return False

        # For backwards compatibility, also check the "dependent children"
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
        attr = self.ast[name]
        if attr.get("class") == "Microscope":
            # The Microscope (root) is special
            return self.root_container

        # If it's a leaf, use its own container
        if self.create_sub_containers and self.is_leaf(name):
            return None

        # If it's not a leaf, it's probably a wrapper (eg, MultiplexActuator),
        # which is simple Python code and so doesn't need to run in a
        # separate container. If clearly it wraps just one other component,
        # use the same container, otherwise, use the root container

        # Get the dependencies
        dependency_names = attr.get("dependencies", {})
        deps_cont = set()
        for child_name in dependency_names.values():
            try:
                cont = self._comp_container[child_name]
            except KeyError:
                logging.warning("Component %s was not created yet, but %s depends on it", child_name, name)
                continue
            deps_cont.add(cont)
        # Ensure backwards compatibility with old-style children (children = dependencies + delegated comps)
        children_names = attr.get("children", {})
        for child_name in children_names.values():
            if "class" in self.ast[child_name]:
                try:
                    cont = self._comp_container[child_name]
                except KeyError:
                    logging.warning("Component %s was not created yet, but %s depends on it", child_name, name)
                    continue
                deps_cont.add(cont)

        if len(deps_cont) == 1:
            return deps_cont.pop()

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
                comp = model.createInContainer(cont, class_comp, args)
            self._comp_container[name] = cont
        except Exception:
            logging.error("Error while instantiating component %s.", name)
            raise

        self.components.add(comp)
        # Add all the children, which were created by delegation, to our list of components.
        self.components |= comp.children.value
        for child in comp.children.value:
            self._comp_container[child.name] = cont

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
        dependencies = attrs.get("dependencies", {}).values()
        for n in dependencies:
            ret.add(n)
            ret |= self.get_required_components(n)

        try:
            ret.add(attrs["power_supplier"])
        except KeyError:
            pass  # no power supplier

        # Support legacy code
        children = attrs.get("children", {}).values()
        for n in children:
            cattrs = self.ast[n]
            if cattrs.get("creator") != name and n not in ret:
                ret.add(n)
                ret |= self.get_required_components(n)

        return ret

    def get_children_names(self, name):
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
                ret |= self.get_children_names(n)

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

    def _update_properties(self, comp_name):
        """
        Set the VA values as defined in the "properties" section of the component. In case
        of persistent properties (specified in "persistent" section of component) try
        to restore the latest value from the settings file. If this fails,
        use the VA definition in the "properties" section instead.

        comp_name (str): name of the component for which to set the VAs
        """
        attrs = self.ast[comp_name]
        comp = self._get_component_by_name(comp_name)

        # Initialize persistent properties from settings file
        props, _ = self.get_persistent(comp_name)
        persistent_props_values = self._persistent_props.get(comp_name, {})
        for prop_name in props:
            # If persistent property wasn't saved in settings file, still check for errors
            try:
                va = getattr(comp, prop_name)
                if not isinstance(va, model.VigilantAttributeBase):
                    raise AttributeError
            except AttributeError:
                raise SemanticError("Error in microscope file: "
                                    "Component '%s' has no property '%s' (defined as persistent)." % (comp_name, prop_name))

            # Load value from settings file if available
            if prop_name in persistent_props_values:
                value = persistent_props_values[prop_name]
                try:
                    va.value = value
                except Exception as exp:
                    logging.warning("Error in settings file: "
                                     "%s.%s = '%s' failed due to '%s'" %
                                     (comp_name, prop_name, value, exp))
                    del persistent_props_values[prop_name]

        if "properties" in attrs:
            for prop_name, value in attrs["properties"].items():
                if prop_name in persistent_props_values:
                    continue  # Already known, from persistent settings
                try:
                    va = getattr(comp, prop_name)
                except AttributeError:
                    raise SemanticError("Error in microscope file: "
                            "Component '%s' has no property '%s'." % (comp_name, prop_name))
                if not isinstance(va, model.VigilantAttributeBase):
                    raise SemanticError("Error in microscope file: "
                            "Component '%s' has no property (VA) '%s'." % (comp_name, prop_name))
                try:
                    va.value = value
                except Exception as exp:
                    raise ValueError("Error in microscope file: "
                                     "%s.%s = '%s' failed due to '%s'" %
                                     (comp_name, prop_name, value, exp))

    def _update_metadata(self, comp_name):
        """
        Update the metadata as defined in the "metadata" section of the component. In case
        of persistent metadata (specified in "persistent" section of component) try
        to restore the latest value from the settings file.

        comp_name (str): name of the component for which to set the metadata
        """
        attrs = self.ast[comp_name]
        comp = self._get_component_by_name(comp_name)
        compmd = {}

        # Initialize persistent metadata from settings file
        _, mds = self.get_persistent(comp_name)
        persistent_mds_values = self._persistent_mds.get(comp_name, {})
        for md_name in mds:
            # Also check persistent metadata that are specified in the model file, but were not saved
            # in the settings file. If something goes wrong, it should fail now rather than at the
            # end when we're trying to write the metadata to the settings file.
            try:
                # To indicate the metadata name:
                # UPPER_CASE_NAME -> use model.MD_UPPER_CASE_NAME
                # We could also accept the actual MD string, but it'd get more
                # complicated and not really help anyone.
                fullname = "MD_" + md_name
                md = getattr(model, fullname)
            except AttributeError:
                raise SemanticError("Error in microscope file: "
                      "Component '%s' has unknown metadata '%s'." % (comp_name, md_name))

            if md_name in persistent_mds_values:
                compmd[md] = persistent_mds_values[md_name]

        # Initialize other metadata from model file
        if "metadata" in attrs:
            for md_name, value in attrs["metadata"].items():
                try:
                    fullname = "MD_" + md_name
                    md = getattr(model, fullname)
                except AttributeError:
                    raise SemanticError("Error in microscope file: "
                        "Component '%s' has unknown metadata '%s'." % (comp_name, md_name))

                if md in compmd:
                    continue  # Already known, from persistent settings
                compmd[md] = value

        # Only update if metadata not empty. Updating a component with empty metadata should not
        # have any effect, but it's safer not to update it when it's not necessary.
        if compmd:
            comp.updateMetadata(compmd)

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
        Generate the component (and its children)
        All the dependencies that are created by separate instantiation must already
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
            deps = list(attrs.get("dependencies", {}).values())
            # support legacy code
            deps += [c for c in attrs.get("children", {}).values() if self.ast[c].get("creator") != n]
            for name in deps:
                if name not in instantiated:
                    logging.debug("Component %s is not instantiable yet", n)
                    break
            else:
                comps.add(n)

        return comps

    def read_yaml(self, f):
        """
        Read content of YAML file. This is a wrapper for yaml.safe_load that returns an
        empty dictionary in case a YAMLError is raised or the file is empty.
        f (File or None): opened YAML file
        return (dict): dictionary with file contents or empty dictionary. The format is:
          comp -> ('metadata'|'properties' -> (key -> value)) 
        """
        if f is None:
            return {}

        # Make sure we start from the beginning, useful if the function
        # is called several times in a row.
        f.seek(0)
        try:
            data = yaml.safe_load(f)
            if not isinstance(data, dict):
                logging.warning("Settings file reset.")
                data = {}
        except yaml.YAMLError as ex:
            logging.warning("Settings file reset after loading failed with Exception %s." % ex)
            data = {}
        return data

    def _parse_settings(self, settings_file):
        """
        Parse settings file and return persistent properties and metadata in useful format.
        settings_file (File or None): opened settings file. If the file is empty or corrupted, two empty
          dict will be returned.
        return (list of 2 nested dicts): persistent properties and persistent metadata for
          each component in the settings file.
          props[comp][prop_name] --> value, mds[comp][md_name] --> value
        """
        data = self.read_yaml(settings_file)
        # Rearrange data from data[comp][type][prop_name] --> type[comp][prop_name],
        # type is 'properties' or 'metadata'
        mds = {}
        props = {}
        for comp in data:
            mds[comp] = data[comp].get('metadata', {})
            props[comp] = data[comp].get('properties', {})
            if not isinstance(mds[comp], Mapping):
                raise ValueError("Persistent metadata for component %s is not a mapping." % comp)
            if not isinstance(props[comp], Mapping):
                raise ValueError("Persistent properties for component %s is not a mapping." % comp)
        return props, mds

    def get_persistent(self, comp_name):
        """
        List all persistent properties and metadata as specified in the model file.
        comp_name (str): name of the component
        return (2 lists of str): VA names, metadata keys
        """
        attrs = self.ast[comp_name]
        persistent = attrs.get("persistent", {})
        prop_names = persistent.get("properties", [])
        md_names = persistent.get("metadata", [])

        if not self._can_persist and (prop_names or md_names):
            logging.warning("Component %s has persistent settings, but no persistent file available",
                            comp_name)

        return prop_names, md_names

