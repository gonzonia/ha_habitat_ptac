"""MQTT client for Habitat PTAC integration."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import ssl
import threading
import urllib.parse
import uuid
from datetime import datetime
from typing import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

from .auth import AWSCredentials
from .const import (
    AWS_REGION,
    IOT_ENDPOINT,
    SHADOW_ACCEPTED_TOPIC,
    SHADOW_UPDATE_TOPIC,
    SHADOW_GET_TOPIC,          
    SHADOW_GET_ACCEPTED_TOPIC, 
    THERMOSTAT_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SigV4 signing helpers
# ---------------------------------------------------------------------------

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = _sign(("AWS4" + key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _create_iot_websocket_path(credentials: AWSCredentials) -> str:
    """Return a SigV4-signed path for paho-mqtt's ws_set_options."""
    service = "iotdevicegateway"
    algorithm = "AWS4-HMAC-SHA256"
    region = AWS_REGION

    now = datetime.utcnow()
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    credential_scope = f"{datestamp}/{region}/{service}/aws4_request"

    # Security-Token is NOT included in the canonical query string for signing.
    # It matches the order observed in the app: ...SignedHeaders=host&Signature=...&Security-Token=...
    params = {
        "X-Amz-Algorithm": algorithm,
        "X-Amz-Credential": f"{credentials.access_key_id}/{credential_scope}",
        "X-Amz-Date": amzdate,
        "X-Amz-SignedHeaders": "host",
    }

    canonical_querystring = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for k, v in sorted(params.items())
    )

    canonical_headers = f"host:{IOT_ENDPOINT}\n"
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical_request = "\n".join([
        "GET",
        "/mqtt",
        canonical_querystring,
        canonical_headers,
        "host",
        payload_hash,
    ])

    string_to_sign = "\n".join([
        algorithm,
        amzdate,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _get_signature_key(
        credentials.secret_key, datestamp, region, service
    )
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    # Append signature first, then Security-Token (matches app behavior)
    encoded_token = urllib.parse.quote(credentials.session_token, safe="")

    return f"/mqtt?{canonical_querystring}&X-Amz-Signature={signature}&X-Amz-Security-Token={encoded_token}"


# ---------------------------------------------------------------------------
# MQTT client
# ---------------------------------------------------------------------------

class HabitatMQTTClient:
    """Manages the MQTT connection to AWS IoT Core."""

    def __init__(
        self,
        thing_names: list[str],
        state_callback: Callable[[str, dict], None],
        identity_id: str,
        gateway_id: str,
    ) -> None:
        self._thing_names = thing_names
        self._state_callback = state_callback
        self._identity_id = identity_id
        self._gateway_id = gateway_id
        self._client: mqtt.Client | None = None
        self._connected = False
        self._lock = threading.Lock()
        
        self._device_states: dict[str, dict] = {}
        
        self._initial_gets_published = set()
    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(self, credentials: AWSCredentials) -> None:
        """Connect to AWS IoT Core with the given credentials."""
        self._start_client(credentials)

    def disconnect(self) -> None:
        """Disconnect and clean up."""
        with self._lock:
            if self._client:
                self._client.disconnect()
                self._client = None
            self._connected = False

    def reconnect(self, credentials: AWSCredentials) -> None:
        """Reconnect with fresh credentials (called on token refresh)."""
        self.disconnect()
        self._start_client(credentials)

    def publish_command(self, thing_name: str, properties: dict) -> None:
        """Publish a command to the device shadow."""
        topic = SHADOW_UPDATE_TOPIC.format(thing_name)
        
        # Inject a refresh ping (using the current time) to force the PTAC to wake up
        import time
        properties["ep0:sPTAC868:SetRefresh"] = str(int(time.time() * 1000))[-6:]
        
        # AWS IoT requires commands to be wrapped in state -> desired
        payload = {
            "state": {
                "desired": {
                    THERMOSTAT_ENDPOINT: {
                        "properties": properties
                    }
                }
            }
        }
        
        if self._client and self._connected:
            self._client.publish(topic, json.dumps(payload), qos=1)
            _LOGGER.debug("Published command to %s: %s", topic, payload)
        else:
            _LOGGER.error("Cannot publish, MQTT client not connected")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_client(self, credentials: AWSCredentials) -> None:
        """Build and start the paho-mqtt client in a background thread."""
        ws_path = _create_iot_websocket_path(credentials)
        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION1,
            client_id=f"{self._gateway_id}-ha-{uuid.uuid4().hex[:8]}",
            clean_session=True,
            transport="websockets",
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=False,
        )
        client.tls_set_context(ssl.create_default_context())
        client.ws_set_options(path=ws_path)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe

        

        with self._lock:
            self._client = client

        thread = threading.Thread(target=self._run, daemon=True, name="habitat_ptac_mqtt")
        thread.start()

    def _run(self) -> None:
        """Connect and block on the MQTT loop — runs in its own daemon thread."""
        try:
            with self._lock:
                client = self._client
            if client:
                _LOGGER.debug(
                    "Connecting to %s:443 with client_id=%s",
                    IOT_ENDPOINT,
                    self._gateway_id,
                )
                client.connect(IOT_ENDPOINT, port=443, keepalive=60)
                client.loop_forever()
        except Exception as err:
            _LOGGER.error("MQTT error: %s", err)
    
    def _on_connect(self, client, userdata, flags, rc) -> None:
        _LOGGER.debug("on_connect called with rc=%d", rc)
        if rc == 0:
            self._connected = True
            _LOGGER.info("Connected to AWS IoT Core")
            
            # Reset the tracking set in case this is a dropped connection reconnecting
            self._initial_gets_published.clear() 
            
            for thing_name in self._thing_names:
                update_topic = SHADOW_ACCEPTED_TOPIC.format(thing_name)
                get_topic = SHADOW_GET_ACCEPTED_TOPIC.format(thing_name)
                
                client.subscribe([(update_topic, 1), (get_topic, 1)])
                _LOGGER.debug("Subscribed to %s and %s", update_topic, get_topic)
        else:
            _LOGGER.error("MQTT connection refused with rc=%d", rc)
            
    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            thing_name = msg.topic.split("/")[2]

            _LOGGER.debug("RAW MQTT MESSAGE on %s: %s", msg.topic, payload)

            state = payload.get("state", {})
            reported = state.get("reported", {})
            
            data = reported.get(THERMOSTAT_ENDPOINT, {})
            properties = data.get("properties", {})

            if properties:
                # 1. Grab everything we already know about the device from memory
                previous_state = self._device_states.get(thing_name, {})
                
                # 2. Inject statuses (fallback to previous state if AWS didn't send them this time)
                properties["_cloud_conn"] = reported.get("cloud_conn", previous_state.get("_cloud_conn", 1))
                properties["_node_conn"] = reported.get("node_conn", previous_state.get("_node_conn", 1))
                properties["_connected"] = str(reported.get("connected", previous_state.get("_connected", "true"))).lower()
                properties["_model"] = data.get("model", previous_state.get("_model", "Habitat PTAC"))
                
                # 3. THE FIX: Merge the new incoming delta properties into our existing memory cache!
                previous_state.update(properties)
                
                # 4. Save the fully merged dictionary back to memory
                self._device_states[thing_name] = previous_state
                
                # 5. Send the complete dictionary to Home Assistant
                self._state_callback(thing_name, previous_state)
                
                #self._device_states[thing_name] = properties
                
                #self._state_callback(thing_name, properties)
                
        except Exception as err:
            _LOGGER.error("Error handling MQTT message: %s", err)
            
    def _on_disconnect(self, client, userdata, rc) -> None:
        self._connected = False
        if rc != 0:
            _LOGGER.warning("Unexpected MQTT disconnect (rc=%d)", rc)
            
    def get_latest_props(self, thing_name: str) -> dict:
        """Return the most recently received properties for a device."""
        return self._device_states.get(thing_name, {})
        
    def _on_subscribe(self, client, userdata, mid, granted_qos) -> None:
        """Automatically request initial state once AWS confirms the subscription."""
        for thing_name in self._thing_names:
            if thing_name not in self._initial_gets_published:
                request_topic = SHADOW_GET_TOPIC.format(thing_name)
                # Ask AWS for the data
                client.publish(request_topic, json.dumps({}))
                _LOGGER.debug("Requested initial state on %s", request_topic)
                
                # Mark it as requested so we don't spam AWS on subsequent subscriptions
                self._initial_gets_published.add(thing_name)