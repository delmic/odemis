#!/usr/bin/env python3
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

import argparse
import grp
from logging import FileHandler
import logging
from logging.handlers import WatchedFileHandler
from odemis import model
import odemis
from odemis.model import ST_UNLOADED, ST_STARTING
from odemis.odemisd import modelgen
from odemis.odemisd.mdupdater import MetadataUpdater
from odemis.util.driver import BACKEND_RUNNING, BACKEND_DEAD, BACKEND_STOPPED, \
    get_backend_status, BACKEND_STARTING
import os
import signal
import stat
import sys
import threading
import time
import yaml
from concurrent import futures

DEFAULT_SETTINGS_FILE = "/etc/odemis-settings.yaml"

status_to_xtcode = {BACKEND_RUNNING: 0,
                    BACKEND_DEAD: 1,
                    BACKEND_STOPPED: 2,
                    BACKEND_STARTING: 3,
                    }

class BackendContainer(model.Container):
    """
    A normal container which also terminates all the other containers when it
    terminates.
    """

    def __init__(self, model_file, settings_file, create_sub_containers=False,
                 dry_run=False, name=model.BACKEND_NAME):
        """
        inst_file (file): opened file that contains the yaml
        settings_file (file): opened file that contains the persistent data
        container (Container): container in which to instantiate the components
        create_sub_containers (bool): whether the leave components (components which
           have no children created separately) are running in isolated containers
        dry_run (bool): if True, it will check the semantic and try to instantiate the
          model without actually any driver contacting the hardware.
        """
        model.Container.__init__(self, name)

        self._model = model_file
        self._settings = settings_file
        self._mdupdater = None
        self._inst_thread = None # thread running the component instantiation
        self._must_stop = threading.Event()
        self._dry_run = dry_run
        # TODO: have an argument to ask for disabling parallel start? same as create_sub_containers?

        # parse the instantiation file
        logging.debug("model instantiation file is: %s", self._model.name)
        try:
            self._instantiator = modelgen.Instantiator(model_file, settings_file, self,
                                                       create_sub_containers, dry_run)
            # save the model
            logging.info("model has been successfully parsed")
        except modelgen.ParseError as exp:
            raise ValueError("Error while parsing file %s:\n%s" % (self._model.name, exp))
        except modelgen.SemanticError as exp:
            raise ValueError("When instantiating file %s:\n%s" % (self._model.name, exp))
        except Exception:
            logging.exception("When instantiating file %s", self._model.name)
            raise IOError("Unexpected error at instantiation")

        # Initialize persistent data, will be updated and written to the settings_file in the
        # end and every time a va is changed.
        self._persistent_listeners = []  # keep reference to persistent va listeners
        self._persistent_data = self._instantiator.read_yaml(settings_file)

    def _observe_persistent_va(self, comp, prop_name):
        """
        Listen to changes in persistent va, update the value of self._persistent_data
        and write data to file.
        comp (HwComponent): component
        prop_name (str): name of va
        """

        def on_va_change(value, comp_name=comp.name, prop_name=prop_name):
            self._persistent_data[comp_name]['properties'][prop_name] = value
            self._write_persistent_data()

        self._persistent_data.setdefault(comp.name, {}).setdefault('properties', {})
        try:
            va = getattr(comp, prop_name)
            self._persistent_data[comp.name]['properties'][prop_name] = va.value
        except AttributeError:
            logging.warning("Persistent property %s not found for component %s." % (prop_name, comp.name))
        else:     
            va.subscribe(on_va_change, init=True)
            self._persistent_listeners.append(on_va_change)

    def _update_persistent_metadata(self):
        """
        Update all metadata in ._persistent_data and write values to settings file.
        """
        for comp in self._instantiator.components:
            _, md_names = self._instantiator.get_persistent(comp.name)
            md_values = comp.getMetadata()
            for md in md_names:
                self._persistent_data.setdefault(comp.name, {}).setdefault('metadata', {})
                fullname = "MD_" + md
                try:
                    self._persistent_data[comp.name]['metadata'][md] = md_values[getattr(model, fullname)]
                except KeyError:
                    logging.warning("Persistent metadata %s not found on component %s" % (md, comp.name))
        self._write_persistent_data()

    def _write_persistent_data(self):
        """
        Write values for all persistent properties and metadata to the settings file.
        """
        if not self._settings or self._dry_run:
            return

        self._settings.truncate(0)  # delete previous file contents
        self._settings.seek(0)  # go back to position 0
        yaml.safe_dump(self._persistent_data, self._settings)

    def run(self):
        # Create the root
        mic = self._instantiator.instantiate_microscope()
        self.setRoot(mic)
        logging.debug("Root component %s created", mic.name)

        # Start by filling up the ghosts VA with all the components
        ghosts_names = set(self._instantiator.ast.keys()) - {mic.name}
        mic.ghosts.value = {n: ST_UNLOADED for n in ghosts_names}

        if self._dry_run:
            # Try to instantiate everything, it will raise an exception if it fails
            try:
                self._instantiate_all()
            finally:
                self.terminate()

            logging.info("model has been successfully validated, exiting")
            return    # everything went fine

        # Start the metadata update
        # TODO: upgrade metadata updater to support online changes
        self._mdupdater = self.instantiate(MetadataUpdater,
                               {"name": "Metadata Updater", "microscope": mic})

        # Keep instantiating the other components in a separate thread
        self._inst_thread = threading.Thread(target=self._instantiate_all,
                                             name="Component instantiator")
        self._inst_thread.start()

        logging.info("Microscope is now available in container '%s'", self._name)

        # From now on, we'll really listen to external calls
        super(BackendContainer, self).run()

    def _instantiate_all(self):
        """
        Thread continuously monitoring the components that need to be instantiated
        """
        try:
            # Hack warning: there is a bug in python when using lock (eg, logging)
            # and simultaneously using threads and process: is a thread acquires
            # a lock while a process is created, it will never be released.
            # See http://bugs.python.org/issue6721
            # To ensure this is not happening, we wait long enough that all (2)
            # threads have started (and logging nothing) before creating new processes.
            time.sleep(1)

            mic = self._instantiator.microscope
            failed = set() # set of str: name of components that failed recently
            while not self._must_stop.is_set():
                # Try to start simultaneously all the components that are
                # independent from each other
                nexts = set()
                while not nexts:
                    instantiated = set(c.name for c in mic.alive.value) | {mic.name}
                    nexts = self._instantiator.get_instantiables(instantiated)
                    # If still some non-failed component, immediately try again,
                    # otherwise give some time for things to get fixed or broken
                    nexts -= failed
                    if not nexts:
                        if self._dry_run:
                            return # everything instantiated, good enough

                        if self._must_stop.wait(10):
                            return
                        failed = set() # not recent anymore

                logging.debug("Trying to instantiate comps: %s", ", ".join(nexts))

                for n in nexts:
                    ghosts = mic.ghosts.value.copy()
                    if n not in ghosts:
                        logging.warning("going to instantiate %s but not a ghost", n)
                    # TODO: run each of them in a future, so that they start
                    # in parallel, and (bonus) when the future is done, check
                    # immediately which component can be started. The only
                    # difficulty is to ensure non-concurrent access to .ghosts
                    # and .alive .
                    try:
                        ghosts[n] = ST_STARTING
                        mic.ghosts.value = ghosts
                        newcmps = self._instantiate_component(n)
                        if self._must_stop.is_set():
                            # in case the termination was too late to stop these new component
                            for c in newcmps:
                                try:
                                    c.terminate()
                                except Exception:
                                    logging.warning("Failed to terminate component '%s'", c.name, exc_info=True)
                            break
                    except ValueError:
                        if self._dry_run:
                            raise
                        # We now need to stop, but cannot call terminate()
                        # directly, as it would deadlock, waiting for us
                        logging.debug("Stopping instantiation due to unrecoverable error")
                        threading.Thread(target=self.terminate).start()
                        return
                    if not newcmps:
                        failed.add(n)

        except Exception:
            logging.exception("Instantiator thread failed")
            raise
        finally:
            logging.debug("Instantiator thread finished")

    def _instantiate_component(self, name):
        """
        Instantiate a component and handle the outcome
        return (set of HwComponent): all the components instantiated, so it is an
          empty set if the component failed to instantiate (due to HwError)
        raise ValueError: if the component failed so badly to instantiate that
                          it's unlikely it'll ever instantiate
        """
        # TODO: use the AST from the microscope (instead of the original one
        # in _instantiator) to allow modifying it online?
        mic = self._instantiator.microscope
        ghosts = mic.ghosts.value.copy()
        try:
            comp = self._instantiator.instantiate_component(name)
        except model.HwError as exp:
            # HwError means: hardware problem, try again later
            logging.warning("Failed to start component %s due to device error: %s",
                            name, exp)
            ghosts[name] = exp
            mic.ghosts.value = ghosts
            return set()
        except Exception as exp:
            # Anything else means: microscope file or driver is borked => give up
            # Exception might have happened remotely, so log it nicely
            logging.error("Failed to instantiate the model due to component %s", name)
            logging.error("Full traceback of the error follows", exc_info=1)
            try:
                remote_tb = exp._pyroTraceback
                logging.info("Remote exception %s", "".join(remote_tb))
            except AttributeError:
                pass
            raise ValueError("Failed to instantiate component %s" % name)
        else:
            new_cmps = self._instantiator.get_children(comp)

            # Check it created at least all the expected children
            new_names = {c.name for c in new_cmps}
            exp_names = self._instantiator.get_children_names(name)
            if exp_names - new_names:
                logging.error("Component %s instantiated components %s, while expected %s",
                              name, new_names, exp_names)
                raise ValueError("Component %s didn't instantiate all components" % (name,))
            elif new_names - exp_names:  # Too many?
                logging.warning("Component %s instantiated extra unexpected components %s",
                                name, new_names - exp_names)

            mic.alive.value = mic.alive.value | new_cmps
            # update ghosts by removing all the new components
            dchildren = self._instantiator.get_children_names(name)
            for n in dchildren:
                del ghosts[n]

            mic.ghosts.value = ghosts

            for c in new_cmps:
                prop_names, _ = self._instantiator.get_persistent(c.name)
                for prop_name in prop_names:
                    self._observe_persistent_va(c, prop_name)
            self._update_persistent_metadata()

            return new_cmps

    def _terminate_all_alive(self):
        """
        Stops all the components that are currently alive (excepted the
          microscope)
        It terminates the dependents (aka "users") first as the dependencies should
         never need their dependent but the dependent might rely on the dependency.
         Children will be terminated before their creator.
        It also stops the containers, once no component is running in them.
        """
        mic = self._instantiator.microscope

        # alive component -> components which depends on it
        self._dependents = {c: set() for c in set(mic.alive.value)}  # comp -> set of comps (all the comps that depend on it)
        # Children will be terminated when their parents are terminated, so no need to keep them in .dependents
        for comp in list(self._dependents.keys()):  # ._dependents will be modified, so we need a copy
            for child in comp.children.value:
                self._dependents.pop(child, None)

        for comp in self._dependents:
            deps = comp.dependencies.value
            for dep in deps:
                # if a component was created by delegation, use its creator
                # instead of itself as "used" by the other components, to ensure
                # they might not be terminated too early.
                try:
                    d = dep.parent or dep
                    self._dependents[d].add(comp)
                except Exception:
                    # if a component died early due to accidents (e.g. a segfault), we might not be able to
                    # access dep.parent
                    logging.warning("Failed to find dependency %s of component %s when terminating.",
                                    dep.name, comp.name, exc_info=True)
            # Add power supply unit as dependency, so it's not terminated before any of the components
            # that depend on it. The reason is that on termination, the power supply unit turns off
            # the power of some components and this should only happen if the components are already
            # properly terminated.
            attr = self._instantiator.ast[comp.name]
            if "power_supplier" in attr:
                psu = self._instantiator._get_component_by_name(attr["power_supplier"])
                self._dependents[psu].add(comp)

        # terminate all the components in order
        terminating_comps = set()  # components that are already terminated or in the process of terminating
        executor = futures.ThreadPoolExecutor(max_workers=20)
        fs_running = set()
        while self._dependents:
            independents = tuple(c for c, p in self._dependents.items() if not p and not c in terminating_comps)
            if not independents and not fs_running:
                # just pick a random component
                independents = tuple(set(self._dependents.keys()) - terminating_comps)[:1]
                if independents:
                    logging.warning("All the components to terminate have parents: %s",
                                    self._dependents)
                else:
                    # That's a sign that some component failed to end => give up
                    logging.warning("Already tried terminating all the components, but still some left: %s",
                                    self._dependents.keys())
                    return

            for comp in independents:
                terminating_comps.add(comp)
                f = executor.submit(self._terminate_independent, comp)
                fs_running.add(f)

            # Block until one future is completed, then continue looping
            done, fs_running = futures.wait(fs_running, return_when=futures.FIRST_COMPLETED)

        logging.debug("Finished requesting termination of all components, waiting for %s components to terminate.",
                      len(fs_running))
        futures.wait(fs_running, return_when=futures.ALL_COMPLETED)

    def _terminate_independent(self, comp):
        try:
            # First terminate the children
            children = comp.children.value
            for child in children:
                self._terminate_component(child)
            # Terminate component itself
            self._terminate_component(comp)
            del self._dependents[comp]  # children are already deleted from ._parents
        except Exception:
            logging.warning("Failed to terminate component %s", comp.name, exc_info=True)

    def _terminate_component(self, c):
        cname = c.name
        logging.debug("Stopping comp %s", cname)
        try:
            c.terminate()
        except Exception:
            logging.warning("Failed to terminate component %s", cname, exc_info=True)

        # remove from the graph
        for p in self._dependents.values():
            p.discard(c)

        # Terminate the container if that was the component for which it
        # was created.
        # TODO: check there is really no component still running in the
        # container?
        if cname in self._instantiator.sub_containers:
            container = self._instantiator.sub_containers[cname]
            logging.debug("Stopping container %s", container)
            try:
                container.terminate()
            except Exception:
                logging.warning("Failed to terminate container %r", container, exc_info=True)
            del self._instantiator.sub_containers[cname]

        try:
            self._instantiator.microscope.alive.value.discard(c)
        except Exception:
            logging.warning("Failed to update the alive VA", exc_info=True)

    def terminate(self):
        if self._must_stop.is_set():
            logging.info("Terminate already called, so not running it again")

        # Save values of persistent properties and metadata
        self._update_persistent_metadata()

        # Stop the component instantiator, to be sure it'll not restart the components
        self._must_stop.set()
        if self._inst_thread:
            self._inst_thread.join(10)
            if self._inst_thread.is_alive():
                logging.warning("Failed to stop the instantiator")
            else:
                self._inst_thread = None

        # Stop all the components
        if self._mdupdater:
            try:
                self._mdupdater.terminate()
            except Exception:
                logging.warning("Failed to terminate Metadata updater", exc_info=True)

        self._terminate_all_alive()

        # In case of instantiation failure, some containers might have no
        # component, but we still need to end them.
        for cname, c in list(self._instantiator.sub_containers.items()):
            logging.debug("Stopping container %s, which was running without component %s",
                          c, cname)
            try:
                c.terminate()
            except Exception:
                logging.warning("Failed to terminate container %r", c, exc_info=True)
            del self._instantiator.sub_containers[cname]

        mic = self._instantiator.microscope
        try:
            mic.terminate()
        except Exception:
            logging.warning("Failed to terminate root", exc_info=True)

        # end ourself
        model.Container.terminate(self)


