@echo off

REM Check if virtual environment directory exists
IF NOT EXIST venv (
    echo Creating virtual environment...
    REM Prefer the Windows launcher: "py -3" resolves to the newest installed Python 3.
    where py >nul 2>&1
    IF NOT ERRORLEVEL 1 (
        py -3 -m venv venv
    ) ELSE (
        python -m venv venv
    )
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
