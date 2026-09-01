"""A short connection test, used by the config flow.

Without this, a wrong password or a blocked port shows up only as an entity stuck at
"unavailable" and a line in a log the user may not read. Failing in the form instead is the
whole point.

Runs in an executor: paho's connect and network loop both block.
"""

from __future__ import annotations

import logging
import time

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

# Result codes worth telling apart for the user.
RESULT_OK = "ok"
RESULT_AUTH = "invalid_auth"
RESULT_CANNOT_CONNECT = "cannot_connect"

# MQTT 3.1.1 CONNACK 4 and 5 are bad credentials and not authorised; MQTT 5 uses 134 and
# 135 for the same two cases.
_AUTH_CODES = {4, 5, 134, 135}


def test_connection(
    broker: str,
    port: int,
    client_id: str,
    username: str | None = None,
    password: str | None = None,
    use_tls: bool = False,
    tls_insecure: bool = False,
    timeout: float = 8.0,
) -> str:
    """Try to connect and return one of the RESULT_ constants."""
    outcome: dict[str, int | None] = {"code": None, "dropped": False}

    def _on_connect(client, userdata, flags, reason_code, properties=None):
        # paho 2 hands over a ReasonCode object, not an int, and int() on it raises.
        # The numeric code lives in .value; older paho passes a plain int.
        value = getattr(reason_code, "value", reason_code)
        try:
            outcome["code"] = int(value)
        except (TypeError, ValueError):
            outcome["code"] = 0 if reason_code == 0 else 1

    def _on_disconnect(client, userdata, *args):
        # Some brokers, Mosquitto included, answer bad credentials by closing the socket
        # rather than sending a CONNACK. Losing the connection before any CONNACK arrives
        # is therefore the signal, and it is the only one available.
        if outcome["code"] is None:
            outcome["dropped"] = True

    # A distinct suffix so a test never collides with the live client's session.
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2, client_id=f"{client_id}-probe"
    )
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    if username:
        client.username_pw_set(username, password or None)
    if use_tls:
        client.tls_set()
        if tls_insecure:
            client.tls_insecure_set(True)

    try:
        client.connect(broker, port, keepalive=10)
    except Exception as err:  # DNS, refused, TLS handshake, wrong port
        _LOGGER.debug("Connection test to %s:%s failed: %s", broker, port, err)
        return RESULT_CANNOT_CONNECT

    client.loop_start()
    deadline = time.monotonic() + timeout
    while outcome["code"] is None and not outcome["dropped"] and time.monotonic() < deadline:
        time.sleep(0.1)
    client.loop_stop()
    try:
        client.disconnect()
    except Exception:  # nothing useful to do if the teardown fails
        pass

    code = outcome["code"]
    if code == 0:
        return RESULT_OK
    if code in _AUTH_CODES:
        return RESULT_AUTH
    if code is None and outcome["dropped"]:
        # Dropped before any CONNACK. With credentials supplied that is almost always a
        # rejection; without them it is a broker that requires them.
        return RESULT_AUTH
    _LOGGER.debug("Connection test to %s:%s gave reason code %s", broker, port, code)
    return RESULT_CANNOT_CONNECT
