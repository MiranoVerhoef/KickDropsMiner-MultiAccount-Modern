"""Main application UI for KickDropsMiner"""
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from urllib.parse import urlparse
import urllib.request
from io import BytesIO
import customtkinter as ctk
from PIL import Image
try:
    import sv_ttk
except Exception:
    sv_ttk = None

from core import (
    Config,
    StreamWorker,
    CookieManager,
    make_chrome_driver,
    kick_is_live_by_api,
    fetch_drops_campaigns_and_progress,
    fetch_drops_progress,
    claim_available_drops,
    fetch_live_streamers_by_category,
    is_campaign_expired
)
from core.browser import accept_kick_cookies
from utils.helpers import (
    APP_DIR,
    domain_from_url,
    cookie_file_for_account,
    cookie_file_for_domain,
    debug_print,
    set_debug_config
)
from utils.translations import translate, TRANSLATIONS


class App(ctk.CTk):
    MIN_WIDTH = 1160
    MIN_HEIGHT = 820

    def __init__(self):
        super().__init__()
        self.title("Kick Drop Miner")
        self._enforce_screen_size()
        self.geometry(f"{self.MIN_WIDTH}x{self.MIN_HEIGHT}")
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self._set_window_icon()

        self.config_data = Config()
        # Set global debug config reference
        set_debug_config(self.config_data)
        self.workers = {}
        self._interactive_driver = None  # Chrome pour capture de cookies
        self.queue_running = False
        self.queue_current_idx = None
        self.current_view = "active"
        self._refresh_after_id = None
        self._last_worker_ui_update = {}
        self.log_entries = []
        self.log_records = []
        self._last_drop_progress_log = {}
        self._log_dirty = True
        self._active_dirty = True
        self._ignored_finishes = set()

        # Helper traduction
        def _t(key: str, **kwargs):
            return translate(self.config_data.language, key).format(**kwargs)

        self.t = _t

        # Appearance / theme
        ctk.set_appearance_mode("Dark" if self.config_data.dark_mode else "Light")
        ctk.set_default_color_theme("dark-blue")
        self.ui = self._ui_tokens()
        self._apply_sun_valley_theme()
        self.configure(fg_color=self.ui["app_bg"])

        # Layout principal: 2 colonnes (sidebar gauche, contenu droit)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = ctk.CTkFrame(
            self,
            width=238,
            corner_radius=0,
            fg_color=self.ui["sidebar_bg"],
        )
        self.sidebar.grid(row=0, column=0, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_columnconfigure(0, weight=1)
        # Leave free space at the bottom to avoid cutting off controls
        # (uses a high empty row to serve as expandable space)
        self.sidebar.grid_rowconfigure(99, weight=1)

        self._build_sidebar()

        # Contenu principal
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=18, pady=18)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self._view_frames = {}

        self.show_active_drops_view()

        # Status bar
        self.status_var = tk.StringVar(value=self.t("status_ready"))
        self.status = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            anchor="w",
            height=34,
            corner_radius=8,
            fg_color=self.ui["panel_bg"],
            text_color=self.ui["muted_text"],
            padx=12,
        )
        self.status.grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14)
        )

        self.refresh_list()
        
        # Start offline retry monitor
        self._start_offline_retry_monitor()
        
        # Auto-start queue if enabled
        if self.config_data.auto_start and self.config_data.items:
            # Delay slightly to let UI finish loading
            self.after(1000, self._auto_start_queue)
        
        # Properly close all browsers when closing the app
        try:
            self.protocol("WM_DELETE_WINDOW", self.on_close)
        except Exception:
            pass

    def _enforce_screen_size(self):
        try:
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
        except Exception:
            return
        if screen_width < self.MIN_WIDTH or screen_height < self.MIN_HEIGHT:
            messagebox.showerror(
                "Screen too small",
                (
                    f"Kick Drop Miner needs at least {self.MIN_WIDTH}x{self.MIN_HEIGHT} pixels. "
                    f"Current desktop is {screen_width}x{screen_height}."
                ),
            )
            self.destroy()
            raise SystemExit(1)

    def _set_window_icon(self):
        try:
            logo_path = os.path.join(APP_DIR, "assets", "logo.png")
            self._window_icon = tk.PhotoImage(file=logo_path)
            self.iconphoto(True, self._window_icon)
        except Exception:
            try:
                ico_path = os.path.join(APP_DIR, "assets", "app.ico")
                if os.path.exists(ico_path):
                    self.iconbitmap(ico_path)
            except Exception:
                pass

    def _ui_tokens(self):
        dark = bool(self.config_data.dark_mode)
        if dark:
            return {
                "app_bg": "#0a0f16",
                "sidebar_bg": "#101823",
                "panel_bg": "#151f2c",
                "panel_alt": "#1d2a3a",
                "border": "#2c3b4f",
                "text": "#f4f8fc",
                "muted_text": "#9baabd",
                "accent": "#19b86a",
                "accent_hover": "#139957",
                "blue": "#2476f2",
                "blue_hover": "#1b63cf",
                "danger": "#ef4444",
                "danger_hover": "#dc2626",
            }
        return {
            "app_bg": "#eef3f8",
            "sidebar_bg": "#ffffff",
            "panel_bg": "#ffffff",
            "panel_alt": "#f4f7fb",
            "border": "#d7e1ee",
            "text": "#0f172a",
            "muted_text": "#64748b",
            "accent": "#16a75c",
            "accent_hover": "#0f8d4b",
            "blue": "#176fe8",
            "blue_hover": "#125cc2",
            "danger": "#e03131",
            "danger_hover": "#c92a2a",
        }

    def _apply_sun_valley_theme(self):
        if not sv_ttk:
            return
        try:
            sv_ttk.set_theme("dark" if self.config_data.dark_mode else "light")
        except Exception as e:
            debug_print(f"DEBUG: Could not apply Sun Valley theme: {e}")

    def _sidebar_button(
        self,
        parent,
        text,
        command=None,
        row=0,
        variant="primary",
        bind_remove=False,
    ):
        colors = {
            "selected": (self.ui["blue"], self.ui["blue_hover"], "white", self.ui["blue"]),
            "primary": (self.ui["blue"], self.ui["blue_hover"], "white", self.ui["blue"]),
            "accent": (self.ui["accent"], self.ui["accent_hover"], "white", self.ui["accent"]),
            "danger": (self.ui["danger"], self.ui["danger_hover"], "white", self.ui["danger"]),
            "warning": ("#f59e0b", "#d97706", "white", "#f59e0b"),
            "neutral": ("transparent", self.ui["panel_alt"], self.ui["text"], self.ui["border"]),
        }
        fg, hover, text_color, border_color = colors.get(variant, colors["primary"])
        button = ctk.CTkButton(
            parent,
            text=text,
            command=command,
            width=196,
            height=38,
            corner_radius=8,
            fg_color=fg,
            hover_color=hover,
            text_color=text_color,
            border_width=1 if variant == "selected" else 0,
            border_color=border_color,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        )
        if bind_remove:
            button.bind("<Button-1>", self.on_remove_button_click)
        button.grid(row=row, column=0, sticky="ew", padx=14, pady=4)
        return button

    def _action_button(self, parent, text, command, column, variant="neutral"):
        colors = {
            "primary": (self.ui["accent"], self.ui["accent_hover"], "white"),
            "secondary": (self.ui["blue"], self.ui["blue_hover"], "white"),
            "danger": ("#fee2e2" if ctk.get_appearance_mode() != "Dark" else "#3a1d24", "#fecaca" if ctk.get_appearance_mode() != "Dark" else "#4a242d", self.ui["danger"]),
            "neutral": (self.ui["panel_alt"], self.ui["border"], self.ui["text"]),
        }
        fg, hover, text_color = colors.get(variant, colors["neutral"])
        button = ctk.CTkButton(
            parent,
            text=text,
            command=command,
            height=42,
            corner_radius=10,
            fg_color=fg,
            hover_color=hover,
            text_color=text_color,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        button.grid(row=0, column=column, sticky="ew", padx=10, pady=16)
        return button

    def _available_languages(self):
        codes = list(TRANSLATIONS.keys())
        ordered = []
        for preferred in ("fr", "en"):
            if preferred in codes:
                ordered.append(preferred)
        for code in sorted(c for c in codes if c not in ordered):
            ordered.append(code)
        return ordered

    def _language_label(self, lang_code):
        label_key = f"language_{lang_code}"
        label = translate(self.config_data.language, label_key)
        if label == label_key:
            label = translate(lang_code, label_key)
        if label == label_key:
            label = lang_code
        return label

    def _get_language_choices(self):
        codes = self._available_languages()
        if self.config_data.language not in codes and codes:
            self.config_data.language = codes[0]
            self.config_data.save()
        labels = {code: self._language_label(code) for code in codes}
        self.lang_display_to_code = {label: code for code, label in labels.items()}
        return [labels[code] for code in codes]

    # ----------- UI construction -----------
    def _build_sidebar(self):
        header = ctk.CTkFrame(self.sidebar, corner_radius=0, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(18, 14), sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        # Logo (assets/logo.png) + title
        try:
            logo_path = os.path.join(APP_DIR, "assets", "logo.png")
            img = Image.open(logo_path)
            self._logo_img = ctk.CTkImage(
                light_image=img, dark_image=img, size=(30, 30)
            )
            logo_lbl = ctk.CTkLabel(header, image=self._logo_img, text="")
            logo_lbl.grid(row=0, column=0, padx=(0, 8), pady=2, sticky="w")
        except Exception:
            pass

        title = ctk.CTkLabel(
            header,
            text="Kick Drop Miner",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=self.ui["text"],
        )
        title.grid(row=0, column=1, padx=0, pady=(0, 0), sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Drops automation",
            font=ctk.CTkFont(size=11),
            text_color=self.ui["muted_text"],
        )
        subtitle.grid(row=1, column=1, padx=0, pady=(0, 2), sticky="w")

        status_card = ctk.CTkFrame(
            self.sidebar,
            corner_radius=0,
            fg_color="transparent",
        )
        status_card.grid(row=1, column=0, sticky="ew", padx=18, pady=(2, 8))
        status_card.grid_columnconfigure(1, weight=1)

        self.sidebar_status_dot = ctk.CTkLabel(
            status_card,
            text="●",
            text_color=self.ui["accent"],
            font=ctk.CTkFont(size=13),
        )
        self.sidebar_status_dot.grid(row=0, column=0, padx=(0, 7), pady=(0, 0), sticky="w")

        self.sidebar_status_label = ctk.CTkLabel(
            status_card,
            text="Running" if self.queue_running else "Ready",
            text_color=self.ui["text"],
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.sidebar_status_label.grid(row=0, column=1, padx=(0, 0), pady=(0, 0), sticky="w")

        queued = len([item for item in self.config_data.items if not item.get("finished") and not item.get("claimed")])
        self.sidebar_queue_label = ctk.CTkLabel(
            status_card,
            text=f"{queued} in queue",
            text_color=self.ui["muted_text"],
            font=ctk.CTkFont(size=11),
        )
        self.sidebar_queue_label.grid(row=1, column=1, padx=(0, 0), pady=(0, 0), sticky="w")

        self._sidebar_button(
            self.sidebar,
            "Main Menu",
            command=self.show_active_drops_view,
            row=2,
            variant="accent",
        )

        queue_spacer = ctk.CTkFrame(self.sidebar, height=38, fg_color="transparent")
        queue_spacer.grid(row=3, column=0, sticky="ew", padx=14, pady=4)
        queue_spacer.grid_propagate(False)

        self.start_queue_button = self._sidebar_button(
            self.sidebar,
            self.t("btn_start_queue"),
            command=self.start_all_in_order,
            row=4,
            variant="primary",
        )

        self.skip_creator_button = self._sidebar_button(
            self.sidebar,
            "Skip creator",
            command=self.skip_creator,
            row=5,
            variant="warning",
        )

        action_spacer = ctk.CTkFrame(self.sidebar, height=38, fg_color="transparent")
        action_spacer.grid(row=6, column=0, sticky="ew", padx=14, pady=4)
        action_spacer.grid_propagate(False)

        self._sidebar_button(
            self.sidebar,
            "Browse Drops",
            command=self.show_browse_drops_view,
            row=7,
            variant="neutral",
        )

        self._sidebar_button(
            self.sidebar,
            self.t("btn_add"),
            command=self.add_link,
            row=8,
            variant="neutral",
        )

        self._sidebar_button(
            self.sidebar,
            self.t("btn_remove"),
            row=9,
            variant="neutral",
            bind_remove=True,
        )

        self._sidebar_button(
            self.sidebar,
            "Logging",
            command=self.show_logging_view,
            row=10,
            variant="neutral",
        )

        self._sidebar_button(
            self.sidebar,
            "Settings",
            command=self.show_settings_view,
            row=11,
            variant="neutral",
        )
        self._update_sidebar_status()

        # Initialize toggle variables (used in settings window)
        self.mute_var = tk.BooleanVar(value=bool(self.config_data.mute))
        self.hide_player_var = tk.BooleanVar(value=bool(self.config_data.hide_player))
        self.mini_player_var = tk.BooleanVar(value=bool(self.config_data.mini_player))
        self.force_160p_var = tk.BooleanVar(value=bool(self.config_data.force_160p))
        self.auto_start_var = tk.BooleanVar(value=bool(self.config_data.auto_start))
        self.theme_var = tk.StringVar(
            value=self.t("theme_dark")
            if self.config_data.dark_mode
            else self.t("theme_light")
        )
        language_choices = self._get_language_choices()
        current_label = self._language_label(self.config_data.language)
        if current_label not in language_choices and language_choices:
            current_label = language_choices[0]
        self.lang_var = tk.StringVar(value=current_label)

    def _build_content(self):
        self.content.grid_rowconfigure(1, weight=1)
        self.content.grid_rowconfigure(2, weight=0)
        self.content.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(22, 18))
        header.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            header,
            text="Main Menu",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=self.ui["text"],
        )
        title.grid(row=0, column=0, sticky="w")

        subtitle = ctk.CTkLabel(
            header,
            text="Track rewards, streamers, progress, and claim status.",
            font=ctk.CTkFont(size=13),
            text_color=self.ui["muted_text"],
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._schedule_refresh_list())
        search = ctk.CTkEntry(
            header,
            textvariable=self.search_var,
            placeholder_text="Search drops...",
            width=260,
            height=42,
            corner_radius=10,
            fg_color=self.ui["panel_bg"],
            border_color=self.ui["border"],
            text_color=self.ui["text"],
            placeholder_text_color=self.ui["muted_text"],
        )
        search.grid(row=0, column=1, rowspan=2, padx=(16, 8), sticky="e")

        refresh_btn = ctk.CTkButton(
            header,
            text="Refresh",
            command=self.refresh_list,
            width=96,
            height=42,
            corner_radius=10,
            fg_color=self.ui["panel_bg"],
            hover_color=self.ui["panel_alt"],
            border_width=1,
            border_color=self.ui["border"],
            text_color=self.ui["text"],
        )
        refresh_btn.grid(row=0, column=2, rowspan=2, padx=(4, 0), sticky="e")

        table_card = ctk.CTkFrame(
            self.content,
            corner_radius=14,
            fg_color=self.ui["panel_bg"],
            border_width=2,
            border_color=self.ui["blue"],
        )
        table_card.grid(row=1, column=0, sticky="nsew")
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        style.theme_use("clam")
        if sv_ttk:
            try:
                style.theme_use("sun-valley-dark" if self.config_data.dark_mode else "sun-valley-light")
            except Exception:
                pass
        tree_bg = self.ui["panel_bg"]
        tree_alt = self.ui["panel_alt"]
        heading_bg = self.ui["panel_alt"]
        style.configure("TButton", padding=(14, 10), font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", padding=(14, 10), font=("Segoe UI", 10, "bold"))
        style.configure(
            "Danger.TButton",
            padding=(14, 10),
            font=("Segoe UI", 10, "bold"),
            foreground=self.ui["danger"],
        )
        style.configure(
            "Treeview",
            background=tree_bg,
            fieldbackground=tree_bg,
            foreground=self.ui["text"],
            rowheight=38,
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=heading_bg,
            foreground=self.ui["muted_text"],
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padding=(12, 12),
        )
        style.map(
            "Treeview",
            background=[("selected", self.ui["blue"])],
            foreground=[("selected", "white")],
        )

        self.tree = ttk.Treeview(
            table_card,
            columns=("url", "minutes", "elapsed"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("url", text="Drop")
        self.tree.heading("minutes", text="Current streamer")
        self.tree.heading("elapsed", text="Progress")
        self.tree.column("url", width=320, minwidth=160, anchor="w", stretch=True)
        self.tree.column("minutes", width=220, minwidth=150, anchor="center", stretch=True)
        self.tree.column("elapsed", width=360, minwidth=260, anchor="center", stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.tree.bind("<Configure>", self._resize_active_tree_columns)

        self.tree_scrollbar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.tree_scrollbar.set)
        self.tree_scrollbar.grid(row=0, column=1, sticky="ns", pady=1)

        self.empty_state = ctk.CTkFrame(table_card, fg_color=self.ui["panel_bg"])
        self.empty_state.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.empty_state.grid_columnconfigure(0, weight=1)
        self.empty_state.grid_rowconfigure(0, weight=1)

        empty_inner = ctk.CTkFrame(self.empty_state, fg_color="transparent")
        empty_inner.grid(row=0, column=0)
        empty_icon = ctk.CTkLabel(
            empty_inner,
            text="↗",
            width=84,
            height=78,
            corner_radius=36,
            fg_color=self.ui["panel_alt"],
            text_color=self.ui["muted_text"],
            font=ctk.CTkFont(size=28, weight="bold"),
        )
        empty_icon.grid(row=0, column=0, pady=(0, 18))
        empty_title = ctk.CTkLabel(
            empty_inner,
            text="No active drops yet",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=self.ui["text"],
        )
        empty_title.grid(row=1, column=0)
        empty_copy = ctk.CTkLabel(
            empty_inner,
            text="Add a campaign or stream link to get started.",
            font=ctk.CTkFont(size=13),
            text_color=self.ui["muted_text"],
        )
        empty_copy.grid(row=2, column=0, pady=(6, 16))
        empty_add = ctk.CTkButton(
            empty_inner,
            text="Browse Drops",
            command=self.show_browse_drops_view,
            width=132,
            height=42,
            corner_radius=10,
            fg_color=self.ui["accent"],
            hover_color=self.ui["accent_hover"],
            text_color="white",
        )
        empty_add.grid(row=3, column=0)

        empty_link = ctk.CTkButton(
            empty_inner,
            text="Add Stream Link",
            command=self.add_link,
            width=132,
            height=38,
            corner_radius=10,
            fg_color="transparent",
            hover_color=self.ui["panel_alt"],
            border_width=1,
            border_color=self.ui["border"],
            text_color=self.ui["text"],
        )
        empty_link.grid(row=4, column=0, pady=(8, 0))

        try:
            self.tree.tag_configure("odd", background=tree_alt)
            self.tree.tag_configure("even", background=tree_bg)
            self.tree.tag_configure("redo", background="#fff7df" if ctk.get_appearance_mode() != "Dark" else "#2f2612")
            self.tree.tag_configure("paused", background="#fff0f0" if ctk.get_appearance_mode() != "Dark" else "#2d181b")
            self.tree.tag_configure("finished", background="#eaf8ef" if ctk.get_appearance_mode() != "Dark" else "#143022")
        except Exception:
            pass

        self.tree.bind("<Double-Button-1>", self.on_tree_double_click)

    def _resize_active_tree_columns(self, event=None):
        if not hasattr(self, "tree") or not self._widget_exists(self.tree):
            return
        width = (event.width if event else self.tree.winfo_width()) - 24
        if width <= 0:
            return
        progress_width = max(300, int(width * 0.42))
        streamer_width = max(170, int(width * 0.23))
        drop_width = max(180, width - progress_width - streamer_width)
        try:
            self.tree.column("url", width=drop_width)
            self.tree.column("minutes", width=streamer_width)
            self.tree.column("elapsed", width=progress_width)
        except Exception:
            pass

    # ----------- Theme -----------
    def _clear_content(self):
        for widget in self.content.winfo_children():
            widget.grid_remove()

    def _widget_exists(self, widget):
        try:
            return bool(widget.winfo_exists())
        except Exception:
            return False

    def _destroy_view_cache(self, name=None):
        if name is None:
            frames = list(getattr(self, "_view_frames", {}).values())
            self._view_frames = {}
            self._active_dirty = True
            self._log_dirty = True
        else:
            frame = self._view_frames.pop(name, None)
            frames = [frame] if frame is not None else []
            if name == "active":
                self._active_dirty = True
            if name == "logging":
                self._log_dirty = True
        for frame in frames:
            try:
                frame.destroy()
            except Exception:
                pass

    def _show_view(self, name, builder, refresh=None):
        self.current_view = name
        for frame in self._view_frames.values():
            try:
                frame.grid_remove()
            except Exception:
                pass
        frame = self._view_frames.get(name)
        if not self._widget_exists(frame):
            frame = ctk.CTkFrame(self.content, corner_radius=0, fg_color="transparent")
            frame.grid_rowconfigure(0, weight=0)
            frame.grid_rowconfigure(1, weight=1)
            frame.grid_rowconfigure(2, weight=0)
            frame.grid_columnconfigure(0, weight=1)
            self._view_frames[name] = frame
            root_content = self.content
            self.content = frame
            try:
                builder()
            finally:
                self.content = root_content
        frame.grid(row=0, column=0, sticky="nsew")
        if refresh:
            refresh()

    def show_active_drops_view(self):
        self._show_view("active", self._build_content)
        if self._active_dirty:
            self.refresh_list()

    def show_browse_drops_view(self):
        self._show_view("drops", self._build_drops_content)

    def show_logging_view(self):
        self._show_view("logging", self._build_logging_content)
        if self._log_dirty:
            self._refresh_logging_view()

    def show_settings_view(self):
        self._show_view("settings", self._build_settings_content)

    def _settings_section(self, parent, title, row, column=0, columnspan=1):
        section = ctk.CTkFrame(
            parent,
            corner_radius=12,
            fg_color=self.ui["panel_bg"],
            border_width=1,
            border_color=self.ui["border"],
        )
        section.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=6, pady=6)
        section.grid_columnconfigure(0, weight=1)
        label = ctk.CTkLabel(
            section,
            text=title,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=self.ui["text"],
        )
        label.grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))
        return section

    def _make_fast_scroll_frame(self, parent, row, column):
        outer = ctk.CTkFrame(parent, fg_color="transparent")
        outer.grid(row=row, column=column, sticky="nsew")
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(
            outer,
            highlightthickness=0,
            borderwidth=0,
            background=self.ui["app_bg"],
        )
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        body = ctk.CTkFrame(canvas, fg_color="transparent")
        body.grid_columnconfigure(0, weight=1)
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def configure_body(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def configure_canvas(event):
            canvas.itemconfigure(window_id, width=event.width)

        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def bind_wheel(_event=None):
            canvas.bind_all("<MouseWheel>", on_mousewheel)

        def unbind_wheel(_event=None):
            canvas.unbind_all("<MouseWheel>")

        body.bind("<Configure>", configure_body)
        canvas.bind("<Configure>", configure_canvas)
        outer.bind("<Enter>", bind_wheel)
        outer.bind("<Leave>", unbind_wheel)
        body._scroll_canvas = canvas
        return body

    def _build_settings_content(self):
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(22, 18))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Settings",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Player behavior, appearance, browser paths, and Kick accounts.",
            font=ctk.CTkFont(size=13),
            text_color=self.ui["muted_text"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        body = ctk.CTkFrame(self.content, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure((0, 1), weight=1, uniform="settings")
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure((1, 2), weight=1)

        accounts = self._settings_section(body, "Accounts", 0, 0, 2)
        self._build_accounts_settings(accounts)

        player = self._settings_section(body, "Player", 1, 0)
        ctk.CTkSwitch(player, text=self.t("switch_mute"), command=self.on_toggle_mute, variable=self.mute_var, text_color=self.ui["text"]).grid(row=1, column=0, sticky="w", padx=18, pady=6)
        ctk.CTkSwitch(player, text=self.t("switch_hide"), command=self.on_toggle_hide, variable=self.hide_player_var, text_color=self.ui["text"]).grid(row=2, column=0, sticky="w", padx=18, pady=6)
        ctk.CTkSwitch(player, text=self.t("switch_mini"), command=self.on_toggle_mini, variable=self.mini_player_var, text_color=self.ui["text"]).grid(row=3, column=0, sticky="w", padx=18, pady=6)
        ctk.CTkSwitch(player, text=self.t("switch_force_160p"), command=self.on_toggle_force_160p, variable=self.force_160p_var, text_color=self.ui["text"]).grid(row=4, column=0, sticky="w", padx=18, pady=(6, 16))

        queue = self._settings_section(body, "Queue", 1, 1)
        ctk.CTkSwitch(queue, text="Auto-start queue", command=self.on_toggle_auto_start, variable=self.auto_start_var, text_color=self.ui["text"]).grid(row=1, column=0, sticky="w", padx=18, pady=(6, 16))

        appearance = self._settings_section(body, "Appearance", 2, 0)
        ctk.CTkLabel(appearance, text="Theme", text_color=self.ui["muted_text"]).grid(row=1, column=0, sticky="w", padx=18, pady=(4, 4))
        ctk.CTkOptionMenu(
            appearance,
            values=[self.t("theme_dark"), self.t("theme_light")],
            command=self.change_theme,
            variable=self.theme_var,
            width=220,
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(0, 12))
        ctk.CTkLabel(appearance, text="Language", text_color=self.ui["muted_text"]).grid(row=3, column=0, sticky="w", padx=18, pady=(4, 4))
        ctk.CTkOptionMenu(
            appearance,
            values=self._get_language_choices(),
            command=self.change_language,
            variable=self.lang_var,
            width=220,
        ).grid(row=4, column=0, sticky="w", padx=18, pady=(0, 16))

        browser = self._settings_section(body, "Browser", 2, 1)
        ctk.CTkButton(browser, text=self.t("btn_chromedriver"), command=self.choose_chromedriver, width=220).grid(row=1, column=0, sticky="w", padx=18, pady=6)
        ctk.CTkButton(browser, text=self.t("btn_extension"), command=self.choose_extension, width=220).grid(row=2, column=0, sticky="w", padx=18, pady=(6, 16))

    def _build_logging_content(self):
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.grid_rowconfigure(1, weight=0)
        self.content.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(22, 18))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Logging",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Runtime events for queue, stream switches, and drop progress.",
            font=ctk.CTkFont(size=13),
            text_color=self.ui["muted_text"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ctk.CTkButton(
            header,
            text="Clear",
            command=self._clear_log_entries,
            width=90,
            height=38,
            corner_radius=10,
            fg_color=self.ui["panel_bg"],
            hover_color=self.ui["panel_alt"],
            border_width=1,
            border_color=self.ui["border"],
            text_color=self.ui["text"],
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        filters = ctk.CTkFrame(self.content, fg_color="transparent")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        filters.grid_columnconfigure(2, weight=1)

        drop_values, creator_values = self._log_filter_values()
        self.log_drop_filter_var = tk.StringVar(value="All drops")
        self.log_creator_filter_var = tk.StringVar(value="All creators")

        ctk.CTkLabel(filters, text="Drop", text_color=self.ui["muted_text"]).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.log_drop_filter = ctk.CTkOptionMenu(
            filters,
            values=drop_values,
            variable=self.log_drop_filter_var,
            command=lambda _value: self._on_log_filter_changed(),
            width=260,
        )
        self.log_drop_filter.grid(row=0, column=1, sticky="w", padx=(0, 14))

        ctk.CTkLabel(filters, text="Creator", text_color=self.ui["muted_text"]).grid(
            row=0, column=2, sticky="e", padx=(0, 8)
        )
        self.log_creator_filter = ctk.CTkOptionMenu(
            filters,
            values=creator_values,
            variable=self.log_creator_filter_var,
            command=lambda _value: self._on_log_filter_changed(),
            width=220,
        )
        self.log_creator_filter.grid(row=0, column=3, sticky="e")

        card = ctk.CTkFrame(
            self.content,
            corner_radius=14,
            fg_color=self.ui["panel_bg"],
            border_width=1,
            border_color=self.ui["border"],
        )
        card.grid(row=2, column=0, sticky="nsew")
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            card,
            wrap="word",
            relief="flat",
            borderwidth=0,
            padx=14,
            pady=12,
            font=("Segoe UI", 10),
            background=self.ui["panel_bg"],
            foreground=self.ui["text"],
            insertbackground=self.ui["text"],
            selectbackground=self.ui["blue"],
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        scrollbar = ttk.Scrollbar(card, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=1)

    def _refresh_logging_view(self):
        if not hasattr(self, "log_text") or not self._widget_exists(self.log_text):
            return
        self._refresh_logging_filters()
        records = self._filtered_log_records()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        if records:
            self.log_text.insert("end", "\n".join(record["line"] for record in records))
        else:
            self.log_text.insert("end", "No log entries yet.")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        self._log_dirty = False

    def _log_filter_values(self):
        drops = sorted({
            record.get("drop")
            for record in self.log_records
            if record.get("drop")
        })
        creators = sorted({
            record.get("creator")
            for record in self.log_records
            if record.get("creator")
        })
        return ["All drops"] + drops, ["All creators"] + creators

    def _refresh_logging_filters(self):
        if not hasattr(self, "log_drop_filter") or not self._widget_exists(self.log_drop_filter):
            return
        drop_values, creator_values = self._log_filter_values()
        current_drop = self.log_drop_filter_var.get()
        current_creator = self.log_creator_filter_var.get()
        if current_drop not in drop_values:
            self.log_drop_filter_var.set("All drops")
        if current_creator not in creator_values:
            self.log_creator_filter_var.set("All creators")
        self.log_drop_filter.configure(values=drop_values)
        self.log_creator_filter.configure(values=creator_values)

    def _filtered_log_records(self):
        selected_drop = "All drops"
        selected_creator = "All creators"
        if hasattr(self, "log_drop_filter_var"):
            selected_drop = self.log_drop_filter_var.get()
        if hasattr(self, "log_creator_filter_var"):
            selected_creator = self.log_creator_filter_var.get()

        records = []
        for record in self.log_records:
            if selected_drop != "All drops" and record.get("drop") != selected_drop:
                continue
            if selected_creator != "All creators" and record.get("creator") != selected_creator:
                continue
            records.append(record)
        return records

    def _on_log_filter_changed(self):
        self._log_dirty = True
        self._refresh_logging_view()

    def _clear_log_entries(self):
        self.log_entries.clear()
        self.log_records.clear()
        self._last_drop_progress_log.clear()
        self._log_dirty = True
        self._refresh_logging_view()

    def _infer_log_context(self):
        idx = self.queue_current_idx
        if idx is None and self.workers:
            try:
                idx = next(iter(self.workers.keys()))
            except Exception:
                idx = None
        if idx is None or idx < 0 or idx >= len(self.config_data.items):
            return None, None
        item = self.config_data.items[idx]
        return self._drop_title_for_item(item), self._streamer_name_from_url(item.get("url", ""))

    def _add_log_entry(self, message, drop=None, creator=None):
        timestamp = time.strftime("%H:%M:%S")
        if drop is None or creator is None:
            inferred_drop, inferred_creator = self._infer_log_context()
            drop = drop or inferred_drop
            creator = creator or inferred_creator
        line = f"[{timestamp}] {message}"
        record = {
            "time": timestamp,
            "line": line,
            "message": message,
            "drop": drop,
            "creator": creator,
        }
        self.log_entries.append(line)
        self.log_records.append(record)
        if len(self.log_records) > 5000:
            self.log_records = self.log_records[-5000:]
            self.log_entries = [record["line"] for record in self.log_records]
        self._log_dirty = True
        if self.current_view == "logging":
            self._refresh_logging_view()

    def _add_item_log_entry(self, item, message, creator_url=None):
        self._add_log_entry(
            message,
            drop=self._drop_title_for_item(item),
            creator=self._streamer_name_from_url(creator_url or item.get("url", "")),
        )

    def _build_accounts_settings(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        accounts = list(self.config_data.accounts)

        list_frame = ctk.CTkFrame(parent, fg_color="transparent")
        list_frame.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 10))
        list_frame.grid_columnconfigure(0, weight=1)
        if not accounts:
            empty = ctk.CTkFrame(list_frame, fg_color=self.ui["panel_alt"], corner_radius=10)
            empty.grid(row=0, column=0, sticky="ew", pady=4)
            ctk.CTkLabel(
                empty,
                text="No Kick accounts connected. Add an account before running drops.",
                text_color=self.ui["muted_text"],
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)

        for row, account in enumerate(accounts):
            item = ctk.CTkFrame(list_frame, fg_color=self.ui["panel_alt"], corner_radius=10)
            item.grid(row=row, column=0, sticky="ew", pady=4)
            item.grid_columnconfigure(0, weight=1)
            label = account["name"]
            ctk.CTkLabel(
                item,
                text=label,
                text_color=self.ui["text"],
                font=ctk.CTkFont(size=13, weight="bold"),
            ).grid(row=0, column=0, sticky="w", padx=12, pady=10)
            valid = self._account_cookie_valid(account["id"])
            status_text = "✓ Valid" if valid else "Not signed in"
            status_color = self.ui["accent"] if valid else self.ui["muted_text"]
            ctk.CTkLabel(
                item,
                text=status_text,
                text_color=status_color,
                font=ctk.CTkFont(size=12, weight="bold"),
            ).grid(row=0, column=1, padx=(8, 10), pady=10)
            ctk.CTkButton(
                item,
                text="Login",
                width=82,
                height=30,
                state="disabled" if valid else "normal",
                command=lambda a=account: self.login_account(a["id"]),
            ).grid(row=0, column=2, padx=4, pady=6)
            ctk.CTkButton(
                item,
                text="Remove",
                width=82,
                height=30,
                fg_color=self.ui["danger"],
                hover_color=self.ui["danger_hover"],
                command=lambda a=account: self.remove_account(a["id"]),
            ).grid(row=0, column=3, padx=(4, 8), pady=6)

        ctk.CTkButton(
            parent,
            text="Add Account",
            width=160,
            fg_color=self.ui["blue"],
            hover_color=self.ui["blue_hover"],
            command=self.add_account,
        ).grid(row=2, column=0, sticky="w", padx=18, pady=(2, 16))

    def _account_cookie_valid(self, account_id):
        path = cookie_file_for_account("kick.com", account_id)
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
        except Exception:
            return False
        if not cookies:
            return False
        now = int(time.time())
        unexpired = 0
        for cookie in cookies:
            expiry = cookie.get("expiry")
            if expiry is None:
                unexpired += 1
                continue
            try:
                if int(expiry) > now:
                    unexpired += 1
            except Exception:
                continue
        return unexpired > 0

    def add_account(self):
        account_id = self.config_data.add_account("New account")
        self._destroy_view_cache("settings")
        self.show_settings_view()
        result = self.obtain_cookies_interactively("https://kick.com", "kick.com", account_id)
        if not isinstance(result, dict) or not result.get("saved"):
            self.config_data.remove_account(account_id)
            self._destroy_view_cache("settings")
            self.show_settings_view()
            return
        detected_name = result.get("account_name") if isinstance(result, dict) else None
        if not detected_name:
            if not messagebox.askyesno(
                "Login not confirmed",
                "Kick did not return a logged-in username. Did you fully sign in to Kick in the browser window?",
            ):
                self.config_data.remove_account(account_id)
                self._destroy_view_cache("settings")
                self.show_settings_view()
                messagebox.showerror("Login failed", "No Kick account was added because login could not be confirmed.")
                return
            name = self._ask_account_name()
            if not name:
                self.config_data.remove_account(account_id)
                self._destroy_view_cache("settings")
                self.show_settings_view()
                return
            self.config_data.update_account_name(account_id, name)
        self._destroy_view_cache("settings")
        self.show_settings_view()

    def remove_account(self, account_id):
        if not messagebox.askyesno("Remove Account", f"Remove {self._account_name(account_id)}?"):
            return
        self.config_data.remove_account(account_id)
        self._destroy_view_cache("settings")
        self.show_settings_view()

    def login_account(self, account_id):
        self.obtain_cookies_interactively("https://kick.com", "kick.com", account_id)

    def _show_modal(self, dialog):
        dialog.transient(self)
        dialog.grab_set()
        dialog.focus_force()
        self.wait_window(dialog)

    def _ask_account_name(self):
        result = {"value": None}
        dialog = ctk.CTkToplevel(self)
        dialog.title("Account name")
        dialog.geometry("360x190")
        dialog.resizable(False, False)
        dialog.configure(fg_color=self.ui["app_bg"])
        dialog.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            dialog,
            text="Name this Kick account",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(22, 4))
        ctk.CTkLabel(
            dialog,
            text="Kick did not expose the username, so choose a label.",
            text_color=self.ui["muted_text"],
            wraplength=310,
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 12))
        name_entry = ctk.CTkEntry(dialog, height=38, placeholder_text="Account name")
        name_entry.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 16))

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 18))
        actions.grid_columnconfigure((0, 1), weight=1)

        def save():
            value = name_entry.get().strip()
            if value:
                result["value"] = value
                dialog.destroy()

        ctk.CTkButton(actions, text="Cancel", fg_color=self.ui["panel_alt"], text_color=self.ui["text"], hover_color=self.ui["border"], command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(actions, text="Save", fg_color=self.ui["blue"], hover_color=self.ui["blue_hover"], command=save).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        name_entry.bind("<Return>", lambda _event: save())
        name_entry.focus_set()
        self._show_modal(dialog)
        return result["value"]

    def _choose_account_dialog(self):
        accounts = list(self.config_data.accounts)
        if not accounts:
            return None
        result = {"account_id": None}
        name_to_id = {account["name"]: account["id"] for account in accounts}
        initial = self._account_name(self.config_data.default_account_id or accounts[0]["id"])

        dialog = ctk.CTkToplevel(self)
        dialog.title("Choose account")
        dialog.geometry("380x190")
        dialog.resizable(False, False)
        dialog.configure(fg_color=self.ui["app_bg"])
        dialog.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            dialog,
            text="Choose a Kick account",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(22, 4))
        ctk.CTkLabel(
            dialog,
            text="This drop will run with the selected account cookies.",
            text_color=self.ui["muted_text"],
        ).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 12))
        selected = tk.StringVar(value=initial)
        ctk.CTkOptionMenu(dialog, values=list(name_to_id.keys()), variable=selected, width=250).grid(row=2, column=0, sticky="w", padx=22, pady=(0, 16))

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 18))
        actions.grid_columnconfigure((0, 1), weight=1)

        def save():
            result["account_id"] = name_to_id.get(selected.get())
            dialog.destroy()

        ctk.CTkButton(actions, text="Cancel", fg_color=self.ui["panel_alt"], text_color=self.ui["text"], hover_color=self.ui["border"], command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(actions, text="Use account", fg_color=self.ui["blue"], hover_color=self.ui["blue_hover"], command=save).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self._show_modal(dialog)
        return result["account_id"]

    def _ask_add_link_details(self):
        accounts = list(self.config_data.accounts)
        if not accounts:
            messagebox.showwarning("Account required", "Add a Kick account before adding drops or stream links.")
            self.show_settings_view()
            return None

        result = {"value": None}
        name_to_id = {account["name"]: account["id"] for account in accounts}
        initial_account = self._account_name(self.config_data.default_account_id or accounts[0]["id"])

        dialog = ctk.CTkToplevel(self)
        dialog.title("Add link")
        dialog.geometry("560x420")
        dialog.minsize(500, 380)
        dialog.resizable(True, True)
        dialog.configure(fg_color=self.ui["app_bg"])
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(6, weight=1)

        ctk.CTkLabel(dialog, text="Add stream link", font=ctk.CTkFont(size=20, weight="bold"), text_color=self.ui["text"]).grid(row=0, column=0, sticky="w", padx=22, pady=(22, 4))
        ctk.CTkLabel(dialog, text="Use this for a manual Kick stream target.", text_color=self.ui["muted_text"]).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 16))

        ctk.CTkLabel(dialog, text="Kick live URL", text_color=self.ui["muted_text"]).grid(row=2, column=0, sticky="w", padx=22, pady=(0, 4))
        url_entry = ctk.CTkEntry(dialog, height=38, placeholder_text="https://kick.com/channel")
        url_entry.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 12))

        target_row = ctk.CTkFrame(dialog, fg_color="transparent")
        target_row.grid(row=4, column=0, sticky="ew", padx=22, pady=(0, 12))
        target_row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(target_row, text="Target handling", text_color=self.ui["muted_text"]).grid(row=0, column=0, sticky="w", pady=(0, 4))
        target_choice = tk.StringVar(value="Manual")
        target_segment = ctk.CTkSegmentedButton(
            target_row,
            values=["Manual", "Timed Based"],
            variable=target_choice,
        )
        target_segment.grid(row=1, column=0, sticky="ew")

        fields = ctk.CTkFrame(dialog, fg_color="transparent")
        fields.grid(row=5, column=0, sticky="ew", padx=22, pady=(0, 16))
        fields.grid_columnconfigure((0, 1), weight=1)
        manual_label = ctk.CTkLabel(fields, text="Online time minutes", text_color=self.ui["muted_text"])
        manual_label.grid(row=0, column=0, sticky="w", pady=(0, 4))
        ctk.CTkLabel(fields, text="Account", text_color=self.ui["muted_text"]).grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 4))
        minutes_entry = ctk.CTkEntry(fields, height=38, placeholder_text="0")
        minutes_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        selected_account = tk.StringVar(value=initial_account)
        ctk.CTkOptionMenu(fields, values=list(name_to_id.keys()), variable=selected_account, height=38).grid(row=1, column=1, sticky="ew", padx=(6, 0))

        def toggle_target_mode():
            enabled = target_choice.get() == "Timed Based"
            minutes_entry.configure(state="normal" if enabled else "disabled")
            if enabled:
                manual_label.grid()
                minutes_entry.grid()
            else:
                manual_label.grid_remove()
                minutes_entry.grid_remove()

        target_segment.configure(command=lambda _choice: toggle_target_mode())
        toggle_target_mode()

        error_label = ctk.CTkLabel(dialog, text="", text_color=self.ui["danger"])
        error_label.grid(row=6, column=0, sticky="w", padx=22, pady=(0, 8))

        actions = ctk.CTkFrame(dialog, fg_color="transparent")
        actions.grid(row=7, column=0, sticky="ew", padx=22, pady=(0, 20))
        actions.grid_columnconfigure((0, 1), weight=1)

        def save():
            url = url_entry.get().strip()
            if not url:
                error_label.configure(text="Enter a Kick live URL.")
                return
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            if target_choice.get() == "Manual":
                minutes = 0
            else:
                try:
                    minutes = int((minutes_entry.get() or "0").strip())
                    if minutes < 0:
                        raise ValueError
                except ValueError:
                    error_label.configure(text="Target minutes must be 0 or higher.")
                    return
            result["value"] = {
                "url": url,
                "minutes": minutes,
                "account_id": name_to_id.get(selected_account.get()),
            }
            dialog.destroy()

        ctk.CTkButton(actions, text="Cancel", fg_color=self.ui["panel_alt"], text_color=self.ui["text"], hover_color=self.ui["border"], command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkButton(actions, text="Add link", fg_color=self.ui["blue"], hover_color=self.ui["blue_hover"], command=save).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        url_entry.bind("<Return>", lambda _event: save())
        minutes_entry.bind("<Return>", lambda _event: save())
        url_entry.focus_set()
        self._show_modal(dialog)
        return result["value"]

    def _detect_logged_in_kick_name(self, driver):
        endpoints = (
            "https://kick.com/api/v2/user",
            "https://kick.com/api/v1/user",
            "https://web.kick.com/api/v1/user",
            "https://web.kick.com/api/v1/users/me",
        )
        script = """
        const cb = arguments[arguments.length - 1];
        const urls = arguments[0];
        (async () => {
          for (const url of urls) {
            try {
              const res = await fetch(url, {
                credentials: 'include',
                cache: 'no-store',
                headers: { 'Accept': 'application/json' }
              });
              if (!res.ok) continue;
              const text = await res.text();
              if (text) return cb(text);
            } catch (e) {}
          }
          cb('');
        })();
        """
        try:
            driver.set_script_timeout(15)
        except Exception:
            pass
        try:
            text = driver.execute_async_script(script, list(endpoints))
            data = json.loads(text) if text else None
        except Exception:
            data = None

        candidates = []
        if isinstance(data, dict):
            candidates.extend([data])
            for key in ("data", "user", "result"):
                if isinstance(data.get(key), dict):
                    candidates.append(data[key])
            for candidate in candidates:
                for key in ("username", "name", "slug", "display_name"):
                    value = candidate.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        return None

    def _build_drops_content(self):
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=0)
        self.content.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self.content, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(22, 18))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Browse Drops",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=self.ui["text"],
        ).grid(row=0, column=0, sticky="w")
        self.drops_status_label = ctk.CTkLabel(
            header,
            text=self.t("drops_loading"),
            font=ctk.CTkFont(size=13),
            text_color=self.ui["muted_text"],
        )
        self.drops_status_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ctk.CTkButton(
            header,
            text=self.t("btn_refresh_drops"),
            width=110,
            height=38,
            corner_radius=10,
            fg_color=self.ui["blue"],
            hover_color=self.ui["blue_hover"],
            command=lambda: self._refresh_drops(self.drops_scrollable_frame, self.drops_status_label),
        ).grid(row=0, column=1, rowspan=2, sticky="e")

        self.drops_scrollable_frame = ctk.CTkScrollableFrame(
            self.content,
            fg_color=self.ui["panel_bg"],
            corner_radius=12,
            border_width=1,
            border_color=self.ui["border"],
        )
        self.drops_scrollable_frame.grid(row=1, column=0, sticky="nsew")
        self.drops_scrollable_frame.grid_columnconfigure(0, weight=1)
        self.drops_scrollable_frame.grid_rowconfigure(0, weight=1)
        self._refresh_drops(self.drops_scrollable_frame, self.drops_status_label)

    def show_settings_window(self):
        self.show_settings_view()
        return
        """Open settings window with all toggles and dropdowns"""
        # Create settings window
        settings_window = ctk.CTkToplevel(self)
        settings_window.title("Settings")
        settings_window.geometry("450x650")
        settings_window.resizable(False, False)
        settings_window.transient(self)
        settings_window.grab_set()  # Make it modal
        
        # Center the window
        settings_window.update_idletasks()
        x = (settings_window.winfo_screenwidth() // 2) - (450 // 2)
        y = (settings_window.winfo_screenheight() // 2) - (700 // 2)
        settings_window.geometry(f"450x700+{x}+{y}")
        
        # Consistent theme
        ctk.set_appearance_mode("Dark" if self.config_data.dark_mode else "Light")
        
        # Main frame with padding
        main_frame = ctk.CTkFrame(settings_window)
        main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Title
        title_label = ctk.CTkLabel(
            main_frame,
            text="⚙️ Settings",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        title_label.pack(pady=(0, 20))
        
        # Scrollable frame for settings
        scrollable_frame = ctk.CTkScrollableFrame(main_frame)
        scrollable_frame.pack(fill="both", expand=True)
        
        # Player Settings Section
        player_section = ctk.CTkFrame(scrollable_frame)
        player_section.pack(fill="x", pady=(0, 15))
        
        player_title = ctk.CTkLabel(
            player_section,
            text="Player Settings",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        player_title.pack(anchor="w", padx=15, pady=(15, 10))
        
        # Mute toggle
        sw_mute = ctk.CTkSwitch(
            player_section,
            text=self.t("switch_mute"),
            command=self.on_toggle_mute,
            variable=self.mute_var,
        )
        sw_mute.pack(anchor="w", padx=15, pady=5)
        
        # Hide player toggle
        sw_hide = ctk.CTkSwitch(
            player_section,
            text=self.t("switch_hide"),
            command=self.on_toggle_hide,
            variable=self.hide_player_var,
        )
        sw_hide.pack(anchor="w", padx=15, pady=5)
        
        # Mini player toggle
        sw_mini = ctk.CTkSwitch(
            player_section,
            text=self.t("switch_mini"),
            command=self.on_toggle_mini,
            variable=self.mini_player_var,
        )
        sw_mini.pack(anchor="w", padx=15, pady=5)
        
        # Force 160p toggle
        sw_force_160p = ctk.CTkSwitch(
            player_section,
            text=self.t("switch_force_160p"),
            command=self.on_toggle_force_160p,
            variable=self.force_160p_var,
        )
        sw_force_160p.pack(anchor="w", padx=15, pady=(5, 15))
        
        # Queue Settings Section
        queue_section = ctk.CTkFrame(scrollable_frame)
        queue_section.pack(fill="x", pady=(0, 15))
        
        queue_title = ctk.CTkLabel(
            queue_section,
            text="Queue Settings",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        queue_title.pack(anchor="w", padx=15, pady=(15, 10))
        
        # Auto-start toggle
        sw_auto_start = ctk.CTkSwitch(
            queue_section,
            text="Auto-start queue",
            command=self.on_toggle_auto_start,
            variable=self.auto_start_var,
        )
        sw_auto_start.pack(anchor="w", padx=15, pady=(5, 15))
        
        # Appearance Settings Section
        appearance_section = ctk.CTkFrame(scrollable_frame)
        appearance_section.pack(fill="x", pady=(0, 15))
        
        appearance_title = ctk.CTkLabel(
            appearance_section,
            text="Appearance",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        appearance_title.pack(anchor="w", padx=15, pady=(15, 10))
        
        # Theme dropdown
        theme_label = ctk.CTkLabel(appearance_section, text=self.t("label_theme"))
        theme_label.pack(anchor="w", padx=15, pady=(5, 5))
        theme_menu = ctk.CTkOptionMenu(
            appearance_section,
            values=[self.t("theme_dark"), self.t("theme_light")],
            command=self.change_theme,
            variable=self.theme_var,
            width=350,
        )
        theme_menu.pack(anchor="w", padx=15, pady=(0, 10))
        
        # Language dropdown
        language_choices = self._get_language_choices()
        lang_label = ctk.CTkLabel(appearance_section, text=self.t("label_language"))
        lang_label.pack(anchor="w", padx=15, pady=(5, 5))
        lang_menu = ctk.CTkOptionMenu(
            appearance_section,
            values=language_choices,
            command=self.change_language,
            variable=self.lang_var,
            width=350,
        )
        lang_menu.pack(anchor="w", padx=15, pady=(0, 15))
        
        # Browser Settings Section
        browser_section = ctk.CTkFrame(scrollable_frame)
        browser_section.pack(fill="x", pady=(0, 15))
        
        browser_title = ctk.CTkLabel(
            browser_section,
            text="Browser Settings",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        browser_title.pack(anchor="w", padx=15, pady=(15, 10))
        
        # ChromeDriver button
        def choose_chromedriver_wrapper():
            self.choose_chromedriver()
            settings_window.lift()
            settings_window.focus_force()
            # Refresh the window to update labels
            settings_window.destroy()
            self.show_settings_window()
        
        btn_chromedriver = ctk.CTkButton(
            browser_section,
            text=self.t("btn_chromedriver"),
            command=choose_chromedriver_wrapper,
            width=350,
        )
        btn_chromedriver.pack(anchor="w", padx=15, pady=5)
        
        # Show current chromedriver path if set
        chromedriver_label = ctk.CTkLabel(
            browser_section,
            text=f"Current: {os.path.basename(self.config_data.chromedriver_path) if self.config_data.chromedriver_path else 'Not set'}",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray50")
        )
        chromedriver_label.pack(anchor="w", padx=15, pady=(0, 10))
        
        # Chrome Extension button
        def choose_extension_wrapper():
            self.choose_extension()
            settings_window.lift()
            settings_window.focus_force()
            # Refresh the window to update labels
            settings_window.destroy()
            self.show_settings_window()
        
        btn_extension = ctk.CTkButton(
            browser_section,
            text=self.t("btn_extension"),
            command=choose_extension_wrapper,
            width=350,
        )
        btn_extension.pack(anchor="w", padx=15, pady=5)
        
        # Show current extension path if set
        extension_label = ctk.CTkLabel(
            browser_section,
            text=f"Current: {os.path.basename(self.config_data.extension_path) if self.config_data.extension_path else 'Not set'}",
            font=ctk.CTkFont(size=11),
            text_color=("gray50", "gray50")
        )
        extension_label.pack(anchor="w", padx=15, pady=(0, 15))
        
        # Close button
        close_btn = ctk.CTkButton(
            settings_window,
            text="Close",
            command=settings_window.destroy,
            width=200,
        )
        close_btn.pack(pady=15)

    def change_theme(self, choice):
        # Accepts FR/EN
        dark = choice in (self.t("theme_dark"), "Sombre", "Dark")
        self.config_data.dark_mode = dark
        self.config_data.save()
        ctk.set_appearance_mode("Dark" if dark else "Light")
        self.ui = self._ui_tokens()
        self._apply_sun_valley_theme()
        self.configure(fg_color=self.ui["app_bg"])
        try:
            self.sidebar.configure(fg_color=self.ui["sidebar_bg"])
            self.content.configure(fg_color="transparent")
            self.status.configure(
                fg_color=self.ui["panel_bg"],
                text_color=self.ui["muted_text"],
            )
        except Exception:
            pass
        for w in self.sidebar.winfo_children():
            w.destroy()
        self._build_sidebar()
        self._destroy_view_cache()
        if self.current_view == "settings":
            self.show_settings_view()
        elif self.current_view == "drops":
            self.show_browse_drops_view()
        elif self.current_view == "logging":
            self.show_logging_view()
        else:
            self.show_active_drops_view()

    # ----------- Language -----------
    def change_language(self, choice):
        mapping = getattr(self, "lang_display_to_code", {})
        new_lang = None

        if isinstance(choice, str):
            new_lang = mapping.get(choice)
            if not new_lang:
                # Fallback: case-insensitive match
                for label, code in mapping.items():
                    if label.lower() == choice.lower():
                        new_lang = code
                        break

        if not new_lang:
            return

        if new_lang == self.config_data.language:
            return  # No change needed

        self.config_data.language = new_lang
        self.config_data.save()

        # Rebuild sidebar & content to refresh text
        try:
            self.sidebar.configure(fg_color=self.ui["sidebar_bg"])
            for w in self.sidebar.winfo_children():
                w.destroy()
            self._build_sidebar()
        except Exception:
            pass

        try:
            self._destroy_view_cache()
            if self.current_view == "settings":
                self.show_settings_view()
            elif self.current_view == "drops":
                self.show_browse_drops_view()
            elif self.current_view == "logging":
                self.show_logging_view()
            else:
                self.show_active_drops_view()
        except Exception:
            pass

        # Update status bar if it's at the initial text
        try:
            ready_variants = [translate(lang, "status_ready") for lang in TRANSLATIONS]
            if self.status_var.get() in ready_variants:
                self.status_var.set(self.t("status_ready"))
        except Exception:
            pass

    # ----------- Actions -----------
    def on_tree_double_click(self, event):
        """Handle double-click on tree to edit minutes"""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        
        column = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        
        if not row_id:
            return
        
        # Check if clicked on progress column (column #3) to edit target minutes
        if column == "#3":
            idx = int(row_id)
            if idx >= len(self.config_data.items):
                return
            
            # Check if this stream is currently running
            if idx in self.workers:
                messagebox.showwarning(
                    self.t("warning"),
                    self.t("cannot_edit_active_stream")
                )
                return
                
            current_minutes = self.config_data.items[idx]["minutes"]
            
            new_minutes = simpledialog.askinteger(
                self.t("prompt_minutes_title"),
                self.t("prompt_minutes_msg"),
                initialvalue=current_minutes,
                minvalue=0
            )
            
            if new_minutes is not None:
                self.config_data.items[idx]["minutes"] = new_minutes
                self.config_data.save()
                self.refresh_list()
                self.status_var.set(f"Updated target to {new_minutes} minutes")
    
    def _streamer_name_from_url(self, url):
        try:
            parsed = urlparse(url)
            return parsed.path.strip("/").split("/")[0] or url
        except Exception:
            return url

    def _drop_title_for_item(self, item):
        if item.get("campaign_name"):
            parts = [item["campaign_name"]]
            if item.get("game"):
                parts.append(item["game"])
            rewards = item.get("reward_names") or []
            if rewards:
                parts.append(", ".join(rewards[:3]))
            if item.get("account_id"):
                parts.append(self._account_name(item.get("account_id")))
            return " | ".join(parts)
        if item.get("drop_name"):
            return item.get("drop_name")
        if item.get("is_manual_link"):
            return "Manual link"
        return item.get("url", "")

    def _format_duration(self, seconds):
        seconds = max(0, int(seconds or 0))
        minutes, secs = divmod(seconds, 60)
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def _progress_text_for_seconds(self, item, elapsed_seconds):
        base_seconds = (
            item.get("cumulative_time", 0)
            if item.get("is_global_drop")
            else item.get("watched_seconds", 0)
        )
        total_seconds = int(base_seconds or 0) + int(elapsed_seconds or 0)
        target_minutes = item.get("minutes", 0) or 0
        if not target_minutes:
            return f"Manual | {self._format_duration(total_seconds)}"
        target_seconds = int(target_minutes) * 60
        remaining_seconds = max(0, target_seconds - total_seconds)
        return (
            f"{self._format_duration(total_seconds)} / "
            f"{self._format_duration(target_seconds)} | "
            f"{self._format_duration(remaining_seconds)} left"
        )

    def _progress_text_for_item(self, idx, item):
        if item.get("claimed"):
            return "Claimed"
        if item.get("finished"):
            return self.t("tag_finished")

        elapsed = self.workers[idx].elapsed_seconds if idx in self.workers else 0
        return self._progress_text_for_seconds(item, elapsed)

    def _save_worker_progress(self, idx, reason=None, log=True):
        if idx < 0 or idx >= len(self.config_data.items):
            return 0
        worker = self.workers.get(idx)
        if not worker:
            return 0
        item = self.config_data.items[idx]
        elapsed = int(getattr(worker, "elapsed_seconds", 0) or 0)
        if elapsed <= 0:
            return 0
        if item.get("is_global_drop"):
            item["cumulative_time"] = int(item.get("cumulative_time", 0) or 0) + elapsed
            item["watched_seconds"] = int(item.get("cumulative_time", 0) or 0)
        else:
            item["watched_seconds"] = int(item.get("watched_seconds", 0) or 0) + elapsed
        self.config_data.save()
        if log:
            suffix = f" ({reason})" if reason else ""
            self._add_item_log_entry(
                item,
                f"Saved progress{suffix}: {self._progress_text_for_seconds(item, 0)}",
            )
        return elapsed

    def _account_name(self, account_id):
        for account in self.config_data.accounts:
            if account.get("id") == account_id:
                return account.get("name", account_id)
        return "No account"

    def _choose_account_for_new_drop(self):
        accounts = list(self.config_data.accounts)
        if not accounts:
            messagebox.showwarning("Account required", "Add a Kick account before adding drops.")
            self.show_settings_view()
            return None
        if len(accounts) == 1:
            return self.config_data.default_account_id or accounts[0]["id"]
        return self._choose_account_dialog()

    def _sync_claimed_drop_after_finish(self, campaign_id, account_id=None):
        """Refresh Kick progress and remove a drop once Kick reports it claimed."""
        if not campaign_id:
            return

        def sync():
            driver = None
            try:
                claim_result = claim_available_drops(account_id=account_id)
                claim_driver = claim_result.get("driver")
                if claim_driver:
                    try:
                        claim_driver.quit()
                    except Exception:
                        pass
                result = fetch_drops_progress(account_id=account_id)
                progress_data = result.get("progress", [])
                driver = result.get("driver")
                match = None
                for campaign in progress_data:
                    if isinstance(campaign, dict) and campaign.get("id") == campaign_id:
                        match = campaign
                        break
                if not match:
                    return

                rewards = match.get("rewards", [])
                all_claimed = bool(rewards) and all(
                    bool(reward.get("claimed")) for reward in rewards if isinstance(reward, dict)
                )
                claimed = match.get("status") == "claimed" or all_claimed
                progress_units = match.get("progress_units", 0)

                def apply_progress():
                    idx = self._find_campaign_index(campaign_id)
                    if idx is None:
                        return
                    if claimed:
                        self.config_data.items[idx]["claimed"] = True
                        self.config_data.items[idx]["finished"] = True
                        self.config_data.save()
                        self._add_item_log_entry(self.config_data.items[idx], f"Drop {self._drop_title_for_item(self.config_data.items[idx])} claimed")
                        if not self.queue_running and not self.workers:
                            self.config_data.remove(idx)
                        self.refresh_list()
                        self.status_var.set("Claimed drop removed from active list" if not self.queue_running else "Claimed drop will be removed when queue stops")
                        return
                    self.config_data.items[idx]["progress_units"] = progress_units
                    self.config_data.save()
                    self._add_item_log_entry(self.config_data.items[idx], f"Drop {self._drop_title_for_item(self.config_data.items[idx])} is on {self._progress_text_for_item(idx, self.config_data.items[idx])}")
                    self.refresh_list()

                self.after(0, apply_progress)
            except Exception as e:
                debug_print(f"DEBUG: Could not sync claimed drop state: {e}")
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass

        threading.Thread(target=sync, daemon=True).start()

    def _prune_claimed_items(self):
        if self.workers:
            return
        before = len(self.config_data.items)
        self.config_data.items = [
            item for item in self.config_data.items if not item.get("claimed")
        ]
        if len(self.config_data.items) != before:
            self.config_data.save()
            self.refresh_list()

    def _update_sidebar_status(self):
        queued = len([
            item for item in self.config_data.items
            if not item.get("finished") and not item.get("claimed")
        ])
        running = self.queue_running or bool(self.workers)
        try:
            self.sidebar_status_label.configure(text="Running" if running else "Ready")
            self.sidebar_status_dot.configure(
                text_color=self.ui["accent"] if running else self.ui["muted_text"]
            )
            self.sidebar_queue_label.configure(text=f"{queued} in queue")
            if hasattr(self, "start_queue_button"):
                if running:
                    self.start_queue_button.configure(
                        text="Stop Queue",
                        command=self.stop_queue,
                        fg_color=self.ui["danger"],
                        hover_color=self.ui["danger_hover"],
                    )
                else:
                    self.start_queue_button.configure(
                        text=self.t("btn_start_queue"),
                        command=self.start_all_in_order,
                        fg_color=self.ui["blue"],
                        hover_color=self.ui["blue_hover"],
                    )
            if hasattr(self, "skip_creator_button"):
                if running:
                    self.skip_creator_button.configure(
                        state="normal",
                        fg_color="#f59e0b",
                        hover_color="#d97706",
                        text_color="white",
                    )
                else:
                    self.skip_creator_button.configure(
                        state="disabled",
                        fg_color=self.ui["panel_alt"],
                        hover_color=self.ui["panel_alt"],
                        text_color=self.ui["muted_text"],
                    )
        except Exception:
            pass

    def _schedule_refresh_list(self, delay=120):
        if self._refresh_after_id:
            try:
                self.after_cancel(self._refresh_after_id)
            except Exception:
                pass
        self._refresh_after_id = self.after(delay, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self):
        self._refresh_after_id = None
        self.refresh_list()

    def refresh_list(self):
        for r in self.tree.get_children():
            self.tree.delete(r)
        query = ""
        try:
            query = (self.search_var.get() or "").strip().lower()
        except Exception:
            pass
        visible_count = 0
        for i, item in enumerate(self.config_data.items):
            drop_title = self._drop_title_for_item(item)
            current_streamer = self._streamer_name_from_url(item.get("url", ""))
            progress = self._progress_text_for_item(i, item)
            haystack = " ".join([
                drop_title,
                current_streamer,
                progress,
                item.get("game") or "",
                " ".join(item.get("reward_names") or []),
            ]).lower()
            if query and query not in haystack:
                continue

            tags = ["odd" if i % 2 else "even"]
            if item.get("finished"):
                tags.append("finished")
            if item.get("claimed"):
                tags.append("finished")
            self.tree.insert(
                "",
                "end",
                iid=str(i),
                values=(drop_title, current_streamer, progress),
                tags=tuple(tags),
            )
            visible_count += 1

        try:
            if visible_count:
                self.empty_state.grid_remove()
                self.tree.grid()
                self.tree_scrollbar.grid()
            else:
                self.tree.grid_remove()
                self.tree_scrollbar.grid_remove()
                self.empty_state.grid()
        except Exception:
            pass
        self._update_sidebar_status()
        self._active_dirty = False

    def add_link(self):
        details = self._ask_add_link_details()
        if not details:
            return
        self.config_data.add(
            details["url"],
            details["minutes"],
            account_id=details["account_id"],
            is_manual_link=True,
            drop_name="Manual link",
        )
        self.refresh_list()
        self.status_var.set(self.t("status_link_added"))
        # Auto-start if enabled and queue not running
        if self.config_data.auto_start and not self.queue_running:
            self.after(500, self._auto_start_queue)

    def on_remove_button_click(self, event):
        """Handle remove button click - check for Ctrl key"""
        # Check if Ctrl key is pressed (state & 0x4 is Control modifier)
        ctrl_pressed = (event.state & 0x4) != 0
        
        if ctrl_pressed:
            # Ctrl is pressed - show clear all dialog
            self.after(0, self.clear_all_items)
        else:
            # Normal remove action
            self.after(0, self.remove_selected)
    
    def clear_all_items(self):
        """Clear all items from the list after confirmation"""
        if not self.config_data.items:
            return  # Nothing to clear
        
        # Show confirmation dialog
        result = messagebox.askyesno(
            "Clear All Items",
            f"Are you sure you want to remove all {len(self.config_data.items)} item(s) from the list?",
            icon="warning"
        )
        
        if result:
            # Stop all running workers
            for idx, worker in list(self.workers.items()):
                try:
                    worker.stop()
                except Exception:
                    pass
            self.workers.clear()
            
            # Clear all items
            self.config_data.items = []
            self.config_data.save()
            
            # Refresh UI
            self.refresh_list()
            self.status_var.set("All items cleared")
            debug_print(f"DEBUG: Cleared all items from list")
    
    def remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.config_data.remove(idx)
        if idx in self.workers:
            self.workers[idx].stop()
            del self.workers[idx]
        # Re-index workers (because indices have shifted)
        self.workers = {
            new_i: self.workers[old_i]
            for new_i, old_i in enumerate(sorted(self.workers.keys()))
            if old_i < len(self.config_data.items)
        }
        self.refresh_list()
        self.status_var.set(self.t("status_link_removed"))

    def start_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self._start_index(idx)

    def _ensure_cookies_for_item(self, item, domain):
        account_id = item.get("account_id") or self.config_data.default_account_id
        if not account_id:
            self._add_item_log_entry(item, f"No Kick account connected for {self._drop_title_for_item(item)}")
            if self.config_data.auto_start:
                self.status_var.set(f"Skipping {item['url']} - no Kick account connected")
                return False
            if messagebox.askyesno("Account required", "Add a Kick account before running this drop?"):
                self.add_account()
            return False
        cookie_path = cookie_file_for_account(domain, account_id)
        if os.path.exists(cookie_path):
            return True
        self._add_log_entry(f"No valid cookies for {self._account_name(account_id)}; login required")
        if self.config_data.auto_start:
            self.status_var.set(
                f"Skipping {item['url']} - no cookies for {self._account_name(account_id)}"
            )
            return False
        if messagebox.askyesno(
            "Missing account cookies",
            f"No saved cookies for {self._account_name(account_id)}. Open browser to sign in?",
        ):
            self.obtain_cookies_interactively(item["url"], domain, account_id)
            return os.path.exists(cookie_path)
        return False

    def _start_index(self, idx):
        """Start a stream, ensuring only one runs at a time (Kick limitation)"""
        # Stop any currently running stream (Kick only allows 1 at a time)
        if len(self.workers) > 0:
            # Find and stop the currently running worker
            for running_idx, worker in list(self.workers.items()):
                self._ignored_finishes.add(running_idx)
                worker.stop()
                del self.workers[running_idx]
                # Mark as not finished so it can be retried
                if running_idx < len(self.config_data.items):
                    self.config_data.items[running_idx]["finished"] = False
            time.sleep(2)  # Brief pause to let browser close
        
        item = self.config_data.items[idx]
        
        # Try alternative channels from same campaign if current is offline
        if not kick_is_live_by_api(item["url"]):
            self._add_item_log_entry(item, f"{self._streamer_name_from_url(item['url'])} is offline")
            campaign_channels = item.get("campaign_channels", [])
            if campaign_channels:
                tried_channels = item.get("tried_channels", [])
                current_url = item["url"]
                
                # Add current URL to tried list if not already there
                if current_url not in tried_channels:
                    tried_channels.append(current_url)
                
                # Get all channel URLs
                all_channel_urls = []
                for ch in campaign_channels:
                    ch_url = ch.get("url") if isinstance(ch, dict) else ch
                    if ch_url:
                        all_channel_urls.append(ch_url)
                if current_url not in all_channel_urls:
                    all_channel_urls.append(current_url)
                
                # Reset if all channels tried
                if len(tried_channels) >= len(all_channel_urls):
                    tried_channels.clear()
                    debug_print(f"DEBUG: Reset tried_channels in _start_index for campaign {item.get('campaign_id')}")
                
                # Try to find a live alternative channel that hasn't been tried
                switched_in_start = False
                for alt_channel in campaign_channels:
                    alt_url = alt_channel.get("url") if isinstance(alt_channel, dict) else alt_channel
                    if alt_url and alt_url != item["url"] and alt_url not in tried_channels:
                        if kick_is_live_by_api(alt_url):
                            # Switch to this alternative channel
                            self.config_data.items[idx]["url"] = alt_url
                            tried_channels.append(alt_url)
                            item["tried_channels"] = tried_channels
                            self.config_data.save()
                            self.refresh_list()
                            item = self.config_data.items[idx]  # Update item reference
                            debug_print(f"DEBUG: Switched to alternative in _start_index: {alt_url} (tried: {len(tried_channels)}/{len(all_channel_urls)})")
                            self.status_var.set(f"Switched to {alt_url.split('/')[-1]} - waiting for page to load...")
                            self._add_item_log_entry(item, f"Streamer went offline, moving to next: {self._streamer_name_from_url(alt_url)}", alt_url)
                            switched_in_start = True
                            # Wait 8 seconds to allow browser to fully load before checking if stream is live
                            # Use after() to avoid blocking UI thread
                            self.after(8000, lambda i=idx: self._start_index_after_switch(i))
                            return
                
                # If we switched, we already scheduled a callback, so return early
                if switched_in_start:
                    return
        
        # Check again after potential channel switch
        if not kick_is_live_by_api(item["url"]):
            try:
                values = list(self.tree.item(str(idx), "values"))
                values[2] = self.t("retry")
                self.tree.item(str(idx), values=values, tags=("redo",))
            except Exception:
                pass
            self.status_var.set(self.t("offline_wait_retry", url=item["url"]))
            self._add_item_log_entry(item, f"No live streamer available for {self._drop_title_for_item(item)}; waiting to retry")
            return

        domain = domain_from_url(item["url"])
        if not domain:
            messagebox.showerror(self.t("error"), self.t("invalid_url"))
            return

        if not self._ensure_cookies_for_item(item, domain):
            return

        stop_event = threading.Event()
        
        # Setup cumulative time callback for global drops
        is_global_drop = item.get("is_global_drop", False)
        cumulative_time_callback = None
        if is_global_drop:
            campaign_id = item.get("campaign_id")
            def get_cumulative_time():
                """Get current cumulative time for this campaign"""
                if not campaign_id:
                    return 0
                total = 0
                for other_item in self.config_data.items:
                    if other_item.get("campaign_id") == campaign_id:
                        total += other_item.get("cumulative_time", 0)
                return total
            cumulative_time_callback = get_cumulative_time
        
        saved_seconds = int(item.get("cumulative_time" if is_global_drop else "watched_seconds", 0) or 0)
        target_seconds = int(item.get("minutes", 0) or 0) * 60
        remaining_minutes = item["minutes"]
        if target_seconds:
            remaining_seconds = max(0, target_seconds - saved_seconds)
            if remaining_seconds <= 0:
                item["finished"] = True
                self.config_data.save()
                self.refresh_list()
                self._add_item_log_entry(item, f"Already complete at {self._format_duration(saved_seconds)} watched")
                return
            remaining_minutes = remaining_seconds / 60
            self._add_item_log_entry(
                item,
                f"Resuming at {self._format_duration(saved_seconds)} watched; {self._format_duration(remaining_seconds)} left",
            )
        else:
            self._add_item_log_entry(item, f"Resuming manual watch at {self._format_duration(saved_seconds)} watched")
        if is_global_drop and cumulative_time_callback:
            original_cumulative_time_callback = cumulative_time_callback
            cumulative_time_callback = lambda saved=saved_seconds: max(
                0, original_cumulative_time_callback() - saved
            )

        worker = StreamWorker(
            item["url"],
            remaining_minutes,
            on_update=lambda s, live: self.on_worker_update(idx, s, live),
            on_finish=lambda e, c: self.on_worker_finish(idx, e, c),
            stop_event=stop_event,
            driver_path=self.config_data.chromedriver_path,
            extension_path=self.config_data.extension_path,
            hide_player=bool(self.hide_player_var.get()),
            mute=bool(self.mute_var.get()),
            mini_player=bool(self.mini_player_var.get()),
            force_160p=bool(self.config_data.force_160p),
            required_category_id=item.get("required_category_id"),
            cumulative_time_callback=cumulative_time_callback,
            account_id=item.get("account_id") or self.config_data.default_account_id,
        )
        self.workers[idx] = worker
        worker.start()
        self.tree.selection_set(str(idx))
        self.status_var.set(self.t("status_playing", url=item["url"]))
        self._add_item_log_entry(item, f"Start watching stream: {self._streamer_name_from_url(item['url'])} for {self._drop_title_for_item(item)}")

    def _start_index_after_switch(self, idx):
        """Continue _start_index after a delay when switching channels"""
        if idx < 0 or idx >= len(self.config_data.items):
            return
        
        item = self.config_data.items[idx]
        
        # Check again after potential channel switch (after delay)
        if not kick_is_live_by_api(item["url"]):
            try:
                values = list(self.tree.item(str(idx), "values"))
                values[2] = self.t("retry")
                self.tree.item(str(idx), values=values, tags=("redo",))
            except Exception:
                pass
            self.status_var.set(self.t("offline_wait_retry", url=item["url"]))
            self._add_item_log_entry(item, f"{self._streamer_name_from_url(item['url'])} is still offline after switch delay")
            return

        domain = domain_from_url(item["url"])
        if not domain:
            messagebox.showerror(self.t("error"), self.t("invalid_url"))
            return

        if not self._ensure_cookies_for_item(item, domain):
            return

        stop_event = threading.Event()
        
        # Setup cumulative time callback for global drops
        is_global_drop = item.get("is_global_drop", False)
        cumulative_time_callback = None
        if is_global_drop:
            campaign_id = item.get("campaign_id")
            def get_cumulative_time():
                """Get current cumulative time for this campaign"""
                if not campaign_id:
                    return 0
                total = 0
                for other_item in self.config_data.items:
                    if other_item.get("campaign_id") == campaign_id:
                        total += other_item.get("cumulative_time", 0)
                return total
            cumulative_time_callback = get_cumulative_time
        
        saved_seconds = int(item.get("cumulative_time" if is_global_drop else "watched_seconds", 0) or 0)
        target_seconds = int(item.get("minutes", 0) or 0) * 60
        remaining_minutes = item["minutes"]
        if target_seconds:
            remaining_seconds = max(0, target_seconds - saved_seconds)
            if remaining_seconds <= 0:
                item["finished"] = True
                self.config_data.save()
                self.refresh_list()
                self._add_item_log_entry(item, f"Already complete at {self._format_duration(saved_seconds)} watched")
                return
            remaining_minutes = remaining_seconds / 60
            self._add_item_log_entry(
                item,
                f"Resuming at {self._format_duration(saved_seconds)} watched; {self._format_duration(remaining_seconds)} left",
            )
        else:
            self._add_item_log_entry(item, f"Resuming manual watch at {self._format_duration(saved_seconds)} watched")
        if is_global_drop and cumulative_time_callback:
            original_cumulative_time_callback = cumulative_time_callback
            cumulative_time_callback = lambda saved=saved_seconds: max(
                0, original_cumulative_time_callback() - saved
            )

        worker = StreamWorker(
            item["url"],
            remaining_minutes,
            on_update=lambda s, live: self.on_worker_update(idx, s, live),
            on_finish=lambda e, c: self.on_worker_finish(idx, e, c),
            stop_event=stop_event,
            driver_path=self.config_data.chromedriver_path,
            extension_path=self.config_data.extension_path,
            hide_player=bool(self.hide_player_var.get()),
            mute=bool(self.mute_var.get()),
            mini_player=bool(self.mini_player_var.get()),
            force_160p=bool(self.config_data.force_160p),
            required_category_id=item.get("required_category_id"),
            cumulative_time_callback=cumulative_time_callback,
            account_id=item.get("account_id") or self.config_data.default_account_id,
        )
        self.workers[idx] = worker
        worker.start()
        self.tree.selection_set(str(idx))
        self.status_var.set(self.t("status_playing", url=item["url"]))
        self._add_item_log_entry(item, f"Start watching stream: {self._streamer_name_from_url(item['url'])} for {self._drop_title_for_item(item)}")

    def start_all_in_order(self):
        if self.queue_running or self.workers:
            self.stop_queue()
            return
        self.queue_running = True
        self.queue_current_idx = None
        self._update_sidebar_status()
        self._add_log_entry("Queue started")
        self._run_queue_from(0)

    def _run_queue_from(self, start_idx: int):
        """Run queue ensuring only one stream at a time"""
        # Ensure no other streams are running
        if len(self.workers) > 0:
            # Wait for current stream to finish
            return
        
        for i in range(start_idx, len(self.config_data.items)):
            item = self.config_data.items[i]
            if item.get("finished") or item.get("claimed"):
                continue
            self.tree.selection_set(str(i))
            before = set(self.workers.keys())
            self._start_index(i)
            after = set(self.workers.keys())
            if i in after:
                self.queue_current_idx = i
                self.status_var.set(self.t("queue_running_status", url=item["url"]))
                return  # Only one stream at a time
        self.queue_running = False
        self.queue_current_idx = None
        self._prune_claimed_items()
        self.status_var.set(self.t("queue_finished_status"))
        self._add_log_entry("Queue finished")
        self._update_sidebar_status()

    def stop_queue(self):
        self.queue_running = False
        self.queue_current_idx = None
        for idx, worker in list(self.workers.items()):
            self._save_worker_progress(idx, reason="queue stopped")
            self._ignored_finishes.add(idx)
            try:
                worker.stop()
            except Exception:
                pass
            self.workers.pop(idx, None)
        self.status_var.set("Queue stopped")
        self._add_log_entry("Queue stopped")
        self.refresh_list()
        self._update_sidebar_status()

    def skip_creator(self):
        idx = self.queue_current_idx
        if idx is None and self.workers:
            idx = next(iter(self.workers.keys()))
        if idx is None or idx < 0 or idx >= len(self.config_data.items):
            return

        item = self.config_data.items[idx]
        current_url = item.get("url", "")
        self._add_item_log_entry(item, f"Skipped creator: {self._streamer_name_from_url(current_url)}", current_url)

        tried_channels = item.get("tried_channels", [])
        if current_url and current_url not in tried_channels:
            tried_channels.append(current_url)

        next_url = None
        for channel in item.get("campaign_channels", []):
            alt_url = channel.get("url")
            if alt_url and alt_url != current_url and alt_url not in tried_channels:
                next_url = alt_url
                tried_channels.append(alt_url)
                break

        item["tried_channels"] = tried_channels
        if next_url:
            item["_manual_next_url"] = next_url
            self.status_var.set(f"Skipped to {self._streamer_name_from_url(next_url)}")
            self._add_item_log_entry(item, f"Moving to next creator: {self._streamer_name_from_url(next_url)}", next_url)
        else:
            item["_manual_skip_drop"] = True
            self.status_var.set("No more creators for this drop; moving to next drop")
            self._add_item_log_entry(item, f"No more creators for {self._drop_title_for_item(item)}; moving to next drop")

        worker = self.workers.get(idx)
        if worker:
            self._save_worker_progress(idx, reason="creator skipped")
            try:
                worker.stop()
            except Exception:
                pass
            return

        self._complete_manual_skip(idx)

    def _complete_manual_skip(self, idx):
        if idx < 0 or idx >= len(self.config_data.items):
            return True
        item = self.config_data.items[idx]
        next_url = item.pop("_manual_next_url", None)
        skip_drop = bool(item.pop("_manual_skip_drop", False))
        if not next_url and not skip_drop:
            return False
        self.workers.pop(idx, None)
        if next_url:
            item["url"] = next_url
            self.config_data.save()
            self.refresh_list()
            if self.queue_running:
                self.after(500, lambda i=idx: self._start_index(i))
            return True
        self.config_data.save()
        self.refresh_list()
        if self.queue_running:
            self.queue_current_idx = None
            self.after(500, lambda: self._run_queue_from(idx + 1))
        return True

    def stop_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        if idx in self.workers:
            self._save_worker_progress(idx, reason="stream stopped")
            self._ignored_finishes.add(idx)
            self.workers[idx].stop()
            del self.workers[idx]
            self.status_var.set(self.t("status_stopped"))
            self._add_item_log_entry(self.config_data.items[idx], f"Stopped stream: {self._drop_title_for_item(self.config_data.items[idx])}")
            self._update_sidebar_status()
            # Update the display
            if str(idx) in self.tree.get_children():
                values = list(self.tree.item(str(idx), "values"))
                values[2] = f"{values[2]} ({self.t('tag_stop')})"
                self.tree.item(str(idx), values=values)

    def obtain_cookies_interactively(self, url, domain, account_id=None):
        try:
            drv = make_chrome_driver(
                headless=False,
                driver_path=self.config_data.chromedriver_path,
                extension_path=self.config_data.extension_path,
            )
            self._interactive_driver = drv
        except Exception as e:
            messagebox.showerror(self.t("error"), self.t("chrome_start_fail", e=e))
            return {"saved": False, "account_name": None}
        drv.get(url)
        try:
            self.after(1500, lambda: accept_kick_cookies(drv))
        except Exception:
            pass
        saved_account_id = account_id or self.config_data.default_account_id
        account_name = self._account_name(saved_account_id)
        is_placeholder_account = account_name in ("New account", "Signing in...", "No account")
        login_prompt = (
            "Please sign in to Kick in the Chrome window, then click OK to save cookies."
            if is_placeholder_account
            else f"Sign in as {account_name} in the Chrome window, then click OK to save cookies."
        )
        messagebox.showinfo(
            self.t("action_required"),
            login_prompt,
        )
        try:
            CookieManager.save_cookies(drv, domain, saved_account_id)
            detected_name = self._detect_logged_in_kick_name(drv)
            if detected_name and saved_account_id:
                self.config_data.update_account_name(saved_account_id, detected_name)
                account_name = detected_name
            messagebox.showinfo(
                self.t("ok"), f"Cookies saved for {account_name} ({domain})"
            )
            if self.current_view == "settings":
                self._destroy_view_cache("settings")
                self.show_settings_view()
            return {"saved": True, "account_name": detected_name}
        except Exception as e:
            messagebox.showerror(self.t("error"), self.t("cannot_save_cookies", e=e))
            return {"saved": False, "account_name": None}
        finally:
            try:
                drv.quit()
            except Exception:
                pass
            finally:
                self._interactive_driver = None

    def on_close(self):
        # Stop the queue and close all browser windows
        try:
            self.queue_running = False
        except Exception:
            pass

        # Close Chrome cookie import window if open
        try:
            if self._interactive_driver:
                try:
                    self._interactive_driver.quit()
                except Exception:
                    pass
                self._interactive_driver = None
        except Exception:
            pass

        # Stop and close all Selenium drivers from workers
        for idx, w in list(self.workers.items()):
            try:
                w.stop()
            except Exception:
                pass
            try:
                if getattr(w, "driver", None):
                    try:
                        w.driver.quit()
                    except Exception:
                        pass
            except Exception:
                pass

        # Wait briefly for threads to stop
        for idx, w in list(self.workers.items()):
            try:
                w.join(timeout=2.5)
            except Exception:
                pass

        # Close the application
        try:
            self.destroy()
        except Exception:
            os._exit(0)

    def connect_to_kick(self):
        if not self.config_data.accounts:
            self.add_account()
            return
        sel = self.tree.selection()
        if sel:
            idx = int(sel[0])
            url = self.config_data.items[idx]["url"]
            domain = domain_from_url(url)
        else:
            url = "https://kick.com"
            domain = "kick.com"
        account_id = self.config_data.default_account_id
        if messagebox.askyesno(
            self.t("connect_title"), self.t("open_url_to_get_cookies", url=url)
        ):
            self.obtain_cookies_interactively(url, domain, account_id)

    def choose_chromedriver(self):
        path = filedialog.askopenfilename(
            title=self.t("pick_chromedriver_title"),
            filetypes=[(self.t("executables_filter"), "*.exe;*")],
        )
        if not path:
            return
        self.config_data.chromedriver_path = path
        self.config_data.save()
        messagebox.showinfo(self.t("ok"), self.t("chromedriver_set", path=path))

    def choose_extension(self):
        path = filedialog.askopenfilename(
            title=self.t("pick_extension_title"),
            filetypes=[("CRX", "*.crx"), (self.t("all_files_filter"), "*.*")],
        )
        if not path:
            return
        self.config_data.extension_path = path
        self.config_data.save()
        messagebox.showinfo(self.t("ok"), self.t("extension_set", path=path))

    def show_drops_window(self):
        self.show_browse_drops_view()
        return
        """Opens a window to display and select drop campaigns"""
        drops_window = ctk.CTkToplevel(self)
        drops_window.title(self.t("drops_title"))
        drops_window.geometry("1000x700")
        drops_window.minsize(900, 600)
        
        # Keep window on top
        drops_window.attributes('-topmost', True)
        drops_window.lift()
        drops_window.focus_force()

        # Consistent theme
        ctk.set_appearance_mode("Dark" if self.config_data.dark_mode else "Light")

        # Main frame with background color
        main_frame = ctk.CTkFrame(drops_window, fg_color=("gray92", "gray14"))
        main_frame.pack(fill="both", expand=True, padx=0, pady=0)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(1, weight=1)

        # Header with refresh button
        header_frame = ctk.CTkFrame(main_frame, fg_color=("gray86", "gray17"), corner_radius=0, height=60)
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        header_frame.grid_propagate(False)

        status_label = ctk.CTkLabel(
            header_frame, text=self.t("drops_loading"), 
            font=ctk.CTkFont(size=16, weight="bold")
        )
        status_label.grid(row=0, column=0, sticky="w", padx=20, pady=15)

        scrollable_frame = ctk.CTkScrollableFrame(
            main_frame, 
            label_text="",
            fg_color=("gray92", "gray14")
        )
        scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=15)
        scrollable_frame.grid_columnconfigure(0, weight=1)

        refresh_btn = ctk.CTkButton(
            header_frame,
            text=self.t("btn_refresh_drops"),
            width=130,
            height=35,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=("#3b82f6", "#2563eb"),
            hover_color=("#2563eb", "#1d4ed8"),
            command=lambda: self._refresh_drops(scrollable_frame, status_label),
        )
        refresh_btn.grid(row=0, column=1, padx=20, pady=15)

        # Refresh function for buttons
        def refresh_callback():
            threading.Thread(target=lambda: self._refresh_drops(scrollable_frame, status_label), daemon=True).start()
        
        # Store reference for buttons
        self._current_drops_refresh = refresh_callback
        
        # Load initial campaigns in a separate thread
        def load_and_focus():
            self._refresh_drops(scrollable_frame, status_label)
            # Bring window to front after loading
            try:
                drops_window.lift()
                drops_window.focus_force()
            except:
                pass
        
        threading.Thread(target=load_and_focus, daemon=True).start()

    def _refresh_drops(self, scrollable_frame, status_label):
        """Refreshes the list of drop campaigns with integrated progress"""

        # Clean the frame
        def clear_frame():
            if not self._widget_exists(scrollable_frame) or not self._widget_exists(status_label):
                return
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            status_label.configure(text=self.t("drops_loading"))

        self.after(0, clear_frame)

        def display_campaigns():
            driver = None
            try:
                # Fetch both campaigns and progress using a single Chrome instance
                result = fetch_drops_campaigns_and_progress()
                if not self._widget_exists(scrollable_frame) or not self._widget_exists(status_label):
                    return
                campaigns = result.get("campaigns", [])
                progress_data = result.get("progress", [])
                progress_data = [p for p in progress_data if isinstance(p, dict)]
                driver = result.get("driver")
                
                if not campaigns:
                    status_label.configure(text=self.t("drops_error"))
                    no_data_label = ctk.CTkLabel(
                        scrollable_frame,
                        text=self.t("drops_error"),
                        font=ctk.CTkFont(size=12),
                        text_color="gray",
                    )
                    no_data_label.grid(row=0, column=0, pady=20)
                    return

                # Create a progress lookup by campaign ID
                progress_by_id = {}
                for prog in progress_data:
                    if not isinstance(prog, dict):
                        continue  # Skip unexpected progress entries
                    campaign_id = prog.get("id")
                    if campaign_id:
                        progress_by_id[campaign_id] = prog
                
                # Merge progress data into campaigns
                for campaign in campaigns:
                    campaign_id = campaign.get("id")
                    if campaign_id in progress_by_id:
                        # Campaign has progress - merge progress info
                        prog = progress_by_id[campaign_id]
                        campaign["progress_data"] = prog
                        campaign["progress_status"] = prog.get("status", "not_started")
                        campaign["progress_units"] = prog.get("progress_units", 0)
                        
                        # Merge category from progress data if not already in campaign
                        if "category" in prog and "category" not in campaign:
                            campaign["category"] = prog["category"]
                        elif "category" in prog:
                            # Update category if progress has more complete data
                            campaign["category"] = prog["category"]
                        
                        # Merge reward progress
                        reward_progress = {}
                        for reward in prog.get("rewards", []):
                            reward_id = reward.get("id")
                            if reward_id:
                                reward_progress[reward_id] = {
                                    "progress": reward.get("progress", 0.0),
                                    "claimed": reward.get("claimed", False),
                                    "required_units": reward.get("required_units", 0),
                                }
                        
                        # Attach progress to each reward in campaign
                        for reward in campaign.get("rewards", []):
                            reward_id = reward.get("id")
                            if reward_id in reward_progress:
                                reward["progress"] = reward_progress[reward_id]["progress"]
                                reward["claimed"] = reward_progress[reward_id]["claimed"]
                                reward["progress_required_units"] = reward_progress[reward_id]["required_units"]
                    else:
                        # Campaign has no progress - not started
                        campaign["progress_data"] = None
                        campaign["progress_status"] = "not_started"
                        campaign["progress_units"] = 0
                        for reward in campaign.get("rewards", []):
                            reward["progress"] = 0.0
                            reward["claimed"] = False

                # Filter campaigns into active and expired
                active_campaigns = []
                expired_campaigns = []
                
                for campaign in campaigns:
                    if is_campaign_expired(campaign):
                        expired_campaigns.append(campaign)
                    else:
                        active_campaigns.append(campaign)
                
                # Group active campaigns by game and sort by progress status
                games = {}
                for campaign in active_campaigns:
                    # Double-check: skip if expired (safety check)
                    if is_campaign_expired(campaign):
                        continue
                    game_name = campaign["game"]
                    if game_name not in games:
                        games[game_name] = {
                            "image": campaign.get("game_image", ""),
                            "campaigns": [],
                        }
                    games[game_name]["campaigns"].append(campaign)
                
                # Sort campaigns within each game by progress status
                # Priority: in progress > not started > claimed/completed
                def sort_key(campaign):
                    status = campaign.get("progress_status", "not_started")
                    if status == "in progress":
                        return 0
                    elif status == "not_started":
                        return 1
                    elif status == "claimed":
                        return 2
                    else:
                        return 3
                
                for game_name, game_data in games.items():
                    game_data["campaigns"].sort(key=sort_key)
                
                # Sort games by priority: games with in-progress campaigns first
                def game_priority(game_data):
                    campaigns = game_data["campaigns"]
                    # Check if any campaign is in progress
                    has_in_progress = any(c.get("progress_status") == "in progress" for c in campaigns)
                    if has_in_progress:
                        return 0
                    # Check if any campaign is not started
                    has_not_started = any(c.get("progress_status") == "not_started" for c in campaigns)
                    if has_not_started:
                        return 1
                    return 2
                
                # Convert to list, sort, then back to dict (or use OrderedDict)
                games_list = sorted(games.items(), key=lambda x: game_priority(x[1]))
                games = dict(games_list)

                status_text = self.t("drops_loaded", count=len(active_campaigns))
                if expired_campaigns:
                    status_text += f" ({len(expired_campaigns)} expired)"
                status_label.configure(text=status_text)

                # Add toggle for showing expired campaigns
                if not hasattr(scrollable_frame, "_show_expired_var"):
                    scrollable_frame._show_expired_var = tk.BooleanVar(value=False)
                
                show_expired = scrollable_frame._show_expired_var.get()
                
                # Display each game with its campaigns
                row_idx = 0
                for game_name, game_data in games.items():
                    # Separate campaigns into active and completed
                    game_active_campaigns = []
                    game_completed_campaigns = []
                    
                    for campaign in game_data["campaigns"]:
                        status = campaign.get("progress_status", "not_started")
                        if status == "claimed":
                            game_completed_campaigns.append(campaign)
                        else:
                            game_active_campaigns.append(campaign)
                    # Frame for game (collapsible) - improved style
                    game_frame = ctk.CTkFrame(
                        scrollable_frame, 
                        corner_radius=12,
                        border_width=2,
                        border_color=("#3b82f6", "#2563eb")
                    )
                    game_frame.grid(row=row_idx, column=0, sticky="ew", padx=0, pady=10)
                    game_frame.grid_columnconfigure(0, weight=1)

                    # Variable for toggle collapse
                    is_expanded = tk.BooleanVar(value=True)

                    # Game header (clickable to collapse/expand) - larger and colored
                    game_header = ctk.CTkFrame(
                        game_frame, 
                        fg_color=("#e0f2fe", "#1e3a5f"),
                        cursor="hand2",
                        corner_radius=10
                    )
                    game_header.grid(row=0, column=0, sticky="ew", padx=3, pady=3)
                    # Don't expand any column - let content determine width
                    game_header.grid_columnconfigure(3, weight=1)  # Expand the empty space column

                    # Expand/collapse icon - more visible
                    collapse_icon = ctk.CTkLabel(
                        game_header, 
                        text="▼", 
                        font=ctk.CTkFont(size=14, weight="bold"),
                        text_color=("#3b82f6", "#60a5fa")
                    )
                    collapse_icon.grid(row=0, column=0, padx=(15, 10), pady=12)

                    # Game image (if available) - larger
                    col_offset = 1
                    if game_data["image"]:
                        try:
                            # Download and display game image
                            with urllib.request.urlopen(
                                game_data["image"], timeout=3
                            ) as response:
                                image_data = response.read()
                            game_img = Image.open(BytesIO(image_data))
                            game_img = game_img.resize(
                                (48, 48), Image.Resampling.LANCZOS
                            )
                            game_photo = ctk.CTkImage(
                                light_image=game_img, dark_image=game_img, size=(48, 48)
                            )

                            img_label = ctk.CTkLabel(
                                game_header, image=game_photo, text="", cursor="hand2"
                            )
                            img_label.image = game_photo
                            img_label.grid(row=0, column=1, padx=(0, 12))
                            col_offset = 2
                        except Exception as e:
                            print(f"Could not load game image: {e}")

                    # Game name - larger and colored
                    game_label = ctk.CTkLabel(
                        game_header,
                        text=game_name,
                        font=ctk.CTkFont(size=20, weight="bold"),
                        text_color=("#1e40af", "#93c5fd")
                    )
                    game_label.grid(row=0, column=col_offset, sticky="w", padx=(0, 0))
                    
                    # Spacer column to push badge to the right
                    # (column 3 has weight=1)

                    # Number of campaigns - styled badge, aligned right
                    count_label = ctk.CTkLabel(
                        game_header,
                        text=f"{len(game_data['campaigns'])} campaign{'s' if len(game_data['campaigns']) > 1 else ''}",
                        font=ctk.CTkFont(size=11, weight="bold"),
                        fg_color=("#bfdbfe", "#1e40af"),
                        corner_radius=12,
                        padx=10,
                        pady=4
                    )
                    count_label.grid(row=0, column=4, sticky="e", padx=(15, 15))

                    # Campaigns frame (can be hidden)
                    campaigns_container = ctk.CTkFrame(
                        game_frame, fg_color="transparent"
                    )
                    campaigns_container.grid(row=1, column=0, sticky="ew")
                    campaigns_container.grid_columnconfigure(0, weight=1)

                    # Fonction toggle
                    def toggle_collapse(
                        event=None,
                        icon=collapse_icon,
                        container=campaigns_container,
                        var=is_expanded,
                    ):
                        if var.get():
                            container.grid_remove()
                            icon.configure(text="▶")
                            var.set(False)
                        else:
                            container.grid()
                            icon.configure(text="▼")
                            var.set(True)

                    # Make header clickable
                    game_header.bind("<Button-1>", toggle_collapse)
                    game_label.bind("<Button-1>", toggle_collapse)
                    collapse_icon.bind("<Button-1>", toggle_collapse)
                    count_label.bind("<Button-1>", toggle_collapse)
                    # Bind img_label if it exists
                    for widget in game_header.winfo_children():
                        if isinstance(widget, ctk.CTkLabel) and hasattr(
                            widget, "image"
                        ):
                            widget.bind("<Button-1>", toggle_collapse)

                    # Display active campaigns first
                    camp_idx = 0
                    for campaign in active_campaigns:
                        self._create_campaign_display(campaigns_container, campaign, camp_idx, scrollable_frame, game_data, status_label)
                        camp_idx += 1
                    
                    # Display completed campaigns in a collapsible section
                    if game_completed_campaigns:
                        # Add separator if there are active campaigns
                        if active_campaigns:
                            separator = ctk.CTkFrame(campaigns_container, fg_color="transparent", height=2)
                            separator.grid(row=camp_idx, column=0, sticky="ew", padx=8, pady=6)
                            camp_idx += 1
                        
                        # Collapsible header for completed campaigns
                        completed_header_frame = ctk.CTkFrame(
                            campaigns_container,
                            fg_color=("gray85", "#2d3748"),
                            corner_radius=8,
                            cursor="hand2"
                        )
                        completed_header_frame.grid(row=camp_idx, column=0, sticky="ew", padx=8, pady=6)
                        completed_header_frame.grid_columnconfigure(1, weight=1)
                        
                        completed_expanded = tk.BooleanVar(value=False)  # Collapsed by default
                        
                        completed_collapse_icon = ctk.CTkLabel(
                            completed_header_frame,
                            text="▶",
                            font=ctk.CTkFont(size=12, weight="bold"),
                            text_color=("gray60", "gray40")
                        )
                        completed_collapse_icon.grid(row=0, column=0, padx=(12, 8), pady=8)
                        
                        completed_header_label = ctk.CTkLabel(
                            completed_header_frame,
                            text=f"{self.t('drops_completed_campaigns')} ({len(game_completed_campaigns)})",
                            font=ctk.CTkFont(size=12, weight="bold"),
                            text_color=("gray60", "gray40")
                        )
                        completed_header_label.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=8)
                        
                        # Container for completed campaigns
                        completed_container = ctk.CTkFrame(
                            campaigns_container,
                            fg_color="transparent"
                        )
                        completed_container.grid(row=camp_idx + 1, column=0, sticky="ew")
                        completed_container.grid_columnconfigure(0, weight=1)
                        completed_container.grid_remove()  # Hidden by default
                        
                        def toggle_completed(event=None):
                            if completed_expanded.get():
                                completed_container.grid_remove()
                                completed_collapse_icon.configure(text="▶")
                                completed_expanded.set(False)
                            else:
                                completed_container.grid()
                                completed_collapse_icon.configure(text="▼")
                                completed_expanded.set(True)
                        
                        completed_header_frame.bind("<Button-1>", toggle_completed)
                        completed_collapse_icon.bind("<Button-1>", toggle_completed)
                        completed_header_label.bind("<Button-1>", toggle_completed)
                        
                        # Display completed campaigns
                        for comp_idx, campaign in enumerate(game_completed_campaigns):
                            self._create_campaign_display(completed_container, campaign, comp_idx, scrollable_frame, game_data, status_label)
                        
                        camp_idx += 2  # Skip header and container rows
                    
                    row_idx += 1
                
                # Display expired campaigns section if toggle is on
                if expired_campaigns and hasattr(scrollable_frame, "_show_expired_var") and scrollable_frame._show_expired_var.get():
                        expired_separator = ctk.CTkFrame(scrollable_frame, fg_color=("gray70", "gray30"), height=2)
                        expired_separator.grid(row=row_idx, column=0, sticky="ew", padx=0, pady=15)
                        row_idx += 1
                        
                        expired_label = ctk.CTkLabel(
                            scrollable_frame,
                            text=f"⏰ Expired Campaigns ({len(expired_campaigns)})",
                            font=ctk.CTkFont(size=14, weight="bold"),
                            text_color=("#6b7280", "#9ca3af"),
                        )
                        expired_label.grid(row=row_idx, column=0, sticky="w", padx=15, pady=10)
                        row_idx += 1
                        
                        for exp_idx, campaign in enumerate(expired_campaigns):
                            self._create_campaign_display(scrollable_frame, campaign, exp_idx, scrollable_frame, {"image": ""}, status_label)
                            row_idx += 1
                
                # Force update
                scrollable_frame.update_idletasks()
            except Exception as e:
                status_label.configure(text=f"Error: {str(e)}")
                import traceback
                traceback.print_exc()
            finally:
                # Close driver after displaying all campaigns
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass

        # Call on UI thread in background to avoid blocking
        threading.Thread(target=display_campaigns, daemon=True).start()

    def _auto_find_streamers_for_game(self, campaign, category_id, scrollable_frame, status_label):
        """Auto-find and add live streamers for a global drop campaign"""
        def find_and_add():
            game_name = campaign.get('game', 'game')
            debug_print(f"DEBUG: Starting search for live streamers")
            debug_print(f"DEBUG: Campaign: {campaign.get('name', 'unknown')}")
            debug_print(f"DEBUG: Game: {game_name}")
            debug_print(f"DEBUG: Category ID: {category_id}")
            
            status_label.configure(text=f"🔍 Searching for live streamers of {game_name}...")
            
            # Use existing driver from drops window if available, or create new one
            driver = None
            try:
                debug_print("DEBUG: Attempting to get driver from drops fetch...")
                # Try to get driver from current drops fetch
                result = fetch_drops_campaigns_and_progress()
                driver = result.get("driver")
                if driver:
                    debug_print("DEBUG: Reusing existing driver")
                else:
                    debug_print("DEBUG: No existing driver, will create new one")
            except Exception as e:
                debug_print(f"DEBUG: Error getting driver: {e}")
                pass
            
            debug_print(f"DEBUG: Calling fetch_live_streamers_by_category with category_id={category_id}")
            streamers = fetch_live_streamers_by_category(category_id, limit=24, driver=driver)
            debug_print(f"DEBUG: Found {len(streamers)} streamers")
            
            if not streamers:
                status_label.configure(text=f"❌ No live streamers found for {game_name}")
                debug_print(f"DEBUG: No streamers found, closing driver if needed")
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
                return
            
            debug_print(f"DEBUG: Processing {len(streamers)} streamers as alternatives")
            status_label.configure(text=f"📝 Adding drop with {len(streamers)} live streamer option(s)...")

            campaign = dict(campaign)
            campaign["channels"] = [
                {"url": s["url"], "username": s.get("username", "")}
                for s in streamers
            ]
            added = self._add_or_update_drop_task(
                campaign,
                preferred_url=streamers[0]["url"],
                is_global_drop=True,
            )

            debug_print(f"DEBUG: Added global drop task: {added}")
            self.refresh_list()
            if added:
                status_label.configure(text=f"✅ Added drop for {game_name} with {len(streamers)} streamer option(s)")
            else:
                status_label.configure(text=f"❌ Could not add drop for {game_name}")
            
            # Auto-start if enabled
            if self.config_data.auto_start and not self.queue_running:
                debug_print("DEBUG: Auto-start enabled, starting queue")
                self.after(500, self._auto_start_queue)
            else:
                debug_print("DEBUG: Auto-start disabled or queue already running")
            
            if driver:
                try:
                    debug_print("DEBUG: Closing driver")
                    driver.quit()
                except Exception as e:
                    debug_print(f"DEBUG: Error closing driver: {e}")
        
        threading.Thread(target=find_and_add, daemon=True).start()

    def _create_campaign_display(self, parent, campaign, camp_idx, scrollable_frame, game_data, status_label=None):
        """Helper function to create a campaign display frame"""
        try:
            campaign_frame = ctk.CTkFrame(
                parent,
                corner_radius=10,
                fg_color=("white", "#1f2937"),
                border_width=1,
                border_color=("#d1d5db", "#374151")
            )
            campaign_frame.grid(
                row=camp_idx, column=0, sticky="ew", padx=8, pady=6
            )
            campaign_frame.grid_columnconfigure(0, weight=1)

            # Campaign header - improved style
            header = ctk.CTkFrame(campaign_frame, fg_color="transparent")
            header.grid(row=0, column=0, sticky="ew", padx=15, pady=(12, 8))
            header.grid_columnconfigure(1, weight=1)

            campaign_name_label = ctk.CTkLabel(
                header,
                text=campaign["name"],
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w"
            )
            campaign_name_label.grid(
                row=0, column=0, columnspan=2, sticky="w"
            )

            # Status badge - show progress status if available
            progress_status = campaign.get("progress_status", "not_started")
            if progress_status == "not_started":
                status_text = campaign["status"].upper()
                status_color = ("#10b981", "#059669") if campaign["status"] == "active" else ("#6b7280", "#4b5563")
            elif progress_status == "in progress":
                status_text = "IN PROGRESS"
                status_color = ("#f59e0b", "#d97706")
            elif progress_status == "claimed":
                status_text = "CLAIMED"
                status_color = ("#10b981", "#059669")
            else:
                status_text = campaign["status"].upper()
                status_color = ("#6b7280", "#4b5563")
            
            status_badge = ctk.CTkLabel(
                header,
                text=status_text,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color=status_color,
                text_color="white",
                corner_radius=6,
                padx=10,
                pady=4,
            )
            status_badge.grid(row=0, column=2, sticky="e")

            # Display rewards (drops) with images
            rewards = campaign.get("rewards", [])
            if rewards:
                rewards_frame = ctk.CTkFrame(
                    campaign_frame, 
                    fg_color=("gray90", "#111827"),
                    corner_radius=8
                )
                rewards_frame.grid(
                    row=1, column=0, sticky="ew", padx=15, pady=(0, 10)
                )
                rewards_frame.grid_columnconfigure(1, weight=1)

                rewards_label = ctk.CTkLabel(
                    rewards_frame,
                    text="🎁 Rewards:",
                    font=ctk.CTkFont(size=12, weight="bold"),
                    text_color=("#7c3aed", "#a78bfa")
                )
                rewards_label.grid(row=0, column=0, sticky="w", padx=(12, 10), pady=10)

                # Horizontal frame for drop images
                images_frame = ctk.CTkFrame(
                    rewards_frame, fg_color="transparent"
                )
                images_frame.grid(row=0, column=1, sticky="w", pady=10, padx=(0, 12))

                for rew_idx, reward in enumerate(
                    rewards[:6]
                ):  # Max 6 rewards shown
                    try:
                        # Build complete image URL
                        reward_img_url = reward.get("image_url", "")
                        if reward_img_url and not reward_img_url.startswith(
                            "http"
                        ):
                            reward_img_url = (
                                f"https://ext.cdn.kick.com/{reward_img_url}"
                            )

                        if reward_img_url:
                            # CDN images - use simple urllib request with headers
                            try:
                                req = urllib.request.Request(
                                    reward_img_url,
                                    headers={
                                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                                        "Referer": "https://kick.com/"
                                    }
                                )
                                with urllib.request.urlopen(req, timeout=5) as response:
                                    img_data = response.read()

                                rew_img = Image.open(BytesIO(img_data))
                                rew_img = rew_img.resize(
                                    (50, 50), Image.Resampling.LANCZOS
                                )
                                rew_photo = ctk.CTkImage(
                                    light_image=rew_img,
                                    dark_image=rew_img,
                                    size=(50, 50),
                                )

                                reward_name = reward.get(
                                    "name", "Unknown"
                                )
                                required_mins = reward.get(
                                    "required_units", 0
                                )
                                
                                # Get progress info if available
                                progress = reward.get("progress", 0.0)
                                claimed = reward.get("claimed", False)
                                progress_units = campaign.get("progress_units", 0)
                                
                                # Build tooltip with progress info
                                if progress > 0 or claimed:
                                    progress_percent = int(progress * 100)
                                    if claimed:
                                        tooltip_text = f"{reward_name}\n⏱️ {required_mins} minutes\n✓ CLAIMED ({progress_percent}%)"
                                    else:
                                        tooltip_text = f"{reward_name}\n⏱️ {required_mins} minutes\n📊 {progress_percent}% ({progress_units}/{required_mins})"
                                else:
                                    tooltip_text = f"{reward_name}\n⏱️ {required_mins} minutes\n⏸️ Not started"

                                # Frame with border for each reward - change border color if claimed
                                border_color = ("#10b981", "#059669") if claimed else ("#f59e0b", "#d97706") if progress > 0 else ("#d1d5db", "#374151")
                                border_width = 3 if claimed or progress > 0 else 2
                                
                                rew_container = ctk.CTkFrame(
                                    images_frame,
                                    fg_color=("white", "#0f172a"),
                                    border_width=border_width,
                                    border_color=border_color,
                                    corner_radius=8,
                                    width=60,
                                    height=60
                                )
                                rew_container.grid(row=0, column=rew_idx, padx=4)
                                rew_container.grid_propagate(False)
                                
                                rew_label = ctk.CTkLabel(
                                    rew_container,
                                    image=rew_photo,
                                    text="",
                                )
                                rew_label.image = rew_photo
                                rew_label.place(relx=0.5, rely=0.5, anchor="center")
                                
                                # Add claimed checkmark overlay if claimed
                                if claimed:
                                    claimed_overlay = ctk.CTkLabel(
                                        rew_container,
                                        text="✓",
                                        font=ctk.CTkFont(size=16, weight="bold"),
                                        text_color="#10b981",
                                        fg_color="transparent"
                                    )
                                    claimed_overlay.place(relx=0.85, rely=0.15, anchor="center")

                                # Add tooltip (drop name on hover) - on container for better functionality
                                self._create_tooltip(rew_container, tooltip_text)
                                self._create_tooltip(rew_label, tooltip_text)
                            except Exception:
                                pass  # Silently skip images that fail to load
                    except Exception:
                        pass

            # Participating channels - improved style
            channels_frame = ctk.CTkFrame(
                campaign_frame, fg_color="transparent"
            )
            channels_frame.grid(
                row=2, column=0, sticky="ew", padx=15, pady=(0, 12)
            )
            channels_frame.grid_columnconfigure(0, weight=1)
            
            # Store widget references (defined before if/else to avoid scope error)
            channel_buttons = []

            if not campaign["channels"]:
                # Global drop - show option to auto-find streamers
                global_drop_frame = ctk.CTkFrame(channels_frame, fg_color="transparent")
                global_drop_frame.grid(row=0, column=0, sticky="ew", pady=5)
                global_drop_frame.grid_columnconfigure(0, weight=1)
                
                no_channels_label = ctk.CTkLabel(
                    global_drop_frame,
                    text=self.t("drops_no_channels"),
                    text_color=("#6b7280", "#9ca3af"),
                    font=ctk.CTkFont(size=11, slant="italic"),
                )
                no_channels_label.grid(row=0, column=0, sticky="w")
                
                # Button to auto-find streamers for this game
                # Get category_id from campaign (from progress API or campaigns API)
                category = campaign.get("category", {})
                category_id = category.get("id") if isinstance(category, dict) else None
                
                # Also check in progress_data if category not found
                if not category_id:
                    progress_data = campaign.get("progress_data", {})
                    if isinstance(progress_data, dict):
                        progress_category = progress_data.get("category", {})
                        if isinstance(progress_category, dict):
                            category_id = progress_category.get("id")
                
                # Try alternative structure (if category is not nested)
                if not category_id:
                    category_id = campaign.get("category_id")
                
                # Always show button, but disable if no category_id
                def find_streamers(c=campaign, cid=category_id, sl=status_label):
                    if not cid:
                        if sl:
                            sl.configure(text="Error: No category_id found for this campaign")
                        debug_print(f"DEBUG: Campaign structure: {list(c.keys())}")
                        debug_print(f"DEBUG: Category: {c.get('category')}")
                        debug_print(f"DEBUG: Progress data: {c.get('progress_data', {}).get('category') if isinstance(c.get('progress_data'), dict) else 'N/A'}")
                        return
                    if sl:
                        self._auto_find_streamers_for_game(c, cid, scrollable_frame, sl)
                    else:
                        debug_print("DEBUG: No status_label available")
                
                find_btn = ctk.CTkButton(
                    global_drop_frame,
                    text="🔍 Find Live Streamers",
                    width=180,
                    height=30,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    fg_color=("#10b981", "#059669") if category_id else ("#6b7280", "#4b5563"),
                    hover_color=("#059669", "#047857") if category_id else ("#4b5563", "#374151"),
                    command=find_streamers,
                    state="normal" if category_id else "disabled",
                )
                find_btn.grid(row=0, column=1, padx=(10, 0), sticky="e")
                
                if not category_id:
                    debug_print(f"DEBUG: No category_id found for campaign {campaign.get('name', 'unknown')}")
                    debug_print(f"DEBUG: Campaign keys: {list(campaign.keys())}")
                    debug_print(f"DEBUG: Category value: {campaign.get('category')}")
            else:
                # List of channels with buttons - improved design
                for ch_idx, channel in enumerate(campaign["channels"][:5]):
                    channel_url = channel["url"]
                    is_added = self._is_channel_in_list(channel_url)
                    
                    channel_row = ctk.CTkFrame(
                        channels_frame, 
                        fg_color=("gray95", "#1f2937"),
                        corner_radius=6
                    )
                    channel_row.grid(
                        row=ch_idx, column=0, sticky="ew", pady=3
                    )
                    channel_row.grid_columnconfigure(0, weight=1)

                    # Icon according to state, but text always normal
                    icon = "✓" if is_added else "📺"
                    ch_label = ctk.CTkLabel(
                        channel_row,
                        text=f"{icon} {channel['username']}",
                        font=ctk.CTkFont(size=12),
                        anchor="w"
                    )
                    ch_label.grid(row=0, column=0, sticky="w", padx=(12, 10), pady=8)

                    # Add or Remove button depending on state
                    action_btn = ctk.CTkButton(
                        channel_row,
                        text="✗ Remove" if is_added else "+ Add",
                        width=90,
                        height=28,
                        font=ctk.CTkFont(size=11, weight="bold"),
                        fg_color=("#ef4444", "#dc2626") if is_added else ("#3b82f6", "#2563eb"),
                        hover_color=("#dc2626", "#b91c1c") if is_added else ("#2563eb", "#1d4ed8"),
                        corner_radius=6,
                    )
                    action_btn.grid(row=0, column=1, sticky="e", padx=8, pady=4)
                    
                    # Store reference to this button
                    channel_buttons.append((channel_url, action_btn, ch_label, channel['username']))
                    
                    # Function to toggle button state
                    def toggle_channel(url=channel_url, btn=action_btn, label=ch_label, username=channel['username'], camp=campaign):
                        if self._is_channel_in_list(url):
                            # Remove
                            self._remove_drop_channel(url)
                            # Update button and label (icon only)
                            btn.configure(
                                text="+ Add",
                                fg_color=("#3b82f6", "#2563eb"),
                                hover_color=("#2563eb", "#1d4ed8")
                            )
                            label.configure(text=f"📺 {username}")
                        else:
                            # Add
                            self._add_drop_channel(url, 120, camp)
                            # Update button and label (icon only)
                            btn.configure(
                                text="✗ Remove",
                                fg_color=("#ef4444", "#dc2626"),
                                hover_color=("#dc2626", "#b91c1c")
                            )
                            label.configure(text=f"✓ {username}")
                    
                    action_btn.configure(command=toggle_channel)

                # "Add/Remove All Channels" button - toggle based on state
                add_all_btn = None
                if len(campaign["channels"]) > 1:
                    # Check if all channels are added
                    all_added = all(self._is_channel_in_list(ch['url']) for ch in campaign["channels"])
                    
                    add_all_btn = ctk.CTkButton(
                        channels_frame,
                        text=f"✨ {self.t('btn_remove_all_channels')}" if all_added else f"✨ {self.t('btn_add_all_channels')}",
                        height=32,
                        font=ctk.CTkFont(size=12, weight="bold"),
                        fg_color=("#ef4444", "#dc2626") if all_added else ("#10b981", "#059669"),
                        hover_color=("#dc2626", "#b91c1c") if all_added else ("#059669", "#047857"),
                        corner_radius=8,
                    )
                    add_all_btn.grid(
                        row=len(campaign["channels"][:5]),
                        column=0,
                        sticky="ew",
                        pady=(8, 0),
                    )
                    
                    # Function for add/remove all with individual button updates
                    def toggle_all_channels(c=campaign, bulk_btn=add_all_btn, btn_refs=channel_buttons):
                        # Check if all are added
                        all_added = all(self._is_channel_in_list(ch['url']) for ch in c["channels"])
                        
                        if all_added:
                            # Remove all
                            for ch in c["channels"]:
                                self._remove_drop_channel(ch['url'])
                            # Update bulk button
                            bulk_btn.configure(
                                text=f"✨ {translate(self.config_data.language, 'btn_add_all_channels')}",
                                fg_color=("#10b981", "#059669"),
                                hover_color=("#059669", "#047857")
                            )
                            # Update all displayed individual buttons
                            for url, btn, label, username in btn_refs:
                                btn.configure(
                                    text="+ Add",
                                    fg_color=("#3b82f6", "#2563eb"),
                                    hover_color=("#2563eb", "#1d4ed8")
                                )
                                label.configure(text=f"📺 {username}")
                        else:
                            # Add all
                            self._add_all_campaign_channels(c)
                            # Update bulk button
                            bulk_btn.configure(
                                text=f"✨ {translate(self.config_data.language, 'btn_remove_all_channels')}",
                                fg_color=("#ef4444", "#dc2626"),
                                hover_color=("#dc2626", "#b91c1c")
                            )
                            # Update all displayed individual buttons
                            for url, btn, label, username in btn_refs:
                                btn.configure(
                                    text="✗ Remove",
                                    fg_color=("#ef4444", "#dc2626"),
                                    hover_color=("#dc2626", "#b91c1c")
                                )
                                label.configure(text=f"✓ {username}")
                    
                    add_all_btn.configure(command=toggle_all_channels)
                
                # Now configure individual button commands (with access to bulk_btn)
                for url, btn, label, username in channel_buttons:
                    def make_toggle(url=url, btn=btn, label=label, username=username, c=campaign, bulk_btn=add_all_btn, btn_refs=channel_buttons):
                        def toggle():
                            if self._is_channel_in_list(url):
                                # Remove
                                self._remove_drop_channel(url)
                                btn.configure(
                                    text="+ Add",
                                    fg_color=("#3b82f6", "#2563eb"),
                                    hover_color=("#2563eb", "#1d4ed8")
                                )
                                label.configure(text=f"📺 {username}")
                            else:
                                # Add
                                self._add_drop_channel(url, 120, c)
                                btn.configure(
                                    text="✗ Remove",
                                    fg_color=("#ef4444", "#dc2626"),
                                    hover_color=("#dc2626", "#b91c1c")
                                )
                                label.configure(text=f"✓ {username}")
                            
                            # Check if all channels are now added and update bulk button
                            if bulk_btn:
                                all_now_added = all(self._is_channel_in_list(ch['url']) for ch in c["channels"])
                                if all_now_added:
                                    bulk_btn.configure(
                                        text=f"✨ {translate(self.config_data.language, 'btn_remove_all_channels')}",
                                        fg_color=("#ef4444", "#dc2626"),
                                        hover_color=("#dc2626", "#b91c1c")
                                    )
                                else:
                                    bulk_btn.configure(
                                        text=f"✨ {translate(self.config_data.language, 'btn_add_all_channels')}",
                                        fg_color=("#10b981", "#059669"),
                                        hover_color=("#059669", "#047857")
                                    )
                        return toggle
                    
                    btn.configure(command=make_toggle())
        except Exception as e:
            print(f"Error creating campaign display: {e}")
            import traceback
            traceback.print_exc()

    def _setup_progress_tab(self, parent, drops_window):
        """Sets up the progress tab UI"""
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)
        
        # Header with refresh button
        header_frame = ctk.CTkFrame(parent, fg_color=("gray86", "gray17"), corner_radius=0, height=60)
        header_frame.grid(row=0, column=0, sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)
        header_frame.grid_propagate(False)
        
        status_label = ctk.CTkLabel(
            header_frame, text=self.t("drops_progress_loading"),
            font=ctk.CTkFont(size=16, weight="bold")
        )
        status_label.grid(row=0, column=0, sticky="w", padx=20, pady=15)
        
        refresh_btn = ctk.CTkButton(
            header_frame,
            text=self.t("btn_refresh_progress"),
            width=130,
            height=35,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=("#3b82f6", "#2563eb"),
            hover_color=("#2563eb", "#1d4ed8"),
            command=lambda: self._refresh_progress(scrollable_frame, status_label),
        )
        refresh_btn.grid(row=0, column=1, padx=20, pady=15)
        
        # Scrollable frame for progress
        scrollable_frame = ctk.CTkScrollableFrame(
            parent,
            label_text="",
            fg_color=("gray92", "gray14")
        )
        scrollable_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=15)
        scrollable_frame.grid_columnconfigure(0, weight=1)
        
        # Initial load
        self._refresh_progress(scrollable_frame, status_label)
        
        # Bring window to front after loading
        def load_and_focus():
            try:
                drops_window.lift()
                drops_window.focus_force()
            except:
                pass
        
        threading.Thread(target=load_and_focus, daemon=True).start()

    def _refresh_progress(self, scrollable_frame, status_label):
        """Fetches and displays drop progress"""
        # Clear existing content
        def clear_frame():
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            status_label.configure(text=self.t("drops_progress_loading"))
        
        self.after(0, clear_frame)
        
        def display_progress():
            try:
                result = fetch_drops_progress()
                progress_data = result.get("progress", [])
                progress_data = [p for p in progress_data if isinstance(p, dict)]
                driver = result.get("driver")
                
                try:
                    if not progress_data:
                        def show_error():
                            status_label.configure(text=self.t("drops_progress_error"))
                            no_data_label = ctk.CTkLabel(
                                scrollable_frame,
                                text=self.t("drops_progress_no_data"),
                                font=ctk.CTkFont(size=12),
                                text_color="gray",
                            )
                            no_data_label.grid(row=0, column=0, pady=20)
                        self.after(0, show_error)
                        return
                    
                    # Group by status
                    in_progress = [p for p in progress_data if p.get("status") == "in progress"]
                    claimed = [p for p in progress_data if p.get("status") == "claimed"]
                    
                    total = len(progress_data)
                    active = len(in_progress)
                    
                    def update_ui():
                        status_label.configure(
                            text=self.t("drops_progress_loaded", total=total, active=active)
                        )
                        
                        row_idx = 0
                        
                        # Display in-progress campaigns
                        if in_progress:
                            section_label = ctk.CTkLabel(
                                scrollable_frame,
                                text=self.t("drops_progress_in_progress"),
                                font=ctk.CTkFont(size=14, weight="bold"),
                            )
                            section_label.grid(row=row_idx, column=0, sticky="w", padx=20, pady=(20, 10))
                            row_idx += 1
                            
                            for campaign in in_progress:
                                self._create_progress_card(scrollable_frame, campaign, row_idx)
                                row_idx += 1
                        
                        # Display claimed campaigns
                        if claimed:
                            if in_progress:
                                row_idx += 1  # Spacing
                            
                            section_label = ctk.CTkLabel(
                                scrollable_frame,
                                text=self.t("drops_progress_claimed"),
                                font=ctk.CTkFont(size=14, weight="bold"),
                            )
                            section_label.grid(row=row_idx, column=0, sticky="w", padx=20, pady=(20, 10))
                            row_idx += 1
                            
                            for campaign in claimed:
                                self._create_progress_card(scrollable_frame, campaign, row_idx)
                                row_idx += 1
                    
                    self.after(0, update_ui)
                            
                finally:
                    # Close driver after UI is rendered
                    if driver:
                        try:
                            driver.quit()
                        except:
                            pass
                            
            except Exception as e:
                print(f"Error displaying progress: {e}")
                import traceback
                traceback.print_exc()
                def show_error():
                    status_label.configure(text=self.t("drops_progress_error"))
                self.after(0, show_error)
        
        # Run in thread to avoid blocking UI
        threading.Thread(target=display_progress, daemon=True).start()

    def _create_progress_card(self, parent, campaign, row):
        """Creates a card displaying campaign progress"""
        card_frame = ctk.CTkFrame(parent, corner_radius=10)
        card_frame.grid(row=row, column=0, sticky="ew", padx=20, pady=10)
        card_frame.grid_columnconfigure(0, weight=1)
        
        # Campaign name
        name_label = ctk.CTkLabel(
            card_frame,
            text=campaign.get("name", "Unknown Campaign"),
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        name_label.grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=(15, 5))
        
        # Game info
        category = campaign.get("category", {})
        game_label = ctk.CTkLabel(
            card_frame,
            text=f"Game: {category.get('name', 'Unknown')}",
            font=ctk.CTkFont(size=12),
        )
        game_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=15, pady=5)
        
        # Status badge
        status = campaign.get("status", "unknown")
        status_color = "#10b981" if status == "claimed" else "#f59e0b"
        status_label = ctk.CTkLabel(
            card_frame,
            text=status.upper(),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=status_color,
        )
        status_label.grid(row=2, column=0, sticky="w", padx=15, pady=5)
        
        # Rewards with progress
        rewards = campaign.get("rewards", [])
        for i, reward in enumerate(rewards):
            reward_frame = ctk.CTkFrame(card_frame, fg_color=("gray90", "gray16"))
            reward_frame.grid(row=3 + i, column=0, columnspan=2, sticky="ew", padx=15, pady=5)
            reward_frame.grid_columnconfigure(1, weight=1)
            
            # Reward name
            reward_name = ctk.CTkLabel(
                reward_frame,
                text=reward.get("name", "Unknown Reward"),
                font=ctk.CTkFont(size=11),
            )
            reward_name.grid(row=0, column=0, sticky="w", padx=10, pady=5)
            
            # Progress information
            progress = reward.get("progress", 0.0)
            required = reward.get("required_units", 0)
            progress_units = campaign.get("progress_units", 0)
            
            progress_percent = int(progress * 100)
            progress_text = f"{progress_percent}% ({progress_units}/{required} units)"
            
            progress_label = ctk.CTkLabel(
                reward_frame,
                text=progress_text,
                font=ctk.CTkFont(size=10),
                text_color="gray",
            )
            progress_label.grid(row=0, column=1, sticky="e", padx=10, pady=5)
            
            # Progress bar
            progress_bar = ctk.CTkProgressBar(reward_frame)
            progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
            progress_bar.set(progress)
            
            # Claimed status
            if reward.get("claimed"):
                claimed_label = ctk.CTkLabel(
                    reward_frame,
                    text="✓ Claimed",
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#10b981",
                )
                claimed_label.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 5))

    def _is_channel_in_list(self, url):
        """Check if a URL is already in the list"""
        return self._find_channel_index(url) is not None
    
    def _find_channel_index(self, url):
        """Find the index of a URL in the list"""
        for idx, item in enumerate(self.config_data.items):
            if item.get("url") == url:
                return idx
            for channel in item.get("campaign_channels", []):
                channel_url = channel.get("url") if isinstance(channel, dict) else channel
                if channel_url == url:
                    return idx
        return None

    def _find_campaign_index(self, campaign_id):
        """Find an existing queued drop by campaign ID."""
        if not campaign_id:
            return None
        for idx, item in enumerate(self.config_data.items):
            if item.get("campaign_id") == campaign_id:
                return idx
        return None

    def _campaign_channel_payload(self, campaign):
        return [
            {
                "url": ch.get("url") if isinstance(ch, dict) else ch,
                "username": ch.get("username", "") if isinstance(ch, dict) else "",
            }
            for ch in campaign.get("channels", [])
            if (ch.get("url") if isinstance(ch, dict) else ch)
        ]

    def _campaign_minutes(self, campaign, default=120):
        minutes = default
        for reward in campaign.get("rewards", []):
            required_units = reward.get("required_units", 0)
            if required_units > minutes:
                minutes = required_units
        return minutes

    def _campaign_category_id(self, campaign):
        category = campaign.get("category", {})
        if isinstance(category, dict) and category.get("id"):
            return category.get("id")
        progress_data = campaign.get("progress_data", {})
        if isinstance(progress_data, dict):
            progress_category = progress_data.get("category", {})
            if isinstance(progress_category, dict):
                return progress_category.get("id")
        return campaign.get("category_id")

    def _campaign_reward_names(self, campaign):
        names = []
        for reward in campaign.get("rewards", []):
            name = reward.get("name")
            if name:
                names.append(name)
        return names

    def _add_or_update_drop_task(self, campaign, preferred_url=None, is_global_drop=False):
        """Queue one drop task with all eligible channels as alternatives."""
        campaign_id = campaign.get("id") if campaign else None
        existing_idx = self._find_campaign_index(campaign_id)
        campaign_channels = self._campaign_channel_payload(campaign) if campaign else []
        if preferred_url:
            selected_url = preferred_url
        elif campaign_channels:
            selected_url = campaign_channels[0]["url"]
        else:
            selected_url = None
        if not selected_url:
            return False

        minutes = self._campaign_minutes(campaign)
        required_category_id = self._campaign_category_id(campaign)
        progress_units = campaign.get("progress_units", 0) if campaign else 0
        claimed = campaign.get("progress_status") == "claimed" if campaign else False
        account_id = self._choose_account_for_new_drop()
        if not account_id:
            return False

        if existing_idx is not None:
            item = self.config_data.items[existing_idx]
            item.update({
                "url": selected_url,
                "minutes": minutes,
                "campaign_channels": campaign_channels,
                "required_category_id": required_category_id,
                "is_global_drop": is_global_drop,
                "campaign_name": campaign.get("name"),
                "game": campaign.get("game"),
                "reward_names": self._campaign_reward_names(campaign),
                "progress_units": progress_units,
                "claimed": claimed,
                "account_id": account_id,
            })
            self.config_data.save()
            return True

        self.config_data.add(
            selected_url,
            minutes,
            campaign_id,
            campaign_channels,
            required_category_id=required_category_id,
            is_global_drop=is_global_drop,
            campaign_name=campaign.get("name"),
            game=campaign.get("game"),
            reward_names=self._campaign_reward_names(campaign),
            progress_units=progress_units,
            claimed=claimed,
            account_id=account_id,
        )
        return True

    def _add_drop_channel(self, url, minutes=120, campaign=None):
        """Add a drop channel to the queue with campaign info"""
        try:
            if campaign:
                if not self._add_or_update_drop_task(campaign, preferred_url=url):
                    return
            else:
                self.config_data.add(url, minutes or 0)
            self.refresh_list()
            if campaign:
                self.status_var.set(f"Added drop: {campaign.get('name', url)}")
            else:
                self.status_var.set(self.t("drops_added", channel=url.split("/")[-1]))
            # Auto-start if enabled and queue not running
            if self.config_data.auto_start and not self.queue_running:
                self.after(500, self._auto_start_queue)
        except Exception as e:
            print(f"Error adding channel: {e}")
    
    def _remove_drop_channel(self, url):
        """Remove a channel from the queue"""
        try:
            idx = self._find_channel_index(url)
            if idx is not None:
                self.config_data.remove(idx)
                if idx in self.workers:
                    self.workers[idx].stop()
                    del self.workers[idx]
                # Re-index workers
                self.workers = {
                    new_i: self.workers[old_i]
                    for new_i, old_i in enumerate(sorted(self.workers.keys()))
                    if old_i < len(self.config_data.items)
                }
                self.refresh_list()
                self.status_var.set(f"Removed: {url.split('/')[-1]}")
        except Exception as e:
            print(f"Error removing channel: {e}")

    def _add_all_campaign_channels(self, campaign):
        """Add one drop task with every campaign channel as an alternative."""
        if not self._add_or_update_drop_task(campaign):
            self.status_var.set(f"Could not add drop: {campaign.get('name', 'unknown')}")
            return

        self.refresh_list()
        self.status_var.set(f"Added drop: {campaign['name']}")
        # Auto-start if enabled and queue not running
        if self.config_data.auto_start and not self.queue_running:
            self.after(500, self._auto_start_queue)

    def _create_tooltip(self, widget, text):
        """Create a tooltip that displays on widget hover"""
        tooltip = None

        def on_enter(event):
            nonlocal tooltip
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() - 10

            tooltip = tk.Toplevel(widget)
            tooltip.wm_overrideredirect(True)
            tooltip.wm_attributes("-topmost", True)
            
            # Frame with shadow (modern effect)
            frame = tk.Frame(
                tooltip,
                background="#1f2937" if self.config_data.dark_mode else "#ffffff",
                relief="flat",
                borderwidth=0
            )
            frame.pack(padx=2, pady=2)
            
            label = tk.Label(
                frame,
                text=text,
                justify="center",
                background="#1f2937" if self.config_data.dark_mode else "#ffffff",
                foreground="#f9fafb" if self.config_data.dark_mode else "#111827",
                font=("Segoe UI", 10, "bold"),
                padx=12,
                pady=8,
            )
            label.pack()
            
            # Center tooltip above widget
            tooltip.update_idletasks()
            tooltip_width = tooltip.winfo_width()
            tooltip.wm_geometry(f"+{x - tooltip_width // 2}+{y - tooltip.winfo_height() - 10}")

        def on_leave(event):
            nonlocal tooltip
            if tooltip:
                tooltip.destroy()
                tooltip = None

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    # ----------- Toggles -----------
    def on_toggle_mute(self):
        self.config_data.mute = bool(self.mute_var.get())
        self.config_data.save()
        for w in list(self.workers.values()):
            try:
                w.mute = self.config_data.mute
                w.ensure_player_state()
            except Exception:
                pass

    def on_toggle_hide(self):
        self.config_data.hide_player = bool(self.hide_player_var.get())
        self.config_data.save()
        for w in list(self.workers.values()):
            try:
                w.hide_player = self.config_data.hide_player
                w.ensure_player_state()
            except Exception:
                pass

    def on_toggle_mini(self):
        self.config_data.mini_player = bool(self.mini_player_var.get())
        self.config_data.save()
        for w in list(self.workers.values()):
            try:
                w.mini_player = self.config_data.mini_player
                w.ensure_player_state()
            except Exception:
                pass

    def on_toggle_force_160p(self):
        self.config_data.force_160p = bool(self.force_160p_var.get())
        self.config_data.save()
        # Note: force_160p only affects new streams (set during initialization)
        # Existing streams will need to be restarted to apply the change

    def on_toggle_auto_start(self):
        self.config_data.auto_start = bool(self.auto_start_var.get())
        self.config_data.save()
        if self.config_data.auto_start and not self.queue_running:
            # Auto-start if enabled and queue not running
            if self.config_data.items:
                self.start_all_in_order()
    

    def _auto_start_queue(self):
        """Auto-start queue on launch if enabled"""
        if not self.queue_running and self.config_data.items:
            # Check if there are any unfinished items
            unfinished = [i for i, item in enumerate(self.config_data.items) 
                         if not item.get("finished")]
            if unfinished:
                self.start_all_in_order()

    def _start_offline_retry_monitor(self):
        """Background thread that periodically checks offline streams and retries them"""
        def monitor():
            while True:
                time.sleep(30)  # Check every 30 seconds
                try:
                    if not self.queue_running:
                        continue
                    
                    # Only check if we're not currently running a stream
                    # (Kick only allows 1 stream at a time)
                    if len(self.workers) > 0:
                        continue
                    
                    # Find next unfinished item
                    for idx, item in enumerate(self.config_data.items):
                        if item.get("finished"):
                            continue
                        
                        if idx in self.workers:
                            continue  # Already running
                        
                        # Check if stream is now live
                        if kick_is_live_by_api(item["url"]):
                            # Stream is back online, retry it
                            self.after(0, lambda item=item: self._add_item_log_entry(item, f"Stream back online, retrying: {self._streamer_name_from_url(item['url'])}"))
                            self.after(0, lambda i=idx: self._start_index(i))
                            break  # Only start one at a time
                except Exception as e:
                    print(f"Monitor error: {e}")
                    time.sleep(60)  # Wait longer on error
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()

    # ----------- Callbacks Worker -----------
    def on_worker_update(self, idx, seconds, live):
        now = time.monotonic()
        last_time, last_live = self._last_worker_ui_update.get(idx, (0, None))
        if now - last_time < 0.9 and last_live == live:
            return
        self._last_worker_ui_update[idx] = (now, live)
        self._maybe_log_drop_progress(idx, now)

        def ui_update():
            if idx < 0 or idx >= len(self.config_data.items):
                return
            
            item = self.config_data.items[idx]
            is_global_drop = item.get("is_global_drop", False)
            
            if str(idx) in self.tree.get_children():
                values = list(self.tree.item(str(idx), "values"))
                values[2] = self._progress_text_for_seconds(item, seconds)
                
                current_tags = set(self.tree.item(str(idx), "tags") or [])
                if live:
                    current_tags.discard("paused")
                else:
                    current_tags.add("paused")
                self.tree.item(str(idx), values=values, tags=tuple(current_tags))
            
            # Update status bar with elapsed time
            if is_global_drop:
                cumulative_seconds = item.get("cumulative_time", 0) + seconds
                cumulative_minutes = cumulative_seconds // 60
                secs = cumulative_seconds % 60
                time_str = f"{cumulative_minutes}m {secs}s" if cumulative_minutes > 0 else f"{secs}s"
                status = self.t("tag_live") if live else self.t("tag_paused")
                
                if self.queue_running and self.queue_current_idx == idx:
                    self.status_var.set(f"{self.t('queue_running_status', url=item['url'])} - {time_str} cumulative ({status})")
                else:
                    self.status_var.set(f"{self.t('status_playing', url=item['url'])} - {time_str} cumulative ({status})")
            else:
                minutes = seconds // 60
                secs = seconds % 60
                time_str = f"{minutes}m {secs}s" if minutes > 0 else f"{secs}s"
                status = self.t("tag_live") if live else self.t("tag_paused")
                
                if self.queue_running and self.queue_current_idx == idx:
                    self.status_var.set(f"{self.t('queue_running_status', url=item['url'])} - {time_str} ({status})")
                else:
                    self.status_var.set(f"{self.t('status_playing', url=item['url'])} - {time_str} ({status})")

        self.after(0, ui_update)

    def _maybe_log_drop_progress(self, idx, now=None):
        if idx < 0 or idx >= len(self.config_data.items):
            return
        now = now or time.monotonic()
        last = self._last_drop_progress_log.get(idx, 0)
        if now - last < 300:
            return
        item = self.config_data.items[idx]
        self._last_drop_progress_log[idx] = now
        self._add_item_log_entry(item, f"Drop {self._drop_title_for_item(item)} is on {self._progress_text_for_item(idx, item)}")

    def on_worker_finish(self, idx, elapsed, completed):
        def ui_finish():
            if idx in self._ignored_finishes:
                self._ignored_finishes.discard(idx)
                return
            if idx < 0 or idx >= len(self.config_data.items):
                return
            if self._complete_manual_skip(idx):
                return

            worker = self.workers.get(idx)
            ended_offline = bool(worker and getattr(worker, "ended_because_offline", False))
            ended_wrong_category = bool(worker and getattr(worker, "ended_because_wrong_category", False))
            if idx in self.workers:
                del self.workers[idx]
            self._last_worker_ui_update.pop(idx, None)
            self._last_drop_progress_log.pop(idx, None)
            
            item = self.config_data.items[idx]
            is_global_drop = item.get("is_global_drop", False)
            campaign_id = item.get("campaign_id")
            elapsed = int(elapsed or 0)
            
            # Initialize completed variable
            # For regular drops, use the value passed from worker
            # For global drops, we'll recalculate based on cumulative time
            completed_value = completed  # Store original value from function parameter
            
            # Track cumulative time for global drops
            if is_global_drop and campaign_id:
                # Add elapsed time to cumulative time for all items in this campaign
                debug_print(f"DEBUG: Global drop - adding {elapsed} seconds to cumulative time")
                for other_item in self.config_data.items:
                    if other_item.get("campaign_id") == campaign_id:
                        current_cumulative = other_item.get("cumulative_time", 0)
                        other_item["cumulative_time"] = current_cumulative + elapsed
                        other_item["watched_seconds"] = other_item["cumulative_time"]
                        debug_print(f"DEBUG: Item {other_item['url']} cumulative time: {other_item['cumulative_time']}s")
                self.config_data.save()
                
                # Check if cumulative time reached target
                target_minutes = item.get("minutes", 0)
                cumulative_seconds = item.get("cumulative_time", 0)
                cumulative_minutes = cumulative_seconds // 60
                
                debug_print(f"DEBUG: Cumulative time: {cumulative_minutes} minutes / {target_minutes} minutes target")
                
                if target_minutes > 0 and cumulative_minutes >= target_minutes:
                    # Mark all items in campaign as finished
                    debug_print(f"DEBUG: Target reached! Marking all items in campaign as finished")
                    for other_item in self.config_data.items:
                        if other_item.get("campaign_id") == campaign_id:
                            other_item["finished"] = True
                    self.config_data.save()
                    completed_value = True
                else:
                    # Not finished yet, continue with other streamers
                    completed_value = False
                    debug_print(f"DEBUG: Still need {target_minutes - cumulative_minutes} more minutes")
            elif elapsed > 0:
                item["watched_seconds"] = int(item.get("watched_seconds", 0) or 0) + elapsed
                self.config_data.save()
            
            # Use completed_value (always defined - either from function parameter or recalculated for global drops)
            if completed_value:
                self._add_item_log_entry(
                    item,
                    f"Finished watching {self._drop_title_for_item(item)} at {self._progress_text_for_seconds(item, 0)}",
                )
                if not is_global_drop:
                    # Regular drop - mark individual item as finished
                    self.config_data.items[idx]["finished"] = True
                    self.config_data.save()
                # Reset tried_channels on successful completion
                self.config_data.items[idx]["tried_channels"] = []
                self.config_data.save()
                if str(idx) in self.tree.get_children():
                    values = list(self.tree.item(str(idx), "values"))
                    if is_global_drop:
                        cumulative_minutes = item.get("cumulative_time", 0) // 60
                        values[2] = f"{self._format_duration(item.get('cumulative_time', 0))} ({self.t('tag_finished')})"
                    else:
                        values[2] = f"{self._progress_text_for_seconds(item, 0)} ({self.t('tag_finished')})"
                    current_tags = set(self.tree.item(str(idx), "tags") or [])
                    current_tags.add("finished")
                    current_tags.discard("paused")
                    current_tags.discard("redo")
                    self.tree.item(str(idx), values=values, tags=tuple(current_tags))
                if campaign_id:
                    self._sync_claimed_drop_after_finish(campaign_id, item.get("account_id"))
            elif ended_offline or ended_wrong_category:
                reason = "wrong category" if ended_wrong_category else "streamer went offline"
                self._add_item_log_entry(item, f"{reason.capitalize()} for {self._streamer_name_from_url(item['url'])}")
                # Try alternative channel from same campaign
                campaign_channels = item.get("campaign_channels", [])
                
                switched = False
                if campaign_id and campaign_channels:
                    current_url = item["url"]
                    tried_channels = item.get("tried_channels", [])
                    
                    # Add current URL to tried list if not already there
                    if current_url not in tried_channels:
                        tried_channels.append(current_url)
                    
                    # Get all channel URLs
                    all_channel_urls = []
                    for ch in campaign_channels:
                        ch_url = ch.get("url") if isinstance(ch, dict) else ch
                        if ch_url:
                            all_channel_urls.append(ch_url)
                    
                    # Also include current URL in the list
                    if current_url not in all_channel_urls:
                        all_channel_urls.append(current_url)
                    
                    # If we've tried all channels, reset the tried list
                    if len(tried_channels) >= len(all_channel_urls):
                        tried_channels.clear()
                        debug_print(f"DEBUG: Reset tried_channels for campaign {campaign_id} - all channels exhausted")
                    
                    # Find next available live channel from same campaign that hasn't been tried
                    for alt_channel in campaign_channels:
                        alt_url = alt_channel.get("url") if isinstance(alt_channel, dict) else alt_channel
                        if alt_url and alt_url != current_url and alt_url not in tried_channels:
                            # Check if this alternative is live
                            if kick_is_live_by_api(alt_url):
                                # Switch to this alternative channel
                                self.config_data.items[idx]["url"] = alt_url
                                tried_channels.append(alt_url)  # Mark as tried
                                item["tried_channels"] = tried_channels  # Update item
                                self.config_data.save()
                                self.refresh_list()
                                switched = True
                                debug_print(f"DEBUG: Switched to alternative: {alt_url} (tried: {len(tried_channels)}/{len(all_channel_urls)})")
                                self.status_var.set(f"Switched to alternative: {alt_url.split('/')[-1]} - waiting for page to load...")
                                self._add_item_log_entry(item, f"Streamer went offline, moving to next: {self._streamer_name_from_url(alt_url)}", alt_url)
                                
                                # Retry with new channel if queue is running
                                # Wait 8 seconds to allow browser to fully load the new stream
                                if getattr(self, "queue_running", False):
                                    self.after(8000, lambda i=idx: self._start_index(i))
                                    return
                                break
                    
                    # If no live alternative found, but we haven't tried all channels, mark current as tried and wait
                    if not switched and len(tried_channels) < len(all_channel_urls):
                        item["tried_channels"] = tried_channels  # Update tried list even if no switch
                        self.config_data.save()
                        debug_print(f"DEBUG: No live alternatives found, but {len(all_channel_urls) - len(tried_channels)} channels remain untried")
                
                if not switched:
                    self._add_item_log_entry(item, f"No live alternative found for {self._drop_title_for_item(item)}; waiting to retry")
                    # No alternative found, mark for retry
                    if str(idx) in self.tree.get_children():
                        values = list(self.tree.item(str(idx), "values"))
                        values[2] = f"{elapsed}s ({self.t('retry')})"
                        current_tags = set(self.tree.item(str(idx), "tags") or [])
                        current_tags.add("redo")
                        current_tags.discard("paused")
                        current_tags.discard("finished")
                        self.tree.item(str(idx), values=values, tags=tuple(current_tags))
                    try:
                        self.status_var.set(
                            self.t("offline_wait_retry", url=self.config_data.items[idx]["url"])
                        )
                    except Exception:
                        pass

            # Continue queue if applicable
            if getattr(self, "queue_running", False) and self.queue_current_idx == idx:
                self._run_queue_from(idx + 1)

        self.after(0, ui_finish)
