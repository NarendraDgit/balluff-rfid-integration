import csv
import json
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request

# ============================================================
# CONFIGURATION
# ============================================================

DEVICE_IP = "192.168.72.223"

USERNAME = "admin"

# Password environment variable 
PASSWORD = os.getenv("BALLUFF_PASSWORD")

HEAD_ALIAS = "Head_1"

# MQTT
MQTT_BROKER = "localhost"
MQTT_PORT = 1883

DEVICE_SERIAL = "CN00980657400941"

MQTT_TOPIC = (
    f"balluff/{DEVICE_SERIAL}/"
    f"rfid/heads/{HEAD_ALIAS}/tagdata"
)

# Files
#BASE_DIR = Path("/home/narendra/Music/RW_Phase1")
#BASE_DIR = Path("C:\\Users\\ndrdd\\RW_Phase1")
BASE_DIR = Path(r"C:\Users\ndrdd\RW_Phase1")

CSV_FILE = BASE_DIR / "rfid_events.csv"

# Balluff REST API
API_BASE = (
    f"http://{DEVICE_IP}/api/balluff/v2"
)

# Local REST API
REST_HOST = "127.0.0.1"
REST_PORT = 5000


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


# ============================================================
# BALLUFF WRITER
# ============================================================

class BalluffWriter:

    def __init__(self):

        self.session = requests.Session()

        self.token = None

        # Multiple write requests एकाच वेळी होऊ नयेत
        self.write_lock = threading.Lock()

    # --------------------------------------------------------
    # LOGIN
    # --------------------------------------------------------

    def login(self):

        if not PASSWORD:
            logging.error(
                "BALLUFF_PASSWORD environment variable is not set"
            )
            return False

        url = (
            f"{API_BASE}/users/login"
        )

        payload = {
            "username": USERNAME,
            "password": PASSWORD
        }

        try:

            logging.info(
                "Logging in to Balluff..."
            )

            response = self.session.post(
                url,
                json=payload,
                timeout=5
            )

            response.raise_for_status()

            data = response.json()

            # Balluff API bearer field return करते
            self.token = data.get("bearer")

            if not self.token:

                logging.error(
                    "Login successful but bearer token not found"
                )

                return False

            logging.info(
                "Balluff login successful"
            )

            return True

        except requests.RequestException as e:

            logging.error(
                f"Balluff login failed: {e}"
            )

            return False

        except Exception as e:

            logging.exception(
                f"Unexpected login error: {e}"
            )

            return False

    # --------------------------------------------------------
    # WRITE RFID TAG
    # --------------------------------------------------------

    def write_tag_data(
        self,
        start_address,
        data
    ):

        with self.write_lock:

            # --------------------------------------------
            # Validate start address
            # --------------------------------------------

            try:

                start_address = int(
                    start_address
                )

            except (ValueError, TypeError):

                return {
                    "status": "ERROR",
                    "message": "Invalid start_address"
                }

            if start_address < 0:

                return {
                    "status": "ERROR",
                    "message": "start_address cannot be negative"
                }

            # --------------------------------------------
            # Validate HEX data
            # --------------------------------------------

            if not isinstance(data, str):

                return {
                    "status": "ERROR",
                    "message": "data must be a HEX string"
                }

            data = data.strip().upper()

            if not data:

                return {
                    "status": "ERROR",
                    "message": "data cannot be empty"
                }

            # HEX length must be even
            if len(data) % 2 != 0:

                return {
                    "status": "ERROR",
                    "message": (
                        "HEX data must contain "
                        "an even number of characters"
                    )
                }

            # Check valid HEX
            try:

                bytes.fromhex(data)

            except ValueError:

                return {
                    "status": "ERROR",
                    "message": "Invalid HEX data"
                }

            number_of_bytes = len(data) // 2

            # --------------------------------------------
            # Login if token doesn't exist
            # --------------------------------------------

            if not self.token:

                if not self.login():

                    return {
                        "status": "ERROR",
                        "message": "Balluff login failed"
                    }

            # --------------------------------------------
            # Write URL
            # --------------------------------------------

            url = (
                f"{API_BASE}/rfid/heads/"
                f"{HEAD_ALIAS}/write"
            )

            # --------------------------------------------
            # Balluff write payload
            # --------------------------------------------

            payload = {

                "command": "WRITE_TAG_DATA",

                "bank": "USER_DATA",

                "startAddress": start_address,

                "numberOfBytes": number_of_bytes,

                "format": "HEX_ASCII",

                "data": data
            }

            # --------------------------------------------
            # HTTP headers
            # --------------------------------------------

            headers = {

                "Authorization": (
                    f"Bearer {self.token}"
                ),

                "Content-Type": "application/json"
            }

            try:

                logging.info(
                    f"RFID WRITE | "
                    f"Address={start_address} | "
                    f"Bytes={number_of_bytes} | "
                    f"Data={data}"
                )

                response = self.session.post(

                    url,

                    json=payload,

                    headers=headers,

                    timeout=10
                )

                # ----------------------------------------
                # Token expired
                # ----------------------------------------

                if response.status_code == 401:

                    logging.warning(
                        "Balluff token expired. "
                        "Logging in again..."
                    )

                    self.token = None

                    if not self.login():

                        return {
                            "status": "ERROR",
                            "message": (
                                "Balluff re-login failed"
                            )
                        }

                    headers["Authorization"] = (
                        f"Bearer {self.token}"
                    )

                    response = self.session.post(

                        url,

                        json=payload,

                        headers=headers,

                        timeout=10
                    )

                # ----------------------------------------
                # HTTP error
                # ----------------------------------------

                response.raise_for_status()

                balluff_response = response.json()

                # ----------------------------------------
                # Check Balluff response
                # ----------------------------------------

                status = balluff_response.get(
                    "status",
                    {}
                )

                status_code = status.get(
                    "code"
                )

                if status_code == 0:

                    logging.info(
                        "RFID WRITE SUCCESS"
                    )

                    return {

                        "status": "OK",

                        "message": (
                            "RFID write successful"
                        ),

                        "start_address": (
                            start_address
                        ),

                        "number_of_bytes": (
                            number_of_bytes
                        ),

                        "data": data,

                        "balluff_response": (
                            balluff_response
                        )
                    }

                # Balluff returned an error
                logging.error(
                    f"RFID WRITE FAILED: "
                    f"{balluff_response}"
                )

                return {

                    "status": "ERROR",

                    "message": (
                        "Balluff RFID write failed"
                    ),

                    "balluff_response": (
                        balluff_response
                    )
                }

            except requests.RequestException as e:

                logging.error(
                    f"RFID write HTTP error: {e}"
                )

                return {

                    "status": "ERROR",

                    "message": (
                        f"RFID write HTTP error: {e}"
                    )
                }

            except Exception as e:

                logging.exception(
                    "Unexpected RFID write error"
                )

                return {

                    "status": "ERROR",

                    "message": str(e)
                }


