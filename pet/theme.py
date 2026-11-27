"""古明地恋手绘风样式令牌：色板、圆角、描边、投影与字体链。"""

# 纸底与墨色
PAPER = "#F8F1DF"
PAPER_DEEP = "#F0E7CD"
SURFACE = "#FFFDF4"
SURFACE_WARM = "#FBF6E6"
INK = "#1F2E26"
INK_SOFT = "#3B4C41"
INK_MUTED = "#7C8474"
LINE = "#DCD2B6"

# 主色与语义色
PRIMARY = "#47836F"
PRIMARY_DEEP = "#31604F"
PRIMARY_SOFT = "#D7E8DF"
PRIMARY_HOVER = "#52947E"
ACCENT = "#9DBE4D"
ACCENT_DEEP = "#6F8F2E"
ACCENT_SOFT = "#EBF2CF"
ACCENT_HOVER = "#AFCD61"
INFO = "#4E7F8C"
INFO_SOFT = "#DCEAEC"
# 上衣黄：标题图标徽章与计时徽章，取自 idle 素材上衣主色
YELLOW = "#F8E880"

# 气泡底色：三种浅色拉开色相，图标统一墨色
BUBBLE_CHAT = "#CBE6D8"
BUBBLE_FEED = "#F7DD93"
BUBBLE_MUSIC = "#CFDFF3"
# 说话气泡底：SURFACE 掺 8% PRIMARY
BUBBLE_SPEECH = "#F0F3E9"
# 第三只眼：气泡徽章的淡紫底与深紫描线，只用于角色标记
THIRD_EYE = "#D7C0E8"
THIRD_EYE_LINE = "#5E4370"
DANGER = "#BF5A4C"
DANGER_HOVER = "#D06A5C"
DANGER_SOFT = "#F6DED8"

TEXT_ON_PRIMARY = SURFACE
TEXT_ON_ACCENT = INK

# 形状
RADIUS_SM = 8
RADIUS_MD = 11
RADIUS_LG = 14
RADIUS_XL = 20
BORDER = 2
SHADOW_WINDOW = 3
TITLE_BAR_H = 40

SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16

# 字体
UI_FONTS = ("HarmonyOS Sans SC", "Microsoft YaHei UI", "PingFang SC", "Segoe UI")
MONO_FONTS = ("Cascadia Mono", "JetBrains Mono", "Consolas", "Microsoft YaHei UI")
# QSS 用的字面量，避免模块级拼接调用
MONO_FONT_CSS = '"Cascadia Mono", "JetBrains Mono", "Consolas", "Microsoft YaHei UI"'

# 终端暗色板：与手绘皮肤分离，值保持不变
TERMINAL_BG = "#17191a"
TERMINAL_PANEL = "#1f2224"
TERMINAL_CODE_BG = "#101213"
TERMINAL_BORDER = "#2f3437"
TERMINAL_HOVER = "#262a2c"

TERMINAL_TEXT = "#e8e5de"
TERMINAL_STDOUT = "#c9c6bf"
TERMINAL_STDERR = "#e08b86"
TERMINAL_MUTED = "#8a9195"
TERMINAL_MUTED_LIGHT = "#a8adaf"

TERMINAL_ACCENT = "#5fbfa8"
TERMINAL_ACCENT_DIM = "#3f7f72"
TERMINAL_ACCENT_TEXT = "#0f1413"
TERMINAL_USER = "#d9a441"
TERMINAL_DANGER = "#d9534f"
TERMINAL_IDLE = "#4a4f52"

TERMINAL_WINDOW_RADIUS = 8
TERMINAL_CONTROL_RADIUS = 6
TERMINAL_CARD_RADIUS = 10
TERMINAL_BLOCK_GAP = 14


def rgba(hex_value: str, alpha: float) -> str:
    """#RRGGBB 转 Qt 可解析的 rgba() 字符串，alpha 取 0~1。"""
    value = hex_value.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha:g})"


def radius_for(height: int) -> int:
    """胶囊半径：高度的一半。"""
    return max(1, int(height) // 2)


def ui_font_stack() -> str:
    """QSS 用的中文字体链。"""
    return '", "'.join(UI_FONTS)


def mono_font_stack() -> str:
    """QSS 用的等宽字体链。"""
    return '", "'.join(MONO_FONTS)
