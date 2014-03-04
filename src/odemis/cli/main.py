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

import argparse
import codecs
import collections
import gc
import importlib
import inspect
import logging
from odemis import model, dataio, util
from odemis.cli.video_displayer import VideoDisplayer
from odemis.util.driver import reproduceTypedValue, BACKEND_RUNNING, \
    BACKEND_DEAD, BACKEND_STOPPED, get_backend_status
import odemis.util.driver
import sys
import time


status_to_xtcode = {BACKEND_RUNNING: 0,
                    BACKEND_DEAD: 1,
                    BACKEND_STOPPED: 2
                    }

# small object that can be remotely executed for scanning
class Scanner(model.Component):
    def __init__(self, cls, **kwargs):
        assert(inspect.isclass(cls))
        model.Component.__init__(self, "scanner for %s" % cls.__name__, **kwargs)
        self.cls = cls
    def scan(self):
        return self.cls.scan()

def scan():
    """
    Scan for connected devices and list them
    Output like:
    Classname: 'Name of Device' init={arg: value, arg2: value2}
    """
    # only here, to avoid importing everything for other commands
    from odemis import driver
    num = 0
    # we scan by using every HwComponent class which has a .scan() method
    for module_name in driver.__all__:
        module = importlib.import_module("." + module_name, "odemis.driver")
        for cls_name, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, model.HwComponent) and hasattr(cls, "scan"):
                logging.info("Scanning for %s.%s components", module_name, cls_name)
                # do it in a separate container so that we don't have to load
                # all drivers in the same process (andor cams don't like it)
                container_name = "scanner%d" % num
                num += 1
                scanner = model.createInNewContainer(container_name, Scanner, {"cls": cls})
                devices = scanner.scan()
                scanner.terminate()
                model.getContainer(container_name).terminate()
                for name, args in devices:
                    print "%s.%s: '%s' init=%s" % (module_name, cls_name, name, str(args))
    return 0

def kill_backend():
    try:
        backend = model.getContainer(model.BACKEND_NAME)
        backend.terminate()
    except:
        logging.error("Failed to stop the back-end")
        return 127
    return 0

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

        children = set(root.children)
        # For microscope, it doesn't have anything in children
        if isinstance(root.detectors, collections.Set):
            children = root.detectors | root.emitters | root.actuators

        # display all the children
        for comp in children:
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
    except:
        logging.error("Failed to contact the back-end")
        return 127
    print_component_tree(microscope, pretty=pretty)
    return 0

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

non_roattributes_names = ("name", "role", "parent", "children", "affects",
                          "actuators", "detectors", "emitters")
def print_roattributes(component, pretty):
    for name, value in model.getROAttributes(component).items():
        # some are handled specifically
        if name in non_roattributes_names:
            continue
        print_roattribute(name, value, pretty)

def print_data_flow(name, df):
    print u"\t" + name + u" (Data-flow)"

def print_data_flows(component):
    # find all dataflows
    for name, value in model.getDataFlows(component).items():
        print_data_flow(name, value)

def print_event(name, evt):
    print u"\t" + name + u" (Event)"

def print_events(component):
    # find all Events
    for name, value in model.getEvents(component).items():
        print_event(name, value)

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

def print_vattributes(component, pretty):
    for name, value in model.getVAs(component).items():
        print_vattribute(name, value, pretty)

def print_attributes(component, pretty):
    if pretty:
        print u"Component '%s':" % component.name
        print u"\trole: %s" % component.role
        print u"\taffects: " + ", ".join([u"'" + c.name + u"'" for c in component.affects])
    else:
        print u"name\tvalue:%s" % component.name
        print u"role\tvalue:%s" % component.role
        print u"affects\tvalue:" + u"\t".join([c.name for c in component.affects])
    print_roattributes(component, pretty)
    print_vattributes(component, pretty)
    print_data_flows(component) # TODO: pretty
    print_events(component)

def get_component_from_set(comp_name, components):
    """
    return the component with the given name from a set of components
    comp_name (string): name of the component to find
    components (iterable Components): the set of components to look into
    raises
        LookupError if the component doesn't exist
        other exception if there is an error while contacting the backend
    """
    component = None
    for c in components:
        if c.name == comp_name:
            component = c
            break

    if component is None:
        raise LookupError("Failed to find component '%s'" % comp_name)

    return component

