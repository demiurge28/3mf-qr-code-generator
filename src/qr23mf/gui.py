"""Tkinter GUI for qr23mf.

Launches a two-window configurator:

* **Settings window** — payload text, EC level, base-plate size
  (width / depth / thickness), QR size and X/Y offset on the plate, module
  style (squares or dots), and a text-label list with per-label content /
  position / height / extrusion.
* **Preview window** — 2D top-down rendering of the plate, QR modules, and
  text labels plus a **Create\u2026** button that writes a binary STL via
  :mod:`qr23mf.writers.stl`.

This module imports :mod:`tkinter` at the top level, so the CLI must
lazy-import ``qr23mf.gui`` — that way ``qr23mf`` itself stays importable on
Python installs without Tk bindings (Homebrew's Python 3.11 ships without Tk
by default; ``brew install python-tk@3.11`` provides it).
"""

from __future__ import annotations

import json
import math
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from stl.mesh import Mesh

from qr23mf import __version__
from qr23mf.geometry import (
    GeometryParams,
    ModuleStyle,
    QrFinish,
    QrPlacement,
    TextLabel,
    build_meshes,
)
from qr23mf.qr import EcLevel, QrMatrix, build_matrix
from qr23mf.writers.stl import write_stl

__all__ = ["run"]

# Canvas rendering constants.
_CANVAS_PX: int = 480
_CANVAS_MARGIN_PX: int = 20
_LAYOUT_CANVAS_PX: int = 300
_LAYOUT_CANVAS_MARGIN_PX: int = 12
_PLATE_FILL: str = "#f0f0e8"
_PLATE_OUTLINE: str = "#222"
_MODULE_FILL: str = "#111"
_TEXT_FILL: str = "#0047ab"
_LABEL_BOX_FILL: str = "#e3f3ff"
_LABEL_BOX_OUTLINE: str = "#0047ab"
_LABEL_SELECTED_FILL: str = "#fff2e0"
_LABEL_SELECTED_OUTLINE: str = "#ff6600"
_QR_SELECTED_OUTLINE: str = "#ff6600"

# Step size (mm) for nudging the QR position via arrow keys once it's
# clicked on the layout canvas.
_QR_NUDGE_MM: float = 0.5

# Alignment-grid and snap constants for the interactive layout canvas.
_GRID_SPACING_MM: float = 5.0
_SNAP_TOLERANCE_MM: float = 1.0
_GRID_MINOR_COLOR: str = "#ededed"
_GRID_MAJOR_COLOR: str = "#c6c6c6"
_GRID_MAJOR_EVERY: int = 5  # every 5 minor lines = 25 mm when step is 5 mm
_SPACING_LINE_COLOR: str = "#0047ab"
_SPACING_TEXT_COLOR: str = "#0047ab"
_SPACING_FONT_PX: int = 8

# Update-check constants (GitHub public API, no auth required).
_UPDATE_CHECK_URL: str = (
    "https://api.github.com/repos/demiurge28/3mf-qr-code-generator/releases/latest"
)
_UPDATE_RELEASES_HTML_URL: str = "https://github.com/demiurge28/3mf-qr-code-generator/releases"
_UPDATE_CHECK_TIMEOUT_SEC: float = 5.0


def run() -> None:
    """Launch the Tkinter GUI and block until the main window closes."""
    root = tk.Tk()
    root.title("qr23mf \u2014 QR Code Plate Designer")
    app = _SettingsApp(root)
    app.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
    root.mainloop()


# ---------------------------------------------------------------------------
# Settings window
# ---------------------------------------------------------------------------


