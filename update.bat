@echo off
setlocal enabledelayedexpansion
title Koishi AI Pet - 一键更新

echo ============================================
echo   Koishi AI Pet 一键更新脚本
echo   从 GitHub 最新 Release 更新
echo ============================================
echo.

:: ===== 0. 检查项目根目录 =====
if not exist "%~dp0pyproject.toml" (
    echo [错误] 未找到 pyproject.toml，请把脚本放在项目根目录运行
    pause
    exit /b 1
)

:: ===== 1. 检查虚拟环境 =====
if not exist "%~dp0venv\Scripts\python.exe" (
    echo [错误] 未检测到虚拟环境 venv，请先运行 setup.bat 完成安装
    echo        或手动创建：python -m venv venv
    pause
    exit /b 1
)

set "REPO=Koishi007/koishi-ai-pet"
set "API_URL=https://api.github.com/repos/%REPO%/releases/latest"

:: ===== 2. 读取本地版本 =====
set "LOCAL_VER="
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"version" "%~dp0pyproject.toml"') do (
    set "VER_LINE=%%a"
    set "VER_LINE=!VER_LINE: =!"
    set "VER_LINE=!VER_LINE:"=!"
    if not defined LOCAL_VER set "LOCAL_VER=!VER_LINE!"
)
echo 本地版本: !LOCAL_VER!

:: ===== 3. 获取最新 Release 版本 =====
echo.
echo [1/4] 查询 GitHub 最新 Release...
for /f "usebackq delims=" %%t in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { $r=Invoke-RestMethod -Uri '%API_URL%' -Headers @{'User-Agent'='koishi-updater'} -TimeoutSec 30; Write-Output $r.tag_name } catch { Write-Output '' }"`) do set "REL_TAG=%%t"

if not defined REL_TAG (
    echo [错误] 无法获取最新 Release 信息，请检查网络连接
    echo        也可手动访问 https://github.com/%REPO%/releases 下载
    pause
    exit /b 1
)
if "!REL_TAG!"=="" (
    echo [错误] 无法获取最新 Release 信息，请检查网络连接
    pause
    exit /b 1
)

:: 去掉 tag 开头的 v/V 用于比较
set "REL_VER=!REL_TAG!"
if /i "!REL_VER:~0,1!"=="v" set "REL_VER=!REL_VER:~1!"
echo 最新版本: !REL_VER!

:: 版本相同时提示
if /i "!LOCAL_VER!"=="!REL_VER!" (
    echo.
    echo [提示] 当前已是最新版本 (!REL_VER!)，无需更新
    echo        如需强制重新下载安装，请删除 venv 后运行 setup.bat
    pause
    exit /b 0
)

:: ===== 4. 下载 Release 源码包 =====
echo.
echo [2/4] 下载最新源码包...
set "ZIP_URL=https://github.com/%REPO%/archive/refs/tags/!REL_TAG!.zip"
set "ZIP_FILE=%TEMP%\koishi-ai-pet-!REL_TAG!.zip"
set "EXTRACT_DIR=%TEMP%\koishi-ai-pet-extract-%RANDOM%"

echo   下载: !ZIP_URL!
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '!ZIP_URL!' -OutFile '!ZIP_FILE!' -UseBasicParsing -TimeoutSec 120 } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
    echo [错误] 下载失败，请检查网络连接
    if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
    pause
    exit /b 1
)
echo   下载完成

:: ===== 5. 解压 =====
echo.
echo [3/4] 解压并同步源码...
if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '!ZIP_FILE!' -DestinationPath '!EXTRACT_DIR!' -Force"
if errorlevel 1 (
    echo [错误] 解压失败
    if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
    if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!" 2>nul
    pause
    exit /b 1
)

:: 解压后目录名形如 koishi-ai-pet-v1.2.1，查找唯一子目录
set "SRC_DIR="
for /d %%d in ("!EXTRACT_DIR!\*") do (
    if not defined SRC_DIR set "SRC_DIR=%%d"
)
if not defined SRC_DIR (
    echo [错误] 解压目录结构异常
    if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
    if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!" 2>nul
    pause
    exit /b 1
)

:: 同步源码：不删除目标额外文件，保留 venv/logs/config.json/*.db 等用户数据
echo   同步到项目目录（保留 venv、logs、config.json、数据库等）...
robocopy "!SRC_DIR!" "%~dp0" /E /XD .git venv logs __pycache__ /XF *.log config.json *.db *.db-journal *.db-wal *.db-shm .deps_installed /NFL /NDL /NJH /NJS /NC /NS /NP >nul
:: robocopy 退出码 <8 视为成功
if errorlevel 8 (
    echo [错误] 同步文件失败
    if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
    if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!" 2>nul
    pause
    exit /b 1
)

:: 清理临时文件
if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!" 2>nul
echo   同步完成

:: ===== 6. 激活虚拟环境并更新依赖 =====
echo.
echo [4/4] 激活虚拟环境并更新依赖...
call "%~dp0venv\Scripts\activate.bat"
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败
    pause
    exit /b 1
)
python -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul
pip install -e "%~dp0." -q -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [错误] 依赖更新失败，请检查网络或 pyproject.toml
    pause
    exit /b 1
)
echo   依赖更新完成

:: ===== 7. 校验入口可执行文件 =====
if not exist "%~dp0venv\Scripts\koishi.exe" (
    echo.
    echo [警告] 未找到 venv\Scripts\koishi.exe
    echo        可手动启动：venv\Scripts\python.exe -m pet
)

echo.
echo ============================================
echo   更新完成！已更新到 !REL_TAG!
echo.
echo   启动方式：
echo     1. 双击桌面 "Koishi AI Pet" 快捷方式
echo     2. 或运行：%~dp0venv\Scripts\koishi.exe
echo ============================================
pause
exit /b 0