# ============================================================
# CSV LOGGER
# ============================================================

class CsvLogger:

    FIELDNAMES = [

        "timestamp",

        "received_timestamp",

        "event",

        "port_number",

        "tag_uid",

        "tag_epc",

        "tag_tid",

        "tag_type",

        "tag_user_data",

        "rssi"
    ]

    def __init__(self):

        self.lock = threading.Lock()

        # Directory ensure करा
        CSV_FILE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

    # --------------------------------------------------------
    # LOG RFID DATA
    # --------------------------------------------------------

    def log(self, data):

        with self.lock:

            file_exists = (
                CSV_FILE.exists()
                and CSV_FILE.stat().st_size > 0
            )

            try:

                with open(
                    CSV_FILE,
                    "a",
                    newline="",
                    encoding="utf-8"
                ) as file:

                    writer = csv.DictWriter(

                        file,

                        fieldnames=self.FIELDNAMES
                    )

                    if not file_exists:

                        writer.writeheader()

                    writer.writerow(data)

                logging.info(
                    f"CSV: "
                    f"{data.get('event')} | "
                    f"UID={data.get('tag_uid')} | "
                    f"DATA={data.get('tag_user_data')}"
                )

            except Exception as e:

                logging.exception(
                    f"CSV logging failed: {e}"
                )


# ============================================================
# MQTT RFID READER
# ============================================================

