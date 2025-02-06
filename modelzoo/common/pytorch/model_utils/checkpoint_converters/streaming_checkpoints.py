# Copyright 2022 Cerebras Systems.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging
import math
import os
import re
from typing import Union

import torch

from cerebras_appliance.utils.units import convert_byte_unit
from cerebras_pytorch.saver.pt_h5_saver import PyTorchH5Saver
from cerebras_pytorch.utils.nest import recurse_spec


def convert_file_size_to_int(size: Union[int, str]):
    """
    Converts a size expressed as a string with digits and unit (like `"5MB"`) to an integer (in bytes).

    Args:
        size (`int` or `str`): The size to convert. Will be directly returned if an `int`.

    Example:
    ```py
    >>> convert_file_size_to_int("10GiB")
    10737418240
    ```
    """
    if isinstance(size, str):
        match = re.search(r'(\d+)(.*)', size)
        if not match:
            raise ValueError(
                f"size '{size}' is not in a valid format. Use an integer followed by the unit, e.g., '10GB'."
            )
        try:
            num = int(match.group(1))
            unit = match.group(2)
            size = convert_byte_unit(num, "B", src_unit=unit)
        except:
            raise ValueError(
                f"size '{size}' is not in a valid format. Use an integer followed by the unit, e.g., '10GB'."
            )
    return size


def dtype_byte_size(dtype: torch.dtype) -> float:
    """
    Returns the size (in bytes) occupied by one parameter of type `dtype`.

    Example:

    ```py
    >>> dtype_byte_size(torch.float32)
    4.0
    ```
    """
    if dtype == torch.bool:
        return 1 / 8
    if dtype.is_floating_point:
        return torch.finfo(dtype).bits / 8
    else:
        return torch.iinfo(dtype).bits / 8


class StreamingShardedHFReader:
    r"""Allows sharded HuggingFace checkpoints to be read in a streaming manner
    rather than loading all shards into memory all at once. The underlying
    checkpoint is read-only.
    
    Only one shard is stored into memory at a time. For this reason, accessing
    random keys may slow due to the switching cost (loading) between shards. For
    this reason, it is recommend that keys are accessed in the order given by
    `self.keys()` or `self.__iter__()` as keys that appear in the same shard
    are in consecutive order.

    Args:
        index_file: Path to .index.json file.

    """

    def __init__(self, index_file: str) -> None:
        self.index_dir = os.path.dirname(index_file)
        with open(index_file, "r") as f:
            index = json.load(f)
            self.weight_map = index["weight_map"]

        self.file2keys = {
            file: [] for file in sorted(set(self.weight_map.values()))
        }

        for file in self.file2keys:
            shard_path = os.path.join(self.index_dir, file)
            if not os.path.exists(shard_path):
                raise FileNotFoundError(
                    f"Detected missing checkpoint shard: {shard_path}"
                )

        for key, file in self.weight_map.items():
            self.file2keys[file].append(key)

        self.active_file_name = None
        self.active_file_data = None

    def __len__(self):
        return len(self.weight_map)

    def __iter__(self):
        for file in self.file2keys:
            for key in self.file2keys[file]:
                yield key

    def __getitem__(self, key):
        if key not in self.weight_map:
            raise KeyError

        file = self.weight_map[key]
        if file != self.active_file_name:
            self.active_file_name = file
            if self.active_file_data is not None:
                # Drop old data *before* load.
                # Without this, peak mem usage = prev shard + new shard
                del self.active_file_data
            self.active_file_data = torch.load(
                os.path.join(self.index_dir, file), map_location="cpu",
            )
        return self.active_file_data[key]

    def items(self):
        for key in self.keys():
            yield key, self[key]

    def keys(self):
        return list(self.__iter__())

    def values(self):
        for key in self.keys():
            yield self[key]


