#!/usr/bin/python

"""
Application Module
"""

import warnings

from .debugging import bacpypes_debugging, DebugContents, ModuleLogger
from .comm import ApplicationServiceElement, bind
from .iocb import IOQController, IOCB

from .pdu import Address

from .primitivedata import ObjectIdentifier

from .capability import Collector
from .appservice import StateMachineAccessPoint, ApplicationServiceAccessPoint
from .netservice import NetworkServiceAccessPoint, NetworkServiceElement
from .bvllservice import BIPSimple, BIPForeign, AnnexJCodec, UDPMultiplexer

from .apdu import UnconfirmedRequestPDU, ConfirmedRequestPDU, \
    SimpleAckPDU, ComplexAckPDU, ErrorPDU, RejectPDU, AbortPDU, Error

from .errors import ExecutionError, UnrecognizedService, AbortException, RejectException

# for computing protocol services supported
from .apdu import confirmed_request_types, unconfirmed_request_types, \
    ConfirmedServiceChoice, UnconfirmedServiceChoice
from .basetypes import ServicesSupported

# basic services
from .service.device import WhoIsIAmServices
from .service.object import ReadWritePropertyServices

# some debugging
_debug = 0
_log = ModuleLogger(globals())

#
#   DeviceInfo
#

@bacpypes_debugging
class DeviceInfo(DebugContents):

    _debug_contents = (
        'deviceIdentifier',
        'address',
        'maxApduLengthAccepted',
        'segmentationSupported',
        'vendorID',
        'maxNpduLength',
        'maxSegmentsAccepted',
        )

    def __init__(self):
        # this information is from an IAmRequest
        self.deviceIdentifier = None                    # device identifier
        self.address = None                             # LocalStation or RemoteStation
        self.maxApduLengthAccepted = 1024               # maximum APDU device will accept
        self.segmentationSupported = 'noSegmentation'   # normally no segmentation
        self.vendorID = None                            # vendor identifier

        self.maxNpduLength = 1497           # maximum we can send in transit
        self.maxSegmentsAccepted = None     # value for proposed/actual window size

#
#   DeviceInfoCache
#

@bacpypes_debugging
class DeviceInfoCache:

    def __init__(self):
        if _debug: DeviceInfoCache._debug("__init__")

        # empty cache
        self.cache = {}

    def has_device_info(self, key):
        """Return true iff cache has information about the device."""
        if _debug: DeviceInfoCache._debug("has_device_info %r", key)

        return key in self.cache

    def add_device_info(self, apdu):
        """Create a device information record based on the contents of an
        IAmRequest and put it in the cache."""
        if _debug: DeviceInfoCache._debug("add_device_info %r", apdu)

        # get the existing cache record by identifier
        info = self.get_device_info(apdu.iAmDeviceIdentifier[1])
        if _debug: DeviceInfoCache._debug("    - info: %r", info)

        # update existing record
        if info:
            if (info.address == apdu.pduSource):
                return

            info.address = apdu.pduSource
        else:
            # get the existing record by address (creates a new record)
            info = self.get_device_info(apdu.pduSource)
            if _debug: DeviceInfoCache._debug("    - info: %r", info)

            info.deviceIdentifier = apdu.iAmDeviceIdentifier[1]

        # update the rest of the values
        info.maxApduLengthAccepted = apdu.maxAPDULengthAccepted
        info.segmentationSupported = apdu.segmentationSupported
        info.vendorID = apdu.vendorID

        # say this is an updated record
        self.update_device_info(info)

    def get_device_info(self, key):
        """Return the known information about the device.  If the key is the
        address of an unknown device, build a generic device information record
        add put it in the cache."""
        if _debug: DeviceInfoCache._debug("get_device_info %r", key)

        if isinstance(key, int):
            current_info = self.cache.get(key, None)

        elif not isinstance(key, Address):
            raise TypeError("key must be integer or an address")

        elif key.addrType not in (Address.localStationAddr, Address.remoteStationAddr):
            raise TypeError("address must be a local or remote station")

        else:
            current_info = self.cache.get(key, None)
            if not current_info:
                current_info = DeviceInfo()
                current_info.address = key
                current_info._cache_keys = (None, key)

                self.cache[key] = current_info

        if _debug: DeviceInfoCache._debug("    - current_info: %r", current_info)

        return current_info

    def update_device_info(self, info):
        """The application has updated one or more fields in the device
        information record and the cache needs to be updated to reflect the
        changes.  If this is a cached version of a persistent record then this 
        is the opportunity to update the database."""
        if _debug: DeviceInfoCache._debug("update_device_info %r", info)

        cache_id, cache_address = info._cache_keys

        if (cache_id is not None) and (info.deviceIdentifier != cache_id):
            if _debug: DeviceInfoCache._debug("    - device identifier updated")

            # remove the old reference, add the new one
            del self.cache[cache_id]
            self.cache[info.deviceIdentifier] = info

            cache_id = info.deviceIdentifier

        if (cache_address is not None) and (info.address != cache_address):
            if _debug: DeviceInfoCache._debug("    - device address updated")

            # remove the old reference, add the new one
            del self.cache[cache_address]
            self.cache[info.address] = info

            cache_address = info.address

        # update the keys
        info._cache_keys = (cache_id, cache_address)

    def release_device_info(self, info):
        """This function is called by the segmentation state machine when it
        has finished with the device information."""
        if _debug: DeviceInfoCache._debug("release_device_info %r", info)

        cache_id, cache_address = info._cache_keys
        if cache_id is not None:
            del self.cache[cache_id]
        if cache_address is not None:
            del self.cache[cache_address]

