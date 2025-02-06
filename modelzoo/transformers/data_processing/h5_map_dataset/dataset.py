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

import logging

import torch

import cerebras_pytorch as cstorch
import cerebras_pytorch.distributed as dist
from cerebras_pytorch.distributed import get_worker_state
from modelzoo.common.pytorch.input_utils import get_streaming_batch_size
from modelzoo.vision.pytorch.input.utils import create_worker_cache

from .readers import H5Reader, Mixture
from .samplers import CBSampler


class RestartableDataLoader(torch.utils.data.DataLoader):
    """
    The state we care about for allowing deterministic restart of instances
    of `HDF5Dataset` is the total number of samples streamed globally,
    which gets consumed by the sampler. Accordingly each worker saves the number
    of samples that it has streamed in `state_dict()`. We aggregate these
    together via summation to save the global number of samples streamed across
    all workers, which is the same thing that is used to set the state of the
    sampler on state dict load.
    """

    def __init__(self, *args, **kwargs):
        # keep track of how many samples were streamed in the previous portion
        # of the run so that we can track cumulative samples streamed in the
        # state_dict
        self.previous_samples_streamed = 0
        super().__init__(*args, **kwargs)

    def state_dict(self):
        """
        Save number of samples streamed for current worker
        """
        worker_state = get_worker_state()
        return {
            "samples_streamed": worker_state.samples_streamed,
            "previous_samples_streamed": self.previous_samples_streamed,
        }

    def load_state_dict(self, state_dict):
        """
        Set sampler state with the total number of samples streamed globally
        """
        self.validate_state_dict(state_dict)
        self.previous_samples_streamed = state_dict["samples_streamed"]
        if (
            self.dataset.shuffle or isinstance(self.dataset.reader, Mixture)
        ) and state_dict["seed"] != self.dataset.seed:
            raise ValueError(
                f"shuffle seed {self.dataset.seed} doesn't match the seed used "
                f"for the previous portion of the run {state_dict['seed']}"
            )
        self.dataset.sampler.set_state(state_dict["samples_streamed"])

    def aggregate_state_dict(self, worker_states):
        """
        Sum samples streamed across all workers to get the number of samples
        streamed globally
        """
        return {
            "samples_streamed": sum(
                sd["samples_streamed"] for sd in worker_states
            )
            + worker_states[0]["previous_samples_streamed"],
            "seed": self.dataset.seed,
        }

    def deaggregate_state_dict(self, aggregated_state_dict):
        """
        No deaggregation needed since the sampler needs the global number of
        samples streamed
        """
        return aggregated_state_dict

    @staticmethod
    def validate_state_dict(sd):
        if len(sd) != 2 or "samples_streamed" not in sd or "seed" not in sd:
            raise RuntimeError(
                "The keys in state_dict must be 'samples_streamed' and 'seed', "
                f"found {sd.keys()}. This means that the dataloader state in "
                "the checkpoint you are loading from is not compatible with "
                "the dataloader currently in use. Consider re-running without "
                "loading the dataloader state."
            )


