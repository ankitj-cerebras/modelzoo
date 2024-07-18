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
import re
from typing import Tuple

import torch

from cerebras.modelzoo.tools.checkpoint_converters.base_converter import (
    BaseCheckpointConverter_HF_CS,
    BaseConfigConverter,
    BaseConfigConverter_HF_CS,
    ConfigConversionError,
    ConversionRule,
    EquivalentSubkey,
    FormatVersions,
)
from cerebras.modelzoo.tools.checkpoint_converters.gpt_neox_hf_cs import (
    Converter_GPT_Neox_Attention_HF_CS17,
)


class Converter_Falcon_40B_Attention_HF_CS20(
    Converter_GPT_Neox_Attention_HF_CS17
):
    def __init__(self):
        super().__init__()
        self.rules = [
            ConversionRule(
                [
                    EquivalentSubkey("dense", "proj_output_dense_layer"),
                    r"\.(?:weight|bias)",
                ],
                action=self.replaceKey,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("query_key_value", "proj_q_dense_layer"),
                    r"\.(?:weight|bias)",
                ],
                action=self.qkv_converter,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("query_key_value", "proj_k_dense_layer"),
                    r"\.(?:weight|bias)",
                ],
                action=self.assert_already_converted,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("query_key_value", "proj_v_dense_layer"),
                    r"\.(?:weight|bias)",
                ],
                action=self.assert_already_converted,
            ),
        ]

    def interleave_helper(self, rotary_dim, t):
        if len(t.shape) == 4:
            to_rotate = t[:, :, :rotary_dim, :]
            to_pass = t[:, :, rotary_dim:, :]
            to_rotate = (
                to_rotate.reshape(t.shape[0], t.shape[1], 2, -1, t.shape[-1])
                .permute(0, 1, 3, 2, 4)
                .reshape(t.shape[0], t.shape[1], -1, t.shape[-1])
            )
            interleaved = torch.cat((to_rotate, to_pass), dim=2)
        elif len(t.shape) == 3:
            to_rotate = t[:, :, :rotary_dim]
            to_pass = t[:, :, rotary_dim:]
            to_rotate = (
                to_rotate.reshape(t.shape[0], t.shape[1], 2, -1)
                .permute(0, 1, 3, 2)
                .reshape(t.shape[0], t.shape[1], -1)
            )
            interleaved = torch.cat((to_rotate, to_pass), dim=2)
        else:
            assert False, (
                "shape of query, key, value projection tensor has to have shape of length 2 "
                "(biases) or 3 (weights) when converting from HF to CS"
            )
        return interleaved

    def reverse_interleave_helper(
        self, rotary_dim, t, group_size=None, num_groups=None
    ):
        if len(t.shape) == 2:
            t = t.reshape(num_groups, group_size, -1, t.shape[-1])
            to_rotate = t[:, :, :rotary_dim, :]
            to_pass = t[:, :, rotary_dim:, :]
            # pylint: disable=redefined-builtin
            reversed = (
                to_rotate.reshape(num_groups, group_size, -1, 2, t.shape[-1])
                .permute(0, 1, 3, 2, 4)  # 2, 1, 3)
                .reshape(num_groups, group_size, rotary_dim, t.shape[-1])
            )
            reversed = torch.cat((reversed, to_pass), dim=2)
        elif len(t.shape) == 1:
            t = t.reshape(num_groups, group_size, -1)
            to_rotate = t[:, :, :rotary_dim]
            to_pass = t[:, :, rotary_dim:]
            reversed = (
                to_rotate.reshape(num_groups, group_size, -1, 2)
                .permute(0, 1, 3, 2)
                .reshape(num_groups, group_size, -1)
            )
            reversed = torch.cat((reversed, to_pass), dim=2)
        else:
            assert False, (
                "shape of query, key, value projection tensor has to have shape of length 1 "
                "(biases) or 2 (weights) when converting from CS to HF"
            )
        return reversed

    def qkv_converter_hf_to_cs(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        action_fn_args,
    ):
        # HF represents Q, K, and V in a packed format (torch.Size(3*hidden, hidden)). We need to
        # unpack the weight and bias tensor for CS 1.7 format.
        q_key = new_key
        k_key = re.sub(r"\.proj_q_dense_layer\.", ".proj_k_dense_layer.", q_key)
        v_key = re.sub(r"\.proj_q_dense_layer\.", ".proj_v_dense_layer.", q_key)

        cs_config = action_fn_args["configs"][1]
        hidden_size = cs_config["model"]["hidden_size"]
        num_heads = cs_config["model"]["num_heads"]
        head_size = hidden_size // num_heads
        num_kv_groups = cs_config["model"]["extra_attention_params"][
            "num_kv_groups"
        ]
        kv_group_size = num_heads // num_kv_groups

        if new_key.endswith(".bias"):
            assert len(old_state_dict[old_key].shape) == 1
            packed_dim = old_state_dict[old_key].shape[0]
            assert (
                head_size * (num_kv_groups * 2 + num_heads) == packed_dim
            ), "Invalid tensor shape {} at {}.".format(
                old_state_dict[old_key].shape, old_key
            )
            split_by_num_heads = old_state_dict[old_key].reshape(
                num_kv_groups, (kv_group_size + 2), -1
            )

            query = split_by_num_heads[:, :kv_group_size]
            key = split_by_num_heads[:, kv_group_size : kv_group_size + 1]
            value = split_by_num_heads[:, kv_group_size + 1 : kv_group_size + 2]

            query = self.interleave_helper(head_size, query)
            key = self.interleave_helper(head_size, key)

            query = query.reshape(-1)
            value = value.reshape(-1)
            key = key.reshape(-1)
            new_state_dict[q_key] = query
            new_state_dict[k_key] = key
            new_state_dict[v_key] = value
        elif new_key.endswith(".weight"):
            packed_dim, dim = old_state_dict[old_key].shape
            assert (
                head_size * (num_kv_groups * 2 + num_heads)
            ) == packed_dim, "Invalid tensor shape {} at {}.".format(
                old_state_dict[old_key].shape, old_key
            )
            split_by_num_heads = old_state_dict[old_key].reshape(
                num_kv_groups, (kv_group_size + 2), -1, dim
            )

            query = split_by_num_heads[:, :kv_group_size]
            key = split_by_num_heads[:, kv_group_size : kv_group_size + 1]
            value = split_by_num_heads[:, kv_group_size + 1 : kv_group_size + 2]

            query = self.interleave_helper(head_size, query)
            key = self.interleave_helper(head_size, key)

            query = query.reshape(-1, dim)
            value = value.reshape(-1, dim)
            key = key.reshape(-1, dim)
            new_state_dict[q_key] = query
            new_state_dict[k_key] = key
            new_state_dict[v_key] = value
        else:
            raise ValueError("Invalid key after conversion: {}".format(new_key))

    def qkv_converter_cs_to_hf(
        self, old_key, new_key, old_state_dict, new_state_dict, action_fn_args
    ):
        # HF represents Q, K, and V in a packed format. It also contains
        # special ".bias" and ".masked_bias" register buffers that need to be
        # initialized
        q_key = old_key
        k_key = re.sub(r"\.proj_q_dense_layer\.", ".proj_k_dense_layer.", q_key)
        v_key = re.sub(r"\.proj_q_dense_layer\.", ".proj_v_dense_layer.", q_key)

        cs_config = action_fn_args["configs"][1]
        hidden_size = cs_config["model"]["hidden_size"]
        num_heads = cs_config["model"]["num_heads"]
        head_size = hidden_size // num_heads
        num_kv_groups = cs_config["model"]["extra_attention_params"][
            "num_kv_groups"
        ]
        kv_group_size = num_heads // num_kv_groups

        assert (
            k_key in old_state_dict
        ), "Expected the following key to exist! {}".format(k_key)
        assert (
            v_key in old_state_dict
        ), "Expected the following key to exist! {}".format(v_key)

        query = old_state_dict[q_key]
        value = old_state_dict[v_key]
        key = old_state_dict[k_key]

        if new_key.endswith(".bias"):
            cs_config = action_fn_args["configs"][1]

            # map qkv
            query = self.reverse_interleave_helper(
                head_size,
                query,
                group_size=kv_group_size,
                num_groups=num_kv_groups,
            )
            key = self.reverse_interleave_helper(
                head_size, key, group_size=1, num_groups=num_kv_groups
            )
            value = value.reshape(num_kv_groups, 1, -1)

            packed_qkv = torch.cat(
                (
                    query,
                    key,
                    value,
                ),
                dim=1,
            )
            packed_qkv = packed_qkv.reshape(-1)
            new_state_dict[new_key] = packed_qkv
        elif new_key.endswith(".weight"):
            hidden_size = query.shape[-1]

            query = self.reverse_interleave_helper(
                head_size,
                query,
                group_size=kv_group_size,
                num_groups=num_kv_groups,
            )
            key = self.reverse_interleave_helper(
                head_size, key, group_size=1, num_groups=num_kv_groups
            )
            value = value.reshape(num_kv_groups, 1, -1, value.shape[-1])

            packed_qkv = torch.cat(
                (
                    query,
                    key,
                    value,
                ),
                dim=1,
            )
            packed_qkv = packed_qkv.reshape(-1, hidden_size)
            new_state_dict[new_key] = packed_qkv
        else:
            raise ValueError("Invalid key after conversion: {}".format(new_key))

    def qkv_converter(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        from_index,
        action_fn_args,
    ):
        if from_index == 0:
            self.qkv_converter_hf_to_cs(
                old_key, new_key, old_state_dict, new_state_dict, action_fn_args
            )
        else:
            self.qkv_converter_cs_to_hf(
                old_key, new_key, old_state_dict, new_state_dict, action_fn_args
            )

    @staticmethod
    def formats() -> Tuple[FormatVersions, FormatVersions]:
        return (
            FormatVersions("hf"),
            FormatVersions("cs-1.9", "cs-2.0", "cs-2.1", "cs-2.2", "cs-2.3"),
        )

    @staticmethod
    def get_config_converter_class() -> BaseConfigConverter:
        return ConfigConverter_Falcon_40B_HF_CS20


