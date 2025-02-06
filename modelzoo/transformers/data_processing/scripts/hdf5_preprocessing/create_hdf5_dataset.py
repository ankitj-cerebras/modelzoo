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

"""
Script that generates a dataset in HDF5 format for GPT Models.
"""

import importlib
import logging
import os
import sys
from multiprocessing import cpu_count
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "../../../../.."))
from modelzoo.common.input.utils import check_and_create_output_dirs
from modelzoo.transformers.data_processing.scripts.hdf5_preprocessing.utils import (
    dump_args,
    dump_result,
    get_files,
    get_params,
    get_verification_args,
    process_dataset,
    verify_saved_hdf5_files_mp,
)

from modelzoo.transformers.data_processing.scripts.hdf5_preprocessing.hdf5_dataset_preprocessors import (  # noqa
    LMDataPreprocessor,
    SummarizationPreprocessor,
)

# Custom preprocessors
from modelzoo.transformers.data_processing.scripts.hdf5_preprocessing.hdf5_curation_corpus_preprocessor import (  # noqa
    CurationCorpusPreprocessor,
)
from modelzoo.transformers.data_processing.scripts.hdf5_preprocessing.hdf5_nlg_preprocessor import (  # noqa
    NLGPreprocessor,
)


logging.basicConfig()
logger = logging.getLogger(__file__)
logger.setLevel(logging.INFO)


def main():
    """Main function for execution."""
    params = get_params(desc="Create HDF5 dataset for language models")

    output_dir = params["setup"].get("output_dir", "./data_dir/")
    if not params["processing"].get("resume_from_checkpoint", False):
        check_and_create_output_dirs(output_dir, filetype="h5")
    logger.info(f"\nWriting data to {output_dir}.")
    json_params_file = os.path.join(output_dir, "data_params.json")
    dump_args(params, json_params_file)

    metadata_files = params["setup"].pop("metadata_files", None)
    if metadata_files:
        metadata_files = metadata_files.split(",")
    input_dir = params["setup"].pop("input_dir", None)
    input_files = get_files(input_dir=input_dir, metadata_files=metadata_files)

    processes = params["setup"].pop("processes", 0)
    if processes == 0:
        processes = cpu_count()

    ds_processor = params["setup"].pop(
        "dataset_processor", "LMDataPreprocessor"
    )
    module_name = params["setup"].pop("module", None)
    if module_name:
        module = importlib.import_module(module_name)
        dataset_processor = getattr(module, ds_processor)(params)
    else:
        dataset_processor = getattr(sys.modules[__name__], ds_processor)(params)

    unused_params = [
        key for key in params["setup"].keys() if key != "output_dir"
    ]
    if unused_params:
        logger.warning(
            "The following setup params are unused: " + ", ".join(unused_params)
        )

    results = process_dataset(input_files, dataset_processor, processes)
    vocab_size = dataset_processor.get_vocab_size()

    logger.info(
        f"\nFinished writing data to {output_dir}."
        f" Runtime arguments and outputs can be found at {json_params_file}."
    )

    logger.info(f"Verifying the converted dataset at: {output_dir}")
    output_files = list(Path(output_dir).glob("*.h5"))
    verification_args = get_verification_args(
        processes, dataset_processor
    )  # for verify_saved_hdf5_files_mp
    dataset_stats = verify_saved_hdf5_files_mp(
        output_files, verification_args, vocab_size
    )
    logger.info("Done verifying the converted dataset.")

    dump_result(
        results,
        dataset_stats,
        json_params_file,
        dataset_processor.eos_id,
        dataset_processor.pad_id,
        vocab_size,
    )


if __name__ == "__main__":
    main()
