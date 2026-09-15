import datetime
import json
import os
import shlex
import sys
import subprocess
import requests
import local_config as config

from PyQt5 import QtCore
from PyQt5 import QtWidgets
from PyQt5.QtCore import QRegExp
from PyQt5.QtGui import QIntValidator, QRegExpValidator
from PyQt5.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton
from hikvision import HikvisionAttendanceClient, HikvisionConnectionError


config_template = '''# ERPNext related configs
ERPNEXT_API_KEY = '{0}'
ERPNEXT_API_SECRET = '{1}'
ERPNEXT_URL = '{2}'
ERPNEXT_VERSION = {3}


# operational configs
PULL_FREQUENCY = {4} or 60 # in minutes
LOGS_DIRECTORY = 'logs' # logs of this script is stored in this directory
IMPORT_START_DATE = '{5}' or None # format: '20190501'

# Biometric device configs (all keys mandatory)
    #- device_id - must be unique, strictly alphanumerical chars only. no space allowed.
    #- ip - device IP Address
    #- punch_direction - 'IN'/'OUT'/'AUTO'/None
    #- clear_from_device_on_fetch: if set to true then attendance is deleted after fetch is successful.
    #(Caution: this feature can lead to data loss if used carelessly.)
devices = {6}

# Configs updating sync timestamp in the Shift Type DocType
shift_type_device_mapping = {7}
'''


class BiometricWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.reg_exp_for_ip = r"((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(?=\s*netmask)"
        self.init_ui()

    def closeEvent(self, event):
        can_exit = not hasattr(self, "p")
        if can_exit:
            event.accept()
        else:
            create_message_box(text="Window cannot be closed when \nservice is running!", title="Message", width=200)
            event.ignore()

    def init_ui(self):
        self.counter = 0
        self.setup_window()
        self.setup_textboxes_and_label()
        self.center()
        self.show()

    def setup_window(self):
        self.setFixedSize(890, 580)
        self.setWindowTitle('ERPNext Attendance Sync')
        self.setStyleSheet("QMainWindow { background: #f7f9fc; } QLineEdit, QComboBox { padding: 5px; background: white; border: 1px solid #cbd5e1; border-radius: 4px; } QPushButton { padding: 6px 10px; background: #2563eb; color: white; border: 0; border-radius: 4px; } QPushButton:disabled { background: #94a3b8; }")

    def setup_textboxes_and_label(self):

        self.create_label("API Secret", "api_secret", 20, 0, 200, 30)
        self.create_field("textbox_erpnext_api_secret", 20, 30, 200, 30)

        self.create_label("API Key", "api_key", 20, 60, 200, 30)
        self.create_field("textbox_erpnext_api_key", 20, 90, 200, 30)

        self.create_label("ERPNext URL", "erpnext_url", 20, 120, 200, 30)
        self.create_field("textbox_erpnext_url", 20, 150, 200, 30)

        self.create_label("Pull Frequency (in minutes)",
                          "pull_frequency", 250, 0, 200, 30)
        self.create_field("textbox_pull_frequency", 250, 30, 200, 30)

        self.create_label("Import Start Date",
                          "import_start_date", 250, 60, 200, 30)
        self.create_field("textbox_import_start_date", 250, 90, 200, 30)
        self.validate_data(r"^\d{1,2}/\d{1,2}/\d{4}$", "textbox_import_start_date")

        self.create_separator(210, 890)
        self.create_button('+ Device', 'add', 730, 225, 70, 30, self.add_devices_fields)
        self.create_button('- Device', 'remove', 805, 225, 70, 30, self.remove_devices_fields)

        self.create_label("Device ID", "device_id", 20, 260, 0, 30)
        self.create_label("LAN IP / hostname", "device_ip", 140, 260, 0, 30)
        self.create_label("Type", "device_type", 280, 260, 0, 30)
        self.create_label("Hikvision user", "device_user", 375, 260, 0, 30)
        self.create_label("Hikvision password", "device_password", 485, 260, 0, 30)
        self.create_label("Port", "device_port", 600, 260, 0, 30)
        self.create_label("Shift", "shift", 665, 260, 0, 0)

        # First Row for table
        self.create_device_fields(0)

        # Actions buttons
        self.create_button('Save Configuration', 'set_conf', 20, 530, 150, 30, self.setup_local_config)
        self.create_button('Test Hikvision', 'test_hikvision', 180, 530, 130, 30, self.test_hikvision_connections)
        self.create_button('Test ERPNext', 'test_erpnext', 320, 530, 120, 30, self.test_erpnext_connection)
        self.create_button('Sync Now', 'sync_now', 450, 530, 110, 30, self.run_manual_sync)
        self.create_button('Running Status', 'running_status', 570, 530, 120, 30, self.get_running_status, enable=False)
        self.create_button('Start Service', 'start_or_stop_service', 700, 530, 120, 30, self.integrate_biometric, enable=False)
        self.set_default_value_or_placeholder_of_field()

        # validating integer
        self.onlyInt = QIntValidator(10, 30)
        self.textbox_pull_frequency.setValidator(self.onlyInt)

    def set_default_value_or_placeholder_of_field(self):
        if os.path.exists("local_config.py"):
            import local_config as config
            self.textbox_erpnext_api_secret.setText(config.ERPNEXT_API_SECRET)
            self.textbox_erpnext_api_key.setText(config.ERPNEXT_API_KEY)
            self.textbox_erpnext_url.setText(config.ERPNEXT_URL)
            self.textbox_pull_frequency.setText(str(config.PULL_FREQUENCY))

            if len(config.devices):
                self.device_id_0.setText(config.devices[0]['device_id'])
                self.device_ip_0.setText(config.devices[0]['ip'])
                self.device_type_0.setCurrentText(config.devices[0].get('device_type', 'zkteco'))
                self.device_user_0.setText(config.devices[0].get('username', ''))
                self.device_password_0.setText(config.devices[0].get('password', ''))
                self.device_port_0.setText(str(config.devices[0].get('port', 4370)))
                self.shift_0.setText(
                    self.get_shift_name_for_device(config, config.devices[0]['device_id']))

            if len(config.devices) > 1:
                for _ in range(self.counter, len(config.devices) - 1):
                    self.add_devices_fields()

                    device = getattr(self, 'device_id_' + str(self.counter))
                    ip = getattr(self, 'device_ip_' + str(self.counter))
                    device_type = getattr(self, 'device_type_' + str(self.counter))
                    username = getattr(self, 'device_user_' + str(self.counter))
                    password = getattr(self, 'device_password_' + str(self.counter))
                    port = getattr(self, 'device_port_' + str(self.counter))
                    shift = getattr(self, 'shift_' + str(self.counter))

                    device.setText(config.devices[self.counter]['device_id'])
                    ip.setText(config.devices[self.counter]['ip'])
                    device_type.setCurrentText(config.devices[self.counter].get('device_type', 'zkteco'))
                    username.setText(config.devices[self.counter].get('username', ''))
                    password.setText(config.devices[self.counter].get('password', ''))
                    port.setText(str(config.devices[self.counter].get('port', 4370)))
                    shift.setText(
                        self.get_shift_name_for_device(config, config.devices[self.counter]['device_id']))
        else:
            self.textbox_erpnext_api_secret.setPlaceholderText("c70ee57c7b3124c")
            self.textbox_erpnext_api_key.setPlaceholderText("fb37y8fd4uh8ac")
            self.textbox_erpnext_url.setPlaceholderText("example.erpnext.com")
            self.textbox_pull_frequency.setPlaceholderText("60")

        self.textbox_import_start_date.setPlaceholderText("DD/MM/YYYY")

    @staticmethod
    def get_shift_name_for_device(config, device_id):
        """Return the configured shift name for a device as text for the UI."""
        for mapping in config.shift_type_device_mapping:
            if device_id in mapping.get('related_device_id', []):
                shift_name = mapping.get('shift_type_name', '')
                if isinstance(shift_name, list):
                    return str(shift_name[0]) if shift_name else ''
                return str(shift_name)
        return ''

    # Widgets Genrators
    def create_label(self, label_text, label_name, x, y, height, width):
        setattr(self,  label_name, QLabel(self))
        label = getattr(self, label_name)
        label.move(x, y)
        label.setText(label_text)
        if height and width:
            label.resize(height, width)
        label.show()

    def create_field(self, field_name, x, y, height, width):
        setattr(self,  field_name, QLineEdit(self))
        field = getattr(self, field_name)
        field.move(x, y)
        field.resize(height, width)
        field.show()

    def create_device_fields(self, index):
        y = 290 + (index * 35)
        self.create_field("device_id_" + str(index), 20, y, 110, 30)
        self.create_field("device_ip_" + str(index), 140, y, 130, 30)
        setattr(self, "device_type_" + str(index), QComboBox(self))
        device_type = getattr(self, "device_type_" + str(index))
        device_type.addItems(["zkteco", "hikvision"])
        device_type.move(280, y)
        device_type.resize(85, 30)
        device_type.show()
        self.create_field("device_user_" + str(index), 375, y, 100, 30)
        self.create_field("device_password_" + str(index), 485, y, 105, 30)
        getattr(self, "device_password_" + str(index)).setEchoMode(QLineEdit.Password)
        self.create_field("device_port_" + str(index), 600, y, 55, 30)
        self.create_field("shift_" + str(index), 665, y, 200, 30)

    def create_separator(self, y, width):
        setattr(self, 'separator', QLineEdit(self))
        field = getattr(self, 'separator')
        field.move(0, y)
        field.resize(width, 5)
        field.setEnabled(False)
        field.show()

    def create_button(self, button_label, button_name, x, y, height, width, callback_function, enable=True):
        setattr(self,  button_name, QPushButton(button_label, self))
        button = getattr(self, button_name)
        button.move(x, y)
        button.resize(height, width)
        button.clicked.connect(callback_function)
        button.setEnabled(enable)

    def center(self):
        frame = self.frameGeometry()
        screen = QApplication.desktop().screenNumber(QApplication.desktop().cursor().pos())
        centerPoint = QApplication.desktop().screenGeometry(screen).center()
        frame.moveCenter(centerPoint)
        self.move(frame.topLeft())

    def add_devices_fields(self):
        if self.counter < 5:
            self.counter += 1
            self.create_device_fields(self.counter)

    def validate_data(self, reg_exp, field_name):
        field = getattr(self, field_name)
        reg_ex = QRegExp(reg_exp)
        input_validator = QRegExpValidator(reg_ex, field)
        field.setValidator(input_validator)

    def remove_devices_fields(self):
        if self.counter > 0:
            b = getattr(self, "shift_" + str(self.counter))
            b.deleteLater()
            b = getattr(self, "device_id_" + str(self.counter))
            b.deleteLater()
            b = getattr(self, "device_ip_" + str(self.counter))
            b.deleteLater()
            for field in ("device_type_", "device_user_", "device_password_", "device_port_"):
                getattr(self, field + str(self.counter)).deleteLater()

            self.counter -= 1

    def integrate_biometric(self):
        button = getattr(self, "start_or_stop_service")

        if not hasattr(self, 'p'):
            print("Starting Service...")
            command = [sys.executable, '-c', 'from erpnext_sync import infinite_loop; infinite_loop()']
            self.p = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("Process running at {}".format(self.p.pid))
            button.setText("Stop Service")
            create_message_box("Service status", "Service has been started")
            self.create_label(str(datetime.datetime.now()), "service_start_time", 20, 60, 200, 30)
            self.service_start_time.setHidden(True)
            getattr(self, 'running_status').setEnabled(True)
            QtCore.QTimer.singleShot(1000, self.check_service_startup)
        else:
            print("Stopping Service...")
            self.p.kill()
            del self.p
            button.setText("Start Service")
            create_message_box("Service status", "Service has been stoped")
            getattr(self, 'running_status').setEnabled(False)

    def check_service_startup(self):
        if hasattr(self, 'p') and self.p.poll() is not None:
            del self.p
            getattr(self, 'start_or_stop_service').setText("Start Service")
            getattr(self, 'running_status').setEnabled(False)
            create_message_box(
                "Service failed to start",
                "The sync process exited. Install dependencies and check logs/error.log for details.",
                "warning", width=360,
            )

    def setup_local_config(self):
        bio_config = self.get_local_config()

        print("Setting Local Configuration...")

        if not bio_config:
            print("Local Configuration not updated...")
            return 0

        if os.path.exists("local_config.py"):
            os.remove("local_config.py")

        with open("local_config.py", 'w+') as f:
            f.write(bio_config)

        print("Local Configuration Updated.")

        create_message_box("Message", "Configuration Updated!\nClick on Start Service.")

        getattr(self, 'start_or_stop_service').setEnabled(True)

    def get_device_details(self):
        device = {}
        devices = []
        shifts = []

        for idx in range(0, self.counter+1):
            shift = getattr(self, "shift_" + str(idx)).text()
            device_id = getattr(self, "device_id_" + str(idx)).text()
            device_type = getattr(self, "device_type_" + str(idx)).currentText()
            port = getattr(self, "device_port_" + str(idx)).text()
            devices.append({
                'device_id': device_id,
                'ip': getattr(self, "device_ip_" + str(idx)).text(),
                'device_type': device_type,
                'port': int(port or (80 if device_type == 'hikvision' else 4370)),
                'username': getattr(self, "device_user_" + str(idx)).text(),
                'password': getattr(self, "device_password_" + str(idx)).text(),
                'major': 5,
                'minor': 0,
                'punch_direction': None,
                'clear_from_device_on_fetch': False,
                'latitude': None,
                'longitude': None,
            })
            if shift in device:
                device[shift].append(device_id)
            else:
                device[shift]=[device_id]
        
        for shift_type_name in device.keys():
            shifts.append({
                'shift_type_name': shift_type_name,
                'related_device_id': device[shift_type_name]
            })
        return devices, shifts

    def test_hikvision_connections(self):
        found_hikvision_device = False
        for idx in range(self.counter + 1):
            if getattr(self, "device_type_" + str(idx)).currentText() != "hikvision":
                continue
            found_hikvision_device = True
            host = getattr(self, "device_ip_" + str(idx)).text()
            username = getattr(self, "device_user_" + str(idx)).text()
            password = getattr(self, "device_password_" + str(idx)).text()
            port = getattr(self, "device_port_" + str(idx)).text() or "80"
            if not all((host, username, password)):
                create_message_box("Hikvision configuration", "Enter the LAN IP, username, and password first.", "warning")
                return
            try:
                HikvisionAttendanceClient(host, username, password, port=port).test_connection()
            except HikvisionConnectionError as error:
                create_message_box("Hikvision connection failed", str(error), "warning", width=400)
                return
        if found_hikvision_device:
            create_message_box("Hikvision connection", "All configured Hikvision devices responded successfully.")
        else:
            create_message_box("Hikvision configuration", "Select Hikvision for at least one device first.", "warning")

    def test_erpnext_connection(self):
        url = self.textbox_erpnext_url.text().rstrip('/')
        api_key = self.textbox_erpnext_api_key.text()
        api_secret = self.textbox_erpnext_api_secret.text()
        if not all((url, api_key, api_secret)):
            create_message_box("ERPNext configuration", "Enter the ERPNext URL, API key, and API secret first.", "warning")
            return

        try:
            response = requests.get(
                url + '/api/method/frappe.auth.get_logged_user',
                headers={'Authorization': 'token ' + api_key + ':' + api_secret},
                timeout=15,
            )
            response.raise_for_status()
            user = response.json().get('message', 'authenticated user')
        except (requests.RequestException, ValueError) as error:
            create_message_box("ERPNext connection failed", str(error), "warning", width=450)
            return

        create_message_box("ERPNext connection", 'Connected successfully as: ' + str(user), width=350)

    def run_manual_sync(self):
        if hasattr(self, 'p'):
            create_message_box("Manual sync", "Stop the scheduled service before running a manual sync.", "warning", width=350)
            return
        if hasattr(self, 'manual_sync_process'):
            create_message_box("Manual sync", "A manual sync is already running.", "warning")
            return

        os.makedirs(config.LOGS_DIRECTORY, exist_ok=True)
        output_path = os.path.join(config.LOGS_DIRECTORY, 'manual_sync_console.log')
        self.manual_sync_output = open(output_path, 'a', encoding='utf-8')
        command = [sys.executable, '-c', 'from erpnext_sync import main; main(force=True)']
        self.manual_sync_process = subprocess.Popen(
            command, stdout=self.manual_sync_output, stderr=subprocess.STDOUT,
        )
        getattr(self, 'sync_now').setEnabled(False)
        create_message_box("Manual sync", "Manual sync started. It may take up to the device timeout. Check Running Status and logs/manual_sync_console.log when it finishes.", width=450)
        QtCore.QTimer.singleShot(500, self.check_manual_sync)

    def check_manual_sync(self):
        if self.manual_sync_process.poll() is None:
            QtCore.QTimer.singleShot(500, self.check_manual_sync)
            return

        self.manual_sync_output.close()
        exit_code = self.manual_sync_process.returncode
        del self.manual_sync_output
        del self.manual_sync_process
        getattr(self, 'sync_now').setEnabled(True)
        if exit_code:
            create_message_box("Manual sync failed", "The manual sync process exited with an error. Check logs/manual_sync_console.log and logs/error.log.", "warning", width=450)
        else:
            create_message_box("Manual sync finished", "The manual sync process finished. Check Running Status for results.", width=400)

    def get_local_config(self):
        if not validate_fields(self):
            return 0
        string = self.textbox_import_start_date.text()
        formated_date = "".join([ele for ele in reversed(string.split("/"))])

        devices, shifts = self.get_device_details()
        return config_template.format(
            self.textbox_erpnext_api_key.text(), self.textbox_erpnext_api_secret.text(),
            self.textbox_erpnext_url.text(), getattr(config, 'ERPNEXT_VERSION', 15),
            self.textbox_pull_frequency.text(), formated_date, repr(devices), repr(shifts),
        )

    def get_running_status(self):
        running_status = []
        logs_path = os.path.join(config.LOGS_DIRECTORY, 'logs.log')
        error_path = os.path.join(config.LOGS_DIRECTORY, 'error.log')
        if not os.path.exists(logs_path) and not os.path.exists(error_path):
            create_message_box("Running status", "No service logs exist yet. Start the service and check its configuration.", "warning", width=350)
            return
        if os.path.exists(logs_path):
            with open(logs_path, 'r') as f:
                index = 0
                for idx, line in enumerate(f, 1):
                    logdate = convert_into_date(line.split(',')[0], '%Y-%m-%d %H:%M:%S')
                    if logdate and logdate >= convert_into_date(self.service_start_time.text().split('.')[0], '%Y-%m-%d %H:%M:%S'):
                        index = idx
                        break
                if index:
                    running_status.extend(read_file_contents('logs', index))

        if os.path.exists(error_path):
            with open(error_path, 'r') as fread:
                error_index = 0
                for error_idx, error_line in enumerate(fread, 1):
                    start_date = convert_into_date(self.service_start_time.text().split('.')[0], '%Y-%m-%d %H:%M:%S')
                    if start_date and start_date.strftime('%Y-%m-%d') in error_line:
                        error_logdate = convert_into_date(error_line.split(',')[0], '%Y-%m-%d %H:%M:%S')
                        if error_logdate and error_logdate >= start_date:
                            error_index = error_idx
                            break
                if error_index:
                    running_status.extend(read_file_contents('error', error_index))

        if running_status:
            create_message_box("Running status", ''.join(running_status))
        else:
            create_message_box("Running status", 'Process not yet started')

