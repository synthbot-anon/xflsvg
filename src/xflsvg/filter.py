from collections import defaultdict
from contextlib import contextmanager
from typing import Set, Tuple

from .util import splitext, get_matching_path
from .samplerenderer import SampleReader


class AssetFilter:
    def __init__(self, args):
        self.check_fn = None
        self._labels_by_asset = {}
        self._assets_by_label = {}
        self._file_context = []
        self._switch_on_frame = None

        assert (not args.discard) or (not args.retain)
        if args.discard:
            discard_list = self.get_filtered_list(args.discard)
            self.check_fn = lambda x: x not in discard_list
            self._default_render = True
            self._render_allowed = True
        elif args.retain:
            retain_list = self.get_filtered_list(args.retain)
            self.check_fn = lambda x: x in retain_list
            self._default_render = False
            self._render_allowed = False
        else:
            self.check_fn = lambda x: True

    def allow_asset(self, fla, asset):
        return self.check_fn((fla, asset))

    def get_filtered_list(self, filter_spec) -> Set[Tuple[str, str]]:
        input_path = filter_spec.split("[", maxsplit=2)[0]
        labels_by_asset, assets_by_label = self.load_assets(input_path)

        if "[" not in filter_spec:
            return labels_by_asset.keys()

        assert filter_spec[-1] == "]"

        result = set()

        label_filter_start = filter_spec.index("[")
        label_filters = [x.strip() for x in f[label_filter_start:-1].split(",")]
        for label in label_filters:
            result.update(assets_by_label[label])

        return result

    def load_assets(self, input_path):
        if input_path in self._labels_by_asset:
            return self._labels_by_asset[input_path], self._assets_by_label[input_path]

        assets_path, ext = splitext(input_path)

        if ext == ".samples":
            reader = SampleReader(assets_path)
            labels = reader.get_labels()
        else:
            raise Exception("cannot create a filter from input type", ext)

        self._labels_by_asset[input_path] = labels

        # reverse the labels dictionary so it's easier to find things by label
        assets_by_label = defaultdict(set)
        self._assets_by_label[input_path] = assets_by_label
        for asset, labels in labels.items():
            for l in labels:
                assets_by_label[l].add(asset)

        return self._labels_by_asset[input_path], self._assets_by_label[input_path]

    def wrap_push_transform(self, push_transform):
        def _modified(frame, *args, **kwargs):
            if frame.element_type != "asset":
                push_transform(frame, *args, **kwargs)
                return

            if self._default_render:
                if self._render_allowed:
                    asset_allowed = self.allow_asset(
                        self._file_context[-1], frame.element_id
                    )
                    if not asset_allowed:
                        self._render_allowed = False
                        self._switch_on_frame = frame
            else:
                if not self._render_allowed:
                    asset_allowed = self.allow_asset(
                        self._file_context[-1], frame.element_id
                    )
                    if asset_allowed:
                        self._render_allowed = True
                        self._switch_on_frame = frame

            push_transform(frame, *args, **kwargs)

        return _modified

    def wrap_pop_transform(self, pop_transform):
        def _modified(frame, *args, **kwargs):
            pop_transform(frame, *args, **kwargs)

        return _modified

    def wrap_render_shape(self, render_shape):
        def _modified(frame, *args, **kwargs):
            if self._render_allowed:
                render_shape(frame, *args, **kwargs)

        return _modified

    def wrap_on_frame_rendered(self, on_frame_rendered):
        def _modified(frame, *args, **kwargs):
            if self._render_allowed:
                on_frame_rendered(frame, *args, **kwargs)

            if frame == self._switch_on_frame:
                self._render_allowed = not self._render_allowed
                self._switch_on_frame = None

        return _modified

    @contextmanager
    def filtered_render_context(self, file_base, renderer):
        self._file_context.append(file_base)
        prev_push = renderer.push_transform
        prev_pop = renderer.pop_transform
        prev_shape = renderer.render_shape
        prev_rendered = renderer.on_frame_rendered

        renderer.push_transform = self.wrap_push_transform(renderer.push_transform)
        renderer.pop_transform = self.wrap_pop_transform(renderer.pop_transform)
        renderer.render_shape = self.wrap_render_shape(renderer.render_shape)
        renderer.on_frame_rendered = self.wrap_on_frame_rendered(
            renderer.on_frame_rendered
        )
        try:
            with renderer:
                yield
        finally:
            renderer.push_transform = prev_push
            renderer.pop_transform = prev_pop
            renderer.render_shape = prev_shape
            renderer.on_frame_rendered = prev_rendered
            self._file_context.pop()
