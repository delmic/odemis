#!/usr/bin/env python
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

from __future__ import division

import argparse
import codecs
import collections
import gc
import importlib
import inspect
import logging
from odemis import model, dataio, util
import odemis
from odemis.cli.video_displayer import VideoDisplayer
from odemis.util import units
from odemis.util.conversion import convertToObject
from odemis.util.driver import BACKEND_RUNNING, \
    BACKEND_DEAD, BACKEND_STOPPED, get_backend_status, BACKEND_STARTING
import sys
import threading


status_to_xtcode = {BACKEND_RUNNING: 0,
                    BACKEND_DEAD: 1,
                    BACKEND_STOPPED: 2,
                    BACKEND_STARTING: 3,
                    }

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
        for cls_name, clso in inspect.getmembers(module, inspect.isclass):
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
                        print "%s.%s: '%s' init=%s" % (module_name, cls_name, name, str(args))

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
            indent = u"  "*level + u"↳ "
        print u"%s%s\trole:%s" % (indent, comp.name, comp.role)
    else:
        pstr = u""
        try:
            pname = comp.parent.name
            if isinstance(pname, basestring):
                pstr = u"\tparent:" + pname
        except AttributeError:
            pass
        print u"%s\trole:%s%s" % (comp.name, comp.role, pstr)
    # TODO would be nice to display which class is the component
    # TODO:
    # * if emitter, display .shape
    # * if detector, display .shape
    # * if actuator, display .axes

def print_component_tree(root, pretty=True, level=0):
    """
    Print all the components starting from the root.
    root (Component): the component at the root of the tree
    pretty (bool): if True, display with pretty-printing
    level (int > 0): hierarchy level (for pretty printing)
    """
    if pretty:
        # first print the root component
        print_component(root, pretty, level)

        # display all the children
        for comp in root.children.value:
            print_component_tree(comp, pretty, level + 1)
    else:
        for c in model.getComponents():
            print_component(c, pretty)

def list_components(pretty=True):
    """
    pretty (bool): if True, display with pretty-printing
    """
    # We actually just browse as a tree the microscope
    try:
        microscope = model.getMicroscope()
    except Exception:
        raise IOError("Failed to contact the back-end")

    print_component_tree(microscope, pretty=pretty)

def print_axes(name, value, pretty):
    if pretty:
        print u"\t%s (RO Attribute)" % (name,)
        for an, ad in value.items():
            print u"\t\t%s:\t%s" % (an, ad)
    else:
        print u"%s\ttype:roattr\tvalue:%s" % (name,
                                             u", ".join(k for k in value.keys()))
def print_roattribute(name, value, pretty):
    if name == "axes":
        return print_axes(name, value, pretty)

    if pretty:
        print u"\t%s (RO Attribute)\tvalue: %s" % (name, value)
    else:
        print u"%s\ttype:roattr\tvalue:%s" % (name, value)

non_roattributes_names = ("name", "role", "parent", "affects")
def print_roattributes(component, pretty):
    for name, value in model.getROAttributes(component).items():
        # some are handled specifically
        if name in non_roattributes_names:
            continue
        print_roattribute(name, value, pretty)

def print_data_flow(name, df, pretty):
    if pretty:
        print u"\t" + name + u" (Data-flow)"
    else:
        print(u"%s\ttype:data-flow" % (name,))

def print_data_flows(component, pretty):
    # find all dataflows
    for name, value in model.getDataFlows(component).items():
        print_data_flow(name, value, pretty)

def print_event(name, evt, pretty):
    if pretty:
        print u"\t" + name + u" (Event)"
    else:
        print(u"%s\ttype:event" % (name,))

def print_events(component, pretty):
    # find all Events
    for name, value in model.getEvents(component).items():
        print_event(name, value, pretty)

def print_vattribute(name, va, pretty):
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
            str_range = u"\trange:%s" % unicode(varange)
    except (AttributeError, model.NotApplicableError):
        str_range = u""

    try:
        vachoices = va.choices # set or dict
        if pretty:
            if isinstance(va.choices, dict):
                str_choices = u" (choices: %s)" % ", ".join(
                                [u"%s: '%s'" % i for i in vachoices.items()])
            else:
                str_choices = u" (choices: %s)" % u", ".join([str(c) for c in vachoices])
        else:
            str_choices = u"\tchoices:%s" % unicode(vachoices)
    except (AttributeError, model.NotApplicableError):
        str_choices = ""

    if pretty:
        print(u"\t" + name + u" (%sVigilant Attribute)\t value: %s%s%s%s" %
            (readonly, str(va.value), unit, str_range, str_choices))
    else:
        print(u"%s\ttype:%sva\tvalue:%s%s%s%s" %
              (name, readonly, str(va.value), unit, str_range, str_choices))

special_va_names = ("children", "affects") # , "alive", "ghosts")
# TODO: handle .ghosts and .alive correctly in print_va and don't consider them special
def print_vattributes(component, pretty):
    for name, value in model.getVAs(component).items():
        if name in special_va_names:
            continue
        print_vattribute(name, value, pretty)

def print_metadata(component, pretty):
    md = component.getMetadata()
    if pretty:
        if not md:
            return
        print("\tMetadata:")
        for name, value in md.items():
            print(u"\t\t%s: '%s'" % (name, value))
    else:
        for name, value in md.items():
            print(u"%s\ttype:metadata\tvalue:%s" % (name, value))

