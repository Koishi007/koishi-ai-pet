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
set "PROJ_DIR=%~dp0"
if "!PROJ_DIR:~-1!"=="\" set "PROJ_DIR=!PROJ_DIR:~0,-1!"
set "API_URL=https://api.github.com/repos/%REPO%/releases/latest"

:: ===== 2. 读取本地版本（用临时 ps1 解析，避免 cmd 引号/括号冲突）=====
set "PS_LOCAL=%TEMP%\_koishi_local_%RANDOM%.ps1"
> "%PS_LOCAL%" echo $c = Get-Content '%~dp0pyproject.toml' -Raw
>> "%PS_LOCAL%" echo if ($c -match 'version\s*=\s*"([^"]+)"') { Write-Output $matches[1] }

set "LOCAL_VER="
for /f "usebackq delims=" %%v in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_LOCAL%"`) do set "LOCAL_VER=%%v"
del /q "%PS_LOCAL%" 2>nul
echo 本地版本: !LOCAL_VER!

:: ===== 3. 获取最新 Release 版本 =====
echo.
echo [1/4] 查询 GitHub 最新 Release...
set "PS_TAG=%TEMP%\_koishi_tag_%RANDOM%.ps1"
> "%PS_TAG%" echo $ProgressPreference = 'SilentlyContinue'
>> "%PS_TAG%" echo try {
>> "%PS_TAG%" echo   $r = Invoke-RestMethod -Uri '%API_URL%' -Headers @{ 'User-Agent' = 'koishi-updater' } -TimeoutSec 30
>> "%PS_TAG%" echo   Write-Output $r.tag_name
>> "%PS_TAG%" echo } catch {
>> "%PS_TAG%" echo   Write-Output ''
>> "%PS_TAG%" echo }

set "REL_TAG="
for /f "usebackq delims=" %%t in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_TAG%"`) do set "REL_TAG=%%t"
del /q "%PS_TAG%" 2>nul

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
if /i "!REL_VER:~0,1!"=="V" set "REL_VER=!REL_VER:~1!"
echo 最新版本: !REL_VER!

:: 版本相同时提示
if /i "!LOCAL_VER!"=="!REL_VER!" (
    echo.
    echo [提示] 当前已是最新版本: !REL_VER!，无需更新
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
set "PS_DL=%TEMP%\_koishi_dl_%RANDOM%.ps1"
> "%PS_DL%" echo $ProgressPreference = 'SilentlyContinue'
>> "%PS_DL%" echo try {
>> "%PS_DL%" echo   [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
>> "%PS_DL%" echo   Invoke-WebRequest -Uri '!ZIP_URL!' -OutFile '!ZIP_FILE!' -UseBasicParsing -TimeoutSec 120
>> "%PS_DL%" echo } catch {
>> "%PS_DL%" echo   Write-Error $_
>> "%PS_DL%" echo   exit 1
>> "%PS_DL%" echo }

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_DL%"
set "DL_RC=!errorlevel!"
del /q "%PS_DL%" 2>nul
if !DL_RC! neq 0 (
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
set "PS_UNZIP=%TEMP%\_koishi_unzip_%RANDOM%.ps1"
> "%PS_UNZIP%" echo Expand-Archive -Path '!ZIP_FILE!' -DestinationPath '!EXTRACT_DIR!' -Force
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_UNZIP%"
set "UZ_RC=!errorlevel!"
del /q "%PS_UNZIP%" 2>nul
if !UZ_RC! neq 0 (
    echo [错误] 解压失败
    if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
    if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!" 2>nul
    pause
    exit /b 1
)

:: 解压后目录名形如 koishi-ai-pet-1.2.2，查找唯一子目录
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
:: 排除 update.bat/update.sh：正在运行的脚本无法被覆盖，否则 robocopy 报错退出码 8
:: /R:0 /W:0：遇到锁定文件立即失败，不重试挂起
echo   同步到项目目录（保留 venv、logs、config.json、数据库等）...
robocopy "!SRC_DIR!" "!PROJ_DIR!" /E /R:0 /W:0 /XD .git venv logs __pycache__ /XF *.log config.json *.db *.db-journal *.db-wal *.db-shm .deps_installed update.bat update.sh /NFL /NDL /NJH /NJS /NC /NS /NP >nul
set "RC_RC=!errorlevel!"
:: robocopy 退出码 <8 视为成功（1=已复制，2=有额外文件，4=有不匹配，均可接受）
if !RC_RC! geq 8 (
    echo [错误] 同步文件失败，robocopy 退出码: !RC_RC!
    echo        可能有文件被占用，请先关闭正在运行的 Koishi AI Pet 桌宠，然后重试
    if exist "!ZIP_FILE!" del /q "!ZIP_FILE!" 2>nul
    if exist "!EXTRACT_DIR!" rmdir /s /q "!EXTRACT_DIR!" 2>nul
    pause
    exit /b 1
)

:: 单独把新版 update 脚本复制为 .new，供用户手动替换（运行中的脚本无法直接覆盖）
if exist "!SRC_DIR!\update.bat" copy /y "!SRC_DIR!\update.bat" "%~dp0update.bat.new" >nul 2>nul
if exist "!SRC_DIR!\update.sh" copy /y "!SRC_DIR!\update.sh" "%~dp0update.sh.new" >nul 2>nul

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
if exist "%~dp0update.bat.new" (
    echo.
    echo [提示] 检测到新版更新脚本: update.bat.new
    echo        本次未自动覆盖运行中的脚本，可手动用其替换 update.bat
)
echo.
echo   启动方式：
echo     1. 双击桌面 "Koishi AI Pet" 快捷方式
echo     2. 或运行：%~dp0venv\Scripts\koishi.exe
echo ============================================
pause
exit /b 0
