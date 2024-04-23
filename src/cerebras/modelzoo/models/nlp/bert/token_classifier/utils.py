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


def set_defaults(params, mode=None):
    for section in ["train_input", "eval_input"]:
        for key in ["vocab_file"]:
            if params.get(section, {}).get(key):
                params[section][key] = os.path.abspath(params[section][key])

    params["model"]["layer_norm_epsilon"] = params["model"].get(
        "layer_norm_epsilon", 1.0e-5
    )
    params["model"]["label_vocab_file"] = params["train_input"].get(
        "label_vocab_file", None
    )
    # If set to `False`, `pad` token loss will not contribute to loss.
    include_padding_in_loss = params["model"].get(
        "include_padding_in_loss", False
    )
    params["model"]["include_padding_in_loss"] = include_padding_in_loss
    loss_weight = params["model"].get("loss_weight", 1.0)
    if include_padding_in_loss:
        max_sequence_length = params["train_input"]["max_sequence_length"]
        loss_weight *= 1.0 / max_sequence_length
    params["model"]["loss_weight"] = loss_weight
    params["optimizer"]["log_summaries"] = params["optimizer"].get(
        "log_summaries", False
    )
