#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 12 Jul 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
# This is a basic command line interface to the odemis back-end

from past.builtins import basestring, unicode
from builtins import str
from collections.abc import Iterable, Mapping
import argparse
import codecs
import importlib
import inspect
import logging
import math
import numbers
from odemis import model, dataio, util
import odemis
from odemis.util import units, inspect_getmembers
from odemis.util.conversion import convert_to_object
from odemis.util.driver import BACKEND_RUNNING, \
    BACKEND_DEAD, BACKEND_STOPPED, get_backend_status, BACKEND_STARTING
import sys
import threading


status_to_xtcode = {BACKEND_RUNNING: 0,
                    BACKEND_DEAD: 1,
                    BACKEND_STOPPED: 2,
                    BACKEND_STARTING: 3,
                    }

# Special VigilantAttributes
VAS_COMPS = {"alive", "dependencies"}
VAS_HIDDEN = {"children", "affects"}

# Command line arguments which can have "--" omitted
ACTION_NAMES = ("kill", "check", "list", "list-prop", "set-attr", "update-metadata",
                "move", "position", "stop", "reference", "acquire", "live", "scan",
                "version", "help")


# small object that can be remotely executed for scanning
class Scanner(model.Component):
    def __init__(self, cls, **kwargs):
        assert(inspect.isclass(cls))
        model.Component.__init__(self, "scanner for %s" % cls.__name__, **kwargs)
        self.cls = cls

    def scan(self):
        return self.cls.scan()

def scan(cls=None):
    """
    Scan for connected devices and list them
    cls (str or None): the class name to scan (as written in the microscope file)
    Output like:
    Classname: 'Name of Device' init={arg: value, arg2: value2}
    """
    # FIXME: need to work when /var/run/odemisd is not available:
    # => fail to create the container.
    # only here, to avoid importing everything for other commands
    from odemis import driver
    num = 0
    cls_found = False
    # we scan by using every HwComponent class which has a .scan() method
    for module_name in driver.__all__:
        try:
            module = importlib.import_module("." + module_name, "odemis.driver")
        except ImportError:
            logging.warning("Cannot try module %s, failed to load." % module_name)
        except Exception:
            logging.exception("Failed to load module %s" % module_name)
        for cls_name, clso in inspect_getmembers(module, inspect.isclass):
            if issubclass(clso, model.HwComponent) and hasattr(clso, "scan"):
                if cls:
                    full_name = "%s.%s" % (module_name, cls_name)
                    if cls != full_name:
                        logging.debug("Skipping %s", full_name)
                        continue
                    else:
                        cls_found = True

                logging.info("Scanning for %s.%s components", module_name, cls_name)
                # do it in a separate container so that we don't have to load
                # all drivers in the same process (andor cams don't like it)
                container_name = "scanner%d" % num
                num += 1
                try:
                    cont, scanner = model.createInNewContainer(container_name, Scanner, {"cls": clso})
                    devices = scanner.scan()
                    scanner.terminate()
                    cont.terminate()
                except Exception:
                    logging.exception("Failed to scan %s.%s components", module_name, cls_name)
                else:
                    if not devices:
                        logging.info("No device found")
                    for name, args in devices:
                        print("%s.%s: '%s' init=%r" % (module_name, cls_name, name, args))

    if cls and not cls_found:
        raise ValueError("Failed to find class %s" % cls)


def kill_backend():
    try:
        backend = model.getContainer(model.BACKEND_NAME)
        backend.terminate()
    except Exception:
        raise IOError("Failed to stop the back-end")


def print_component(comp, pretty=True, level=0):
    """
    Pretty print on one line a component
    comp (Component): the component to print
    pretty (bool): if True, display with pretty-printing
    level (int > 0): hierarchy level (for indentation)
    """
    if pretty:
        if level == 0:
            indent = u""
        else:
            indent = u"  " * level + u"↳ "
        role = comp.role
        if role is None:
            str_role = "(no role)"
        else:
            str_role = "role:%s" % (role,)

        print(u"%s%s\t%s" % (indent, comp.name, str_role))
    else:
        pstr = u""
        try:
            pname = comp.parent.name
            if isinstance(pname, basestring):
                pstr = u"\tparent:" + pname
        except AttributeError:
            pass
        print(u"%s\trole:%s%s" % (comp.name, comp.role, pstr))
    # TODO would be nice to display which class is the component
    # TODO:
    # * if emitter, display .shape
    # * if detector, display .shape
    # * if actuator, display .axes


