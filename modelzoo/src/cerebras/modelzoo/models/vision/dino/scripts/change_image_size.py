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

import argparse
import logging
import math
import os
from typing import List, Optional, Tuple

import torch
import torch.nn.functional as F
import yaml

import cerebras.pytorch as cstorch

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Note that MZ does not use timm package yet, so we copy the function here for now
# Code from https://github.com/huggingface/pytorch-image-models/blob/cdbafd90574206d997bdf6530ca98af22588b1c5/timm/layers/pos_embed.py#L17
## COPY_START
def resample_abs_pos_embed(
    posemb: torch.Tensor,
    new_size: List[int],
    old_size: Optional[List[int]] = None,
    num_prefix_tokens: int = 1,
    interpolation: str = 'bicubic',
    antialias: bool = True,
    verbose: bool = False,
):
    # sort out sizes, assume square if old size not provided
    num_pos_tokens = posemb.shape[1]
    num_new_tokens = new_size[0] * new_size[1] + num_prefix_tokens
    if num_new_tokens == num_pos_tokens and new_size[0] == new_size[1]:
        return posemb

    if old_size is None:
        hw = int(math.sqrt(num_pos_tokens - num_prefix_tokens))
        old_size = hw, hw

    if num_prefix_tokens:
        posemb_prefix, posemb = (
            posemb[:, :num_prefix_tokens],
            posemb[:, num_prefix_tokens:],
        )
    else:
        posemb_prefix, posemb = None, posemb

    # do the interpolation
    embed_dim = posemb.shape[-1]
    orig_dtype = posemb.dtype
    posemb = posemb.float()  # interpolate needs float32
    posemb = posemb.reshape(1, old_size[0], old_size[1], -1).permute(0, 3, 1, 2)
    posemb = F.interpolate(
        posemb, size=new_size, mode=interpolation, antialias=antialias
    )
    posemb = posemb.permute(0, 2, 3, 1).reshape(1, -1, embed_dim)
    posemb = posemb.to(orig_dtype)

    # add back extra (class, etc) prefix tokens
    if posemb_prefix is not None:
        posemb = torch.cat([posemb_prefix, posemb], dim=1)

    if not torch.jit.is_scripting() and verbose:
        logger.info(f'Resized position embedding: {old_size} to {new_size}.')

    return posemb


## COPY_END

# -----------------------------------------------------------------------------
# Utility Functions
# -----------------------------------------------------------------------------


def load_yaml_config(path: str) -> dict:
    """
    Load a YAML configuration file from the given path.

    Args:
        path (str): Path to the YAML file.

    Returns:
        dict: Parsed configuration dictionary.
    """
    with open(path, "r") as f:
        return yaml.safe_load(f)


def save_yaml_config(config: dict, path: str, input_config_path: str):
    """
    Save a dictionary as a YAML configuration file.

    Args:
        config (dict): Configuration dictionary to save.
        path (str): File path to save the YAML file to.
    """
    with open(path, "w") as f:
        f.write("# Generated by change_image_size.py\n")
        f.write(f"# --input_config: {input_config_path}\n")
        yaml.dump(config, f, sort_keys=False)


def parse_image_size(values: List[str]) -> List[int]:
    """
    Converts 1 or 2 CLI integers into [W, H].

    Args:
        values (list[str]): List of strings representing image size(s).

    Returns:
        list[int]: [width, height] list of integers.

    Raises:
        ValueError: If the number of values is not 1 or 2.
    """
    if len(values) == 1:
        val = int(values[0])
        return [val, val]
    elif len(values) == 2:
        return [int(values[0]), int(values[1])]
    else:
        raise ValueError(f"Expected 1 or 2 integers, got {values}.")


# -----------------------------------------------------------------------------
# Config Update Logic
# -----------------------------------------------------------------------------


