---
name: blueprint
description: Turn a floor plan (an image or a DWG/DXF drawing), a pen sketch or a few reference images into a photoreal 3D house in Blender, then a film of it building itself. Every footing, post, board, tatami mat, shoji strip and roof panel is its own object, stills are matched against references, and the film opens on the input itself, assembles the house part by part on top of it, brings it to life and walks inside. Use when someone wants a plan, a sketch, a brochure page or a description of a house, an apartment, a villa or a room turned into 3D, renders, a walk-through, a "build" video or an architecture visualisation. 한국어 요청도 이 스킬이다 — "스케치로 3D 집 만들어줘", "분양 평면도(이미지)로 3D 아파트", "참고 사진처럼 집 지어줘", "집이 조립되는 영상", "도면 위에 집이 지어지는 영상", "건축 조립 애니메이션", "평면도로 집 안 걸어 들어가는 영상", "3D 집 렌더와 내레이션 영상", "DWG·DXF 도면으로 조립 영상", "CAD 평면도로 집이 지어지는 영상", "조경 도면으로 정원·공원 3D 영상". DWG/DXF 도면으로 실내 렌더 한 장만 만드는 일은 cad-interior-render가 맡는다.
---

# Blueprint

One input, one Blender scene in code, a set of stills, one film. Nothing is modelled by hand and no 3D file of the
house exists: the scene is a Python script that Claude writes, renders, looks at and fixes.

`SKILL_DIR` is the folder this file lives in. Renders go to `$BLUEPRINT_OUT` (default `./blueprint-out`).

## What you need

- **Blender 4.2 or newer** on PATH as `blender` (tested on 5.2 with Cycles on an NVIDIA GPU; any OptiX or CUDA card
  works, 8 GB is enough with the steps in "Fit the card").
- **Python 3** with `pillow` (the counter overlay) and, for reading a plan image, `opencv-python` and `numpy`; for a
  DWG/DXF drawing also `ezdxf` (a DWG goes through ODA File Converter to DXF first).
- **ffmpeg** on PATH (or `FFMPEG=/path/to/ffmpeg`).
- **Optional:** `ELEVENLABS_API_KEY` for the music. Without it `tools/music.py` mixes the action sounds alone.
- **Optional, for furniture that fits the drawing:** the MCPWorld Blender connector's asset tools
  (`blender_search_assets`, `blender_get_asset`, `blender_acquire_asset`); without them the furniture is modelled.
- **Optional, for a voice-over:** a local Qwen3-TTS voice clone, set up in `~/.blueprint/tts.json` (`python`, `runner`,
  `ref_wav`, `ref_text`, `takes`) or the `QWEN_TTS_*` variables; see `tools/voice.py`. No TTS service is called.
- **Optional, on a laptop:** `~/.blueprint/render.json` `{"before": [...], "after": [...]}`, two commands (argument
  lists) that switch the maker's cooling profile to performance and back; see "Fit the card".

## First run

The materials and plants are CC0 from Poly Haven. Each house fetches its own into `$BLUEPRINT_ASSETS`
(default `~/.blueprint/polyhaven`) and skips what is already there:

```
python "$SKILL_DIR/houses/courtyard/fetch_assets.py"
```

## The three houses inside

| House | Input | Folder | What it shows |
|---|---|---|---|
| **Courtyard house** | a pen sketch on paper (`sketch.png`) | `houses/courtyard/` | 14 x 14 m, a 7 x 7 m court with a red maple, tatami room, engawa, standing-seam ring roof. The film opens on the sketch, builds 2,444 parts on the paper in white clay with each system flashing its colour and a counter, then a wave from the maple turns paper into hillside and clay into cedar, washi and stone. |
| **Cliff villa** | 4 reference images | `houses/villa/` | A travertine pavilion on a headland at sunset, an infinity pool cantilevered over the cliff. The film runs in over the sea, builds the villa from tiles and blocks, fills the pool, lights it up and dives in through the open glass. |
| **Apartment** | a floor plan from a sales brochure | `houses/apartment/` | A 3-bedroom flat, walls traced from the plan's fill at its scale bar, doors and furniture read on a metre grid. A dollhouse view, then a walk through. |

Each house has `build.py` (the scene, stills), `film.py` (the film), `score.py` (the action sounds on the film's
timeline) and `music_plan.json` (an ElevenLabs composition plan). Copy the closest one for a new house.

A fourth template, **`houses/park/`**, builds a neighbourhood park from its landscape sheets (planting, facilities,
paving), see "A park from landscape drawings" below.

```
blender -b --factory-startup -P houses/courtyard/build.py -- tatami engawa corner --samples 192 --res 1920x1080
blender -b --factory-startup -P houses/courtyard/film.py -- --res 1920x1080 --samples 64
python houses/courtyard/hud.py --frames blueprint-out/courtyard/film --out blueprint-out/courtyard/silent.mp4
python tools/music.py --film blueprint-out/courtyard/silent.mp4 --score houses/courtyard/score.py --plan houses/courtyard/music_plan.json --out blueprint-out/courtyard/final.mp4
```

