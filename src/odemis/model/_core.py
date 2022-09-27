# -*- coding: utf-8 -*-
'''
Created on 18 Jun 2012

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
from past.builtins import basestring
import Pyro4
from Pyro4.core import oneway
from collections.abc import Mapping
import logging
import multiprocessing
import os
import threading
from future.moves.urllib.parse import quote
from odemis.util import inspect_getmembers


# Pyro4.config.COMMTIMEOUT = 30.0 # a bit of timeout
# There is a problem with threadpool: threads have a timeout on waiting for a
# request. That obviously doesn't make much sense, but also means it's not
# possible to put a global timeout with the current version and threadpool.
# One possibility is to change ._pyroTimeout on each proxy.
# thread is restricted: it can handle at the same time only
# MAXTHREADS concurrent connections.
# After that it simply blocks. As there is one connection per object, it goes fast.
# Multiplex can handle a much larger number of connections, but will always
# execute the requests one at a time, which can cause deadlock when handling
# callbacks.
#Pyro4.config.SERVERTYPE = "multiplex"
Pyro4.config.THREADPOOL_MINTHREADS = 16 # TODO: still need 48, because it can block when increasing the pool?
Pyro4.config.THREADPOOL_MAXTHREADS = 128
# TODO make sure Pyro can now grow the pool: it used to allocate a huge static
# number of threads. It seems also that when growing the pool it sometimes blocks

# maximum call time for the instantiation (which can be extra long for some hardware)
# and the standard call (normally, the long ones return a future).
INIT_TIMEOUT = 300  # s
CALL_TIMEOUT = 30  # s

# TODO needs a different value on Windows
# TODO try a user temp directory if /var/run/odemisd doesn't exist (and cannot be created)
BASE_DIRECTORY="/var/run/odemisd"
BASE_GROUP="odemis" # user group that is allowed to access the backend


BACKEND_FILE = BASE_DIRECTORY + "/backend.ipc" # the official ipc file for backend (just to detect status)
BACKEND_NAME = "backend" # the official name for the backend container

_microscope = None

def getMicroscope():
    """
    return the microscope component managed by the backend
    Note: if a connection has already been set up, it will reuse it, unless
    you reset _microscope to None
    """
    global _microscope # cached at the module level
    if _microscope is None:
        backend = getContainer(BACKEND_NAME, validate=False)

        # Force a short timeout, because if the backend is not reachable very
        # soon it's unlikely it will ever get better
        prev_to = backend._pyroTimeout
        backend._pyroTimeout = 5  # s
        _microscope = backend.getRoot()
        backend._pyroTimeout = prev_to
    return _microscope

def getComponent(name=None, role=None):
    """
    Find a component, according to its name or role.
    At least a name or a role should be provided
    name (str): name of the component to look for
    role (str): role of the component to look for
    return (Component): the component with the given name
    raise LookupError: if no component with such a name is given
    """
    # Note: we could have a "light-weight" version which directly connects to
    # the right container (by-passing the backend), but it's probably going to
    # save time only if just one component is ever used (and immediately found)

    if name is None and role is None:
        raise ValueError("Need to specify at least a name or a role")

    for c in getComponents():
        if name is not None and c.name != name:
            continue
        if role is not None and c.role != role:
            continue
        return c
    else:
        errors = []
        if name is not None:
            errors.append("name %s" % name)
        if role is not None:
            errors.append("role %s" % role)
        raise LookupError("No component with the %s" % (" and ".join(errors),))


def getComponents():
    """
    return (set of Component): all the HwComponents (alive) managed by the backend
    """
    microscope = getMicroscope()
    return microscope.alive.value | {microscope}
    # return _getChildren(microscope)


def _getChildren(root):
    """
    Return the set of components which are referenced from the given component
     (via children)
    root (HwComponent): the component to start from
    returns (set of HwComponents)
    """
    ret = {root}
    for child in root.children.value:
        ret |= _getChildren(child)

    return ret


# TODO: that should be part of Pyro, called anytime a proxy is received
def _getMostDirectObject(obj, rmtobj):
    """
    obj (object): a object (typically) registered on the Pyro Daemon (server)
    rmtobj (object): any object, which could be a Pyro proxy
    returns (object): if rmtobj is a pyroProxy of an object handled by the same
      Pyro daemon as obj, returns the actual object, otherwise, returns rmtobj
    """
    if not isinstance(rmtobj, Pyro4.core.Proxy):
        return rmtobj

    if isinstance(obj, Pyro4.core.DaemonObject):
        daemon = obj.daemon  # DaemonObject is special
    else:
        daemon = getattr(obj, "_pyroDaemon", None)
    if daemon is None:
        logging.info("Not possible to find shortcut as obj is not registered on Pyro")
        return rmtobj

    # check if this daemon is exporting an object with the same URI
    uri = rmtobj._pyroUri
    for obj_id, act_obj in daemon.objectsById.items():
        if uri == daemon.uriFor(obj_id):
            logging.info("Found short-cut for Proxy %r: %r", rmtobj, act_obj)
            return act_obj
    else:
        logging.debug("Found no URI matching %s in the %d objects available", uri, len(daemon.objectsById))
    return rmtobj


# TODO special attributes, which are just properties that are explicitly duplicated
# on the proxy. Getting/setting them always access the actual object remotely.
# declarator is like a property. Two possible implementations:
# * special message types (get/set) instead of method call
# * create special methods on the object, to handle these attributes (when the parent object is registered or shared)

# The special read-only attribute which are duplicated on proxy objects
class roattribute(property):
    """
    A member of an object which will be cached in the proxy when remotely shared.
    It can be modified only before the object is ever shared. (Technically, it
    can still be written afterwards but the values will not be synchronised
    between the containers).
    """
    # the implementation is just a (python) property with only a different name
    # TODO force to not have setter, but I have no idea how to, override __init__?
    pass


def get_roattributes(self):
    """
    list all roattributes of an instance
    Note: this only works on an original class, not on a proxy
    """
    members = inspect_getmembers(self.__class__)
    return [name for name, obj in members if isinstance(obj, roattribute)]


def dump_roattributes(self):
    """
    list all the roattributes and their value
    """
    # if it is a proxy, use _odemis_roattributes
    roattr = getattr(self, "_odemis_roattributes", [])
    roattr += get_roattributes(self)

    return {name: getattr(self, name) for name in roattr}


def load_roattributes(self, roattributes):
    """
    duplicate the given roattributes into the instance.
    useful only for a proxy class
    """
    for a, value in roattributes.items():
        setattr(self, a, value)

    # save the list in case we need to pickle the object again
    self._odemis_roattributes = list(roattributes.keys())

if os.name != 'nt':
    import resource
    FILES_PER_VA = 6

    def prepare_to_listen_to_more_vas(inc):
        """
        There's a limit on the number of VA subscribers we can create (the number of open
        file descriptors). By default, it's set to 1024 on ubuntu. If we want to use more
        subscribers, we need to explicitly increase this limit. This is for example necessary in
        the SettingsObserver (cf odemis.acq).
        This function allows us to use an additional amount of inc VA subscribers. It corresponds
        to the system call ulimit -n.
        inc (int): how many VAs to open additionally
        """
        cur_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (cur_limit[0] + inc * FILES_PER_VA, cur_limit[1]))
        except ValueError:
            # this happens when starting odemis from eclipse
            logging.info("Maximum number of open files is already at its limit %s.", cur_limit[0])

else:

    # do nothing in Windows
    def prepare_to_listen_to_more_vas(inc):
        pass

# Container management functions and class
class ContainerObject(Pyro4.core.DaemonObject):
    """Object which represent the daemon for remote access"""

    # it'll never be able to answer back if everything goes fine
    @oneway
    def terminate(self):
        """
        stops the server
        """
        self.daemon.terminate()

    def instantiate(self, klass, kwargs):
        """
        instantiate a component and publish it
        klass (class): component class
        kwargs (dict (str -> value)): arguments for the __init__() of the component
        returns the new component instantiated
        """
        try:
            # HACK: we know that dependencies should contain components, which are pretty
            # much always PyroProxies. In case, the component run in the same
            # container, get the direct reference, to make it faster.
            # The best would be that Pyro takes care of this automatically for all Proxies
            dependencies = kwargs["dependencies"]
            if isinstance(dependencies, Mapping):
                logging.debug("Looking to simplify dependencies entry %s", dependencies)
                dependencies = {k: _getMostDirectObject(self, v) for k, v in dependencies.items()}
                kwargs["dependencies"] = dependencies
        except KeyError:
            pass
        except Exception:
            logging.warning("Exception while trying to unwrap dependencies", exc_info=True)

        return self.daemon.instantiate(klass, kwargs)

    def getRoot(self):
        """
        returns the root object, if it has been defined in the container
        """
        return self.getObject(self.daemon.rootId)

# Basically a wrapper around the Pyro Daemon
class Container(Pyro4.core.Daemon):
    def __init__(self, name):
        """
        name: name of the container (must be unique)
        """
        assert "/" not in name
        self._name = name
        # all the sockets are in the same directory so it's independent from the PWD
        self.ipc_name = BASE_DIRECTORY + "/" + quote(name) + ".ipc"

        if not os.path.isdir(BASE_DIRECTORY + "/."): # + "/." to check it's readable
            logging.error("Directory " + BASE_DIRECTORY + " is not accessible, "
                          "which is needed for creating the container %s", name)
        elif os.path.exists(self.ipc_name):
            try:
                os.remove(self.ipc_name)
                logging.warning("The file '%s' was deleted to create container '%s'.", self.ipc_name, name)
            except OSError:
                logging.error("Impossible to delete file '%s', needed to create container '%s'.", self.ipc_name, name)

        Pyro4.Daemon.__init__(self, unixsocket=self.ipc_name, interface=ContainerObject)

        # To be set by the user of the container
        self.rootId = None # objectId of a "Root" component

    def run(self):
        """
        runs and serve the objects registered in the container.
        returns only when .terminate() is called
        """
        # wrapper to requestLoop() just because the name is strange
        self.requestLoop()

    def terminate(self):
        """
        stops the server
        Can be called remotely or locally
        """
        # wrapper to shutdown(), in order to be more consistent with the vocabulary
        if self.transportServer:  # To avoid failure on multiple calls
            self.shutdown()
        # All the cleaning is done in the original thread, after the run()

    def close(self):
        """
        Cleans up everything behind, once the container is already done with running
        Has to be called locally, at the end.
        """
        # unregister every object still around, to be sure everything gets
        # deallocated from the memory (but normally, it's up to the client to
        # terminate() every component before)
        for obj in list(self.objectsById.values()):
            if hasattr(obj, "_unregister"):
                try:
                    obj._unregister()
                except Exception:
                    logging.exception("Failed to unregister object %s when terminating container", str(obj))
            else:
                self.unregister(obj)

        Pyro4.Daemon.close(self)

    def instantiate(self, klass, kwargs):
        """
        instantiate a Component and publish it
        klass (class): component class
        kwargs (dict (str -> value)): arguments for the __init__() of the component
        returns the new component instantiated
        """
        kwargs["daemon"] = self # the component will auto-register
        try:
            comp = klass(**kwargs)
        except Exception:
            try:
                # If the component already auto-registered, unregister it, so
                # that it can try again later
                self.unregister(quote(kwargs["name"]))
            except Exception:
                pass
            raise
        return comp

    def setRoot(self, component):
        """
        sets the root object. It has to be one of the component handled by the
         container.
        component (Component)
        """
        assert isinstance(component._pyroId, basestring)
        self.rootId = component._pyroId


# helper functions
def getContainer(name, validate=True):
    """
    returns (a proxy to) the container with the given name
    validate (boolean): if the connection should be validated
    raises an exception if no such container exist
    """
    # detect when the base directory doesn't even exists and is readable
    if not os.path.isdir(BASE_DIRECTORY + "/."): # + "/." to check it's readable
        raise IOError("Directory " + BASE_DIRECTORY + " is not accessible.")

    # the container is the default pyro daemon at the address named by the container
    container = Pyro4.Proxy("PYRO:Pyro.Daemon@./u:" + BASE_DIRECTORY + "/" + quote(name) + ".ipc")
    container._pyroTimeout = CALL_TIMEOUT
    container._pyroOneway.add("terminate")

    # A proxy doesn't connect until the first remote call, check the connection
    if validate:
        container.ping() # raise an exception if connection fails
    return container


def getObject(container_name, object_name):
    """
    returns (a proxy to) the object with the given name in the given container
    raises an exception if no such object or container exist
    """
    container = getContainer(container_name, validate=False)
    return container.getObject(quote(object_name))


def createNewContainer(name, validate=True, in_own_process=True):
    """
    creates a new container in an independent and isolated process
    validate (bool): if the connection should be validated
    in_own_process (bool): if True, creates the container in a separate process
     (so can run fully asynchronously). Otherwise, it is run in a thread.
    returns the (proxy to the) new container
    """
    # create a container separately
    if in_own_process:
        isready = multiprocessing.Event()
        p = multiprocessing.Process(name="Container " + name, target=_manageContainer,
                                    args=(name, isready))
    else:
        isready = threading.Event()
        p = threading.Thread(name="Container " + name, target=_manageContainer,
                             args=(name, isready))
    p.start()
    if not isready.wait(5):  # wait maximum 5s
        logging.error("Container %s is taking too long to get ready", name)
        raise IOError("Container creation timeout")

    if in_own_process:
        # Show a message when the process ends (badly)
        def wait_process(proc):
            proc.join()
            xc = proc.exitcode
            if xc:
                # exitcode < 0 if ended by a signal
                logging.warning("Container %s finished with exit code %d", name, xc)
                # TODO: report the container (and all its component) are not alive
                # anymore to the creator?

        wpt = threading.Thread(name="Waiter for container " + name, target=wait_process,
                               args=(p,))
        wpt.daemon = True
        wpt.start()

    # connect to the new container
    return getContainer(name, validate)


def createInNewContainer(container_name, klass, kwargs):
    """
    creates a new component in a new container
    container_name (string)
    klass (class): component class
    kwargs (dict (str -> value)): arguments for the __init__() of the component
    returns:
        (Container) the new container
        (Component) the (proxy to the) new component
    """
    container = createNewContainer(container_name, validate=False)
    try:
        comp = createInContainer(container, klass, kwargs)
    except Exception:
        # TODO: we might want to do something special in case of TimeoutError,
        # as the component might be blocked or still running (slowly). Killing
        # the container process could be better than leaving it as-is.
        try:
            container.terminate()  # Non blocking
        except Exception:
            logging.exception("Failed to stop the container %s after component failure",
                              container_name)
        raise
    return container, comp


def createInContainer(container, klass, kwargs):
    # Temporarily put a longer timeout
    container._pyroTimeout = INIT_TIMEOUT
    try:
        return container.instantiate(klass, kwargs)
    finally:
        container._pyroTimeout = CALL_TIMEOUT


def _manageContainer(name, isready=None):
    """
    manages the whole life of a container, from birth till death
    name (string)
    isready (Event): set when the container is (almost) ready to publish objects
    """
    container = Container(name)
    # TODO: also change the process name/arguments to easily known which process
    # is what? cf py-setproctitle
    logging.debug("Container %s runs in PID %d", name, os.getpid())
    if isready is not None:
        isready.set()
    container.run()
    container.close()