class _SettingsApp(ttk.Frame):
    """Main settings window: payload, plate, QR placement, style, labels."""

    _labels: list[TextLabel]

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._labels = []
        # Drag state for the interactive layout canvas.
        self._drag_label_index: int | None = None
        self._drag_moved: bool = False
        self._drag_press_xy: tuple[int, int] = (0, 0)
        # True when the QR footprint was the last thing clicked on the
        # canvas; arrow keys then nudge the QR X/Y offset.
        self._qr_selected: bool = False
        # Layout canvas toggles: alignment grid overlay, snap-to-align, and
        # per-label spacing annotations.
        self._grid_var = tk.BooleanVar(value=False)
        self._snap_var = tk.BooleanVar(value=False)
        self._spacing_var = tk.BooleanVar(value=False)
        self._build()

    def _build(self) -> None:
        # --- Payload + EC ----------------------------------------------------
        text_row = ttk.Frame(self)
        text_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(text_row, text="Payload text:").pack(side=tk.LEFT, padx=(0, 6))
        self._text_var = tk.StringVar(value="https://example.com")
        ttk.Entry(text_row, textvariable=self._text_var, width=36).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        ttk.Label(text_row, text="EC:").pack(side=tk.LEFT, padx=(12, 4))
        self._ec_var = tk.StringVar(value="M")
        ttk.Combobox(
            text_row,
            textvariable=self._ec_var,
            width=4,
            values=["L", "M", "Q", "H"],
            state="readonly",
        ).pack(side=tk.LEFT)

        # --- 3D object (plate) ----------------------------------------------
        plate = ttk.LabelFrame(self, text="3D object (base plate)")
        plate.pack(fill=tk.X, pady=6)
        self._plate_w = _add_float_spinbox(plate, "Width (mm):", 60.0, 5.0, 500.0, 0.5)
        self._plate_d = _add_float_spinbox(plate, "Depth (mm):", 60.0, 5.0, 500.0, 0.5)
        self._plate_h = _add_float_spinbox(plate, "Thickness (mm):", 2.0, 0.2, 20.0, 0.1)

        # --- QR code --------------------------------------------------------
        qr = ttk.LabelFrame(self, text="QR code")
        qr.pack(fill=tk.X, pady=6)
        self._qr_size = _add_float_spinbox(qr, "Size (mm, 0 = fill):", 50.0, 0.0, 500.0, 0.5)
        self._qr_x = _add_float_spinbox(qr, "X offset (mm):", 0.0, -250.0, 250.0, 0.5)
        self._qr_y = _add_float_spinbox(qr, "Y offset (mm):", 0.0, -250.0, 250.0, 0.5)
        self._pixel_h = _add_float_spinbox(qr, "Module extrusion (mm):", 1.0, 0.1, 10.0, 0.1)
        self._quiet = _add_int_spinbox(qr, "Quiet zone (modules):", 4, 0, 20)

        style_row = ttk.Frame(qr)
        style_row.pack(fill=tk.X, padx=6, pady=3)
        ttk.Label(style_row, text="Module style:").pack(side=tk.LEFT)
        self._style_var = tk.StringVar(value="square")
        ttk.Radiobutton(
            style_row,
            text="Squares",
            variable=self._style_var,
            value="square",
        ).pack(side=tk.LEFT, padx=(8, 6))
        ttk.Radiobutton(
            style_row,
            text="Dots",
            variable=self._style_var,
            value="dot",
        ).pack(side=tk.LEFT)

        finish_row = ttk.Frame(qr)
        finish_row.pack(fill=tk.X, padx=6, pady=3)
        ttk.Label(finish_row, text="Finish:").pack(side=tk.LEFT)
        self._finish_var = tk.StringVar(value="extruded")
        ttk.Radiobutton(
            finish_row,
            text="Extruded",
            variable=self._finish_var,
            value="extruded",
        ).pack(side=tk.LEFT, padx=(8, 6))
        ttk.Radiobutton(
            finish_row,
            text="Flush",
            variable=self._finish_var,
            value="flush",
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Radiobutton(
            finish_row,
            text="Sunken",
            variable=self._finish_var,
            value="sunken",
        ).pack(side=tk.LEFT)

        # --- Text labels (form on the left, interactive canvas on the right)
        labels_frame = ttk.LabelFrame(
            self,
            text=(
                "Text labels  \u2014  click plate to add  \u00b7  drag to move  "
                "\u00b7  right-click to remove"
            ),
        )
        labels_frame.pack(fill=tk.BOTH, expand=True, pady=6)
        labels_frame.grid_columnconfigure(0, weight=1)
        labels_frame.grid_columnconfigure(1, weight=0)

        left_panel = ttk.Frame(labels_frame)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        right_panel = ttk.Frame(labels_frame)
        right_panel.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)

        # Left panel: listbox, form, buttons
        self._labels_list = tk.Listbox(left_panel, height=4, exportselection=False)
        self._labels_list.pack(fill=tk.X)
        self._labels_list.bind("<<ListboxSelect>>", self._on_label_selected)

        form = ttk.Frame(left_panel)
        form.pack(fill=tk.X, pady=(6, 0))

        self._label_text = tk.StringVar()
        self._label_x = tk.DoubleVar(value=0.0)
        self._label_y = tk.DoubleVar(value=-20.0)
        self._label_h = tk.DoubleVar(value=5.0)
        self._label_ext = tk.DoubleVar(value=1.0)

        _grid_row(form, 0, "Text:", ttk.Entry(form, textvariable=self._label_text, width=28))
        _grid_row(
            form,
            1,
            "X (mm):",
            ttk.Spinbox(
                form, textvariable=self._label_x, from_=-250, to=250, increment=0.5, width=8
            ),
        )
        _grid_row(
            form,
            2,
            "Y (mm):",
            ttk.Spinbox(
                form, textvariable=self._label_y, from_=-250, to=250, increment=0.5, width=8
            ),
        )
        _grid_row(
            form,
            3,
            "Height (mm):",
            ttk.Spinbox(
                form, textvariable=self._label_h, from_=1.0, to=100.0, increment=0.5, width=8
            ),
        )
        _grid_row(
            form,
            4,
            "Extrusion (mm):",
            ttk.Spinbox(
                form, textvariable=self._label_ext, from_=0.1, to=10.0, increment=0.1, width=8
            ),
        )

        buttons = ttk.Frame(left_panel)
        buttons.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(buttons, text="Add label", command=self._add_label).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Update selected", command=self._update_label).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="Remove selected", command=self._remove_label).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="Remove all", command=self._remove_all_labels).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        # Right panel: interactive layout canvas + usage hint.
        self._layout_canvas = tk.Canvas(
            right_panel,
            width=_LAYOUT_CANVAS_PX,
            height=_LAYOUT_CANVAS_PX,
            bg="white",
            highlightthickness=1,
            highlightbackground="#ccc",
        )
        self._layout_canvas.pack()
        ttk.Label(
            right_panel,
            text=(
                "Left-click on plate   \u2192 add a label using current form values\n"
                "Left-click + drag     \u2192 move the label under the cursor\n"
                "Right-click on label  \u2192 remove it (Ctrl+Click on macOS)"
            ),
            justify=tk.LEFT,
            foreground="#555",
        ).pack(anchor=tk.W, pady=(4, 0))

        # Layout options: grid overlay, snap-to-align, spacing display.
        options_row = ttk.Frame(right_panel)
        options_row.pack(anchor=tk.W, pady=(6, 0))
        ttk.Checkbutton(
            options_row,
            text=f"Grid ({_GRID_SPACING_MM:g} mm)",
            variable=self._grid_var,
            command=self._redraw_layout,
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            options_row,
            text="Snap",
            variable=self._snap_var,
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(
            options_row,
            text="Show spacing",
            variable=self._spacing_var,
            command=self._redraw_layout,
        ).pack(side=tk.LEFT, padx=(8, 0))

        self._layout_canvas.bind("<Button-1>", self._on_canvas_press)
        self._layout_canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self._layout_canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self._layout_canvas.bind("<Button-2>", self._on_canvas_right_click)
        self._layout_canvas.bind("<Button-3>", self._on_canvas_right_click)
        self._layout_canvas.bind("<Control-Button-1>", self._on_canvas_right_click)
        # Arrow-key nudging for the QR position (only active while the QR
        # has been selected with the mouse).
        self._layout_canvas.bind("<Left>", lambda _e: self._nudge_qr(-_QR_NUDGE_MM, 0.0))
        self._layout_canvas.bind("<Right>", lambda _e: self._nudge_qr(+_QR_NUDGE_MM, 0.0))
        self._layout_canvas.bind("<Up>", lambda _e: self._nudge_qr(0.0, +_QR_NUDGE_MM))
        self._layout_canvas.bind("<Down>", lambda _e: self._nudge_qr(0.0, -_QR_NUDGE_MM))

        # Redraw when plate or QR placement values change (typing in a spinbox).
        for layout_var in (self._plate_w, self._plate_d, self._qr_size, self._qr_x, self._qr_y):
            layout_var.trace_add("write", self._on_layout_vars_changed)
        # Initial paint once the widget is mapped.
        self.after_idle(self._redraw_layout)

        # --- Footer ----------------------------------------------------------
        footer = ttk.Frame(self)
        footer.pack(fill=tk.X, pady=(12, 0))
        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(footer, textvariable=self._status_var).pack(side=tk.LEFT)
        ttk.Button(footer, text="Preview", command=self._on_preview).pack(side=tk.RIGHT)
        ttk.Button(
            footer,
            text="Check for updates",
            command=self._check_for_updates,
        ).pack(side=tk.RIGHT, padx=(0, 6))

    # --- Label list handlers -------------------------------------------------

    def _selected_label_index(self) -> int | None:
        sel = self._labels_list.curselection()  # type: ignore[no-untyped-call]
        if not sel:
            return None
        return int(sel[0])

    def _on_label_selected(self, _event: object) -> None:
        idx = self._selected_label_index()
        if idx is None:
            return
        label = self._labels[idx]
        self._label_text.set(label.content)
        self._label_x.set(label.x_mm)
        self._label_y.set(label.y_mm)
        self._label_h.set(label.height_mm)
        self._label_ext.set(label.extrusion_mm)
        self._redraw_layout()

    def _build_label_from_form(self) -> TextLabel | None:
        content = self._label_text.get().strip()
        if not content:
            messagebox.showerror("Invalid label", "Text content must not be empty.")
            return None
        try:
            return TextLabel(
                content=content,
                x_mm=float(self._label_x.get()),
                y_mm=float(self._label_y.get()),
                height_mm=float(self._label_h.get()),
                extrusion_mm=float(self._label_ext.get()),
            )
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid label", str(exc))
            return None

    def _add_label(self) -> None:
        label = self._build_label_from_form()
        if label is None:
            return
        self._labels.append(label)
        self._labels_list.insert(tk.END, _label_display(label))
        self._redraw_layout()

    def _update_label(self) -> None:
        idx = self._selected_label_index()
        if idx is None:
            messagebox.showinfo("Update label", "Select a label first.")
            return
        label = self._build_label_from_form()
        if label is None:
            return
        self._labels[idx] = label
        self._labels_list.delete(idx)
        self._labels_list.insert(idx, _label_display(label))
        self._labels_list.selection_set(idx)
        self._redraw_layout()

    def _remove_label(self) -> None:
        idx = self._selected_label_index()
        if idx is None:
            messagebox.showinfo("Remove label", "Select a label first.")
            return
        del self._labels[idx]
        self._labels_list.delete(idx)
        self._redraw_layout()

    def _remove_all_labels(self) -> None:
        if not self._labels:
            messagebox.showinfo("Remove all labels", "There are no labels to remove.")
            return
        count = len(self._labels)
        if not messagebox.askyesno(
            "Remove all labels",
            f"Remove all {count} label{'s' if count != 1 else ''}?",
        ):
            return
        self._labels.clear()
        self._labels_list.delete(0, tk.END)
        self._redraw_layout()

    # --- Layout canvas (interactive placement) -------------------------------

    def _on_layout_vars_changed(self, *_args: object) -> None:
        self._redraw_layout()

    def _get_layout_transform(self) -> tuple[float, float, float, float, float] | None:
        """Return ``(canvas_w, canvas_h, scale, plate_w, plate_d)`` or ``None``.

        Returns ``None`` when the plate dimensions are invalid (e.g. the user
        is mid-edit and the spinbox contains an empty string).
        """
        try:
            plate_w = float(self._plate_w.get())
            plate_d = float(self._plate_d.get())
        except (ValueError, tk.TclError):
            return None
        if plate_w <= 0 or plate_d <= 0:
            return None
        cw = float(self._layout_canvas.cget("width"))
        ch = float(self._layout_canvas.cget("height"))
        margin = _LAYOUT_CANVAS_MARGIN_PX
        scale = min((cw - 2 * margin) / plate_w, (ch - 2 * margin) / plate_d)
        return cw, ch, scale, plate_w, plate_d

    def _canvas_to_world(self, cx: float, cy: float) -> tuple[float, float] | None:
        xform = self._get_layout_transform()
        if xform is None:
            return None
        cw, ch, scale, _pw, _pd = xform
        return (cx - cw / 2.0) / scale, -(cy - ch / 2.0) / scale

    def _label_under_cursor(self, cx: float, cy: float) -> int | None:
        items = self._layout_canvas.find_overlapping(cx, cy, cx, cy)
        for item in items:
            for tag in self._layout_canvas.gettags(item):
                if tag.startswith("label-"):
                    return int(tag[len("label-") :])
        return None

    def _qr_footprint_bounds_mm(self) -> tuple[float, float, float, float] | None:
        """Return ``(x_left, y_bottom, x_right, y_top)`` of the QR footprint in mm.

        Returns ``None`` when any spinbox contains an unparseable intermediate
        value (e.g. during typing) or the plate dims are non-positive.
        """
        try:
            plate_w = float(self._plate_w.get())
            plate_d = float(self._plate_d.get())
            qr_size = float(self._qr_size.get())
            qr_x = float(self._qr_x.get())
            qr_y = float(self._qr_y.get())
        except (ValueError, tk.TclError):
            return None
        if plate_w <= 0 or plate_d <= 0:
            return None
        footprint = qr_size if qr_size > 0 else min(plate_w, plate_d)
        half = footprint / 2.0
        return qr_x - half, qr_y - half, qr_x + half, qr_y + half

    def _point_in_qr_footprint(self, x_mm: float, y_mm: float) -> bool:
        bounds = self._qr_footprint_bounds_mm()
        if bounds is None:
            return False
        x0, y0, x1, y1 = bounds
        return x0 <= x_mm <= x1 and y0 <= y_mm <= y1

    def _collect_snap_anchors(
        self, exclude_label_index: int | None = None
    ) -> tuple[list[float], list[float]]:
        """Build (x_anchors, y_anchors) of mm positions for snap-to-align.

        Anchors include the plate center (0, 0), plate edges, the QR center
        and edges (if the QR footprint is well-defined), every other label's
        center, and — when the grid overlay is enabled — every grid line.
        """
        xs: list[float] = [0.0]
        ys: list[float] = [0.0]
        try:
            plate_w = float(self._plate_w.get())
            plate_d = float(self._plate_d.get())
        except (ValueError, tk.TclError):
            return xs, ys
        if plate_w <= 0 or plate_d <= 0:
            return xs, ys
        xs.extend([-plate_w / 2.0, +plate_w / 2.0])
        ys.extend([-plate_d / 2.0, +plate_d / 2.0])

        qbounds = self._qr_footprint_bounds_mm()
        if qbounds is not None:
            qx0, qy0, qx1, qy1 = qbounds
            xs.extend([qx0, (qx0 + qx1) / 2.0, qx1])
            ys.extend([qy0, (qy0 + qy1) / 2.0, qy1])

        for i, lab in enumerate(self._labels):
            if i == exclude_label_index:
                continue
            xs.append(lab.x_mm)
            ys.append(lab.y_mm)

        if self._grid_var.get():
            step = _GRID_SPACING_MM
            k_min = math.ceil(-plate_w / 2.0 / step)
            k_max = math.floor(plate_w / 2.0 / step)
            xs.extend(k * step for k in range(k_min, k_max + 1))
            k_min = math.ceil(-plate_d / 2.0 / step)
            k_max = math.floor(plate_d / 2.0 / step)
            ys.extend(k * step for k in range(k_min, k_max + 1))

        return xs, ys

    def _draw_grid_overlay(
        self,
        canvas: tk.Canvas,
        to_c: Callable[[float, float], tuple[float, float]],
        plate_w: float,
        plate_d: float,
    ) -> None:
        """Draw vertical and horizontal grid lines at every ``_GRID_SPACING_MM``.

        Lines spanning the plate are drawn inside the plate's bounds only.
        Every ``_GRID_MAJOR_EVERY``-th line uses the heavier major color
        (e.g. 25 mm increments for a 5 mm grid).
        """
        step = _GRID_SPACING_MM
        k_min = math.ceil(-plate_w / 2.0 / step)
        k_max = math.floor(plate_w / 2.0 / step)
        for k in range(k_min, k_max + 1):
            x = k * step
            x_top, y_top = to_c(x, +plate_d / 2.0)
            x_bot, y_bot = to_c(x, -plate_d / 2.0)
            color = _GRID_MAJOR_COLOR if k % _GRID_MAJOR_EVERY == 0 else _GRID_MINOR_COLOR
            canvas.create_line(x_top, y_top, x_bot, y_bot, fill=color)

        k_min = math.ceil(-plate_d / 2.0 / step)
        k_max = math.floor(plate_d / 2.0 / step)
        for k in range(k_min, k_max + 1):
            y = k * step
            x_left, y_left = to_c(-plate_w / 2.0, y)
            x_right, y_right = to_c(+plate_w / 2.0, y)
            color = _GRID_MAJOR_COLOR if k % _GRID_MAJOR_EVERY == 0 else _GRID_MINOR_COLOR
            canvas.create_line(x_left, y_left, x_right, y_right, fill=color)

    def _estimate_label_bbox_mm(self, label: TextLabel) -> tuple[float, float, float, float]:
        """Return ``(x0, y0, x1, y1)`` for the label's approximate bbox in mm.

        Uses the same glyph-width heuristic as :meth:`_redraw_layout`.
        """
        est_w_mm = max(len(label.content) * label.height_mm * 0.6, label.height_mm)
        est_h_mm = label.height_mm
        return (
            label.x_mm - est_w_mm / 2.0,
            label.y_mm - est_h_mm / 2.0,
            label.x_mm + est_w_mm / 2.0,
            label.y_mm + est_h_mm / 2.0,
        )

    def _draw_spacing_for_label(
        self,
        label_idx: int,
        canvas: tk.Canvas,
        to_c: Callable[[float, float], tuple[float, float]],
        plate_w: float,
        plate_d: float,
    ) -> None:
        """Annotate the selected label with mm distances to plate edges and QR.

        Dashed guides run from each side of the label's bounding box to the
        nearest plate edge, and (when it doesn't overlap) to the nearest QR
        footprint edge in X and Y. Each guide is labelled with its distance
        in millimetres.
        """
        label = self._labels[label_idx]
        lx0, ly0, lx1, ly1 = self._estimate_label_bbox_mm(label)
        mid_x = (lx0 + lx1) / 2.0
        mid_y = (ly0 + ly1) / 2.0

        def annotate(
            x0_mm: float, y0_mm: float, x1_mm: float, y1_mm: float, distance_mm: float
        ) -> None:
            if distance_mm <= 0:
                return
            p0 = to_c(x0_mm, y0_mm)
            p1 = to_c(x1_mm, y1_mm)
            canvas.create_line(
                p0[0],
                p0[1],
                p1[0],
                p1[1],
                fill=_SPACING_LINE_COLOR,
                dash=(3, 2),
                width=1,
            )
            mid = to_c((x0_mm + x1_mm) / 2.0, (y0_mm + y1_mm) / 2.0)
            canvas.create_text(
                mid[0],
                mid[1],
                text=f"{distance_mm:.1f} mm",
                fill=_SPACING_TEXT_COLOR,
                font=("TkDefaultFont", _SPACING_FONT_PX),
            )

        # Distances from label bbox to each plate edge.
        annotate(-plate_w / 2.0, mid_y, lx0, mid_y, lx0 - (-plate_w / 2.0))
        annotate(lx1, mid_y, +plate_w / 2.0, mid_y, (+plate_w / 2.0) - lx1)
        annotate(mid_x, ly1, mid_x, +plate_d / 2.0, (+plate_d / 2.0) - ly1)
        annotate(mid_x, -plate_d / 2.0, mid_x, ly0, ly0 - (-plate_d / 2.0))

        # Distance to the QR footprint in each axis (only when the label
        # doesn't overlap the QR along that axis).
        qbounds = self._qr_footprint_bounds_mm()
        if qbounds is not None:
            qx0, qy0, qx1, qy1 = qbounds
            if lx1 < qx0:
                annotate(lx1, mid_y, qx0, mid_y, qx0 - lx1)
            elif lx0 > qx1:
                annotate(qx1, mid_y, lx0, mid_y, lx0 - qx1)
            if ly1 < qy0:
                annotate(mid_x, ly1, mid_x, qy0, qy0 - ly1)
            elif ly0 > qy1:
                annotate(mid_x, qy1, mid_x, ly0, ly0 - qy1)

    def _nudge_qr(self, dx_mm: float, dy_mm: float) -> None:
        """Move the QR by (dx, dy) mm, clamped to keep it inside the plate."""
        if not self._qr_selected:
            return
        try:
            x = float(self._qr_x.get())
            y = float(self._qr_y.get())
            plate_w = float(self._plate_w.get())
            plate_d = float(self._plate_d.get())
            qr_size = float(self._qr_size.get())
        except (ValueError, tk.TclError):
            return
        footprint = qr_size if qr_size > 0 else min(plate_w, plate_d)
        half = footprint / 2.0
        new_x = max(-plate_w / 2.0 + half, min(plate_w / 2.0 - half, x + dx_mm))
        new_y = max(-plate_d / 2.0 + half, min(plate_d / 2.0 - half, y + dy_mm))
        self._qr_x.set(new_x)
        self._qr_y.set(new_y)
        # Layout redraws via the var trace, but call explicitly in case
        # the nudge produced a clamped no-op (no trace would fire).
        self._redraw_layout()

    def _redraw_layout(self) -> None:
        canvas = self._layout_canvas
        canvas.delete("all")
        xform = self._get_layout_transform()
        if xform is None:
            return
        cw, ch, scale, plate_w, plate_d = xform

        def to_c(x_mm: float, y_mm: float) -> tuple[float, float]:
            return cw / 2.0 + x_mm * scale, ch / 2.0 - y_mm * scale

        # Plate outline (drawn first; grid overlays the plate fill, features
        # and labels sit on top).
        px0, py0 = to_c(-plate_w / 2.0, +plate_d / 2.0)
        px1, py1 = to_c(+plate_w / 2.0, -plate_d / 2.0)
        canvas.create_rectangle(
            px0, py0, px1, py1, fill=_PLATE_FILL, outline=_PLATE_OUTLINE, width=2
        )

        # Alignment grid overlay (optional).
        if self._grid_var.get():
            self._draw_grid_overlay(canvas, to_c, plate_w, plate_d)

        # QR footprint (dashed outline; highlighted when clicked-on).
        try:
            qr_size = float(self._qr_size.get())
            qr_x = float(self._qr_x.get())
            qr_y = float(self._qr_y.get())
        except (ValueError, tk.TclError):
            qr_size, qr_x, qr_y = 0.0, 0.0, 0.0
        qr_footprint = qr_size if qr_size > 0 else min(plate_w, plate_d)
        qx0, qy0 = to_c(qr_x - qr_footprint / 2.0, qr_y + qr_footprint / 2.0)
        qx1, qy1 = to_c(qr_x + qr_footprint / 2.0, qr_y - qr_footprint / 2.0)
        qr_outline = _QR_SELECTED_OUTLINE if self._qr_selected else "#888"
        qr_width = 2 if self._qr_selected else 1
        canvas.create_rectangle(qx0, qy0, qx1, qy1, outline=qr_outline, dash=(4, 3), width=qr_width)

        # Text labels.
        selected = self._selected_label_index()
        for i, label in enumerate(self._labels):
            # Approximate bounding box for drawing + hit-testing. The exact
            # rasterized geometry isn't needed here; the preview mesh is built
            # from Pillow's rasterization at "Preview" time.
            est_w_mm = max(len(label.content) * label.height_mm * 0.6, label.height_mm)
            est_h_mm = label.height_mm
            lx0, ly0 = to_c(label.x_mm - est_w_mm / 2.0, label.y_mm + est_h_mm / 2.0)
            lx1, ly1 = to_c(label.x_mm + est_w_mm / 2.0, label.y_mm - est_h_mm / 2.0)
            is_selected = i == selected
            outline = _LABEL_SELECTED_OUTLINE if is_selected else _LABEL_BOX_OUTLINE
            fill = _LABEL_SELECTED_FILL if is_selected else _LABEL_BOX_FILL
            tags = ("label", f"label-{i}")
            canvas.create_rectangle(
                lx0, ly0, lx1, ly1, fill=fill, outline=outline, width=2, tags=tags
            )
            canvas.create_text(
                (lx0 + lx1) / 2.0,
                (ly0 + ly1) / 2.0,
                text=label.content,
                fill=outline,
                tags=tags,
            )

        # Spacing annotations for the currently selected label (optional).
        if self._spacing_var.get() and selected is not None:
            self._draw_spacing_for_label(selected, canvas, to_c, plate_w, plate_d)

    def _on_canvas_press(self, event: tk.Event[tk.Misc]) -> None:
        # Focus the canvas so subsequent arrow-key events reach our bindings.
        self._layout_canvas.focus_set()
        ex, ey = int(event.x), int(event.y)
        self._drag_press_xy = (ex, ey)
        self._drag_moved = False
        self._drag_label_index = self._label_under_cursor(ex, ey)
        if self._drag_label_index is not None:
            # Clicking a label deselects the QR and selects the label.
            self._qr_selected = False
            self._labels_list.selection_clear(0, tk.END)
            self._labels_list.selection_set(self._drag_label_index)
            self._on_label_selected(None)
            return
        # Not on a label. Check whether the click lands inside the QR footprint.
        world = self._canvas_to_world(float(ex), float(ey))
        if world is not None and self._point_in_qr_footprint(*world):
            # Select the QR so arrow keys nudge it.
            self._qr_selected = True
            self._labels_list.selection_clear(0, tk.END)
        else:
            # Empty plate area: deselect everything. The release handler will
            # treat the click as "add a label here" using current form values.
            self._qr_selected = False
        self._redraw_layout()

    def _on_canvas_drag(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag_label_index is None:
            return
        ex, ey = int(event.x), int(event.y)
        if (
            abs(ex - self._drag_press_xy[0]) + abs(ey - self._drag_press_xy[1]) < 2
            and not self._drag_moved
        ):
            return
        world = self._canvas_to_world(ex, ey)
        xform = self._get_layout_transform()
        if world is None or xform is None:
            return
        self._drag_moved = True
        x_mm, y_mm = world
        _, _, _, plate_w, plate_d = xform
        x_mm = max(-plate_w / 2.0, min(plate_w / 2.0, x_mm))
        y_mm = max(-plate_d / 2.0, min(plate_d / 2.0, y_mm))

        i = self._drag_label_index
        if self._snap_var.get():
            xs, ys = self._collect_snap_anchors(exclude_label_index=i)
            x_mm = _snap_coord(x_mm, xs)
            y_mm = _snap_coord(y_mm, ys)
        existing = self._labels[i]
        try:
            new_label = TextLabel(
                content=existing.content,
                x_mm=x_mm,
                y_mm=y_mm,
                height_mm=existing.height_mm,
                extrusion_mm=existing.extrusion_mm,
            )
        except ValueError:
            return
        self._labels[i] = new_label
        self._labels_list.delete(i)
        self._labels_list.insert(i, _label_display(new_label))
        self._labels_list.selection_set(i)
        self._label_x.set(x_mm)
        self._label_y.set(y_mm)
        self._redraw_layout()

    def _on_canvas_release(self, event: tk.Event[tk.Misc]) -> None:
        # Empty-area click (not on a label, not on QR) with no drag motion =
        # add a new label at that point.
        if self._drag_label_index is None and not self._drag_moved and not self._qr_selected:
            content = self._label_text.get().strip()
            if not content:
                messagebox.showinfo(
                    "Add label",
                    "Enter text content in the form first, then click on the plate.",
                )
            else:
                ex, ey = int(event.x), int(event.y)
                world = self._canvas_to_world(ex, ey)
                if world is not None:
                    x_mm, y_mm = world
                    if self._snap_var.get():
                        xs, ys = self._collect_snap_anchors(exclude_label_index=None)
                        x_mm = _snap_coord(x_mm, xs)
                        y_mm = _snap_coord(y_mm, ys)
                    try:
                        new_label = TextLabel(
                            content=content,
                            x_mm=x_mm,
                            y_mm=y_mm,
                            height_mm=float(self._label_h.get()),
                            extrusion_mm=float(self._label_ext.get()),
                        )
                    except (ValueError, tk.TclError) as exc:
                        messagebox.showerror("Invalid label", str(exc))
                    else:
                        self._labels.append(new_label)
                        self._labels_list.insert(tk.END, _label_display(new_label))
                        self._labels_list.selection_clear(0, tk.END)
                        self._labels_list.selection_set(tk.END)
                        self._redraw_layout()
        self._drag_label_index = None
        self._drag_moved = False

    def _on_canvas_right_click(self, event: tk.Event[tk.Misc]) -> None:
        ex, ey = int(event.x), int(event.y)
        idx = self._label_under_cursor(ex, ey)
        if idx is None:
            return
        content = self._labels[idx].content
        if not messagebox.askyesno("Remove label", f"Remove label {content!r}?"):
            return
        del self._labels[idx]
        self._labels_list.delete(idx)
        self._redraw_layout()

    # --- Update check --------------------------------------------------------

    def _check_for_updates(self) -> None:
        self._status_var.set("Checking for updates\u2026")
        self.update_idletasks()
        try:
            latest = _fetch_latest_release_tag()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._status_var.set("Update check failed.")
            messagebox.showerror(
                "Update check failed",
                f"Could not reach GitHub to check for updates:\n{exc}",
            )
            return
        except ValueError as exc:
            self._status_var.set("Update check failed.")
            messagebox.showerror(
                "Update check failed",
                f"Unexpected response from GitHub:\n{exc}",
            )
            return

        if latest is None or not _is_newer(latest, __version__):
            self._status_var.set(f"No New Updates (running {__version__}).")
            messagebox.showinfo("Check for updates", "No New Updates")
            return

        self._status_var.set(f"Update available: {latest}.")
        if messagebox.askyesno(
            "Update available",
            f"qr23mf {latest} is available (you have {__version__}).\n\n"
            "Open the releases page in your browser?",
        ):
            webbrowser.open(_UPDATE_RELEASES_HTML_URL)

    # --- Preview flow --------------------------------------------------------

    def _gather_design(
        self,
    ) -> (
        tuple[
            GeometryParams,
            QrPlacement,
            ModuleStyle,
            QrFinish,
            tuple[TextLabel, ...],
            str,
            EcLevel,
        ]
        | None
    ):
        try:
            params = GeometryParams(
                size_mm=float(self._plate_w.get()),
                base_height_mm=float(self._plate_h.get()),
                pixel_height_mm=float(self._pixel_h.get()),
                quiet_zone_modules=int(self._quiet.get()),
                depth_mm=float(self._plate_d.get()),
            )
            qr_size = float(self._qr_size.get())
            placement = QrPlacement(
                qr_size_mm=qr_size if qr_size > 0 else None,
                x_offset_mm=float(self._qr_x.get()),
                y_offset_mm=float(self._qr_y.get()),
            )
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("Invalid input", str(exc))
            return None

        style_raw = self._style_var.get()
        if style_raw not in ("square", "dot"):
            messagebox.showerror("Invalid input", f"Unknown module style {style_raw!r}.")
            return None
        style: ModuleStyle = "square" if style_raw == "square" else "dot"

        finish_raw = self._finish_var.get()
        finish: QrFinish
        if finish_raw == "extruded":
            finish = "extruded"
        elif finish_raw == "flush":
            finish = "flush"
        elif finish_raw == "sunken":
            finish = "sunken"
        else:
            messagebox.showerror("Invalid input", f"Unknown QR finish {finish_raw!r}.")
            return None

        text = self._text_var.get()
        if not text:
            messagebox.showerror("Invalid input", "Payload text must not be empty.")
            return None

        ec_raw = self._ec_var.get().upper()
        if ec_raw not in ("L", "M", "Q", "H"):
            messagebox.showerror("Invalid input", f"Unknown EC level {ec_raw!r}.")
            return None
        # Narrow the literal for mypy.
        ec: EcLevel
        if ec_raw == "L":
            ec = "L"
        elif ec_raw == "M":
            ec = "M"
        elif ec_raw == "Q":
            ec = "Q"
        else:
            ec = "H"

        return params, placement, style, finish, tuple(self._labels), text, ec

    def _on_preview(self) -> None:
        gathered = self._gather_design()
        if gathered is None:
            return
        params, placement, style, finish, labels, text, ec = gathered

        try:
            matrix = build_matrix(text, ec=ec)
            base, features = build_meshes(
                matrix,
                params,
                placement=placement,
                module_style=style,
                qr_finish=finish,
                text_labels=labels,
            )
        except ValueError as exc:
            messagebox.showerror("Cannot build mesh", str(exc))
            return

        self._status_var.set(
            f"Previewing: base={base.vectors.shape[0]} triangles, "
            f"features={features.vectors.shape[0]} triangles (finish: {finish})."
        )
        _PreviewWindow(self, params, placement, style, finish, labels, matrix, base, features)


# ---------------------------------------------------------------------------
# Preview window
# ---------------------------------------------------------------------------


class _PreviewWindow(tk.Toplevel):
    """Top-down 2D preview with Back / Create buttons."""

    def __init__(
        self,
        parent: tk.Misc,
        params: GeometryParams,
        placement: QrPlacement,
        style: ModuleStyle,
        finish: QrFinish,
        labels: tuple[TextLabel, ...],
        matrix: QrMatrix,
        base: Mesh,
        features: Mesh,
    ) -> None:
        super().__init__(parent)
        self.title("Preview")
        self._params = params
        self._placement = placement
        self._style = style
        self._finish = finish
        self._labels = labels
        self._matrix = matrix
        self._base = base
        self._features = features

        self._canvas = tk.Canvas(
            self,
            width=_CANVAS_PX,
            height=_CANVAS_PX,
            bg="white",
            highlightthickness=1,
            highlightbackground="#ccc",
        )
        self._canvas.pack(padx=12, pady=12)

        summary = (
            f"Plate: {params.size_mm:g} x {params.effective_depth_mm:g} x "
            f"{params.base_height_mm:g} mm | Style: {style} | Finish: {finish} | "
            f"Text labels: {len(labels)} | "
            f"Triangles: base={base.vectors.shape[0]}, "
            f"features={features.vectors.shape[0]}"
        )
        ttk.Label(self, text=summary).pack(padx=12, anchor=tk.W)

        # Split-output checkbox: when on, Create writes base + features as
        # two STL files so the slicer sees two selectable bodies (one per
        # filament). Default on because the Finish modes are most useful
        # with multi-material printing.
        self._split_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self,
            text="Write separate STLs for base and features (for multi-filament slicers)",
            variable=self._split_var,
        ).pack(padx=12, anchor=tk.W, pady=(4, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(6, 12))
        ttk.Button(buttons, text="Back", command=self.destroy).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Create\u2026", command=self._on_create).pack(side=tk.RIGHT)

        self._draw_preview()
        self.transient(parent.winfo_toplevel())
        self.grab_set()

    def _draw_preview(self) -> None:
        c = self._canvas
        params = self._params
        placement = self._placement

        plate_w = float(params.size_mm)
        plate_d = params.effective_depth_mm
        usable = _CANVAS_PX - 2 * _CANVAS_MARGIN_PX
        scale = min(usable / plate_w, usable / plate_d)

        def to_canvas(x_mm: float, y_mm: float) -> tuple[float, float]:
            # Center plate on canvas; +Y in world maps to -Y on canvas.
            cx = _CANVAS_PX / 2.0 + x_mm * scale
            cy = _CANVAS_PX / 2.0 - y_mm * scale
            return cx, cy

        # Plate outline
        px0, py0 = to_canvas(-plate_w / 2.0, +plate_d / 2.0)
        px1, py1 = to_canvas(+plate_w / 2.0, -plate_d / 2.0)
        c.create_rectangle(px0, py0, px1, py1, fill=_PLATE_FILL, outline=_PLATE_OUTLINE, width=2)

        # QR modules — mirror the geometry math so the preview matches the mesh.
        qr_footprint_mm = (
            float(placement.qr_size_mm)
            if placement.qr_size_mm is not None
            else min(plate_w, plate_d)
        )
        total_modules = self._matrix.size + 2 * params.quiet_zone_modules
        module_mm = qr_footprint_mm / total_modules
        quiet_offset = params.quiet_zone_modules * module_mm
        qr_left = placement.x_offset_mm - qr_footprint_mm / 2.0 + quiet_offset
        qr_top = placement.y_offset_mm + qr_footprint_mm / 2.0 - quiet_offset

        dark_rows, dark_cols = np.nonzero(self._matrix.modules)
        for row_i, col_i in zip(dark_rows.tolist(), dark_cols.tolist(), strict=True):
            mx0 = qr_left + col_i * module_mm
            mx1 = mx0 + module_mm
            my1 = qr_top - row_i * module_mm
            my0 = my1 - module_mm
            a_x, a_y = to_canvas(mx0, my1)
            b_x, b_y = to_canvas(mx1, my0)
            if self._style == "square":
                c.create_rectangle(a_x, a_y, b_x, b_y, fill=_MODULE_FILL, outline="")
            else:
                c.create_oval(a_x, a_y, b_x, b_y, fill=_MODULE_FILL, outline="")

        # Text labels — render text via Tk for legibility; exact rasterization
        # in the mesh may differ slightly but the placement matches.
        for label in self._labels:
            lx, ly = to_canvas(label.x_mm, label.y_mm)
            font_px = max(6, round(label.height_mm * scale * 0.85))
            c.create_text(
                lx,
                ly,
                text=label.content,
                fill=_TEXT_FILL,
                font=("TkDefaultFont", font_px),
            )

    def _on_create(self) -> None:
        out = filedialog.asksaveasfilename(
            parent=self,
            title="Save STL",
            defaultextension=".stl",
            filetypes=[("Binary STL", "*.stl"), ("All files", "*.*")],
        )
        if not out:
            return
        path = Path(out)
        split = bool(self._split_var.get())
        try:
            written = write_stl(self._base, self._features, path, split=split)
        except OSError as exc:
            messagebox.showerror("Write failed", f"Could not write STL: {exc}")
            return
        if len(written) == 1:
            messagebox.showinfo("Saved", f"Wrote {written[0]}")
        else:
            lines = "\n".join(f"\u2022 {p}" for p in written)
            messagebox.showinfo(
                "Saved",
                f"Wrote {len(written)} files (import both for per-filament selection):\n{lines}",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label_display(label: TextLabel) -> str:
    return (
        f"{label.content!r} @ ({label.x_mm:g}, {label.y_mm:g}) mm, "
        f"h={label.height_mm:g} mm, ext={label.extrusion_mm:g} mm"
    )


def _add_float_spinbox(
    parent: tk.Misc,
    label: str,
    initial: float,
    min_val: float,
    max_val: float,
    step: float,
) -> tk.DoubleVar:
    row = ttk.Frame(parent)
    row.pack(fill=tk.X, padx=6, pady=2)
    ttk.Label(row, text=label, width=24, anchor=tk.W).pack(side=tk.LEFT)
    var = tk.DoubleVar(value=initial)
    ttk.Spinbox(
        row,
        textvariable=var,
        from_=min_val,
        to=max_val,
        increment=step,
        width=10,
    ).pack(side=tk.LEFT)
    return var


def _add_int_spinbox(
    parent: tk.Misc,
    label: str,
    initial: int,
    min_val: int,
    max_val: int,
) -> tk.IntVar:
    row = ttk.Frame(parent)
    row.pack(fill=tk.X, padx=6, pady=2)
    ttk.Label(row, text=label, width=24, anchor=tk.W).pack(side=tk.LEFT)
    var = tk.IntVar(value=initial)
    ttk.Spinbox(
        row,
        textvariable=var,
        from_=min_val,
        to=max_val,
        increment=1,
        width=10,
    ).pack(side=tk.LEFT)
    return var


def _grid_row(parent: tk.Misc, row: int, label: str, widget: tk.Widget) -> None:
    ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, padx=(0, 6), pady=2)
    widget.grid(row=row, column=1, sticky=tk.W, pady=2)


def _snap_coord(value: float, anchors: list[float]) -> float:
    """Return ``value`` snapped to the nearest anchor within ``_SNAP_TOLERANCE_MM``.

    Falls back to ``value`` unchanged when no anchor is closer than the
    tolerance, so snapping degrades gracefully when no alignment target is
    relevant.
    """
    best = value
    best_dist = _SNAP_TOLERANCE_MM
    for anchor in anchors:
        d = abs(value - anchor)
        if d < best_dist:
            best_dist = d
            best = anchor
    return best


# ---------------------------------------------------------------------------
# Update check helpers
# ---------------------------------------------------------------------------


def _fetch_latest_release_tag(timeout: float = _UPDATE_CHECK_TIMEOUT_SEC) -> str | None:
    """Return the latest release tag name from GitHub, or ``None`` if missing.

    Raises ``urllib.error.URLError`` / ``OSError`` / ``TimeoutError`` for
    network problems and ``ValueError`` if the JSON payload is malformed.
    """
    req = urllib.request.Request(
        _UPDATE_CHECK_URL,
        headers={
            "User-Agent": f"qr23mf/{__version__}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise ValueError("GitHub response was not a JSON object")
    tag = payload.get("tag_name")
    if tag is None:
        return None
    if not isinstance(tag, str):
        raise ValueError(f"Unexpected tag_name type: {type(tag).__name__}")
    return tag


def _is_newer(latest_tag: str, current: str) -> bool:
    """Return True iff ``latest_tag`` represents a newer release than ``current``.

    Parses both as dotted integer tuples after stripping a leading ``v``/``V``
    and any pre-release / local suffix. Falls back to string inequality when
    either version can't be parsed as integers.
    """

    def _normalize(s: str) -> str:
        s = s.lstrip("vV")
        s = s.split("+", 1)[0]
        s = s.split("-", 1)[0]
        return s

    try:
        latest_tuple = tuple(int(p) for p in _normalize(latest_tag).split(".") if p)
        current_tuple = tuple(int(p) for p in _normalize(current).split(".") if p)
    except ValueError:
        return latest_tag != current
    if not latest_tuple or not current_tuple:
        return latest_tag != current
    return latest_tuple > current_tuple
