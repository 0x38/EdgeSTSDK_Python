#!/usr/bin/env python

################################################################################
# COPYRIGHT(c) 2018 STMicroelectronics                                         #
#                                                                              #
# Redistribution and use in source and binary forms, with or without           #
# modification, are permitted provided that the following conditions are met:  #
#   1. Redistributions of source code must retain the above copyright notice,  #
#      this list of conditions and the following disclaimer.                   #
#   2. Redistributions in binary form must reproduce the above copyright       #
#      notice, this list of conditions and the following disclaimer in the     #
#      documentation and/or other materials provided with the distribution.    #
#   3. Neither the name of STMicroelectronics nor the names of its             #
#      contributors may be used to endorse or promote products derived from    #
#      this software without specific prior written permission.                #
#                                                                              #
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"  #
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE    #
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE   #
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE    #
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR          #
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF         #
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS     #
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN      #
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)      #
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE   #
# POSSIBILITY OF SUCH DAMAGE.                                                  #
################################################################################

################################################################################
# Author:  Davide Aliprandi, STMicroelectronics                                #
################################################################################


# DESCRIPTION
#
# This application example shows how to connect Bluetooth Low Energy (BLE)
# devices implementing the "BlueST" protocol to a Linux gateway, and to make
# them communicate to the Amazon AWS IoT Cloud through the AWS Greengrass edge
# computing service.
#
# The Greengrass edge computing service allows to perform local computation of
# Lambda functions with the same logic available on the cloud even when the
# connection to the cloud is missing; moreover, as soon as the connection
# becomes available the shadow devices on the cloud get automatically
# synchronized to the local virtual devices.
#
# This application example involves two BLE devices exporting the "Switch"
# feature as specified by the BlueST protocol; pressing the user button on a
# device makes the LED of the other device toggle its status. In particular,
# whenever the user button is pressed on a device, the sending device publishes
# a JSON message on a "sense" topic with its device identifier and the status of
# the button, a simple lambda function swaps the device identifier and publishes
# the new message on an "act" topic, and the recipient device toggles the status
# of its LED.


# IMPORT

from __future__ import print_function
import sys
import os
import time
import getopt
import json
import logging
from enum import Enum
from bluepy.btle import BTLEException

from blue_st_sdk.manager import Manager
from blue_st_sdk.manager import ManagerListener
from blue_st_sdk.node import NodeListener
from blue_st_sdk.feature import FeatureListener
from blue_st_sdk.features import *
from blue_st_sdk.utils.blue_st_exceptions import InvalidOperationException

from edge_st_sdk.aws.aws_greengrass import AWSGreengrass
from edge_st_sdk.utils.edge_st_exceptions import WrongInstantiationException


# PRECONDITIONS
#
# Please remember to add to the "PYTHONPATH" environment variable the location
# of the "BlueSTSDK_Python" and the "EdgeSTSDK_Python" SDKs.
#
# On Linux:
# export PYTHONPATH=/home/<user>/BlueSTSDK_Python:/home/<user>/EdgeSTSDK_Python


# CONSTANTS

# Usage message.
USAGE = """Usage:

Use certificate based mutual authentication:
python <application>.py -e <endpoint> -r <root_ca_path>

"""

# Help message.
HELP = """-e, --endpoint
    Your AWS IoT custom endpoint
-r, --rootCA
    Root CA file path
-h, --help
    Help information

"""

# Presentation message.
INTRO = """###############################################
# Edge IoT Example with Amazon Cloud Platform #
###############################################"""

# Bluetooth Low Energy devices' MAC address.
IOT_DEVICE_1_MAC = 'd1:07:fd:84:30:8c'
IOT_DEVICE_2_MAC = 'd7:90:95:be:58:7e'

# Timeouts.
SCANNING_TIME_s = 5
SHADOW_CALLBACK_TIMEOUT_s = 5

# MQTT QoS.
MQTT_QOS_0 = 0
MQTT_QOS_1 = 1

# MQTT Topics.
MQTT_IOT_DEVICE_SWITCH_SENSE_TOPIC = "iot_device/switch_sense"
MQTT_IOT_DEVICE_SWITCH_ACT_TOPIC =   "iot_device/switch_act"

# Devices' certificates, private keys, and path on the Linux gateway.
CERTIF_EXT = ".pem"
PRIV_K_EXT = ".prv"
DEVICES_PATH = "./devices_ble_aws/"
IOT_DEVICE_1_NAME = 'IoT_Device_1'
IOT_DEVICE_2_NAME = 'IoT_Device_2'
IOT_DEVICE_1_CERTIF_PATH = DEVICES_PATH + IOT_DEVICE_1_NAME + CERTIF_EXT
IOT_DEVICE_2_CERTIF_PATH = DEVICES_PATH + IOT_DEVICE_2_NAME + CERTIF_EXT
IOT_DEVICE_1_PRIV_K_PATH = DEVICES_PATH + IOT_DEVICE_1_NAME + PRIV_K_EXT
IOT_DEVICE_2_PRIV_K_PATH = DEVICES_PATH + IOT_DEVICE_2_NAME + PRIV_K_EXT