class HDF5Dataset(torch.utils.data.Dataset):
    """
    Dynamically read samples from disk for using mapping paradigms.

    It supports two different data formats on disk. The first is data stored
    in an H5 file in the shape `(num_tokens,)`, i.e. a series of documents
    tokenized and concatenated together. We call this format the 'corpus' format
    The second format is H5 data of shape `(num_sequences, ...)`, i.e. data has
    already been tokenized and split into sequences. We call this format the
    'sample' format.

    The corpus format supports flexible choice of MSL backed by a single copy of
    the data on disk. Both formats support deterministic restart, and a data
    order that is independent of the configuration of the cluster you are
    running on. I.e. you can pause a run, increase or decrease the number of
    systems you are running on, and restart the run with no change in data
    order.

    When used in combination with shuffling, this implementation relies on
    random access reads to disk to dynamically split samples into sequences
    and shuffle. Users with unusually slow storage should look out for data
    loading bottlenecks and might consider using `use_worker_cache=True` if
    disk access is indeed a bottleneck.

    Args:
        params (dict): a dictionary containing the following fields:
            - "data_dir" (str or list[str]): the path to the HDF5 files.
                Exactly one of "data_dir" or "mixture" must be specified.
            - "batch_size" (int): batch size
            - "shuffle" (bool): whether or not to shuffle the dataset. Defaults
                to `False`
            - "shuffle_seed" (int): seed used for deterministic shuffling.
                Defaults to 0.
            - "use_worker_cache" (bool): whether or not to copy data to storage
                that is directly attached to each individual worker node.
                Useful when your network storage is unusually slow, but
                otherwise discouraged.
            - "max_sequence_length" (int): the sequence length of samples
                produced by the dataloader. When using the 'corpus' data format,
                the same preprocessed data will work with any max sequence
                length, so this may be set at runtime. When using the 'sample'
                format this must be set to `None`.
            - "data_subset" (str): an optional specification to only consider a
                subset of the full dataset, useful for sequence length
                scheduling and multi-epoch testing. Expected to be a comma
                separated list of ranges, e.g. '0.0-0.5' or '0.1-0.3,0.7-1.0'.
                Specifying '0.0-0.5' creates a dataset from the first half of
                the data on disk and disregards the second half.
            - "mixture" list[dict]: an optional specification of multiple
                datasets to mix over to create one single weighted combination.
                Each element must be a dictionary containing keys `data_dir`
                and `weight`. `data_dir` serves the same purpose as mentioned
                above. `weight` defines the probability with which this dataset
                should be sampled from. Weights are normalized to sum to 1.
                Optionally, the dictionary may also contain a `data_subset`
                field which functions the same as the `data_subset` argument
                above.
            - "drop_last" (bool): similar to the PyTorch drop_last setting
                except that samples that when set to `True`, samples that would
                have been dropped at the end of one epoch are yielded at the
                start of the next epoch so that there is no data loss. This is
                necessary for a data ordering that is independent of the
                distributed setup being used.
            - "num_samples" (int): the number of samples to shuffle over (if
                shuffling is enabled). In multi-epoch training, it is common to
                set this to the total number of samples that you plan to train
                on so that epochs are not sequential but instead shuffled
                together for potentially improved convergence.
            - "sort_files" (bool): whether or not the reader should sort the input
                files. This is included for backwards compatibility and should
                almost always be set to `True`.
    """

    def __init__(self, params):
        self.use_worker_cache = params.get("use_worker_cache", False)
        self.msl = params.get("max_sequence_length", None)
        self.shuffle = params.get("shuffle", False)
        self._seed = params.get("shuffle_seed", 0)
        data_dir = params.get("data_dir", None)
        mixture_params = params.get("mixture", None)
        batch_size = get_streaming_batch_size(params["batch_size"])
        micro_batch_size = params.get("micro_batch_size")
        drop_last = params.get("drop_last", True)
        num_samples = params.get("num_samples", None)
        self.sort_files = params.get("sort_files", True)

        if data_dir and mixture_params:
            raise ValueError(
                "you can't specify `data_dir` and `mixture` at the same time"
            )
        if data_dir is not None:
            self.reader = self._set_up_reader(
                data_dir, params.get("data_subset", None)
            )
        else:
            self.reader = Mixture(
                [
                    self._set_up_reader(
                        x["data_dir"], x.get("data_subset", None)
                    )
                    for x in mixture_params
                ],
                [x["weight"] for x in mixture_params],
                interleave=not self.shuffle,
                seed=self._seed,
            )

        self.sampler = CBSampler(
            self,
            shuffle=self.shuffle,
            seed=self._seed,
            shard=True,
            batch_size=batch_size,
            drop_last=drop_last,
            num_samples=num_samples,
        )

        self.map_fn = None

        if self.by_sample and self.shuffle:
            logging.warning(
                "You have chosen to use the sample data format with shuffling. "
                "If you are doing a single-epoch run, it is usually beneficial "
                "to shuffle at preprocessing time instead of runtime. On some "
                "storage setups, shuffling at runtime can cause performance "
                "degredation."
            )

    @property
    def by_sample(self):
        return self.reader.by_sample

    @property
    def seed(self):
        return self._seed

    def map(self, fn):
        if self.map_fn is not None:
            raise ValueError(
                f"You may only apply one map function to a H5MapDataset"
            )
        self.map_fn = fn

    def _set_up_reader(self, data_dir, subset):
        if not isinstance(data_dir, list):
            data_dir = [data_dir]
        if self.use_worker_cache and cstorch.use_cs() and dist.is_streamer():
            data_dir = [create_worker_cache(d) for d in data_dir]

        reader = H5Reader(data_dir, self.msl, True, subset, self.sort_files)
        return reader

    def __getitem__(self, i):
        x = self.reader[i]
        if self.map_fn is not None:
            return self.map_fn(x)
        return x

    def __len__(self):
        return len(self.reader)
