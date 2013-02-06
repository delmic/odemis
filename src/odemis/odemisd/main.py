#!/usr/bin/env python
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

from odemis.odemisd import modelgen
from odemis.odemisd.mdupdater import MetadataUpdater
from odemis import __version__
from odemis import model
import argparse
import grp
import logging
import os
import signal
import stat
import sys
import time

# TODO the way metadata is updated has probably to be completely changed
# cf specification (=> send all the metadata to the data generator)
def updateMetadata(metadata, parent):
    """
    Update/fill the metadata with all the metadata from all the components
      affecting the given component
    metadata (dict str -> value): metadata
    parent (HwComponent): the component which created the data to which the metadata refers to. 
      Note that the metadata from this very component are not added.
    """
    # find every component which affects the parent
    for comp in model.getComponents():
        try:
            if parent in comp.affects:
                metadata.update(comp.getMetadata())
        except AttributeError:
            # no affects == empty set
            pass


class BackendContainer(model.Container):
    """
    A normal container which also terminates all the other containers when it
    terminates.
    """
    def __init__(self, name=model.BACKEND_NAME):
        model.Container.__init__(self, name)
        self.components = set() # to be updated later on
        self.sub_containers = set() # to be updated later on
    
    def terminate(self):
        # Stop all the components
        for comp in self.components:
            try:
                comp.terminate()
            except:
                logging.warning("Failed to terminate component '%s'", comp.name)
        
        # end all the (sub-)containers
        for container in self.sub_containers:
            try:
                container.terminate()
            except:
                logging.warning("Failed to terminate container %r", container)
    
        # end ourself
        model.Container.terminate(self)
    
    def setMicroscope(self, component):
        self.rootId = component._pyroId


class BackendRunner(object):
    CONTAINER_DISABLE="0" # only for debugging: everything is created in the process: no backend accessible
    CONTAINER_ALL_IN_ONE="1" # one backend container for everything
    CONTAINER_SEPARATED="+" # each component is started in a separate container
    
    def __init__(self, model_file, daemon=False, dry_run=False, containement=CONTAINER_SEPARATED):
        """
        containement (CONTAINER_*): the type of container policy to use
        """
        self.model = model_file
        self.daemon = daemon
        self.dry_run = dry_run
        self.containement = containement
        
        self._container = None
        self._components = set()
        
        signal.signal(signal.SIGINT, self.handle_signal)
    
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
            logging.warning("Not enough permissions to run in group " + model.BASE_GROUP + ", trying anyway...")
            
        # everything created after must be rw by group
        os.umask(~(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)) 

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

    def handle_signal(self, signum, frame):
        logging.warning("Received signal %d: quitting", signum)
        self.stop()
    
    def terminate_all_components(self):
        """
        try to terminate all the components given as much as possible
        components (set of Components): set of components to stop
        """
        for comp in self._components:
            try:
                comp.terminate()
            except:
                # can happen if it was already terminated 
                logging.warning("Failed to terminate component '%s'", comp.name)
    
    def stop(self):
        if self._container:
            self._container.terminate()
            self._container.close()
        else:
            self.terminate_all_components()
            
    def run(self):
        # parse the instantiation file 
        try:
            logging.debug("model instantiation file is: %s", self.model.name)
            inst_model = modelgen.get_instantiation_model(self.model)
            logging.info("model has been read successfully")
        except modelgen.ParseError:
            logging.exception("Error while parsing file %s", self.model.name)
            return 127
    
        # change to odemis group and create the base directory
        try:
            self.set_base_group()
        except:
            logging.exception("Failed to get group " + model.BASE_GROUP)
            return 127
    
        try:
            self.mk_base_dir() 
        except:
            logging.exception("Failed to create back-end directory " + model.BASE_DIRECTORY)
            return 127
    
        # create the root container
        try:
            # create daemon for containing the backend container
            if self.daemon:
                pid = os.fork()
                if pid:
                    logging.debug("Daemon started with pid %d", pid)
                    # TODO: we could try to contact the backend and see if it managed to start
                    return 0
            if self.containement != BackendRunner.CONTAINER_DISABLE:
                self._container = BackendContainer()
        except:
            logging.exception("Failed to create back-end container")
            return 127
        
        try:
            if self.containement == BackendRunner.CONTAINER_SEPARATED: 
                create_sub_containers = True
            else:
                create_sub_containers = False
            mic, comps, sub_containers = modelgen.instantiate_model(
                                            inst_model, self._container, 
                                            create_sub_containers,
                                            dry_run=self.dry_run)
            # save the model
            if self._container:
                self._container.setMicroscope(mic)
                self._container.sub_containers |= sub_containers
            self._components = comps
            logging.info("model has been successfully instantiated")
            logging.debug("model microscope is %s", mic.name) 
            logging.debug("model components are %s", ", ".join([c.name for c in comps])) 
        except:
            logging.exception("When instantiating file %s", self.model.name)
            self.stop()
            return 127
        
        if self.dry_run:
            logging.info("model has been successfully validated, exiting")
            self.stop()
            return 0    # everything went fine
        
        try:
            # special "meta" component
            mdUpdater = self._container.instantiate(MetadataUpdater, 
             {"name": "Metadata Updater", "microscope": mic, "components": comps})
            self._components.add(mdUpdater)
        except:
            logging.exception("When starting the metadata updater")
            self.stop()
            return 127
        
        if self.containement == BackendRunner.CONTAINER_DISABLE:
            # in case it was not clear it's only for debug!
            logging.warning("Going to wait for an hour and die")
            time.sleep(3600)
            self.stop()
            return 0
        
        try:
            self._container.components = self._components
            logging.info("Microscope is now available in container '%s'", model.BACKEND_NAME)
            self._container.run()
        except:
            # This is coming here in case of signal received when the daemon is running
            logging.exception("When running back-end container")
            self.stop()
            return 127
        
        return 0

