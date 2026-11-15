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

Snapshots are taken the first time a pass touches a tile and reused after
that. Tuning a pass and re-running it starts from the same source. The
seams snapshot is the exception: it is refreshed whenever the seams stamp
changes (for example after a spots re-run), so it always starts from the
current tiles. If you reslice the map (`--tilewise` again), delete these
directories so the passes snapshot the new tiles.

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