`film.py` resumes: frames already on disk are skipped, so a crashed render picks up where it stopped (delete any
empty placeholder first). `--stills 0,6,13` renders single moments for checks, `--step 3 --res 480x270 --samples 6`
a quick preview of the whole film.

## Building a new house

1. **Read the input into numbers, never into a mood.** A sketch: trust its dimension labels over its pixels (an image
   model draws a 14 m square 745 px wide and 670 px tall). A plan: find the scale bar, trace the wall fill, and read
   doors and windows by hand on a metre grid (`grid_view.py`); automatic opening detection puts doors across rooms.
   Write the rooms, walls, openings and furniture down as numbers before any geometry.
   A **DWG/DXF drawing** is read, not traced, with `tools/dxf_read.py` (metres, the drawing's own axes and origin):
   `layers` names the wall, door, window, furniture, planting and paving layers; `walls --walls <layers> --seal
   <window and door layers>` writes the walls as non-overlapping rectangles and the enclosed floor in the shape of
   `houses/apartment/walls.json` (look at its `--debug` picture); `dump --layers ...` lists every block insert
   (furniture, trees), outline, hatch (paving, beds), arc, text and dimension in metres, and `plan.json` is written
   from that list. What the drawing does not say (ceiling height, sill and head heights, furniture heights, the sun,
   the cameras) goes into `plan.json` with one line of reason each, and anything that is the person's call (storey
   height, a named finish) is asked before building. Read the notes against use: a wide opening onto a balcony is a
   door down to the floor even when the sheet says "window height 1,200" for every opening.
2. **Pin the look with 3-4 references.** Write image-model prompts for the views you will render (one high view, one
   room, one detail) and let the person generate them. They decide the materials, the light and the furniture.
3. **Build it the way a carpenter would.** Footings, sills, joists, posts, plates, tie beams, rafters, roof panels,
   cladding boards, floor boards, every tatami mat with its edging, every shoji with every kumiko strip, the glass,
   the furniture, the garden. Each one a separate object, tagged with its stage (`K.STAGE[0] = "frame"`): the film
   flies them in by stage and the counter counts them. Address repeated wings by a side and (u, v) coordinates so one
   function builds all four and the hips and valleys land on the diagonals.
   Loose furniture whose shape makes it real (sofa, bed, dining set, lamps, plants) can come from the MCPWorld asset
   tools instead: search in English with the size range, read a candidate before taking it (a buttoned leather
   Chesterfield does not belong in a new flat), acquire at 2k for what is near the camera and 1k for the rest, and
   place it with `K.fitted(path, x, y, (w, d, h), yaw)`. It keeps the model's proportions, matches the long side and
   warns when any axis misses by more than 5 cm: then take another asset, never squeeze this one. Built-ins sized by
   the drawing (wardrobes, kitchen runs and tops) stay in code. Record each asset's id, file, error, licence and
   source in `plan.json`, and copy the files next to the renders, not into this folder.
4. **Render stills next to the references and fix what differs.** Low resolution first (960x540, 32 samples), the
   reference beside the render, one list of differences, then fix and render again. Look for things that poke
   through: a beam end through a roof reads as a dot from above, a rock through a pool floor as a stain.
   When the stills are for someone else, have them judged by an agent that did not build them: it gets only the
   renders, the references and the plan picture, lists for each view what falls short with a number and a severity
   (high: a viewer sees at once it is not a photo; low: only on close inspection) and scores each view 0-100, and it
   never says how to fix. Fix the high ones that are a value (light, camera, colour) or a small change of form, leave
   cloth folds and extra props for later, ask the person where a finding argues with the drawing, and stop after two
   rounds or when only low findings are left. After every camera change look at the frame's outer 15% again.
5. **Pick the light last, and show both.** Blue hour reads moody and can read gloomy; a bright day after rain (a low
   sun, wet boards, a pale sky) reads open. `build.py --look day|dusk` switches the whole scene.
6. **Film it building itself.** Open on the input: the sketch as a textured plane mapped so its plan is the real
   size, a top-down camera with a long lens that spirals down while the lens shortens (the plan keeps its size while
   the perspective opens). Fly the parts in by stage in white clay with a colour flash per system. Then one event
   that makes it real (a wave of materials from the focal point, the lights coming on), a slow hold, and a move
   inside. End on the reference room's own framing.
7. **Fit the card.** Motion blur on thousands of animated parts runs an 8 GB card out of memory. Bake bevel modifiers
   into the meshes before keying (Cycles treats a modified mesh as deforming), and use decimated copies of heavy
   scanned plants (`make_tree_lod.py`: Poly Haven conifers are 2-7 million polygons each).
   On a laptop, a balanced cooling profile can cut the GPU's power limit and keep it cut once the card reaches its
   target temperature (an RTX 5050 laptop went from 40 W to 21 W for the rest of the render, frames took three times
   as long, and a performance profile never cut it). If `~/.blueprint/render.json` exists, run its `before` command
   ahead of any render longer than a few minutes and its `after` command when the render ends, also when it fails or
   is stopped. Watch the first minutes with `nvidia-smi --query-gpu=power.draw,enforced.power.limit,temperature.gpu
   --format=csv -l 10`.
