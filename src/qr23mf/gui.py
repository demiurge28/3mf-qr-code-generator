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

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
from stl.mesh import Mesh

from qr23mf.geometry import (
    GeometryParams,
    ModuleStyle,
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
_PLATE_FILL: str = "#f0f0e8"
_PLATE_OUTLINE: str = "#222"
_MODULE_FILL: str = "#111"
_TEXT_FILL: str = "#0047ab"


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

        # --- Text labels ----------------------------------------------------
        labels = ttk.LabelFrame(self, text="Text labels")
        labels.pack(fill=tk.BOTH, expand=True, pady=6)
        self._labels_list = tk.Listbox(labels, height=4, exportselection=False)
        self._labels_list.pack(fill=tk.X, padx=6, pady=(6, 3))
        self._labels_list.bind("<<ListboxSelect>>", self._on_label_selected)

        form = ttk.Frame(labels)
        form.pack(fill=tk.X, padx=6, pady=(0, 6))

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

        buttons = ttk.Frame(labels)
        buttons.pack(fill=tk.X, padx=6, pady=(0, 6))
        ttk.Button(buttons, text="Add label", command=self._add_label).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Update selected", command=self._update_label).pack(
            side=tk.LEFT, padx=(6, 0)
        )
        ttk.Button(buttons, text="Remove selected", command=self._remove_label).pack(
            side=tk.LEFT, padx=(6, 0)
        )

        # --- Footer ----------------------------------------------------------
        footer = ttk.Frame(self)
        footer.pack(fill=tk.X, pady=(12, 0))
        self._status_var = tk.StringVar(value="Ready.")
        ttk.Label(footer, textvariable=self._status_var).pack(side=tk.LEFT)
        ttk.Button(footer, text="Preview", command=self._on_preview).pack(side=tk.RIGHT)

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

    def _remove_label(self) -> None:
        idx = self._selected_label_index()
        if idx is None:
            messagebox.showinfo("Remove label", "Select a label first.")
            return
        del self._labels[idx]
        self._labels_list.delete(idx)

    # --- Preview flow --------------------------------------------------------

    def _gather_design(
        self,
    ) -> (
        tuple[GeometryParams, QrPlacement, ModuleStyle, tuple[TextLabel, ...], str, EcLevel] | None
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

        return params, placement, style, tuple(self._labels), text, ec

    def _on_preview(self) -> None:
        gathered = self._gather_design()
        if gathered is None:
            return
        params, placement, style, labels, text, ec = gathered

        try:
            matrix = build_matrix(text, ec=ec)
            base, features = build_meshes(
                matrix,
                params,
                placement=placement,
                module_style=style,
                text_labels=labels,
            )
        except ValueError as exc:
            messagebox.showerror("Cannot build mesh", str(exc))
            return

        self._status_var.set(
            f"Previewing: base={base.vectors.shape[0]} triangles, "
            f"features={features.vectors.shape[0]} triangles."
        )
        _PreviewWindow(self, params, placement, style, labels, matrix, base, features)


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
            f"{params.base_height_mm:g} mm | Style: {style} | "
            f"Text labels: {len(labels)} | "
            f"Triangles: base={base.vectors.shape[0]}, "
            f"features={features.vectors.shape[0]}"
        )
        ttk.Label(self, text=summary).pack(padx=12, anchor=tk.W)

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
        try:
            write_stl(self._base, self._features, path)
        except OSError as exc:
            messagebox.showerror("Write failed", f"Could not write STL: {exc}")
            return
        messagebox.showinfo("Saved", f"Wrote {path}")


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
