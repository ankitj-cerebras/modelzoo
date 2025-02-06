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

"""Cerebras ModelZoo Config Management CLI Tool"""

import argparse
import io
import os
import shutil
import tempfile
import warnings
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from cerebras.modelzoo.cli.utils import MZ_CLI_NAME


class ConfigMgmtCLI:
    def __init__(self):
        parser = argparse.ArgumentParser()
        self.configure_parser(parser)
        args = parser.parse_args()
        args.func(args)

    @staticmethod
    def epilog():
        return (
            f"Use `{MZ_CLI_NAME} model -h` to learn how to manage model configuration files. "
            f"See below for some basic examples.\n\n"
            f"Copy config file to local:\n"
            f"  $ {MZ_CLI_NAME} config pull gpt2_tiny -o workdir\n\n"
            f"Copy config file to local and rename it:\n"
            f"  $ {MZ_CLI_NAME} config pull gpt2_tiny -o workdir/my_gpt2_config.yaml\n\n"
            f"Convert a legacy config file to the current format:\n"
            f"  $ {MZ_CLI_NAME} config convert_legacy my_old_params.yaml -o my_new_params.yaml\n\n"
            f"Validate a config file:\n"
            f"  $ {MZ_CLI_NAME} config validate my_new_params.yaml\n\n"
            f"Get statistics on a config file including number of params:\n"
            f"  $ {MZ_CLI_NAME} config stats my_new_params.yaml\n\n"
            f"For more information on YAML configuration files, see: "
            f"https://docs.cerebras.net/en/latest/wsc/Model-zoo/yaml/index.html"
        )

    @staticmethod
    def configure_parser(parser):
        subparsers = parser.add_subparsers(dest="cmd", required=True)

        pull_parser = subparsers.add_parser(
            "pull",
            help="Saves a config file with a given variant name to the local workspace.",
        )
        pull_parser.add_argument(
            "variant",
            help="Config variant name to load.",
        )
        pull_parser.add_argument(
            "-o",
            "--out",
            help=(
                "Path to save the config file to. If it's a directory, the config file is saved to that "
                "directory. Otherwise, the config file is saved to the given file path. By default, the "
                "config is saved to the current working directory."
            ),
        )
        pull_parser.set_defaults(func=ConfigMgmtCLI.config_pull)

        validate_parser = subparsers.add_parser(
            "validate",
            help="Validate the provided config file.",
        )
        validate_parser.add_argument(
            "config",
            help="Config file to validate.",
        )
        validate_parser.set_defaults(func=ConfigMgmtCLI.config_validate)

        convert_parser = subparsers.add_parser(
            "convert_legacy",
            help="Convert a legacy params file to the updated format.",
        )
        convert_parser.add_argument(
            "config",
            help="Config file to convert.",
        )
        convert_parser.add_argument(
            "-o",
            "--out",
            help=(
                "Path to save the config file to. If it's a directory, the config file is saved to that "
                "directory. Otherwise, the config file is saved to the given file path. By default, the "
                "config is saved to the current working directory."
            ),
        )
        convert_parser.set_defaults(func=ConfigMgmtCLI.config_convert)

        stats_parser = subparsers.add_parser(
            "stats",
            help="Get relevant statistics for a model based on an input config file.",
        )
        stats_parser.add_argument(
            "params",
            help="Path to .yaml file with model parameters.",
        )
        stats_parser.set_defaults(
            func=ConfigMgmtCLI.config_stats, seen_args=set("params")
        )

    @staticmethod
    def config_pull(args):
        variant_path = ConfigMgmtCLI._find_variant(args.variant)
        out_path = Path(args.out if args.out else os.getcwd())
        if out_path.is_dir():
            filepath = out_path / variant_path.name
        else:
            filepath = out_path

        print(f"Saving config {args.variant} to {filepath}.")

        filepath.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(variant_path), str(filepath))

    @staticmethod
    def config_validate(args):
        from cerebras.modelzoo.trainer.validate_configs import validate_config

        validate_config(args.config)

    @staticmethod
    def config_convert(args):
        from cerebras.modelzoo.trainer.utils import (
            convert_legacy_params_to_trainer_params,
            is_legacy_params,
        )

        config_path = Path(args.config)

        if not config_path.exists():
            raise ValueError(f"Config file path {config_path} does not exist.")

        with config_path.open("r") as f:
            params = yaml.safe_load(f)

        if is_legacy_params(params):
            params = convert_legacy_params_to_trainer_params(params)
            out_path = Path(args.out if args.out else os.getcwd())
            if out_path.is_dir():
                filepath = out_path / f"{config_path.stem}_v2.yaml"
            else:
                filepath = out_path

            # injecting default device to config
            params["trainer"]["init"].setdefault("device", "CSX")

            print(f"Saving converted config to {filepath}.")

            filepath.parent.mkdir(parents=True, exist_ok=True)
            with filepath.open("w") as f:
                yaml.dump(params, f, sort_keys=False)

        else:
            print(f"Config file {config_path} is not a legacy config")

    @staticmethod
    def _find_variant(variant):
        import cerebras.modelzoo

        config = list(
            Path(cerebras.modelzoo.__file__)
            .parent.joinpath("models")
            .rglob(f"configs/params_{variant}.yaml")
        ) + list(
            Path(cerebras.modelzoo.__file__)
            .parent.joinpath("models")
            .rglob(f"configs/{variant}.yaml")
        )

        # handle edge cases
        if not config:
            config = list(
                Path(cerebras.modelzoo.__file__)
                .parent.joinpath("models")
                .rglob(f"configs/**/params_{variant}.yaml")
            ) + list(
                Path(cerebras.modelzoo.__file__)
                .parent.joinpath("models")
                .rglob(f"configs/**/{variant}.yaml")
            )

        if config:
            if len(config) > 1:
                raise ValueError(
                    f"Found multiple config files with name {variant}.yaml. Paths:\n{config}"
                )
            return config[0]

        raise ValueError(
            f"Config for variant {variant} not found in ModelZoo. Please ensure that {variant} is a valid "
            f"model variant. Use `{MZ_CLI_NAME} model list` and `{MZ_CLI_NAME} model info "
            f"<model-name>` to list valid models and their variants respectively."
        )

    @staticmethod
    def config_stats(args):
        from cerebras.modelzoo.cli.utils import _args_to_params
        from cerebras.modelzoo.trainer.utils import (
            configure_trainer_from_config,
            convert_legacy_params_to_trainer_params,
            is_legacy_params,
            merge_callbacks,
        )
        from cerebras.modelzoo.trainer.validate import validate_trainer_params

        with tempfile.TemporaryDirectory() as tempdir:
            params = _args_to_params(args)

            if isinstance(params, dict) and is_legacy_params(params):
                warnings.warn(
                    f"Detected that legacy params are being used. "
                    f"Automatically converting params to new format."
                )

                params = convert_legacy_params_to_trainer_params(
                    params,
                    # Allow None values in the params
                    obj_filter=lambda obj: obj is None,
                )

                # injecting default device to config
                params["trainer"]["init"].setdefault("device", "CSX")

            trainer_params = params["trainer"]
            if isinstance(trainer_params, list):
                init_params = [p["trainer"]["init"] for p in trainer_params]
            else:
                init_params = [trainer_params["init"]]

            for p in init_params:
                p["model_dir"] = tempdir
                p["callbacks"] = merge_callbacks(
                    p.get("callbacks", []), [{"CountParams": {}}]
                )

            configs = validate_trainer_params(params)

            for i, config in enumerate(configs):
                print(f"Trainer instance {i}:")
                with redirect_stdout(io.StringIO()) as f:
                    trainer = configure_trainer_from_config(config)

                count_params = trainer.callbacks["CountParams"]

                out, _ = count_params.get_table(trainer.model)

                print(out)


if __name__ == '__main__':
    ConfigMgmtCLI()
