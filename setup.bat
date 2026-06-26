@echo off
setlocal enabledelayedexpansion
title Koishi AI Pet - 一键安装

echo ============================================
echo   Koishi AI Pet 一键安装脚本
echo ============================================
echo.

:: ===== 1. 检查 Python 是否可用 =====
python --version >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到可用的 Python，请先安装 Python 3.11+
    echo        下载地址：https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

:: 解析版本号，校验 >= 3.11
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)
if !PYMAJOR! lss 3 (
    echo [错误] Python 版本过低：!PYVER!，需要 3.11+
    pause
    exit /b 1
)
if !PYMAJOR! equ 3 if !PYMINOR! lss 11 (
    echo [错误] Python 版本过低：!PYVER!，需要 3.11+
    pause
    exit /b 1
)
echo 检测到 Python !PYVER!

:: ===== 2. 检查项目根目录 =====
if not exist "%~dp0pyproject.toml" (
    echo [错误] 未找到 pyproject.toml，请把脚本放在项目根目录运行
    pause
    exit /b 1
)

:: ===== 3. 创建虚拟环境 =====
echo.
echo [1/4] 创建虚拟环境...
if exist "%~dp0venv\Scripts\python.exe" (
    echo   虚拟环境已存在，跳过
) else (
    :: 清理可能存在的半残 venv 目录
    if exist "%~dp0venv" rmdir /s /q "%~dp0venv"
    python -m venv "%~dp0venv"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo   完成
)

:: ===== 4. 激活并升级 pip =====
echo.
echo [2/4] 激活虚拟环境并升级 pip...
call "%~dp0venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败
    pause
    exit /b 1
)
python -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [警告] pip 升级失败，继续使用内置版本
)

:: ===== 5. 安装依赖 =====
echo.
echo [3/4] 安装依赖...
pip install -e "%~dp0." -q -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] 安装依赖失败，请检查网络或 pyproject.toml
    pause
    exit /b 1
)
echo   完成

:: ===== 6. 校验入口可执行文件 =====
if not exist "%~dp0venv\Scripts\koishi.exe" (
    echo.
    echo [错误] 未找到 venv\Scripts\koishi.exe
    echo        请确认 pyproject.toml 中已配置 [project.gui-scripts] koishi = "..."
    echo        可手动启动：venv\Scripts\python.exe -m pet
    pause
    exit /b 1
)

:: ===== 7. 创建桌面快捷方式 =====
echo.
echo [4/4] 创建桌面快捷方式...

:: 图标直接使用 index.ico
set "ICON_ICO=%~dp0assets\icon\index.ico"

:: 读取真实桌面路径（兼容 OneDrive 重定向）
for /f "usebackq tokens=2,*" %%a in (`reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" /v Desktop 2^>nul`) do set "DESKTOP=%%b"
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

set "SHORTCUT=!DESKTOP!\Koishi AI Pet.lnk"

:: 始终覆盖重建，避免旧快捷方式指向失效路径
if exist "!SHORTCUT!" del /q "!SHORTCUT!"

:: 设置图标路径（若 index.ico 不存在则留空，使用 koishi.exe 默认图标）
set "ICON_ARG="
if exist "%ICON_ICO%" set "ICON_ARG=$sc.IconLocation='%ICON_ICO%'; "

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws=New-Object -ComObject WScript.Shell; $sc=$ws.CreateShortcut('!SHORTCUT!'); $sc.TargetPath='%~dp0venv\Scripts\koishi.exe'; $sc.WorkingDirectory='%~dp0'; $sc.Description='Koishi AI Pet'; $sc.WindowStyle=1; !ICON_ARG!$sc.Save()"

if errorlevel 1 (
    echo [警告] 快捷方式创建失败，请手动启动：
    echo        %~dp0venv\Scripts\koishi.exe
) else (
    echo   完成
)

echo.
echo ============================================
echo   安装完成！
echo.
echo   启动方式：
echo     1. 双击桌面 "Koishi AI Pet" 快捷方式
echo     2. 或运行：%~dp0venv\Scripts\koishi.exe
echo ============================================
pause
endlocal
