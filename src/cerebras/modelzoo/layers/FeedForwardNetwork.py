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

from typing import Callable, List, Optional, Union

import torch.nn as nn
from torch import Tensor

from cerebras.modelzoo.layers.activations import (
    get_activation,
    is_glu_activation,
)
from cerebras.modelzoo.layers.create_initializer import create_initializer


class SingleFeedForwardLayer(nn.Module):
    """
    Initialize Single FFN layer instance.
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        use_bias: bool = False,
        activation: Optional[Union[str, Callable[[Tensor], Tensor]]] = None,
        dropout: Optional[float] = None,
        device=None,
    ):
        super(SingleFeedForwardLayer, self).__init__()

        self.linear_layer = nn.Linear(
            in_features,
            out_features,
            bias=use_bias,
            device=device,
        )

        self.is_glu_activation = is_glu_activation(activation)
        if self.is_glu_activation:
            self.linear_layer_for_glu = nn.Linear(
                in_features,
                out_features,
                bias=use_bias,
                device=device,
            )

        if activation:
            self.act_layer = get_activation(activation)
        else:
            self.act_layer = None

        if dropout and dropout > 0.0:
            self.dropout_layer = nn.Dropout(p=dropout)
        else:
            self.dropout_layer = None

    def forward(self, inputs):
        if self.is_glu_activation:
            glu_component_1 = self.linear_layer(inputs)
            glu_component_2 = self.linear_layer_for_glu(inputs)
            outputs = self.act_layer(glu_component_1, glu_component_2)
        else:
            outputs = self.linear_layer(inputs)
            if self.act_layer:
                outputs = self.act_layer(outputs)
        if self.dropout_layer:
            outputs = self.dropout_layer(outputs)
        return outputs


class FeedForwardNetwork(nn.Module):
    """
    A feed forward network that consists of a stack of fully connected\
    layers arranged as [LinearLayer -> Activation -> Dropout] block
    repeated `len(layers_units)` times.

    Args:
        input_unit (int): integer for number of in_features of input.
        layers_units (list[int]): List of units for each layer.
        layers_activation (list[str]): List of activation types (str) for each layer.
        layers_dropout_rates (list[float]): List of dropout rates (float) for each
            layer.
        use_bias (bool): If `True`, use bias throughout all layers.
        kernel_initializer: Kernel initializer. Defaults to
            `"xavier_uniform"`.
        bias_initializer: Bias initializer. Defaults to `"zeros"`.
        output_layer_initializer: If not None, initialize the last projection
            layer with this initializer. Defaults to None.
        device (optional): Device to create the model parameters on, can be a cuda device or CS device.
    """

    def __init__(
        self,
        input_unit: int,
        layers_units: List[int],
        layers_activation: Optional[
            List[Union[str, Callable[[Tensor], Tensor]]]
        ] = None,
        layers_dropout_rates: Optional[List[float]] = None,
        use_bias: bool = False,
        kernel_initializer: str = "xavier_uniform",
        bias_initializer: str = "zeros",
        output_layer_initializer: Optional[str] = None,
        device=None,
    ):
        """
        Initialize the FFN object instance.
        """
        super(FeedForwardNetwork, self).__init__()
        self.num_dense_layers = len(layers_units)

        self.input_units = [input_unit] + layers_units[:-1]
        self.output_units = layers_units

        self.layers_activation = layers_activation
        self.layers_dropout_rates = layers_dropout_rates

        self.use_bias = use_bias
        self.kernel_initializer = kernel_initializer
        self.bias_initializer = bias_initializer
        self.device = device

        if output_layer_initializer is None:
            self.output_layer_initializer = self.kernel_initializer
        else:
            self.output_layer_initializer = output_layer_initializer

        assert (
            self.num_dense_layers > 0
        ), "Number of dense layers should be at least 1."

        if self.layers_activation:
            assert len(self.layers_activation) == self.num_dense_layers, (
                "len(layers_activation) should equal the number"
                " of dense layers."
            )
        else:
            self.layers_activation = [None] * self.num_dense_layers

        if self.layers_dropout_rates:
            assert len(self.layers_dropout_rates) == self.num_dense_layers, (
                "len(layers_dropout) should equal the number" "of dense layers."
            )
        else:
            self.layers_dropout_rates = [None] * self.num_dense_layers

        # This sets the namespace of the layer.
        # Using `nn.ModuleList` to have clear namespace such as
        # `ffn.{layer_num}.weight` and `ffn.{layer_num}.bias`
        # Class attributes cannot have `.` in their names when
        # inheriting from `nn.Module` and therefore cannot generate
        # attribute names on the fly and hence the need to use ModuleList.
        self.ffn = nn.ModuleList(
            [
                SingleFeedForwardLayer(
                    in_features,
                    out_features,
                    use_bias=self.use_bias,
                    activation=activation,
                    dropout=dropout,
                    device=self.device,
                )
                for in_features, out_features, activation, dropout in zip(
                    self.input_units,
                    self.output_units,
                    self.layers_activation,
                    self.layers_dropout_rates,
                )
            ]
        )

        # Initialize weights in Linear layers.
        self.__reset_parameters()

    def reset_parameters(self):
        self.__reset_parameters()

    def __reset_parameters(self):
        # Initialize weights for Linear layers
        for layer_num, linear_layer_module in enumerate(self.ffn):
            weight_initializer = create_initializer(self.kernel_initializer)
            if layer_num == self.num_dense_layers - 1:
                weight_initializer = create_initializer(
                    self.output_layer_initializer
                )
            # Initialize linear layer weights associated with the
            # 'GLU' type activation function with the kernel_initializer
            if hasattr(linear_layer_module, 'linear_layer_for_glu'):
                weight_initializer(
                    linear_layer_module.linear_layer_for_glu.weight.data
                )
            weight_initializer(linear_layer_module.linear_layer.weight.data)
            if self.use_bias:
                create_initializer(self.bias_initializer)(
                    linear_layer_module.linear_layer.bias.data
                )

    def forward(self, inputs):
        outputs = inputs
        for ffn_layer in self.ffn:
            outputs = ffn_layer(outputs)

        return outputs