def update_image_sizes_in_dict(
    d: dict,
    old_global_image_size: int,
    old_local_image_size: int,
    new_global_image_size: int,
    new_local_image_size: int,
) -> dict:
    """
    Recursively updates 'global_image_size'/'image_size' and 'local_image_size'
    if they match expected old values. Warns if it encounters integers
    matching old sizes in other keys.

    Args:
        d (dict): Dictionary to update.
        old_global_image_size (int): Old global image size.
        old_local_image_size (int): Old local image size.
        new_global_image_size (int): New global image size.
        new_local_image_size (int): New local image size.
    Returns:
        dict: Updated dictionary.
    """

    def recursive_update(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                current_path = f"{path}.{k}" if path else k

                # If v is int, check if it matches old sizes
                if isinstance(v, int):
                    if k in ["global_image_size", "image_size"]:
                        if v != old_global_image_size:
                            raise ValueError(
                                f"[Mismatch] {current_path}: "
                                f"expected {old_global_image_size}, got {v}"
                            )
                        obj[k] = new_global_image_size
                        logger.info(
                            f"Updated {current_path}: "
                            f"{old_global_image_size} -> {new_global_image_size}"
                        )
                    elif k == "local_image_size":
                        if v != old_local_image_size:
                            raise ValueError(
                                f"[Mismatch] {current_path}: "
                                f"expected {old_local_image_size}, got {v}"
                            )
                        obj[k] = new_local_image_size
                        logger.info(
                            f"Updated {current_path}: "
                            f"{old_local_image_size} -> {new_local_image_size}"
                        )
                    elif v in (old_global_image_size, old_local_image_size):
                        logger.warning(
                            f"Potential match found at {current_path} = {v}"
                        )
                elif isinstance(v, (dict, list)):
                    recursive_update(v, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                current_path = f"{path}[{i}]"
                if isinstance(item, int) and item in (
                    old_global_image_size,
                    old_local_image_size,
                ):
                    logger.warning(
                        f"Potential match found at {current_path} = {item}"
                    )
                elif isinstance(item, (dict, list)):
                    recursive_update(item, current_path)

    recursive_update(d)
    return d


def update_config(
    config: dict,
    global_size: List[int],
    local_size: List[int],
    patch_size: List[int],
) -> dict:
    """
    Updates config to reflect new global image size and local patch dims.

    Args:
        config (dict): Configuration dictionary.
        global_size (list[int]): Global image size [W, H].
        local_size (list[int]): Local image size [W, H].
        patch_size (list[int]): Patch size [W, H].

    Returns:
        dict: Updated configuration dictionary.
    """
    old_global_size = None
    old_local_size = None

    for trunk_cfg in config["trainer"]["init"]["model"]["image_model_trunks"]:
        if "image_model" not in trunk_cfg:
            continue
        model_cfg = trunk_cfg["image_model"]

        # Update the global image size if it's a list
        if isinstance(model_cfg.get("image_size"), list):
            old_global_size = model_cfg["image_size"]
            model_cfg["image_size"] = global_size

        # Update local_patch_dims if present
        interp_cfg = model_cfg.get("interpolate_position_embedding", {})
        if "local_patch_dims" in interp_cfg:
            if old_local_size is None:
                old_local_size = [
                    l * p
                    for l, p in zip(interp_cfg["local_patch_dims"], patch_size)
                ]
            interp_cfg["local_patch_dims"] = [
                local_size[0] // patch_size[0],
                local_size[1] // patch_size[1],
            ]

    if old_global_size is None or old_local_size is None:
        raise ValueError(
            "Could not find valid global/local image sizes to update."
        )

    # If both old/new sizes are square, update data loaders
    # (update_image_sizes_in_dict only handles single int sizes).
    if (
        old_global_size[0] == old_global_size[1]
        and global_size[0] == global_size[1]
        and old_local_size[0] == old_local_size[1]
        and local_size[0] == local_size[1]
    ):
        for section in config["trainer"].keys():
            for data_loader_cfg in config["trainer"][section]:
                update_image_sizes_in_dict(
                    config["trainer"][section][data_loader_cfg],
                    old_global_size[0],
                    old_local_size[0],
                    global_size[0],
                    local_size[0],
                )

    return config


def compute_new_interpolation_matrix(
    global_size: List[int],
    patch_size: List[int],
    model_config: dict,
) -> torch.Tensor:
    """
    Creates a bicubic interpolation matrix for position embeddings when
    resizing the image. Numerically matches the original implementation.
    The code is adapted from ViTEmbeddingLayer.create_bicubic_interpolation_matrix

    Args:
        global_size (list[int]): Global image size [W, H].
        patch_size (list[int]): Patch size [W, H].
        model_config (dict): Configuration for the image model.

    Returns:
        torch.Tensor: Computed interpolation matrix.

    Raises:
        ValueError: If the position_embedding_type is not "learned".
    """
    if model_config["position_embedding_type"] != "learned":
        raise ValueError("Only 'learned' position embeddings are supported.")

    interp_cfg = model_config["interpolate_position_embedding"]
    local_patch_dims = interp_cfg["local_patch_dims"]
    interpolate_offset = interp_cfg.get("interpolate_offset", 0.1)
    antialias = interp_cfg.get("antialias", False)

    gw, gh = [g // p for g, p in zip(global_size, patch_size)]
    num_global_patches = gw * gh

    def get_kernel(position):
        lw, lh = local_patch_dims  # local patch dims
        sx = (lw + interpolate_offset) / gw
        sy = (lh + interpolate_offset) / gh
        return F.interpolate(
            position,
            mode="bicubic",
            antialias=antialias,
            scale_factor=(sx, sy),
        )

    T = torch.eye(num_global_patches, device="cpu").reshape(
        num_global_patches, 1, 1, gw, gh
    )
    interp_matrix = (
        torch.vmap(get_kernel, in_dims=0)(T)
        .reshape(num_global_patches, -1)
        .transpose(1, 0)
    )

    # If model prepends CLS token, expand interpolation matrix
    if model_config.get("prepend_cls_token", False):
        # Expand interpolation matrix to accommodate for CLS token
        interp_matrix = F.pad(
            interp_matrix, pad=(0, 1, 1, 0), mode='constant', value=0
        )
        interp_matrix[0, -1] = 1

    return interp_matrix


def verify_checkpoint_with_config(input_ckpt: str, config: dict):
    """
    Verifies that the checkpoint's interpolation matrix dimensions match
    the config's expected image size and patch layout for the first trunk.

    Args:
        input_ckpt (str): Path to the input checkpoint file.
        config (dict): Parsed configuration dictionary.

    Raises:
        ValueError: If the interpolation matrix shape does not match
            the expected dimensions or if none is found.
        FileNotFoundError: If input_ckpt does not exist.
    """
    if not os.path.isfile(input_ckpt):
        raise FileNotFoundError(f"Checkpoint not found: {input_ckpt}")

    checkpoint = cstorch.load(input_ckpt)

    # Grab the model config from the first trunk
    model_cfg = config["trainer"]["init"]["model"]["image_model_trunks"][0][
        "image_model"
    ]
    patch_size = model_cfg["patch_size"]

    # Get the global image size from config (assuming it's already properly set or is an int)
    global_size = model_cfg.get("image_size", None)
    if global_size is None:
        raise ValueError("Model config does not have an 'image_size' key.")

    if isinstance(global_size, int):
        global_size = [global_size, global_size]

    # Number of global patches
    gw, gh = [g // p for g, p in zip(global_size, patch_size)]
    num_global_patches = gw * gh

    # Number of local patches
    local_patches = model_cfg["interpolate_position_embedding"][
        "local_patch_dims"
    ]
    num_local_patches = local_patches[0] * local_patches[1]

    # Account for CLS token
    prepend_cls = model_cfg.get("prepend_cls_token", False)
    expected_local_dim = num_local_patches + (1 if prepend_cls else 0)
    expected_global_dim = num_global_patches + (1 if prepend_cls else 0)

    found_any = False
    for key, value in checkpoint["model"].items():
        if key.endswith("embedding_layer.interpolation_matrix"):
            found_any = True
            if value.shape != (expected_local_dim, expected_global_dim):
                raise ValueError(
                    f"Checkpoint interpolation matrix '{key}' has shape "
                    f"{value.shape} but expected {(expected_local_dim, expected_global_dim)} "
                    f"based on the config."
                )

    if not found_any:
        raise ValueError(
            "No 'embedding_layer.interpolation_matrix' found in checkpoint to verify."
        )

    logger.info("Checkpoint interpolation matrix dimensions match the config.")


def update_checkpoint(
    input_ckpt: str,
    new_interp_matrix: torch.Tensor,
    old_image_size: Tuple[int, int],
    patch_size: Tuple[int, int],
    new_image_size: Tuple[int, int],
    has_cls_token: bool,
    antialias: bool = False,
):
    """
    Updates all matching interpolation_matrix parameters in the checkpoint.

    Args:
        input_ckpt (str): Path to the input checkpoint.
        new_interp_matrix (torch.Tensor): New interpolation matrix to insert.
        old_image_size (tuple[int, int]): Original image size.
        patch_size (tuple[int, int]): Patch size.
        new_image_size (tuple[int, int]): New image size.
        has_cls_token (bool): Whether the model prepends a CLS token.
        antialias (bool): Whether to use antialiasing during resampling.

    Returns:
        dict: The updated checkpoint state.
    Raises:
        FileNotFoundError: If the input checkpoint does not exist.
    """
    if not os.path.isfile(input_ckpt):
        raise FileNotFoundError(f"Checkpoint not found: {input_ckpt}")

    checkpoint = cstorch.load(input_ckpt)

    # Replace any key ending in 'embedding_layer.interpolation_matrix'
    replaced = 0
    for key in checkpoint["model"].keys():
        if key.endswith("embedding_layer.interpolation_matrix"):
            checkpoint["model"][key] = new_interp_matrix
            replaced += 1
    logger.info(f"Replaced {replaced} interpolation matrices")

    replaced = 0
    new_patch_dims = (
        new_image_size[0] // patch_size[0],
        new_image_size[1] // patch_size[1],
    )
    old_patch_dims = (
        old_image_size[0] // patch_size[0],
        old_image_size[1] // patch_size[1],
    )
    for key in checkpoint["model"].keys():
        if key.endswith("embedding_layer.position_embeddings.weight"):
            new_position_embedding = resample_abs_pos_embed(
                checkpoint["model"][key][None, :, :],
                new_patch_dims,
                old_patch_dims,
                num_prefix_tokens=1 if has_cls_token else 0,
                antialias=antialias,
            ).squeeze(0)
            checkpoint["model"][key] = new_position_embedding
            replaced += 1
    logger.info(f"Replaced {replaced} position embeddings")
    return checkpoint


def main():
    """
    Main function to convert DINO config & checkpoint to new image sizes.

    1. Parse command-line arguments.
    2. Load original config.
    3. Verify checkpoint compatibility with the existing config.
    4. Perform sanity checks on global/local sizes.
    5. Update config with new sizes.
    6. Save the updated config.
    7. Compute new interpolation matrix.
    8. Update checkpoint with the new matrix and new position embeddings.
    """
    parser = argparse.ArgumentParser(
        "Convert DINO config & checkpoint to new image sizes."
    )
    parser.add_argument("--input_config", required=True)
    parser.add_argument("--input_ckpt", required=True)
    parser.add_argument("--output_config", required=True)
    parser.add_argument("--output_ckpt", required=True)
    parser.add_argument("--global_size", nargs="+", required=True)
    parser.add_argument("--local_size", nargs="+", required=True)
    args = parser.parse_args()

    # 1. Load original config
    config = load_yaml_config(args.input_config)
    try:
        model_cfg = config["trainer"]["init"]["model"]["image_model_trunks"][0][
            "image_model"
        ]
        old_image_size = model_cfg["image_size"]
        patch_size = model_cfg["patch_size"]

        if not isinstance(old_image_size, list) or not isinstance(
            patch_size, list
        ):
            raise ValueError(
                "Expected 'image_size' and 'patch_size' to be a list."
            )

        logger.info(
            f"Found model image_size:{old_image_size}, patch size:{patch_size}"
        )
    except KeyError:
        raise ValueError(
            "Invalid config format. Please make sure the input config is in cszoo v2 format."
        )

    # 2. Verify that the checkpoint matches the *current* config dimensions
    #    (before we modify the config).
    verify_checkpoint_with_config(args.input_ckpt, config)

    # 3. Parse the user-specified global/local sizes
    global_size = parse_image_size(args.global_size)
    local_size = parse_image_size(args.local_size)

    # 4. Sanity checks
    for size_ in (global_size, local_size):
        for s, p in zip(size_, patch_size):
            if s % p != 0:
                raise ValueError(
                    f"Size {size_} must be a multiple of patch size {patch_size}."
                )

    # 5. Update config
    config = update_config(config, global_size, local_size, patch_size)

    # 6. Save updated config
    save_yaml_config(config, args.output_config, args.input_config)
    logger.info(f"Saved updated config to {args.output_config}")

    # 7. Compute new interpolation matrix
    has_cls_token = model_cfg.get("prepend_cls_token", False)
    antialias = model_cfg.get("interpolate_position_embedding", {}).get(
        "antialias", False
    )
    new_interp_mat = compute_new_interpolation_matrix(
        global_size, patch_size, model_cfg
    )

    # 8. Update checkpoint
    checkpoint = update_checkpoint(
        args.input_ckpt,
        new_interp_mat,
        old_image_size,
        patch_size,
        global_size,
        has_cls_token,
        antialias,
    )
    cstorch.save(checkpoint, args.output_ckpt)
    logger.info(f"Saved checkpoint to {args.output_ckpt}")


if __name__ == "__main__":
    main()