#
#   ApplicationController
#

@bacpypes_debugging
class ApplicationController(IOQController):

    def __init__(self, request_fn, address):
        """Initialize an application controller.  To process requests it only
        needs the function to call that sends an APDU down the stack, the address
        parameter is to help with debugging."""
        if _debug: ApplicationController._debug("__init__ %r %r", request_fn, address)
        IOQController.__init__(self, str(address))

        # save a reference to the request function
        self.request_fn = request_fn
        self.address = address

    def process_io(self, iocb):
        """Called to start processing a request.  This is called immediately
        when the controller is idle, otherwise this is called for the next IOCB
        when the current request has been satisfied."""
        if _debug: ApplicationController._debug("process_io %r", iocb)

        # this is now an active request
        self.active_io(iocb)

        # send the request
        self.request_fn(iocb.args[0])

#
#   Application
#

@bacpypes_debugging
class Application(ApplicationServiceElement, Collector):

    def __init__(self, localDevice=None, localAddress=None, deviceInfoCache=None, aseID=None):
        if _debug: Application._debug("__init__ %r %r deviceInfoCache=%r aseID=%r", localDevice, localAddress, deviceInfoCache, aseID)
        ApplicationServiceElement.__init__(self, aseID)

        # local objects by ID and name
        self.objectName = {}
        self.objectIdentifier = {}

        # keep track of the local device
        if localDevice:
            self.localDevice = localDevice

            # bind the device object to this application
            localDevice._app = self

            # local objects by ID and name
            self.objectName[localDevice.objectName] = localDevice
            self.objectIdentifier[localDevice.objectIdentifier] = localDevice

        # local address deprecated, but continue to use the old initializer
        if localAddress is not None:
            warnings.warn(
                "local address at the application layer deprecated",
                DeprecationWarning,
                )

            # allow the address to be cast to the correct type
            if isinstance(localAddress, Address):
                self.localAddress = localAddress
            else:
                self.localAddress = Address(localAddress)

        # use the provided cache or make a default one
        self.deviceInfoCache = deviceInfoCache or DeviceInfoCache()

        # controllers for managing confirmed requests as a client
        self.controllers = {}

        # now set up the rest of the capabilities
        Collector.__init__(self)

    def add_object(self, obj):
        """Add an object to the local collection."""
        if _debug: Application._debug("add_object %r", obj)

        # extract the object name and identifier
        object_name = obj.objectName
        if not object_name:
            raise RuntimeError("object name required")
        object_identifier = obj.objectIdentifier
        if not object_identifier:
            raise RuntimeError("object identifier required")

        # assuming the object identifier is well formed, check the instance number
        if (object_identifier[1] >= ObjectIdentifier.maximum_instance_number):
            raise RuntimeError("invalid object identifier")

        # make sure it hasn't already been defined
        if object_name in self.objectName:
            raise RuntimeError("already an object with name {0!r}".format(object_name))
        if object_identifier in self.objectIdentifier:
            raise RuntimeError("already an object with identifier {0!r}".format(object_identifier))

        # now put it in local dictionaries
        self.objectName[object_name] = obj
        self.objectIdentifier[object_identifier] = obj

        # append the new object's identifier to the local device's object list
        # if there is one and it has an object list property
        if self.localDevice and self.localDevice.objectList:
            self.localDevice.objectList.append(object_identifier)

        # let the object know which application stack it belongs to
        obj._app = self

    def delete_object(self, obj):
        """Add an object to the local collection."""
        if _debug: Application._debug("delete_object %r", obj)

        # extract the object name and identifier
        object_name = obj.objectName
        object_identifier = obj.objectIdentifier

        # delete it from the application
        del self.objectName[object_name]
        del self.objectIdentifier[object_identifier]

        # remove the object's identifier from the device's object list
        # if there is one and it has an object list property
        if self.localDevice and self.localDevice.objectList:
            indx = self.localDevice.objectList.index(object_identifier)
            del self.localDevice.objectList[indx]

        # make sure the object knows it's detached from an application
        obj._app = None

    def get_object_id(self, objid):
        """Return a local object or None."""
        return self.objectIdentifier.get(objid, None)

    def get_object_name(self, objname):
        """Return a local object or None."""
        return self.objectName.get(objname, None)

    def iter_objects(self):
        """Iterate over the objects."""
        return iter(self.objectIdentifier.values())

    def get_services_supported(self):
        """Return a ServicesSupported bit string based in introspection, look
        for helper methods that match confirmed and unconfirmed services."""
        if _debug: Application._debug("get_services_supported")

        services_supported = ServicesSupported()

        # look through the confirmed services
        for service_choice, service_request_class in confirmed_request_types.items():
            service_helper = "do_" + service_request_class.__name__
            if hasattr(self, service_helper):
                service_supported = ConfirmedServiceChoice._xlate_table[service_choice]
                services_supported[service_supported] = 1

        # look through the unconfirmed services
        for service_choice, service_request_class in unconfirmed_request_types.items():
            service_helper = "do_" + service_request_class.__name__
            if hasattr(self, service_helper):
                service_supported = UnconfirmedServiceChoice._xlate_table[service_choice]
                services_supported[service_supported] = 1

        # return the bit list
        return services_supported

    #-----

    def request(self, apdu):
        """Intercept downstream requests and filter them.  For unconfirmed
        services the APDU is passed down the stack and None is returned.  For
        confirmed services an IOCB is built with the request and queued to
        be sent by an application controller.
        """
        if _debug: Application._debug("request %r", apdu)

        # the parent class request function
        request_fn = super(Application, self).request
        if _debug: Application._debug("    - request_fn: %r", request_fn)

        if isinstance(apdu, UnconfirmedRequestPDU):
            iocb = None
            request_fn(apdu)

        elif isinstance(apdu, ConfirmedRequestPDU):
            iocb = IOCB(apdu)
            if _debug: Application._debug("    - iocb: %r", iocb)

            # get the controller for this destination
            controller = self.controllers.get(apdu.pduDestination, None)
            if not controller:
                if _debug: Application._debug("    - new controller")
                controller = ApplicationController(
                    request_fn, apdu.pduDestination,
                    )

                # keep track of the controller
                self.controllers[apdu.pduDestination] = controller
            if _debug: Application._debug("    - controller: %r", controller)

            # request this apdu
            controller.request_io(iocb)

        # return the iocb if one was created
        return iocb

    def confirmation(self, apdu):
        """Upstream confirmations are from confirmed services that this
        application has generated.  The service will be the active IOCB
        of the application controller."""
        if _debug: Application._debug("confirmation %r", apdu)

        # get the queue for this destination
        controller = self.controllers.get(apdu.pduSource, None)
        if _debug: Application._debug("    - controller: %r", controller)
        if not controller:
            return

        # this request is complete
        if isinstance(apdu, (SimpleAckPDU, ComplexAckPDU)):
            controller.complete_io(controller.active_iocb, apdu)
        elif isinstance(apdu, (ErrorPDU, RejectPDU, AbortPDU)):
            controller.abort_io(controller.active_iocb, apdu)
        else:
            raise RuntimeError("unrecognized APDU type")
        if _debug: Application._debug("    - controller finished")

        # if the queue is empty, forget about the controller
        if not controller.ioQueue.queue:
            if _debug: Application._debug("    - controller queue is empty")
            del self.controllers[apdu.pduSource]

    def indication(self, apdu):
        if _debug: Application._debug("indication %r", apdu)

        # get a helper function
        helperName = "do_" + apdu.__class__.__name__
        helperFn = getattr(self, helperName, None)
        if _debug: Application._debug("    - helperFn: %r", helperFn)

        # send back a reject for unrecognized services
        if not helperFn:
            if isinstance(apdu, ConfirmedRequestPDU):
                raise UnrecognizedService("no function %s" % (helperName,))
            return

        # pass the apdu on to the helper function
        try:
            helperFn(apdu)
        except RejectException as err:
            if _debug: Application._debug("    - reject exception: %r", err)
            raise
        except AbortException as err:
            if _debug: Application._debug("    - abort exception: %r", err)
            raise
        except ExecutionError as err:
            if _debug: Application._debug("    - execution error: %r", err)

            # send back an error
            if isinstance(apdu, ConfirmedRequestPDU):
                resp = Error(errorClass=err.errorClass, errorCode=err.errorCode, context=apdu)
                self.response(resp)

        except Exception as err:
            Application._exception("exception: %r", err)

            # send back an error
            if isinstance(apdu, ConfirmedRequestPDU):
                resp = Error(errorClass='device', errorCode='operationalProblem', context=apdu)
                self.response(resp)

