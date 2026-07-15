"""Memory-safe hyperparameter defaults.

Gradient checkpointing defaults ON everywhere a run/session is born. Without
it, a batch of long sequences (seq² attention activations) blows past Metal's
working-set limit on Apple Silicon — run #26 OOM'd at >27 GB with it off and
peaked at 7.9 GB with it on, same model/dataset/batch.
"""
from __future__ import annotations

from apps.api.models.run import Run
from apps.api.models.session import TrainingSession
from apps.api.routers.runs import RunCreate
from apps.api.routers.sessions import SessionCreate


def test_run_model_defaults_grad_checkpoint_on() -> None:
    assert Run(dataset="d", base_model="m").grad_checkpoint is True


def test_run_create_schema_defaults_grad_checkpoint_on() -> None:
    assert RunCreate(dataset="d").grad_checkpoint is True


def test_run_create_accepts_explicit_off() -> None:
    assert RunCreate(dataset="d", grad_checkpoint=False).grad_checkpoint is False


def test_session_model_defaults_grad_checkpoint_on() -> None:
    assert TrainingSession(name="s", dataset="d", base_model="m").grad_checkpoint is True


def test_session_create_schema_defaults_grad_checkpoint_on() -> None:
    assert SessionCreate(name="s", dataset="d").grad_checkpoint is True