class BackendRunner(object):
    CONTAINER_ALL_IN_ONE = "1" # one backend container for everything
    CONTAINER_SEPARATED = "+" # each component is started in a separate container

    def __init__(self, model_file, settings_file, daemon=False, dry_run=False,
                 containement=CONTAINER_SEPARATED):
        """
        containement (CONTAINER_*): the type of container policy to use
        """
        self.model = model_file
        self.settings = settings_file
        self.daemon = daemon
        self.dry_run = dry_run
        self.containement = containement

        self._container = None

        # React nicely to keyboard interrupt and shutdown request
        self._main_thread = threading.current_thread()
        signal.signal(signal.SIGINT, self.on_signal_term)
        signal.signal(signal.SIGTERM, self.on_signal_term)

    # TODO: drop the need to be root (and allow to run directly as a standard user)
    # need to ensure that BASE_DIRECTORY is already existing, and that the log
    # file exists either in a sub-directory with odemis group write permissions,
    # or use logrotate (+ use WatchedFileHandler).
    def set_base_group(self):
        """
        Change the current process to be running in the base group (odemis)
        raise:
            Exception in case it's not possible (lack of permissions...)
        """
        try:
            gid_base = grp.getgrnam(model.BASE_GROUP).gr_gid
        except KeyError:
            logging.exception(model.BASE_GROUP + " group doesn't exists.")
            raise

        try:
            os.setgid(gid_base)
        except OSError:
            # This can happen especially when running the backend as a standard user.
            logging.warning("Not enough permissions to run in group " + model.BASE_GROUP + ", trying anyway...")

        # Everything created after should be (also) accessible by the group.
        # Need the user execute bit, to allow directory creation (with files inside)
        os.umask(~(stat.S_IRWXU | stat.S_IRGRP | stat.S_IWGRP))

    def mk_base_dir(self):
        """
        Create the base directory for communication between containers if it's not
        present yet. To create it, you likely need root permissions.
        raise:
            Exception in case it's not possible to create it (lack of permissions...)
        """
        if not os.path.exists(model.BASE_DIRECTORY):
            # it will raise an appropriate exception if it fails to create it
            os.mkdir(model.BASE_DIRECTORY)

    #        # change the group
    #        gid_base = grp.getgrnam(model.BASE_GROUP).gr_gid
    #        os.chown(model.BASE_DIRECTORY, -1, gid_base)
            # Files inside are all group odemis, and it can be listed by anyone
            os.chmod(model.BASE_DIRECTORY, stat.S_ISGID | stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH | stat.S_IXOTH)
            logging.debug("created directory " + model.BASE_DIRECTORY)
        elif not os.path.isdir(model.BASE_DIRECTORY):
            # the unlikely case it's a file
            logging.warning(model.BASE_DIRECTORY + " is not a directory, trying anyway...")

    def on_signal_term(self, signum, frame):
        # TODO: ensure this is only processed by the main thread
        if threading.current_thread() == self._main_thread:
            logging.warning("Received signal %d: quitting", signum)
            threading.Thread(target=self.stop).start()
        else:
            # TODO: do something more clever for sub-processes?
            logging.info("Skipping signal %d in sub-thread", signum)

    def stop(self):
        self._container.terminate()
        self._container.close()

    def run(self):
        # change to odemis group and create the base directory
        try:
            self.set_base_group()
        except Exception:
            logging.error("Failed to get group " + model.BASE_GROUP)
            raise

        try:
            self.mk_base_dir()
        except Exception:
            logging.error("Failed to create back-end directory " + model.BASE_DIRECTORY)
            raise

        # create the root container
        try:
            # create daemon for containing the backend container
            if self.daemon:
                pid = os.fork()
                if pid:
                    logging.debug("Daemon started with pid %d", pid)
                    # TODO: we could try to contact the backend and see if it managed to start
                    return 0
                else:
                    self._main_thread = threading.current_thread()
        except Exception:
            logging.error("Failed to start daemon")
            raise

        if self.containement == BackendRunner.CONTAINER_SEPARATED:
            create_sub_containers = True
        else:
            create_sub_containers = False

        self._container = BackendContainer(self.model, self.settings, create_sub_containers,
                                        dry_run=self.dry_run)

        try:
            self._container.run()
        except Exception:
            self.stop()
            raise