#
#   BIPSimpleApplication
#

@bacpypes_debugging
class BIPSimpleApplication(Application, WhoIsIAmServices, ReadWritePropertyServices):

    def __init__(self, localDevice, localAddress, deviceInfoCache=None, aseID=None):
        if _debug: BIPSimpleApplication._debug("__init__ %r %r deviceInfoCache=%r aseID=%r", localDevice, localAddress, deviceInfoCache, aseID)
        Application.__init__(self, localDevice, deviceInfoCache, aseID=aseID)

        # local address might be useful for subclasses
        if isinstance(localAddress, Address):
            self.localAddress = localAddress
        else:
            self.localAddress = Address(localAddress)

        # include a application decoder
        self.asap = ApplicationServiceAccessPoint()

        # pass the device object to the state machine access point so it
        # can know if it should support segmentation
        self.smap = StateMachineAccessPoint(localDevice)

        # the segmentation state machines need access to the same device
        # information cache as the application
        self.smap.deviceInfoCache = self.deviceInfoCache

        # a network service access point will be needed
        self.nsap = NetworkServiceAccessPoint()

        # give the NSAP a generic network layer service element
        self.nse = NetworkServiceElement()
        bind(self.nse, self.nsap)

        # bind the top layers
        bind(self, self.asap, self.smap, self.nsap)

        # create a generic BIP stack, bound to the Annex J server
        # on the UDP multiplexer
        self.bip = BIPSimple()
        self.annexj = AnnexJCodec()
        self.mux = UDPMultiplexer(self.localAddress)

        # bind the bottom layers
        bind(self.bip, self.annexj, self.mux.annexJ)

        # bind the BIP stack to the network, no network number
        self.nsap.bind(self.bip)