def print_component_graph(graph, pretty=True, level=0):
    """
    Print all the components starting from the root.
    graph (dict {Component -> dict {Component -> dict...}}): parent -> children, recursive
    pretty (bool): if True, display with pretty-printing
    level (int > 0): hierarchy level (for pretty printing)
    """
    for comp, subg in graph.items():
        # first print the root component
        print_component(comp, pretty, level)
        print_component_graph(subg, pretty, level + 1)


def build_graph_children(comps):
    """
    Constructs a graph based on the children hierarchy, so each component is a
    node, and the children are sub-nodes of their parent. Precisely, it builds a
    tree, or several trees if there is more than one root component.
    comps (set of Component): All the components
    return (dict {Component -> dict {Component -> dict...}}): parent -> children, recursive
    """
    # Start from the leaves, which have no children, and merge all the leaves
    # into their parent, once the parent has all its children in the graph.
    # Note: the children must have a single parent, otherwise, it'll not work
    lefts = set(comps)
    graph = {}
    while lefts:
        prev_lefts = lefts.copy()
        for comp in prev_lefts:
            children = set(comp.children.value)
            if not (children - set(graph.keys())):
                graph[comp] = {k: v for k, v in graph.items() if k in children}
                for child in children:
                    del graph[child]
                lefts.remove(comp)

        if lefts == prev_lefts:
            logging.warning("Some components have children not in the graph: %s",
                            ", ".join(c.name for c in lefts))
            # Let's not completely fail: put all the components left-over as
            # roots, and leave their children as-is.
            for comp in lefts:
                graph[comp] = {}
            break

    return graph


def list_components(pretty=True):
    """
    pretty (bool): if True, display with pretty-printing
    """
    # Show the root first, and don't use it for the graph, because its "children"
    # are actually "dependencies", and it'd multiple parents in the graph.
    microscope = model.getMicroscope()
    subcomps = model.getComponents() - {microscope}

    print_component(microscope, pretty)
    if pretty:
        graph = build_graph_children(subcomps)
        print_component_graph(graph, pretty, 1)
    else:
        # The "pretty" code would do the same, but much slower
        for c in subcomps:
            print_component(c, pretty)


def print_axes(name, value, pretty):
    if pretty:
        print(u"\t%s (RO Attribute)" % (name,))
        # show in alphabetical order
        for an in sorted(value.keys()):
            print(u"\t\t%s:\t%s" % (an, value[an]))
    else:
        print(u"%s\ttype:roattr\tvalue:%s" %
              (name, u", ".join(k for k in value.keys())))

def print_roattribute(name, value, pretty):
    if name == "axes":
        return print_axes(name, value, pretty)

    if pretty:
        print(u"\t%s (RO Attribute)\tvalue: %s" % (name, value))
    else:
        print(u"%s\ttype:roattr\tvalue:%s" % (name, value))

non_roattributes_names = ("name", "role", "parent", "affects")
def print_roattributes(component, pretty):
    for name, value in model.getROAttributes(component).items():
        # some are handled specifically
        if name in non_roattributes_names:
            continue
        print_roattribute(name, value, pretty)

def print_data_flow(name, df, pretty):
    if pretty:
        print(u"\t" + name + u" (Data-flow)")
    else:
        print(u"%s\ttype:data-flow" % (name,))

def print_data_flows(component, pretty):
    # find all dataflows
    for name, value in model.getDataFlows(component).items():
        print_data_flow(name, value, pretty)

def print_event(name, evt, pretty):
    if pretty:
        print(u"\t" + name + u" (Event)")
    else:
        print(u"%s\ttype:event" % (name,))

def print_events(component, pretty):
    # find all Events
    for name, value in model.getEvents(component).items():
        print_event(name, value, pretty)