class Converter_Falcon_40B_Headless_WithoutModelPrefix_HF_CS20(
    BaseCheckpointConverter_HF_CS
):
    def __init__(self):
        super().__init__()
        self.rules = [
            # Embedding:
            ConversionRule(
                [
                    EquivalentSubkey(
                        "word_embeddings", "embedding_layer.word_embeddings"
                    ),
                    r"\.weight",
                ],
                action=self.replaceKey,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("h", "transformer_decoder.layers"),
                    r"\.\d+\.",
                    EquivalentSubkey("ln_attn.", "norm1."),
                    r"(?:weight|bias)",
                ],
                action=self.replaceKey,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("h", "transformer_decoder.layers"),
                    r"\.\d+\.",
                    EquivalentSubkey("ln_mlp.", "norm3."),
                    r"(?:weight|bias)",
                ],
                action=self.replaceKey,
            ),
            # Attention:
            ConversionRule(
                [
                    EquivalentSubkey("h", "transformer_decoder.layers"),
                    r"\.\d+\.",
                    EquivalentSubkey("self_attention.", "self_attn."),
                    Converter_Falcon_40B_Attention_HF_CS20(),
                ],
                action=None,
            ),
            # mlp
            ConversionRule(
                [
                    EquivalentSubkey("h", "transformer_decoder.layers"),
                    r"\.\d+\.",
                    EquivalentSubkey(
                        "mlp.dense_h_to_4h", "ffn.ffn.0.linear_layer"
                    ),
                    r"\.(?:weight|bias)",
                ],
                action=self.replaceKey,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("h", "transformer_decoder.layers"),
                    r"\.\d+\.",
                    EquivalentSubkey(
                        "mlp.dense_4h_to_h", "ffn.ffn.1.linear_layer"
                    ),
                    r"\.(?:weight|bias)",
                ],
                action=self.replaceKey,
            ),
            # final norm
            ConversionRule(
                [
                    EquivalentSubkey("ln_f", "transformer_decoder.norm"),
                    r"\.(?:weight|bias)",
                ],
                action=self.replace_final_norm,
            ),
            # other
            ConversionRule([r"lm_head\.(?:weight|bias)"], exists="right"),
            ConversionRule([r"ln_f\.(?:weight|bias)"], exists="right"),
        ]

    def replace_final_norm(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        from_index,
        action_fn_args,
    ):
        new_state_dict[new_key] = old_state_dict[old_key]
        # CS model has both "ln_f" and "transformer_decoder.norm"
        # we need to copy the original ("ln_f") too:
        if from_index == 0:
            ln_f_key = re.sub(r"transformer_decoder\.norm\.", "ln_f.", new_key)
            new_state_dict[ln_f_key] = old_state_dict[old_key]

    def pre_model_convert(
        self,
        old_state_dict,
        new_state_dict,
        configs,
        converter_indices,
        drop_unmatched_keys,
    ):
        if converter_indices.direction == 0:
            logging.warning(
                "{} Falcon has a language model head (lm_head) "
                "while {} GPTNeoxModel does not. Initializing lm_head to default.".format(
                    self.formats()[1], self.formats()[0]
                )
            )

        # Manually tie weights
        if (
            converter_indices.direction == 1
            and configs[1]["model"]["share_embedding_weights"]
        ):
            if (
                old_state_dict.get("embedding_layer.word_embeddings.weight", 0)
                is None
            ):
                old_state_dict["embedding_layer.word_embeddings.weight"] = (
                    old_state_dict["lm_head.weight"]
                )

    def post_model_convert(
        self,
        old_state_dict,
        new_state_dict,
        configs,
        converter_indices,
        drop_unmatched_keys,
        key_prefix="",
    ):
        if converter_indices.direction == 0:
            # We are converting from HF Falcon (which is headless) -> CS GPTJModel (which has a
            # head). We need to create 'lm_head' and init to default values
            hf_config = configs[0]
            cs_config = configs[1]
            use_bias_in_output = cs_config["model"].get(
                "use_bias_in_output", False
            )
            vocab_size = cs_config["model"]["vocab_size"]
            embed_dim = cs_config["model"]["hidden_size"]
            if hf_config["tie_word_embeddings"]:
                lm_head_weight = old_state_dict[
                    'transformer.word_embeddings.weight'
                ]
            else:
                lm_head_weight = torch.zeros((vocab_size, embed_dim))
                lm_head_weight.normal_(mean=0.0, std=0.02)
            new_state_dict[key_prefix + "lm_head.weight"] = lm_head_weight
            if use_bias_in_output:
                lm_head_bias = torch.zeros(vocab_size)
                new_state_dict[key_prefix + "lm_head.bias"] = lm_head_bias
        super().post_model_convert(
            old_state_dict,
            new_state_dict,
            configs,
            converter_indices,
            drop_unmatched_keys,
            key_prefix=key_prefix,
        )

    @staticmethod
    def formats() -> Tuple[FormatVersions, FormatVersions]:
        return (
            FormatVersions("hf"),
            FormatVersions("cs-1.9", "cs-2.0", "cs-2.1", "cs-2.2", "cs-2.3"),
        )

    @staticmethod
    def get_config_converter_class() -> BaseConfigConverter:
        return ConfigConverter_Falcon_40B_HF_CS20


