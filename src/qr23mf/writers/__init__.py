"""Output writers.

qr23mf can emit the design in two output formats:

* **3MF** — :func:`qr23mf.writers.threemf.write_3mf` produces a two-object
  3MF package (base plate + QR / text features) for 3D printing. Slicers
  import the two objects as independently selectable bodies so each can
  be assigned a different filament for two-color printing.
* **SVG** — :func:`qr23mf.writers.svg.write_svg` produces a flat 2D SVG
  in millimetre units, suitable for laser etching / engraving / cutting
  or for import into vector editors (Inkscape, Illustrator, LightBurn).
  The same geometry that powers the 3MF output is emitted as axis-
  aligned rectangles (or circles in dot mode) plus rasterized text cells.
"""