def print_attributes(component, pretty):
    if pretty:
        print u"Component '%s':" % component.name
        print u"\trole: %s" % component.role
        print u"\taffects: " + ", ".join([u"'%s'" % n for n in component.affects.value])
    else:
        print u"name\tvalue:%s" % component.name
        print u"role\tvalue:%s" % component.role
        print u"affects\tvalue:" + u"\t".join(component.affects.value)
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
    if (not isinstance(comp.shape, collections.Iterable) or
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

        new_val = convertToObject(str_val)

        # Special case for floats, due to rounding error, it's very hard to put the
        # exact value if it's an enumerated VA. So just pick the closest one in this
        # case.
        if isinstance(new_val, float) and (
           hasattr(attr, "choices") and isinstance(attr.choices, collections.Iterable)):
            orig_val = new_val
            new_val = util.find_closest(new_val, attr.choices)
            if new_val != orig_val:
                logging.debug("Adjusting value to %s", new_val)

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

    md = {}
    for key_name, str_val in key_val_str.items():
        # Check that the metadata is a valid one. It's a bit tricky as there is no
        # "official" list. But we look at the ones defined in model.MD_*
        for n, v in inspect.getmembers(model, lambda m: isinstance(m, str)):
            if n.startswith("MD_") and v == key_name:
                key = key_name
                break
        else:
            # fallback to looking for MD_{key_name}
            try:
                key = getattr(model, "MD_%s" % key_name)
            except AttributeError:
                raise ValueError("Metadata key '%s' is unknown" % key_name)

        md[key] = convertToObject(str_val)

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
def move(comp_name, moves):
    """
    move (relatively) the axis of the given component by the specified amount of µm
    comp_name (str): name of the component
    moves (dict str -> str): axis -> distance (as text, and in µm for distances)
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
                distance = float(str_distance) * 1e-6 # µm -> m
            except ValueError:
                raise ValueError("Distance '%s' cannot be converted to a number" %
                                 str_distance)

            if abs(distance) > MAX_DISTANCE:
                raise IOError("Distance of %f m is too big (> %f m)" %
                              (abs(distance), MAX_DISTANCE))
        else:
            distance = convertToObject(str_distance)

        act_mv[axis_name] = distance
        logging.info(u"Will move %s.%s by %s", comp_name, axis_name,
                     units.readable_str(distance, ad.unit, sig=3))

    try:
        m = component.moveRel(act_mv)
        m.result(120)
    except Exception as exc:
        raise IOError("Failed to move component %s by %s: %s" %
                      (comp_name, act_mv, exc))


def move_abs(comp_name, moves):
    """
    move (in absolute) the axis of the given component to the specified position
    comp_name (str): name of the component
    moves (dict str -> str): axis -> position (as text)
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

        if ad.unit == "m":
            try:
                position = float(str_position)
            except ValueError:
                raise ValueError("Position '%s' cannot be converted to a number" % str_position)

            # compare to the current position, to see if the new position sounds reasonable
            cur_pos = component.position.value[axis_name]
            if abs(cur_pos - position) > MAX_DISTANCE:
                raise IOError("Distance of %f m is too big (> %f m)" %
                              (abs(cur_pos - position), MAX_DISTANCE))
        else:
            position = convertToObject(str_position)

        # If only a couple of positions are possible, and asking for a float,
        # avoid the rounding error by looking for the closest possible
        if (hasattr(ad, "choices") and
            isinstance(ad.choices, collections.Iterable) and
            position not in ad.choices):
            closest = util.find_closest(position, ad.choices)
            if util.almost_equal(closest, position, rtol=1e-3):
                logging.debug("Adjusting value %.15g to %.15g", position, closest)
                position = closest

        act_mv[axis_name] = position
        logging.info(u"Will move %s.%s to %s", comp_name, axis_name,
                     units.readable_str(position, ad.unit, sig=3))

    try:
        m = component.moveAbs(act_mv)
        m.result(120)
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
            raise ValueError("Axis %s of actuator %s cannot be referenced" % (axis_name, comp_name))
    except (TypeError, AttributeError):
        raise ValueError("Axis %s of actuator %s cannot be referenced" % (axis_name, comp_name))

    try:
        m = component.reference({axis_name})
        m.result(360)
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
        if not isinstance(c.axes, collections.Mapping):
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

    exporter = dataio.find_fittest_exporter(filename)
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

    print "Press 'Q' to quit"
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

    # TODO: if only one line -> use plot
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

    options = parser.parse_args(args[1:])

    # To allow printing unicode even with pipes
    ensure_output_encoding()

    # Cannot use the internal feature, because it doesn't support multiline
    if options.version:
        print (odemis.__fullname__ + " " + odemis.__version__ + "\n" +
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
    if options.upmd:
        for l in options.upmd:
            if len(l) < 3 or (len(l) - 1) % 2 == 1:
                logging.error("--update-metadata expects component name and then a even number of arguments")

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
                move_abs(c, m)
        elif options.move is not None:
            moves = merge_moves(options.move)
            for c, m in moves.items():
                move(c, m)
        elif options.stop:
            stop_move()
        elif options.acquire is not None:
            component = options.acquire[0]
            if len(options.acquire) == 1:
                dataflows = ["data"]
            else:
                dataflows = options.acquire[1:]
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
