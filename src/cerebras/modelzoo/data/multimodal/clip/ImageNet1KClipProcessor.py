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

from typing import Union

import torch
import torchvision
from transformers import CLIPTokenizerFast

import cerebras.pytorch as cstorch
from cerebras.modelzoo.data.common.restartable_dataloader import (
    RestartableDataLoader,
)
from cerebras.modelzoo.data.vision.classification.data.imagenet import (
    ImageNet1KProcessor,
)
from cerebras.modelzoo.data.vision.classification.dataset_factory import (
    VisionSubset,
)


def _verify_dataset(dataset):
    """
    Verify the dataset type is compatible with ImageNet.
    """
    assert (
        isinstance(dataset, torchvision.datasets.VisionDataset)
        or isinstance(dataset, VisionSubset)
        or isinstance(dataset, torch.utils.data.Subset)
    ), f"Got {type(dataset)} but dataset must be type VisionDataset, VisionSubset, or torch.utils.data.Subset"


class ImageNet1KClipProcessor(ImageNet1KProcessor):
    def __init__(self, params):
        super().__init__(params)
        self.image_size = params.get("image_size")
        self.patch_size = params.get("patch_size")
        self.image_channels = params.get("image_channels")
        self.template = "this is a photo of <>."
        self.tokenizer = CLIPTokenizerFast.from_pretrained(
            "openai/clip-vit-base-patch16"
        )
        # the maximum length of tokens for label text after tokenization.
        # ref: https://huggingface.co/openai/clip-vit-base-patch16/blob/main/config.json#L45
        self.text_max_length = 77

    def clip_collate_fn(self, data):
        assert self.classes is not None, "Need class names to construct samples"
        input_images = torch.stack([d[0] for d in data])  # [bs, c, h, w]
        # labels = torch.stack([d[1] for d in data])  # [bs, c, h, w]
        labels = []
        for d in data:
            label = d[1]
            class_name = self.classes[label][0]  # always choose the first label
            labels.append(self.template.replace("<>", class_name))

        # tokenize
        labels = self.tokenizer(
            labels,
            max_length=self.text_max_length,
            padding="max_length",
            return_tensors="pt",
        )

        results = {}
        results["input_images"] = input_images
        results["input_ids_text"] = labels["input_ids"]
        results["attention_mask_text"] = labels["attention_mask"]

        return results

    def create_dataloader(
        self,
        dataset: Union[
            torchvision.datasets.VisionDataset,
            VisionSubset,
            torch.utils.data.Subset,
        ],
        is_training=False,
    ):
        _verify_dataset(dataset)

        self.classes = dataset.classes

        shuffle = self.shuffle and is_training

        if self.shuffle_seed is None:
            self.shuffle_seed = 0

        data_sampler = cstorch.utils.data.DistributedSampler(
            data_source=dataset,
            shuffle=shuffle,
            seed=self.shuffle_seed,
            shard=True,
            batch_size=self.global_batch_size,
            drop_last=self.drop_last,
        )

        dataloader = RestartableDataLoader(
            dataset,
            batch_sampler=data_sampler,
            num_workers=self.num_workers,
            pin_memory=self.distributed,
            prefetch_factor=self.prefetch_factor,
            persistent_workers=self.persistent_workers,
            worker_init_fn=self._worker_init_fn,
        )

        dataloader.collate_fn = self.clip_collate_fn
        return dataloader
