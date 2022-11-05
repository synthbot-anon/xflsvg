from collections import defaultdict
from contextlib import contextmanager
import os
import re
from typing import Set, Tuple

from .util import splitext, get_matching_path, InputFileSpec
from .samplerenderer import create_filename, SampleReader
from .xflsvg import Frame


def join_path(folder, file):
    if folder and file:
        return os.path.join(folder, file)
    else:
        return folder or file


class AssetFilter:
    def __init__(self, args):
        self.relevant_asset_patterns = None
        self.allow_relevant_assets = None
        self._available_timelines = None
        self._dest_paths_by_fla = {}
        self._file_context = []
        self._switch_on_frame = None
        self._mask_depth = 0
        self.frame_empty = True
        self.seq_labels = {}

        # Figure out which pieces to keep/remove within a render
        assert (not args.discard) or (
            not args.retain
        ), "You can't specify both --retain and --discard."
        if args.discard:
            discard_list, fla_asset_relpaths = self._get_filtered_list(args.discard)
            self.relevant_asset_patterns = discard_list
            self.allow_relevant_assets = False
            self._default_render = True
            self._render_allowed = True
        elif args.retain:
            retain_list, fla_asset_relpaths = self._get_filtered_list(args.retain)
            self.relevant_asset_patterns = retain_list
            self.allow_relevant_assets = True
            self.allow_asset_fn = lambda x: x in retain_list
            self._default_render = False
            self._render_allowed = False
        else:
            self._render_allowed = True
            self._default_render = True

        # Figure out what to render from which files
        self._available_timelines, self._fla_asset_destpath = self._get_timelines(
            args.input
        )

        if args.isolate:
            isolate_list, self._fla_asset_destpath = self._get_filtered_list(
                args.isolate
            )
            assert (
                self._fla_asset_destpath != None
            ), "--isolate param can only be an .asset or .samples"

            self.isolated_items_by_fla = defaultdict(list)
            for fla, asset in isolate_list:
                self.isolated_items_by_fla[fla].append(asset)

            self._has_isolated_task = False
            self._in_isolated_item = False
            self._finish_on_frame = None
            self._finished = False
        else:
            self.isolated_items_by_fla = None
            self._has_isolated_task = True
            self._in_isolated_item = False
            self._finish_on_frame = None
            self._finished = False

        if args.seq_labels:
            self.seq_labels = SampleReader.load_samples(args.seq_labels.pathspec)[0]

    @classmethod
    def _get_filtered_list(cls, input) -> Set[Tuple[str, str]]:
        if input.ext == ".samples":
            (
                labels_by_asset,
                assets_by_label,
                fla_asset_relpaths,
            ) = SampleReader.load_samples(input.pathspec)
            relevant_assets = labels_by_asset.keys()
        elif input.ext == ".asset":
            relevant_assets = {(None, input.path)}
            assets_by_label = {}
            fla_asset_relpaths = {None: {input.path: [None]}}
        elif input.ext == ".regex":
            relevant_assets = {(None, re.compile(input.path))}
            assets_by_label = {}
            fla_asset_relpaths = None
        else:
            raise Exception(
                "--retain and --discard should be a .samples, .asset, or .regex. --isolate should be a .samples or .asset."
            )

        if not input.param:
            return relevant_assets, fla_asset_relpaths

        assert input.ext == ".samples", "You can't subset a .asset or .regex"

        result = set()
        label_filters = [x.strip() for x in input.param.split(",")]
        for label in label_filters:
            result.update(assets_by_label[label])

        return result, fla_asset_relpaths

    def _get_timelines(self, input):
        if not input.param:
            fla = os.path.basename(input.path.rstrip("/"))
            name, ext = splitext(fla)
            return {None: set([None])}, name

        filtered_lists, fla_asset_path = self._get_filtered_list(
            InputFileSpec.from_spec(input.param)
        )

        timelines = {}
        for fla, asset in filtered_lists:
            timelines.setdefault(fla, set()).add(asset)

        return timelines, fla_asset_path

    def _get_fla_isolated_tasks(self, fla):
        if self.isolated_items_by_fla == None:
            yield None, None
            return

        for isolated_item in self.isolated_items_by_fla.get(fla, []):
            for relpath in self._fla_asset_destpath[fla][isolated_item]:
                dirname = os.path.dirname(relpath)
                new_fn = create_filename(fla, isolated_item, None, None)
                yield isolated_item, os.path.join(dirname, new_fn)

        for isolated_item in self.isolated_items_by_fla.get(None, []):
            new_fn = create_filename(fla, isolated_item, None, None)
            yield isolated_item, new_fn

    def get_tasks(self, input, output, batch=False):
        basename = os.path.basename(os.path.normpath(input.path.rstrip("/")))
        if input.ext == ".trace":
            basename = splitext(basename)[0]

        timelines = set()
        timelines.update(self._available_timelines.get(basename, []))
        timelines.update(self._available_timelines.get(None, []))

        for timeline in timelines:
            for isolated_item, relpath in self._get_fla_isolated_tasks(basename):
                if relpath:
                    dest_path = os.path.join(output.path, relpath)
                elif timeline:
                    if batch:
                        dest_path = os.path.join(
                            output.matching_descendent(input).path, timeline
                        )
                    else:
                        dest_path = os.path.join(output.path, timeline)
                else:
                    if batch:
                        dest_path = output.matching_descendent(input).path
                    else:
                        dest_path = output.path

                seq_labels = self.seq_labels.get((basename, isolated_item), [])

                yield timeline, dest_path, isolated_item, seq_labels

    def _allow_asset(self, fla, asset):
        if self.relevant_asset_patterns == None:
            return True

        found_match = False
        for pattern_fla, pattern in self.relevant_asset_patterns:
            if pattern_fla != fla and pattern_fla != None:
                continue

            if isinstance(pattern, str):
                if asset == pattern:
                    found_match = True
                    break
            elif isinstance(pattern, re.Pattern):
                if pattern.match(asset):
                    found_match = True
                    break

        return found_match == self.allow_relevant_assets

    def _wrap_push_transform(self, push_transform):
        def _modified(frame, *args, **kwargs):
            push_transform(frame, *args, **kwargs)

            if frame.element_type != "asset":
                return

            if self._file_context[-1][1] == frame.element_id:
                self._in_isolated_item = True
                self._finish_on_frame = frame

            if not self._in_isolated_item:
                return

            if self._default_render:
                if self._render_allowed:
                    asset_allowed = self._allow_asset(
                        self._file_context[-1][0], frame.element_id
                    )
                    if not asset_allowed:
                        self._render_allowed = False
                        self._switch_on_frame = frame
            else:
                if not self._render_allowed:
                    asset_allowed = self._allow_asset(
                        self._file_context[-1][0], frame.element_id
                    )
                    if asset_allowed:
                        self._render_allowed = True
                        self._switch_on_frame = frame

        return _modified

    def _wrap_pop_transform(self, pop_transform):
        def _modified(frame, *args, **kwargs):
            if frame == self._switch_on_frame:
                self._render_allowed = not self._render_allowed
                self._switch_on_frame = None

            if frame == self._finish_on_frame:
                self._in_isolated_item = self._has_isolated_task
                self._finish_on_frame = None

            if not self._in_isolated_item:
                pop_transform(Frame(), *args, **kwargs)
            else:
                pop_transform(frame, *args, **kwargs)

        return _modified

    def _wrap_render_shape(self, render_shape):
        def _modified(frame, *args, **kwargs):
            if self._mask_depth > 0:
                render_shape(frame, *args, **kwargs)
            elif self._in_isolated_item and self._render_allowed:
                render_shape(frame, *args, **kwargs)
                self.frame_empty = False

        return _modified

    def _wrap_push_mask(self, push_mask):
        def _modified(frame, *args, **kwargs):
            if self._in_isolated_item:
                push_mask(frame, *args, **kwargs)
                self._mask_depth += 1

        return _modified

    def _wrap_pop_mask(self, pop_mask):
        def _modified(frame, *args, **kwargs):
            if self._in_isolated_item:
                pop_mask(frame, *args, **kwargs)
                self._mask_depth -= 1

        return _modified

    def _wrap_push_masked_render(self, push_masked_render):
        def _modified(frame, *args, **kwargs):
            if self._in_isolated_item:
                push_masked_render(frame, *args, **kwargs)

        return _modified

    def _wrap_pop_masked_render(self, pop_masked_render):
        def _modified(frame, *args, **kwargs):
            if self._in_isolated_item:
                pop_masked_render(frame, *args, **kwargs)

        return _modified

    def _wrap_on_frame_rendered(self, on_frame_rendered):
        def _modified(frame, *args, **kwargs):
            on_frame_rendered(frame, *args, **kwargs)

        return _modified

    @contextmanager
    def filtered_render_context(self, file_base, renderer, isolated_task):
        self._file_context.append((file_base, isolated_task))
        prev_push = renderer.push_transform
        prev_pop = renderer.pop_transform
        prev_shape = renderer.render_shape
        prev_push_mask = renderer.push_mask
        prev_pop_mask = renderer.pop_mask
        prev_push_masked_render = renderer.push_masked_render
        prev_pop_masked_render = renderer.pop_masked_render
        prev_rendered = renderer.on_frame_rendered

        renderer.push_transform = self._wrap_push_transform(renderer.push_transform)
        renderer.pop_transform = self._wrap_pop_transform(renderer.pop_transform)
        renderer.render_shape = self._wrap_render_shape(renderer.render_shape)
        renderer.push_mask = self._wrap_push_mask(renderer.push_mask)
        renderer.pop_mask = self._wrap_pop_mask(renderer.pop_mask)
        renderer.push_masked_render = self._wrap_push_masked_render(
            renderer.push_masked_render
        )
        renderer.pop_masked_render = self._wrap_pop_masked_render(
            renderer.pop_masked_render
        )
        renderer.on_frame_rendered = self._wrap_on_frame_rendered(
            renderer.on_frame_rendered
        )

        self._in_isolated_item = self._has_isolated_task
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
