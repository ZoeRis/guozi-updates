import ctypes
from ctypes import wintypes
import json
import math
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime

try:
    import winsound
except ImportError:
    winsound = None

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QWidget,
)


# ---------- 路径 ----------

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    ASSET_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
else:
    APP_DIR = Path(__file__).resolve().parent
    ASSET_DIR = APP_DIR

IMAGE_DIR = ASSET_DIR / "images"
POSITION_FILE = APP_DIR / "position.json"
SETTINGS_FILE = APP_DIR / "settings.json"
MESSAGES_FILE = APP_DIR / "messages.txt"
ACTIONS_FILE = APP_DIR / "actions.json"
GREETINGS_FILE = APP_DIR / "greetings.json"
ONLINE_IMAGE_DIR = APP_DIR / "online_images"
ONLINE_SOUND_DIR = APP_DIR / "online_sounds"
DEBUG_LOG_FILE = APP_DIR / "guozi_debug.log"


def debug_log(message):
    """写入轻量诊断日志；失败时不影响果子运行。"""

    try:
        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        with DEBUG_LOG_FILE.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(
                f"[{timestamp}] {message}\n"
            )
    except OSError:
        pass

RAW_REPO_BASE_URL = (
    "https://raw.githubusercontent.com/"
    "ZoeRis/guozi-updates/main/"
)
CDN_REPO_BASE_URL = (
    "https://cdn.jsdelivr.net/gh/"
    "ZoeRis/guozi-updates@main/"
)
UPDATE_INFO_URLS = (
    RAW_REPO_BASE_URL + "version.json",
    CDN_REPO_BASE_URL + "version.json",
)
MAX_ACTION_IMAGE_BYTES = 12 * 1024 * 1024
MAX_ACTION_SOUND_BYTES = 8 * 1024 * 1024

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
VK_LBUTTON = 0x01

DESKTOP_WINDOW_CLASSES = {
    "Progman",
    "WorkerW",
    "SHELLDLL_DefView",
    "SysListView32",
}


def configure_windows_mouse_api():
    """配置本功能使用的少量 Windows API。"""

    if sys.platform != "win32":
        return

    user32 = ctypes.windll.user32

    user32.GetAsyncKeyState.argtypes = [
        ctypes.c_int
    ]
    user32.GetAsyncKeyState.restype = (
        ctypes.c_short
    )

    user32.GetCursorPos.argtypes = [
        ctypes.POINTER(wintypes.POINT)
    ]
    user32.GetCursorPos.restype = (
        wintypes.BOOL
    )

    user32.WindowFromPoint.argtypes = [
        wintypes.POINT
    ]
    user32.WindowFromPoint.restype = (
        wintypes.HWND
    )

    user32.GetParent.argtypes = [
        wintypes.HWND
    ]
    user32.GetParent.restype = wintypes.HWND

    user32.GetClassNameW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetClassNameW.restype = (
        ctypes.c_int
    )


configure_windows_mouse_api()


def keep_widget_topmost(widget):
    """在 Windows 上重新确认窗口置顶，同时不抢输入焦点。"""

    if (
        sys.platform != "win32"
        or widget is None
        or not widget.isVisible()
    ):
        return

    try:
        hwnd = int(widget.winId())
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOACTIVATE,
        )
    except (AttributeError, OSError, TypeError, ValueError):
        pass


DEFAULT_SETTINGS = {
    "pet_size": 150,
    "walk_speed": 2,
    "movement_mode": "horizontal",
    "speech_wait_min": 30,
    "speech_wait_max": 90,
    "speech_duration": 3,
    "action_wait_min": 45,
    "action_wait_max": 100,
    "sleep_wait_min": 90,
    "sleep_wait_max": 180,
    "sleep_duration_min": 30,
    "sleep_duration_max": 90,
    "sleep_frame_interval": 900,
}

DEFAULT_MESSAGES = [
    "你好呀！",
    "果子在这里。",
    "今天也要开心。",
    "你刚刚点我啦！",
    "我要出去散步。",
    "记得保存文件！",
]

DEFAULT_GREETINGS = {
    "morning": [
        "早呀！",
        "早上好耶！",
        "新的一天开始啦。",
    ],
    "late_morning": [
        "上午好呀！",
        "今天也慢慢来。",
        "果子来看看你。",
    ],
    "noon": [
        "到饭点啦！",
        "中午好耶！",
        "记得吃点东西呀。",
    ],
    "afternoon": [
        "下午好呀！",
        "果子还在这里。",
        "下午也慢慢来。",
    ],
    "evening": [
        "晚上好耶！",
        "今天过得怎么样呀？",
        "果子来陪你啦。",
    ],
    "late_night": [
        "这么晚还没睡呀？",
        "夜深啦。",
        "果子陪你待一会儿。",
    ],
}


class SpeechBubble(QWidget):
    """自己绘制背景的自适应气泡。"""

    MIN_WIDTH = 220
    MAX_WIDTH = 520
    MIN_HEIGHT = 70
    MAX_HEIGHT = 260
    HORIZONTAL_PADDING = 24
    VERTICAL_PADDING = 18

    def __init__(self):
        super().__init__()
        debug_log("DesktopPet init")

        self.text = ""

        self.setFixedSize(
            self.MIN_WIDTH,
            self.MIN_HEIGHT,
        )
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )

    def bubble_font(self):
        return QFont("Microsoft YaHei", 10)

    def resize_for_text(self, text):
        """短句保持小气泡，长信息自动扩大并换行。"""

        font = self.bubble_font()
        metrics = QFontMetrics(font)

        max_text_width = (
            self.MAX_WIDTH
            - self.HORIZONTAL_PADDING
        )

        # 先看单行理想宽度。短句不会突然变成大气泡，
        # 更新信息过长时再限制到最大宽度并自动换行。
        lines = str(text).splitlines() or [""]
        single_line_width = max(
            metrics.horizontalAdvance(line)
            for line in lines
        )

        target_text_width = min(
            max(
                single_line_width + 2,
                self.MIN_WIDTH
                - self.HORIZONTAL_PADDING,
            ),
            max_text_width,
        )

        bounds = metrics.boundingRect(
            0,
            0,
            target_text_width,
            2000,
            int(
                Qt.AlignmentFlag.AlignCenter
                | Qt.TextFlag.TextWordWrap
            ),
            str(text),
        )

        width = max(
            self.MIN_WIDTH,
            min(
                self.MAX_WIDTH,
                bounds.width()
                + self.HORIZONTAL_PADDING,
            ),
        )
        height = max(
            self.MIN_HEIGHT,
            min(
                self.MAX_HEIGHT,
                bounds.height()
                + self.VERTICAL_PADDING,
            ),
        )

        # 长文本已经触发换行时，直接给足计算用的宽度，
        # 防止 boundingRect 因最后一行较短又把气泡缩窄。
        if single_line_width > max_text_width:
            width = self.MAX_WIDTH

        self.setFixedSize(width, height)

    def set_text(self, text):
        self.text = str(text)
        self.resize_for_text(self.text)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        rect = QRectF(
            2,
            2,
            self.width() - 4,
            self.height() - 4,
        )

        painter.setPen(
            QPen(QColor("#6b514a"), 2)
        )
        painter.setBrush(
            QColor(255, 255, 255, 245)
        )
        painter.drawRoundedRect(rect, 16, 16)

        font = self.bubble_font()
        painter.setFont(font)
        painter.setPen(QColor("#3b2d2a"))

        text_rect = rect.adjusted(
            10,
            7,
            -10,
            -7,
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignCenter
            | Qt.TextFlag.TextWordWrap,
            self.text,
        )


class SleepZzzWidget(QWidget):
    """睡觉时缓慢飘动的 Zzz。"""

    def __init__(self):
        super().__init__()

        self.phase = 0.0

        self.setFixedSize(78, 72)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(120)
        self.animation_timer.timeout.connect(
            self.update_animation
        )

        self.hide()

    def start_animation(self):
        self.phase = 0.0
        self.show()
        self.raise_()
        self.animation_timer.start()

    def stop_animation(self):
        self.animation_timer.stop()
        self.hide()

    def update_animation(self):
        self.phase += 0.16
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            True,
        )

        z_items = [
            (7, 55, 14, 135, 0.0),
            (27, 38, 18, 185, 0.8),
            (49, 20, 22, 235, 1.6),
        ]

        for x, y, size, alpha, offset in z_items:
            float_y = int(
                math.sin(self.phase + offset) * 3
            )

            color = QColor("#6b514a")
            color.setAlpha(alpha)

            font = QFont("Arial", size)
            font.setBold(True)

            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(x, y + float_y, "Z")