# SHADOW JSON SCHEMAS

#"IoT_Device_X"
#"state": {
#  "desired": {
#    "welcome": "aws-iot",
#    "switch_status": 0
#  },
#  "reported": {
#    "welcome": "aws-iot"
#  },
#  "delta": {
#    "switch_status": 0
#  }
#}


# CLASSES

# Status of the switch.
class SwitchStatus(Enum):
    OFF = 0
    ON = 1


# FUNCTIONS

#
# Printing intro.
#
def print_intro():
    print('\n' + INTRO + '\n')

#
# Reading input.
#
def read_input(argv):
    global endpoint, root_ca_path

    # Reading in command-line parameters.
    try:
        opts, args = getopt.getopt(argv, "hwe:k:c:r:", ['help", "endpoint=", "key=","cert=","rootCA='])
        if len(opts) == 0:
            raise getopt.GetoptError("No input parameters!")
        for opt, arg in opts:
            if opt in ("-h", "--help"):
                print(HELP)
                exit(0)
            if opt in ("-e", "--endpoint"):
                endpoint = arg
            if opt in ("-r", "--rootCA"):
                root_ca_path = arg
    except getopt.GetoptError:
        print(USAGE)
        exit(1)

    # Missing configuration parameters.
    missing_configuration = False
    if not endpoint:
        print("Missing '-e' or '--endpoint'")
        missing_configuration = True
    if not root_ca_path:
        print("Missing '-r' or '--rootCA'")
        missing_configuration = True
    if missing_configuration:
        exit(2)

#
# Configure logging.
#
def configure_logging():
    logger = logging.getLogger("Demo")
    logger.setLevel(logging.ERROR)
    streamHandler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    streamHandler.setFormatter(formatter)
    logger.addHandler(streamHandler)


# INTERFACES

#
# Implementation of the interface used by the Manager class to notify that a new
# node has been discovered or that the scanning starts/stops.
#
class MyManagerListener(ManagerListener):

    #
    # This method is called whenever a discovery process starts or stops.
    #
    # @param manager Manager instance that starts/stops the process.
    # @param enabled True if a new discovery starts, False otherwise.
    #
    def on_discovery_change(self, manager, enabled):
        print('Discovery %s.' % ('started' if enabled else 'stopped'))
        if not enabled:
            print()

    #
    # This method is called whenever a new node is discovered.
    #
    # @param manager Manager instance that discovers the node.
    # @param node    New node discovered.
    #
    def on_node_discovered(self, manager, node):
        print('New device discovered: %s.' % (node.get_name()))


#
# Implementation of the interface used by the Node class to notify that a node
# has updated its status.
#
class MyNodeListener(NodeListener):

    #
    # To be called whenever a node changes its status.
    #
    # @param node       Node that has changed its status.
    # @param new_status New node status.
    # @param old_status Old node status.
    #
    def on_status_change(self, node, new_status, old_status):
        print('Device %s went from %s to %s.' %
            (node.get_name(), str(old_status), str(new_status)))


#
# Implementation of the interface used by the Feature class to notify that a
# feature has updated its status.
#
class MyFeatureSwitchListener(FeatureListener):

    #
    # Constructor.
    #
    def __init__(self, client, topic):
        super(MyFeatureSwitchListener, self).__init__()
        self._client = client
        self._topic = topic

    #
    # To be called whenever the feature updates its data.
    #
    # @param feature Feature that has updated.
    # @param sample  Data extracted from the feature.
    #
    def on_update(self, feature, sample):
        # Getting value.
        switch_status = feature_switch.FeatureSwitch.get_switch_status(sample)

        # Getting a JSON string representation of the message to publish.
        sample_json_str = json.dumps(
            {'{:s}'.format(
                feature.get_fields_description()[0].get_name()): \
                '({:d}) {:s} {:s}'.format(
                    sample.get_timestamp(),
                    self._client.get_client_id(),
                    str(switch_status)
                    )})

        # Publishing the message.
        #print('Publishing: %s' % (sample_json_str))
        self._client.publish(self._topic, sample_json_str, MQTT_QOS_0)


# DEVICES' CALLBACKS