def get_component(comp_name):
    """
    return the component with the given name
    comp_name (string): name of the component to find
    raises
        LookupError if the component doesn't exist
        other exception if there is an error while contacting the backend
    """
    return get_component_from_set(comp_name, model.getComponents())


def get_detector(comp_name):
    """
    return the actuator component with the given name
    comp_name (string): name of the component to find
    raises
        LookupError if the component doesn't exist
        other exception if there is an error while contacting the backend
    """
    # isinstance() doesn't work, so we just list every component in microscope.detectors
    microscope = model.getMicroscope()
    return get_component_from_set(comp_name, microscope.detectors)

def list_properties(comp_name, pretty=True):
    """
    print the data-flows and VAs of a component
    comp_name (string): name of the component
    pretty (bool): if True, display with pretty-printing
    """
    logging.debug("Looking for component %s", comp_name)
    try:
        component = get_component(comp_name)
    except LookupError:
        logging.error("Failed to find component '%s'", comp_name)
        return 127
    except:
        logging.error("Failed to contact the back-end")
        return 127

    print_attributes(component, pretty)
    return 0


def set_attr(comp_name, attr_name, str_val):
    """
    set the value of vigilant attribute of the given component using the type
    of the current value of the attribute.
    """
    try:
        component = get_component(comp_name)
    except LookupError:
        logging.error("Failed to find component '%s'", comp_name)
        return 127
    except:
        logging.error("Failed to contact the back-end")
        return 127

    try:
        attr = getattr(component, attr_name)
    except:
        logging.error("Failed to find attribute '%s' on component '%s'", attr_name, comp_name)
        return 129

    if not isinstance(attr, model.VigilantAttributeBase):
        logging.error("'%s' is not a vigilant attribute of component '%s'", attr_name, comp_name)
        return 129

    try:
        new_val = reproduceTypedValue(attr.value, str_val)
    except TypeError:
        logging.error("'%s' is of unsupported type %r", attr_name, type(attr.value))
        return 127
    except ValueError:
        logging.error("Impossible to convert '%s' to a %r", str_val, type(attr.value))
        return 127

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
    except:
        logging.exception("Failed to set %s.%s = '%s'", comp_name, attr_name, str_val)
        return 127
    return 0

MAX_DISTANCE = 0.01 #m
def move(comp_name, axis_name, str_distance):
    """
    move (relatively) the axis of the given component by the specified amount of µm
    """
    # for safety reason, we use µm instead of meters, as it's harder to type a
    # huge distance
    try:
        component = get_component(comp_name)
    except LookupError:
        logging.error("Failed to find actuator '%s'", comp_name)
        return 127
    except:
        logging.error("Failed to contact the back-end")
        return 127

    try:
        if axis_name not in component.axes:
            logging.error("Actuator %s has not axis '%s'", comp_name, axis_name)
            return 129
        ad = component.axes[axis_name]
    except (TypeError, AttributeError):
        logging.error("Component %s is not an actuator", comp_name)
        return 127

    if ad.unit == "m":
        try:
            distance = float(str_distance) * 1e-6 # µm -> m
        except ValueError:
            logging.error("Distance '%s' cannot be converted to a number", str_distance)
            return 127

        if abs(distance) > MAX_DISTANCE:
            logging.error("Distance of %f m is too big (> %f m)", abs(distance), MAX_DISTANCE)
            return 129
    else:
        cur_pos = component.position.value[axis_name]
        distance = reproduceTypedValue(cur_pos, str_distance)

    try:
        m = component.moveRel({axis_name: distance})
        m.result()
    except Exception:
        logging.error("Failed to move axis %s of component %s", axis_name, comp_name)
        return 127

    return 0

