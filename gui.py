"""Modern desktop dashboard for the attendance sync service."""

import json
import os
import socket
import subprocess
import sys

import requests
from PyQt5 import QtCore
from PyQt5.QtGui import QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QMenu, QSpinBox, QSplashScreen, QStackedWidget, QSystemTrayIcon, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from hikvision import HikvisionAttendanceClient, HikvisionConnectionError
from config_manager import config_path, load_config

config = load_config()


STYLE = """
QMainWindow,QDialog,QMessageBox { background:#07101f; color:#eaf0ff; } QWidget { font-family:Segoe UI; font-size:13px; color:#eaf0ff; }
QFrame#sidebar { background:#050b16; border-right:1px solid #16243b; } QFrame#card { background:#0c1728; border:1px solid #1a2a45; border-radius:10px; }
QLabel#eyebrow { color:#77a5ff; font-size:11px; font-weight:700; } QLabel#title { font-size:26px; font-weight:600; } QLabel#muted { color:#95a7c3; }
QLineEdit,QSpinBox,QComboBox,QPlainTextEdit,QTableWidget { background:#07101f; border:1px solid #28405f; border-radius:6px; padding:7px; }
QPushButton { background:#1268ff; border:0; border-radius:6px; padding:8px 13px; font-weight:600; } QPushButton:hover { background:#2b7cff; } QPushButton:disabled { background:#33445e; }
QPushButton#nav { background:transparent; color:#9aabc6; text-align:left; padding:10px 14px; } QPushButton#nav:checked { background:#102b55; color:white; border-left:3px solid #4f8cff; }
QHeaderView::section { background:#102039; color:#aebfdb; border:0; padding:8px; font-weight:600; } QTableWidget { gridline-color:#1b2d48; selection-background-color:#173762; }
QMessageBox QLabel,QDialog QLabel { color:#eaf0ff; } QMenu { background:#0c1728; border:1px solid #28405f; } QMenu::item { padding:8px 24px; } QMenu::item:selected { background:#173762; }
"""


def message(parent, title, text, warning=False):
    box = QMessageBox(parent); box.setWindowTitle(title); box.setText(text)
    box.setIcon(QMessageBox.Warning if warning else QMessageBox.Information); box.exec_()


def line(value="", secret=False):
    control = QLineEdit(str(value or "")); control.setEchoMode(QLineEdit.Password if secret else QLineEdit.Normal)
    return control


def resource_path(*parts):
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, *parts)


def set_windows_application_id():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("techinc.attendance-sync")
    except (AttributeError, OSError):
        pass


class DeviceDialog(QDialog):
    def __init__(self, device=None, parent=None):
        super().__init__(parent); self.setWindowTitle("Add device" if device is None else "Edit device"); self.setMinimumWidth(430)
        device = device or {}; layout = QVBoxLayout(self); form = QFormLayout()
        self.device_id, self.host = line(device.get("device_id")), line(device.get("ip"))
        self.kind = QComboBox(); self.kind.addItems(["hikvision", "zkteco"]); self.kind.setCurrentText(device.get("device_type", "zkteco"))
        self.port = QSpinBox(); self.port.setRange(1, 65535); self.port.setValue(int(device.get("port", 80 if self.kind.currentText() == "hikvision" else 4370)))
        self.user, self.password = line(device.get("username")), line(device.get("password"), True)
        self.major, self.minor = QSpinBox(), QSpinBox(); self.major.setRange(0, 999); self.minor.setRange(0, 999); self.major.setValue(int(device.get("major", 5))); self.minor.setValue(int(device.get("minor", 0)))
        self.shift = line(device.get("shift", "")); self.direction = QComboBox(); self.direction.addItems(["None", "AUTO", "IN", "OUT"]); self.direction.setCurrentText(device.get("punch_direction") or "None")
        self.clear = QCheckBox("Clear device records after successful push"); self.clear.setChecked(bool(device.get("clear_from_device_on_fetch", False)))
        for label, control in (("Device ID", self.device_id), ("LAN IP / hostname", self.host), ("Device type", self.kind), ("Port", self.port), ("Username (Hikvision)", self.user), ("Password (Hikvision)", self.password), ("Hikvision major", self.major), ("Hikvision minor", self.minor), ("ERPNext shift", self.shift), ("Punch direction", self.direction)): form.addRow(label, control)
        layout.addLayout(form); layout.addWidget(self.clear); buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def value(self):
        return {"device_id": self.device_id.text().strip(), "ip": self.host.text().strip(), "device_type": self.kind.currentText(), "port": self.port.value(), "username": self.user.text(), "password": self.password.text(), "major": self.major.value(), "minor": self.minor.value(), "shift": self.shift.text().strip(), "punch_direction": None if self.direction.currentText() == "None" else self.direction.currentText(), "clear_from_device_on_fetch": self.clear.isChecked(), "latitude": None, "longitude": None}


