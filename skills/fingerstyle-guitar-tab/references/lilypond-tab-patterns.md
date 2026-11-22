# GNU LilyPond 6-Line TAB Engraving Patterns

## 1. Rootless Toolchain Discovery

`build_pdf.py` must resolve LilyPond cleanly across environments in this order:
1. `$TAB_TOOLS_ROOT/usr/bin/lilypond`
2. `<project_dir>/.tools/root/usr/bin/lilypond`
3. `<repo_root>/.tools/root/usr/bin/lilypond`
4. System `shutil.which("lilypond")`

When using a local `.tools/root` extracted from `.deb` packages, populate:
- `LD_LIBRARY_PATH`: `<tools_root>/usr/lib/<multiarch-linux-gnu>` and `<multiarch>/guile/<ver>`
- `LILYPOND_DATADIR`: `<tools_root>/usr/share/lilypond/<ver>`
- `GUILE_LOAD_PATH`: `<tools_root>/usr/share/guile/<ver>`
- `GUILE_LOAD_COMPILED_PATH`: `<tools_root>/usr/lib/<multiarch-linux-gnu>/guile/<ver>/ccache`

Use `\version "2.24.0"` followed immediately by `\pointAndClickOff` in generated `.ly` files, and invoke LilyPond with `-dno-point-and-click`. This prevents LilyPond from embedding clickable `textedit://` local filesystem URIs into compiled PDFs, and ensures both `2.24.3` (Ubuntu 24.04) and `2.24.4` compile cleanly without leaking workstation paths.

## 2. Fonts & A4 Page Layout

Verify CJK fonts on the host with `fc-list :lang=zh family | sort -u` and declare global fonts inside `\paper`:

```lilypond
#(define fonts
  (set-global-fonts
    #:roman "Noto Serif CJK SC"
    #:sans "Noto Sans CJK SC"
    #:typewriter "DejaVu Sans Mono"
    #:factor (/ staff-height pt 20)))
```

Set `ragged-last-bottom = ##f`, `ragged-bottom = ##f`, `indent = 0\mm`, and place explicit `\break` / `\pageBreak` points on logical phrase boundaries (e.g., 4-bar phrases) so 40–45 measures fit cleanly on 3 balanced A4 pages.

## 3. Synchronized `ChordNames` + `TabStaff`

Wrap `\new ChordNames` and `\new TabStaff` inside `<< ... >>`.

- **Grace-note synchronization rule:** Any `BeatEvent` containing a `grace_from` note emits `\slashedGrace { <pitch\string>16\glissando }` in `TabStaff`. Because grace notes consume zero nominal time yet create an extra musical moment, `format_chords()` **must** emit `\grace { s16 }` at that exact slot in `ChordNames`; otherwise chord labels shift out of alignment.
- **Note ordering inside `<...>`:** Sort notes ascending by `string` (`1` highest first) so `\glissando` maps the melody string consistently across events.
- **Full rhythmic notation:** Include `\tabFullNotation` inside `TabStaff` so stems, beams, dots, and rests appear clearly alongside fret numbers.

## 4. Custom Stencils & Technique Markings

| Technique | LilyPond Emission |
| :--- | :--- |
| Hammer-on / Pull-off (`H` / `P`) | Close prior slur `)` on target event BEFORE opening new `(` on source event (`0h3p0`), plus `^\markup { \bold \fontsize #-2 "H" }` |
| Slide (`S` link) | `<...>\glissando` + `^\markup { \bold \fontsize #-2 "S" }` with `Glissando.minimum-length = #3.5` and `springs-and-rods` |
| Grace Slide-in | `\slashedGrace { <grace_pitch\string>16\glissando }` before target chord |
| Arpeggio | `\arpeggioArrowUp` in `tabMusic` + `\arpeggio` on event |
| Natural Harmonic (`<12>`) | `\tweak TabNoteHead.stencil #(tab-note-head::print-custom-fret-label "<12>") pitch\string` |
| Thumb Slap (`X`) | `\tweak TabNoteHead.stencil #(tab-note-head::print-custom-fret-label "X") pitch\string` |
| Palm Mute (`P.M.`) | `TextSpanner` with `bound-details.left.text = "P.M. "`, dashed line, `\startTextSpan` .. `\stopTextSpan` |
| Pickup Measure (`M00`) | `\partial 4` + `\once \omit Score.BarNumber` in `TabStaff`, and `\partial 4 s4 |` in `ChordNames` |
