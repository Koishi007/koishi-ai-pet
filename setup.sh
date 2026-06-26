#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "============================================"
echo "  Koishi AI Pet 一键安装脚本"
echo "============================================"
echo ""

# ===== 1. 检测操作系统 =====
OS="$(uname -s)"
case "$OS" in
    Darwin)   OS_NAME="macOS" ;;
    Linux)    OS_NAME="Linux" ;;
    *)        echo -e "${RED}[错误]${NC} 不支持的操作系统: $OS"; exit 1 ;;
esac
echo "检测到系统: $OS_NAME"
echo ""

# ===== 2. 检查 Python 3.11+ =====
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        PY_MAJOR=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo "0")
        PY_MINOR=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo "0")
        if [ "$PY_MAJOR" -gt 3 ] 2>/dev/null || { [ "$PY_MAJOR" -eq 3 ] 2>/dev/null && [ "$PY_MINOR" -ge 11 ] 2>/dev/null; }; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}[错误]${NC} 未检测到 Python 3.11+"
    echo "       请先安装 Python："
    echo "       macOS:  brew install python"
    echo "       Linux:  sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
echo "Python: $($PYTHON --version)"

# ===== 3. 检查 pyproject.toml =====
if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
    echo -e "${RED}[错误]${NC} 未找到 pyproject.toml，请把脚本放在项目根目录运行"
    exit 1
fi

# ===== 4. 创建虚拟环境 =====
echo ""
echo "[1/4] 创建虚拟环境..."
VENV_DIR="$SCRIPT_DIR/venv"
if [ -f "$VENV_DIR/bin/python" ]; then
    echo "   虚拟环境已存在，跳过"
else
    # 清理可能存在的半残 venv
    if [ -d "$VENV_DIR" ]; then
        echo "   检测到损坏的虚拟环境，正在重建..."
        rm -rf "$VENV_DIR"
    fi
    if ! "$PYTHON" -m venv "$VENV_DIR"; then
        echo -e "${RED}[错误]${NC} 创建虚拟环境失败"
        exit 1
    fi
    echo "   完成"
fi

# ===== 5. 激活并升级 pip =====
echo ""
echo "[2/4] 激活虚拟环境并升级 pip..."
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo -e "${RED}[错误]${NC} 虚拟环境损坏，缺少 activate 脚本"
    echo "       请删除 venv 目录后重新运行：rm -rf $VENV_DIR"
    exit 1
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
    || echo -e "${YELLOW}[警告]${NC} pip 升级失败，继续使用内置版本"

# ===== 6. 安装依赖 =====
echo ""
echo "[3/4] 安装依赖..."

echo "   安装: pip install -e \"$SCRIPT_DIR\""

if ! pip install -e "$SCRIPT_DIR" -q -i https://pypi.tuna.tsinghua.edu.cn/simple; then
    echo -e "${RED}[错误]${NC} 安装依赖失败"
    echo "       请检查网络连接或 pyproject.toml 配置"
    deactivate 2>/dev/null || true
    exit 1
fi
echo "   完成"

# ===== 7. 校验入口可执行文件 =====
LAUNCHER_CMD="$VENV_DIR/bin/koishi"
if [ ! -f "$LAUNCHER_CMD" ]; then
    echo -e "${RED}[错误]${NC} 未找到 $LAUNCHER_CMD"
    echo "       请确认 pyproject.toml 中已配置 [project.scripts] koishi = \"...\""
    echo "       可手动启动: $VENV_DIR/bin/python -m pet"
    deactivate 2>/dev/null || true
    exit 1
fi
chmod +x "$LAUNCHER_CMD"

# ===== 8. 创建桌面启动器 =====
echo ""
echo "[4/4] 创建桌面启动器..."

if [ "$OS_NAME" = "Linux" ]; then
    DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$DESKTOP_DIR" || {
        echo -e "${YELLOW}[警告]${NC} 无法创建 $DESKTOP_DIR，跳过"
    }

    DESKTOP_FILE="$DESKTOP_DIR/koishi-ai-pet.desktop"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Koishi AI Pet
Comment=AI 桌面虚拟宠物
Exec=$LAUNCHER_CMD
Path=$SCRIPT_DIR
Icon=$SCRIPT_DIR/assets/icon/sys_tray.png
Terminal=false
Categories=Utility;
EOF
    chmod +x "$DESKTOP_FILE"

    if [ -f "$DESKTOP_FILE" ]; then
        # 刷新桌面数据库（可选，失败不影响）
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
        echo -e "   ${GREEN}已创建:${NC} $DESKTOP_FILE"
        echo "   可在应用菜单中找到 Koishi AI Pet"
    else
        echo -e "${YELLOW}[警告]${NC} 桌面启动器创建失败"
    fi

elif [ "$OS_NAME" = "macOS" ]; then
    # 创建 .app 包，实现双击启动
    APP_DIR="$HOME/Applications/Koishi AI Pet.app"
    mkdir -p "$APP_DIR/Contents/MacOS"
    mkdir -p "$APP_DIR/Contents/Resources"

    # 生成 .app 图标（PNG → ICNS）
    ICON_SRC="$SCRIPT_DIR/assets/icon/sys_tray.png"
    ICON_ICNS="$APP_DIR/Contents/Resources/applet.icns"
    if [ -f "$ICON_SRC" ]; then
        "$VENV_DIR/bin/python" -c "from PIL import Image; img=Image.open('$ICON_SRC'); img.save('$ICON_ICNS', format='ICNS')" 2>/dev/null || \
            echo -e "   ${YELLOW}[警告]${NC} 图标转换失败，.app 将使用默认图标"
    fi

    cat > "$APP_DIR/Contents/MacOS/launch.sh" << EOF
#!/usr/bin/env bash
cd "$SCRIPT_DIR"
exec "$SCRIPT_DIR/venv/bin/koishi"
EOF
    chmod +x "$APP_DIR/Contents/MacOS/launch.sh"

    cat > "$APP_DIR/Contents/Info.plist" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Koishi AI Pet</string>
    <key>CFBundleDisplayName</key>
    <string>Koishi AI Pet</string>
    <key>CFBundleExecutable</key>
    <string>launch.sh</string>
    <key>CFBundleIconFile</key>
    <string>applet.icns</string>
    <key>CFBundleIdentifier</key>
    <string>com.koishi.aipet</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
</dict>
</plist>
EOF

    echo -e "   ${GREEN}已创建:${NC} $APP_DIR"
    echo "   可在 Spotlight 或 ~/Applications 中双击启动"
fi

# 退出虚拟环境
deactivate 2>/dev/null || true

echo ""
echo "============================================"
echo -e "  ${GREEN}安装完成！${NC}"
echo ""
echo "  启动方式："
if [ "$OS_NAME" = "Linux" ]; then
    echo "    1. 应用菜单中点击 Koishi AI Pet"
elif [ "$OS_NAME" = "macOS" ]; then
    echo "    1. Spotlight 搜索 Koishi AI Pet"
    echo "    2. 或双击 ~/Applications/Koishi AI Pet.app"
fi
echo "    3. 终端运行: $LAUNCHER_CMD"
echo "============================================"