#
#   BIPForeignApplication
#

@bacpypes_debugging
class BIPForeignApplication(Application, WhoIsIAmServices, ReadWritePropertyServices):

    def __init__(self, localDevice, localAddress, bbmdAddress, bbmdTTL, aseID=None):
        if _debug: BIPForeignApplication._debug("__init__ %r %r %r %r aseID=%r", localDevice, localAddress, bbmdAddress, bbmdTTL, aseID)
        Application.__init__(self, localDevice, aseID=aseID)

        # local address might be useful for subclasses
        if isinstance(localAddress, Address):
            self.localAddress = localAddress
        else:
            self.localAddress = Address(localAddress)

        # include a application decoder
        self.asap = ApplicationServiceAccessPoint()

        # pass the device object to the state machine access point so it
        # can know if it should support segmentation
        self.smap = StateMachineAccessPoint(localDevice)

        # a network service access point will be needed
        self.nsap = NetworkServiceAccessPoint()

        # give the NSAP a generic network layer service element
        self.nse = NetworkServiceElement()
        bind(self.nse, self.nsap)

        # bind the top layers
        bind(self, self.asap, self.smap, self.nsap)

        # create a generic BIP stack, bound to the Annex J server
        # on the UDP multiplexer
        self.bip = BIPForeign(bbmdAddress, bbmdTTL)
        self.annexj = AnnexJCodec()
        self.mux = UDPMultiplexer(self.localAddress, noBroadcast=True)

        # bind the bottom layers
        bind(self.bip, self.annexj, self.mux.annexJ)

        # bind the NSAP to the stack, no network number
        self.nsap.bind(self.bip)

#
#   BIPNetworkApplication
#

@bacpypes_debugging
class BIPNetworkApplication(NetworkServiceElement):

    def __init__(self, localAddress, eID=None):
        if _debug: BIPNetworkApplication._debug("__init__ %r eID=%r", localAddress, eID)
        NetworkServiceElement.__init__(self, eID)

        # allow the address to be cast to the correct type
        if isinstance(localAddress, Address):
            self.localAddress = localAddress
        else:
            self.localAddress = Address(localAddress)

        # a network service access point will be needed
        self.nsap = NetworkServiceAccessPoint()

        # give the NSAP a generic network layer service element
        bind(self, self.nsap)

        # create a generic BIP stack, bound to the Annex J server
        # on the UDP multiplexer
        self.bip = BIPSimple()
        self.annexj = AnnexJCodec()
        self.mux = UDPMultiplexer(self.localAddress)

        # bind the bottom layers
        bind(self.bip, self.annexj, self.mux.annexJ)

        # bind the NSAP to the stack, no network number
        self.nsap.bind(self.bip)