BACKEND_RUNNING = "RUNNING"
BACKEND_DEAD = "DEAD"
BACKEND_STOPPED = "STOPPED"
def get_backend_status():
    try:
        microscope = model.getMicroscope()
        if len(microscope.name) > 0:
            return BACKEND_RUNNING
    except:
        if os.path.exists(model.BACKEND_FILE):
            return BACKEND_DEAD
        else:
            return BACKEND_STOPPED
    return BACKEND_DEAD

status_to_xtcode = {BACKEND_RUNNING: 0,
                    BACKEND_DEAD: 1,
                    BACKEND_STOPPED: 2
                    }



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
    parser = argparse.ArgumentParser(description=__version__.name)

    parser.add_argument('--version', action='version', 
                        version=__version__.name + " " + __version__.version + " – " + __version__.copyright)
    dm_grp = parser.add_argument_group('Daemon management')
    dm_grpe = dm_grp.add_mutually_exclusive_group()
    dm_grpe.add_argument("--kill", "-k", dest="kill", action="store_true", default=False,
                         help="Kill the running back-end")
    dm_grpe.add_argument("--check", dest="check", action="store_true", default=False,
                        help="Check for a running back-end (only returns exit code)")
    dm_grpe.add_argument("--daemonize", "-D", action="store_true", dest="daemon",
                         default=False, help="Daemonize the back-end after startup")
    opt_grp = parser.add_argument_group('Options')
    opt_grp.add_argument('--validate', dest="validate", action="store_true", default=False,
                         help="Validate the microscope description file and exit")
    dm_grpe.add_argument("--debug", action="store_true", dest="debug",
                         default=False, help="Activate debug mode, where everything runs in one process")
    opt_grp.add_argument("--log-level", dest="loglev", metavar="LEVEL", type=int,
                         default=0, help="Set verbosity level (0-2, default = 0)")
    opt_grp.add_argument("--log-target", dest="logtarget", metavar="{auto,stderr,filename}",
                         default="auto", help="Specify the log target (auto, stderr, filename)")
    parser.add_argument("model", metavar="file.odm.yaml", nargs='?', type=open, 
                        help="Microscope model instantiation file (*.odm.yaml)")

    options = parser.parse_args(args[1:])
    
    # Set up logging before everything else
    if options.loglev < 0:
        parser.error("log-level must be positive.")
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    
    # auto = {odemis.log if daemon, stderr otherwise} 
    if options.logtarget == "auto":
        # default to SysLogHandler ?
        if options.daemon:
            handler = logging.FileHandler("odemis.log")
        else:
            handler = logging.StreamHandler()
    elif options.logtarget == "stderr":
        handler = logging.StreamHandler()
    else:
        handler = logging.FileHandler(options.logtarget)
    logging.getLogger().setLevel(loglev)
    handler.setFormatter(logging.Formatter('%(asctime)s (%(module)s) %(levelname)s: %(message)s'))
    logging.getLogger().addHandler(handler)
    
    if options.validate and (options.kill or options.check or options.daemon):
        logging.error("Impossible to validate a model and manage the daemon simultaneously")
        return 127
    
    # Daemon management
    # python-daemon is a fancy library but seems to do too many things for us.
    # We just need to contact the backend and see what happens
    status = get_backend_status()
    if options.kill:
        if status != BACKEND_RUNNING:
            logging.error("No running back-end to kill")
            return 127
        try:
            backend = model.getContainer(model.BACKEND_NAME)
            backend.terminate()
        except:
            logging.error("Failed to stop the back-end")
            return 127
        return 0
    elif options.check:
        logging.info("Status of back-end is %s", status)
        return status_to_xtcode[status]
    
    # check if there is already a backend running
    if status == BACKEND_RUNNING:
        logging.error("Back-end already running, cannot start a new one")
    
    if options.model is None:
        logging.error("No microscope model instantiation file provided")
        return 127
    
    if options.debug:
        #cont_pol = BackendRunner.CONTAINER_DISABLE
        cont_pol = BackendRunner.CONTAINER_ALL_IN_ONE
    else:
        cont_pol = BackendRunner.CONTAINER_SEPARATED
    
    # let's become the back-end for real
    runner = BackendRunner(options.model, options.daemon, options.validate, cont_pol)
    return runner.run()

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown() 
    exit(ret)
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