#
# Custom MQTT message callback for first device.
#
def iot_device_1_callback(client, userdata, message):
    global iot_device_1_act_flag, iot_device_1_status

    #print("Receiving: %s" % (message.payload))

    # Getting the client identifier and the switch status from the message.
    feature_name = feature_switch.FeatureSwitch.FEATURE_DATA_NAME
    if feature_name in message.payload:
        message_json = json.loads(message.payload)
        (ts, client_id, switch_status) = message_json[feature_name].split(" ")

    # Set switch status.
    if client_id == IOT_DEVICE_1_NAME:
        iot_device_1_status = SwitchStatus.ON if switch_status != "0" else SwitchStatus.OFF
        iot_device_1_act_flag = True

#
# Custom MQTT message callback for second device.
#
def iot_device_2_callback(client, userdata, message):
    global iot_device_2_act_flag, iot_device_2_status

    #print("Receiving: %s" % (message.payload))

    # Getting the client identifier and the switch status from the message.
    feature_name = feature_switch.FeatureSwitch.FEATURE_DATA_NAME
    if feature_name in message.payload:
        message_json = json.loads(message.payload)
        (ts, client_id, switch_status) = message_json[feature_name].split(" ")

    # Set switch status.
    if client_id == IOT_DEVICE_2_NAME:
        iot_device_2_status = SwitchStatus.ON if switch_status != "0" else SwitchStatus.OFF
        iot_device_2_act_flag = True

#
# Handling actuation of devices.
#
def iot_device_act(iot_device, iot_device_feature, iot_device_status, iot_device_client):

    # Writing switch status.
    iot_device.disable_notifications(iot_device_feature)
    iot_device_feature.write_switch_status(iot_device_status.value)
    iot_device.enable_notifications(iot_device_feature)

    # Updating switch shadow device's state.
    state_json_str = '{"state":{"desired":{"switch_status":' + str(iot_device_status.value) + '}}}'
    iot_device_client.update_shadow_state(state_json_str, custom_shadow_callback_update, SHADOW_CALLBACK_TIMEOUT_s)


# SHADOW DEVICES' CALLBACKS

#
# Custom shadow callback for "get()" operations.
#
def custom_shadow_callback_get(payload, response_status, token):
    # "payload" is a JSON string ready to be parsed using "json.loads()" both in
    # both Python 2.x and Python 3.x
    print("Get request with token \"" + token + "\" " + response_status)
    #if response_status == "accepted":
    #    state_json_str = json.loads(payload)

#
# Custom shadow callback for "update()" operations.
#
def custom_shadow_callback_update(payload, response_status, token):
    # "payload" is a JSON string ready to be parsed using "json.loads()" both in
    # both Python 2.x and Python 3.x
    print("Update request with token \"" + token + "\" " + response_status)
    #if response_status == "accepted":
    #    state_json_str = json.loads(payload)

#
# Custom shadow callback for "delete()" operations.
#
def custom_shadow_callback_delete(payload, response_status, token):
    # "payload" is a JSON string ready to be parsed using "json.loads()" both in
    # both Python 2.x and Python 3.x
    print("Delete request with token \"" + token + "\" " + response_status)
    #if response_status == "accepted":
    #    state_json_str = json.loads(payload)


# MAIN APPLICATION

