# Polish passes

After `slice_frontend.py` slices the restyled windows into the tile pyramid,
the map edge and a few seams still show where the restyle stopped: blocky
water, gray-washed land, dashed roads trailing into the fog, mint-colored
forest rims. The polish passes fix these in place. They are all local (no API
calls), and each one is idempotent, so re-running a pass gives the same tiles.

Each pass is a flag on `slice_frontend.py`. Pass the same world arguments every
time (see the `CORE` array in [pipeline.md](pipeline.md#4-slice-into-the-viewer)).
Higher zoom levels are rebuilt after every pass.

## Order

| Step | Flags | What it fixes |
| --- | --- | --- |
| 1 | `--polish-vignette` | Rebuilds the edge band from the source windows and fades it to the viewer's beige with one smooth, slightly wobbled vignette. No square steps along tile edges. |
| 2 | `--polish-water --polish-forest --polish-flats` | Water: one bay tone with its wave texture kept, fog holes over open water filled, and a separate fade along the water body. Forest: gray-washed land just inside the edge cross-fades to the clean staging, and restyle-window greens are evened. Flats: featureless ground near the edge (flat fill, gray puddles, white lots) fades into the fog. |
| 3 | `--polish-fogline` | Near the edge, pixels whose surroundings are mostly fog (dashed traces, gray-washed windows, straight cut-offs) fade to beige along a feathered ramp. |
| 4 | `--polish-mint --polish-spots FILE` | Mint: teal procedural-staging rims near the edge turn toward the restyled forest hue; real water and glass are left alone. Spots: one-off fixes listed in a JSON file, including milky river veils. |
| 5 | `--polish-seams` | Feathers a tone jump across a z0 tile edge, and across a restyle-window hem when those windows are still on disk. Only the low-frequency colour is moved, and only where both sides are the same kind of surface, so a building cut or a river bank is not smeared. |
| 6 | `--polish-tone` | Gives the whole restyle one house tone. Replaces step 5: it reads the tiles from before the seam blend. See [House tone](#house-tone). |
| 7 | `--polish-gaps --gaps-backup DIR` | Fills holes inside the city: beige voids, ghostly veils, buildings cut off on a tile or window line, and blurry patches. See [Gaps](#gaps). |
| 8 | `--polish-edges --edges-backup DIR` | Fixes the edge band and the district rims: content washed to white or cut off on a line, roads and motorways that stop dead, roads or bridges over open water. See [Edges](#edges). |

`--polish-vignette` needs `--district` arguments to reslice from the source
windows, `--polish-water` needs them for the district water masks, and
`--polish-seams` uses them to locate restyle-window hems. Passing the full
`CORE` array every time covers all three. Run one step per command, in
order, and pass the step-2 flags together. Step 5 can share a command with
step 4 (`--polish-mint --polish-spots FILE --polish-seams`); spots run
first, then the seam blend.

## Sources and re-runs

Every pass reads from a source that it never overwrites, inside the restyle
tile directory (`tiles-restyled/` by default):

| Pass | Source |
| --- | --- |
| vignette, forest | `.vignette-clean/`, a clean reslice of the edge band |
| water, forest, flats | `.water-src/`, a one-time snapshot of the tiles |
| fogline | `.fogline-src/` |
| mint | `.mint-src/` |
| spots | `.spots-src/` |
| seams | `.seams-src/` |
| tone | `.tone-src/` (seeded once from `.seams-src/`, else from z0) |

Snapshots are taken the first time a pass touches a tile and reused after
that. Tuning a pass and re-running it starts from the same source. The
seams snapshot is the exception: it is refreshed whenever the seams stamp
changes (for example after a spots re-run), so it always starts from the
current tiles. If you reslice the map (`--tilewise` again), delete these
directories so the passes snapshot the new tiles.

## House tone

The restyle was painted in overlapping windows, and each window came back with
its own palette: a pale lime pond field next to a dark teal one, whole 512-px
squares a shade lighter. Feathering 16 px across an edge (`--polish-seams`)
does nothing for that. `--polish-tone` uses the procedural tiles (`tiles/`,
the render the restyle was made from), which have one consistent tone:

1. Fit one colour transform from the original to the restyle in CIE Lab over
   the whole city (a robust cubic plus a residual lookup table). That is the
   house style.
2. Find hard tone steps. They sit on z0 tile edges and on district and
   restyle-window edges. On each of those lines the residual (house-styled
   original minus restyle) is compared in 16-px bands on both sides, segment by
   segment. A step is cancelled only where the restyle itself jumps
   consistently along the line. Each side takes half the step, fading out over
   160 px, so there are no terraces in flat water.
3. Low-pass what is left with a robust Gaussian (sigma 96 px) on one global
   cell grid, and add it to every restyle pixel. Only low frequencies move, so
   the restyle's detail stays. A patch where the restyle drew different content
   (a tower on a field) gets a low weight and does not tint its surroundings.
4. Refill washed-out patches, where the restyle returned flat beige over
   buildings, from the house-styled original.

The edge is left alone: the pass tapers off from 384 px inside the coverage edge, never tones a pixel that is already viewer-fog beige, and only refills washes deep inside the city, so the vignette and fogline work stays as it was. A whole-city run caches its measurements and logs finished tiles (`.tone-measure.pkl`, `.tone-fields.npz`, `.tone-progress.txt`), so an interrupted run picks up where it stopped. Use `--tone-region TX0,TY0,TX1,TY1`
with `--tone-out DIR` to preview a few tiles without touching the map. After a
reslice, or after re-running an earlier pass, delete `.tone-src/` so it is
seeded again.

## Gaps

Some of the city came back from the restyle as a hole: a flat beige void, a
ghostly veil where towers are drawn at a quarter of their contrast, a block
that stops on a 512-px tile line, or a smeared patch. Most sit where two
districts overlap and each district's own edge fade left a wash. The tone pass
evens colour and cannot redraw any of these. `--polish-gaps` runs last:

1. Every z0 tile inside the city is scored on one global 16-px cell grid
   against the original (`tiles/`). The city is where the original has
   content, not its dithered dark edge fog, and lies deep inside the restyle
   coverage (the vignette field below -820 px), so the outer fog never counts.
   Smooth restyle water over the original's dithered water is not a gap.
2. A cell is a gap when the original has sharp edges (buildings, roads, trees)
   and the restyle has far fewer, or keeps them at a fraction of their
   contrast, or is flat beige there. Cells are grouped into regions with
   hysteresis (seeded at 0.4, grown through connected cells above 0.2) and
   classed as `void`, `cutoff` (a long side on a tile or window line) or
   `blur`. Whole tiles far below their neighbours' detail are listed as blurry
   squares too.
3. Each region is filled from the best free source. First come the restyle
   windows on disk: the windows overlap by 256 px, and another district's
   window often still has the area intact. A window counts when it correlates
   with the original at full contrast there and is not an unrestyled fog-window
   copy. Windows are tried greedily, up to four per region. What is left comes
   from the original through the fitted house-tone transform. Either source
   is nudged toward the surrounding restyle with a robust 96-px low-pass and
   blended in with a soft mask. Water and the original's fog dither are never
   refilled from the original.
4. Each filled region is scored again. A fill that does not lower its gap
   score is dropped and the restyle is kept (`"source": "kept"` in the report).

```bash
python3 lora/slice_frontend.py "${CORE[@]}" --polish-gaps \
  --gaps-backup /workspace/tinysz-core-z0-backup-gaps --gaps-report /tmp/gaps-report
```

Only the z0 tiles that change are written, after a copy of each goes to
`--gaps-backup DIR/0`. A re-run starts from those copies, so it lands on the
same tiles. Only the pyramid tiles above them are rebuilt, then the overview
and the manifest build. `--gaps-stage DIR` writes all of that to `DIR` (same
layout as `--out`) instead and leaves the map alone, for a review before
copying it over. `--gaps-report DIR` writes `gaps.json`, a ranked list with
the source used for each region, and `sheet.jpg`, the worst 40 as original,
before and after.

## Edges

The gaps pass only looks deep inside the city. Around the outer edge of the
restyle, and along each district's restyle rectangle, the restyle left
half-baked work: forest and blocks washed toward white, content that stops on
a straight tile or window line, motorways that end in the fog, roads drawn
over open water. `--polish-edges` (see `lora/edges.py`) runs after the gaps
pass:

1. The audit zone is the vignette field above -860 px (everything that is not
   deep city) plus 384 px either side of every district rectangle. The
   original is palette art, so each pixel is classed exactly: blank ground,
   water, the black fog dither, grey road, content.
2. On the global 16-px grid, four scores: `veil` (the original draws content,
   the restyle is fog-mixed against the house-styled original or lacks its
   edges; classed `white`, `cutoff` on a tile or window line, or `veil`),
   `road` (original road pixels the restyle no longer draws grey; `road-cut`
   when the road continues next to it), `phantom` (textured grey road pixels
   over open water or fog, 12 px from anything the original draws on land;
   blank ground counts as land, since it is also piers, dykes and roofs) and
   `ghost` (pale half-faded detail past the original's own edge, within 300 px
   of the coverage edge). Regions are grouped with the gaps hysteresis.
3. Fixes: `veil` from an intact restyle window, else the house-styled original
   with its black dither removed, only where the original really draws
   something; `road` from the house-styled original, on its road pixels only;
   `phantom` inpainted from the surrounding restyle; `ghost` faded to the
   viewer fog. Big regions are done in 1536-px blocks. Every fix fades out
   over 96 px toward a z0 tile the map does not have, so nothing stops on a
   straight tile edge.
4. Each block is re-scored with its own measure; a fix that does not lower it
   by 0.05 is dropped (`"source": "kept"`).

```bash
python3 lora/slice_frontend.py "${CORE[@]}" --polish-edges \
  --edges-backup /workspace/tinysz-core-z0-backup-edges --edges-report /tmp/edges-report
```

z0 tiles are copied to `DIR/0` before they change, and a re-run starts from
those copies. `DIR/work` caches the cell fields, the house style and one file
per fixed block, so an interrupted run picks up where it stopped; delete it
after changing `edges.py`. `--edges-dry-run` writes only the report.
`--edges-report DIR` writes `edges.json` (regions by kind, sources, scores
before and after), `before.jpg` and `after.jpg`. Only the pyramid tiles above
changed tiles are rebuilt; rerun `--web-tiles` afterwards.

## Spots file

`--polish-spots` takes a JSON file of blemishes that no general rule catches
without hurting elsewhere. Coordinates are global z0 pixels on the world's
grid (`tile * 512 + offset`). The live map's list is
[`lora/spots/tinysz-core.json`](../lora/spots/tinysz-core.json):

```json
{
  "spots": [
    {"kind": "water", "box": [3000, 14150, 4650, 15300], "sigma": 60, "where": "isolated water blob off Shekou"},
    {"kind": "fog", "center": [10234, 12230], "radius": [90, 150], "where": "expressway stub ending in the fog"}
  ]
}
```

- `fog`: fades an ellipse (`center`, `radius` as `[rx, ry]`) to beige,
  feathered over its outer half.
- `water`: redraws an isolated water blob inside `box` with an outline smoothed
  by `sigma` pixels, so straight tile-edge cuts round off.
- `veil`: pulls a milky wash on water inside `box` back toward the deep-water
  tone, keeping wave texture. Land, sand and buildings are left alone.
- `where` is a note for people and is ignored.

## Other flags

| Flag | Use |
| --- | --- |
| `--fade N` | Edge-fade radius in z0 pixels (default 768 for the vignette). |
| `--feather N` | Edge-fog feather radius for the plain slice. |
| `--pyramid-only` | Rebuilds the zoom levels and overview from the existing z0 tiles. |
| `--repair` | Re-composites only z0 tiles that contain viewer-fog beige, then fills gaps. |
| `--polish`, `--polish-edge` | Older edge passes, superseded by `--polish-vignette`. Don't run `--polish` before the vignette. |