class Converter_Falcon_40B_Headless_HF_CS20(
    Converter_Falcon_40B_Headless_WithoutModelPrefix_HF_CS20
):
    def __init__(self):
        super().__init__()
        self.rules = [
            # Catch checkpoints from Pytorch 2.0 API
            ConversionRule(
                [
                    Converter_Falcon_40B_Headless_WithoutModelPrefix_HF_CS20(),
                ],
                action=None,
            ),
            # Catch checkpoints from 1.7/1.8
            ConversionRule(
                [
                    EquivalentSubkey("", "model."),
                    Converter_Falcon_40B_Headless_WithoutModelPrefix_HF_CS20(),
                ],
                action=None,
            ),
        ]


class Converter_Falcon_40B_WithoutModelPrefix_HF_CS20(
    BaseCheckpointConverter_HF_CS
):
    def __init__(self):
        super().__init__()
        self.rules = [
            ConversionRule(
                [
                    "lm_head",
                    r"\.(?:weight|bias)",
                ],
                action=self.replaceKey,
            ),
            ConversionRule(
                [
                    EquivalentSubkey("transformer.", ""),
                    Converter_Falcon_40B_Headless_WithoutModelPrefix_HF_CS20(),
                ],
                action=None,
            ),
        ]

    def pre_model_convert(
        self,
        old_state_dict,
        new_state_dict,
        configs,
        converter_indices,
        drop_unmatched_keys,
    ):
        # Manually tie weights
        if (
            converter_indices.direction == 1
            and configs[1]["model"]["share_embedding_weights"]
        ):
            if (
                old_state_dict.get("embedding_layer.word_embeddings.weight", 0)
                is None
            ):
                old_state_dict["embedding_layer.word_embeddings.weight"] = (
                    old_state_dict["lm_head.weight"]
                )

    @staticmethod
    def formats() -> Tuple[FormatVersions, FormatVersions]:
        return (
            FormatVersions("hf"),
            FormatVersions("cs-1.9", "cs-2.0", "cs-2.1", "cs-2.2", "cs-2.3"),
        )

    @staticmethod
    def get_config_converter_class() -> BaseConfigConverter:
        return ConfigConverter_Falcon_40B_HF_CS20


