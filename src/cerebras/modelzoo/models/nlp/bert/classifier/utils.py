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
        params_section = params.get(section, {})
        if params_section is not None:
            for key in ["vocab_file"]:
                if key in params_section and params_section[key] is not None:
                    params[section][key] = os.path.abspath(params_section[key])

    params["model"]["layer_norm_epsilon"] = params["model"].get(
        "layer_norm_epsilon", 1.0e-5
    )
    data_processor = params["train_input"]["data_processor"]
    params["model"]["is_mnli_dataset"] = "MNLI" in data_processor
    params["model"]["fp16_type"] = params["model"].get("fp16_type", "float16")
    params["optimizer"]["log_summaries"] = params["optimizer"].get(
        "log_summaries", False
    )
