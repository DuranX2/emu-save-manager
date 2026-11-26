"""
apptheme.py - the ROM Library's own look: a dark teal theme with amber highlights, matching its
icon and Steam artwork, so it's never confused with the Emulator Save Manager (which keeps the
system's standard look and its blue artwork).

    import apptheme
    apptheme.apply_library(root)        # once, on the Tk root, before building widgets

Uses ttk's "clam" theme (available everywhere) recoloured, plus option-database defaults for the
plain Tk widgets (menus, canvases, text, listboxes) and for dialogs opened later.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# palette
BG = "#10201f"           # window
PANEL = "#162b29"        # lists, panels
FIELD = "#1d3533"        # entries, list backgrounds
RAISED = "#21413d"       # buttons
HOVER = "#2a5650"
LINE = "#2f4d49"
TEXT = "#e7f3f0"
DIM = "#9dbbb4"
ACCENT = "#2fb9a3"       # selection, focus
ACCENT_DARK = "#1c7d6e"
AMBER = "#f2b84b"        # highlights (headings, the checked-out badge colour in the artwork)
COVER_BG = "#122624"     # behind the cover grid

# status colours readable on the dark background (the light theme's are too dark here)
STATUS = {"here": "#6fd18a", "partial": "#ffb260", "local_only": "#7cc0ff", "elsewhere": "#a6b4b1",
          "busy": "#d39cf0", "failed": "#ff7a7a"}


def is_library(widget) -> bool:
    """True when the widget is in a window that uses the ROM Library's theme."""
    try:
        return getattr(widget.winfo_toplevel()._root(), "_apptheme", None) == "library"
    except Exception:  # noqa
        return False


def colors(widget) -> dict:
    """Colours for text that a window sets itself, readable in whichever app it's shown."""
    if is_library(widget):
        return {"dim": DIM, "warn": AMBER, "bad": STATUS["failed"], "log": FIELD, "log_fg": TEXT}
    return {"dim": "#555", "warn": "#8a6d00", "bad": "#b3261e", "log": "#f6f6f6", "log_fg": "#000"}


def apply_library(root: tk.Misc) -> ttk.Style:
    root._apptheme = "library"
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=BG)
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=FIELD, bordercolor=LINE,
                    darkcolor=BG, lightcolor=LINE, troughcolor=PANEL, focuscolor=ACCENT,
                    selectbackground=ACCENT_DARK, selectforeground="#ffffff", insertcolor=TEXT,
                    arrowcolor=TEXT)
    style.map(".", foreground=[("disabled", "#5f7a75")], background=[("disabled", BG)])
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TLabelframe", background=BG, bordercolor=LINE)
    style.configure("TLabelframe.Label", background=BG, foreground=AMBER)
    style.configure("TButton", background=RAISED, foreground=TEXT, bordercolor=LINE, focusthickness=1,
                    padding=(10, 4))
    style.map("TButton", background=[("pressed", ACCENT_DARK), ("active", HOVER), ("disabled", PANEL)],
              foreground=[("disabled", "#5f7a75")])
    style.configure("Accent.TButton", background=ACCENT_DARK, foreground="#ffffff")
    style.map("Accent.TButton", background=[("pressed", ACCENT_DARK), ("active", ACCENT)])
    style.configure("Toolbutton", background=BG, foreground=DIM, padding=(10, 3))
    style.map("Toolbutton", background=[("selected", ACCENT_DARK), ("active", HOVER)],
              foreground=[("selected", "#ffffff"), ("active", TEXT)])
    for w in ("TCheckbutton", "TRadiobutton"):
        style.configure(w, background=BG, foreground=TEXT, indicatorbackground=FIELD, indicatorforeground=TEXT)
        style.map(w, background=[("active", BG)], indicatorbackground=[("selected", ACCENT)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, bordercolor=LINE, insertcolor=TEXT)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=FIELD, background=RAISED, foreground=TEXT, arrowcolor=TEXT,
                    bordercolor=LINE)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", TEXT)],
              bordercolor=[("focus", ACCENT)])
    style.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT, background=RAISED, arrowcolor=TEXT)
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, bordercolor=LINE)
    style.map("Treeview", background=[("selected", ACCENT_DARK)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=RAISED, foreground=AMBER, bordercolor=LINE, relief="flat")
    style.map("Treeview.Heading", background=[("active", HOVER)])
    style.configure("TScrollbar", background=RAISED, troughcolor=PANEL, bordercolor=BG, arrowcolor=TEXT)
    style.map("TScrollbar", background=[("active", HOVER)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, bordercolor=LINE)
    style.configure("TNotebook", background=BG, bordercolor=LINE)
    style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=(12, 4))
    style.map("TNotebook.Tab", background=[("selected", RAISED)], foreground=[("selected", TEXT)])
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=6, background=BG)
    style.configure("TSeparator", background=LINE)
    # plain Tk widgets, including those in dialogs opened later
    for pattern, value in (("*Background", BG), ("*Foreground", TEXT), ("*Toplevel.Background", BG),
                           ("*Menu.Background", PANEL), ("*Menu.Foreground", TEXT),
                           ("*Menu.activeBackground", ACCENT_DARK), ("*Menu.activeForeground", "#ffffff"),
                           ("*Text.Background", FIELD), ("*Text.Foreground", TEXT),
                           ("*Text.insertBackground", TEXT), ("*Listbox.Background", FIELD),
                           ("*Listbox.Foreground", TEXT), ("*Listbox.selectBackground", ACCENT_DARK),
                           ("*TCombobox*Listbox.Background", FIELD), ("*TCombobox*Listbox.Foreground", TEXT),
                           ("*TCombobox*Listbox.selectBackground", ACCENT_DARK),
                           ("*Canvas.Background", PANEL), ("*selectBackground", ACCENT_DARK),
                           ("*selectForeground", "#ffffff")):
        root.option_add(pattern, value)
    return style