def print_vattribute(component, name, va, pretty):
    """
    Print on one line the information about a VigilantAttribute
    component (Component): the component containing the VigilantAttribute
    name (str): the name of the VigilantAttribute
    va (VigilantAttribute): the VigilantAttribute to display
    pretty (bool): whether to display for the user (True) or for a machine (False)
    """
    if va.unit:
        if pretty:
            unit = u" (unit: %s)" % va.unit
        else:
            unit = u"\tunit:%s" % va.unit
    else:
        unit = u""

    if va.readonly:
        if pretty:
            readonly = u"RO "
        else:
            readonly = u"ro"
    else:
        readonly = u""

    # we cannot discover if it continuous or enumerated, just try and see if it fails
    try:
        varange = va.range
        if pretty:
            str_range = u" (range: %s → %s)" % (varange[0], varange[1])
        else:
            str_range = u"\trange:%s" % str(varange)
    except AttributeError:
        str_range = u""

    try:
        vachoices = va.choices # set or dict
        if pretty:
            if isinstance(va.choices, dict):
                str_choices = u" (choices: %s)" % u", ".join(
                                u"%s: '%s'" % i for i in vachoices.items())
            else:
                str_choices = u" (choices: %s)" % u", ".join([str(c) for c in vachoices])
        else:
            str_choices = u"\tchoices:%s" % str(vachoices)
    except AttributeError:
        str_choices = ""

    if pretty:
        val = va.value
        if name in VAS_COMPS:
            try:
                val = {c.name for c in val}
            except Exception:
                logging.info("Failed to convert %s to component names")
                # Leave the value as-is

        # Convert to nicer unit for user
        if va.unit and va.unit == "rad" and isinstance(val, numbers.Real):
            try:
                val_converted = u" = %s°" % (math.degrees(val),)
            except Exception:
                logging.warning("Failed to convert %s to degrees", name)
                val_converted = u""
        else:
            val_converted = u""

        # For position, it's trickier, as the unit is on .axes
        if (name == "position" and isinstance(va.value, dict) and
            hasattr(component, "axes") and isinstance(component.axes, dict)
           ):
            pos_deg = {}
            for an, pos in va.value.items():
                try:
                    axis_def = component.axes[an]
                except KeyError:
                    logging.warning("axes is missing axis '%s' from .position", an)
                    continue
                if axis_def.unit == "rad":
                    pos_deg[an] = math.degrees(pos)

            if pos_deg:
                val_converted = u"\t{%s}" % (u", ".join(u"%r: %r°" % (k, pos_deg[k]) for k in sorted(pos_deg.keys())),)

        # Display set/dict sorted, so that they always look the same.
        # Especially handy for VAs such as .position, which show axis names.
        if isinstance(val, dict):
            sval = u"{%s}" % (u", ".join(u"%r: %r" % (k, val[k]) for k in sorted(val.keys())),)
        elif isinstance(val, set):
            sval = u"{%s}" % (u", ".join(u"%r" % v for v in sorted(val)),)
        else:
            sval = str(val)
        print(u"\t" + name + u" (%sVigilant Attribute)\t value: %s%s%s%s%s" %
              (readonly, sval, unit, str_range, str_choices, val_converted))
    else:
        print(u"%s\ttype:%sva\tvalue:%s%s%s%s" %
              (name, readonly, str(va.value), unit, str_range, str_choices))


def print_vattributes(component, pretty):
    for name, va in model.getVAs(component).items():
        if name in VAS_HIDDEN:
            continue
        print_vattribute(component, name, va, pretty)


def map_metadata_names():
    """
    Find the name of each known metadata
    return dict str->str: the metadata key string -> the name of the metadata (without the "MD_")
    """
    ret = {}
    for n, v in inspect_getmembers(model, lambda m: isinstance(m, str)):
        if n.startswith("MD_"):
            ret[v] = n[3:]

    return ret


def print_metadata(component, pretty):
    md = component.getMetadata()

    md2name = map_metadata_names()
    if pretty:
        if not md:
            return
        print("\tMetadata:")
        for key, value in md.items():
            name = md2name.get(key, "'%s'" % (key,))
            if isinstance(value, basestring):
                print(u"\t\t%s: '%s'" % (name, value))
            else:
                print(u"\t\t%s: %s" % (name, value))
    else:
        for key, value in md.items():
            name = md2name.get(key, "'%s'" % (key,))
            print(u"%s\ttype:metadata\tvalue:%s" % (name, value))

def print_attributes(component, pretty):
    if pretty:
        print(u"Component '%s':" % component.name)
        print(u"\trole: %s" % component.role)
        print(u"\taffects: " + ", ".join(u"'%s'" % n for n in sorted(component.affects.value)))
    else:
        print(u"name\tvalue:%s" % component.name)
        print(u"role\tvalue:%s" % component.role)
        print(u"affects\tvalue:" + u"\t".join(component.affects.value))
    print_roattributes(component, pretty)
    print_vattributes(component, pretty)
    print_data_flows(component, pretty)
    print_events(component, pretty)
    print_metadata(component, pretty)

def get_component(comp_name):
    """
    return the component with the given name
    comp_name (string): name of the component to find
    raises
        ValueError if the component doesn't exist
        other exception if there is an error while contacting the backend
    """
    logging.debug("Looking for component %s", comp_name)
    try:
        return model.getComponent(name=comp_name)
    except LookupError:
        try:
            comp = model.getComponent(role=comp_name)
            logging.info("Using component %s with role %s", comp.name, comp.role)
            return comp
        except LookupError:
            raise ValueError("No component found with name or role '%s'" % comp_name)

