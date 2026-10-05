# balluff-rfid-integration
# Balluff RFID Integration

A Python-based RFID integration service for communicating with a Balluff BIS V-6107 RFID device using **MQTT** and **REST APIs**.

The service receives RFID tag events through MQTT, communicates with the Balluff RFID device through REST APIs for operations such as tag writing, and provides a foundation for integrating RFID-based station workflows with an IT7 application.

## Features

* Receive RFID tag data using MQTT
* Communicate with Balluff RFID device using REST API
* Read RFID tag events from configured RFID heads
* Write data to RFID tags through REST API
* Background RFID service for continuous device communication
* Flask-based API for RFID operations
* MQTT-based event handling
* Error handling and service health monitoring
* Support for integration with IT7 application communication flow

## Architecture

```text
                 ┌──────────────────────┐
                 │   Balluff RFID       │
                 │   BIS V-6107         │
                 └──────────┬───────────┘
                            │
                 RFID Tag / Device Data
                            │
                            ▼
                 ┌──────────────────────┐
                 │    MQTT Broker       │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │  Python RFID Service │
                 │                      │
                 │  MQTT Client         │
                 │  REST Client         │
                 │  RFID Processing     │
                 └──────────┬───────────┘
                            │
                 ┌──────────┴───────────┐
                 │                      │
                 ▼                      ▼
        ┌────────────────┐      ┌────────────────┐
        │ Flask REST API │      │   IT7 App      │
        │ RFID Operations│      │ Communication  │
        └────────────────┘      └────────────────┘
```

## Technologies

* **Python 3**
* **MQTT**
* **REST API**
* **Flask**
* **Paho MQTT**
* **HTTP/HTTPS**
* **Balluff BIS V-6107 RFID**

## RFID Communication

The application uses two communication mechanisms:

### MQTT

MQTT is used to receive RFID tag events from the Balluff system.

Example topic:

```text
balluff/<serial>/rfid/heads/Head_1/tagdata
```

When a tag is detected, the MQTT callback receives the tag information and processes the event.

### REST API

REST APIs are used for operations that require direct communication with the Balluff RFID device.

For example, RFID tag data can be written using the application's REST endpoint:

```text
POST /rfid/write
```

Example request:

```json
{
    "start_address": 0,
    "data": "1122334455667788"
}
```

## Application Flow

The basic RFID workflow is:

```text
RFID Tag Detected
       │
       ▼
 MQTT Tag Event
       │
       ▼
Python RFID Service
       │
       ▼
Process RFID Data
       │
       ▼
Application Workflow
```

For the IT7 integration workflow:

```text
WAIT_FOR_PART
      │
      ▼
RFID_READ
      │
      ▼
PROCESS_RFID_DATA
      │
      ▼
OpenBatchList
      │
      ▼
IT7 OpenBatchList_OK
      │
      ▼
Operator Selects Batch
      │
      ▼
IT7 BatchLoaded
      │
      ▼
SetAuxData
```

## Project Structure

```text
balluff-rfid-integration/
│
├── rfid_service.py
├── requirements.txt
├── README.md
│
├── config/
│
├── api/
│
└── ...
```

> The project structure may evolve as additional RFID operations and IT7 integration features are added.

## Configuration

The application requires the Balluff device configuration and MQTT broker details.

Sensitive credentials should be provided through environment variables instead of being stored directly in the source code.

Example:

```bash
export BALLUFF_PASSWORD="your_password"
```

Other configuration values such as:

* Balluff device IP
* MQTT broker address
* MQTT port
* RFID device serial number
* RFID head/topic

can be configured according to the deployment environment.

## Running the Service

Create and activate a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Set required environment variables:

```bash
export BALLUFF_PASSWORD="your_password"
```

Start the RFID service:

```bash
python3 rfid_service.py
```

## Health Check

The service provides a health endpoint to verify that the application is running.

```text
GET /health
```

Example response:

```json
{
    "status": "ok"
}
```

## Security

* Do not commit passwords, API credentials, or device credentials.
* Use environment variables for sensitive configuration.
* Add `.env` and other credential files to `.gitignore`.
* Replace real device IP addresses and credentials with example values before publishing the repository.

## Purpose

This project demonstrates an industrial RFID communication service built using Python, MQTT, and REST APIs.

It is designed to act as a communication layer between RFID hardware, application-level workflows, and an IT7-based production environment.