def read_file_contents(file_name, index):
    running_status = []
    with open('/'.join([config.LOGS_DIRECTORY])+f'/{file_name}.log', 'r') as file_handler:
        for idx, line in enumerate(file_handler,1):
            if idx>=index:
                running_status.append(line)
    return running_status


def validate_fields(self):
    def message(text):
        create_message_box("Missing Value", "Please Set {}".format(text), "warning")

    if not self.textbox_erpnext_api_key.text():
        return message("API Key")

    if not self.textbox_erpnext_api_secret.text():
        return message("API Secret")

    if not self.textbox_erpnext_url.text():
        return message("ERPNext URL")

    if not self.textbox_import_start_date.text():
        return message("Import Start Date")

    return validate_date(self.textbox_import_start_date.text())


def validate_date(date):
    try:
        datetime.datetime.strptime(date, '%d/%m/%Y')
        return True
    except ValueError:
        create_message_box("", "Please Enter Date in correct format", "warning", width=200)
        return False


def convert_into_date(datestring, pattern):
    try:
        return datetime.datetime.strptime(datestring, pattern)
    except:
        return None


def create_message_box(title, text, icon="information", width=150):
    msg = QMessageBox()
    msg.setWindowTitle(title)
    lineCnt = len(text.split('\n'))
    if lineCnt > 15:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(1)
        content = QtWidgets.QWidget()
        scroll.setWidget(content)
        layout = QtWidgets.QVBoxLayout(content)
        tmpLabel = QtWidgets.QLabel(text)
        tmpLabel.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(tmpLabel)
        msg.layout().addWidget(scroll, 12, 10, 1, msg.layout().columnCount())
        msg.setStyleSheet("QScrollArea{min-width:550 px; min-height: 400px}")
    else:
        msg.setText(text)
        if icon == "warning":
            msg.setIcon(QtWidgets.QMessageBox.Warning)
            msg.setStyleSheet("QMessageBox Warning{min-width: 50 px;}")
        else:
            msg.setIcon(QtWidgets.QMessageBox.Information)
            msg.setStyleSheet("QMessageBox Information{min-width: 50 px;}")
        msg.setStyleSheet("QmessageBox QLabel{min-width: "+str(width)+"px;}")
    msg.exec_()


def setup_window():
    biometric_app = QApplication(sys.argv)
    biometric_window = BiometricWindow()
    biometric_app.exec_()


if __name__ == "__main__":
    setup_window()
