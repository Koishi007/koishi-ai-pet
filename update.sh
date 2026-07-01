#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

REPO="Koishi007/koishi-ai-pet"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

echo "============================================"
echo "  Koishi AI Pet 一键更新脚本"
echo "  从 GitHub 最新 Release 更新"
echo "============================================"
echo ""

# ===== 0. 检查项目根目录 =====
if [ ! -f "$SCRIPT_DIR/pyproject.toml" ]; then
    echo -e "${RED}[错误]${NC} 未找到 pyproject.toml，请把脚本放在项目根目录运行"
    exit 1
fi

# ===== 1. 检查虚拟环境 =====
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
if [ ! -f "$VENV_PYTHON" ]; then
    echo -e "${RED}[错误]${NC} 未检测到虚拟环境 venv，请先运行 setup.sh 完成安装"
    echo "       或手动创建：python3 -m venv venv"
    exit 1
fi

# ===== 2. 读取本地版本 =====
LOCAL_VER="$(grep '^version' "$SCRIPT_DIR/pyproject.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || echo "")"
echo "本地版本: ${LOCAL_VER:-未知}"

# ===== 3. 获取最新 Release 版本 =====
echo ""
echo "[1/4] 查询 GitHub 最新 Release..."

fetch_tag() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -H "User-Agent: koishi-updater" --max-time 30 "$API_URL" 2>/dev/null \
            | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O - --header="User-Agent: koishi-updater" --timeout=30 "$API_URL" 2>/dev/null \
            | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
    else
        echo ""
    fi
}

REL_TAG="$(fetch_tag)"
if [ -z "$REL_TAG" ]; then
    echo -e "${RED}[错误]${NC} 无法获取最新 Release 信息，请检查网络连接"
    echo "       也可手动访问 https://github.com/${REPO}/releases 下载"
    exit 1
fi

# 去掉 tag 开头的 v/V 用于比较
REL_VER="${REL_TAG#v}"
REL_VER="${REL_VER#V}"
echo "最新版本: $REL_VER"

# 版本相同时提示
if [ "$LOCAL_VER" = "$REL_VER" ]; then
    echo ""
    echo -e "${YELLOW}[提示]${NC} 当前已是最新版本 ($REL_VER)，无需更新"
    echo "       如需强制重新下载安装，请删除 venv 后运行 setup.sh"
    exit 0
fi

# ===== 4. 下载 Release 源码包 =====
echo ""
echo "[2/4] 下载最新源码包..."
ZIP_URL="https://github.com/${REPO}/archive/refs/tags/${REL_TAG}.zip"
TMP_DIR="$(mktemp -d)"
ZIP_FILE="$TMP_DIR/source.zip"
EXTRACT_DIR="$TMP_DIR/extract"

echo "   下载: $ZIP_URL"
if command -v curl >/dev/null 2>&1; then
    if ! curl -fsSL --max-time 120 -o "$ZIP_FILE" "$ZIP_URL"; then
        echo -e "${RED}[错误]${NC} 下载失败，请检查网络连接"
        rm -rf "$TMP_DIR"
        exit 1
    fi
elif command -v wget >/dev/null 2>&1; then
    if ! wget -q --timeout=120 -O "$ZIP_FILE" "$ZIP_URL"; then
        echo -e "${RED}[错误]${NC} 下载失败，请检查网络连接"
        rm -rf "$TMP_DIR"
        exit 1
    fi
else
    echo -e "${RED}[错误]${NC} 未找到 curl 或 wget，无法下载"
    rm -rf "$TMP_DIR"
    exit 1
fi
echo "   下载完成"

# ===== 5. 解压并同步 =====
echo ""
echo "[3/4] 解压并同步源码..."
if ! unzip -q "$ZIP_FILE" -d "$EXTRACT_DIR"; then
    echo -e "${RED}[错误]${NC} 解压失败"
    rm -rf "$TMP_DIR"
    exit 1
fi

# 解压后目录名形如 koishi-ai-pet-v1.2.1，查找唯一子目录
SRC_DIR="$(find "$EXTRACT_DIR" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [ -z "$SRC_DIR" ]; then
    echo -e "${RED}[错误]${NC} 解压目录结构异常"
    rm -rf "$TMP_DIR"
    exit 1
fi

# 同步源码：不删除目标额外文件，保留 venv/logs/config.json/*.db 等用户数据
echo "   同步到项目目录（保留 venv、logs、config.json、数据库等）..."
# 优先 rsync，不可用则用 cp 覆盖
if command -v rsync >/dev/null 2>&1; then
    # 排除 update 脚本自身：运行中的脚本不应被覆盖
    rsync -a \
        --exclude '.git' --exclude 'venv' --exclude 'logs' --exclude '__pycache__' \
        --exclude '*.log' --exclude 'config.json' \
        --exclude '*.db' --exclude '*.db-journal' --exclude '*.db-wal' --exclude '*.db-shm' \
        --exclude '.deps_installed' \
        --exclude 'update.bat' --exclude 'update.sh' \
        "$SRC_DIR/" "$SCRIPT_DIR/"
else
    # cp 不带 -n，会覆盖同名文件；保留 venv/logs 等通过跳过实现
    (cd "$SRC_DIR" && find . -mindepth 1 -maxdepth 1 \
        ! -name '.git' ! -name 'venv' ! -name 'logs' ! -name '__pycache__' \
        ! -name 'config.json' ! -name '.deps_installed' \
        ! -name 'update.bat' ! -name 'update.sh' \
        -exec cp -r {} "$SCRIPT_DIR/" \;)
fi
echo -e "   ${GREEN}同步完成${NC}"

# 清理临时文件
rm -rf "$TMP_DIR"

# ===== 6. 激活虚拟环境并更新依赖 =====
echo ""
echo "[4/4] 激活虚拟环境并更新依赖..."
# shellcheck disable=SC1091
source "$SCRIPT_DIR/venv/bin/activate"

python -m pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
    || echo -e "${YELLOW}[警告]${NC} pip 升级失败，继续使用内置版本"

echo "   执行: pip install -e \"$SCRIPT_DIR\""
if ! pip install -e "$SCRIPT_DIR" -q -i https://pypi.tuna.tsinghua.edu.cn/simple; then
    echo -e "${RED}[错误]${NC} 依赖更新失败"
    echo "       请检查网络连接或 pyproject.toml 配置"
    deactivate 2>/dev/null || true
    exit 1
fi
echo -e "   ${GREEN}依赖更新完成${NC}"

# ===== 7. 校验入口可执行文件 =====
LAUNCHER_CMD="$SCRIPT_DIR/venv/bin/koishi"
if [ ! -f "$LAUNCHER_CMD" ]; then
    echo ""
    echo -e "${YELLOW}[警告]${NC} 未找到 $LAUNCHER_CMD"
    echo "       可手动启动: $SCRIPT_DIR/venv/bin/python -m pet"
fi
[ -f "$LAUNCHER_CMD" ] && chmod +x "$LAUNCHER_CMD"

# 退出虚拟环境
deactivate 2>/dev/null || true

echo ""
echo "============================================"
echo -e "  ${GREEN}更新完成！${NC} 已更新到 $REL_TAG"
echo ""
echo "  启动方式："
echo "    1. 应用菜单 / Spotlight 启动 Koishi AI Pet"
echo "    2. 或运行: $LAUNCHER_CMD"
echo "============================================"