class StreamingShardedHFWriter:
    r"""Writes a HuggingFace sharded checkpoint in a streaming manner rather
    than accumulating the full checkpoint into memory and then writing all
    shards at the end.
    
    A partial checkpoint is accumulated into memory until it reaches the shard
    size limit at which point this shard is written to disk.

    It is essential that `self.save()` is called in order to flush the last
    shard to disk and to save other required metadata.

    The StreamingShardedHFWriter class supports re-accessing and even updating
    keys that have already been written. Note that accessing existing keys
    randomly may be slow due to the switching cost (loading) between shards that
    have already been written to disk. For this reason, it is recommend that
    keys are re-accessed in the order given by `self.keys()` or
    `self.__iter__()` as keys that appear in the same shard are in consecutive
    order. Note that updating data stored in a shard may result in a shard that
    is smaller/larger than the original shard size, as StreamingShardedHFWriter
    will not intelligently split or coalesce shards during updates. 

    Args:
        checkpoint_dir: Path to where a new directory will be created to store
                        the checkpoint shards.
        shard_size:     The maximum size each checkpoint shard should be. Can be
                        an integer representing the number of bytes, or a
                        formatted string (ex: "10GB").
                        See convert_file_size_to_int for valid string formats.

    """

    def __init__(
        self, checkpoint_dir: str, shard_size: Union[str, int] = "10GB"
    ) -> None:
        self.checkpoint_dir = checkpoint_dir
        os.mkdir(self.checkpoint_dir)
        self.index_file = os.path.join(
            self.checkpoint_dir, "pytorch_model.bin.index.json"
        )
        self.weight_map = {}
        self.current_file_number = 0
        self.last_file_number = 0
        self.total_shards_finalized = 0
        self.active_file_name = self.get_filename(
            self.current_file_number, self.total_shards_finalized
        )
        self.active_file_data = {}
        self.file_size = {self.active_file_name: 0}
        self.dirty = False
        self.max_shard_size = convert_file_size_to_int(shard_size)

    def __len__(self):
        return len(self.weight_map)

    def __iter__(self):
        for key in self.weight_map:
            yield key

    def __getitem__(self, key):
        if key not in self.weight_map:
            raise KeyError

        file = self.weight_map[key]
        if file != self.active_file_name:
            self._switch_shards(file)

        return self.active_file_data[key]

    def __setitem__(self, key, value):

        if key in self.weight_map:
            # We are updating a key that has already been seen before
            file = self.weight_map[key]
            if self.active_file_name != file:
                self._switch_shards(file)

            old_value = self.active_file_data[key]
            old_weight_size = math.ceil(
                old_value.numel() * dtype_byte_size(old_value.dtype)
            )
            weight_size = math.ceil(
                value.numel() * dtype_byte_size(value.dtype)
            )
            delta_size = weight_size - old_weight_size

            if (
                self.file_size[self.active_file_name] + delta_size
                > self.max_shard_size
            ):
                logging.warn(
                    f"Updating {key} is causing shard {self.active_file_name} to be larger than limit"
                )

            self.active_file_data[key] = value
            self.weight_map[key] = self.active_file_name
            self.file_size[self.active_file_name] += delta_size
            self.dirty = True
        else:
            # We are adding a new key that hasn't been seen before

            weight_size = math.ceil(
                value.numel() * dtype_byte_size(value.dtype)
            )

            if self.current_file_number != self.last_file_number:
                self._switch_shards(
                    self.get_filename(
                        self.last_file_number, self.total_shards_finalized
                    )
                )

            # Create a new shard if this new weight "tips" us over the limit:
            if (
                self.file_size[self.active_file_name] + weight_size
                > self.max_shard_size
            ):
                self._flush()
                self.last_file_number += 1
                self.current_file_number = self.last_file_number

                if self.active_file_data is not None:
                    # Drop old data *before* load.
                    # Without this, peak mem usage = prev shard + new shard
                    del self.active_file_data
                self.active_file_data = {}
                self.active_file_name = self.get_filename(
                    self.current_file_number, self.total_shards_finalized
                )
                self.file_size[self.active_file_name] = 0

            self.active_file_data[key] = value
            self.weight_map[key] = self.active_file_name
            self.file_size[self.active_file_name] += weight_size
            self.dirty = True

    @staticmethod
    def get_filename(file_number, total_shards=0):
        return f"pytorch_model-{file_number+1:05d}-of-{total_shards:05d}.bin"

    def _flush(self):
        if self.dirty:
            torch.save(
                self.active_file_data,
                os.path.join(self.checkpoint_dir, self.active_file_name),
            )
            self.dirty = False

    def _switch_shards(self, new_file):
        self._flush()
        self.active_file_name = new_file
        if self.active_file_data is not None:
            # Drop old data *before* load.
            # Without this, peak mem usage = prev shard + new shard
            del self.active_file_data
        self.active_file_data = torch.load(
            os.path.join(self.checkpoint_dir, new_file), map_location="cpu",
        )

    def save(self):
        self._flush()

        total_size = sum(shard_size for shard_size in self.file_size.values())

        # Finalize total number of shards:
        new_total_shards = self.last_file_number + 1
        if self.total_shards_finalized != new_total_shards:
            # Step 1: Figure out the prev file -> new file mapping so that
            # we can rename the files / data structures
            file_renames = {
                self.get_filename(
                    i, self.total_shards_finalized
                ): self.get_filename(i, new_total_shards)
                for i in range(new_total_shards)
            }

            # Step 2: Rename the checkpoint files
            for prev_file, new_file in file_renames.items():
                os.rename(
                    os.path.join(self.checkpoint_dir, prev_file),
                    os.path.join(self.checkpoint_dir, new_file),
                )

            # Step 3: Update the weight map & file size data structures:
            self.weight_map = {
                key: file_renames[prev_file]
                for key, prev_file in self.weight_map.items()
            }

            self.file_size = {
                file_renames[prev_file]: size
                for prev_file, size in self.file_size.items()
            }

            # Step 4: Update the # of finalized shards so that future updates
            # to the writer will be able to correctly pick up the shards
            self.total_shards_finalized = new_total_shards

        with open(self.index_file, "w") as f:
            f.write(
                json.dumps(
                    {
                        "metadata": {"total_size": total_size,},
                        "weight_map": self.weight_map,
                    },
                    indent=4,
                )
            )

    def items(self):
        for key in self.keys():
            yield key, self[key]

    def keys(self):
        return list(self.__iter__())

    def values(self):
        for key in self.keys():
            yield self[key]