class DeviceProbeWorker(QtCore.QThread):
    results_ready = QtCore.pyqtSignal(dict)

    def __init__(self, devices):
        super().__init__()
        self.devices = [dict(device) for device in devices]

    def run(self):
        results = {}
        for device in self.devices:
            try:
                if device.get("device_type") == "hikvision":
                    HikvisionAttendanceClient(device["ip"], device.get("username", ""), device.get("password", ""), port=device.get("port", 80), timeout=4).test_connection()
                else:
                    with socket.create_connection((device["ip"], int(device.get("port", 4370))), timeout=4): pass
                results[device["device_id"]] = (True, "Online")
            except (HikvisionConnectionError, OSError, ValueError):
                results[device["device_id"]] = (False, "Offline")
        self.results_ready.emit(results)


class AttendanceWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.devices = [dict(item) for item in config.devices]
        for device in self.devices: device.setdefault("shift", self.shift(device.get("device_id")))
        self.service = None; self.manual = None; self.allow_exit = False; self.tray = None; self.device_health = {}; self.health_worker = None
        self.setWindowTitle("Attendance Sync"); self.resize(1180, 760); self.setMinimumSize(980, 650); self.build(); self.refresh_all()
        self.setWindowIcon(QIcon(resource_path("assets", "attendance_sync.ico")))
        self.create_tray()
        self.show_page(2 if self.first_launch() else 0)
        self.health_timer = QtCore.QTimer(self); self.health_timer.timeout.connect(self.probe_devices); self.health_timer.start(60000); self.probe_devices()

    def build(self):
        root = QWidget(); self.setCentralWidget(root); main = QHBoxLayout(root); main.setContentsMargins(0, 0, 0, 0); main.setSpacing(0)
        main.addWidget(self.sidebar()); self.stack = QStackedWidget(); main.addWidget(self.stack, 1)
        self.stack.addWidget(self.dashboard()); self.stack.addWidget(self.devices_page()); self.stack.addWidget(self.connection_page()); self.stack.addWidget(self.logs_page()); self.stack.addWidget(self.settings_page())

    def sidebar(self):
        panel = QFrame(); panel.setObjectName("sidebar"); panel.setFixedWidth(205); layout = QVBoxLayout(panel); layout.setContentsMargins(14, 22, 14, 16); layout.setSpacing(4)
        brand = QLabel("ATTENDANCE\nSYNC"); brand.setStyleSheet("font-size:18px;font-weight:700;color:white;letter-spacing:1px;"); layout.addWidget(brand)
        subtitle = QLabel("ERPNext local bridge"); subtitle.setObjectName("muted"); layout.addWidget(subtitle); layout.addSpacing(30); self.nav = []
        for label, page in (("Dashboard", 0), ("Devices", 1), ("Connection", 2), ("Live logs", 3), ("Settings", 4)):
            button = QPushButton(label); button.setObjectName("nav"); button.setCheckable(True); button.clicked.connect(lambda _, index=page: self.show_page(index)); layout.addWidget(button); self.nav.append(button)
        layout.addStretch(); self.service_label = QLabel("● Service stopped"); self.service_label.setObjectName("muted"); layout.addWidget(self.service_label); return panel

    def shell(self, eyebrow, title, detail):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(30, 26, 30, 18); layout.setSpacing(14)
        a, b, c = QLabel(eyebrow.upper()), QLabel(title), QLabel(detail); a.setObjectName("eyebrow"); b.setObjectName("title"); c.setObjectName("muted"); c.setWordWrap(True)
        layout.addWidget(a); layout.addWidget(b); layout.addWidget(c); return page, layout

    def dashboard(self):
        page, layout = self.shell("Operations · Local bridge", "Every punch, accounted for.", "Pull LAN terminals locally, then post Employee Checkins securely to ERPNext.")
        grid = QGridLayout(); self.stats = []
        for index, (label, value, note) in enumerate((("Devices", "0", "configured"), ("Last successful sync", "—", "not recorded"), ("Errors", "0", "in error log"), ("Service", "Stopped", "manual available"))):
            card = QFrame(); card.setObjectName("card"); box = QVBoxLayout(card); small = QLabel(label.upper()); small.setObjectName("eyebrow"); number = QLabel(value); number.setStyleSheet("font-size:23px;font-weight:600;"); muted = QLabel(note); muted.setObjectName("muted"); box.addWidget(small); box.addWidget(number); box.addWidget(muted); grid.addWidget(card, 0, index); self.stats.append(number)
        layout.addLayout(grid); actions = QHBoxLayout(); self.sync_button = QPushButton("Sync now"); self.sync_button.clicked.connect(self.manual_sync); backfill = QPushButton("Backfill device data"); backfill.clicked.connect(lambda: self.manual_sync(backfill=True)); self.service_button = QPushButton("Start service"); self.service_button.clicked.connect(self.toggle_service); open_logs = QPushButton("Open live logs"); open_logs.clicked.connect(lambda: self.show_page(3)); actions.addWidget(self.sync_button); actions.addWidget(backfill); actions.addWidget(self.service_button); actions.addWidget(open_logs); actions.addStretch(); layout.addLayout(actions)
        self.dashboard_table = self.table(["Device", "Type", "LAN address", "Last sync", "Status"]); layout.addWidget(self.dashboard_table, 1); return page

    def devices_page(self):
        page, layout = self.shell("Terminals", "Devices on your LAN.", "Configure Hikvision or ZKTeco terminals. Credentials remain on this computer.")
        actions = QHBoxLayout()
        for label, callback in (("Add device", self.add_device), ("Edit selected", self.edit_device), ("Remove selected", self.remove_device), ("Test selected", self.test_device), ("Save configuration", self.save)):
            button = QPushButton(label); button.clicked.connect(callback); actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions); self.devices_table = self.table(["Device ID", "Type", "LAN address", "Port", "Shift", "Status"]); self.devices_table.setSelectionBehavior(QTableWidget.SelectRows); layout.addWidget(self.devices_table, 1); return page

    def connection_page(self):
        page, layout = self.shell("ERPNext", "Connect your attendance ledger.", "Test credentials before syncing. This check does not create attendance records.")
        card = QFrame(); card.setObjectName("card"); form = QFormLayout(card); form.setContentsMargins(24, 24, 24, 24); self.erp_url, self.erp_key, self.erp_secret = line(config.ERPNEXT_URL), line(config.ERPNEXT_API_KEY), line(config.ERPNEXT_API_SECRET, True)
        form.addRow("ERPNext URL", self.erp_url); form.addRow("API key", self.erp_key); form.addRow("API secret", self.erp_secret); layout.addWidget(card)
        actions = QHBoxLayout(); test, save = QPushButton("Test ERPNext"), QPushButton("Save connection"); test.clicked.connect(self.test_erp); save.clicked.connect(self.save); actions.addWidget(test); actions.addWidget(save); actions.addStretch(); layout.addLayout(actions); layout.addStretch(); return page

    def logs_page(self):
        page, layout = self.shell("Diagnostics", "Live logs.", "Read service output locally for device, ERPNext, and manual-sync debugging."); actions = QHBoxLayout(); self.log_choice = QComboBox(); self.log_choice.currentIndexChanged.connect(self.refresh_logs); refresh = QPushButton("Refresh"); refresh.clicked.connect(self.refresh_logs); actions.addWidget(self.log_choice); actions.addWidget(refresh); actions.addStretch(); layout.addLayout(actions); self.log_view = QPlainTextEdit(); self.log_view.setReadOnly(True); self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap); layout.addWidget(self.log_view, 1); return page

    def settings_page(self):
        page, layout = self.shell("Preferences", "Control the local service.", "Manual sync is always available. Start the service only when timed polling is required.")
        card = QFrame(); card.setObjectName("card"); form = QFormLayout(card); form.setContentsMargins(24, 24, 24, 24); self.frequency = QSpinBox(); self.frequency.setRange(1, 1440); self.frequency.setValue(int(config.PULL_FREQUENCY)); self.start_date = line(config.IMPORT_START_DATE or ""); self.hikvision_lookback = QSpinBox(); self.hikvision_lookback.setRange(5, 1440); self.hikvision_lookback.setValue(int(getattr(config, "HIKVISION_LOOKBACK_MINUTES", 240))); self.log_size = QSpinBox(); self.log_size.setRange(1, 100); self.log_size.setValue(int(getattr(config, "LOG_MAX_MB", 2))); self.log_days = QSpinBox(); self.log_days.setRange(1, 365); self.log_days.setValue(int(getattr(config, "LOG_RETENTION_DAYS", 30))); form.addRow("Pull frequency (minutes)", self.frequency); form.addRow("Import start date (YYYYMMDD)", self.start_date); form.addRow("Hikvision late-event overlap (minutes)", self.hikvision_lookback); form.addRow("Archive logs after (MB)", self.log_size); form.addRow("Delete archived logs after (days)", self.log_days); layout.addWidget(card); save = QPushButton("Save settings"); save.clicked.connect(self.save); layout.addWidget(save); layout.addStretch(); return page

    def table(self, headers):
        table = QTableWidget(0, len(headers)); table.setHorizontalHeaderLabels(headers); table.setEditTriggers(QTableWidget.NoEditTriggers); table.horizontalHeader().setStretchLastSection(True); return table

    def show_page(self, index):
        self.stack.setCurrentIndex(index)
        for position, button in enumerate(self.nav): button.setChecked(position == index)
        if index == 3: self.refresh_logs()
        if index in (0, 1): self.refresh_all()

    def refresh_all(self): self.refresh_devices(); self.refresh_dashboard(); self.refresh_log_sources(); self.refresh_logs()

    def refresh_devices(self):
        self.devices_table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            values = [device.get("device_id"), device.get("device_type", "zkteco"), device.get("ip"), device.get("port"), device.get("shift") or self.shift(device.get("device_id")), self.health_label(device.get("device_id"))]
            self.set_table_row(self.devices_table, row, values)

    def refresh_dashboard(self):
        state = self.status(); running = self.service and self.service.poll() is None; self.stats[0].setText(str(len(self.devices))); last = state.get("mission_accomplished_timestamp", "—"); self.stats[1].setText(str(last)[:19]); self.stats[2].setText(str(self.error_count())); self.stats[3].setText("Running" if running else "Stopped")
        self.service_label.setText("● Service running" if running else "● Service stopped"); self.service_label.setStyleSheet("color: #40d98b;" if running else "color: #ff667a;")
        self.dashboard_table.setRowCount(len(self.devices))
        for row, device in enumerate(self.devices):
            values = [device.get("device_id"), device.get("device_type", "zkteco"), device.get("ip"), str(state.get(f'{device.get("device_id")}_push_timestamp', "—"))[:19], self.health_label(device.get("device_id"))]
            self.set_table_row(self.dashboard_table, row, values)

    def set_table_row(self, table, row, values):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value or "—"))
            if column == len(values) - 1 and str(value).endswith("Online"):
                item.setForeground(QColor("#40d98b"))
            elif column == len(values) - 1 and str(value).endswith("Offline"):
                item.setForeground(QColor("#ff667a"))
            table.setItem(row, column, item)

    def health_label(self, device_id):
        connected, label = self.device_health.get(device_id, (None, "Checking…"))
        return ("● " + label) if connected is not None else label

    def probe_devices(self):
        if self.health_worker and self.health_worker.isRunning(): return
        self.device_health = {device.get("device_id"): (None, "Checking…") for device in self.devices}
        self.refresh_devices(); self.refresh_dashboard()
        self.health_worker = DeviceProbeWorker(self.devices); self.health_worker.results_ready.connect(self.apply_device_health); self.health_worker.start()

    def apply_device_health(self, results):
        self.device_health = results; self.refresh_devices(); self.refresh_dashboard()

    def status(self):
        try:
            with open(os.path.join(config.LOGS_DIRECTORY, "status.json"), encoding="utf-8") as handle: return json.load(handle)
        except (OSError, ValueError): return {}

    def error_count(self):
        try:
            with open(os.path.join(config.LOGS_DIRECTORY, "error.log"), encoding="utf-8") as handle: return sum("\tERROR\t" in row for row in handle)
        except OSError: return 0

    def selected(self):
        rows = self.devices_table.selectionModel().selectedRows(); return rows[0].row() if rows else None

    def add_device(self):
        dialog = DeviceDialog(parent=self)
        if dialog.exec_() == QDialog.Accepted:
            value = dialog.value()
            if not value["device_id"] or not value["ip"]: return message(self, "Missing values", "Device ID and LAN IP / hostname are required.", True)
            self.devices.append(value); self.refresh_all()

    def edit_device(self):
        index = self.selected()
        if index is None: return message(self, "Select a device", "Select a device to edit first.", True)
        dialog = DeviceDialog(self.devices[index], self)
        if dialog.exec_() == QDialog.Accepted: self.devices[index] = dialog.value(); self.refresh_all()

    def remove_device(self):
        index = self.selected()
        if index is None: return message(self, "Select a device", "Select a device to remove first.", True)
        if QMessageBox.question(self, "Remove device", "Remove the selected device from this unsaved configuration?") == QMessageBox.Yes: self.devices.pop(index); self.refresh_all()

    def test_device(self):
        index = self.selected()
        if index is None: return message(self, "Select a device", "Select a device to test first.", True)
        device = self.devices[index]
        if device.get("device_type") != "hikvision": return message(self, "ZKTeco test", "Use Sync now to test a ZKTeco device connection.", True)
        try: HikvisionAttendanceClient(device["ip"], device.get("username", ""), device.get("password", ""), port=device.get("port", 80)).test_connection()
        except HikvisionConnectionError as error: return message(self, "Device connection failed", str(error), True)
        message(self, "Device connection", "Hikvision device responded successfully.")

    def test_erp(self):
        try:
            response = requests.get(self.erp_url.text().rstrip("/") + "/api/method/frappe.auth.get_logged_user", headers={"Authorization": "token " + self.erp_key.text() + ":" + self.erp_secret.text()}, timeout=15); response.raise_for_status(); user = response.json().get("message", "authenticated user")
        except (requests.RequestException, ValueError) as error: return message(self, "ERPNext connection failed", str(error), True)
        message(self, "ERPNext connection", "Connected successfully as: " + str(user))

    def manual_sync(self, backfill=False):
        if self.service: return message(self, "Manual sync", "Stop the timed service before starting a manual sync.", True)
        if self.manual: return
        if backfill and QMessageBox.question(self, "Backfill device data", "Resend all device records from the configured Import Start Date? ERPNext may reject duplicates safely.") != QMessageBox.Yes: return
        os.makedirs(config.LOGS_DIRECTORY, exist_ok=True); self.output = open(os.path.join(config.LOGS_DIRECTORY, "manual_sync_console.log"), "a", encoding="utf-8")
        command = "from erpnext_sync import main; main(force=True, reset_hikvision_cursor=True)" if backfill else "from erpnext_sync import main; main(force=True)"
        self.manual = subprocess.Popen([sys.executable, "-c", command], stdout=self.output, stderr=subprocess.STDOUT); self.sync_button.setEnabled(False); QtCore.QTimer.singleShot(500, self.check_manual)

    def check_manual(self):
        if self.manual.poll() is None: return QtCore.QTimer.singleShot(500, self.check_manual)
        result = self.manual.returncode; self.output.close(); self.manual = None; self.sync_button.setEnabled(True); self.refresh_all(); message(self, "Manual sync", "Manual sync finished. Review Live logs for results." if result == 0 else "Manual sync failed. Review Live logs.", result != 0)

    def toggle_service(self):
        if self.service: self.service.kill(); self.service = None; self.service_button.setText("Start service"); self.service_label.setText("● Service stopped")
        else: self.service = subprocess.Popen([sys.executable, "-c", "from erpnext_sync import infinite_loop; infinite_loop()"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); self.service_button.setText("Stop service"); self.service_label.setText("● Service running")
        self.refresh_dashboard()

    def first_launch(self):
        return not all((config.ERPNEXT_URL, config.ERPNEXT_API_KEY, config.ERPNEXT_API_SECRET))

    def create_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        self.tray = QSystemTrayIcon(QIcon(resource_path("assets", "attendance_sync.ico")), self)
        menu = QMenu(self); open_action = QAction("Open Attendance Sync", self); open_action.triggered.connect(self.restore_window)
        exit_action = QAction("Exit", self); exit_action.triggered.connect(self.exit_application)
        menu.addAction(open_action); menu.addSeparator(); menu.addAction(exit_action); self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore_window() if reason == QSystemTrayIcon.Trigger else None); self.tray.show()

    def restore_window(self):
        self.showNormal(); self.activateWindow(); self.raise_()

    def exit_application(self):
        self.allow_exit = True
        if self.service: self.service.kill()
        if self.manual: self.manual.kill()
        QApplication.quit()

    def refresh_logs(self):
        if not hasattr(self, "log_choice"): return
        index = self.log_choice.currentIndex(); files = ["logs.log", "error.log", "manual_sync_console.log"]
        filename = self.log_choice.itemData(index) or files[index]
        try:
            with open(os.path.join(config.LOGS_DIRECTORY, filename), encoding="utf-8") as handle: text = handle.read()[-30000:]
        except OSError: text = "No log file exists yet."
        if hasattr(self, "log_view"): self.log_view.setPlainText(text)

    def refresh_log_sources(self):
        current = self.log_choice.currentData()
        self.log_choice.blockSignals(True); self.log_choice.clear()
        for label, filename in (("Service log", "logs.log"), ("Error log", "error.log"), ("Manual sync console", "manual_sync_console.log")):
            self.log_choice.addItem(label, filename)
        for device in self.devices:
            device_id = device.get("device_id")
            if device_id: self.log_choice.addItem("ERPNext audit · " + device_id, "erpnext_audit_log_" + device_id + ".log")
        position = self.log_choice.findData(current)
        self.log_choice.setCurrentIndex(position if position >= 0 else 0); self.log_choice.blockSignals(False)

    def shift(self, device_id):
        for mapping in getattr(config, "shift_type_device_mapping", []):
            if device_id in mapping.get("related_device_id", []):
                name = mapping.get("shift_type_name", ""); return name[0] if isinstance(name, list) and name else name
        return ""

    def save(self):
        global config
        if not all((self.erp_url.text().strip(), self.erp_key.text().strip(), self.erp_secret.text().strip())): return message(self, "Missing connection values", "ERPNext URL, API key, and API secret are required.", True)
        ids = [device.get("device_id") for device in self.devices]
        if not ids or any(not item for item in ids) or len(ids) != len(set(ids)): return message(self, "Invalid devices", "Add devices with unique Device IDs before saving.", True)
        groups = {}
        for device in self.devices:
            if device.get("shift"): groups.setdefault(device["shift"], []).append(device["device_id"])
        mappings = [{"shift_type_name": name, "related_device_id": ids} for name, ids in groups.items()]
        saved_devices = [{key: value for key, value in device.items() if key != "shift"} for device in self.devices]
        content = "\n".join(["ERPNEXT_API_KEY = " + repr(self.erp_key.text().strip()), "ERPNEXT_API_SECRET = " + repr(self.erp_secret.text().strip()), "ERPNEXT_URL = " + repr(self.erp_url.text().strip().rstrip("/")), "ERPNEXT_VERSION = " + repr(getattr(config, "ERPNEXT_VERSION", 15)), "PULL_FREQUENCY = " + repr(self.frequency.value()), "HIKVISION_LOOKBACK_MINUTES = " + repr(self.hikvision_lookback.value()), "LOGS_DIRECTORY = " + repr(getattr(config, "LOGS_DIRECTORY", "logs")), "LOG_MAX_MB = " + repr(self.log_size.value()), "LOG_RETENTION_DAYS = " + repr(self.log_days.value()), "IMPORT_START_DATE = " + repr(self.start_date.text().strip() or None), "devices = " + repr(saved_devices), "shift_type_device_mapping = " + repr(mappings), "allowed_exceptions = [1, 2, 3]", ""])
        with open(config_path(), "w", encoding="utf-8") as handle: handle.write(content)
        config = load_config()
        message(self, "Configuration saved", "Saved locally. Test ERPNext or use Sync now to verify it.")

    def closeEvent(self, event):
        if not self.allow_exit and self.tray:
            self.hide(); self.tray.showMessage("Attendance Sync", "The application is running in the system tray.", QSystemTrayIcon.Information, 2000); event.ignore()
        elif self.service or self.manual: message(self, "Sync active", "Stop the service and wait for manual sync to finish before closing.", True); event.ignore()
        else: event.accept()

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.WindowStateChange and self.isMinimized() and self.tray:
            QtCore.QTimer.singleShot(0, self.hide)
        super().changeEvent(event)


def setup_window():
    set_windows_application_id()
    app = QApplication(sys.argv); app.setStyleSheet(STYLE); app.setWindowIcon(QIcon(resource_path("assets", "attendance_sync.ico")))
    splash_image = QPixmap(resource_path("assets", "attendance_sync.png")).scaled(360, 360, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    splash = QSplashScreen(splash_image); splash.showMessage("Starting Attendance Sync…", QtCore.Qt.AlignBottom | QtCore.Qt.AlignHCenter, QtCore.Qt.white); splash.show(); app.processEvents()

    def open_main_window():
        app.main_window = AttendanceWindow()
        app.main_window.show()
        splash.finish(app.main_window)

    QtCore.QTimer.singleShot(1400, open_main_window)
    sys.exit(app.exec_())


if __name__ == "__main__": setup_window()