def move_abs(comp_name, axis_name, str_position):
    """
    move (in absolute) the axis of the given component to the specified position in µm
    """
    # for safety reason, we use µm instead of meters, as it's harder to type a
    # huge distance
    try:
        component = get_component(comp_name)
    except LookupError:
        logging.error("Failed to find actuator '%s'", comp_name)
        return 127
    except:
        logging.error("Failed to contact the back-end")
        return 127

    try:
        if axis_name not in component.axes:
            logging.error("Actuator %s has not axis '%s'", comp_name, axis_name)
            return 129
        ad = component.axes[axis_name]
    except (TypeError, AttributeError):
        logging.error("Component %s is not an actuator", comp_name)
        return 127

    # TODO: check whether the component supports absolute positioning
    if ad.unit == "m":
        try:
            position = float(str_position) * 1e-6 # µm -> m
        except ValueError:
            logging.error("Distance '%s' cannot be converted to a number", str_position)
            return 127

        # compare to the current position, to see if the new position sounds reasonable
        cur_pos = component.position.value[axis_name]
        if abs(cur_pos - position) > MAX_DISTANCE:
            logging.error("Distance of move of %g m is too big (> %g m)", abs(cur_pos - position), MAX_DISTANCE)
            return 129
    else:
        cur_pos = component.position.value[axis_name]
        position = reproduceTypedValue(cur_pos, str_position)

    try:
        m = component.moveAbs({axis_name: position})
        m.result()
    except Exception:
        logging.error("Failed to move axis %s of component %s", axis_name, comp_name)
        return 127

    return 0

def stop_move():
    """
    stop the move of every axis of every actuators
    """
    # We actually just browse as a tree the microscope
    try:
        microscope = model.getMicroscope()
        actuators = microscope.actuators
    except Exception:
        logging.error("Failed to contact the back-end")
        return 127

    ret = 0
    for actuator in actuators:
        try:
            actuator.stop()
        except Exception:
            logging.error("Failed to stop actuator %s", actuator.name)
            ret = 127

    return ret

def acquire(comp_name, dataflow_names, filename):
    """
    Acquire an image from one (or more) dataflow
    comp_name (string): name of the detector to find
    dataflow_names (list of string): name of each dataflow to access
    filename (unicode): name of the output file (format depends on the extension)
    """
    try:
        component = get_detector(comp_name)
    except LookupError:
        logging.error("Failed to find detector '%s'", comp_name)
        return 127
    except:
        logging.error("Failed to contact the back-end")
        return 127

    # check the dataflow exists
    dataflows = []
    for df_name in dataflow_names:
        try:
            df = getattr(component, df_name)
        except:
            logging.error("Failed to find data-flow '%s' on component '%s'", df_name, comp_name)
            return 129

        if not isinstance(df, model.DataFlowBase):
            logging.error("'%s' is not a data-flow of component '%s'", df_name, comp_name)
            return 129
        dataflows.append(df)

    images = []
    for df in dataflows:
        try:
            image = df.get()
            images.append(image)
            logging.info("Acquired an image of dimension %r.", image.shape)
        except:
            logging.exception("Failed to acquire image from component '%s'", comp_name)
            return 127

        try:
            if model.MD_PIXEL_SIZE in image.metadata:
                pxs = image.metadata[model.MD_PIXEL_SIZE]
                dim = (image.shape[0] * pxs[0], image.shape[1] * pxs[1])
                logging.info("Physical dimension of image is %fx%f m.", dim[0], dim[1])
            else:
                logging.warning("Physical dimension of image is unknown.")

            if model.MD_SENSOR_PIXEL_SIZE in image.metadata:
                spxs = image.metadata[model.MD_SENSOR_PIXEL_SIZE]
                dim_sens = (image.shape[0] * spxs[0], image.shape[1] * spxs[1])
                logging.info("Physical dimension of sensor is %fx%f m.", dim_sens[0], dim_sens[1])
        except:
            logging.exception("Failed to read image information")

    exporter = dataio.find_fittest_exporter(filename)
    try:
        exporter.export(filename, images)
    except IOError as exc:
        logging.error(u"Failed to save to '%s': %s", filename, exc)
    return 0

