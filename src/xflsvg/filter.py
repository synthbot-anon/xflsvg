from collections import defaultdict
from contextlib import contextmanager
import os
from typing import Set, Tuple

from .util import splitext, get_matching_path
from .samplerenderer import SampleReader, create_filename
from .xflsvg import Frame


class AssetFilter:
    def __init__(self, args):
        self.check_fn = None

        self._labels_by_asset = {}
        self._assets_by_label = {}
        self._asset_paths_by_fla = {}

        self._allowed_tasks_by_path = None
        self._output_paths_by_fla = {}
        self._file_context = []
        self._switch_on_frame = None
        self._mask_depth = 0

        assert (not args.discard) or (
            not args.retain
        ), "You can't specify both --retain and --discard."
        if args.discard:
            discard_list, asset_paths_by_fla = self.get_filtered_list(args.discard)
            self.check_fn = lambda x: x not in discard_list
            self._default_render = True
            self._render_allowed = True
        elif args.retain:
            retain_list, asset_paths_by_fla = self.get_filtered_list(args.retain)
            self.check_fn = lambda x: x in retain_list
            self._default_render = False
            self._render_allowed = False
        else:
            self.check_fn = lambda x: True
            self._render_allowed = True
            self._default_render = True

        self._allowed_tasks_by_path = self.parse_allowed_tasks(args.input)

        if args.focus:
            focus_list, asset_paths_by_fla = self.get_filtered_list(args.focus)
            for fla, asset_paths in asset_paths_by_fla.items():
                for asset, paths in asset_paths.items():
                    self._output_paths_by_fla.setdefault(fla, {}).setdefault(
                        asset, set()
                    ).update(paths)
            self.focus_list_by_fla = defaultdict(list)
            for fla, asset in focus_list:
                self.focus_list_by_fla[fla].append(asset)
            self._default_in_focus = False
            self._in_focus = False
            self._finish_on_frame = None
            self._finished = False
        else:
            self.focus_list_by_fla = None
            self._default_in_focus = True
            self._in_focus = False
            self._finish_on_frame = None
            self._finished = False

    def parse_allowed_tasks(self, input_spec):
        if "[" not in input_spec:
            return None

        result = {}
        input_path, filter_spec = input_spec.split("[", maxsplit=1)
        filtered_lists, self._output_paths_by_fla = self.get_filtered_list(filter_spec)

        for fla, asset in filtered_lists:
            result.setdefault(fla, set()).add(asset)

        return result

    def get_tasks(self, input_relpath, output_path, batch):
        basename = os.path.basename(os.path.normpath(input_relpath))
        filename, ext = splitext(basename)
        filename = filename.rstrip("/\\")

        if self._allowed_tasks_by_path == None:
            if self.focus_list_by_fla == None:
                if not batch:
                    output = output_path
                else:
                    output = os.path.join(output_path, input_relpath)
                yield None, output, lambda x: True
            else:
                for focus_item in self.focus_list_by_fla[filename]:
                    for relpath in self._output_paths_by_fla[filename][focus_item]:
                        dirname = os.path.dirname(relpath)
                        new_fn = create_filename(filename, focus_item, None, None)
                        yield None, f"{output_path}/{dirname}/{new_fn}", focus_item
            return

        if filename not in self._allowed_tasks_by_path:
            return

        for asset, relpaths in self._output_paths_by_fla[filename].items():
            for relpath in relpaths:
                for focus_fn in self.focus_list_by_fla[filename]:
                    yield asset, os.path.join(output_path, relpath), focus_fn

    def allow_asset(self, fla, asset):
        return self.check_fn((fla, asset))

    def get_filtered_list(self, filter_spec) -> Set[Tuple[str, str]]:
        input_path = filter_spec.split("[", maxsplit=1)[0]
        labels_by_asset, assets_by_label, asset_paths_by_fla = self.load_assets(
            input_path
        )

        if "[" not in filter_spec:
            return labels_by_asset.keys(), asset_paths_by_fla

        assert filter_spec[-1] == "]"

        result = set()

        label_filter_start = filter_spec.index("[")
        label_filters = [
            x.strip() for x in filter_spec[label_filter_start:-1].split(",")
        ]
        for label in label_filters:
            result.update(assets_by_label[label])

        return result, asset_paths_by_fla

    def load_assets(self, input_path):
        if input_path in self._labels_by_asset:
            return (
                self._labels_by_asset[input_path],
                self._assets_by_label[input_path],
                self._asset_paths_by_fla[input_path],
            )

        assets_path, ext = splitext(input_path)

        if ext == ".samples":
            reader = SampleReader(assets_path)
            labels, orig_paths = reader.get_labels()
        else:
            raise Exception("cannot create a filter from input type", ext)

        self._labels_by_asset[input_path] = labels
        self._asset_paths_by_fla[input_path] = orig_paths

        # reverse the labels dictionary so it's easier to find things by label
        assets_by_label = defaultdict(set)
        self._assets_by_label[input_path] = assets_by_label
        for asset, labels in labels.items():
            for l in labels:
                assets_by_label[l].add(asset)

        return (
            self._labels_by_asset[input_path],
            self._assets_by_label[input_path],
            self._asset_paths_by_fla[input_path],
        )

    def wrap_push_transform(self, push_transform):
        def _modified(frame, *args, **kwargs):
            push_transform(frame, *args, **kwargs)

            if frame.element_type != "asset":
                return

            if self._file_context[-1][1] == frame.element_id:
                self._in_focus = True
                self._finish_on_frame = frame

            if not self._in_focus:
                return

            if self._default_render:
                if self._render_allowed:
                    asset_allowed = self.allow_asset(
                        self._file_context[-1][0], frame.element_id
                    )
                    if not asset_allowed:
                        self._render_allowed = False
                        self._switch_on_frame = frame
            else:
                if not self._render_allowed:
                    asset_allowed = self.allow_asset(
                        self._file_context[-1][0], frame.element_id
                    )
                    if asset_allowed:
                        self._render_allowed = True
                        self._switch_on_frame = frame

        return _modified

    def wrap_pop_transform(self, pop_transform):
        def _modified(frame, *args, **kwargs):
            if frame == self._switch_on_frame:
                self._render_allowed = not self._render_allowed
                self._switch_on_frame = None

            if frame == self._finish_on_frame:
                self._in_focus = self._default_in_focus
                self._finish_on_frame = None

            if not self._in_focus:
                pop_transform(Frame(), *args, **kwargs)
            else:
                pop_transform(frame, *args, **kwargs)

        return _modified

    def wrap_render_shape(self, render_shape):
        def _modified(frame, *args, **kwargs):
            if (self._mask_depth > 0) or (self._in_focus and self._render_allowed):
                render_shape(frame, *args, **kwargs)

        return _modified

    def wrap_push_mask(self, push_mask):
        def _modified(frame, *args, **kwargs):
            if self._in_focus:
                push_mask(frame, *args, **kwargs)
                self._mask_depth += 1

        return _modified

    def wrap_pop_mask(self, pop_mask):
        def _modified(frame, *args, **kwargs):
            if self._in_focus:
                pop_mask(frame, *args, **kwargs)
                self._mask_depth -= 1

        return _modified

    def wrap_push_masked_render(self, push_masked_render):
        def _modified(frame, *args, **kwargs):
            if self._in_focus:
                push_masked_render(frame, *args, **kwargs)

        return _modified

    def wrap_pop_masked_render(self, pop_masked_render):
        def _modified(frame, *args, **kwargs):
            if self._in_focus:
                pop_masked_render(frame, *args, **kwargs)

        return _modified

    def wrap_on_frame_rendered(self, on_frame_rendered):
        def _modified(frame, *args, **kwargs):
            on_frame_rendered(frame, *args, **kwargs)

        return _modified

    @contextmanager
    def filtered_render_context(self, file_base, renderer, focus_fn):
        self._file_context.append((file_base, focus_fn))
        prev_push = renderer.push_transform
        prev_pop = renderer.pop_transform
        prev_shape = renderer.render_shape
        prev_push_mask = renderer.push_mask
        prev_pop_mask = renderer.pop_mask
        prev_push_masked_render = renderer.push_masked_render
        prev_pop_masked_render = renderer.pop_masked_render
        prev_rendered = renderer.on_frame_rendered

        renderer.push_transform = self.wrap_push_transform(renderer.push_transform)
        renderer.pop_transform = self.wrap_pop_transform(renderer.pop_transform)
        renderer.render_shape = self.wrap_render_shape(renderer.render_shape)
        renderer.push_mask = self.wrap_push_mask(renderer.push_mask)
        renderer.pop_mask = self.wrap_pop_mask(renderer.pop_mask)
        renderer.push_masked_render = self.wrap_push_masked_render(
            renderer.push_masked_render
        )
        renderer.pop_masked_render = self.wrap_pop_masked_render(
            renderer.pop_masked_render
        )
        renderer.on_frame_rendered = self.wrap_on_frame_rendered(
            renderer.on_frame_rendered
        )

        self._in_focus = self._default_in_focus
        self._finish_on_frame = None
        try:
            with renderer:
                yield
        finally:
            renderer.push_transform = prev_push
            renderer.pop_transform = prev_pop
            renderer.render_shape = prev_shape
            renderer.push_mask = prev_push_mask
            renderer.pop_mask = prev_pop_mask
            renderer.push_masked_render = prev_push_masked_render
            renderer.pop_masked_render = prev_pop_masked_render
            renderer.on_frame_rendered = prev_rendered
            self._file_context.pop()