def get_detector(comp_name):
    """
    return the detector component with the given name
    comp_name (string): name of the component to find
    raises
        ValueError if the component doesn't exist or is not a detector
        other exception if there is an error while contacting the backend
    """
    comp = get_component(comp_name)
    # check it's a detector by looking at some of the required attributes
    if (not isinstance(comp.shape, Iterable) or
        not isinstance(comp.data, model.DataFlowBase)):
        raise ValueError("Component %s is not a detector" % comp.name)
    return comp

def list_properties(comp_name, pretty=True):
    """
    print the data-flows and VAs of a component
    comp_name (string): name of the component or "*"
    pretty (bool): if True, display with pretty-printing
    """
    if comp_name == "*":
        for c in model.getComponents():
            print_attributes(c, pretty)
            print("")
    else:
        component = get_component(comp_name)
        print_attributes(component, pretty)

def set_attr(comp_name, attr_val_str):
    """
    set the value of vigilant attribute of the given component.
    attr_val_str (dict str->str): attribute name -> value as a string
    """
    component = get_component(comp_name)

    for attr_name, str_val in attr_val_str.items():
        try:
            attr = getattr(component, attr_name)
        except Exception:
            raise ValueError("Failed to find attribute '%s' on component '%s'" % (attr_name, comp_name))

        if not isinstance(attr, model.VigilantAttributeBase):
            raise ValueError("'%s' is not a vigilant attribute of component %s" % (attr_name, comp_name))

        new_val = convert_to_object(str_val)

        # Special case for floats, due to rounding error, it's very hard to put the
        # exact value if it's an enumerated VA. So just pick the closest one in this
        # case.
        if (isinstance(new_val, float) and
           hasattr(attr, "choices") and
           isinstance(attr.choices, Iterable)):
            orig_val = new_val
            choices = [v for v in attr.choices if isinstance(v, numbers.Number)]
            new_val = util.find_closest(new_val, choices)
            if new_val != orig_val:
                logging.debug("Adjusting value to %s", new_val)

        # Special case for None being referred to as "null" in YAML, but we should
        # also accept "None"
        elif new_val == "None" and not isinstance(attr.value, basestring):
            new_val = None
            logging.debug("Adjusting value to %s (null)", new_val)
        elif isinstance(new_val, list) and isinstance(attr.value, tuple):
            new_val = tuple(new_val)
            logging.debug("Adjusting value from list to tuple: %s", new_val)

        try:
            attr.value = new_val
        except Exception as exc:
            raise IOError("Failed to set %s.%s = '%s': %s" % (comp_name, attr_name, str_val, exc))

def update_metadata(comp_name, key_val_str):
    """
    update the metadata of the given component with the given key/value
    key_val_str (dict str->str): key name -> value as a string
    """
    component = get_component(comp_name)

    md2name = map_metadata_names()
    md = {}
    for key_name, str_val in key_val_str.items():
        # Check that the metadata is a valid one. It's a bit tricky as there is no
        # "official" list. But we look at the ones defined in model.MD_*
        if key_name in md2name:
            key = key_name
        else:
            # fallback to looking for MD_{key_name}
            try:
                key = getattr(model, "MD_%s" % key_name)
            except AttributeError:
                raise ValueError("Metadata key '%s' is unknown" % key_name)

        md[key] = convert_to_object(str_val)

    try:
        component.updateMetadata(md)
    except Exception as exc:
        raise IOError("Failed to update metadata of %s to %s: %s" %
                      (comp_name, md, exc))

def merge_moves(actions):
    """
    actions (list of tuples of 3 values): component, axis, distance/position
    return (dict of str -> (dict of str -> str)): components -> (axis -> distance))
    """
    moves = {}
    for c, a, d in actions:
        if c not in moves:
            moves[c] = {}
        if a in moves[c]:
            raise ValueError("Multiple moves requested for %s.%s" % (c, a))
        moves[c][a] = d

    return moves

MAX_DISTANCE = 0.01 # m


