@echo off
setlocal enabledelayedexpansion
title Koishi AI Pet - һ����װ

echo ============================================
echo   Koishi AI Pet һ����װ�ű�
echo ============================================
echo.

:: ===== 1. ��� Python �Ƿ����=====
python --version >nul 2>nul
if errorlevel 1 (
    echo [����] δ��⵽���õ� Python�����Ȱ�װ Python 3.11+
    echo        ���ص�ַ��https://www.python.org/downloads/
    echo        ��װʱ��ع�ѡ "Add Python to PATH"
    pause
    exit /b 1
)

:: �����汾�ţ�У�� >= 3.11
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)
if !PYMAJOR! lss 3 (
    echo [����] Python �汾���ͣ�!PYVER!����Ҫ 3.11+
    pause
    exit /b 1
)
if !PYMAJOR! equ 3 if !PYMINOR! lss 11 (
    echo [����] Python �汾���ͣ�!PYVER!����Ҫ 3.11+
    pause
    exit /b 1
)
echo ��⵽ Python !PYVER!

:: ===== 2. �����Ŀ��Ŀ¼ =====
if not exist "%~dp0pyproject.toml" (
    echo [����] δ�ҵ� pyproject.toml����ѽű�������Ŀ��Ŀ¼����
    pause
    exit /b 1
)

:: ===== 3. �������⻷�� =====
echo.
echo [1/4] �������⻷��...
if exist "%~dp0venv\Scripts\python.exe" (
    echo   ���⻷���Ѵ��ڣ�����
) else (
    :: �������ܴ��ڵİ�� venv Ŀ¼
    if exist "%~dp0venv" rmdir /s /q "%~dp0venv"
    python -m venv "%~dp0venv"
    if errorlevel 1 (
        echo [����] �������⻷��ʧ��
        pause
        exit /b 1
    )
    echo   ���
)

:: ===== 4. ������� pip =====
echo.
echo [2/4] �������⻷�������� pip...
call "%~dp0venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [����] �������⻷��ʧ��
    pause
    exit /b 1
)
python -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [����] pip ����ʧ�ܣ�����ʹ�����ð汾
)

:: ===== 5. ��װ���� =====
echo.
echo [3/4] ��װ����...
pip install -e "%~dp0." -q -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [����] ��װ����ʧ�ܣ���������� pyproject.toml
    pause
    exit /b 1
)
echo   ���

:: ===== 6. У����ڿ�ִ���ļ� =====
if not exist "%~dp0venv\Scripts\koishi.exe" (
    echo.
    echo [����] δ�ҵ� venv\Scripts\koishi.exe
    echo        ��ȷ�� pyproject.toml �������� [project.scripts] koishi = "..."
    echo        ���ֶ�������venv\Scripts\python.exe -m pet
    pause
    exit /b 1
)

:: ===== 7. ���������ݷ�ʽ =====
echo.
echo [4/4] ���������ݷ�ʽ...

:: ���ɿ�ݷ�ʽͼ�꣨PNG �� ICO��
set "ICON_SRC=%~dp0assets\icon\sys_tray.png"
set "ICON_ICO=%~dp0assets\icon\sys_tray.ico"
if exist "%ICON_SRC%" (
    "%~dp0venv\Scripts\python.exe" -c "from PIL import Image; img=Image.open(r'%ICON_SRC%'); img.save(r'%ICON_ICO%', format='ICO', sizes=[(256,256),(48,48),(32,32),(16,16)])" 2>nul
    if not exist "%ICON_ICO%" (
        echo [����] ͼ��ת��ʧ�ܣ���ݷ�ʽ��ʹ��Ĭ��ͼ��
    )
)

:: ��ȡ��ʵ����·�������� OneDrive �ض���
for /f "usebackq tokens=2,*" %%a in (`reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" /v Desktop 2^>nul`) do set "DESKTOP=%%b"
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

set "SHORTCUT=!DESKTOP!\Koishi AI Pet.lnk"

:: ʼ�ո����ؽ�������ɿ�ݷ�ʽָ��ʧЧ·��
if exist "!SHORTCUT!" del /q "!SHORTCUT!"

:: ����ͼ��·������ ICO ����ʧ�������գ�ʹ�� koishi.exe Ĭ��ͼ�꣩
set "ICON_ARG="
if exist "%ICON_ICO%" set "ICON_ARG=$sc.IconLocation='%ICON_ICO%'; "

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut('!SHORTCUT!'); $sc.TargetPath='%~dp0venv\Scripts\koishi.exe'; $sc.WorkingDirectory='%~dp0'; $sc.Description='Koishi AI Pet'; $sc.WindowStyle=1; !ICON_ARG!$sc.Save()"

if errorlevel 1 (
    echo [����] ��ݷ�ʽ����ʧ�ܣ����ֶ�������
    echo        %~dp0venv\Scripts\koishi.exe
) else (
    echo   ���
)

echo.
echo ============================================
echo   ��װ��ɣ�
echo.
echo   ������ʽ��
echo     1. ˫������ "Koishi AI Pet" ��ݷ�ʽ
echo     2. �����У�%~dp0venv\Scripts\koishi.exe
echo ============================================
pause
endlocal