class Converter_Falcon_40B_HF_CS20(
    Converter_Falcon_40B_WithoutModelPrefix_HF_CS20
):
    def __init__(self):
        super().__init__()
        self.rules = [
            # Catch checkpoints from Pytorch 2.0 API
            ConversionRule(
                [
                    Converter_Falcon_40B_WithoutModelPrefix_HF_CS20(),
                ],
                action=None,
            ),
            # Catch checkpoints from 1.7/1.8
            ConversionRule(
                [
                    EquivalentSubkey("", "model."),
                    Converter_Falcon_40B_WithoutModelPrefix_HF_CS20(),
                ],
                action=None,
            ),
        ]


class ConfigConverter_Falcon_40B_HF_CS20(BaseConfigConverter_HF_CS):
    def __init__(self):
        super().__init__()
        self.rules = [
            ConversionRule(
                ["model_type"],
                action=BaseConfigConverter.assert_factory_fn(0, "RefinedWeb"),
            ),
            # Embedding
            ConversionRule(["vocab_size"], action=self.replaceKey),
            ConversionRule(
                [EquivalentSubkey("alibi", "position_embedding_type")],
                action=self.convert_position_embedding_type,
            ),
            ConversionRule(
                [
                    EquivalentSubkey(
                        "tie_word_embeddings", "share_embedding_weights"
                    )
                ],
                action=self.replaceKey,
            ),
            # Decoder Block
            ConversionRule(
                ["hidden_size"],
                action=self.convert_hidden_size,
            ),
            ConversionRule(
                [EquivalentSubkey("n_head", "num_heads")],
                action=self.replaceKey,
            ),
            ConversionRule(
                [EquivalentSubkey("n_head_kv", "extra_attention_params")],
                action=self.convert_num_head_groups,
            ),
            ConversionRule(
                [EquivalentSubkey("n_layer", "num_hidden_layers")],
                action=self.replaceKey,
            ),
            ConversionRule(
                ["max_position_embeddings"],
                action=self.replaceKey,
            ),
            ConversionRule(
                [EquivalentSubkey("parallel_attn", "use_untied_layer_norm")],
                action=self.parallel_attn_convert,
            ),
            ConversionRule(
                ["use_projection_bias_in_attention"],
                exists="right",
                action=BaseConfigConverter.assert_factory_fn(1, False),
            ),
            ConversionRule(
                ["use_ffn_bias_in_attention"],
                exists="right",
                action=BaseConfigConverter.assert_factory_fn(1, False),
            ),
            ConversionRule(
                ["use_ffn_bias"],
                exists="right",
                action=BaseConfigConverter.assert_factory_fn(1, False),
            ),
            ConversionRule(
                ["nonlinearity"],
                exists="right",
                action=BaseConfigConverter.assert_factory_fn(1, "gelu"),
            ),
            ConversionRule(
                [
                    EquivalentSubkey(
                        "attention_dropout", "attention_dropout_rate"
                    )
                ],
                action=self.replaceKey,
            ),
            ConversionRule(
                [EquivalentSubkey("hidden_dropout", "residual_dropout_rate")],
                action=self.replaceKey,
            ),
            ConversionRule(
                ["layer_norm_epsilon"],
                action=self.replaceKey,
            ),
            ConversionRule(
                ["use_bias_in_output"],
                exists="right",
                action=BaseConfigConverter.assert_factory_fn(1, False),
            ),
            ConversionRule(
                ["initializer_range"],
                action=self.replaceKey,
            ),
            ConversionRule(
                ["bias"],
                exists="left",
                action=BaseConfigConverter.assert_factory_fn(0, False),
            ),
            ConversionRule(
                ["alibi"],
                exists="left",
                action=BaseConfigConverter.assert_factory_fn(0, False),
            ),
        ]

        self.defaults = [
            {
                "alibi": False,
                "architectures": ["RWForCausalLM"],
                "auto_map": {
                    "AutoConfig": "configuration_RW.RWConfig",
                    "AutoModel": "modelling_RW.RWModel",
                    "AutoModelForSequenceClassification": (
                        "modelling_RW.RWForSequenceClassification"
                    ),
                    "AutoModelForTokenClassification": "modelling_RW.RWForTokenClassification",
                    "AutoModelForQuestionAnswering": "modelling_RW.RWForQuestionAnswering",
                    "AutoModelForCausalLM": "modelling_RW.RWForCausalLM",
                },
                "parallel_attn": True,
                "bias": False,
                "bos_token_id": 11,
                "eos_token_id": 11,
                "model_type": "RefinedWeb",
                "torch_dtype": "bfloat16",
                "use_cache": True,
                "tie_word_embeddings": True,
            },
            {
                "position_embedding_type": "rotary",
                "embedding_dropout_rate": 0.0,
                "share_embedding_weights": True,
                "nonlinearity": "gelu",
                "max_position_embeddings": 2048,
                "attention_module": "multiquery_attention",
                "attention_type": "scaled_dot_product",
                "use_untied_layer_norm": True,
                "extra_attention_params": {"num_kv_groups": 1},
                "loss_scaling": "num_tokens",
            },
        ]

    def convert_num_head_groups(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        from_index,
        action_fn_args,
    ):
        if from_index == 0:
            extra = {"num_kv_groups": old_state_dict[old_key]}
            new_state_dict[new_key] = extra
        elif from_index == 1:
            new_state_dict[new_key] = old_state_dict[old_key]["num_kv_groups"]

    def convert_position_embedding_type(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        from_index,
        action_fn_args,
    ):
        # HF supports absolute, or sinusoidal (fixed)
        # CS supports learned, fixed

        if from_index == 0:
            if old_state_dict[old_key] == True:
                raise ConfigConversionError(
                    "CS model doesn't support falcon with position_embedding_type = alibi"
                )
            new_state_dict[new_key] = "rotary"
        else:
            new_state_dict[new_key] = False

    def convert_hidden_size(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        from_index,
        action_fn_args,
    ):
        new_state_dict[new_key] = old_state_dict[old_key]
        if from_index == 0:
            # Falcon uses 4 * hidden as intermediate size
            new_state_dict["filter_size"] = old_state_dict[old_key] * 4
        else:
            assert (
                old_state_dict[old_key] * 4 == old_state_dict["filter_size"]
            ), "HF model only supports filter_size = 4 * hidden_size"

    def parallel_attn_convert(
        self,
        old_key,
        new_key,
        old_state_dict,
        new_state_dict,
        from_index,
        action_fn_args,
    ):
        assert (
            old_state_dict[old_key] == True
        ), "parallel attention has to be enabled for falcon-40B"
        new_state_dict[new_key] = True

    def pre_config_convert(
        self,
        config,
        converter_indices,
    ):
        config = super().pre_config_convert(config, converter_indices)

        # Apply defaults
        for key in self.defaults[converter_indices.direction]:
            if key not in config:
                config[key] = self.defaults[converter_indices.direction][key]

        return config

    def post_config_convert(
        self,
        original_config,
        old_config,
        new_config,
        converter_indices,
        drop_unmatched_keys,
    ):
        # Apply defaults
        for key in self.defaults[1 - converter_indices.direction]:
            if key not in new_config:
                new_config[key] = self.defaults[
                    1 - converter_indices.direction
                ][key]

        if converter_indices.direction == 0:
            # falcon uses rotary_dim == head_dim
            new_config["rotary_dim"] = (
                old_config["hidden_size"] // old_config["n_head"]
            )
        else:
            # embedding dropout check
            assert (
                old_config["embedding_dropout_rate"] == 0.0
            ), "Falcon has no embedding dropout"

            # rotary check
            assert (
                old_config["rotary_dim"]
                == old_config["hidden_size"] // old_config["num_heads"]
            ), "rotary dimension of falcon is equal to head_dim"

        return super().post_config_convert(
            original_config,
            old_config,
            new_config,
            converter_indices,
            drop_unmatched_keys,
        )

    @staticmethod
    def formats() -> Tuple[FormatVersions, FormatVersions]:
        return (
            FormatVersions("hf"),
            FormatVersions("cs-1.9", "cs-2.0", "cs-2.1", "cs-2.2", "cs-2.3"),
        )