def move(comp_name, moves, check_distance=True, to_radians=False):
    """
    move (relatively) the axis of the given component by the specified amount of µm
    comp_name (str): name of the component
    moves (dict str -> str): axis -> distance (as text, and in µm for distances)
    check_distance (bool): if the axis is in meters, check that the move is not
      too big.
    to_radians (bool): will convert from degrees to radians if the axis is in radians,
      otherwise will fail
    """
    # for safety reason, we use µm instead of meters, as it's harder to type a
    # huge distance
    component = get_component(comp_name)

    act_mv = {} # axis -> value
    for axis_name, str_distance in moves.items():
        try:
            if axis_name not in component.axes:
                raise ValueError("Actuator %s has no axis '%s'" % (comp_name, axis_name))
            ad = component.axes[axis_name]
        except (TypeError, AttributeError):
            raise ValueError("Component %s is not an actuator" % comp_name)

        if ad.unit == "m":
            try:
                # Use convert_to_object() to allow typing negative values with e:
                # -1e-6 => '!!float -1.0e-6'. It's not very nice, but does work.
                distance = float(convert_to_object(str_distance)) * 1e-6  # µm -> m
            except ValueError:
                raise ValueError("Distance '%s' cannot be converted to a number" %
                                 str_distance)

            if check_distance and abs(distance) > MAX_DISTANCE:
                raise IOError("Distance of %f m is too big (> %f m), use '--big-distance' to allow the move." %
                              (abs(distance), MAX_DISTANCE))
        else:
            distance = convert_to_object(str_distance)

        if to_radians:
            if ad.unit == "rad":
                distance = math.radians(distance)
            else:
                raise ValueError("Axis %s is in %s, doesn't support value in degrees" % (axis_name, ad.unit))

        act_mv[axis_name] = distance
        logging.info(u"Will move %s.%s by %s", comp_name, axis_name,
                     units.readable_str(distance, ad.unit, sig=3))

    try:
        m = component.moveRel(act_mv)
        try:
            m.result(120)
        except KeyboardInterrupt:
            logging.warning("Cancelling relative move of component %s", comp_name)
            m.cancel()
            raise
    except Exception as exc:
        raise IOError("Failed to move component %s by %s: %s" %
                      (comp_name, act_mv, exc))


def move_abs(comp_name, moves, check_distance=True, to_radians=False):
    """
    move (in absolute) the axis of the given component to the specified position
    comp_name (str): name of the component
    moves (dict str -> str): axis -> position (as text)
    check_distance (bool): if the axis is in meters, check that the move is not
      too big.
    to_radians (bool): will convert from degrees to radians if the axis is in radians,
      otherwise will fail
    """
    component = get_component(comp_name)

    act_mv = {} # axis -> value
    for axis_name, str_position in moves.items():
        try:
            if axis_name not in component.axes:
                raise ValueError("Actuator %s has no axis '%s'" % (comp_name, axis_name))
            ad = component.axes[axis_name]
        except (TypeError, AttributeError):
            raise ValueError("Component %s is not an actuator" % comp_name)

        # Allow the user to indicate the position via the user-friendly choice entry
        position = None
        if hasattr(ad, "choices") and isinstance(ad.choices, dict):
            for key, value in ad.choices.items():
                if value == str_position:
                    logging.info("Converting '%s' into %s", str_position, key)
                    position = key
                    # Even if it's a big distance, we don't complain as it's likely
                    # that all choices are safe
                    break

        if position is None:
            if ad.unit == "m":
                try:
                    position = float(convert_to_object(str_position))
                except ValueError:
                    raise ValueError("Position '%s' cannot be converted to a number" % str_position)

                # compare to the current position, to see if the new position sounds reasonable
                cur_pos = component.position.value[axis_name]
                if check_distance and abs(cur_pos - position) > MAX_DISTANCE:
                    raise IOError("Distance of %f m is too big (> %f m), use '--big-distance' to allow the move." %
                                  (abs(cur_pos - position), MAX_DISTANCE))
            else:
                position = convert_to_object(str_position)

            if to_radians:
                if ad.unit == "rad":
                    position = math.radians(position)
                else:
                    raise ValueError("Axis %s is in %s, doesn't support value in degrees" % (axis_name, ad.unit))

            # If only a couple of positions are possible, and asking for a float,
            # avoid the rounding error by looking for the closest possible
            if (isinstance(position, numbers.Real) and
                hasattr(ad, "choices") and
                isinstance(ad.choices, Iterable) and
                position not in ad.choices):
                closest = util.find_closest(position, ad.choices)
                if util.almost_equal(closest, position, rtol=1e-3):
                    logging.debug("Adjusting value %.15g to %.15g", position, closest)
                    position = closest

        act_mv[axis_name] = position
        if isinstance(position, numbers.Real):
            pos_pretty = units.readable_str(position, ad.unit, sig=3)
        else:
            pos_pretty = "%s" % (position,)
        logging.info(u"Will move %s.%s to %s", comp_name, axis_name, pos_pretty)

    try:
        m = component.moveAbs(act_mv)
        try:
            m.result(120)
        except KeyboardInterrupt:
            logging.warning("Cancelling absolute move of component %s", comp_name)
            m.cancel()
            raise
    except Exception as exc:
        raise IOError("Failed to move component %s to %s: %s" %
                      (comp_name, act_mv, exc))