class StreamingCSLeaf:
    r"""Marks checkpoint keys that can be directly loaded from/saved to the
    H5 checkpoint. Non-leafs are accessed through StreamingCSWriterView due to
    their iterable nature.
    """

    def __str__(self) -> str:
        return "*"

    def __repr__(self) -> str:
        return "*"


class StreamingCSWriterView:
    r"""StreamingCSWriterView allows for checkpoints with arbitrarily nested
    dictionaries/lists to be written in a streaming (incremental) manner by
    offering a "view" into a StreamingCSWriter. For example, in a checkpoint
    with the structure {"model": {<model state>}}, we can obtain a view into the
    model state via checkpoint["model"]. This view has state <model state> and
    prefix ["model"]. The view acts like a dict (offers `__getitem__`,
    `__setitem__`, etc operations) which incrementally saves/loads from an H5
    checkpoint under the hood.
    
    Args:
        checkpoint_file:    Path to H5 checkpoint
        state:              (Sub)state dictionary corresponding to the current
                            view of the checkpoint.
        prefix:             Chain of keys that were accessed in the checkpoint
                            that yielded the current view

    """

    def __init__(self, checkpoint_file, state, prefix=[]) -> None:
        self.checkpoint_file = checkpoint_file
        self.state = state
        self.prefix = prefix

    def __str__(self):
        return str(self.state)

    def __repr__(self):
        return f"StreamingCSWriterView: {str(self)}"

    def __iter__(self):
        if isinstance(self.state, dict):
            for key in self.keys():
                yield key
        if isinstance(self.state, (list, tuple)):
            for i in range(len(self.state)):
                yield self[i]

    def __len__(self):
        return len(self.state)

    def items(self):
        assert isinstance(self.state, dict)
        for key in self.keys():
            yield key, self[key]

    def keys(self):
        assert isinstance(self.state, dict)
        for key in self.state:
            if key in self:
                yield key

    def values(self):
        assert isinstance(self.state, dict)
        for key in self.keys():
            yield self[key]

    def __contains__(self, item):
        return item in self.state

    def __getitem__(self, key):
        value = self.state[key]

        if isinstance(value, StreamingCSLeaf):
            saver = PyTorchH5Saver()
            name = ".".join(self.prefix + [key])
            return saver.load_tensor(self.checkpoint_file, name)

        if isinstance(value, StreamingCSWriterView):
            return value
        if isinstance(value, (dict, list, tuple)):
            subview = StreamingCSWriterView(
                self.checkpoint_file, value, self.prefix + [key]
            )
            return subview

        return value

    def get(self, key, default=None):
        if key in self:
            return self[key]
        return default

    def __setitem__(self, key, value):
        if key in self.state and not isinstance(
            self.state[key], StreamingCSLeaf
        ):
            raise ValueError(
                "StreamingCSWriter does not support updating an existing \
                     key which had a dict/list/tuple value"
            )

        if isinstance(value, (dict, list, tuple)):
            if key in self.state:
                raise ValueError(
                    "StreamingCSWriter does not support updating a key which \
                    already exists with a dict/list/tuple"
                )

            flattened, spec = torch.utils._pytree.tree_flatten(value)

            for scope, v in zip(recurse_spec(spec), flattened):
                name = ".".join(self.prefix + [key] + scope)
                saver = PyTorchH5Saver()
                saver.save_tensor(self.checkpoint_file, name, v)

            substate = torch.utils._pytree.tree_unflatten(
                [StreamingCSLeaf() for i in range(len(flattened))], spec,
            )
            self.state[key] = substate
        else:
            name = ".".join(self.prefix + [key])
            saver = PyTorchH5Saver()
            saver.save_tensor(self.checkpoint_file, name, value)
            self.state[key] = StreamingCSLeaf()