#
# Main application.
#
def main(argv):

    # Global variables.
    global endpoint, root_ca_path
    global iot_device_1_client, iot_device_2_client
    global iot_device_1, iot_device_2
    global iot_device_1_feature_switch, iot_device_2_feature_switch
    global iot_device_1_status, iot_device_2_status
    global iot_device_1_act_flag, iot_device_2_act_flag

    # Initial state.
    iot_device_1_status = SwitchStatus.OFF
    iot_device_2_status = SwitchStatus.OFF
    iot_device_1_act_flag = False
    iot_device_2_act_flag = False

    # Configure logging.
    configure_logging()

    # Printing intro.
    print_intro()

    # Reading input.
    read_input(argv)

    try:
        # Creating Bluetooth Manager.
        manager = Manager.instance()
        manager_listener = MyManagerListener()
        manager.add_listener(manager_listener)

        # Synchronous discovery of Bluetooth devices.
        print('Scanning Bluetooth devices...\n')
        # Synchronous discovery.
        #manager.discover(False, SCANNING_TIME_s)
        # Asynchronous discovery.
        manager.start_discovery(False, SCANNING_TIME_s)
        time.sleep(SCANNING_TIME_s)
        manager.stop_discovery()

        # Getting discovered devices.
        discovered_devices = manager.get_nodes()
        if not discovered_devices:
            print('\nNo Bluetooth devices found. Exiting...\n')
            sys.exit(0)

        # Checking discovered devices.
        devices = []
        for discovered in discovered_devices:
            if discovered.get_tag() == IOT_DEVICE_1_MAC:
                iot_device_1 = discovered
                devices.append(iot_device_1)
            elif discovered.get_tag() == IOT_DEVICE_2_MAC:
                iot_device_2 = discovered
                devices.append(iot_device_2)
            if len(devices) == 2:
                break
        if len(devices) < 2:
            print('\nBluetooth setup incomplete. Exiting...\n')
            sys.exit(0)

        # Connecting to the devices.
        for device in devices:
            device.add_listener(MyNodeListener())
            print('Connecting to %s...' % (device.get_name()))
            device.connect()
            print('Connection done.')

        # Getting features.
        print('\nGetting features...')
        iot_device_1_feature_switch = iot_device_1.get_feature(feature_switch.FeatureSwitch)
        iot_device_2_feature_switch = iot_device_2.get_feature(feature_switch.FeatureSwitch)

        # Resetting switches.
        print('Resetting switches...')
        iot_device_1_feature_switch.write_switch_status(iot_device_1_status.value)
        iot_device_2_feature_switch.write_switch_status(iot_device_2_status.value)

        # Bluetooth setup complete.
        print('\nBluetooth setup complete.')

        # Initializing Edge Computing.
        print('\nInitializing Edge Computing...\n')
        edge = AWSGreengrass(endpoint, root_ca_path)

        # Getting AWS MQTT clients.
        iot_device_1_client = edge.get_client(IOT_DEVICE_1_NAME, IOT_DEVICE_1_CERTIF_PATH, IOT_DEVICE_1_PRIV_K_PATH)
        iot_device_2_client = edge.get_client(IOT_DEVICE_2_NAME, IOT_DEVICE_2_CERTIF_PATH, IOT_DEVICE_2_PRIV_K_PATH)

        # Connecting clients to the cloud.
        iot_device_1_client.connect()
        iot_device_2_client.connect()

        # Setting subscriptions.
        iot_device_1_client.subscribe(MQTT_IOT_DEVICE_SWITCH_ACT_TOPIC, MQTT_QOS_1, iot_device_1_callback)
        iot_device_2_client.subscribe(MQTT_IOT_DEVICE_SWITCH_ACT_TOPIC, MQTT_QOS_1, iot_device_2_callback)

        # Resetting shadow states.
        state_json_str = '{"state":{"desired":{"switch_status":' + str(iot_device_1_status.value) + '}}}'
        iot_device_1_client.update_shadow_state(state_json_str, custom_shadow_callback_update, SHADOW_CALLBACK_TIMEOUT_s)
        state_json_str = '{"state":{"desired":{"switch_status":' + str(iot_device_2_status.value) + '}}}'
        iot_device_2_client.update_shadow_state(state_json_str, custom_shadow_callback_update, SHADOW_CALLBACK_TIMEOUT_s)

        # Edge Computing Initialized.
        print('\nEdge Computing Initialized.')

        # Handling sensing of devices.
        iot_device_1_feature_switch.add_listener(MyFeatureSwitchListener(iot_device_1_client, MQTT_IOT_DEVICE_SWITCH_SENSE_TOPIC))
        iot_device_2_feature_switch.add_listener(MyFeatureSwitchListener(iot_device_2_client, MQTT_IOT_DEVICE_SWITCH_SENSE_TOPIC))

        # Enabling notifications.
        print('\nEnabling Bluetooth notifications...')
        iot_device_1.enable_notifications(iot_device_1_feature_switch)
        iot_device_2.enable_notifications(iot_device_2_feature_switch)

        # Demo running.
        print('\nDemo running (\"CTRL+C\" to quit)...\n')

        # Infinite loop.
        while True:

            # Getting notifications.
            if iot_device_1.wait_for_notifications(0.05) or iot_device_2.wait_for_notifications(0.05):
                continue

            # Handling actuation of devices.
            if iot_device_1_act_flag:
                iot_device_act(iot_device_1, iot_device_1_feature_switch, iot_device_1_status, iot_device_1_client)
                iot_device_1_act_flag = False
            elif iot_device_2_act_flag:
                iot_device_act(iot_device_2, iot_device_2_feature_switch, iot_device_2_status, iot_device_2_client)
                iot_device_2_act_flag = False

    except InvalidOperationException as e:
        print(e)
    except BTLEException as e:
        print(e)
        print('Exiting...\n')
        sys.exit(0)
    except WrongInstantiationException as e:
        print(e)
        print('Exiting...\n')
        sys.exit(0)
    except KeyboardInterrupt:
        try:
            # Exiting.
            print('\nExiting...\n')
            sys.exit(0)
        except SystemExit:
            os._exit(0)


if __name__ == "__main__":

    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
