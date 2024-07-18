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

"""Contains the base Callback class and the global callback registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any, Dict, List

import torch

import cerebras.pytorch

if TYPE_CHECKING:
    from ..trainer import Trainer
    from .loop import TrainingLoop, ValidationLoop


class Callback:
    """
    Base class for all callbacks.
    """

    def pre_setup(self, trainer: Trainer):
        """Called before the trainer setup.

        Args:
            trainer: Trainer instance.
        """

    def setup(self, trainer: Trainer):
        """Setup the callback using the trainer.

        Args:
            trainer: Trainer instance.
        """

    def finalize(self):
        """Clean up the callback.

        This method is called when the trainer is destructed.
        """

    def on_enter_fit(
        self,
        trainer: Trainer,
        stack: ExitStack,
        train_dataloader: cerebras.pytorch.utils.data.DataLoader,
        val_dataloader: cerebras.pytorch.utils.data.DataLoader,
        loop: TrainingLoop,
    ):
        """Hook that allows arbitrary context managers to be entered
        at the beginning of the fit method.

        Args:
            trainer: Trainer instance.
            stack: ExitStack object.
            train_dataloader: Train dataloader.
            val_dataloader: Validation dataloader.
            loop: TrainingLoop object.
        """

    def on_fit_start(
        self,
        trainer: Trainer,
        train_dataloader: cerebras.pytorch.utils.data.DataLoader,
        val_dataloader: cerebras.pytorch.utils.data.DataLoader,
        loop: TrainingLoop,
    ):
        """Called at the beginning of the fit method.

        Args:
            trainer: Trainer instance.
            train_dataloader: Train dataloader.
            val_dataloader: Validation dataloader.
            loop: TrainingLoop object.
        """

    def on_fit_end(self, trainer: Trainer, loop: TrainingLoop):
        """Called at the end of the fit method.

        Args:
            trainer: Trainer instance.
            loop: TrainingLoop object.
        """

    def on_fit_exception(self, trainer: Trainer, exception: Exception):
        """Called if an exception is raised during fit.

        Args:
            trainer: Trainer instance.
            exception: Exception object.
        """

    def on_enter_train(
        self,
        trainer: Trainer,
        stack: ExitStack,
        train_dataloader: cerebras.pytorch.utils.data.DataLoader,
        loop: TrainingLoop,
        loop_idx: int,
    ):
        """Hook that allows arbitrary context managers to be entered
        at the beginning of every training iteration.

        Args:
            trainer: Trainer instance.
            stack: ExitStack object.
            train_dataloader: Train dataloader.
            loop: TrainingLoop object.
            loop_idx: training loop index.
        """

    def on_train_start(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        train_dataloader: cerebras.pytorch.utils.data.DataLoader,
        loop: TrainingLoop,
        loop_idx: int,
    ):
        """Called at the beginning of the train loop.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            train_dataloader: Train dataloader.
            loop: TrainingLoop object.
            loop_idx: training loop index.
        """

    def on_train_end(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        loop: TrainingLoop,
        loop_idx: int,
    ):
        """Called at the end of the train loop.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            loop: TrainingLoop object.
            loop_idx: training loop index.
        """

    def on_train_exception(self, trainer, exception):
        """Called if an exception is raised during a training iteration.

        Args:
            trainer: Trainer instance.
            exception: Exception object.
        """

    def on_train_batch_start(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        batch: Any,
        batch_idx: int,
    ):
        """Called at the beginning of every training iteration.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            batch: Batch data.
            batch_idx: Batch index.
        """

    def on_train_batch_end(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        outputs: Dict[str, Any],
        batch: Any,
        batch_idx: int,
    ):
        """Called at the end of every training iteration.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            outputs: Model outputs.
            batch: Batch data.
            batch_idx: Batch index.
        """

    def run_validation(
        self,
        trainer: Trainer,
        loop_idx: int,
        is_last: bool,
    ):
        """Perform a validation run.

        Override this method to perform a custom validation run.

        Args:
            trainer: Trainer instance.
            val_dataloader: Validation dataloader.
            loop_idx: Training loop index.
            is_last: Whether the last training iteration just happened.
        """

    def on_enter_validate(
        self,
        trainer: Trainer,
        stack: ExitStack,
        val_dataloader: cerebras.pytorch.utils.data.DataLoader,
        loop: ValidationLoop,
    ):
        """Hook that allows arbitrary context managers to be entered
        at the beginning of every validation run.

        Args:
            trainer: Trainer instance.
            stack: ExitStack object.
            val_dataloader: Validation dataloader.
            loop: ValidationLoop object.
        """

    def on_validate_start(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        val_dataloader: cerebras.pytorch.utils.data.DataLoader,
        loop: ValidationLoop,
    ):
        """Called at the beginning of the validation loop.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            val_dataloader: Validation dataloader.
            loop: ValidationLoop object.
        """

    def on_validate_end(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        loop: ValidationLoop,
    ):
        """Called at the end of the validation loop.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            loop: ValidationLoop object.
        """

    def on_validate_exception(self, trainer: Trainer, exception: Exception):
        """Called if an exception is raised during validation.

        Args:
            trainer: Trainer instance.
            exception: Exception object.
        """

    def on_validate_batch_start(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        batch: Any,
        batch_idx: int,
    ):
        """Called at the beginning of every validation iteration.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            batch: Batch data.
            batch_idx: Batch index.
        """

    def on_validate_batch_end(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        outputs: Dict[str, Any],
        batch: Any,
        batch_idx: int,
    ):
        """Called at the end of every validation iteration.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            outputs: Model outputs.
            batch: Batch data.
            batch_idx: Batch index.
        """

    def on_enter_validate_all(
        self,
        trainer: Trainer,
        stack: ExitStack,
        val_dataloaders: cerebras.pytorch.utils.data.DataLoader,
        loop: ValidationLoop,
    ):
        """Hook that allows arbitrary context managers to be entered
        at the beginning of every validate all run.

        Args:
            trainer: Trainer instance.
            stack: ExitStack object.
            val_dataloaders: Validation dataloaders.
            loop: ValidationLoop object.
        """

    def on_before_forward(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        batch: Any,
        args: List[Any],
        kwargs: dict,
    ):
        """Called before the forward pass.

        The args and kwargs may be added to to provide additional
        arguments to the forward method.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            batch: Batch data.
            args: Forward pass arguments.
            kwargs: Forward pass keyword arguments.
        """

    def on_after_forward(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        outputs: Dict[str, Any],
        batch: Any,
    ):
        """Called after the forward pass.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            outputs: Model outputs.
            batch: Batch data.
        """

    def on_before_backward(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        outputs: Dict[str, Any],
    ):
        """Called before the backward pass.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            outputs: Model outputs.
        """

    def on_after_backward(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        outputs: Dict[str, Any],
    ):
        """Called after the backward pass.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            outputs: Model outputs.
            batch_idx: Batch index.
        """

    def on_before_optimizer_step(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        optimizer: cerebras.pytorch.optim.Optimizer,
    ):
        """Called before the optimizer step.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            optimizer: Optimizer instance.
        """

    def on_after_optimizer_step(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        optimizer: cerebras.pytorch.optim.Optimizer,
    ):
        """Called after the optimizer step.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            optimizer: Optimizer instance.
        """

    def on_before_optimizer_zero_grad(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        optimizer: cerebras.pytorch.optim.Optimizer,
    ):
        """Called before the optimizer zero_grad.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            optimizer: Optimizer instance.
        """

    def on_after_optimizer_zero_grad(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        optimizer: cerebras.pytorch.optim.Optimizer,
    ):
        """Called after the optimizer zero_grad.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            optimizer: Optimizer instance.
        """

    def on_before_scheduler_step(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        optimizer: cerebras.pytorch.optim.Optimizer,
        scheduler: cerebras.pytorch.optim.scheduler.Scheduler,
    ):
        """Called before the scheduler step.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            optimizer: Optimizer instance.
            scheduler: A scheduler instance.
        """

    def on_after_scheduler_step(
        self,
        trainer: Trainer,
        model: torch.nn.Module,
        optimizer: cerebras.pytorch.optim.Optimizer,
        scheduler: cerebras.pytorch.optim.scheduler.Scheduler,
    ):
        """Called after the scheduler step.

        Args:
            trainer: Trainer instance.
            model: Model instance.
            optimizer: Optimizer instance.
            scheduler: A scheduler instance.
        """

    def on_save_checkpoint(self, trainer: Trainer, state_dict: dict):
        """Called before saving the checkpoint.

        Callbacks should override this method to add states to
        the checkpoint.

        Args:
            trainer: Trainer instance.
            state_dict: Trainer state dictionary.
        """

    def postprocess_checkpoint(self, trainer: Trainer, state_dict: dict):
        """Called after constructing the checkpoint.

        Callbacks should override this method to modify the checkpoint
        before saving.

        Args:
            trainer: Trainer instance.
            state_dict: Trainer state dictionary.
        """

    def on_after_save_checkpoint(self, trainer: Trainer, ckpt_path: str):
        """Called after saving the checkpoint.

        Args:
            trainer: Trainer instance.
            ckpt_path: Checkpoint path.
        """

    def on_before_load_checkpoint(self, trainer: Trainer, ckpt_path: str):
        """Called before loading the checkpoint.

        Args:
            trainer: Trainer instance.
            ckpt_path: Checkpoint path.
        """

    def preprocess_checkpoint(self, trainer: Trainer, state_dict: dict):
        """Called after loading the checkpoint.

        Callbacks should override this method to modify the state_dict
        after loading.

        Args:
            trainer: Trainer instance.
            state_dict: Trainer state dictionary.
        """

    def on_load_checkpoint(self, trainer: Trainer, state_dict: dict):
        """Called after loading the checkpoint.

        Callbacks should override this method to load states from
        the checkpoint.

        Args:
            trainer: Trainer instance.
            state_dict: Trainer state dictionary.
        """

    def __enter__(self):
        """Register the callback as a global callback."""
        if not hasattr(self, "_global_callback_handle"):
            # pylint: disable=attribute-defined-outside-init
            self._global_callback_handle = register_global_callback(self)
        return self

    def __exit__(self, *args):
        """Remove the callback from the global callback registry."""
        if hasattr(self, "_global_callback_handle"):
            # pylint: disable=protected-access
            self._global_callback_handle.remove()
            del self._global_callback_handle


GLOBAL_CALLBACK_REGISTRY = OrderedDict()


def register_global_callback(callback):
    """Register a global callback.

    Args:
        callback: the Callback to register.
            If a class is passed, an instance of the class is created.
            If an instance is passed, it is registered as is.

    Returns:
        A torch.utils.hooks.RemoveableHandle object.
    """
    from inspect import isclass

    from torch.utils import hooks

    if isinstance(callback, Callback):
        handle = hooks.RemovableHandle(GLOBAL_CALLBACK_REGISTRY)
        GLOBAL_CALLBACK_REGISTRY[handle.id] = callback
        return handle
    elif isclass(callback) and issubclass(callback, Callback):
        handle = hooks.RemovableHandle(GLOBAL_CALLBACK_REGISTRY)
        GLOBAL_CALLBACK_REGISTRY[handle.id] = callback()
        # pylint: disable=protected-access
        callback._global_callback_handle = handle
        return callback
    else:
        raise TypeError(f"Expected a Callback. Got: {type(callback)}")


class ValidationCallback(Callback, ABC):
    """
    A special type of callback that indicates to the trainer
    that it will perform some custom validation logic.

    This is useful for callbacks that need to perform downstream validation
    logic that is not covered by the default validation loop.

    All ValidationCallbacks must implement the following methods:

    - run_validation

    Essentially, you are telling the trainer what to run at the end of each
    training run.
    """

    @abstractmethod
    def run_validation(self, trainer, loop_idx, is_last):
        pass
