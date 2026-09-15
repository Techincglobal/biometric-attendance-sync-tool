@echo off
call venv\Scripts\activate.bat
python -m pip install --upgrade pyinstaller
pyinstaller --noconfirm --clean --windowed --onefile --name AttendanceSync --icon assets\attendance_sync.ico --add-data "assets;assets" --add-data "local_config.py.template;." gui.py
echo.
echo Build complete: dist\AttendanceSync.exe