def live_display(comp_name, df_name):
    """
    Acquire an image from one (or more) dataflow
    comp_name (string): name of the detector to find
    df_name (string): name of the dataflow to access
    """
    try:
        component = get_detector(comp_name)
    except LookupError:
        logging.error("Failed to find detector '%s'", comp_name)
        return 127
    except:
        logging.error("Failed to contact the back-end")
        return 127

    # check the dataflow exists
    try:
        df = getattr(component, df_name)
    except:
        logging.error("Failed to find data-flow '%s' on component '%s'", df_name, comp_name)
        return 129

    if not isinstance(df, model.DataFlowBase):
        logging.error("'%s' is not a data-flow of component '%s'", df_name, comp_name)
        return 129

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

    current = sys.stdout.encoding
    if current is None :
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout)
    current = sys.stderr.encoding
    if current is None :
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
    dm_grpe.add_argument("--scan", dest="scan", action="store_true", default=False,
                         help="scan for possible devices to connect (the back-end must be stopped)")
    dm_grpe.add_argument("--list", "-l", dest="list", action="store_true", default=False,
                         help="list the components of the microscope")
    dm_grpe.add_argument("--list-prop", "-L", dest="listprop", metavar="<component>",
                         help="list the properties of a component")
    dm_grpe.add_argument("--set-attr", "-s", dest="setattr", nargs=3, action='append',
                         metavar=("<component>", "<attribute>", "<value>"),
                         help="set the attribute of a component (lists are delimited by commas,"
                         " dictionary keys are delimited by colon)")
    dm_grpe.add_argument("--move", "-m", dest="move", nargs=3, action='append',
                         metavar=("<component>", "<axis>", "<distance>"),
                         help=u"move the axis by the amount of µm.")
    dm_grpe.add_argument("--position", "-p", dest="position", nargs=3, action='append',
                         metavar=("<component>", "<axis>", "<position>"),
                         help=u"move the axis to the given position in µm.")
    dm_grpe.add_argument("--stop", "-S", dest="stop", action="store_true", default=False,
                         help="immediately stop all the actuators in all directions.")
    dm_grpe.add_argument("--acquire", "-a", dest="acquire", nargs="+",
                         metavar=("<component>", "data-flow"),
                         help="acquire an image (default data-flow is \"data\")")
    dm_grp.add_argument("--output", "-o", dest="output",
                        help="name of the file where the image should be saved after acquisition. The file format is derived from the extension (TIFF and HDF5 are supported).")
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
        parser.error("log-level must be positive.")
    # TODO: allow to put logging level so low that nothing is ever output
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]

    # change the log format to be more descriptive
    handler = logging.StreamHandler()
    logging.getLogger().setLevel(loglev)
    handler.setFormatter(logging.Formatter('%(asctime)s (%(module)s) %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)

    # anything to do?
    if (not options.check and not options.kill and not options.scan
        and not options.list and not options.stop and options.move is None
        and options.position is None
        and options.listprop is None and options.setattr is None
        and options.acquire is None and options.live is None):
        logging.error("no action specified.")
        return 127
    if options.acquire is not None and options.output is None:
        logging.error("name of the output file must be specified.")
        return 127


    logging.debug("Trying to find the backend")
    status = get_backend_status()
    if options.check:
        logging.info("Status of back-end is %s", status)
        return status_to_xtcode[status]

    # scan needs to have the backend stopped
    if options.scan:
        if status == BACKEND_RUNNING:
            logging.error("Back-end running while trying to scan for devices")
            return 127
        try:
            return scan()
        except:
            logging.exception("Unexpected error while performing scan.")
            return 127

    # check if there is already a backend running
    if status == BACKEND_STOPPED:
        logging.error("No running back-end")
        return 127
    elif status == BACKEND_DEAD:
        logging.error("Back-end appears to be non-responsive.")
        return 127

    try:
        if options.kill:
            return kill_backend()

        logging.debug("Executing the actions")
        odemis.util.driver.speedUpPyroConnect(model.getMicroscope())

        if options.list:
            return list_components(pretty=not options.machine)
        elif options.listprop is not None:
            return list_properties(options.listprop, pretty=not options.machine)
        elif options.setattr is not None:
            for c, a, v in options.setattr:
                ret = set_attr(c, a, v)
                if ret != 0:
                    return ret
        elif options.position is not None:
            for c, a, d in options.position:
                ret = move_abs(c, a, d)
                # TODO warn if same axis multiple times
                if ret != 0:
                    return ret
            time.sleep(0.5)
        elif options.move is not None:
            for c, a, d in options.move:
                ret = move(c, a, d)
                # TODO move commands to the same actuator should be agglomerated
                if ret != 0:
                    return ret
            time.sleep(0.5) # wait a bit for the futures to close nicely
        elif options.stop:
            return stop_move()
        elif options.acquire is not None:
            component = options.acquire[0]
            if len(options.acquire) == 1:
                dataflows = ["data"]
            else:
                dataflows = options.acquire[1:]
            filename = options.output.decode(sys.getfilesystemencoding())
            return acquire(component, dataflows, filename)
        elif options.live is not None:
            component = options.live[0]
            if len(options.live) == 1:
                dataflow = "data"
            elif len(options.live) == 2:
                dataflow = options.acquire[2]
            else:
                logging.error("live command accepts only one data-flow")
                return 127
            return live_display(component, dataflow)
    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
