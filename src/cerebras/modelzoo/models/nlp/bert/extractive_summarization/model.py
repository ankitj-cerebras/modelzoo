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

import torch

from cerebras.modelzoo.common.registry import registry
from cerebras.modelzoo.models.nlp.bert.bert_finetune_models import (
    BertForSummarization,
    BertForSummarizationLoss,
)
from cerebras.modelzoo.models.nlp.bert.utils import check_unused_model_params


@registry.register_model(
    "bert/extractive_summarization",
    datasetprocessor=["BertSumCSVDataProcessor"],
)
class BertSummarizationModel(torch.nn.Module):
    def __init__(self, params):
        super().__init__()

        model_params = params["model"].copy()
        dropout_rate = model_params.pop("dropout_rate")
        embedding_dropout_rate = model_params.pop(
            "embedding_dropout_rate", dropout_rate
        )

        num_labels = 2
        loss_weight = model_params.pop("loss_weight")
        use_cls_bias = model_params.pop("use_cls_bias")

        model_kwargs = {
            "vocab_size": model_params.pop("vocab_size"),
            "hidden_size": model_params.pop("hidden_size"),
            "num_hidden_layers": model_params.pop("num_hidden_layers"),
            "num_heads": model_params.pop("num_heads"),
            "filter_size": model_params.pop("filter_size"),
            "nonlinearity": model_params.pop("encoder_nonlinearity"),
            "pooler_nonlinearity": model_params.pop(
                "pooler_nonlinearity", None
            ),
            "embedding_dropout_rate": embedding_dropout_rate,
            "dropout_rate": dropout_rate,
            "attention_dropout_rate": model_params.pop(
                "attention_dropout_rate"
            ),
            "attention_kernel": model_params.pop("attention_kernel", None),
            "max_position_embeddings": model_params.pop(
                "max_position_embeddings"
            ),
            "layer_norm_epsilon": float(model_params.pop("layer_norm_epsilon")),
        }

        self.model = BertForSummarization(
            num_labels=num_labels,
            loss_weight=loss_weight,
            use_cls_bias=use_cls_bias,
            **model_kwargs,
        )
        self.loss_fn = BertForSummarizationLoss(
            num_labels,
            loss_weight,
        )

        self.compute_eval_metrics = model_params.pop(
            "compute_eval_metrics", False
        )
        self.vocab_file = model_params.pop("vocab_file")
        check_unused_model_params(model_params)
        if self.compute_eval_metrics:
            raise NotImplementedError(
                "RougeScoreMetric not yet supported in weight streaming"
            )

            self.rouge1_score = RougeScoreMetric(
                max_n=1, vocab_file=self.vocab_file, name="eval/rouge1"
            )
            self.rouge2_score = RougeScoreMetric(
                max_n=2, vocab_file=self.vocab_file, name="eval/rouge2"
            )

    def forward(self, data):
        logits = self.model(
            input_ids=data["input_ids"],
            attention_mask=data["attention_mask"],
            token_type_ids=data["token_type_ids"],
            cls_tokens_positions=data["cls_indices"],
        )
        loss = self.loss_fn(
            logits, data["labels"], data["cls_weights"].clone().to(logits.dtype)
        )
        if not self.model.training and self.compute_eval_metrics:
            labels = data["labels"].clone()
            predictions = logits.argmax(-1).int()
            input_ids = data["input_ids"].clone()
            cls_indices = data["cls_indices"].clone()
            cls_weights = data["cls_weights"].clone()
            self.rouge1_score(
                labels,
                predictions,
                cls_indices,
                cls_weights,
                input_ids,
            )
            self.rouge2_score(
                labels,
                predictions,
                cls_indices,
                cls_weights,
                input_ids,
            )

        return loss
