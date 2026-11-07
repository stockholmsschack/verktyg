# Chess Merida

`ChessMerida.otf` is the font supplied for this project (family: Chess Merida,
version: 1.0). The original Chess Merida font was designed by Armando Hernandez
Marroquin; see the [Chess Merida font project](https://github.com/vasiliyaltunin/chess-merida-font).

`../../images/merida-pawn.svg` contains the outline of the font's white pawn
(`p`). It is used for both the header logo and favicon. The font is retained
here as the source; browsers display the SVG without loading the font.

The outline was exported with HarfBuzz:

```sh
hb-view ChessMerida.otf p --font-size=128 --ink --margin=8 \
  --background=none --foreground=212529 --output-format=svg \
  --output-file=pawn.svg
```

The exported path is placed in a square `128 × 128` viewBox with
`translate(0 128)`, preserving the font coordinates. A white rounded background
keeps the favicon visible on both light and dark browser tabs.
