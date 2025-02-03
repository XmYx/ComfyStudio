@echo off

REM Check if virtual environment directory exists
IF NOT EXIST venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate

REM Upgrade pip
pip install --upgrade pip

REM Check if 'comfystudio' command exists
where comfystudio >nul 2>&1
IF ERRORLEVEL 1 (
    echo Installing comfystudio...
    pip install -e .
) ELSE (
    echo comfystudio is already installed.
)

REM Launch the application
comfystudio
pause