def rotateLog(filename, maxBytes, backupCount=0):
    """
    Rotate the log file if it's bigger than the maxBytes.
    Based on RotatingFileHandler.doRollover()
    """
    if not os.path.exists(filename):
        return

    if os.path.getsize(filename) < maxBytes:
        return

    # Rename the older logs
    if backupCount > 0:
        for i in range(backupCount, 0, -1):
            if i > 1:
                sfn = "%s.%d" % (filename, i - 1)
            else:
                sfn = filename
            dfn = "%s.%d" % (filename, i)
            # print "%s -> %s" % (sfn, dfn)
            if os.path.exists(sfn):
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(sfn, dfn)
    else:
        os.remove(filename)

# This is the cli interface of odemisd, which allows to start the back-end
# It parses the command line and accordingly reads the microscope instantiation
# file, generates a model out of it, and then provides it to the front-end
def main(args):
    """
    Contains the console handling code for the daemon
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    #print args
    # arguments handling
    parser = argparse.ArgumentParser(description=odemis.__fullname__)

    parser.add_argument('--version', dest="version", action='store_true',
                        help="show program's version number and exit")
    dm_grp = parser.add_argument_group('Daemon management')
    dm_grpe = dm_grp.add_mutually_exclusive_group()
    dm_grpe.add_argument("--kill", "-k", dest="kill", action="store_true", default=False,
                         help="Kill the running back-end")
    dm_grpe.add_argument("--check", dest="check", action="store_true", default=False,
                         help="Check for a running back-end (only returns exit code)")
    dm_grpe.add_argument("--daemonize", "-D", action="store_true", dest="daemon",
                         default=False, help="Daemonize the back-end")
    opt_grp = parser.add_argument_group('Options')
    opt_grp.add_argument('--validate', dest="validate", action="store_true", default=False,
                         help="Validate the microscope description file and exit")
    dm_grpe.add_argument("--debug", action="store_true", dest="debug",
                         default=False, help="Activate debug mode, where everything runs in one process")
    opt_grp.add_argument("--log-level", dest="loglev", metavar="LEVEL", type=int,
                         default=0, help="Set verbosity level (0-2, default = 0)")
    opt_grp.add_argument("--log-target", dest="logtarget", metavar="{auto,stderr,filename}",
                         default="auto", help="Specify the log target (auto, stderr, filename)")
    # The settings file is opened here because root privileges are dropped at some point after
    # the initialization.
    opt_grp.add_argument("--settings", dest='settings',
                         default=DEFAULT_SETTINGS_FILE, help="Path to the settings file "
                         "(stores values of persistent properties and metadata). "
                         "Default is %s, if writable." % DEFAULT_SETTINGS_FILE)
    parser.add_argument("model", metavar="file.odm.yaml", nargs='?', type=open,
                        help="Microscope model instantiation file (*.odm.yaml)")

    options = parser.parse_args(args[1:])

    # Cannot use the internal feature, because it doesn't support multiline
    if options.version:
        print(odemis.__fullname__ + " " + odemis.__version__ + "\n" +
              odemis.__copyright__ + "\n" +
              "Licensed under the " + odemis.__license__)
        return 0

    # Set up logging before everything else
    if options.loglev < 0:
        parser.error("log-level must be positive.")
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]

    # auto = {odemis.log if daemon, stderr otherwise}
    if options.logtarget == "auto":
        # default to SysLogHandler ?
        if options.daemon:
            options.logtarget = "odemis.log"
        else:
            options.logtarget = "stderr"
    if options.logtarget == "stderr":
        handler = logging.StreamHandler()
    else:
        if sys.platform.startswith('linux'):
            # On Linux, we use logrotate, so nothing much to do
            handler = WatchedFileHandler(options.logtarget)
        else:
            # Rotate the log, with max 5*50Mb used.
            # Note: we used to rely on RotatingFileHandler, but due to multi-
            # processes, it would be rotated multiple times every time it reached the
            # limit. So now, just do it at startup, and hope it doesn't reach huge
            # size in one run.
            rotateLog(options.logtarget, maxBytes=50 * (2 ** 20), backupCount=5)
            handler = FileHandler(options.logtarget)
    logging.getLogger().setLevel(loglev)
    handler.setFormatter(logging.Formatter("%(asctime)s\t%(levelname)s\t%(module)s:%(lineno)d:\t%(message)s"))
    logging.getLogger().addHandler(handler)

    if loglev <= logging.DEBUG:
        # Activate also Pyro logging
        # TODO: options.logtarget
        pyrolog = logging.getLogger("Pyro4")
        pyrolog.setLevel(min(pyrolog.getEffectiveLevel(), logging.INFO))

    # Useful to debug cases of multiple conflicting installations
    logging.info("Starting Odemis back-end v%s (from %s) using Python %d.%d",
                 odemis.__version__, __file__, sys.version_info[0], sys.version_info[1])

    if options.validate and (options.kill or options.check or options.daemon):
        logging.error("Impossible to validate a model and manage the daemon simultaneously")
        return 1

    # Daemon management
    # python-daemon is a fancy library but seems to do too many things for us.
    # We just need to contact the backend and see what happens
    status = get_backend_status()
    if options.check:
        logging.info("Status of back-end is %s", status)
        return status_to_xtcode[status]

    try:
        if options.kill:
            if status != BACKEND_RUNNING:
                raise IOError("No running back-end to kill")
            backend = model.getContainer(model.BACKEND_NAME)
            backend.terminate()
            return 0

        # check if there is already a backend running
        if status == BACKEND_RUNNING:
            raise IOError("Back-end already running, cannot start a new one")

        if options.model is None:
            raise ValueError("No microscope model instantiation file provided")

        try:
            if os.path.exists(options.settings):
                settings_file = open(options.settings, "rt+")
            else:
                # Create the file if it doesn't exist yet. Note that "at+" doesn't
                # work because not only it automatically creates the file if missing,
                # but it also forces the writes to be appended to the end of the file,
                # so it's not possible to change the values.
                settings_file = open(options.settings, "wt+")
        except IOError as ex:
            logging.warning("%s. Will not be able to use persistent data", ex)
            settings_file = None

        if options.debug:
            cont_pol = BackendRunner.CONTAINER_ALL_IN_ONE
        else:
            cont_pol = BackendRunner.CONTAINER_SEPARATED

        # let's become the back-end for real
        runner = BackendRunner(options.model, settings_file, options.daemon,
                               dry_run=options.validate, containement=cont_pol)
        runner.run()
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
    logging.shutdown()
    exit(ret)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