8. **Score it on the film's clock.** `film.py` writes `timeline.json` with every landing, the wave, the lights and the
   dive; `score.py` puts a sound on each in its own material (stone, hollow wood, paper, metal). Find the music
   track's swell and pass `--offset` so it lands on the reveal.
   For a voice-over, write one sentence per line pinned to the film's moments
   (`houses/apartment/narration.json`: `{"at": "dive", "dt": 0.6, "say": "..."}` or `{"t": 0.3, ...}`) and pass
   `--narration`: each line is spoken by the local Qwen voice, cached, brought to -17 LUFS, and the music and action
   sounds duck under it. A warning names any line that runs into the next one; shorten it or move it.
9. **Check frames, not code.** Contact sheets every second of the film at low resolution before the long render.

## A park from landscape drawings

A landscape set comes as several sheets (planting, facilities, paving) over a shared base drawing (an xref). They are
read together into one scene, not one stage per sheet.

1. **DWG to DXF** with ODA File Converter (`ODAFileConverter.exe <in> <out> ACAD2018 DXF 0 1 "*.dwg"`), xrefs too.
2. **Clean the drawing into `site.json`** with `tools/site_prep.py site.config.json --out site.json --svg plan.svg
   --png preview.png`. The config, with a reason on every value, says:
   - the sheets and the xref;
   - the unit, when the xref has no `$INSUNITS`;
   - the window: the park only. A second copy of a plan (an answer sheet, a revision) is cut out here;
   - the layers that are notes;
   - which layers, blocks and sheets are curb, paving, hedge, shrub, groundcover, tree, facility;
   - the quantity table's layer, and overrides for rows whose symbol is not drawn, matched by count.

   It merges the sheets and drops every text, so no place name survives. It places inserts by their bbox centre (a
   play structure's insert point can lie outside the window). It reads species and heights from the quantity table.
   It writes `zones`: the regions the curbs close off, each `planted`, `hatched` or `open`. Its `selftest` (`site_prep.py selftest`) checks
   the xref, the excluded copy, the legend and that no text leaks. Check that the tree count per species matches the
   table, and grep the outputs for place names before showing anyone.
3. **Say what each zone is in `look.json`**:
   - `kinds` sets the default per kind;
   - `zones` overrides by id (a hatched play area is rubber, not pavers), each with its centre and a reason, because
     the ids change when `site.json` is rebuilt.

   A hatch on a paving sheet is paving. It is never a bed.
4. **Assets.** A manifest gives each species a model (a decimated `.blend` LOD, scaled to the table's H), the
   texture maps and an HDRI. Facilities come from `facilities.py` and `facility_types.json`: one type per block,
   built to the block's own outline (rounded outlines round play equipment are safety zones, not pads).
5. **Stills.** `blender -b --factory-startup -P houses/park/build.py -- --site site.json --look look.json --assets
   manifest.json --shots top,hero,play --res 960x540 --samples 32`. `top` is an ortho camera at 18 px/m that matches
   a 1920x1080 plan drawing of the same window; change `film_pose`'s `FILM_A` and `top()` together if your drawing
   frame differs. The ground beyond the park sinks to near-black, so no horizon or slab edge shows.
6. **Film.** Add `--film --fps 60 --samples 64 --res 1920x1080`:
   - **The shot:** the camera starts as a 3 km telephoto, equal to the top camera to within 3 px. It holds until
     `MOVE[0]`, then comes down and round to the oblique view while the field of view widens.
   - **Output:** `film/f####.png` (frame 0 and `MOVE[0]..MOVE[1]`), plus `film/film.json` for the compositor.
   - **Speed:** only the camera moves (persistent data), so a 1080p frame takes about 5 s on an 8 GB laptop card,
     and a 10 s film at 60 fps about 40 minutes.
   - **Compositing:** the line drawing turning into the 3D is done in compositing, over the static first frame. For
     example, a wipe with the plan's lines lingering behind its edge, ending in an alpha fade to whatever comes next.

## Rules that cost a day each to learn

- **Specular keeps far ground grey.** A dark ground still reads grey at a grazing angle because it reflects the sky.
  Fade the specular out with the colour.

- **Tone mapping:** Khronos PBR Neutral. AgX turns a sunset orange into beige.
- **Glass:** thin, Transparent mixed with Glossy by Fresnel, no refraction. Smooth shading on an unbevelled box
  turns glazing into lenses; smooth only with a bevel and harden normals.
- **Poly Haven "set" files** (a row of shrubs, a heap of rocks) are split into single plants (`model_variants`).
- **A Mix node clamps its factor** by default: turn `clamp_factor` off for a sun disc or a halo added over a sky.
- **Painted tile joints double up** once a slab is cut into real tiles for the film: drop the painted joints there.
- **The camera never faces a blank wall at close range.** Coming in through an opening it turns round to the view
  in the opening, then pulls back into the room.
- **Films run a length that is not round** (18-25 s), and each house gets its own.
- **Images in the repo:** only what the person owns or CC0. A brochure plan stays with the person who brought it.