class DesktopPet(QLabel):

    online_update_ready = Signal(object)
    online_update_failed = Signal(str)

    def __init__(self):
        super().__init__()

        # ---------- 基础数据 ----------

        self.settings = {}
        self.messages = []
        self.actions = []
        self.greetings = {}
        # 每个音效只保留一个长期复用的 QSoundEffect。
        # QSoundEffect 本身就是给低延迟反馈音效使用的；
        # 反复复用同一个对象比创建多个并发播放器更稳定。
        self.sound_effects = {}
        self.sound_cache_generation = 0

        self.custom_action_frames = []
        self.custom_action_frame_index = 0
        self.custom_action_frames_left = 0

        self.custom_action_steps = []
        self.custom_action_step_index = -1
        self.custom_action_current_step = None
        self.custom_action_step_started = 0.0
        self.custom_action_step_duration = 0
        self.custom_action_step_start = None
        self.custom_action_step_target = None
        self.custom_action_origin = None
        self.custom_action_frame_sequence = []

        self.random_action_has_played = False
        self.startup_greeting_pending = False
        self.startup_action_pending = False
        self.action_last_triggered = {}

        # 连戳循环：
        # 第 1 组三下 -> “你怎么还戳呀”
        # 第 2 组三下 -> “你怎么还戳呀”
        # 第 3 组三下 -> “还戳！我跑啦！”
        # 然后整轮重新开始。
        self.poke_group_count = 0
        self.poke_warning_count = 0
        self.poke_input_locked = False
        self.poke_cycle_reset_pending = False

        self.active_custom_action_trigger = None

        self.state = "normal"
        self.walking_paused = False

        self.update_in_progress = False
        self.pending_online_update_package = None

        self.drag_position = None
        self.press_position = None
        self.is_dragging = False
        self.drag_mode = None
        self.drag_preserved_state = None
        self.drag_last_window_position = None
        self.drag_walk_move_was_active = False
        self.drag_bounce_was_active = False
        self.drag_custom_action_pause_started = None
        self.ignore_next_left_release = False
        self.click_woke_from_sleep = False

        # drag1.PNG / drag2.PNG 的固定抓取点。
        # 原图约为 (527, 56) / 1000×1000。
        self.drag_grab_ratio_x = 0.527
        self.drag_grab_ratio_y = 0.056

        self.walk_direction = 1
        self.walk_steps_left = 0
        self.walk_frame_index = 0
        self.walk_float_x = 0.0
        self.walk_float_y = 0.0
        self.walk_velocity_x = 0.0
        self.walk_velocity_y = 0.0
        self.walk_target_x = 0
        self.walk_target_y = 0

        self.summon_run_active = False
        self.global_left_was_down = False
        self.global_first_click_time = 0.0
        self.global_first_click_point = None

        self.sleep_frame_index = 0
        self.drag_frame_index = 0

        # 松开拖动后的轻微回弹
        self.bounce_frame_index = 0
        self.bounce_base_position = None
        self.bounce_offsets = [
            0, -5, -9, -5, 0,
            -3, -6, -3, 0,
        ]

        self.load_settings()
        self.load_messages()
        self.load_actions()
        self.preload_action_sounds()
        self.load_greetings()
        self.load_all_images()

        self.setPixmap(self.normal)
        self.setFixedSize(self.normal.size())

        # ---------- 窗口 ----------

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )

        # ---------- 气泡 ----------

        self.speech_bubble = SpeechBubble()
        self.sleep_zzz = SleepZzzWidget()

        self.online_update_ready.connect(
            self.receive_online_update_package
        )
        self.online_update_failed.connect(
            self.finish_online_update_failure
        )

        self.speech_hide_timer = QTimer(self)
        self.speech_hide_timer.setSingleShot(True)
        self.speech_hide_timer.timeout.connect(
            self.speech_bubble.hide
        )

        self.auto_speech_timer = QTimer(self)
        self.auto_speech_timer.setSingleShot(True)
        self.auto_speech_timer.timeout.connect(
            self.auto_speak
        )

        # ---------- 眨眼 ----------

        self.blink_wait_timer = QTimer(self)
        self.blink_wait_timer.setSingleShot(True)
        self.blink_wait_timer.timeout.connect(
            self.close_eyes
        )

        self.blink_close_timer = QTimer(self)
        self.blink_close_timer.setSingleShot(True)
        self.blink_close_timer.timeout.connect(
            self.open_eyes
        )

        # ---------- 开机 hello 动画 ----------

        self.startup_hello_timer = QTimer(self)
        self.startup_hello_timer.setInterval(220)
        self.startup_hello_timer.timeout.connect(
            self.advance_startup_hello_frame
        )
        self.startup_hello_frame_index = 0

        # ---------- 开心 ----------

        self.happy_timer = QTimer(self)
        self.happy_timer.setSingleShot(True)
        self.happy_timer.timeout.connect(
            self.back_to_normal
        )

        # 单击要稍等一下，确认用户没有继续双击
        self.single_click_timer = QTimer(self)
        self.single_click_timer.setSingleShot(True)
        self.single_click_timer.timeout.connect(
            self.handle_single_click
        )

        # 特殊戳击动作完整结束后，再锁住 0.5 秒。
        # 这 0.5 秒内左键点击和拖动都会被忽略。
        self.poke_cooldown_timer = QTimer(self)
        self.poke_cooldown_timer.setSingleShot(True)
        self.poke_cooldown_timer.timeout.connect(
            self.unlock_poke_input
        )

        # ---------- 线上动作 ----------

        self.custom_action_timer = QTimer(self)
        self.custom_action_timer.timeout.connect(
            self.update_custom_action_step
        )

        self.random_action_timer = QTimer(self)
        self.random_action_timer.setSingleShot(True)
        self.random_action_timer.timeout.connect(
            self.try_random_action
        )

        # ---------- 在线更新应用 ----------

        self.pending_update_timer = QTimer(self)
        self.pending_update_timer.setInterval(100)
        self.pending_update_timer.timeout.connect(
            self.try_apply_pending_online_update
        )

        # ---------- 散步 ----------

        self.walk_wait_timer = QTimer(self)
        self.walk_wait_timer.setSingleShot(True)
        self.walk_wait_timer.timeout.connect(
            self.start_walking
        )

        self.walk_move_timer = QTimer(self)
        self.walk_move_timer.setInterval(30)
        self.walk_move_timer.timeout.connect(
            self.update_walking
        )

        self.walk_animation_timer = QTimer(self)
        self.walk_animation_timer.setInterval(160)
        self.walk_animation_timer.timeout.connect(
            self.update_walk_frame
        )

        # ---------- 拖动动画 ----------

        self.drag_animation_timer = QTimer(self)
        self.drag_animation_timer.setInterval(180)
        self.drag_animation_timer.timeout.connect(
            self.update_drag_frame
        )

        # ---------- 松手回弹 ----------

        self.bounce_timer = QTimer(self)
        self.bounce_timer.setInterval(55)
        self.bounce_timer.timeout.connect(
            self.update_release_bounce
        )

        # ---------- 睡觉 ----------

        self.sleep_wait_timer = QTimer(self)
        self.sleep_wait_timer.setSingleShot(True)
        self.sleep_wait_timer.timeout.connect(
            self.try_sleep
        )

        self.sleep_duration_timer = QTimer(self)
        self.sleep_duration_timer.setSingleShot(True)
        self.sleep_duration_timer.timeout.connect(
            self.wake_up
        )

        self.sleep_animation_timer = QTimer(self)
        self.sleep_animation_timer.timeout.connect(
            self.update_sleep_frame
        )
        self.apply_timer_settings()

        # ---------- 置顶保护 ----------

        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(700)
        self.topmost_timer.timeout.connect(
            self.keep_all_windows_on_top
        )
        self.topmost_timer.start()

        # ---------- 桌面双击召唤 ----------

        self.global_mouse_timer = QTimer(self)
        self.global_mouse_timer.setInterval(20)
        self.global_mouse_timer.timeout.connect(
            self.poll_global_mouse
        )

        if sys.platform == "win32":
            self.global_mouse_timer.start()

        # ---------- 托盘 ----------

        self.setup_tray_icon()

        self.schedule_blink()
        self.schedule_walk()
        self.schedule_auto_speech()
        self.schedule_random_action()
        self.schedule_sleep()

    # ---------- 设置与文件 ----------

    def load_settings(self):
        settings = dict(DEFAULT_SETTINGS)

        if SETTINGS_FILE.exists():
            try:
                loaded = json.loads(
                    SETTINGS_FILE.read_text(
                        encoding="utf-8"
                    )
                )
                if isinstance(loaded, dict):
                    settings.update(loaded)
            except (
                OSError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ):
                pass

        # 防止无效数字导致程序失控
        settings["pet_size"] = max(
            60,
            min(int(settings["pet_size"]), 600),
        )
        settings["walk_speed"] = max(
            1,
            min(int(settings["walk_speed"]), 20),
        )

        movement_mode = str(
            settings.get(
                "movement_mode",
                "horizontal",
            )
        ).strip().lower()

        if movement_mode not in (
            "horizontal",
            "full_screen",
        ):
            movement_mode = "horizontal"

        settings["movement_mode"] = movement_mode

        for key in (
            "speech_wait_min",
            "speech_wait_max",
            "speech_duration",
            "action_wait_min",
            "action_wait_max",
            "sleep_wait_min",
            "sleep_wait_max",
            "sleep_duration_min",
            "sleep_duration_max",
        ):
            settings[key] = max(
                1,
                int(settings[key]),
            )

        settings["sleep_frame_interval"] = max(
            100,
            int(settings["sleep_frame_interval"]),
        )

        if (
            settings["speech_wait_min"]
            > settings["speech_wait_max"]
        ):
            settings["speech_wait_min"], settings[
                "speech_wait_max"
            ] = (
                settings["speech_wait_max"],
                settings["speech_wait_min"],
            )

        if (
            settings["action_wait_min"]
            > settings["action_wait_max"]
        ):
            settings["action_wait_min"], settings[
                "action_wait_max"
            ] = (
                settings["action_wait_max"],
                settings["action_wait_min"],
            )

        if (
            settings["sleep_wait_min"]
            > settings["sleep_wait_max"]
        ):
            settings["sleep_wait_min"], settings[
                "sleep_wait_max"
            ] = (
                settings["sleep_wait_max"],
                settings["sleep_wait_min"],
            )

        if (
            settings["sleep_duration_min"]
            > settings["sleep_duration_max"]
        ):
            settings["sleep_duration_min"], settings[
                "sleep_duration_max"
            ] = (
                settings["sleep_duration_max"],
                settings["sleep_duration_min"],
            )

        self.settings = settings

    def save_settings(self):
        """保存本机设置。"""

        try:
            SETTINGS_FILE.write_text(
                json.dumps(
                    self.settings,
                    ensure_ascii=False,
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def load_messages(self):
        messages = []

        if MESSAGES_FILE.exists():
            try:
                messages = [
                    line.strip()
                    for line in MESSAGES_FILE.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            except OSError:
                messages = []

        self.messages = messages or list(
            DEFAULT_MESSAGES
        )

    def load_greetings(self):
        """读取并验证时间问候语。"""

        greetings = {
            key: list(values)
            for key, values in DEFAULT_GREETINGS.items()
        }

        if GREETINGS_FILE.exists():
            try:
                data = json.loads(
                    GREETINGS_FILE.read_text(
                        encoding="utf-8"
                    )
                )

                if not isinstance(data, dict):
                    raise ValueError(
                        "问候语文件必须是对象。"
                    )

                raw_greetings = data.get(
                    "greetings",
                    {},
                )

                if not isinstance(raw_greetings, dict):
                    raise ValueError(
                        "greetings 必须是对象。"
                    )

                for key in DEFAULT_GREETINGS:
                    raw_items = raw_greetings.get(key)

                    if not isinstance(raw_items, list):
                        continue

                    clean_items = [
                        str(item).strip()[:120]
                        for item in raw_items
                        if isinstance(item, str)
                        and item.strip()
                    ]

                    if clean_items:
                        greetings[key] = clean_items[:50]

            except (
                OSError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ):
                pass

        self.greetings = greetings

    def sanitize_action_filename(self, filename):
        """验证动作图片文件名。"""

        if not isinstance(filename, str):
            return None

        filename = filename.strip()

        if (
            not filename
            or Path(filename).name != filename
            or Path(filename).suffix.lower()
            not in (
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            )
        ):
            return None

        return filename

    def sanitize_action_sound_filename(
        self,
        filename,
    ):
        """验证动作音效文件名。"""

        if not isinstance(filename, str):
            return None

        filename = filename.strip()

        if (
            not filename
            or Path(filename).name != filename
            or Path(filename).suffix.lower() != ".wav"
        ):
            return None

        return filename

    def sanitize_action_step(self, raw_step):
        """把一个动作步骤转换为安全、有限的格式。"""

        if not isinstance(raw_step, dict):
            return None

        step_type = str(
            raw_step.get("type", "")
        ).strip().lower()

        if step_type == "say":
            text = str(
                raw_step.get("text", "")
            ).strip()[:120]

            raw_texts = raw_step.get("texts", [])
            texts = []

            if isinstance(raw_texts, list):
                texts = [
                    str(item).strip()[:120]
                    for item in raw_texts[:30]
                    if isinstance(item, str)
                    and item.strip()
                ]

            if not text and not texts:
                return None

            return {
                "type": "say",
                "text": text,
                "texts": texts,
            }

        if step_type == "wait":
            duration = max(
                50,
                min(
                    int(raw_step.get("duration", 300)),
                    15000,
                ),
            )

            return {
                "type": "wait",
                "duration": duration,
            }

        if step_type == "frames":
            raw_frames = raw_step.get("frames", [])

            if not isinstance(raw_frames, list):
                return None

            frames = []

            for filename in raw_frames[:40]:
                safe_name = self.sanitize_action_filename(
                    filename
                )

                if safe_name:
                    frames.append(safe_name)

            if not frames:
                return None

            frame_interval = max(
                60,
                min(
                    int(
                        raw_step.get(
                            "frame_interval",
                            220,
                        )
                    ),
                    5000,
                ),
            )
            loops = max(
                1,
                min(
                    int(raw_step.get("loops", 1)),
                    50,
                ),
            )
            playback = str(
                raw_step.get(
                    "playback",
                    "loop",
                )
            ).strip().lower()

            if playback not in (
                "loop",
                "pingpong",
            ):
                playback = "loop"

            return {
                "type": "frames",
                "frames": frames,
                "frame_interval": frame_interval,
                "loops": loops,
                "playback": playback,
            }

        if step_type == "sound":
            filename = (
                self.sanitize_action_sound_filename(
                    raw_step.get("file", "")
                )
            )

            if filename is None:
                return None

            return {
                "type": "sound",
                "file": filename,
            }

        if step_type == "stop_sound":
            return {
                "type": "stop_sound",
            }

        if step_type == "move_random":
            duration = max(
                100,
                min(
                    int(raw_step.get("duration", 900)),
                    15000,
                ),
            )
            margin = max(
                0,
                min(
                    int(raw_step.get("margin", 20)),
                    300,
                ),
            )

            return {
                "type": "move_random",
                "duration": duration,
                "margin": margin,
            }

        if step_type == "move_edge":
            duration = max(
                100,
                min(
                    int(raw_step.get("duration", 900)),
                    15000,
                ),
            )
            margin = max(
                0,
                min(
                    int(raw_step.get("margin", 20)),
                    300,
                ),
            )
            edge = str(
                raw_step.get("edge", "random")
            ).strip().lower()

            if edge not in (
                "left",
                "right",
                "top",
                "bottom",
                "random",
            ):
                edge = "random"

            return {
                "type": "move_edge",
                "duration": duration,
                "margin": margin,
                "edge": edge,
            }

        if step_type == "move_mouse":
            duration = max(
                100,
                min(
                    int(raw_step.get("duration", 800)),
                    15000,
                ),
            )
            offset_x = max(
                -600,
                min(
                    int(raw_step.get("offset_x", 0)),
                    600,
                ),
            )
            offset_y = max(
                -600,
                min(
                    int(raw_step.get("offset_y", 0)),
                    600,
                ),
            )

            return {
                "type": "move_mouse",
                "duration": duration,
                "offset_x": offset_x,
                "offset_y": offset_y,
            }

        if step_type == "move_away_mouse":
            duration = max(
                100,
                min(
                    int(raw_step.get("duration", 700)),
                    15000,
                ),
            )
            distance = max(
                20,
                min(
                    int(raw_step.get("distance", 180)),
                    1200,
                ),
            )

            return {
                "type": "move_away_mouse",
                "duration": duration,
                "distance": distance,
            }

        if step_type == "move":
            dx = max(
                -1500,
                min(int(raw_step.get("dx", 0)), 1500),
            )
            dy = max(
                -1500,
                min(int(raw_step.get("dy", 0)), 1500),
            )
            duration = max(
                80,
                min(
                    int(raw_step.get("duration", 500)),
                    15000,
                ),
            )

            if dx == 0 and dy == 0:
                return None

            return {
                "type": "move",
                "dx": dx,
                "dy": dy,
                "duration": duration,
            }

        if step_type == "return":
            duration = max(
                80,
                min(
                    int(raw_step.get("duration", 500)),
                    15000,
                ),
            )

            return {
                "type": "return",
                "duration": duration,
            }

        if step_type == "jump":
            height = max(
                5,
                min(
                    int(raw_step.get("height", 35)),
                    500,
                ),
            )
            duration = max(
                120,
                min(
                    int(raw_step.get("duration", 550)),
                    10000,
                ),
            )
            repeats = max(
                1,
                min(
                    int(raw_step.get("repeats", 1)),
                    10,
                ),
            )

            return {
                "type": "jump",
                "height": height,
                "duration": duration,
                "repeats": repeats,
            }

        if step_type == "shake":
            distance = max(
                1,
                min(
                    int(raw_step.get("distance", 7)),
                    100,
                ),
            )
            duration = max(
                120,
                min(
                    int(raw_step.get("duration", 500)),
                    10000,
                ),
            )
            cycles = max(
                1,
                min(
                    int(raw_step.get("cycles", 7)),
                    30,
                ),
            )

            return {
                "type": "shake",
                "distance": distance,
                "duration": duration,
                "cycles": cycles,
            }

        return None

    def load_actions(self):
        """读取动作；兼容旧格式与步骤序列格式。"""

        actions = []

        if ACTIONS_FILE.exists():
            try:
                data = json.loads(
                    ACTIONS_FILE.read_text(
                        encoding="utf-8"
                    )
                )

                if not isinstance(data, dict):
                    raise ValueError(
                        "动作文件必须是对象。"
                    )

                raw_actions = data.get("actions", [])

                if not isinstance(raw_actions, list):
                    raise ValueError(
                        "actions 必须是列表。"
                    )

                for raw_action in raw_actions[:100]:
                    if not isinstance(raw_action, dict):
                        continue

                    name = str(
                        raw_action.get("name", "")
                    ).strip()[:40]

                    if not name:
                        continue

                    valid_triggers = {
                        "manual",
                        "random",
                        "single_click",
                        "double_click",
                        "triple_click",
                        "five_click",
                        "drag_release",
                        "wake",
                        "edge",
                        "startup",
                    }

                    raw_triggers = raw_action.get(
                        "triggers"
                    )

                    if isinstance(raw_triggers, list):
                        trigger_values = [
                            str(item).strip().lower()
                            for item in raw_triggers[:12]
                            if isinstance(item, str)
                        ]
                    else:
                        trigger_values = [
                            str(
                                raw_action.get(
                                    "trigger",
                                    "manual",
                                )
                            ).strip().lower()
                        ]

                    triggers = []

                    for trigger_value in trigger_values:
                        expanded = (
                            ["manual", "random"]
                            if trigger_value == "both"
                            else [trigger_value]
                        )

                        for trigger_name in expanded:
                            if (
                                trigger_name in valid_triggers
                                and trigger_name not in triggers
                            ):
                                triggers.append(trigger_name)

                    if not triggers:
                        triggers = ["manual"]

                    try:
                        weight = int(
                            raw_action.get("weight", 1)
                        )
                    except (TypeError, ValueError):
                        weight = 1

                    weight = max(1, min(weight, 1000))

                    try:
                        chance = float(
                            raw_action.get("chance", 100)
                        )
                    except (TypeError, ValueError):
                        chance = 100.0

                    chance = max(
                        0.0,
                        min(chance, 100.0),
                    )

                    try:
                        cooldown = float(
                            raw_action.get("cooldown", 0)
                        )
                    except (TypeError, ValueError):
                        cooldown = 0.0

                    cooldown = max(
                        0.0,
                        min(cooldown, 86400.0),
                    )

                    steps = []
                    raw_steps = raw_action.get("steps")

                    if isinstance(raw_steps, list):
                        for raw_step in raw_steps[:60]:
                            step = self.sanitize_action_step(
                                raw_step
                            )

                            if step is not None:
                                steps.append(step)

                    # 兼容原来的 frames / speech / loops 格式。
                    if not steps:
                        speech = str(
                            raw_action.get(
                                "speech",
                                "",
                            )
                        ).strip()[:120]

                        if speech:
                            steps.append(
                                {
                                    "type": "say",
                                    "text": speech,
                                    "texts": [],
                                }
                            )

                        legacy_frames = self.sanitize_action_step(
                            {
                                "type": "frames",
                                "frames": raw_action.get(
                                    "frames",
                                    [],
                                ),
                                "frame_interval": raw_action.get(
                                    "frame_interval",
                                    220,
                                ),
                                "loops": raw_action.get(
                                    "loops",
                                    1,
                                ),
                                "playback": "loop",
                            }
                        )

                        if legacy_frames is not None:
                            steps.append(legacy_frames)

                    if not steps:
                        continue

                    actions.append(
                        {
                            "name": name,
                            "triggers": triggers,
                            "weight": weight,
                            "chance": chance,
                            "cooldown": cooldown,
                            "steps": steps,
                        }
                    )

            except (
                OSError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ):
                actions = []

        self.actions = actions

        if hasattr(self, "tray_actions_menu"):
            self.refresh_tray_actions_menu()

    def image_path(self, filename):
        return IMAGE_DIR / filename

    def action_image_path(self, filename):
        """线上下载图片优先，其次使用程序自带图片。"""

        online_path = ONLINE_IMAGE_DIR / filename

        if online_path.exists():
            return online_path

        return self.image_path(filename)

    def load_action_image(self, filename):
        path = self.action_image_path(filename)
        pixmap = QPixmap(str(path))

        if pixmap.isNull():
            raise FileNotFoundError(
                f"无法读取动作图片：{path}"
            )

        size = self.settings["pet_size"]

        return pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def load_image(self, filename):
        # 核心图片也允许被 online_images 中的新版覆盖。
        # 本地没有线上版本时自动回退到打包自带图片。
        path = self.action_image_path(filename)
        pixmap = QPixmap(str(path))

        if pixmap.isNull():
            raise FileNotFoundError(
                f"无法读取图片：{path}"
            )

        size = self.settings["pet_size"]

        return pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def load_all_images(self):
        self.normal = self.load_image("normal.PNG")
        self.blink = self.load_image("blink.PNG")
        self.happy = self.load_image("happy.PNG")
        self.startup_hello_frames = [
            self.load_image("hello1.PNG"),
            self.load_image("hello2.PNG"),
        ]

        self.walk_left = [
            self.load_image("walkleft1.PNG"),
            self.load_image("walkleft2.PNG"),
        ]
        self.walk_right = [
            self.load_image("walkright1.PNG"),
            self.load_image("walkright2.PNG"),
        ]
        self.sleep_frames = [
            self.load_image("sleep1.PNG"),
            self.load_image("sleep2.PNG"),
        ]
        self.drag_frames = [
            self.load_image("drag1.PNG"),
            self.load_image("drag2.PNG"),
        ]

    def apply_timer_settings(self):
        self.sleep_animation_timer.setInterval(
            self.settings["sleep_frame_interval"]
        )

    def reload_settings_and_messages(self):
        """重新载入，并保持果子的位置不漂移。"""

        debug_log(
            f"reload start state={self.state} "
            f"dragging={self.is_dragging}"
        )

        old_x = self.x()
        old_y = self.y()
        old_width = self.width()
        old_height = self.height()

        was_sleeping = self.state == "sleeping"

        try:
            self.load_settings()
            self.load_messages()
            self.load_actions()
            self.refresh_action_sound_cache()
            self.load_greetings()
            self.load_all_images()
            self.apply_timer_settings()
        except (OSError, ValueError, FileNotFoundError):
            self.say("重新载入失败，请检查文件。")
            return

        self.setFixedSize(self.normal.size())

        new_x = old_x + (
            old_width - self.width()
        ) // 2
        new_y = old_y + (
            old_height - self.height()
        ) // 2

        self.move_to_safe_position(
            new_x,
            new_y,
        )

        if was_sleeping:
            self.sleep_frame_index = 0
            self.setPixmap(
                self.sleep_frames[
                    self.sleep_frame_index
                ]
            )
            self.position_sleep_zzz()
            self.sleep_zzz.start_animation()
            self.schedule_auto_wake()
        else:
            self.state = "normal"
            self.setPixmap(self.normal)

        self.schedule_blink()
        self.schedule_auto_speech()
        self.schedule_random_action()
        self.schedule_sleep()

        if not self.walking_paused:
            self.schedule_walk()

        debug_log(
            f"reload end state={self.state} "
            f"dragging={self.is_dragging}"
        )
        self.say("设置、台词和动作已重新载入。")

    def download_text(self, url):
        """从指定网址下载 UTF-8 文本，并绕过旧缓存。"""

        separator = "&" if "?" in url else "?"
        fresh_url = (
            f"{url}{separator}guozi_time={time.time_ns()}"
        )

        request = urllib.request.Request(
            fresh_url,
            headers={
                "User-Agent": "Guozi-Desktop-Pet",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=7,
        ) as response:
            return response.read().decode("utf-8-sig")

    def download_text_from_sources(
        self,
        urls,
        preferred_source=0,
    ):
        """依次尝试主线路和备用线路。"""

        ordered_sources = list(enumerate(urls))

        if preferred_source == 1:
            ordered_sources.reverse()

        last_error = None

        for source_index, url in ordered_sources:
            try:
                return (
                    self.download_text(url),
                    source_index,
                )
            except (
                OSError,
                UnicodeDecodeError,
                urllib.error.URLError,
                urllib.error.HTTPError,
            ) as error:
                last_error = error

        if last_error is not None:
            raise last_error

        raise urllib.error.URLError(
            "没有可用的更新线路。"
        )

    def download_bytes(self, url):
        """下载动作图片，并限制单张图片的最大体积。"""

        separator = "&" if "?" in url else "?"
        fresh_url = (
            f"{url}{separator}guozi_time={time.time_ns()}"
        )

        request = urllib.request.Request(
            fresh_url,
            headers={
                "User-Agent": "Guozi-Desktop-Pet",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            content_length = response.headers.get(
                "Content-Length"
            )

            if (
                content_length is not None
                and int(content_length)
                > MAX_ACTION_IMAGE_BYTES
            ):
                raise ValueError("动作图片文件过大。")

            data = response.read(
                MAX_ACTION_IMAGE_BYTES + 1
            )

        if len(data) > MAX_ACTION_IMAGE_BYTES:
            raise ValueError("动作图片文件过大。")

        if not data:
            raise ValueError("动作图片文件为空。")

        return data

    def download_bytes_from_sources(
        self,
        urls,
        preferred_source=0,
    ):
        """依次尝试不同线路下载图片。"""

        ordered_sources = list(enumerate(urls))

        if preferred_source == 1:
            ordered_sources.reverse()

        last_error = None

        for source_index, url in ordered_sources:
            try:
                return (
                    self.download_bytes(url),
                    source_index,
                )
            except (
                OSError,
                ValueError,
                urllib.error.URLError,
                urllib.error.HTTPError,
            ) as error:
                last_error = error

        if last_error is not None:
            raise last_error

        raise urllib.error.URLError(
            "没有可用的图片线路。"
        )

    def trusted_file_candidates(self, supplied_url):
        """把可信更新网址转换成主、备用两条线路。"""

        if supplied_url.startswith(RAW_REPO_BASE_URL):
            relative_path = supplied_url[
                len(RAW_REPO_BASE_URL):
            ]
        elif supplied_url.startswith(CDN_REPO_BASE_URL):
            relative_path = supplied_url[
                len(CDN_REPO_BASE_URL):
            ]
        else:
            raise ValueError("更新网址不安全。")

        if relative_path not in (
            "messages.txt",
            "settings.json",
            "actions.json",
            "greetings.json",
        ):
            raise ValueError("更新文件路径不安全。")

        return (
            RAW_REPO_BASE_URL + relative_path,
            CDN_REPO_BASE_URL + relative_path,
        )

    def core_image_filenames_from_version(
        self,
        version_data,
    ):
        """读取 version.json 指定的核心图片列表。"""

        raw_filenames = version_data.get(
            "core_images",
            [],
        )

        if not isinstance(raw_filenames, list):
            raise ValueError("核心图片列表格式错误。")

        filenames = []

        for raw_name in raw_filenames[:100]:
            if not isinstance(raw_name, str):
                raise ValueError("核心图片文件名无效。")

            filename = raw_name.strip()

            if (
                not filename
                or Path(filename).name != filename
                or Path(filename).suffix.lower()
                not in (
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                )
            ):
                raise ValueError(
                    "核心图片文件名不安全。"
                )

            if filename not in filenames:
                filenames.append(filename)

        return filenames

    def merge_unique_filenames(self, *groups):
        """合并多个文件名列表，同时保持顺序并去重。"""

        merged = []

        for group in groups:
            for filename in group:
                if filename not in merged:
                    merged.append(filename)

        return merged

    def action_filenames_from_text(self, actions_text):
        """提取旧动作和步骤序列引用的全部图片。"""

        actions_data = json.loads(actions_text)

        if (
            not isinstance(actions_data, dict)
            or not isinstance(
                actions_data.get("actions", []),
                list,
            )
        ):
            raise ValueError("在线动作文件格式错误。")

        filenames = []

        def add_frames(raw_frames):
            if not isinstance(raw_frames, list):
                return

            for filename in raw_frames[:40]:
                if not isinstance(filename, str):
                    continue

                filename = filename.strip()

                if (
                    not filename
                    or Path(filename).name != filename
                    or Path(filename).suffix.lower()
                    not in (
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                    )
                ):
                    raise ValueError(
                        "动作图片文件名不安全。"
                    )

                if filename not in filenames:
                    filenames.append(filename)

        for action in actions_data.get("actions", [])[:100]:
            if not isinstance(action, dict):
                continue

            add_frames(action.get("frames", []))

            raw_steps = action.get("steps", [])

            if not isinstance(raw_steps, list):
                continue

            for step in raw_steps[:60]:
                if (
                    isinstance(step, dict)
                    and str(
                        step.get("type", "")
                    ).strip().lower()
                    == "frames"
                ):
                    add_frames(step.get("frames", []))

        if len(filenames) > 200:
            raise ValueError("动作图片数量过多。")

        return filenames

    def action_sound_filenames_from_text(
        self,
        actions_text,
    ):
        """提取动作步骤引用的全部 WAV 音效。"""

        actions_data = json.loads(actions_text)

        if (
            not isinstance(actions_data, dict)
            or not isinstance(
                actions_data.get("actions", []),
                list,
            )
        ):
            raise ValueError("在线动作文件格式错误。")

        filenames = []

        for action in actions_data.get("actions", [])[:100]:
            if not isinstance(action, dict):
                continue

            raw_steps = action.get("steps", [])

            if not isinstance(raw_steps, list):
                continue

            for step in raw_steps[:60]:
                if not isinstance(step, dict):
                    continue

                if (
                    str(step.get("type", ""))
                    .strip()
                    .lower()
                    != "sound"
                ):
                    continue

                filename = step.get("file")

                if (
                    not isinstance(filename, str)
                    or not filename.strip()
                    or Path(filename.strip()).name
                    != filename.strip()
                    or Path(filename.strip()).suffix.lower()
                    != ".wav"
                ):
                    raise ValueError(
                        "动作音效文件名不安全。"
                    )

                filename = filename.strip()

                if filename not in filenames:
                    filenames.append(filename)

        if len(filenames) > 100:
            raise ValueError("动作音效数量过多。")

        return filenames

    def download_named_images(
        self,
        filenames,
        preferred_source=0,
    ):
        """下载指定的线上图片，并在替换前验证图片内容。"""

        ONLINE_IMAGE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        downloaded_count = 0

        for filename in filenames:
            safe_name = self.sanitize_action_filename(
                filename
            )

            if safe_name is None:
                raise ValueError("图片文件名不安全。")

            encoded_name = urllib.parse.quote(
                safe_name,
                safe="",
            )
            image_urls = (
                RAW_REPO_BASE_URL
                + "images/"
                + encoded_name,
                CDN_REPO_BASE_URL
                + "images/"
                + encoded_name,
            )

            image_data, source_index = (
                self.download_bytes_from_sources(
                    image_urls,
                    preferred_source,
                )
            )

            if source_index == 1:
                self.update_used_backup = True

            temp_path = (
                ONLINE_IMAGE_DIR
                / f"{safe_name}.new"
            )
            final_path = (
                ONLINE_IMAGE_DIR / safe_name
            )

            temp_path.write_bytes(image_data)

            test_pixmap = QPixmap()
            test_pixmap.loadFromData(image_data)

            if test_pixmap.isNull():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

                raise ValueError(
                    f"图片无法读取：{safe_name}"
                )

            temp_path.replace(final_path)
            downloaded_count += 1

        return downloaded_count

    def download_action_images(
        self,
        actions_text,
        preferred_source=0,
    ):
        """兼容旧调用：下载 actions.json 引用的所有图片。"""

        filenames = self.action_filenames_from_text(
            actions_text
        )

        return self.download_named_images(
            filenames,
            preferred_source,
        )

    def download_action_sounds(
        self,
        actions_text,
        preferred_source=0,
    ):
        """同步 actions.json 引用的 WAV；未变化的文件不碰播放器。"""

        filenames = (
            self.action_sound_filenames_from_text(
                actions_text
            )
        )

        if not filenames:
            return 0

        ONLINE_SOUND_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        changed_count = 0

        for filename in filenames:
            encoded_name = urllib.parse.quote(
                filename,
                safe="",
            )
            sound_urls = (
                RAW_REPO_BASE_URL
                + "sounds/"
                + encoded_name,
                CDN_REPO_BASE_URL
                + "sounds/"
                + encoded_name,
            )

            sound_data, source_index = (
                self.download_bytes_from_sources(
                    sound_urls,
                    preferred_source,
                )
            )

            if len(sound_data) > MAX_ACTION_SOUND_BYTES:
                raise ValueError("动作音效文件过大。")

            if source_index == 1:
                self.update_used_backup = True

            temp_path = (
                ONLINE_SOUND_DIR
                / f"{filename}.new"
            )
            final_path = (
                ONLINE_SOUND_DIR / filename
            )

            temp_path.write_bytes(sound_data)

            try:
                with wave.open(
                    str(temp_path),
                    "rb",
                ) as wav_file:
                    if (
                        wav_file.getnchannels() < 1
                        or wav_file.getframerate() < 1
                        or wav_file.getnframes() < 1
                    ):
                        raise ValueError(
                            "动作音效内容为空。"
                        )
            except (
                wave.Error,
                EOFError,
                OSError,
            ) as error:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

                raise ValueError(
                    f"动作音效无法读取：{filename}"
                ) from error

            # GitHub 上的 WAV 与本地完全一样时：
            # 不替换文件，也不重建 QSoundEffect。
            unchanged = False

            if final_path.exists():
                try:
                    unchanged = (
                        final_path.read_bytes()
                        == sound_data
                    )
                except OSError:
                    unchanged = False

            if unchanged:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
                continue

            # 只有真正换音效时才释放该 WAV 的播放器。
            self.discard_cached_sound_effect(
                filename
            )

            temp_path.replace(final_path)
            changed_count += 1

        return changed_count

    def cleanup_online_asset_directory(
        self,
        directory,
        keep_names,
        allowed_suffixes,
    ):
        """删除线上缓存目录里已经不再引用的受管文件。"""

        if not directory.exists():
            return 0

        keep_names = set(keep_names)
        deleted_count = 0

        for path in directory.iterdir():
            if not path.is_file():
                continue

            # 临时 .new 文件由各下载流程自行管理。
            if path.name.endswith(".new"):
                continue

            if path.suffix.lower() not in allowed_suffixes:
                continue

            if path.name in keep_names:
                continue

            try:
                path.unlink()
                deleted_count += 1
            except OSError:
                # 清理失败不能破坏已经成功的更新。
                pass

        return deleted_count

    def cleanup_online_assets(
        self,
        image_filenames,
        sound_filenames,
    ):
        """清理最新版已经不再使用的线上图片与音效。"""

        deleted_images = (
            self.cleanup_online_asset_directory(
                ONLINE_IMAGE_DIR,
                image_filenames,
                {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                },
            )
        )
        deleted_sounds = (
            self.cleanup_online_asset_directory(
                ONLINE_SOUND_DIR,
                sound_filenames,
                {".wav"},
            )
        )

        return deleted_images, deleted_sounds

    def write_update_files(
        self,
        messages_text,
        settings_text,
        actions_text,
        greetings_text,
    ):
        """验证并安全替换在线内容文件。"""

        message_lines = [
            line.strip()
            for line in messages_text.splitlines()
            if line.strip()
        ]

        if not message_lines:
            raise ValueError("在线台词文件为空。")

        settings_data = json.loads(settings_text)

        if not isinstance(settings_data, dict):
            raise ValueError("在线设置文件格式错误。")

        # 移动模式属于每台电脑自己的偏好。
        # 在线更新设置时，不覆盖用户当前选择。
        settings_data["movement_mode"] = (
            self.settings.get(
                "movement_mode",
                "horizontal",
            )
        )

        actions_data = json.loads(actions_text)

        if (
            not isinstance(actions_data, dict)
            or not isinstance(
                actions_data.get("actions", []),
                list,
            )
        ):
            raise ValueError("在线动作文件格式错误。")

        greetings_data = json.loads(greetings_text)

        if (
            not isinstance(greetings_data, dict)
            or not isinstance(
                greetings_data.get("greetings", {}),
                dict,
            )
        ):
            raise ValueError("在线问候语文件格式错误。")

        raw_greetings = greetings_data["greetings"]

        for key in DEFAULT_GREETINGS:
            items = raw_greetings.get(key)

            if (
                not isinstance(items, list)
                or not any(
                    isinstance(item, str)
                    and item.strip()
                    for item in items
                )
            ):
                raise ValueError(
                    f"问候语分类无效：{key}"
                )

        messages_temp = APP_DIR / "messages.txt.new"
        settings_temp = APP_DIR / "settings.json.new"
        actions_temp = APP_DIR / "actions.json.new"
        greetings_temp = APP_DIR / "greetings.json.new"

        messages_temp.write_text(
            "\n".join(message_lines) + "\n",
            encoding="utf-8",
        )
        settings_temp.write_text(
            json.dumps(
                settings_data,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        actions_temp.write_text(
            json.dumps(
                actions_data,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        greetings_temp.write_text(
            json.dumps(
                greetings_data,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        messages_temp.replace(MESSAGES_FILE)
        settings_temp.replace(SETTINGS_FILE)
        actions_temp.replace(ACTIONS_FILE)
        greetings_temp.replace(GREETINGS_FILE)

    def fetch_online_update_package(self):
        """后台线程下载在线内容；不触碰 Qt 界面或现有音效播放器。"""

        try:
            used_backup = False

            version_text, preferred_source = (
                self.download_text_from_sources(
                    UPDATE_INFO_URLS
                )
            )

            if preferred_source == 1:
                used_backup = True

            version_data = json.loads(version_text)

            if not isinstance(version_data, dict):
                raise ValueError("版本文件格式错误。")

            version = str(
                version_data.get("version", "未知")
            )

            messages_urls = self.trusted_file_candidates(
                version_data["messages_url"]
            )
            settings_urls = self.trusted_file_candidates(
                version_data["settings_url"]
            )
            actions_urls = self.trusted_file_candidates(
                version_data["actions_url"]
            )
            greetings_urls = self.trusted_file_candidates(
                version_data["greetings_url"]
            )

            messages_text, messages_source = (
                self.download_text_from_sources(
                    messages_urls,
                    preferred_source,
                )
            )
            settings_text, settings_source = (
                self.download_text_from_sources(
                    settings_urls,
                    preferred_source,
                )
            )
            actions_text, actions_source = (
                self.download_text_from_sources(
                    actions_urls,
                    preferred_source,
                )
            )
            greetings_text, greetings_source = (
                self.download_text_from_sources(
                    greetings_urls,
                    preferred_source,
                )
            )

            if 1 in (
                messages_source,
                settings_source,
                actions_source,
                greetings_source,
            ):
                used_backup = True

            core_image_filenames = (
                self.core_image_filenames_from_version(
                    version_data
                )
            )
            action_image_filenames = (
                self.action_filenames_from_text(
                    actions_text
                )
            )
            image_filenames = (
                self.merge_unique_filenames(
                    core_image_filenames,
                    action_image_filenames,
                )
            )
            sound_filenames = (
                self.action_sound_filenames_from_text(
                    actions_text
                )
            )

            image_data = {}

            for filename in image_filenames:
                encoded_name = urllib.parse.quote(
                    filename,
                    safe="",
                )
                image_urls = (
                    RAW_REPO_BASE_URL
                    + "images/"
                    + encoded_name,
                    CDN_REPO_BASE_URL
                    + "images/"
                    + encoded_name,
                )

                data, source_index = (
                    self.download_bytes_from_sources(
                        image_urls,
                        preferred_source,
                    )
                )

                if source_index == 1:
                    used_backup = True

                image_data[filename] = data

            sound_data = {}

            for filename in sound_filenames:
                encoded_name = urllib.parse.quote(
                    filename,
                    safe="",
                )
                sound_urls = (
                    RAW_REPO_BASE_URL
                    + "sounds/"
                    + encoded_name,
                    CDN_REPO_BASE_URL
                    + "sounds/"
                    + encoded_name,
                )

                data, source_index = (
                    self.download_bytes_from_sources(
                        sound_urls,
                        preferred_source,
                    )
                )

                if len(data) > MAX_ACTION_SOUND_BYTES:
                    raise ValueError("动作音效文件过大。")

                if source_index == 1:
                    used_backup = True

                sound_data[filename] = data

            self.online_update_ready.emit(
                {
                    "version": version,
                    "messages_text": messages_text,
                    "settings_text": settings_text,
                    "actions_text": actions_text,
                    "greetings_text": greetings_text,
                    "image_filenames": image_filenames,
                    "sound_filenames": sound_filenames,
                    "image_data": image_data,
                    "sound_data": sound_data,
                    "used_backup": used_backup,
                }
            )

        except Exception as exc:
            self.online_update_failed.emit(
                repr(exc)
            )

    def apply_downloaded_images(self, image_data):
        """主线程验证并替换已经下载好的图片。"""

        ONLINE_IMAGE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        synced_count = 0

        for filename, data in image_data.items():
            safe_name = self.sanitize_action_filename(
                filename
            )

            if safe_name is None:
                raise ValueError("图片文件名不安全。")

            test_pixmap = QPixmap()
            test_pixmap.loadFromData(data)

            if test_pixmap.isNull():
                raise ValueError(
                    f"图片无法读取：{safe_name}"
                )

            temp_path = (
                ONLINE_IMAGE_DIR
                / f"{safe_name}.new"
            )
            final_path = (
                ONLINE_IMAGE_DIR / safe_name
            )

            temp_path.write_bytes(data)
            temp_path.replace(final_path)
            synced_count += 1

        return synced_count

    def apply_downloaded_sounds(self, sound_data):
        """沿用现有稳定音效策略：相同 WAV 完全不碰播放器。"""

        ONLINE_SOUND_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        changed_count = 0

        for filename, data in sound_data.items():
            safe_name = (
                self.sanitize_action_sound_filename(
                    filename
                )
            )

            if safe_name is None:
                raise ValueError(
                    "动作音效文件名不安全。"
                )

            temp_path = (
                ONLINE_SOUND_DIR
                / f"{safe_name}.new"
            )
            final_path = (
                ONLINE_SOUND_DIR / safe_name
            )

            temp_path.write_bytes(data)

            try:
                with wave.open(
                    str(temp_path),
                    "rb",
                ) as wav_file:
                    if (
                        wav_file.getnchannels() < 1
                        or wav_file.getframerate() < 1
                        or wav_file.getnframes() < 1
                    ):
                        raise ValueError(
                            "动作音效内容为空。"
                        )
            except (
                wave.Error,
                EOFError,
                OSError,
            ) as error:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

                raise ValueError(
                    f"动作音效无法读取：{safe_name}"
                ) from error

            unchanged = False

            if final_path.exists():
                try:
                    unchanged = (
                        final_path.read_bytes()
                        == data
                    )
                except OSError:
                    unchanged = False

            if unchanged:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
                continue

            self.discard_cached_sound_effect(
                safe_name
            )
            temp_path.replace(final_path)
            changed_count += 1

        return changed_count

    def receive_online_update_package(self, package):
        """网络线程完成后先缓存；等果子处于安全状态再应用。"""

        debug_log(
            f"update downloaded state={self.state} "
            f"dragging={self.is_dragging}"
        )

        self.pending_online_update_package = package
        self.try_apply_pending_online_update()

        if self.pending_online_update_package is not None:
            self.pending_update_timer.start()

    def update_apply_is_safe(self):
        """只在完全空闲的 normal 状态重新载入资源。"""

        return (
            not self.is_dragging
            and self.state == "normal"
            and not self.poke_input_locked
        )

    def try_apply_pending_online_update(self):
        """等待当前动画/拖动结束后再应用已下载的更新。"""

        package = self.pending_online_update_package

        if package is None:
            self.pending_update_timer.stop()
            return

        if not self.update_apply_is_safe():
            return

        self.pending_update_timer.stop()
        self.pending_online_update_package = None

        try:
            debug_log(
                "update apply start "
                f"state={self.state} dragging={self.is_dragging}"
            )

            self.update_used_backup = bool(
                package.get("used_backup", False)
            )

            image_count = self.apply_downloaded_images(
                package["image_data"]
            )
            sound_count = self.apply_downloaded_sounds(
                package["sound_data"]
            )

            self.write_update_files(
                package["messages_text"],
                package["settings_text"],
                package["actions_text"],
                package["greetings_text"],
            )

            deleted_images, deleted_sounds = (
                self.cleanup_online_assets(
                    package["image_filenames"],
                    package["sound_filenames"],
                )
            )

            self.reload_settings_and_messages()

            route_text = (
                "，已使用备用线路"
                if self.update_used_backup
                else ""
            )

            greeting_count = sum(
                len(items)
                for items in self.greetings.values()
            )

            cleanup_text = ""

            if deleted_images or deleted_sounds:
                cleanup_text = (
                    f"，已清理 {deleted_images} 张旧图片、"
                    f"{deleted_sounds} 个旧音效"
                )

            self.say(
                "在线更新完成！"
                f"版本 {package['version']}，"
                f"共 {len(self.messages)} 句台词，"
                f"{greeting_count} 句问候，"
                f"{len(self.actions)} 个动作，"
                f"已同步 {image_count} 张图片，"
                f"更新了 {sound_count} 个音效"
                f"{cleanup_text}"
                f"{route_text}。"
            )

            debug_log(
                "update apply finished "
                f"state={self.state}"
            )

        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            debug_log(
                f"update apply failed {exc!r}"
            )
            print(
                "在线更新应用失败：",
                repr(exc),
            )

            try:
                self.refresh_action_sound_cache()
            except (OSError, RuntimeError):
                pass

            self.say("检查更新失败，请稍后再试。")

        finally:
            self.update_in_progress = False

    def finish_online_update_failure(self, error_text):
        """后台下载失败；保留本地最后一份可用内容。"""

        debug_log(
            f"update download failed {error_text}"
        )
        print(
            "在线更新失败：",
            error_text,
        )

        self.pending_online_update_package = None
        self.pending_update_timer.stop()
        self.update_in_progress = False
        self.say("检查更新失败，请稍后再试。")

    def check_online_updates(self):
        """后台检查在线更新；网络等待不再卡住桌宠。"""

        if self.update_in_progress:
            self.say("正在检查在线更新……")
            return

        self.update_in_progress = True

        debug_log(
            f"update start state={self.state} "
            f"dragging={self.is_dragging}"
        )

        self.say("正在检查在线更新……")

        worker = threading.Thread(
            target=self.fetch_online_update_package,
            daemon=True,
        )
        worker.start()


    def open_file(self, path):
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(path))
                )
        except OSError:
            pass

    def open_messages_file(self):
        self.open_file(MESSAGES_FILE)

    def open_settings_file(self):
        self.open_file(SETTINGS_FILE)

    # ---------- 位置 ----------

    def save_position(self):
        try:
            POSITION_FILE.write_text(
                json.dumps(
                    {
                        "x": self.x(),
                        "y": self.y(),
                    }
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def restore_position(self):
        screen = QApplication.primaryScreen()

        if screen is None:
            return

        area = screen.availableGeometry()

        x = (
            area.right()
            - self.width()
            - 30
        )
        y = (
            area.bottom()
            - self.height()
            - 30
        )

        if POSITION_FILE.exists():
            try:
                data = json.loads(
                    POSITION_FILE.read_text(
                        encoding="utf-8"
                    )
                )
                x = int(data["x"])
                y = int(data["y"])
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
            ):
                pass

        self.move_to_safe_position(x, y)

    def screen_area_for_point(self, point):
        screen = QApplication.screenAt(point)

        if screen is None:
            screen = QApplication.primaryScreen()

        return (
            screen.availableGeometry()
            if screen is not None
            else None
        )

    def safe_coordinates(self, x, y):
        point = QPoint(
            x + self.width() // 2,
            y + self.height() // 2,
        )
        area = self.screen_area_for_point(point)

        if area is None:
            return x, y

        safe_x = max(
            area.left(),
            min(
                x,
                area.right() - self.width() + 1,
            ),
        )
        safe_y = max(
            area.top(),
            min(
                y,
                area.bottom() - self.height() + 1,
            ),
        )

        return safe_x, safe_y

    def move_to_safe_position(self, x, y):
        safe_x, safe_y = self.safe_coordinates(
            x,
            y,
        )
        self.move(safe_x, safe_y)

    # ---------- 置顶 ----------

    def keep_all_windows_on_top(self):
        """让果子、气泡和 Zzz 保持同一套置顶状态。"""

        keep_widget_topmost(self)

        if self.speech_bubble.isVisible():
            keep_widget_topmost(self.speech_bubble)

        if self.sleep_zzz.isVisible():
            keep_widget_topmost(self.sleep_zzz)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(
            0,
            self.keep_all_windows_on_top,
        )

    # ---------- 时间问候 ----------

    def schedule_startup_greeting(self):
        """在线更新提示结束后，只安排一次时间问候。"""

        if not self.startup_greeting_pending:
            return

        self.startup_greeting_pending = False

        delay = max(
            1800,
            self.settings["speech_duration"] * 1000
            + 700,
        )
        QTimer.singleShot(
            delay,
            self.say_time_greeting,
        )

    def schedule_startup_action(self):
        """启动后只安排一次线上启动动作。"""

        if not self.startup_action_pending:
            return

        self.startup_action_pending = False

        QTimer.singleShot(
            max(
                8000,
                self.settings[
                    "speech_duration"
                ] * 1000 + 4500,
            ),
            self.try_startup_action,
        )

    def try_startup_action(self):
        """空闲时播放启动动作。"""

        if not self.get_actions_for_trigger(
            "startup"
        ):
            return

        if (
            self.isVisible()
            and self.state in (
                "normal",
                "walking",
            )
            and not self.is_dragging
        ):
            self.play_trigger_action("startup")
            return

        QTimer.singleShot(
            2500,
            self.try_startup_action,
        )

    def get_time_greeting_text(self):
        """根据电脑当前时间选出一句问候语。"""

        hour = datetime.now().hour

        if 5 <= hour < 10:
            category = "morning"
        elif 10 <= hour < 12:
            category = "late_morning"
        elif 12 <= hour < 14:
            category = "noon"
        elif 14 <= hour < 18:
            category = "afternoon"
        elif 18 <= hour < 23:
            category = "evening"
        else:
            category = "late_night"

        greetings = self.greetings.get(
            category,
            DEFAULT_GREETINGS[category],
        )

        return random.choice(greetings)

    def say_time_greeting(self):
        """根据电脑当前时间说一句问候。"""

        if not self.isVisible():
            return

        if (
            self.state not in (
                "normal",
                "walking",
            )
            or self.is_dragging
        ):
            QTimer.singleShot(
                2500,
                self.say_time_greeting,
            )
            return

        self.say(
            self.get_time_greeting_text()
        )

    def advance_startup_hello_frame(self):
        """循环播放 hello1 / hello2 开机动画。"""

        if self.state != "startup_hello":
            self.startup_hello_timer.stop()
            return

        self.startup_hello_frame_index = (
            self.startup_hello_frame_index + 1
        ) % len(self.startup_hello_frames)

        self.setPixmap(
            self.startup_hello_frames[
                self.startup_hello_frame_index
            ]
        )

    def finish_startup_hello_and_update(self):
        """结束开机动画，然后开始在线更新。"""

        self.startup_hello_timer.stop()

        if self.state == "startup_hello":
            self.state = "normal"
            self.setPixmap(self.normal)
            self.schedule_blink()

            if not self.walking_paused:
                self.schedule_walk()

        self.check_online_updates()

    def play_startup_happy_greeting(self):
        """hello 两帧动画与时间问候同时出现，然后再检查更新。"""

        if not self.isVisible():
            return

        self.stop_walk_timers()
        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.sleep_wait_timer.stop()
        self.happy_timer.stop()
        self.startup_hello_timer.stop()

        if self.state == "sleeping":
            self.sleep_duration_timer.stop()
            self.sleep_animation_timer.stop()
            self.sleep_zzz.stop_animation()

        self.state = "startup_hello"
        self.startup_hello_frame_index = 0
        self.setPixmap(
            self.startup_hello_frames[0]
        )
        self.startup_hello_timer.start()

        # hello 动画和当前时间段问候同步开始。
        self.say(
            self.get_time_greeting_text()
        )

        # 等问候展示完再更新，避免更新文字立刻覆盖开机语。
        update_delay = max(
            2100,
            int(
                self.settings["speech_duration"]
                * 1000
            ) + 200,
        )

        QTimer.singleShot(
            update_delay,
            self.finish_startup_hello_and_update,
        )


    # ---------- 气泡 ----------

    def say(self, text):
        if not self.isVisible():
            return

        self.speech_bubble.set_text(text)
        self.position_speech_bubble()
        self.speech_bubble.show()
        self.keep_all_windows_on_top()

        self.speech_hide_timer.start(
            self.settings["speech_duration"]
            * 1000
        )

    def say_random_message(self):
        if self.state == "sleeping":
            return

        self.say(random.choice(self.messages))

    def position_speech_bubble(self):
        screen = QApplication.screenAt(
            self.frameGeometry().center()
        )

        if screen is None:
            screen = QApplication.primaryScreen()

        if screen is None:
            return

        area = screen.availableGeometry()

        x = self.x() + (
            self.width()
            - self.speech_bubble.width()
        ) // 2
        y = (
            self.y()
            - self.speech_bubble.height()
            - 8
        )

        if y < area.top():
            y = self.y() + self.height() + 8

        x = max(
            area.left(),
            min(
                x,
                area.right()
                - self.speech_bubble.width()
                + 1,
            ),
        )
        y = max(
            area.top(),
            min(
                y,
                area.bottom()
                - self.speech_bubble.height()
                + 1,
            ),
        )

        self.speech_bubble.move(x, y)

    def moveEvent(self, event):
        super().moveEvent(event)

        if (
            hasattr(self, "speech_bubble")
            and self.speech_bubble.isVisible()
        ):
            self.position_speech_bubble()

        if (
            hasattr(self, "sleep_zzz")
            and self.sleep_zzz.isVisible()
        ):
            self.position_sleep_zzz()

    def position_sleep_zzz(self):
        """把 Zzz 放在果子右上方，并限制在屏幕内。"""

        screen = QApplication.screenAt(
            self.frameGeometry().center()
        )

        if screen is None:
            screen = QApplication.primaryScreen()

        if screen is None:
            return

        area = screen.availableGeometry()

        zzz_x = (
            self.x()
            + self.width()
            - self.sleep_zzz.width() // 2
            - 6
        )
        zzz_y = self.y() - 22

        if zzz_y < area.top():
            zzz_y = self.y() + 8

        zzz_x = max(
            area.left(),
            min(
                zzz_x,
                area.right()
                - self.sleep_zzz.width()
                + 1,
            ),
        )
        zzz_y = max(
            area.top(),
            min(
                zzz_y,
                area.bottom()
                - self.sleep_zzz.height()
                + 1,
            ),
        )

        self.sleep_zzz.move(zzz_x, zzz_y)

    def schedule_auto_speech(self):
        self.auto_speech_timer.stop()

        wait_seconds = random.randint(
            self.settings["speech_wait_min"],
            self.settings["speech_wait_max"],
        )
        self.auto_speech_timer.start(
            wait_seconds * 1000
        )

    def auto_speak(self):
        if (
            self.isVisible()
            and self.state != "sleeping"
            and not self.speech_bubble.isVisible()
        ):
            self.say_random_message()

        self.schedule_auto_speech()

    # ---------- 眨眼 ----------

    def schedule_blink(self):
        self.blink_wait_timer.stop()
        self.blink_wait_timer.start(
            random.randint(2000, 6000)
        )

    def close_eyes(self):
        if self.state != "normal":
            self.schedule_blink()
            return

        self.state = "blinking"
        self.setPixmap(self.blink)
        self.blink_close_timer.start(150)

    def open_eyes(self):
        if self.state != "blinking":
            return

        self.state = "normal"
        self.setPixmap(self.normal)
        self.schedule_blink()

    # ---------- 开心 ----------

    def reset_poke_cycle(self):
        """逃跑反应结束后，从第一轮三连戳重新开始。"""

        self.poke_group_count = 0
        self.poke_warning_count = 0
        self.poke_cycle_reset_pending = False

    def register_poke(self):
        """记录一次有效戳击，并返回本次应触发的动作事件。"""

        if self.poke_input_locked:
            return None

        self.poke_group_count += 1

        # 每组前两下都只是普通被戳反应。
        if self.poke_group_count < 3:
            return "single_click"

        # 第三下消费掉这一组计数。
        self.poke_group_count = 0
        self.poke_input_locked = True

        # 前两组三连戳都播放“你怎么还戳呀”。
        if self.poke_warning_count < 2:
            self.poke_warning_count += 1
            return "triple_click"

        # 第三组三连戳改成逃跑；等整段动作与 2 秒锁结束后
        # 再把整个循环重置。
        self.poke_cycle_reset_pending = True
        return "five_click"

    def handle_single_click(self):
        """执行普通戳击或当前轮次的三连戳特殊反应。"""

        trigger_name = self.register_poke()

        if trigger_name is None:
            return

        if trigger_name in (
            "triple_click",
            "five_click",
        ):
            if self.play_trigger_action(trigger_name):
                return

            # 防止线上动作文件缺失时永久锁住果子。
            self.finish_poke_milestone_action(
                trigger_name
            )
            return

        if self.play_trigger_action("single_click"):
            return

        self.become_happy()

    def finish_poke_milestone_action(
        self,
        finished_trigger,
    ):
        """特殊三连戳完整播放后，再保持 0.5 秒不可戳状态。"""

        if finished_trigger not in (
            "triple_click",
            "five_click",
        ):
            return

        # 动作本身已经播完，此时仍保持 poke_input_locked=True。
        # 再过 0.5 秒才重新允许下一组戳击。
        self.poke_cooldown_timer.start(500)

    def unlock_poke_input(self):
        """特殊动作结束 0.5 秒后重新允许戳果子。"""

        self.poke_input_locked = False

        if self.poke_cycle_reset_pending:
            self.reset_poke_cycle()

    def double_click_reaction(self):
        """双击果子的专属反应。"""

        if self.play_trigger_action(
            "double_click"
        ):
            return

        self.stop_walk_timers()
        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.sleep_wait_timer.stop()
        self.happy_timer.stop()

        if self.state == "sleeping":
            self.sleep_duration_timer.stop()
            self.sleep_animation_timer.stop()
            self.sleep_zzz.stop_animation()

        self.state = "happy"
        self.setPixmap(self.happy)
        self.say("在这里耶！")
        self.happy_timer.start(2000)

        self.schedule_auto_speech()
        self.schedule_sleep()
        self.update_menu_text()

    def become_happy(self):
        if self.state == "happy":
            return

        if self.state == "sleeping":
            self.wake_up()
            return

        self.stop_walk_timers()

        self.state = "happy"
        self.setPixmap(self.happy)
        self.happy_timer.start(2000)
        self.say_random_message()
        self.schedule_sleep()

    def back_to_normal(self):
        if self.state != "happy":
            return

        self.state = "normal"
        self.setPixmap(self.normal)

        self.schedule_blink()

        if not self.walking_paused:
            self.schedule_walk()

    # ---------- 线上动作 ----------

    def get_actions_for_trigger(self, trigger_name):
        """返回绑定到指定事件的线上动作。"""

        return [
            action
            for action in self.actions
            if trigger_name in action.get(
                "triggers",
                [],
            )
        ]

    def action_trigger_key(
        self,
        action,
        trigger_name,
    ):
        """生成动作冷却记录使用的键。"""

        return (
            str(action.get("name", "")),
            str(trigger_name),
        )

    def action_is_off_cooldown(
        self,
        action,
        trigger_name,
        now=None,
    ):
        """判断动作在该触发方式下是否已结束冷却。"""

        if now is None:
            now = time.monotonic()

        cooldown = float(
            action.get("cooldown", 0)
        )

        if cooldown <= 0:
            return True

        last_time = self.action_last_triggered.get(
            self.action_trigger_key(
                action,
                trigger_name,
            )
        )

        if last_time is None:
            return True

        return now - last_time >= cooldown

    def choose_weighted_action(
        self,
        actions,
    ):
        """按 weight 从候选动作中抽取一个。"""

        if not actions:
            return None

        weights = [
            max(
                1,
                int(action.get("weight", 1)),
            )
            for action in actions
        ]

        return random.choices(
            actions,
            weights=weights,
            k=1,
        )[0]

    def play_trigger_action(self, trigger_name):
        """按概率、权重和冷却播放事件动作。"""

        now = time.monotonic()
        candidates = []

        for action in self.get_actions_for_trigger(
            trigger_name
        ):
            if not self.action_is_off_cooldown(
                action,
                trigger_name,
                now,
            ):
                continue

            chance = float(
                action.get("chance", 100)
            )

            if random.random() * 100 > chance:
                continue

            candidates.append(action)

        action = self.choose_weighted_action(
            candidates
        )

        if action is None:
            return False

        self.action_last_triggered[
            self.action_trigger_key(
                action,
                trigger_name,
            )
        ] = now

        self.play_custom_action(
            action,
            trigger_name=trigger_name,
        )
        return True

    def get_random_actions(self):
        """返回允许自动随机触发的线上动作。"""

        return [
            action
            for action in self.actions
            if "random" in action.get(
                "triggers",
                [],
            )
        ]

    def schedule_random_action(self):
        """安排下一次随机线上动作。"""

        if not hasattr(self, "random_action_timer"):
            return

        self.random_action_timer.stop()

        if not self.get_random_actions():
            return

        if not self.random_action_has_played:
            wait_milliseconds = random.randint(
                8000,
                15000,
            )
        else:
            wait_seconds = random.randint(
                self.settings["action_wait_min"],
                self.settings["action_wait_max"],
            )
            wait_milliseconds = wait_seconds * 1000

        self.random_action_timer.start(
            wait_milliseconds
        )

    def try_random_action(self):
        """到时间后播放随机线上动作。"""

        random_actions = self.get_random_actions()

        if not random_actions:
            return

        if (
            self.isVisible()
            and self.state in (
                "normal",
                "walking",
            )
            and not self.is_dragging
        ):
            if self.play_trigger_action("random"):
                self.random_action_has_played = True
                return

            # 候选动作正在冷却，或本次概率未通过。
            self.random_action_timer.start(5000)
            return

        # 正在睡觉、被点击或被拖动时，几秒后再试。
        # 不重新等待完整的 45～100 秒。
        self.random_action_timer.start(3000)

    def referenced_action_sound_names(self):
        """返回当前动作实际引用的全部音效文件名。"""

        filenames = []

        for action in self.actions:
            for step in action.get("steps", []):
                if step.get("type") != "sound":
                    continue

                filename = (
                    self.sanitize_action_sound_filename(
                        step.get("file", "")
                    )
                )

                if (
                    filename is not None
                    and filename not in filenames
                ):
                    filenames.append(filename)

        return filenames

    def iter_cached_sound_effects(self):
        """遍历当前缓存的音效播放器。"""

        for effect in self.sound_effects.values():
            if effect is not None:
                yield effect

    def create_sound_effect(self, filename):
        """为一个 WAV 建立一个长期复用的低延迟播放器。"""

        sound_path = ONLINE_SOUND_DIR / filename

        if not sound_path.exists():
            return None

        old_effect = self.sound_effects.pop(
            filename,
            None,
        )

        if old_effect is not None:
            try:
                old_effect.stop()
                old_effect.deleteLater()
            except RuntimeError:
                pass

        effect = QSoundEffect(self)
        effect.setSource(
            QUrl.fromLocalFile(
                str(sound_path)
            )
        )
        effect.setLoopCount(1)
        effect.setVolume(1.0)

        self.sound_effects[filename] = effect
        return effect

    def release_action_sound_cache(self):
        """彻底释放 QSoundEffect 对 WAV 文件的占用。"""

        self.sound_cache_generation += 1

        for effect in self.iter_cached_sound_effects():
            try:
                effect.stop()
                effect.setSource(QUrl())
                effect.deleteLater()
            except RuntimeError:
                pass

        self.sound_effects = {}

        if winsound is not None:
            try:
                winsound.PlaySound(None, 0)
            except (RuntimeError, OSError):
                pass

        # 让 deleteLater / setSource 立即交还文件句柄，
        # 避免 Windows 在更新 poke.wav 时仍占用旧文件。
        QApplication.processEvents()

    def discard_cached_sound_effect(self, filename):
        """只释放指定音效的播放器。"""

        effect = self.sound_effects.pop(
            filename,
            None,
        )

        if effect is None:
            return

        try:
            effect.stop()
            effect.setSource(QUrl())
            effect.deleteLater()
        except RuntimeError:
            pass

        QApplication.processEvents()

    def refresh_action_sound_cache(self):
        """保留仍然有效的播放器，只增删真正变化的音效。"""

        desired_names = set(
            self.referenced_action_sound_names()
        )
        cached_names = set(
            self.sound_effects.keys()
        )

        # 删除 actions.json 已经不再引用的音效播放器。
        for filename in (
            cached_names - desired_names
        ):
            self.discard_cached_sound_effect(
                filename
            )

        created_any = False

        # 新增刚刚出现、或因文件替换而被释放的播放器。
        for filename in desired_names:
            if filename in self.sound_effects:
                continue

            effect = self.create_sound_effect(
                filename
            )

            if effect is not None:
                created_any = True

        if created_any:
            generation = self.sound_cache_generation
            QTimer.singleShot(
                180,
                lambda current_generation=generation:
                self.warm_action_sounds(
                    20,
                    current_generation,
                ),
            )

    def preload_action_sounds(self):
        """预先加载动作音效，并只复用一个播放器。"""

        self.release_action_sound_cache()
        generation = self.sound_cache_generation

        for filename in (
            self.referenced_action_sound_names()
        ):
            self.create_sound_effect(filename)

        # 等文件加载完成后静音播放极短时间一次，
        # 只用于启动时唤醒音频后端，避免第一次戳明显迟到。
        QTimer.singleShot(
            180,
            lambda current_generation=generation:
            self.warm_action_sounds(
                20,
                current_generation,
            ),
        )

    def warm_action_sounds(
        self,
        attempts_left,
        generation,
    ):
        """启动时对已加载音效做一次静音预热。"""

        if generation != self.sound_cache_generation:
            return

        still_loading = False

        for effect in self.iter_cached_sound_effects():
            try:
                if effect.property("guozi_warmed"):
                    continue

                if not effect.isLoaded():
                    still_loading = True
                    continue

                effect.setProperty(
                    "guozi_warmed",
                    True,
                )
                effect.setMuted(True)
                effect.play()

                QTimer.singleShot(
                    60,
                    lambda current_effect=effect,
                    current_generation=generation:
                    self.finish_sound_warmup(
                        current_effect,
                        current_generation,
                    ),
                )
            except RuntimeError:
                continue

        if still_loading and attempts_left > 0:
            QTimer.singleShot(
                120,
                lambda current_generation=generation:
                self.warm_action_sounds(
                    attempts_left - 1,
                    current_generation,
                ),
            )

    def finish_sound_warmup(
        self,
        effect,
        generation,
    ):
        """结束静音预热。"""

        if generation != self.sound_cache_generation:
            return

        try:
            effect.stop()
            effect.setMuted(False)
        except RuntimeError:
            pass

    def get_ready_sound_effect(
        self,
        filename,
    ):
        """取得可用播放器；发生错误时自动重建。"""

        effect = self.sound_effects.get(filename)

        if effect is None:
            effect = self.create_sound_effect(
                filename
            )

        if effect is None:
            return None

        try:
            if (
                effect.status()
                == QSoundEffect.Status.Error
            ):
                effect = self.create_sound_effect(
                    filename
                )
        except RuntimeError:
            effect = self.create_sound_effect(
                filename
            )

        return effect

    def play_action_sound(
        self,
        filename,
        attempts_left=12,
    ):
        """稳定播放短 WAV 音效。"""

        debug_log(
            f"sound request file={filename!r} "
            f"attempts={attempts_left}"
        )

        safe_name = (
            self.sanitize_action_sound_filename(
                filename
            )
        )

        if safe_name is None:
            return False

        sound_path = ONLINE_SOUND_DIR / safe_name

        if not sound_path.exists():
            debug_log(
                f"sound missing path={sound_path}"
            )
            return False

        effect = self.get_ready_sound_effect(
            safe_name
        )

        if effect is None:
            # 缓存可能刚被在线更新释放；立即重建一次。
            effect = self.create_sound_effect(
                safe_name
            )

        if effect is None:
            return self.play_sound_windows_fallback(
                sound_path
            )

        try:
            try:
                debug_log(
                    "sound effect before "
                    f"loaded={effect.isLoaded()} "
                    f"playing={effect.isPlaying()} "
                    f"status={effect.status()}"
                )
            except RuntimeError:
                debug_log(
                    "sound effect before RuntimeError"
                )

            if not effect.isLoaded():
                if attempts_left > 0:
                    QTimer.singleShot(
                        30,
                        lambda current_name=safe_name,
                        remaining=attempts_left - 1:
                        self.play_action_sound(
                            current_name,
                            remaining,
                        ),
                    )
                    return True

                return self.play_sound_windows_fallback(
                    sound_path
                )

            # 连续戳时重新从音效开头播放。
            # 只复用这一个 QSoundEffect，不再同时占用多个音效实例。
            if effect.isPlaying():
                effect.stop()

            effect.setMuted(False)
            effect.play()

            try:
                debug_log(
                    "sound effect after play "
                    f"loaded={effect.isLoaded()} "
                    f"playing={effect.isPlaying()} "
                    f"status={effect.status()}"
                )
            except RuntimeError:
                debug_log(
                    "sound effect after play RuntimeError"
                )

            QTimer.singleShot(
                70,
                lambda current_name=safe_name,
                current_effect=effect,
                current_path=sound_path:
                self.check_sound_playback(
                    current_name,
                    current_effect,
                    current_path,
                ),
            )
            return True

        except RuntimeError:
            self.create_sound_effect(safe_name)
            return self.play_sound_windows_fallback(
                sound_path
            )

    def check_sound_playback(
        self,
        filename,
        effect,
        sound_path,
    ):
        """音效后端异常时重建播放器并用 Windows 补播一次。"""

        try:
            debug_log(
                "sound verify "
                f"file={filename} "
                f"loaded={effect.isLoaded()} "
                f"playing={effect.isPlaying()} "
                f"status={effect.status()}"
            )
            if (
                effect.status()
                == QSoundEffect.Status.Error
            ):
                self.create_sound_effect(filename)
                self.play_sound_windows_fallback(
                    sound_path
                )
                return

            if effect.isPlaying():
                return

        except RuntimeError:
            self.create_sound_effect(filename)

        self.play_sound_windows_fallback(
            sound_path
        )

    def play_sound_windows_fallback(
        self,
        sound_path,
    ):
        """Qt 真正失败时才使用 Windows WAV 播放器。"""

        if winsound is None:
            return False

        try:
            winsound.PlaySound(
                str(sound_path),
                winsound.SND_FILENAME
                | winsound.SND_ASYNC
                | winsound.SND_NODEFAULT,
            )
            debug_log(
                f"winsound fallback started path={sound_path}"
            )
            return True

        except (RuntimeError, OSError):
            return False

    def stop_action_sound(self):
        """停止果子当前播放的动作音效。"""

        for effect in self.iter_cached_sound_effects():
            try:
                effect.stop()
            except RuntimeError:
                pass

        if winsound is not None:
            try:
                winsound.PlaySound(None, 0)
            except (RuntimeError, OSError):
                pass

    def prepare_custom_action(self):
        """暂停会干扰线上动作的其他行为。"""

        self.stop_custom_action(resume=False)
        self.random_action_timer.stop()
        self.stop_walk_timers()
        self.cancel_release_bounce()
        self.drag_animation_timer.stop()

        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.happy_timer.stop()
        self.auto_speech_timer.stop()
        self.sleep_wait_timer.stop()

        if self.state == "sleeping":
            self.sleep_duration_timer.stop()
            self.sleep_animation_timer.stop()
            self.sleep_zzz.stop_animation()

    def play_custom_action(
        self,
        action,
        trigger_name=None,
    ):
        """播放由多个安全步骤组成的线上动作。"""

        debug_log(
            f"custom action start "
            f"name={action.get('name') if isinstance(action, dict) else None} "
            f"trigger={trigger_name} state={self.state}"
        )

        if not isinstance(action, dict):
            return

        steps = action.get("steps", [])

        if not isinstance(steps, list) or not steps:
            self.say("这个动作没有可执行步骤。")
            return

        self.prepare_custom_action()

        self.active_custom_action_trigger = (
            trigger_name
        )
        self.state = "custom_action"
        self.custom_action_steps = [
            dict(step)
            for step in steps
            if isinstance(step, dict)
        ]
        self.custom_action_step_index = -1
        self.custom_action_current_step = None
        self.custom_action_origin = QPoint(
            self.x(),
            self.y(),
        )

        self.advance_custom_action_step()

    def advance_custom_action_step(self):
        """继续执行下一个动作步骤。"""

        self.custom_action_timer.stop()
        self.custom_action_step_index += 1

        while (
            self.custom_action_step_index
            < len(self.custom_action_steps)
        ):
            step = self.custom_action_steps[
                self.custom_action_step_index
            ]
            self.custom_action_current_step = step
            step_type = step.get("type")

            if step_type == "say":
                texts = step.get("texts", [])
                text = step.get("text", "")

                if texts:
                    text = random.choice(texts)

                if text:
                    self.say(text)

                self.custom_action_step_index += 1
                continue

            if step_type == "sound":
                if not self.play_action_sound(
                    step.get("file", "")
                ):
                    self.say("动作音效还没有下载好。")

                self.custom_action_step_index += 1
                continue

            if step_type == "stop_sound":
                self.stop_action_sound()
                self.custom_action_step_index += 1
                continue

            if step_type == "wait":
                self.start_timed_custom_step(
                    step["duration"]
                )
                return

            if step_type == "frames":
                try:
                    frames = [
                        self.load_action_image(filename)
                        for filename in step["frames"]
                    ]
                except (
                    KeyError,
                    TypeError,
                    FileNotFoundError,
                ):
                    self.say("动作图片还没有下载好。")
                    self.stop_custom_action(
                        completed=True
                    )
                    return

                sequence = list(frames)

                if (
                    step.get("playback") == "pingpong"
                    and len(frames) > 1
                ):
                    sequence += frames[-2:0:-1]

                self.custom_action_frame_sequence = (
                    sequence
                    * int(step.get("loops", 1))
                )
                self.custom_action_frame_index = 0

                if not self.custom_action_frame_sequence:
                    self.custom_action_step_index += 1
                    continue

                self.setPixmap(
                    self.custom_action_frame_sequence[0]
                )
                self.custom_action_frame_index = 1
                self.custom_action_timer.setInterval(
                    int(
                        step.get(
                            "frame_interval",
                            220,
                        )
                    )
                )
                self.custom_action_timer.start()
                return

            if step_type in (
                "move",
                "move_random",
                "move_edge",
                "move_mouse",
                "move_away_mouse",
                "return",
                "jump",
                "shake",
            ):
                self.start_motion_custom_step(step)
                return

            self.custom_action_step_index += 1

        self.stop_custom_action(
            completed=True
        )

    def start_timed_custom_step(self, duration):
        self.custom_action_step_started = (
            time.monotonic()
        )
        self.custom_action_step_duration = max(
            1,
            int(duration),
        )
        self.custom_action_timer.setInterval(30)
        self.custom_action_timer.start()

    def current_action_screen_area(self):
        """返回果子当前所在屏幕的可用区域。"""

        center = QPoint(
            self.x() + self.width() // 2,
            self.y() + self.height() // 2,
        )
        return self.screen_area_for_point(center)

    def start_motion_custom_step(self, step):
        """准备动作中的位置变化步骤。"""

        self.custom_action_step_start = QPoint(
            self.x(),
            self.y(),
        )
        step_type = step["type"]

        if step_type == "move":
            target_x, target_y = self.safe_coordinates(
                self.x() + int(step["dx"]),
                self.y() + int(step["dy"]),
            )
            self.custom_action_step_target = QPoint(
                target_x,
                target_y,
            )

        elif step_type == "move_random":
            area = self.current_action_screen_area()

            if area is None:
                self.custom_action_step_target = QPoint(
                    self.x(),
                    self.y(),
                )
            else:
                margin = int(step.get("margin", 20))
                left = area.left() + margin
                top = area.top() + margin
                right = (
                    area.right()
                    - self.width()
                    + 1
                    - margin
                )
                bottom = (
                    area.bottom()
                    - self.height()
                    + 1
                    - margin
                )

                if right < left or bottom < top:
                    target_x, target_y = self.safe_coordinates(
                        self.x(),
                        self.y(),
                    )
                else:
                    target_x = random.randint(left, right)
                    target_y = random.randint(top, bottom)

                self.custom_action_step_target = QPoint(
                    target_x,
                    target_y,
                )

        elif step_type == "move_edge":
            area = self.current_action_screen_area()

            if area is None:
                self.custom_action_step_target = QPoint(
                    self.x(),
                    self.y(),
                )
            else:
                margin = int(step.get("margin", 20))
                left = area.left() + margin
                top = area.top() + margin
                right = (
                    area.right()
                    - self.width()
                    + 1
                    - margin
                )
                bottom = (
                    area.bottom()
                    - self.height()
                    + 1
                    - margin
                )
                edge = step.get("edge", "random")

                if edge == "random":
                    edge = random.choice(
                        [
                            "left",
                            "right",
                            "top",
                            "bottom",
                        ]
                    )

                target_x = max(
                    left,
                    min(self.x(), right),
                )
                target_y = max(
                    top,
                    min(self.y(), bottom),
                )

                if edge == "left":
                    target_x = left
                elif edge == "right":
                    target_x = right
                elif edge == "top":
                    target_y = top
                else:
                    target_y = bottom

                target_x, target_y = self.safe_coordinates(
                    target_x,
                    target_y,
                )
                self.custom_action_step_target = QPoint(
                    target_x,
                    target_y,
                )

        elif step_type == "move_mouse":
            cursor = QCursor.pos()
            target_x, target_y = self.safe_coordinates(
                cursor.x()
                - self.width() // 2
                + int(step.get("offset_x", 0)),
                cursor.y()
                - self.height() // 2
                + int(step.get("offset_y", 0)),
            )
            self.custom_action_step_target = QPoint(
                target_x,
                target_y,
            )

        elif step_type == "move_away_mouse":
            cursor = QCursor.pos()
            pet_center_x = (
                self.x() + self.width() / 2
            )
            pet_center_y = (
                self.y() + self.height() / 2
            )
            dx = pet_center_x - cursor.x()
            dy = pet_center_y - cursor.y()
            length = math.hypot(dx, dy)

            if length < 1:
                angle = random.uniform(
                    0,
                    2 * math.pi,
                )
                dx = math.cos(angle)
                dy = math.sin(angle)
                length = 1.0

            distance = int(step.get("distance", 180))
            target_x, target_y = self.safe_coordinates(
                round(
                    self.x()
                    + dx / length * distance
                ),
                round(
                    self.y()
                    + dy / length * distance
                ),
            )
            self.custom_action_step_target = QPoint(
                target_x,
                target_y,
            )

        elif step_type == "return":
            if self.custom_action_origin is None:
                self.custom_action_step_target = QPoint(
                    self.x(),
                    self.y(),
                )
            else:
                target_x, target_y = self.safe_coordinates(
                    self.custom_action_origin.x(),
                    self.custom_action_origin.y(),
                )
                self.custom_action_step_target = QPoint(
                    target_x,
                    target_y,
                )

        else:
            self.custom_action_step_target = QPoint(
                self.x(),
                self.y(),
            )

        self.start_timed_custom_step(
            int(step["duration"])
        )

    def custom_action_progress(self):
        elapsed_ms = (
            time.monotonic()
            - self.custom_action_step_started
        ) * 1000.0

        return min(
            1.0,
            elapsed_ms
            / max(
                1,
                self.custom_action_step_duration,
            ),
        )

    def update_custom_action_step(self):
        """驱动当前动作步骤。"""

        if self.state != "custom_action":
            self.custom_action_timer.stop()
            return

        step = self.custom_action_current_step

        if not isinstance(step, dict):
            self.advance_custom_action_step()
            return

        step_type = step.get("type")

        position_step_types = (
            "move",
            "move_random",
            "move_edge",
            "move_mouse",
            "move_away_mouse",
            "return",
            "jump",
            "shake",
        )

        if (
            self.is_dragging
            and self.drag_mode == "preserve"
            and step_type in position_step_types
        ):
            if self.drag_custom_action_pause_started is None:
                self.drag_custom_action_pause_started = (
                    time.monotonic()
                )
            return

        if (
            self.drag_custom_action_pause_started is not None
            and step_type in position_step_types
        ):
            self.custom_action_step_started += (
                time.monotonic()
                - self.drag_custom_action_pause_started
            )
            self.drag_custom_action_pause_started = None

        if step_type == "frames":
            if (
                self.custom_action_frame_index
                >= len(
                    self.custom_action_frame_sequence
                )
            ):
                self.advance_custom_action_step()
                return

            self.setPixmap(
                self.custom_action_frame_sequence[
                    self.custom_action_frame_index
                ]
            )
            self.custom_action_frame_index += 1
            return

        progress = self.custom_action_progress()

        if step_type == "wait":
            if progress >= 1.0:
                self.advance_custom_action_step()
            return

        if step_type in (
            "move",
            "move_random",
            "move_edge",
            "move_mouse",
            "move_away_mouse",
            "return",
        ):
            start = self.custom_action_step_start
            target = self.custom_action_step_target

            x = round(
                start.x()
                + (
                    target.x() - start.x()
                ) * progress
            )
            y = round(
                start.y()
                + (
                    target.y() - start.y()
                ) * progress
            )
            self.move(x, y)

        elif step_type == "jump":
            start = self.custom_action_step_start
            repeats = int(step.get("repeats", 1))
            height = int(step.get("height", 35))

            jump_curve = abs(
                math.sin(
                    math.pi
                    * repeats
                    * progress
                )
            )
            x, y = self.safe_coordinates(
                start.x(),
                round(
                    start.y()
                    - height * jump_curve
                ),
            )
            self.move(x, y)

        elif step_type == "shake":
            start = self.custom_action_step_start
            distance = int(
                step.get("distance", 7)
            )
            cycles = int(step.get("cycles", 7))

            x_offset = round(
                distance
                * math.sin(
                    2
                    * math.pi
                    * cycles
                    * progress
                )
            )
            y_offset = round(
                distance
                * 0.35
                * math.sin(
                    4
                    * math.pi
                    * cycles
                    * progress
                )
            )
            x, y = self.safe_coordinates(
                start.x() + x_offset,
                start.y() + y_offset,
            )
            self.move(x, y)

        if progress >= 1.0:
            if step_type in (
                "jump",
                "shake",
            ):
                start = self.custom_action_step_start
                self.move_to_safe_position(
                    start.x(),
                    start.y(),
                )
            elif step_type in (
                "move",
                "move_random",
                "move_edge",
                "move_mouse",
                "move_away_mouse",
                "return",
            ):
                target = self.custom_action_step_target
                self.move_to_safe_position(
                    target.x(),
                    target.y(),
                )

            self.advance_custom_action_step()

    def stop_custom_action(
        self,
        resume=True,
        completed=False,
    ):
        """停止线上动作并恢复普通状态。"""

        debug_log(
            f"custom action stop state={self.state} "
            f"trigger={self.active_custom_action_trigger} "
            f"resume={resume} completed={completed}"
        )

        finished_trigger = (
            self.active_custom_action_trigger
        )
        self.active_custom_action_trigger = None

        self.custom_action_timer.stop()

        current_type = None

        if isinstance(
            self.custom_action_current_step,
            dict,
        ):
            current_type = (
                self.custom_action_current_step.get(
                    "type"
                )
            )

        if (
            current_type in (
                "jump",
                "shake",
            )
            and self.custom_action_step_start
            is not None
        ):
            self.move_to_safe_position(
                self.custom_action_step_start.x(),
                self.custom_action_step_start.y(),
            )

        self.custom_action_frames = []
        self.custom_action_frame_index = 0
        self.custom_action_frames_left = 0
        self.custom_action_frame_sequence = []
        self.custom_action_steps = []
        self.custom_action_step_index = -1
        self.custom_action_current_step = None
        self.custom_action_step_start = None
        self.custom_action_step_target = None
        self.custom_action_origin = None

        if self.state == "custom_action":
            self.state = "normal"
            self.setPixmap(self.normal)

        if resume:
            self.schedule_blink()
            self.schedule_auto_speech()
            self.schedule_random_action()
            self.schedule_sleep()

            if not self.walking_paused:
                self.schedule_walk()

        if (
            finished_trigger in (
                "triple_click",
                "five_click",
            )
            and (
                completed
                or self.poke_input_locked
            )
        ):
            self.finish_poke_milestone_action(
                finished_trigger
            )

    def add_actions_to_menu(self, menu):
        """把已下载的手动动作加入一个菜单。"""

        manual_actions = [
            action
            for action in self.actions
            if "manual" in action.get(
                "triggers",
                [],
            )
        ]

        actions_menu = menu.addMenu("线上动作")

        if not manual_actions:
            empty_action = QAction(
                "暂无可用动作",
                actions_menu,
            )
            empty_action.setEnabled(False)
            actions_menu.addAction(empty_action)
            return

        for action_data in manual_actions:
            action_item = QAction(
                action_data["name"],
                actions_menu,
            )
            action_item.triggered.connect(
                lambda checked=False, data=action_data:
                self.play_custom_action(data)
            )
            actions_menu.addAction(action_item)

    def refresh_tray_actions_menu(self):
        if not hasattr(self, "tray_actions_menu"):
            return

        self.tray_actions_menu.clear()

        manual_actions = [
            action
            for action in self.actions
            if "manual" in action.get(
                "triggers",
                [],
            )
        ]

        if not manual_actions:
            empty_action = QAction(
                "暂无可用动作",
                self.tray_actions_menu,
            )
            empty_action.setEnabled(False)
            self.tray_actions_menu.addAction(
                empty_action
            )
            return

        for action_data in manual_actions:
            action_item = QAction(
                action_data["name"],
                self.tray_actions_menu,
            )
            action_item.triggered.connect(
                lambda checked=False, data=action_data:
                self.play_custom_action(data)
            )
            self.tray_actions_menu.addAction(
                action_item
            )

    # ---------- 桌面双击召唤 ----------

    def windows_class_name(self, hwnd):
        """读取一个 Windows 窗口的类名。"""

        if (
            sys.platform != "win32"
            or not hwnd
        ):
            return ""

        buffer = ctypes.create_unicode_buffer(
            256
        )
        length = ctypes.windll.user32.GetClassNameW(
            hwnd,
            buffer,
            len(buffer),
        )

        return (
            buffer.value
            if length > 0
            else ""
        )

    def is_desktop_background_point(
        self,
        native_point,
    ):
        """判断原生鼠标坐标是否属于 Windows 桌面区域。"""

        if sys.platform != "win32":
            return False

        hwnd = ctypes.windll.user32.WindowFromPoint(
            native_point
        )

        # 沿父窗口向上查找；桌面在不同 Windows
        # 版本里可能由这些不同窗口类承载。
        for _ in range(8):
            if not hwnd:
                break

            if (
                self.windows_class_name(hwnd)
                in DESKTOP_WINDOW_CLASSES
            ):
                return True

            hwnd = ctypes.windll.user32.GetParent(
                hwnd
            )

        return False

    def point_inside_pet(self, point):
        """判断全局坐标是否落在果子窗口附近。"""

        return self.frameGeometry().adjusted(
            -5,
            -5,
            5,
            5,
        ).contains(point)

    def poll_global_mouse(self):
        """检测桌面空白处的全局左键双击。"""

        if sys.platform != "win32":
            return

        is_down = bool(
            ctypes.windll.user32.GetAsyncKeyState(
                VK_LBUTTON
            )
            & 0x8000
        )

        if is_down and not self.global_left_was_down:
            native_point = wintypes.POINT()

            if ctypes.windll.user32.GetCursorPos(
                ctypes.byref(native_point)
            ):
                qt_point = QCursor.pos()
                self.record_global_click(
                    qt_point,
                    native_point,
                )

        self.global_left_was_down = is_down

        if (
            self.global_first_click_point is not None
            and (
                time.monotonic()
                - self.global_first_click_time
            ) > 0.8
        ):
            self.global_first_click_point = None
            self.global_first_click_time = 0.0

    def record_global_click(
        self,
        point,
        native_point,
    ):
        """记录一次桌面点击并识别双击。"""

        # 双击果子本体不触发召唤，也不再执行旧反应。
        if self.point_inside_pet(point):
            self.global_first_click_point = None
            self.global_first_click_time = 0.0
            return

        # 避免在网页、文件或聊天窗口中双击时误召唤。
        if not self.is_desktop_background_point(
            native_point
        ):
            self.global_first_click_point = None
            self.global_first_click_time = 0.0
            return

        now = time.monotonic()
        style_hints = QApplication.styleHints()
        max_interval = (
            style_hints.mouseDoubleClickInterval()
            / 1000.0
        )
        max_distance = max(
            4,
            style_hints.mouseDoubleClickDistance(),
        )

        if (
            self.global_first_click_point is not None
            and (
                now - self.global_first_click_time
            ) <= max_interval
            and (
                point
                - self.global_first_click_point
            ).manhattanLength() <= max_distance
        ):
            self.global_first_click_point = None
            self.global_first_click_time = 0.0
            self.run_to_screen_point(point)
            return

        self.global_first_click_point = QPoint(point)
        self.global_first_click_time = now

    def run_to_screen_point(self, point):
        """让果子快速跑到双击位置附近。"""

        if not self.isVisible():
            return

        self.single_click_timer.stop()
        self.cancel_release_bounce()
        self.drag_animation_timer.stop()

        if self.state == "custom_action":
            self.stop_custom_action(
                resume=False
            )

        if self.state == "sleeping":
            self.sleep_duration_timer.stop()
            self.sleep_animation_timer.stop()
            self.sleep_zzz.stop_animation()

        self.stop_walk_timers()
        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.happy_timer.stop()
        self.auto_speech_timer.stop()
        self.sleep_wait_timer.stop()

        target_x, target_y = self.safe_coordinates(
            point.x() - self.width() // 2,
            point.y() - self.height() // 2,
        )

        current_x = self.x()
        current_y = self.y()
        dx = target_x - current_x
        dy = target_y - current_y
        distance = math.hypot(dx, dy)

        if distance < 8:
            self.state = "normal"
            self.setPixmap(self.normal)
            self.resume_normal_schedules()
            return

        speed = max(
            6.0,
            float(
                self.settings["walk_speed"]
            ) * 3.0,
        )

        self.state = "walking"
        self.summon_run_active = True
        self.walk_direction = -1 if dx < 0 else 1
        self.walk_float_x = float(current_x)
        self.walk_float_y = float(current_y)
        self.walk_velocity_x = speed * dx / distance
        self.walk_velocity_y = speed * dy / distance
        self.walk_target_x = target_x
        self.walk_target_y = target_y
        self.walk_steps_left = max(
            1,
            math.ceil(distance / speed),
        )
        self.walk_frame_index = 0

        self.show_current_walk_frame()
        self.walk_move_timer.start()
        self.walk_animation_timer.start()

    def resume_normal_schedules(self):
        """恢复普通眨眼、说话、散步和睡觉安排。"""

        self.schedule_blink()
        self.schedule_auto_speech()
        self.schedule_random_action()
        self.schedule_sleep()

        if not self.walking_paused:
            self.schedule_walk()

    # ---------- 散步 ----------

    def schedule_walk(self):
        self.walk_wait_timer.stop()

        if (
            self.walking_paused
            or self.state == "sleeping"
            or not self.isVisible()
        ):
            return

        self.walk_wait_timer.start(
            random.randint(3000, 7000)
        )

    def start_walking(self):
        if (
            self.walking_paused
            or self.state != "normal"
            or self.is_dragging
            or not self.isVisible()
        ):
            self.schedule_walk()
            return

        if (
            self.settings["movement_mode"]
            == "full_screen"
        ):
            self.start_full_screen_walk()
        else:
            self.start_horizontal_walk()

    def start_horizontal_walk(self):
        """沿当前高度左右散步。"""

        center = QPoint(
            self.x() + self.width() // 2,
            self.y() + self.height() // 2,
        )
        area = self.screen_area_for_point(center)

        if area is None:
            self.schedule_walk()
            return

        edge_padding = 14
        left_limit = area.left() + edge_padding
        right_limit = (
            area.right()
            - self.width()
            + 1
            - edge_padding
        )

        current_x = max(
            left_limit,
            min(self.x(), right_limit),
        )

        if current_x != self.x():
            self.move(current_x, self.y())

        left_space = max(
            0,
            current_x - left_limit,
        )
        right_space = max(
            0,
            right_limit - current_x,
        )

        minimum_space = max(
            24,
            self.settings["walk_speed"] * 12,
        )

        possible_directions = []

        if left_space >= minimum_space:
            possible_directions.append(-1)

        if right_space >= minimum_space:
            possible_directions.append(1)

        if not possible_directions:
            self.schedule_walk()
            return

        self.state = "walking"
        self.walk_direction = random.choice(
            possible_directions
        )
        self.walk_velocity_x = float(
            self.settings["walk_speed"]
            * self.walk_direction
        )
        self.walk_velocity_y = 0.0
        self.walk_float_x = float(self.x())
        self.walk_float_y = float(self.y())

        available_distance = (
            left_space
            if self.walk_direction == -1
            else right_space
        )
        maximum_steps = max(
            1,
            available_distance
            // self.settings["walk_speed"],
        )
        upper_steps = min(150, maximum_steps)
        lower_steps = min(60, upper_steps)

        self.walk_steps_left = random.randint(
            max(1, lower_steps),
            max(1, upper_steps),
        )
        self.walk_frame_index = 0

        self.show_current_walk_frame()
        self.walk_move_timer.start()
        self.walk_animation_timer.start()

    def start_full_screen_walk(self):
        """在当前屏幕可用区域里选择目标点并斜向移动。"""

        center = QPoint(
            self.x() + self.width() // 2,
            self.y() + self.height() // 2,
        )
        area = self.screen_area_for_point(center)

        if area is None:
            self.schedule_walk()
            return

        edge_padding = 14
        left_limit = area.left() + edge_padding
        top_limit = area.top() + edge_padding
        right_limit = (
            area.right()
            - self.width()
            + 1
            - edge_padding
        )
        bottom_limit = (
            area.bottom()
            - self.height()
            + 1
            - edge_padding
        )

        if (
            right_limit < left_limit
            or bottom_limit < top_limit
        ):
            self.schedule_walk()
            return

        current_x = max(
            left_limit,
            min(self.x(), right_limit),
        )
        current_y = max(
            top_limit,
            min(self.y(), bottom_limit),
        )

        if (
            current_x != self.x()
            or current_y != self.y()
        ):
            self.move(current_x, current_y)

        minimum_distance = max(
            90,
            self.settings["walk_speed"] * 35,
        )

        target_x = current_x
        target_y = current_y
        distance = 0.0

        for _ in range(30):
            candidate_x = random.randint(
                left_limit,
                right_limit,
            )
            candidate_y = random.randint(
                top_limit,
                bottom_limit,
            )

            candidate_distance = math.hypot(
                candidate_x - current_x,
                candidate_y - current_y,
            )

            if candidate_distance >= minimum_distance:
                target_x = candidate_x
                target_y = candidate_y
                distance = candidate_distance
                break

        if distance <= 0:
            self.schedule_walk()
            return

        dx = target_x - current_x
        dy = target_y - current_y
        speed = max(
            1.5,
            float(self.settings["walk_speed"]),
        )

        self.state = "walking"
        self.walk_direction = -1 if dx < 0 else 1
        self.walk_float_x = float(current_x)
        self.walk_float_y = float(current_y)
        self.walk_velocity_x = speed * dx / distance
        self.walk_velocity_y = speed * dy / distance
        self.walk_target_x = target_x
        self.walk_target_y = target_y
        self.walk_steps_left = max(
            1,
            math.ceil(distance / speed),
        )
        self.walk_frame_index = 0

        self.show_current_walk_frame()
        self.walk_move_timer.start()
        self.walk_animation_timer.start()

    def show_current_walk_frame(self):
        frames = (
            self.walk_left
            if self.walk_direction == -1
            else self.walk_right
        )
        self.setPixmap(
            frames[self.walk_frame_index]
        )

    def update_walk_frame(self):
        if self.state != "walking":
            self.walk_animation_timer.stop()
            return

        self.walk_frame_index = (
            self.walk_frame_index + 1
        ) % len(
            self.walk_left
            if self.walk_direction == -1
            else self.walk_right
        )
        self.show_current_walk_frame()

    def update_walking(self):
        if self.state != "walking":
            self.stop_walk_timers()
            return

        if self.walking_paused:
            self.stop_walking()
            return

        if self.summon_run_active:
            self.update_summon_running()
        elif (
            self.settings["movement_mode"]
            == "full_screen"
        ):
            self.update_full_screen_walking()
        else:
            self.update_horizontal_walking()

    def update_summon_running(self):
        """更新双击召唤后的快速跑动。"""

        remaining_x = (
            self.walk_target_x
            - self.walk_float_x
        )
        remaining_y = (
            self.walk_target_y
            - self.walk_float_y
        )
        remaining_distance = math.hypot(
            remaining_x,
            remaining_y,
        )
        step_distance = math.hypot(
            self.walk_velocity_x,
            self.walk_velocity_y,
        )

        if (
            remaining_distance
            <= step_distance
            or self.walk_steps_left <= 1
        ):
            self.move(
                self.walk_target_x,
                self.walk_target_y,
            )
            self.summon_run_active = False
            self.stop_walking()
            self.resume_normal_schedules()
            return

        self.walk_float_x += self.walk_velocity_x
        self.walk_float_y += self.walk_velocity_y

        self.move(
            round(self.walk_float_x),
            round(self.walk_float_y),
        )
        self.walk_steps_left -= 1

    def update_horizontal_walking(self):
        """更新横向散步位置。"""

        new_x = self.x() + (
            self.settings["walk_speed"]
            * self.walk_direction
        )
        new_y = self.y()

        safe_x, safe_y = self.safe_coordinates(
            new_x,
            new_y,
        )

        if safe_x != new_x:
            self.move(safe_x, safe_y)

            if self.play_trigger_action("edge"):
                return

            self.walk_direction *= -1
            self.walk_velocity_x *= -1
            self.walk_steps_left = random.randint(
                40,
                90,
            )
            self.show_current_walk_frame()
            return

        self.move(safe_x, safe_y)

        self.walk_steps_left -= 1

        if self.walk_steps_left <= 0:
            self.stop_walking()

    def update_full_screen_walking(self):
        """更新满屏幕游走位置。"""

        self.walk_float_x += self.walk_velocity_x
        self.walk_float_y += self.walk_velocity_y

        new_x = round(self.walk_float_x)
        new_y = round(self.walk_float_y)

        safe_x, safe_y = self.safe_coordinates(
            new_x,
            new_y,
        )

        if (
            safe_x != new_x
            or safe_y != new_y
        ):
            self.move(safe_x, safe_y)

            if self.play_trigger_action("edge"):
                return

            self.stop_walking()
            return

        self.move(safe_x, safe_y)
        self.walk_steps_left -= 1

        reached_x = (
            abs(safe_x - self.walk_target_x)
            <= max(2, self.settings["walk_speed"])
        )
        reached_y = (
            abs(safe_y - self.walk_target_y)
            <= max(2, self.settings["walk_speed"])
        )

        if (
            self.walk_steps_left <= 0
            or (
                reached_x
                and reached_y
            )
        ):
            self.move(
                self.walk_target_x,
                self.walk_target_y,
            )
            self.stop_walking()

    def stop_walk_timers(self):
        self.walk_wait_timer.stop()
        self.walk_move_timer.stop()
        self.walk_animation_timer.stop()
        self.summon_run_active = False

    def stop_walking(self):
        self.stop_walk_timers()

        if self.state == "walking":
            self.state = "normal"
            self.setPixmap(self.normal)

        self.save_position()

        if not self.walking_paused:
            self.schedule_walk()

    def toggle_walking(self):
        if self.walking_paused:
            self.walking_paused = False

            if self.state != "sleeping":
                self.schedule_walk()
        else:
            self.walking_paused = True
            self.stop_walk_timers()

            if self.state == "walking":
                self.state = "normal"
                self.setPixmap(self.normal)

            self.save_position()

        self.update_menu_text()

    def set_movement_mode(self, mode):
        """切换横向散步或满屏幕游走。"""

        if mode not in (
            "horizontal",
            "full_screen",
        ):
            return

        changed = (
            self.settings["movement_mode"]
            != mode
        )
        self.settings["movement_mode"] = mode
        self.save_settings()

        self.stop_walk_timers()

        if self.state == "walking":
            self.state = "normal"
            self.setPixmap(self.normal)

        if (
            not self.walking_paused
            and self.state != "sleeping"
            and self.isVisible()
        ):
            self.schedule_walk()

        self.update_movement_mode_checks()

        if changed:
            self.say(
                "已切换到满屏幕游走。"
                if mode == "full_screen"
                else "已切换到横向散步。"
            )

    def update_movement_mode_checks(self):
        """更新托盘菜单里的模式勾选。"""

        if hasattr(
            self,
            "tray_horizontal_action",
        ):
            is_horizontal = (
                self.settings["movement_mode"]
                == "horizontal"
            )
            self.tray_horizontal_action.setChecked(
                is_horizontal
            )
            self.tray_full_screen_action.setChecked(
                not is_horizontal
            )

    # ---------- 拖动动画 ----------

    def begin_preserved_drag(self):
        """开始保留当前动画的拖动，不切换 drag1/drag2。"""

        self.drag_mode = "preserve"
        self.drag_preserved_state = self.state
        self.drag_last_window_position = QPoint(
            self.x(),
            self.y(),
        )

        # 散步只暂停“自动位移”，走路帧仍然继续播放。
        self.drag_walk_move_was_active = (
            self.walk_move_timer.isActive()
        )

        if self.drag_walk_move_was_active:
            self.walk_move_timer.stop()

        # 回弹本身会改窗口位置，拖动期间暂停。
        self.drag_bounce_was_active = (
            self.bounce_timer.isActive()
        )

        if self.drag_bounce_was_active:
            self.bounce_timer.stop()

        debug_log(
            f"preserved drag begin state={self.state}"
        )

    def rebase_preserved_motion(self, delta):
        """手动移动窗口后，同步平移原动作的位置基准。"""

        if delta.isNull():
            return

        if self.drag_preserved_state == "walking":
            self.walk_float_x += delta.x()
            self.walk_float_y += delta.y()
            self.walk_target_x += delta.x()
            self.walk_target_y += delta.y()

        if (
            self.bounce_base_position is not None
            and self.drag_preserved_state == "bouncing"
        ):
            self.bounce_base_position += delta

        # custom_action 未来的 return / move / jump / shake
        # 都继续以用户拖到的新位置为基准。
        if self.drag_preserved_state == "custom_action":
            if self.custom_action_step_start is not None:
                self.custom_action_step_start += delta

            if self.custom_action_step_target is not None:
                self.custom_action_step_target += delta

            if self.custom_action_origin is not None:
                self.custom_action_origin += delta

    def finish_preserved_drag(self):
        """松手后继续原状态，不追加拖动动画或回弹动作。"""

        if self.drag_custom_action_pause_started is not None:
            self.custom_action_step_started += (
                time.monotonic()
                - self.drag_custom_action_pause_started
            )
            self.drag_custom_action_pause_started = None

        if (
            self.drag_preserved_state == "walking"
            and self.drag_walk_move_was_active
            and self.state == "walking"
            and not self.walking_paused
        ):
            self.walk_move_timer.start()

        if (
            self.drag_preserved_state == "bouncing"
            and self.drag_bounce_was_active
            and self.state == "bouncing"
        ):
            self.bounce_timer.start()

        debug_log(
            f"preserved drag end state={self.state}"
        )

        self.drag_mode = None
        self.drag_preserved_state = None
        self.drag_last_window_position = None
        self.drag_walk_move_was_active = False
        self.drag_bounce_was_active = False
        self.save_position()

    def drag_grab_offset(self):
        """返回拖动图中被鼠标抓住的位置。"""

        return QPoint(
            round(
                self.width()
                * self.drag_grab_ratio_x
            ),
            round(
                self.height()
                * self.drag_grab_ratio_y
            ),
        )

    def start_drag_animation(self, cursor_position):
        """普通待机拖动：使用“提起来”动画。"""

        self.drag_mode = "pickup"

        debug_log(
            f"start_drag_animation previous_state={self.state}"
        )
        self.single_click_timer.stop()
        self.happy_timer.stop()
        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.sleep_wait_timer.stop()

        if self.state == "sleeping":
            self.sleep_duration_timer.stop()
            self.sleep_animation_timer.stop()
            self.sleep_zzz.stop_animation()

        self.state = "dragging"
        self.drag_frame_index = 0
        self.setPixmap(
            self.drag_frames[self.drag_frame_index]
        )

        # 从这一刻起，不再沿用最初按下鼠标的位置。
        # 无论原来点在果子的哪里，拖起来以后鼠标都会
        # 落在 drag1/drag2 顶部相同的“被提起点”。
        self.drag_position = self.drag_grab_offset()

        target = (
            cursor_position
            - self.drag_position
        )
        self.move_to_safe_position(
            target.x(),
            target.y(),
        )

        self.drag_animation_timer.start()

    def update_drag_frame(self):
        """拖动期间轮流切换 drag1 和 drag2。"""

        if self.state != "dragging":
            self.drag_animation_timer.stop()
            return

        self.drag_frame_index = (
            self.drag_frame_index + 1
        ) % len(self.drag_frames)

        self.setPixmap(
            self.drag_frames[self.drag_frame_index]
        )

    def stop_drag_animation(self):
        """停止“提起来”拖动动画并恢复普通状态。"""

        self.drag_animation_timer.stop()

        if self.state == "dragging":
            self.state = "normal"
            self.setPixmap(self.normal)

        self.drag_mode = None

    # ---------- 松手回弹 ----------

    def start_release_bounce(self):
        """松开拖动后，轻轻上下弹两次。"""

        self.bounce_timer.stop()
        self.bounce_base_position = QPoint(
            self.x(),
            self.y(),
        )
        self.bounce_frame_index = 0

        self.state = "bouncing"
        self.setPixmap(self.normal)
        self.bounce_timer.start()

    def update_release_bounce(self):
        """按预设的高度变化播放回弹。"""

        if (
            self.state != "bouncing"
            or self.bounce_base_position is None
        ):
            self.bounce_timer.stop()
            return

        if (
            self.bounce_frame_index
            >= len(self.bounce_offsets)
        ):
            self.finish_release_bounce()
            return

        offset_y = self.bounce_offsets[
            self.bounce_frame_index
        ]

        self.move_to_safe_position(
            self.bounce_base_position.x(),
            self.bounce_base_position.y()
            + offset_y,
        )

        self.bounce_frame_index += 1

        if (
            self.bounce_frame_index
            >= len(self.bounce_offsets)
        ):
            self.finish_release_bounce()

    def finish_release_bounce(self):
        """回弹结束后恢复正常行为。"""

        self.bounce_timer.stop()

        if self.bounce_base_position is not None:
            self.move_to_safe_position(
                self.bounce_base_position.x(),
                self.bounce_base_position.y(),
            )

        self.bounce_base_position = None
        self.bounce_frame_index = 0

        if self.state == "bouncing":
            self.state = "normal"
            self.setPixmap(self.normal)

        self.save_position()
        self.schedule_blink()
        self.schedule_auto_speech()
        self.schedule_sleep()

        if not self.walking_paused:
            self.schedule_walk()

    def cancel_release_bounce(self):
        """新互动开始时，立即停止回弹。"""

        self.bounce_timer.stop()

        if self.bounce_base_position is not None:
            self.move_to_safe_position(
                self.bounce_base_position.x(),
                self.bounce_base_position.y(),
            )

        self.bounce_base_position = None
        self.bounce_frame_index = 0

        if self.state == "bouncing":
            self.state = "normal"
            self.setPixmap(self.normal)

    # ---------- 睡觉 ----------

    def schedule_sleep(self):
        self.sleep_wait_timer.stop()

        if (
            self.state == "sleeping"
            or not self.isVisible()
        ):
            return

        wait_seconds = random.randint(
            self.settings["sleep_wait_min"],
            self.settings["sleep_wait_max"],
        )
        self.sleep_wait_timer.start(
            wait_seconds * 1000
        )

    def try_sleep(self):
        if (
            self.state == "normal"
            and not self.is_dragging
            and self.isVisible()
        ):
            self.start_sleeping()
        else:
            self.schedule_sleep()

    def schedule_auto_wake(self):
        """随机睡一段时间后自动醒来。"""

        self.sleep_duration_timer.stop()

        if self.state != "sleeping":
            return

        duration_seconds = random.randint(
            self.settings["sleep_duration_min"],
            self.settings["sleep_duration_max"],
        )

        self.sleep_duration_timer.start(
            duration_seconds * 1000
        )

    def start_sleeping(self):
        if self.state == "sleeping":
            return

        self.stop_walk_timers()
        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.happy_timer.stop()
        self.auto_speech_timer.stop()

        self.speech_hide_timer.stop()
        self.speech_bubble.hide()

        self.state = "sleeping"
        self.sleep_frame_index = 0
        self.setPixmap(
            self.sleep_frames[
                self.sleep_frame_index
            ]
        )
        self.sleep_animation_timer.start()

        self.position_sleep_zzz()
        self.sleep_zzz.start_animation()
        self.keep_all_windows_on_top()
        self.schedule_auto_wake()

        self.update_menu_text()

    def update_sleep_frame(self):
        if self.state != "sleeping":
            self.sleep_animation_timer.stop()
            return

        self.sleep_frame_index = (
            self.sleep_frame_index + 1
        ) % len(self.sleep_frames)

        self.setPixmap(
            self.sleep_frames[
                self.sleep_frame_index
            ]
        )

    def wake_up(self):
        debug_log(
            f"wake_up called state={self.state}"
        )

        if self.state != "sleeping":
            return

        self.sleep_duration_timer.stop()
        self.sleep_animation_timer.stop()
        self.sleep_zzz.stop_animation()

        self.state = "normal"
        self.setPixmap(self.normal)

        if self.play_trigger_action("wake"):
            self.update_menu_text()
            return

        # 没有线上醒来动作时，保留原来的反应。
        self.state = "happy"
        self.setPixmap(self.happy)
        self.say("耶嘿！")
        self.happy_timer.start(2000)

        self.schedule_auto_speech()
        self.schedule_sleep()
        self.update_menu_text()

    def toggle_sleep(self):
        if self.state == "sleeping":
            self.wake_up()
        else:
            self.start_sleeping()

    # ---------- 隐藏 ----------

    def prepare_for_hiding(self):
        self.stop_walk_timers()
        self.stop_drag_animation()
        self.cancel_release_bounce()
        self.stop_custom_action(resume=False)

        self.blink_wait_timer.stop()
        self.blink_close_timer.stop()
        self.happy_timer.stop()
        self.startup_hello_timer.stop()
        self.auto_speech_timer.stop()
        self.sleep_wait_timer.stop()
        self.sleep_duration_timer.stop()
        self.sleep_animation_timer.stop()
        self.sleep_zzz.stop_animation()

        self.speech_hide_timer.stop()
        self.speech_bubble.hide()

        self.state = "normal"
        self.setPixmap(self.normal)

        self.save_position()

    def toggle_visibility(self):
        if self.isVisible():
            self.prepare_for_hiding()
            self.hide()
        else:
            self.state = "normal"
            self.setPixmap(self.normal)
            self.show()
            self.keep_all_windows_on_top()

            self.schedule_blink()
            self.schedule_auto_speech()
            self.schedule_sleep()

            if not self.walking_paused:
                self.schedule_walk()

        self.update_menu_text()

    # ---------- 托盘菜单 ----------

    def setup_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(
            QIcon(
                str(
                    self.action_image_path(
                        "normal.PNG"
                    )
                )
            ),
            self,
        )
        self.tray_icon.setToolTip("果子")

        self.tray_menu = QMenu()

        self.tray_visibility_action = QAction(
            "隐藏果子",
            self,
        )
        self.tray_walk_action = QAction(
            "暂停散步",
            self,
        )
        self.tray_sleep_action = QAction(
            "让果子睡觉",
            self,
        )

        self.tray_horizontal_action = QAction(
            "横向散步",
            self,
        )
        self.tray_horizontal_action.setCheckable(True)
        self.tray_horizontal_action.triggered.connect(
            lambda: self.set_movement_mode(
                "horizontal"
            )
        )

        self.tray_full_screen_action = QAction(
            "满屏幕游走",
            self,
        )
        self.tray_full_screen_action.setCheckable(True)
        self.tray_full_screen_action.triggered.connect(
            lambda: self.set_movement_mode(
                "full_screen"
            )
        )

        self.tray_visibility_action.triggered.connect(
            self.toggle_visibility
        )
        self.tray_walk_action.triggered.connect(
            self.toggle_walking
        )
        self.tray_sleep_action.triggered.connect(
            self.toggle_sleep
        )

        speak_action = QAction(
            "让果子说句话",
            self,
        )
        speak_action.triggered.connect(
            self.say_random_message
        )

        reload_action = QAction(
            "重新载入设置、台词和动作",
            self,
        )
        reload_action.triggered.connect(
            self.reload_settings_and_messages
        )

        online_update_action = QAction(
            "检查在线更新",
            self,
        )
        online_update_action.triggered.connect(
            self.check_online_updates
        )

        open_messages_action = QAction(
            "打开台词文件",
            self,
        )
        open_messages_action.triggered.connect(
            self.open_messages_file
        )

        open_settings_action = QAction(
            "打开设置文件",
            self,
        )
        open_settings_action.triggered.connect(
            self.open_settings_file
        )

        exit_action = QAction(
            "退出果子",
            self,
        )
        exit_action.triggered.connect(
            self.quit_pet
        )

        self.tray_menu.addAction(
            self.tray_visibility_action
        )
        self.tray_menu.addAction(
            self.tray_walk_action
        )
        self.tray_movement_menu = (
            self.tray_menu.addMenu("移动模式")
        )
        self.tray_movement_menu.addAction(
            self.tray_horizontal_action
        )
        self.tray_movement_menu.addAction(
            self.tray_full_screen_action
        )
        self.update_movement_mode_checks()
        self.tray_menu.addAction(
            self.tray_sleep_action
        )
        self.tray_menu.addAction(speak_action)
        self.tray_actions_menu = (
            self.tray_menu.addMenu("线上动作")
        )
        self.refresh_tray_actions_menu()
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(reload_action)
        self.tray_menu.addAction(
            online_update_action
        )
        self.tray_menu.addAction(
            open_messages_action
        )
        self.tray_menu.addAction(
            open_settings_action
        )
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(
            self.tray_menu
        )
        self.tray_icon.activated.connect(
            self.tray_icon_activated
        )
        self.tray_icon.show()

    def tray_icon_activated(self, reason):
        if (
            reason
            == QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            self.toggle_visibility()

    def update_menu_text(self):
        if hasattr(
            self,
            "tray_visibility_action",
        ):
            self.tray_visibility_action.setText(
                "隐藏果子"
                if self.isVisible()
                else "显示果子"
            )
            self.tray_walk_action.setText(
                "继续散步"
                if self.walking_paused
                else "暂停散步"
            )
            self.tray_sleep_action.setText(
                "叫醒果子"
                if self.state == "sleeping"
                else "让果子睡觉"
            )
            self.update_movement_mode_checks()

    # ---------- 鼠标 ----------

    def mousePressEvent(self, event):
        if (
            event.button()
            == Qt.MouseButton.LeftButton
        ):
            # 按下本身不再停止任何动作，也不负责叫醒。
            # 先等用户真正“拖”或“松手点击”以后再决定行为。
            self.press_position = (
                event.globalPosition().toPoint()
            )
            self.drag_position = (
                self.press_position
                - self.frameGeometry().topLeft()
            )
            self.drag_last_window_position = QPoint(
                self.x(),
                self.y(),
            )
            self.is_dragging = False
            self.drag_mode = None
            self.click_woke_from_sleep = False

            debug_log(
                f"mouse press state={self.state} "
                f"poke_locked={self.poke_input_locked}"
            )

            event.accept()

    def mouseMoveEvent(self, event):
        if (
            event.buttons()
            & Qt.MouseButton.LeftButton
            and self.drag_position is not None
        ):
            current = (
                event.globalPosition().toPoint()
            )

            if (
                not self.is_dragging
                and self.press_position is not None
                and (
                    current
                    - self.press_position
                ).manhattanLength() > 5
            ):
                self.is_dragging = True

                # 只有完全普通待机、且当前没有在线更新时，
                # 才使用 drag1/drag2“提起来”动画。
                # 其它任何动画/状态都只移动窗口，不改画面。
                if (
                    self.state == "normal"
                    and not self.update_in_progress
                ):
                    debug_log(
                        "drag mode=pickup"
                    )
                    self.cancel_release_bounce()
                    self.stop_walk_timers()
                    self.blink_wait_timer.stop()
                    self.blink_close_timer.stop()
                    self.sleep_wait_timer.stop()

                    self.start_drag_animation(
                        current
                    )
                else:
                    debug_log(
                        f"drag mode=preserve state={self.state} "
                        f"updating={self.update_in_progress}"
                    )
                    self.begin_preserved_drag()

            if self.is_dragging:
                target = (
                    current - self.drag_position
                )

                old_position = QPoint(
                    self.x(),
                    self.y(),
                )

                self.move_to_safe_position(
                    target.x(),
                    target.y(),
                )

                if self.drag_mode == "preserve":
                    new_position = QPoint(
                        self.x(),
                        self.y(),
                    )
                    self.rebase_preserved_motion(
                        new_position - old_position
                    )
                    self.drag_last_window_position = (
                        new_position
                    )

            event.accept()

    def mouseReleaseEvent(self, event):
        if (
            event.button()
            == Qt.MouseButton.LeftButton
        ):
            debug_log(
                f"mouse release state={self.state} "
                f"dragging={self.is_dragging} "
                f"drag_mode={self.drag_mode} "
                f"poke_locked={self.poke_input_locked}"
            )

            if self.is_dragging:
                self.single_click_timer.stop()
                self.ignore_next_left_release = False

                if self.drag_mode == "pickup":
                    self.stop_drag_animation()

                    if not self.play_trigger_action(
                        "drag_release"
                    ):
                        self.start_release_bounce()

                else:
                    self.finish_preserved_drag()

            else:
                # 没有拖动：这时才把鼠标操作解释为“点击”。
                if self.state == "sleeping":
                    # 睡着时这一整个点击只负责叫醒。
                    self.click_woke_from_sleep = True
                    self.wake_up()

                elif self.poke_input_locked:
                    # 特殊连戳动画/冷却期间只禁止“戳”，
                    # 拖动已经在上面的分支始终允许。
                    pass

                elif self.ignore_next_left_release:
                    self.ignore_next_left_release = False

                else:
                    self.handle_single_click()

            self.drag_position = None
            self.press_position = None
            self.drag_last_window_position = None
            self.is_dragging = False
            self.drag_mode = None
            self.click_woke_from_sleep = False

            # 网络如果已经下载完成，现在再尝试安全应用。
            self.try_apply_pending_online_update()

            event.accept()

    def mouseDoubleClickEvent(self, event):
        if (
            event.button()
            == Qt.MouseButton.LeftButton
        ):
            # 果子本体双击没有专属动作。
            # poke lock 仍只影响“戳”，不影响拖动。
            if self.poke_input_locked:
                self.single_click_timer.stop()
                self.ignore_next_left_release = False
                event.accept()
                return

            self.single_click_timer.stop()
            self.ignore_next_left_release = False
            event.accept()


    # ---------- 右键菜单 ----------

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        walk_action = QAction(
            "继续散步"
            if self.walking_paused
            else "暂停散步",
            self,
        )
        walk_action.triggered.connect(
            self.toggle_walking
        )

        movement_menu = QMenu(
            "移动模式",
            menu,
        )

        horizontal_action = QAction(
            "横向散步",
            self,
        )
        horizontal_action.setCheckable(True)
        horizontal_action.setChecked(
            self.settings["movement_mode"]
            == "horizontal"
        )
        horizontal_action.triggered.connect(
            lambda: self.set_movement_mode(
                "horizontal"
            )
        )

        full_screen_action = QAction(
            "满屏幕游走",
            self,
        )
        full_screen_action.setCheckable(True)
        full_screen_action.setChecked(
            self.settings["movement_mode"]
            == "full_screen"
        )
        full_screen_action.triggered.connect(
            lambda: self.set_movement_mode(
                "full_screen"
            )
        )

        movement_menu.addAction(
            horizontal_action
        )
        movement_menu.addAction(
            full_screen_action
        )

        sleep_action = QAction(
            "叫醒果子"
            if self.state == "sleeping"
            else "让果子睡觉",
            self,
        )
        sleep_action.triggered.connect(
            self.toggle_sleep
        )

        speak_action = QAction(
            "让果子说句话",
            self,
        )
        speak_action.triggered.connect(
            self.say_random_message
        )

        reload_action = QAction(
            "重新载入设置、台词和动作",
            self,
        )
        reload_action.triggered.connect(
            self.reload_settings_and_messages
        )

        online_update_action = QAction(
            "检查在线更新",
            self,
        )
        online_update_action.triggered.connect(
            self.check_online_updates
        )

        open_messages_action = QAction(
            "打开台词文件",
            self,
        )
        open_messages_action.triggered.connect(
            self.open_messages_file
        )

        open_settings_action = QAction(
            "打开设置文件",
            self,
        )
        open_settings_action.triggered.connect(
            self.open_settings_file
        )

        hide_action = QAction(
            "隐藏果子",
            self,
        )
        hide_action.triggered.connect(
            self.toggle_visibility
        )

        exit_action = QAction(
            "退出果子",
            self,
        )
        exit_action.triggered.connect(
            self.quit_pet
        )

        menu.addAction(walk_action)
        menu.addMenu(movement_menu)
        menu.addAction(sleep_action)
        menu.addAction(speak_action)
        self.add_actions_to_menu(menu)
        menu.addSeparator()
        menu.addAction(reload_action)
        menu.addAction(online_update_action)
        menu.addAction(open_messages_action)
        menu.addAction(open_settings_action)
        menu.addSeparator()
        menu.addAction(hide_action)
        menu.addAction(exit_action)

        menu.exec(event.globalPos())

    # ---------- 退出 ----------

    def quit_pet(self):
        self.save_position()
        self.speech_bubble.hide()
        self.drag_animation_timer.stop()
        self.bounce_timer.stop()
        self.custom_action_timer.stop()
        self.random_action_timer.stop()
        self.poke_cooldown_timer.stop()
        self.stop_action_sound()
        self.global_mouse_timer.stop()
        self.topmost_timer.stop()
        self.sleep_duration_timer.stop()
        self.sleep_zzz.stop_animation()
        self.tray_icon.hide()
        QApplication.quit()

    def closeEvent(self, event):
        self.save_position()
        self.speech_bubble.hide()
        self.drag_animation_timer.stop()
        self.bounce_timer.stop()
        self.custom_action_timer.stop()
        self.random_action_timer.stop()
        self.pending_update_timer.stop()
        self.stop_action_sound()
        self.global_mouse_timer.stop()
        self.topmost_timer.stop()
        self.sleep_duration_timer.stop()
        self.sleep_zzz.stop_animation()
        event.accept()


def main():
    debug_log("=== process start ===")
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    pet = DesktopPet()
    pet.show()
    pet.restore_position()

    # 开机先同时显示 happy 与当前时间段问候，
    # 等它们结束后再自动检查线上更新。
    QTimer.singleShot(
        150,
        pet.play_startup_happy_greeting,
    )

    app.exec()


if __name__ == "__main__":
    main()
