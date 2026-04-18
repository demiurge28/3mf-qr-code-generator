"""Mesh writers.

qr23mf emits a two-object 3MF package (base + features) via
:func:`qr23mf.writers.threemf.write_3mf`. Slicers import the two objects
as independently selectable bodies so each can be assigned a different
filament for two-color printing.
"""