class MqttReader:

    def __init__(
        self,
        broker,
        port,
        topic,
        csv_logger
    ):

        self.broker = broker

        self.port = port

        self.topic = topic

        self.csv_logger = csv_logger

        # --------------------------------------------
        # Latest RFID data
        # --------------------------------------------

        self.latest_tag_data = None

        self.latest_lock = threading.Lock()

        # --------------------------------------------
        # MQTT Client
        # --------------------------------------------

        self.client = mqtt.Client(

            callback_api_version=(
                mqtt.CallbackAPIVersion.VERSION2
            )
        )

        self.client.on_connect = (
            self.on_connect
        )

        self.client.on_message = (
            self.on_message
        )

        self.client.on_disconnect = (
            self.on_disconnect
        )

        self.running = False

    # --------------------------------------------------------
    # MQTT CONNECT
    # --------------------------------------------------------

    def on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties
    ):

        if reason_code == 0:

            logging.info(
                "MQTT connected"
            )

            result, mid = client.subscribe(
                self.topic
            )

            if result == mqtt.MQTT_ERR_SUCCESS:

                logging.info(
                    f"MQTT subscribed: "
                    f"{self.topic}"
                )

            else:

                logging.error(
                    f"MQTT subscribe failed: "
                    f"{result}"
                )

        else:

            logging.error(
                f"MQTT connection failed: "
                f"{reason_code}"
            )

    # --------------------------------------------------------
    # MQTT DISCONNECT
    # --------------------------------------------------------

    def on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties
    ):

        logging.warning(
            f"MQTT disconnected: "
            f"{reason_code}"
        )

    # --------------------------------------------------------
    # MQTT MESSAGE
    # --------------------------------------------------------

    def on_message(
        self,
        client,
        userdata,
        msg
    ):

        try:

            # ----------------------------------------
            # Decode JSON
            # ----------------------------------------

            payload = json.loads(
                msg.payload.decode(
                    "utf-8"
                )
            )

            data = payload.get(
                "data",
                {}
            )

            # ----------------------------------------
            # RFID event
            # ----------------------------------------

            event = data.get(
                "tagEvent",
                ""
            )

            # आपल्याला फक्त COMING / LEAVING पाहिजे
            if event not in (
                "COMING",
                "LEAVING"
            ):

                return

            # ----------------------------------------
            # Prepare RFID data
            # ----------------------------------------

            tag_data = {

                "timestamp": data.get(
                    "timestamp"
                ),

                # PC वर receive झालेली actual time
                "received_timestamp": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),

                "event": event,

                "port_number": data.get(
                    "portNumber"
                ),

                "tag_uid": data.get(
                    "tagUid"
                ),

                "tag_epc": data.get(
                    "tagEpc"
                ),

                "tag_tid": data.get(
                    "tagTid"
                ),

                "tag_type": data.get(
                    "tagType"
                ),

                "tag_user_data": data.get(
                    "tagUserData"
                ),

                "rssi": data.get(
                    "rssi"
                )
            }

            # ----------------------------------------
            # Save latest data
            # ----------------------------------------

            with self.latest_lock:

                self.latest_tag_data = (
                    tag_data.copy()
                )

            # ----------------------------------------
            # Console log
            # ----------------------------------------

            logging.info(

                f"TAG {event} | "
                f"UID={tag_data['tag_uid']} | "
                f"TYPE={tag_data['tag_type']} | "
                f"DATA={tag_data['tag_user_data']}"
            )

            # ----------------------------------------
            # CSV
            # ----------------------------------------

            self.csv_logger.log(
                tag_data
            )

        except json.JSONDecodeError:

            logging.error(
                "MQTT payload is not valid JSON"
            )

        except Exception as e:

            logging.exception(
                f"MQTT message processing error: {e}"
            )

    # --------------------------------------------------------
    # GET LATEST RFID DATA
    # --------------------------------------------------------

    def get_latest(self):

        with self.latest_lock:

            if self.latest_tag_data is None:

                return None

            return self.latest_tag_data.copy()

    # --------------------------------------------------------
    # START MQTT
    # --------------------------------------------------------

    def start(self):

        try:

            logging.info(
                f"Connecting to MQTT broker "
                f"{self.broker}:{self.port}"
            )

            self.client.connect(

                self.broker,

                self.port,

                keepalive=60
            )

            self.running = True

            # Background MQTT network loop
            self.client.loop_start()

            logging.info(
                "MQTT reader started"
            )

            return True

        except Exception as e:

            logging.exception(
                f"MQTT start failed: {e}"
            )

            return False

    # --------------------------------------------------------
    # STOP MQTT
    # --------------------------------------------------------

    def stop(self):

        self.running = False

        try:

            self.client.loop_stop()

            self.client.disconnect()

        except Exception:

            pass

        logging.info(
            "MQTT reader stopped"
        )


# ============================================================
# RFID SERVICE
# ============================================================

class RfidService:

    def __init__(self):

        self.csv_logger = (
            CsvLogger()
        )

        self.mqtt_reader = MqttReader(

            MQTT_BROKER,

            MQTT_PORT,

            MQTT_TOPIC,

            self.csv_logger
        )

        self.balluff_writer = (
            BalluffWriter()
        )

        self.running = False

    # --------------------------------------------------------
    # START SERVICE
    # --------------------------------------------------------

    def start(self):

        logging.info(
            "Starting RFID service..."
        )

        # Password check
        if not PASSWORD:

            logging.error(
                "BALLUFF_PASSWORD is not set"
            )

            return False

        # Start MQTT
        if not self.mqtt_reader.start():

            logging.error(
                "MQTT reader could not start"
            )

            return False

        self.running = True

        logging.info(
            "RFID service started successfully"
        )

        return True

    # --------------------------------------------------------
    # WRITE RFID
    # --------------------------------------------------------

    def write(
        self,
        start_address,
        data
    ):

        return self.balluff_writer.write_tag_data(

            start_address,

            data
        )

    # --------------------------------------------------------
    # STOP SERVICE
    # --------------------------------------------------------

    def stop(self):

        logging.info(
            "Stopping RFID service..."
        )

        self.mqtt_reader.stop()

        self.running = False

        logging.info(
            "RFID service stopped"
        )


