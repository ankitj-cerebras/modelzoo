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
from torchvision.transforms import transforms

from modelzoo.vision.pytorch.input.transforms import create_transform


def get_preprocess_transform(params):

    transform_specs = params["transforms"]

    transform_list = []
    for spec in transform_specs:
        transform = create_transform(spec)
        transform_list.append(transform)

    if params["mixed_precision"]:
        mp_type = torch.bfloat16 if params["use_bfloat16"] else torch.float16
    else:
        mp_type = torch.float32

    transform_list.append(
        create_transform({"name": "to_dtype", "mp_type": mp_type})
    )

    transform = transforms.Compose(transform_list)

    logging.info(
        f"The following sequence is used to transform data:\n{transform}"
    )
    return transform
