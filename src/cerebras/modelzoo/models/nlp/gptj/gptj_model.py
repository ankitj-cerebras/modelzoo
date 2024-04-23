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

import torch.nn as nn

import cerebras.pytorch as cstorch
from cerebras.modelzoo.common.utils.model.transformer_utils import (
    create_broadcasted_autoregressive_mask,
)
from cerebras.modelzoo.layers import (
    EmbeddingLayer,
    GPTJDecoderLayer,
    TransformerDecoder,
)
from cerebras.modelzoo.layers.norms import get_norm


class GPTJModel(nn.Module):
    def __init__(
        self,
        hidden_size=768,
        # Embedding params
        vocab_size=50257,
        max_position_embeddings=1024,
        embd_pdrop=0.1,
        share_embedding_weights=True,
        position_embedding_type="rotary",
        rotary_dim=None,
        rope_theta=10000,
        pad_rope=False,
        num_relative_attention_buckets=32,
        # Decoder params
        num_hidden_layers=12,
        filter_size=3072,
        dropout_rate=0.1,
        nonlinearity="gelu",
        norm_type="layernorm",
        layer_norm_epsilon=1.0e-5,
        use_ffn_bias=True,
        use_untied_layer_norm=False,
        # Attention params
        num_heads=12,
        attention_module="aiayn_attention",
        attention_sliding_window_length=None,
        extra_attention_params={},
        attention_type="scaled_dot_product",
        attention_dropout_rate=0.1,
        attention_softmax_fp32=True,
        attention_kernel=None,
        use_projection_bias_in_attention=False,
        use_ffn_bias_in_attention=False,
        # Task-specific
        initializer_range=0.02,
        use_bias_in_output=False,
        norm_first=True,
        embedding_initializer=None,
        attention_initializer=None,
        output_layer_initializer=None,
        alibi_trainable_slopes=False,
        pos_scaling_factor=1.0,
    ):
        super(GPTJModel, self).__init__()
        self.hidden_size = hidden_size
        self.vocab_size = vocab_size
        self.max_position_embeddings = max_position_embeddings
        self.share_embedding_weights = share_embedding_weights
        self.initializer_range = initializer_range
        self.num_heads = num_heads

        default_initializer = {
            "name": "truncated_normal",
            "std": self.initializer_range,
            "mean": 0.0,
            "a": self.initializer_range * -2.0,
            "b": self.initializer_range * 2.0,
        }
        if embedding_initializer is None:
            embedding_initializer = default_initializer
        if attention_initializer is None:
            attention_initializer = default_initializer
        if output_layer_initializer is None:
            output_layer_initializer = default_initializer

        # embedding layer that only contains token embeddings
        self.embedding_layer = EmbeddingLayer(
            vocab_size=vocab_size,
            embedding_size=hidden_size,
            embeddings_initializer=embedding_initializer,
            position_embedding_type=position_embedding_type,
            position_embeddings_initializer=embedding_initializer,
            max_position_embeddings=max_position_embeddings,
            num_heads=num_heads,
            num_relative_attention_buckets=num_relative_attention_buckets,
            rotary_dim=rotary_dim,
            rope_theta=rope_theta,
            pad_rope=pad_rope,
            pos_scaling_factor=pos_scaling_factor,
        )

        self.drop_embd = nn.Dropout(embd_pdrop)

        norm_class = get_norm(norm_type)

        extra_attention_params["attention_kernel"] = attention_kernel
        decoder_layer = GPTJDecoderLayer(
            d_model=hidden_size,
            nhead=num_heads,
            use_untied_layer_norm=use_untied_layer_norm,
            dim_feedforward=filter_size,
            dropout=dropout_rate,
            activation=nonlinearity,
            layer_norm_eps=layer_norm_epsilon,
            norm_layer=norm_class,
            attention_module=attention_module,
            extra_attention_params=extra_attention_params,
            add_cross_attention=False,
            attention_type=attention_type,
            attention_dropout_rate=attention_dropout_rate,
            attention_softmax_fp32=attention_softmax_fp32,
            use_projection_bias_in_attention=use_projection_bias_in_attention,
            use_ffn_bias_in_attention=use_ffn_bias_in_attention,
            use_ffn_bias=use_ffn_bias,
            attention_initializer=attention_initializer,
            attention_output_layer_initializer=output_layer_initializer,
            ffn_initializer=output_layer_initializer,
            use_ff_layer1_dropout=False,
            norm_first=True,
        )

        self.ln_f = norm_class(hidden_size, eps=layer_norm_epsilon)

        self.transformer_decoder = TransformerDecoder(
            decoder_layer, num_layers=num_hidden_layers, norm=self.ln_f
        )

        self.attention_sliding_window_length = attention_sliding_window_length

        self.lm_head = nn.Linear(
            hidden_size, vocab_size, bias=use_bias_in_output
        )

        self.tie_weights()

        self.__reset_parameters()

    def reset_parameters(self):
        self.embedding_layer.reset_parameters()
        self.transformer_decoder.reset_parameters()
        self.__reset_parameters()

    def __reset_parameters(self):
        # Init final norm layer
        if hasattr(self.ln_f, "bias"):
            self.ln_f.bias.data.zero_()
        self.ln_f.weight.data.fill_(1.0)
        # Initialize LM head
        self.lm_head.weight.data.normal_(mean=0.0, std=self.initializer_range)
        if self.lm_head.bias is not None:
            self.lm_head.bias.data.zero_()

    def get_input_embeddings(self):
        return self.embedding_layer.get_input_embeddings()

    def get_output_embeddings(self):
        return self.lm_head

    def tie_weights(self):
        if not self.share_embedding_weights:
            return

        output_embedding = self.get_output_embeddings()
        input_embedding = self.get_input_embeddings()
        output_embedding.weight = input_embedding.weight

        if getattr(output_embedding, "bias", None) is not None:
            output_embedding.bias.data = nn.functional.pad(
                output_embedding.bias.data,
                (
                    0,
                    output_embedding.weight.shape[0]
                    - output_embedding.bias.shape[0],
                ),
                "constant",
                0,
            )
        if hasattr(output_embedding, "out_features") and hasattr(
            input_embedding, "num_embeddings"
        ):
            output_embedding.out_features = input_embedding.num_embeddings

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        attention_span=None,
        position_ids=None,
    ):
        hidden_states = self.embedding_layer(
            input_ids, position_ids=position_ids
        )
        hidden_states = self.drop_embd(hidden_states)

        causal_attention_mask = create_broadcasted_autoregressive_mask(
            batch_size=input_ids.shape[0],
            num_heads=self.num_heads,
            tgt_seq_length=input_ids.shape[1],
            attention_span=attention_span,
            sliding_window_length=self.attention_sliding_window_length,
            device=input_ids.device,
            dtype=hidden_states.dtype,
        )

        # Helpers on alibi/relative position embeddings
        length = input_ids.shape[1]
        self_attn_position_bias = self.embedding_layer.compute_position_bias(
            length, length
        )

        hidden_states = self.transformer_decoder(
            hidden_states,
            tgt_mask=causal_attention_mask,
            rotary_position_embedding_helper=self.embedding_layer.get_rope_helper(),
            self_attn_position_bias=self_attn_position_bias,
        )

        if (
            cstorch.use_cs()
            and cstorch.current_executor().cs_config.precision_opt_level == 1
        ):
            return cstorch.pol(bwd_level=0)(self.lm_head)(hidden_states)
        return self.lm_head(hidden_states)