class StreamingCSWriter(StreamingCSWriterView):
    r"""Writes a Cerebras H5 checkpoint in a streaming (incremental) manner
    rather than accumulating the full checkpoint into memory and then writing
    all weights at the end.
    
    It is essential that `self.save()` is called in order to flush the required
    metadata (state's spec). Without this call, the resulting checkpoint will
    not be able to be loaded with `cstorch.load(...)`.

    The StreamingCSWriter class supports re-accessing and even updating
    keys that have already been written. There are two restrictions:
    1.  An existing key that stores a dict/list/tuple cannot be replaced.
    2.  An existing key storing any type cannot be replaced by a dict/list/tuple

    Args:
        checkpoint_file:    Path to new H5 checkpoint. A file cannot already
                            exist at this location.

    """

    def __init__(self, checkpoint_file) -> None:
        if os.path.exists(checkpoint_file):
            raise FileExistsError(
                f"Checkpoint file \"{checkpoint_file}\" cannot be created because "
                "file already exists"
            )

        super().__init__(checkpoint_file, {})

    def save(self):
        saver = PyTorchH5Saver()
        _, spec = saver.flatten_state_dict(self.state)
        saver.save_spec(self.checkpoint_file, spec)

    def __str__(self):
        return f"{self.checkpoint_file}:\n{self.state}"

    def __repr__(self):
        return f"StreamingCSWriter: {str(self)}"
