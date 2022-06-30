# xflsvg
This project exists to turn XFL files into data suitable for animation AI. It has two main purposes:

1. Convert XFL files into render traces.
2. Convert render traces into other formats (currently only SVG sequences).

# Installation
`pip install 'git+git@github.com:synthbot-anon/xflsvg.git'`

# Usage

## Compile XFL to render traces.

Command line:

```
$ python -m xflsvg input-file.xfl output-trace-folder/
```

Python:

```
from xflsvg import XflReader
from xflsvg.rendertrace import RenderTracer

reader = XflReader(input_folder)
renderer = RenderTracer()

timeline = reader.get_timeline()
with renderer:
    for frame in list(timeline):
        frame.render()
        renderer.save_frame(frame)
renderer.compile(output_path)
```

## Compile render traces to SVG.

Command line:

```
# This will output a sequence of SVGs.
# Each SVG file will be given a 4-digit frame suffix.
$ python -m xflsvg trace-folder/ output.svg
```

Python:

```
from xflsvg.rendertrace import RenderTraceReader
from xflsvg.svgrenderer import SvgRenderer

reader = RenderTraceReader(input_folder)
renderer = SvgRenderer()

timeline = reader.get_timeline()
with renderer:
    for frame in list(timeline):
        frame.render()
        renderer.save_frame(frame)
renderer.compile(output_path)
```

## Compile XFL to SVG.

Command line:

```
$ python -m xflsvg input.xfl output.svg
```

Python:

```
from xflsvg import XflReader
from xflsvg.svgrenderer import SvgRenderer

reader = XflReader(input_folder)
renderer = SvgRenderer()

timeline = reader.get_timeline()
with renderer:
    for frame in list(timeline):
        frame.render()
        renderer.save_frame(frame)
renderer.compile(output_path)
```


# Render traces
Render traces are recordings of all actions required to render a scene. They consist of the following files:
* `shapes.json`: This consists of XFL DOMShape objects that describe basic shapes. These are vector shapes made up of straight lines, bezier curves, line strokes, and fills.
* `frames.json`: This is a DAG (directed acyclic graph). Each node represents a frame, and each edge represents information for how to render one frame as part of another (including any matrix transformation or color transformations required).
* `labels.json`: This is a table of labels for each frame. This is explained in detail below.

## Understanding timelines and layers

```
                                               timeline
frame     0   1   2   3   4   5   6   7  8 ...
         ----------------------------------------------------
layer 1 |                                                   |
layer 2 |                                                   |
layer 3 |                                                   |
layer 3 |                                                   |
         ----------------------------------------------------
```

Animation is represented as a collection timelines, each of which is a 2D grid. A vertical slice of a timeline corresponds to a still shot, and it is called a frame. A horizontal slice of a timeline is called a layer. To render a scene, each frame of a timeline is rendered in sequence. Each cell of a timeline table represents a composition of other timeline frames that get posed and rendered together. The DAG structure of `frames.json` comes from the fact that each timeline table cell references potentially other timeline frames.
* Note: a vertical slice of a layer (i.e., a cell in the timeline table) is also called a frame.

To render a timeline frame:
1. Each corresponding layer frame is collected and rendered separately. This is done by rendering the required references and posing them (applying the appropriate transformations and masks). In `labels.json`, these aggregate frames (each corresponding to a single cell in a timeline table) has a `layer` label and a `frame` label identifying the cell.
2. The layer frames are aggregated into a single timeline frame. This is done by rendering each layer frame in sequence. In `labels.json`, these aggregate frames (each corresponding to a vertical slice in a timeline table) has a `timeline` label and a `frame` label identifying the vertical slice.

`labels.json` describes how frames fit into these timeline tables. Some frames in `frames.json` correspond to vertical slices of a timeline table, and these frames are identified by `(timeline, frame index)` pairs. Some frames in `frames.json` correspond to individual cells of a timeline table, and these frames are identified by `(layer, frame index)` pairs.

## Understanding the DAG of frames
Frames have the following attributes:
* `children`: Each child is rendered recursively, then the various following transformations are applied to get the final frame render.
* `mask`: This render mask is used to mask out (stencil) parts of the frame so that not everything gets rendered. This is often important for rendering eyes correctly.
* `filter`: This corresponds to an RGBA color transformation.
* `transform`: This corresponds to a matrix transformation (shift, scale, rotate, skew).

The recursion bottoms out at basic shapes, each of which is described as a DOMShape in `shapes.json`.

## Understanding DOMShapes
A DOMShape is an XML node consisting of lines, bezier curves, strokes, and fills. This defines a shape as a vector image. There is an `xfl_domshape_to_svg` function in `domshape.shape` of the `xflsvg` library, which turns the DOMShape into SVG data.

You can use the xflsvg library to compile DOMShapes directly to SVG.
```
from xflsvg.xflsvg import ShapeFrame
from xflsvg.svgrenderer import SvgRenderer

shape = ShapeFrame(domshapeString)
renderer = SvgRenderer()

with renderer:
    shape.render()
    renderer.save_frame(shape)
renderer.compile(output_path)
```