def reference(comp_name, axis_name):
    """
    reference the axis of the given component
    """
    component = get_component(comp_name)

    try:
        if axis_name not in component.axes:
            raise ValueError("Actuator %s has no axis '%s'" % (comp_name, axis_name))
    except (TypeError, AttributeError):
        raise ValueError("Component %s is not an actuator" % comp_name)

    try:
        if axis_name not in component.referenced.value:
            raise AttributeError()  # immediately caught
    except (TypeError, AttributeError):
        raise ValueError("Axis %s of actuator %s cannot be referenced" % (axis_name, comp_name))

    try:
        m = component.reference({axis_name})
        try:
            m.result(360)
        except KeyboardInterrupt:
            logging.warning("Cancelling referencing of axis %s", axis_name)
            m.cancel()
            raise
    except Exception as exc:
        raise IOError("Failed to reference axis %s of component %s: %s" %
                      (axis_name, comp_name, exc))

def stop_move():
    """
    stop the move of every axis of every actuators
    """
    # Take all the components and skip the ones that don't look like an actuator
    try:
        comps = model.getComponents()
    except Exception:
        raise IOError("Failed to contact the back-end")

    error = False
    for c in comps:
        if not isinstance(c.axes, Mapping):
            continue
        try:
            c.stop()
        except Exception:
            logging.exception("Failed to stop actuator %s", c.name)
            error = True

    if error:
        raise IOError("Failed to stop all the actuators")

def _get_big_image(df):
    """
    Same as df.get(), but avoids "out of memory" errors more often in case of
    really big images (>100 Mb)
    df (Dataflow)
    """
    evt = threading.Event()
    images = []

    def get_data(dflow, da):
        dflow.unsubscribe(get_data)
        images.append(da)
        evt.set()

    df.subscribe(get_data)
    evt.wait()
    return images[-1]

def acquire(comp_name, dataflow_names, filename):
    """
    Acquire an image from one (or more) dataflow
    comp_name (string): name of the detector to find
    dataflow_names (list of string): name of each dataflow to access
    filename (unicode): name of the output file (format depends on the extension)
    """
    component = get_detector(comp_name)

    # check the dataflow exists
    dataflows = []
    for df_name in dataflow_names:
        try:
            df = getattr(component, df_name)
        except AttributeError:
            raise ValueError("Failed to find data-flow '%s' on component %s" % (df_name, comp_name))

        if not isinstance(df, model.DataFlowBase):
            raise ValueError("%s.%s is not a data-flow" % (comp_name, df_name))

        dataflows.append(df)

    images = []
    for df in dataflows:
        try:
            # Note: currently, get() uses Pyro, which is not as memory efficient
            # as .subscribe(), which uses ZMQ. So would need to use
            # _get_big_image() if very large image is requested.
            image = df.get()
        except Exception as exc:
            raise IOError("Failed to acquire image from component %s: %s" % (comp_name, exc))

        logging.info("Acquired an image of dimension %r.", image.shape)
        images.append(image)

        try:
            if model.MD_PIXEL_SIZE in image.metadata:
                pxs = image.metadata[model.MD_PIXEL_SIZE]
                dim = (image.shape[0] * pxs[0], image.shape[1] * pxs[1])
                logging.info("Physical dimension of image is %s.",
                             units.readable_str(dim, unit="m", sig=3))
            else:
                logging.warning("Physical dimension of image is unknown.")

            if model.MD_SENSOR_PIXEL_SIZE in image.metadata:
                spxs = image.metadata[model.MD_SENSOR_PIXEL_SIZE]
                dim_sens = (image.shape[0] * spxs[0], image.shape[1] * spxs[1])
                logging.info("Physical dimension of sensor is %s.",
                             units.readable_str(dim_sens, unit="m", sig=3))
        except Exception as exc:
            logging.exception("Failed to read image information.")

    exporter = dataio.find_fittest_converter(filename)
    try:
        exporter.export(filename, images)
    except IOError as exc:
        raise IOError(u"Failed to save to '%s': %s" % (filename, exc))

def live_display(comp_name, df_name):
    """
    Acquire an image from one (or more) dataflow
    comp_name (string): name of the detector to find
    df_name (string): name of the dataflow to access
    """
    component = get_detector(comp_name)

    # check the dataflow exists
    try:
        df = getattr(component, df_name)
    except AttributeError:
        raise ValueError("Failed to find data-flow '%s' on component %s" % (df_name, comp_name))

    if not isinstance(df, model.DataFlowBase):
        raise ValueError("%s.%s is not a data-flow" % (comp_name, df_name))

    print("Press 'Q' to quit")
    # try to guess the size of the first image that will come
    try:
        size = component.resolution.value
        # check it's a 2-tuple, mostly to detect if it's a RemoteMethod, which
        # means it doesn't exists.
        if not isinstance(size, (tuple, list)) or len(size) != 2:
            raise ValueError
    except (AttributeError, ValueError):
        # pick something not too stupid
        size = (512, 512)

    # We only import it here, because it pools lots of dependencies for the GUI,
    # which is slow to load, and annoying for all the times this function is not
    # used.
    from odemis.cli.video_displayer import VideoDisplayer

    # create a window
    window = VideoDisplayer("Live from %s.%s" % (comp_name, df_name), size)

    # update the picture and wait
    def new_image_wrapper(df, image):
        window.new_image(image)
    try:
        df.subscribe(new_image_wrapper)

        # wait until the window is closed
        window.waitQuit()
    finally:
        df.unsubscribe(new_image_wrapper)