# ============================================================
# FLASK REST API
# ============================================================

app = Flask(__name__)

service = RfidService()


# ============================================================
# ROOT
# ============================================================

@app.route("/", methods=["GET"])
def root():

    return jsonify({

        "service": (
            "running"
            if service.running
            else "stopped"
        ),

        "mqtt": "READ",

        "rest": "WRITE",

        "endpoints": {

            "health": "/health",

            "latest": "/rfid/latest",

            "write": "/rfid/write"
        }
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/health", methods=["GET"])
def health():

    mqtt_status = (
        "running"
        if service.mqtt_reader.running
        else "stopped"
    )

    service_status = (
        "running"
        if service.running
        else "stopped"
    )

    overall_status = (
        "OK"
        if service.running
        and service.mqtt_reader.running
        else "ERROR"
    )

    return jsonify({

        "status": overall_status,

        "service": service_status,

        "mqtt": mqtt_status
    })


# ============================================================
# GET LATEST RFID DATA
# ============================================================

@app.route(
    "/rfid/latest",
    methods=["GET"]
)
def rfid_latest():

    data = (
        service.mqtt_reader.get_latest()
    )

    # अजून RFID tag आलेला नाही
    if data is None:

        return jsonify({

            "status": "NO_TAG",

            "data": None
        })

    return jsonify({

        "status": "OK",

        "data": data
    })


# ============================================================
# RFID WRITE
# ============================================================

@app.route(
    "/rfid/write",
    methods=["POST"]
)
def rfid_write():

    try:

        # --------------------------------------------
        # JSON body
        # --------------------------------------------

        body = request.get_json(
            silent=True
        )

        if body is None:

            return jsonify({

                "status": "ERROR",

                "message": (
                    "Request body must be JSON"
                )

            }), 400

        # --------------------------------------------
        # Get parameters
        # --------------------------------------------

        start_address = body.get(
            "start_address"
        )

        data = body.get(
            "data"
        )

        # --------------------------------------------
        # Validate
        # --------------------------------------------

        if start_address is None:

            return jsonify({

                "status": "ERROR",

                "message": (
                    "start_address is required"
                )

            }), 400

        if data is None:

            return jsonify({

                "status": "ERROR",

                "message": (
                    "data is required"
                )

            }), 400

        # --------------------------------------------
        # Perform RFID write
        # --------------------------------------------

        result = service.write(

            start_address,

            data
        )

        # --------------------------------------------
        # HTTP status
        # --------------------------------------------

        if result.get("status") == "OK":

            return jsonify(result), 200

        return jsonify(result), 500

    except Exception as e:

        logging.exception(
            "REST RFID write error"
        )

        return jsonify({

            "status": "ERROR",

            "message": str(e)

        }), 500


# ============================================================
# REST SERVER THREAD
# ============================================================

def start_rest_server():

    logging.info(
        f"REST API starting on "
        f"http://{REST_HOST}:{REST_PORT}"
    )

    app.run(

        host=REST_HOST,

        port=REST_PORT,

        threaded=True,

        use_reloader=False
    )


# ============================================================
# SIGNAL HANDLER
# ============================================================

def signal_handler(
    signum,
    frame
):

    logging.info(
        "Shutdown signal received"
    )

    service.stop()

    raise SystemExit(0)


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------
    # Signal handlers
    # --------------------------------------------

    signal.signal(
        signal.SIGINT,
        signal_handler
    )

    signal.signal(
        signal.SIGTERM,
        signal_handler
    )

    # --------------------------------------------
    # Start RFID service
    # --------------------------------------------

    if not service.start():

        logging.error(
            "RFID service failed to start"
        )

        return

    # --------------------------------------------
    # Start REST server
    # --------------------------------------------

    rest_thread = threading.Thread(

        target=start_rest_server,

        daemon=True
    )

    rest_thread.start()

    # --------------------------------------------
    # Keep main process alive
    # --------------------------------------------

    logging.info(
        "RFID service is running in background"
    )

    logging.info(
        "MQTT READ  : ENABLED"
    )

    logging.info(
        "REST WRITE : ENABLED"
    )

    logging.info(
        "REST LATEST: ENABLED"
    )

    logging.info(
        "Press Ctrl+C to stop"
    )

    try:

        while True:

            threading.Event().wait(1)

    except KeyboardInterrupt:

        pass

    finally:

        service.stop()


# ============================================================
# PROGRAM ENTRY
# ============================================================

if __name__ == "__main__":

    main()