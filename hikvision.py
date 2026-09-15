"""Hikvision ISAPI attendance client for devices reachable on the local LAN."""

import datetime as dt

import requests
from requests.auth import HTTPDigestAuth


class HikvisionConnectionError(RuntimeError):
    """Raised when a Hikvision device cannot be contacted or rejects a request."""


class HikvisionAttendanceClient:
    def __init__(self, host, username, password, port=80, use_https=False, timeout=15,
                 major=5, minor=0, session=None):
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port)
        self.use_https = use_https
        self.timeout = int(timeout)
        self.major = int(major)
        self.minor = int(minor)
        self.session = session or requests.Session()
        self.skipped_event_count = 0

    def test_connection(self):
        """Verify ISAPI access without changing any device data."""
        response = self._get("/ISAPI/System/deviceInfo?format=json")
        self._raise_for_status(response, "test the Hikvision connection")
        try:
            return response.json()
        except ValueError:
            return {"response": response.text}

    def fetch_events(self, start_time, end_time):
        """Return normalized attendance events for the requested inclusive time range."""
        records = []
        position = 0
        page_size = 100

        while True:
            response = self._post_events(start_time, end_time, position, page_size)
            self._raise_for_status(response, "fetch attendance events")
            result = response.json().get("AcsEvent", {})
            page = result.get("InfoList", [])
            records.extend(page)
            position += len(page)

            if not page or position >= int(result.get("totalMatches", 0)):
                break

        events = []
        for index, record in enumerate(records):
            try:
                event = self._normalize_event(record, index)
            except ValueError:
                event = None
            if event:
                events.append(event)
            else:
                self.skipped_event_count += 1
        return sorted(events, key=lambda event: event["timestamp"])

    def _url(self, path):
        scheme = "https" if self.use_https else "http"
        default_port = 443 if self.use_https else 80
        port = "" if self.port == default_port else f":{self.port}"
        return f"{scheme}://{self.host}{port}{path}"

    def _get(self, path):
        try:
            return self.session.get(self._url(path), auth=self._auth(), timeout=self.timeout)
        except requests.RequestException as error:
            raise HikvisionConnectionError(f"Unable to contact Hikvision device: {error}") from error

    def _post_events(self, start_time, end_time, position, page_size):
        payload = {"AcsEventCond": {
            "searchID": "erpnext-attendance-sync",
            "searchResultPosition": position,
            "maxResults": page_size,
            "major": self.major,
            "minor": self.minor,
            "startTime": self._format_time(start_time),
            "endTime": self._format_time(end_time),
        }}
        try:
            return self.session.post(
                self._url("/ISAPI/AccessControl/AcsEvent?format=json"),
                json=payload, auth=self._auth(), timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise HikvisionConnectionError(f"Unable to fetch attendance events: {error}") from error

    def _auth(self):
        return HTTPDigestAuth(self.username, self.password)

    @staticmethod
    def _format_time(value):
        if isinstance(value, str):
            value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _normalize_event(record, index):
        employee_id = record.get("employeeNoString")
        event_time = record.get("time")
        if not employee_id or not event_time:
            return None

        timestamp = dt.datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        if timestamp.tzinfo:
            timestamp = timestamp.astimezone().replace(tzinfo=None)

        return {
            "uid": record.get("serialNo", index),
            "user_id": str(employee_id),
            "timestamp": timestamp,
            "punch": record.get("minor", 0),
            "status": record.get("major", 0),
            "raw": record,
        }

    @staticmethod
    def _raise_for_status(response, action):
        try:
            response.raise_for_status()
        except requests.RequestException as error:
            raise HikvisionConnectionError(f"Unable to {action}: {error}") from error