def ensure_output_encoding():
    """
    Make sure the output encoding supports unicode
    """
    # When piping to the terminal, python knows the encoding needed, and
    # sets it automatically. But when piping, python can not check the output
    # encoding. In that case, it is None. In that case, we force it to UTF-8

    current = getattr(sys.stdout, "encoding", None)
    if current is None:
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout)

    current = getattr(sys.stderr, "encoding", None)
    if current is None:
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="odemis-cli",
                                     description=odemis.__fullname__)

    # argparse doesn't allow optional arguments without dash. So to support
    # action-like arguments, we add "--" on the fly.
    for i, arg in enumerate(args):
        if arg in ACTION_NAMES:
            args[i] = "--" + arg
            # Only do it on the first match, as a "safety" in case an argument
            # (eg, component role) would be matching action too.
            break

    parser.add_argument('--version', dest="version", action='store_true',
                        help="show program's version number and exit")
    opt_grp = parser.add_argument_group('Options')
    opt_grp.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                         default=0, help="set verbosity level (0-2, default = 0)")
    opt_grp.add_argument("--machine", dest="machine", action="store_true", default=False,
                         help="display in a machine-friendly way (i.e., no pretty printing)")
    dm_grp = parser.add_argument_group('Microscope management')
    dm_grpe = dm_grp.add_mutually_exclusive_group()
    dm_grpe.add_argument("--kill", "-k", dest="kill", action="store_true", default=False,
                         help="kill the running back-end")
    dm_grpe.add_argument("--check", dest="check", action="store_true", default=False,
                         help="check for a running back-end (only returns exit code)")
    dm_grpe.add_argument("--scan", dest="scan", const=True, default=False, nargs="?",
                         metavar="class",
                         help="scan for possible devices to connect (the "
                         "back-end must be stopped). Optionally class name of "
                         "a specific hardware to scan can be specified.")
    dm_grpe.add_argument("--list", "-l", dest="list", action="store_true", default=False,
                         help="list the components of the microscope")
    dm_grpe.add_argument("--list-prop", "-L", dest="listprop", metavar="<component>",
                         help="list the properties of a component. Use '*' to list all the components.")
    dm_grpe.add_argument("--set-attr", "-s", dest="setattr", nargs="+", action='append',
                         metavar=("<component>", "<attribute>"),
                         help="set the attribute of a component. First the component name, "
                         "then a series of attribute/value to be set. "
                         "(Lists are delimited by commas, dictionary keys are delimited by colon)")
    dm_grpe.add_argument("--update-metadata", "-u", dest="upmd", nargs="+", action='append',
                         metavar=("<component>", "<key>"),
                         help="update the metadata entry of a component. First the component name, "
                         "then a series of key/value to be set. "
                         "(Lists are delimited by commas)")
    dm_grpe.add_argument("--move", "-m", dest="move", nargs=3, action='append',
                         metavar=("<component>", "<axis>", "<distance>"),
                         help=u"move the axis by the given amount (µm for distances).")
    dm_grpe.add_argument("--position", "-p", dest="position", nargs=3, action='append',
                         metavar=("<component>", "<axis>", "<position>"),
                         help=u"move the axis to the given position.")
    dm_grp.add_argument("--big-distance", dest="bigdist", action="store_true", default=False,
                        help=u"flag needed to allow any move bigger than 10 mm.")
    dm_grp.add_argument("--degrees", dest="degrees", action="store_true", default=False,
                        help=u"indicate the position is in degrees, it will be converted to radians.")
    dm_grpe.add_argument("--reference", dest="reference", nargs=2, action="append",
                         metavar=("<component>", "<axis>"),
                         help="runs the referencing procedure for the given axis.")
    dm_grpe.add_argument("--stop", "-S", dest="stop", action="store_true", default=False,
                         help="immediately stop all the actuators in all directions.")
    dm_grpe.add_argument("--acquire", "-a", dest="acquire", nargs="+",
                         metavar=("<component>", "data-flow"),
                         help="acquire an image (default data-flow is \"data\")")
    dm_grp.add_argument("--output", "-o", dest="output",
                        help="name of the file where the image should be saved "
                        "after acquisition. The file format is derived from the extension "
                        "(TIFF and HDF5 are supported).")
    dm_grpe.add_argument("--live", dest="live", nargs="+",
                         metavar=("<component>", "data-flow"),
                         help="display and update an image on the screen (default data-flow is \"data\")")

    # To allow printing unicode even with pipes
    ensure_output_encoding()

    options = parser.parse_args(args[1:])

    # Cannot use the internal feature, because it doesn't support multiline
    if options.version:
        print(odemis.__fullname__ + " " + odemis.__version__ + "\n" +
              odemis.__copyright__ + "\n" +
              "Licensed under the " + odemis.__license__)
        return 0

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    # TODO: allow to put logging level so low that nothing is ever output
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]

    # change the log format to be more descriptive
    handler = logging.StreamHandler()
    logging.getLogger().setLevel(loglev)
    handler.setFormatter(logging.Formatter('%(asctime)s (%(module)s) %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)

    if loglev <= logging.DEBUG:
        # Activate also Pyro logging
        # TODO: options.logtarget
        pyrolog = logging.getLogger("Pyro4")
        pyrolog.setLevel(min(pyrolog.getEffectiveLevel(), logging.INFO))

    # anything to do?
    if not any((options.check, options.kill, options.scan,
        options.list, options.stop, options.move,
        options.position, options.reference,
        options.listprop, options.setattr, options.upmd,
        options.acquire, options.live)):
        logging.error("No action specified.")
        return 127
    if options.acquire is not None and options.output is None:
        logging.error("Name of the output file must be specified.")
        return 127
    if options.setattr:
        for l in options.setattr:
            if len(l) < 3 or (len(l) - 1) % 2 == 1:
                logging.error("--set-attr expects component name and then a even number of arguments")
                return 127
    if options.upmd:
        for l in options.upmd:
            if len(l) < 3 or (len(l) - 1) % 2 == 1:
                logging.error("--update-metadata expects component name and then a even number of arguments")
                return 127

    logging.debug("Trying to find the backend")
    status = get_backend_status()
    if options.check:
        logging.info("Status of back-end is %s", status)
        return status_to_xtcode[status]

    try:
        # scan needs to have the backend stopped
        if options.scan:
            if status == BACKEND_RUNNING:
                raise ValueError("Back-end running while trying to scan for devices")
            if isinstance(options.scan, basestring):
                scan(options.scan)
            else:
                scan()
            return 0

        # check if there is already a backend running
        if status == BACKEND_STOPPED:
            raise IOError("No running back-end")
        elif status == BACKEND_DEAD:
            raise IOError("Back-end appears to be non-responsive.")

        logging.debug("Executing the actions")

        if options.kill:
            kill_backend()
        elif options.list:
            list_components(pretty=not options.machine)
        elif options.listprop is not None:
            list_properties(options.listprop, pretty=not options.machine)
        elif options.setattr is not None:
            for l in options.setattr:
                # C A B E F => C, {A: B, E: F}
                c = l[0]
                avs = dict(zip(l[1::2], l[2::2]))
                set_attr(c, avs)
        elif options.upmd is not None:
            for l in options.upmd:
                c = l[0]
                kvs = dict(zip(l[1::2], l[2::2]))
                update_metadata(c, kvs)
        # TODO: catch keyboard interrupt and stop the moves
        elif options.reference is not None:
            for c, a in options.reference:
                reference(c, a)
        elif options.position is not None:
            moves = merge_moves(options.position)
            for c, m in moves.items():
                move_abs(c, m, check_distance=(not options.bigdist), to_radians=options.degrees)
        elif options.move is not None:
            moves = merge_moves(options.move)
            for c, m in moves.items():
                move(c, m, check_distance=(not options.bigdist), to_radians=options.degrees)
        elif options.stop:
            stop_move()
        elif options.acquire is not None:
            component = options.acquire[0]
            if len(options.acquire) == 1:
                dataflows = ["data"]
            else:
                dataflows = options.acquire[1:]
            if isinstance(options.output, unicode):  # python3
                filename = options.output
            else:  # python2
                filename = options.output.decode(sys.getfilesystemencoding())
            acquire(component, dataflows, filename)
        elif options.live is not None:
            component = options.live[0]
            if len(options.live) == 1:
                dataflow = "data"
            elif len(options.live) == 2:
                dataflow = options.acquire[2]
            else:
                raise ValueError("Live command accepts only one data-flow")
            live_display(component, dataflow)
    except KeyboardInterrupt:
        logging.info("Interrupted before the end of the execution")
        return 1
    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except IOError as exp:
        logging.error("%s", exp)
        return 129
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.debug("Threads still running: %s", threading.enumerate())
    exit(ret)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
