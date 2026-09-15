"""Load the writable local configuration in development and packaged builds."""

import importlib.util
import os
import shutil
import sys


APP_NAME = "AttendanceSync"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def config_directory():
    if not getattr(sys, "frozen", False):
        return PROJECT_DIR
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)


def config_path():
    return os.path.join(config_directory(), "local_config.py")


def template_path():
    root = getattr(sys, "_MEIPASS", PROJECT_DIR)
    return os.path.join(root, "local_config.py.template")


def load_config():
    path = config_path()
    if not os.path.exists(path):
        os.makedirs(config_directory(), exist_ok=True)
        shutil.copyfile(template_path(), path)

    spec = importlib.util.spec_from_file_location("attendance_sync_local_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not os.path.isabs(module.LOGS_DIRECTORY):
        module.LOGS_DIRECTORY = os.path.join(config_directory(), module.LOGS_DIRECTORY)
    return module
