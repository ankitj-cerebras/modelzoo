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

import os

import h5py
import numpy as np
from PIL import Image
from torchvision.datasets.utils import verify_str_arg
from torchvision.datasets.vision import VisionDataset

from cerebras.modelzoo.data.vision.classification.dataset_factory import (
    Processor,
    VisionSubset,
)


class PatchCamelyon(VisionDataset):
    """
    The PatchCamelyon benchmark is a new and challenging image classification
    dataset. It consists of 327.680 color images (96 x 96px) extracted from
    histopathologic scans of lymph node sections. Each image is annoted with
    a binary label indicating presence of metastatic tissue. PCam provides a
    new benchmark for machine learning models: bigger than CIFAR10, smaller
    than Imagenet, trainable on a single GPU.
    """

    _file_dict = {
        'test_x': 'camelyonpatch_level_2_split_test_x.h5',
        'test_y': 'camelyonpatch_level_2_split_test_y.h5',
        'train_x': 'camelyonpatch_level_2_split_train_x.h5',
        'train_y': 'camelyonpatch_level_2_split_train_y.h5',
        'val_x': 'camelyonpatch_level_2_split_valid_x.h5',
        'val_y': 'camelyonpatch_level_2_split_valid_y.h5',
    }

    def __init__(
        self, root, split="train", transform=None, target_transform=None
    ):
        super().__init__(
            os.path.join(root, "patch_camelyon"),
            transform=transform,
            target_transform=target_transform,
        )
        self.split = verify_str_arg(split, "split", ("train", "val", "test"))
        if not os.path.exists(self.root):
            raise RuntimeError(
                "Dataset not found. Download and extract from "
                "https://patchcamelyon.grand-challenge.org/"
            )

        self.path_x = os.path.join(self.root, self._file_dict[f"{split}_x"])
        self.path_y = os.path.join(self.root, self._file_dict[f"{split}_y"])
        with h5py.File(self.path_x, "r") as fx:
            self.length = len(fx["x"])
        self.images = None
        self.labels = None

    def __getitem__(self, index):
        # Workaround so that dataset is pickleable and allow for multiprocessing
        # See discussion:
        # https://discuss.pytorch.org/t/dataloader-when-num-worker-0-there-is-bug/25643/16
        if self.images is None:
            self.images = h5py.File(self.path_x, "r")["x"]
        if self.labels is None:
            self.labels = h5py.File(self.path_y, "r")["y"]

        img = Image.fromarray(self.images[index].astype('uint8'), 'RGB')
        target = self.labels[index].flatten()[0]

        if self.transform is not None:
            img = self.transform(img)

        if self.target_transform is not None:
            target = self.target_transform(target)

        return img, target

    def __len__(self):
        return self.length


class PatchCamelyonProcessor(Processor):
    def __init__(self, params):
        super().__init__(params)
        self.allowable_split = ["train", "val", "test"]
        self.num_classes = 2

    def create_dataset(self, use_training_transforms=True, split="train"):
        self.check_split_valid(split)
        transform, target_transform = self.process_transform(
            use_training_transforms
        )
        dataset = PatchCamelyon(
            root=self.data_dir,
            split=split,
            transform=transform,
            target_transform=target_transform,
        )
        return dataset

    def create_vtab_dataset(self, use_1k_sample=True, seed=42):
        train_transform, train_target_transform = self.process_transform(
            use_training_transforms=True
        )
        eval_transform, eval_target_transform = self.process_transform(
            use_training_transforms=False
        )

        train_set = PatchCamelyon(
            root=self.data_dir,
            split="train",
            transform=train_transform,
            target_transform=train_target_transform,
        )
        val_set = PatchCamelyon(
            root=self.data_dir,
            split="val",
            transform=eval_transform,
            target_transform=eval_target_transform,
        )
        test_set = PatchCamelyon(
            root=self.data_dir,
            split="test",
            transform=eval_transform,
            target_transform=eval_target_transform,
        )

        if use_1k_sample:
            rng = np.random.default_rng(seed)
            sample_idx = self.create_shuffled_idx(len(train_set), rng)
            train_set = VisionSubset(train_set, sample_idx[:800])

            sample_idx = self.create_shuffled_idx(len(val_set), rng)
            val_set = VisionSubset(val_set, sample_idx[:200])

        return train_set, val_set, test_set
