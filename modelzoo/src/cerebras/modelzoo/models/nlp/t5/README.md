# T5 Language Models

- [T5 Language Models](#t5-language-models)
  - [Model overview](#model-overview)
  - [Sequence of the steps to perform](#sequence-of-the-steps-to-perform)
  - [Code structure](#code-structure)
  - [Data processing](#data-processing)
    - [Colossal Clean Crawled Corpus dataset](#colossal-clean-crawled-corpus-dataset)
    - [C4 processing commands](#c4-processing-commands)
  - [Input function pipeline](#input-function-pipeline)
  - [Running model commands](#running-model-commands)
  - [To compile/validate, run train and eval on Cerebras System](#to-compilevalidate-run-train-and-eval-on-cerebras-system)
  - [To run train and eval on GPU/CPU](#to-run-train-and-eval-on-gpucpu)
  - [Implementation notes](#implementation-notes)
  - [Configs included for this model](#configs-included-for-this-model)
  - [Citations](#citations)

## Model overview

This directory contains implementations for the T5 Model, specifically, a version of the T5 model introduced in [\[2\]](https://arxiv.org/abs/1910.10683), which was trained in a self-supervised manner on the C4 [dataset](https://www.tensorflow.org/datasets/catalog/c4).
An earlier version of T5 also included supervised datasets during pre-training, but we follow the improvements in T5.1.1 [\[3\]](https://github.com/google-research/text-to-text-transfer-transformer/blob/main/released_checkpoints.md#t511) that include training only on C4.
T5 includes some changes to the block, including the location of the normalization layer and the residual connections, also illustrated in the figure below.

We refer to sections of the T5 paper for further details, but the primary contributions from T5 come from studying different variations of transformer architectures such as encoder-decoder vs decoder-only (Section 3.2), and optimization objectives such as language-modeling vs denoising (Section 3.3). 
They also introduce the "text-to-text" formulation that allows any arbitrary NLP task to be converted into the same format. This allows their model to be directly applied to any task (Section 2.4).

<p align="center">
    <img src="./images/t5_block.png">
</p>
<p align="center">
    T5 block
</p>

## Sequence of the steps to perform

The high-level steps for training a model are relatively simple, involving data-processing and tokenization, and then model training.

* Data-processing and tokenization
    * Elaborated in the [Data Processing Commands](#data-processing) section.
* Training the model on CS system or GPU using `run.py`
    * Elaborated in the [Running Model Commands](#running-model-commands) section.

The steps to perform are listed in the diagram below. Bold files are scripts to be run, with explanations of the steps in parenthesis underneath. 
<p align="center">
    <img src="./images/t5_seq_steps.png">
</p>
<p align="center">
    Flow-charts for the training procedure for the T5 models. Files in bold are scripts to be run, along with short explanations of the steps involved. 
</p>

## Code structure

In this section we describe the structure of the code for the Cerebras model and data implementations.

The following few scripts are relatively generic and shared between models. They provide an entry-point from the model-specific code to interface with shared training/validation code.

* `run.py`: A generic training script.
* `model.py`: Provides a common wrapper for all models, which interfaces with  model-specific code. In this repo the model-specific code is in `t5_model.py`. The wrapper provides a common interface for handling the function call of the model with its specific data format. It also provides a common interface to use the same format of configuration files from `configs/` to construct various models.
* `utils.py`: Miscellaneous functions that are used to interface with the YAML files.

The following directories contain the specific implementation details for the current model.

* `configs/`: A directory of YAML files that specifies all the details about a training run. Each config YAML is split into five sections that determine the training run: `train_input`, `eval_input`, `model`, `optimizer`, and `runconfig`. The first two sections specify the data-processor class and its various arguments, such as batch-size, file-paths to data, etc. The `model` section specifies arguments such as hidden-sizes, number of layers, dropout rates, etc. The `optimizer` section specifies which algorithm to use, such as Adam [\[4\]](https://arxiv.org/abs/1412.6980), AdamW [\[5\]](https://arxiv.org/abs/1711.05101), or Adafactor [\[6\]](https://arxiv.org/abs/1804.04235). It also specifies arguments such as decay rates. Finally the `runconfig` section specifies how many steps you want to train for, the interval for saving models, interval for logging loss values in tensorboard, etc.
* `data/nlp/t5`: A directory for scripts relating to data-processing. The `T5DynamicDataProcessor.py` and `TransformerDynamicDataProcessor.py` scripts create the [PyTorch DataLoader](https://pytorch.org/tutorials/beginner/basics/data_tutorial.html) that is used during training and validation. It uses functions from `t5_utils.py` for a lot of the functionality. 

## Data processing

### Colossal Clean Crawled Corpus dataset

The Colossal Clean Crawled Corpus (C4) Dataset is a publicly-available dataset hosted [here](https://www.tensorflow.org/datasets/catalog/c4), and is based on cleaning ~7 TB of data from [Common Crawl](https://commoncrawl.org/). See Section 2.2 of [\[2\]](https://arxiv.org/abs/1910.10683) for further details.
The following commands handle formatting of this dataset for you, but if you decide to change the dataset or dataloaders, make 
sure you follow the same input function pipeline as described in [Input function pipeline](#input-function-pipeline).

### C4 processing commands

Download the pre-trained tokenizer model from [HuggingFace](https://huggingface.co/google/t5-11b-ssm-nq/blob/main/spiece.model). Place it in the `./input/` directory.

Move to the [data_preparation/nlp/t5](../../../data_preparation/nlp/t5) directory, and simply run [preprocess_external.sh](../../../data_preparation/nlp/t5/preprocess_external.sh) script:

```bash
bash preprocess_external.sh
```

By running it, you will download C4 from HuggingFace and tokenize it using the [sentencepiece](https://github.com/google/sentencepiece) tokenizer. The tokens for Tensorflow and PyTorch models are the same, so this only needs to be run once.  

Note: it saves the data to `./c4` directory, but this can be changed easily by adjusting the first line in the `preprocess.sh` script that specifies `dataset_root`. Since the dataset is extremely large, it takes ~56 hours on a 4 cpu core machine. However, it can easily be parallelized across multiple nodes to speed up the process if you have a distributed compute cluster.

## Input function pipeline

For details about the input function pipeline used for the models located in this folder, please refer to a separate documentation [README.md](../../../data_preparation/nlp/t5/README.md).

## Running model commands

## To compile/validate, run train and eval on Cerebras System

Please follow the instructions on our [quickstart in the Developer Docs](https://docs.cerebras.net/en/latest/wsc/getting-started/cs-appliance.html).

## To run train and eval on GPU/CPU

If running on a cpu or gpu, activate the environment from [Python GPU Environment setup](../../../../../../PYTHON-SETUP.md), and simply run:

```
python run.py {CPU,GPU} --mode train --params path/to/yaml --model_dir /path/to/model_dir
```

For each of these commands,

* `path/to/yaml` is a path to the YAML configuration file containing the model parameters. Parameters for the base configuration of the model are provided in the section [Configs included for this model](#Configs-included-for-this-model).
* `path/to/model_dir` is the path to the model directory where compile and training artifacts will be saved.

## Implementation notes

There are a couple modifications to both models based on current support for operations on CS systems. Resolving these is currently in progress:

1. We do not currently support the Adafactor optimizer used to train the original T5 model. Instead we use AdamW, which results in a higher loss at the end of pre-training.
2. For T5, we do not currently support `RMSNorm` [\[7\]](https://arxiv.org/abs/1910.07467). Instead, we use `LayerNorm` [\[8\]](https://arxiv.org/abs/1607.06450v1) as our normalization layer. 

## Configs included for this model

In the [configs](./configs/) directory we have files for T5. 

* [T5-small](configs/t5_small.yaml) have a small reference with `d_kv=64`, `num_heads=6`, `encoder_num_hidden_layers=8`.
* [T5-base](configs/t5_base.yaml) have a base reference with `d_kv=64`, `num_heads=12`, `encoder_num_hidden_layers=12`.
* [T5-3B](configs/t5_3B.yaml) have a 3B model reference with `d_kv=128`, `num_heads=32`, `encoder_num_hidden_layers=24`.
* [T5-11B](configs/t5_11B.yaml) have a base reference with `d_kv=128`, `num_heads=128`, `encoder_num_hidden_layers=24`.


These files are just samples, and can be adjusted for any changes in training procedure that you desire, such as different number of layers or hidden sizes, or different number of steps.  


## Citations

[1] [Attention Is All You Need](https://arxiv.org/abs/1706.03762) 

[2] [Exploring the Limits of Transfer Learning with a Unified Text-to-text Transformer](https://arxiv.org/abs/1910.10683).

[3] [T5v1.1](https://github.com/google-research/text-to-text-transfer-transformer/blob/main/released_checkpoints.md#t511).

[4] [Adam](https://arxiv.org/abs/1412.6980)

[5] [AdamW](https://arxiv.org/abs/1711.05101)

[6] [Adafactor](https://arxiv.org/abs/1804.04235)

[7] [RMSNorm](https://arxiv.org/abs/1910.07467)

[8] [LayerNorm](https://arxiv.org/abs/1607.06450v1)

[9] [An Empirical Study of Pre-Trained Language Model Positional Encoding](https://arxiv.org/pdf/2010.04903.pdf)
